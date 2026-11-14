"""
批量提示词反推编排器
串行处理多个抖音链接：下载 → 反推 → 通用化 → 保存 .txt
"""
import asyncio
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from config import settings

# 输出目录
PROMPTS_DIR = Path(settings.OUTPUT_DIR) / "batch_prompts"


def _to_web_url(path: str) -> str:
    """把本地视频路径转成可预览的 /outputs 或 /uploads URL。"""
    if not path:
        return ""
    p = Path(path).resolve()
    try:
        return "/outputs/" + p.relative_to(Path(settings.OUTPUT_DIR).resolve()).as_posix()
    except ValueError:
        pass
    try:
        return "/uploads/" + p.relative_to(Path(settings.UPLOAD_DIR).resolve()).as_posix()
    except ValueError:
        return ""


def _fresh_pool_path(uid: str, snapshot: str) -> str:
    """这一行的原片**现在**在盘上哪儿 —— 快照还在就用快照，死了才回退问池行现在的位置。

    🔴 为什么要现查：任务改名会把 `outputs/tasks/<目录>` 整个搬走，而批次账里存的是**提交那一刻**的
    绝对路径 ⇒ 搬过之后它指向一个不存在的地方，裁剪撞 ffmpeg，屏上只剩一串退出码
    （2026-10-23 在 8099 那边现场：`…/T20261023-01_修改人物1-1/原片.mp4` 早已搬成 `…_替换人物/`）。
    路径的唯一主人是池行自己（改名时台账会跟着搬），所以先问它；问不到才退回快照，并验一次在不在盘上。
    """
    now = ""
    if uid:
        try:
            from backend.services import pool_store
            now = str((pool_store.get(uid) or {}).get("video_path") or "")
        except Exception:
            now = ""
    # 谁在盘上用谁，**快照优先**：这份快照本来就是提交那一刻按当时那一格算出来的路径
    # （上传件、已裁好的片段都走它），只有它死了（= 改名把文件夹搬走了）才回退去问池行现在的位置。
    # 反过来写（池行优先）会把还活着的路径也换掉 —— 10-23 我先写成那样，check_prompt_name 六条一起红。
    for cand in (str(snapshot or ""), now):
        if cand and Path(cand).exists():
            return cand
    return str(snapshot or "") or now



async def run_batch(
    links: List[str],
    video_paths: Optional[List[str]] = None,
    on_item_update: Optional[Callable] = None,
    batch_id: Optional[str] = None,
    trim_ranges: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """串行处理多个抖音链接，生成通用提示词并保存。

    Args:
        links: 抖音链接列表（每行一个）
        on_item_update: 回调函数，每个 item 状态变化时调用
                        签名: async fn(item: dict) -> None

    Returns:
        {
            "batch_id": str,
            "total": int,
            "items": [...]  # 每个 item 的最终状态
        }
    """
    batch_id = batch_id or f"batch_{int(time.time())}"
    work_dir = PROMPTS_DIR / batch_id
    work_dir.mkdir(parents=True, exist_ok=True)

    items: List[Dict[str, Any]] = []
    for i, link in enumerate(links):
        items.append({
            "index": i,
            "link": link.strip(),
            "status": "pending",  # pending → downloading → reversing → generalizing → saving → done / failed
            "stage": "等待处理",
            "progress": 0,
            "product_category": "",
            "general_prompt": "",
            "prompt_file": "",
            "video_url": "",     # H3 生成视频回传
            "error": "",
        })

    async def _update(item: dict):
        if on_item_update:
            try:
                await on_item_update(item)
            except Exception as e:
                print(f"  [batch] 回调失败：{e}", flush=True)

    # ── 串行处理 ──
    for item in items:
        await _process_single_item(
            item, work_dir, links, video_paths, on_item_update,
            batch_id=batch_id or "",
            trim_ranges=trim_ranges,
        )

    return {
        "batch_id": batch_id,
        "total": len(items),
        "items": items,
    }


async def _process_single_item(
    item: dict,
    work_dir: Path,
    links: list,
    video_paths: list,
    on_item_update: Optional[Callable] = None,
    batch_id: str = "",
    trim_ranges: Optional[list] = None,
) -> dict:
    """处理单个 item：download → reverse → generalize → save

    支持断点续跑：
    - 已有 reverse_result.h3_prompt → 跳过 download + reverse，从 generalize 续跑
    - 已有 source_video_path → 跳过 download，从 reverse 续跑
    """
    async def _update(item: dict):
        if on_item_update:
            try:
                await on_item_update(item)
            except Exception as e:
                print(f"  [batch] 回调失败：{e}", flush=True)

    link = item.get("link", "")
    idx = item["index"]
    uploaded_path = _fresh_pool_path(item.get("pool_uid", ""),
                                     video_paths[idx] if video_paths and idx < len(video_paths) else "")
    if not link and not uploaded_path and not item.get("source_video_path"):
        item["status"] = "failed"
        item["stage"] = "缺少抖音链接或视频文件"
        item["error"] = item["stage"]
        await _update(item)
        return item
    item_dir = work_dir / f"item_{idx}"
    item_dir.mkdir(parents=True, exist_ok=True)

    # 断点续跑判断
    existing_rev = item.get("reverse_result") or {}
    has_reverse = bool(existing_rev.get("h3_prompt"))
    has_video = bool(item.get("source_video_path"))

    try:
        from backend.services.prompt_reverser import reverse_engineer_prompt, _gpt_generalize_prompt

        # Step 1: 下载/使用已上传视频（断点：已有 video_path 则跳过）
        if not has_video:
            item["status"] = "downloading"
            item["stage"] = "准备视频..."
            item["progress"] = 10
            await _update(item)

            if uploaded_path:
                video_path = uploaded_path
                dl_result = {"title": Path(video_path).stem, "video_path": video_path}
                item["stage"] = "使用上传视频"
            else:
                # 下载这条腿只有一份：② 不再自己直接调下载器（原来那样少了 ① 的两次重试与
                # 断头片判定，还靠默认值吃策略）。分发（抖音/小红书）也在漏斗里做。
                from backend.services.link_fetch import link_fetch_local

                async def _dl_cb(msg: str):
                    item["stage"] = msg
                    await _update(item)

                got = await link_fetch_local(link, cb=_dl_cb, out_dir=item_dir, analyze=True)
                dl_result = {"video_path": got["file_path"], "title": got["title"]}
                video_path = got["file_path"]

            item["source_video_path"] = video_path
            item["source_video_url"] = _to_web_url(video_path)
            item["source_title"] = dl_result.get("title", "")
            item["progress"] = 30
            await _update(item)
        else:
            video_path = item["source_video_path"]
            # 断点续跑拿到的那份也是**提交时**的路径 ⇒ 同一个毛病同一个修法：按池行现在的位置校正一次。
            video_path = _fresh_pool_path(item.get("pool_uid", ""), video_path) or video_path

        # 可选：按用户选定区间裁剪后再反推
        trim_range = None
        if trim_ranges and idx < len(trim_ranges):
            trim_range = trim_ranges[idx]
        if (
            trim_range
            and not has_reverse
            and not item.get("trimmed")
            and isinstance(trim_range, dict)
            and trim_range.get("start") is not None
            and trim_range.get("end") is not None
        ):
            start = float(trim_range["start"])
            end = float(trim_range["end"])
            if end > start + 0.2:
                item["stage"] = f"裁剪视频 {start:.1f}s → {end:.1f}s..."
                await _update(item)
                from backend.services.super_clone_wan3 import trim_video
                trimmed_path = str(item_dir / "trimmed.mp4")
                await asyncio.to_thread(trim_video, video_path, start, end, trimmed_path)
                video_path = trimmed_path
                item["source_video_path"] = video_path
                item["trimmed"] = True
                item["trim_start"] = start
                item["trim_end"] = end
                item["source_video_url"] = _to_web_url(video_path)

        # Step 2: MiMo 反推（断点：已有 reverse_result.h3_prompt 则跳过）
        if not has_reverse:
            item["status"] = "reversing"
            item["stage"] = "MiMo-V2.5 原始提示词反推中..."
            item["progress"] = 40
            await _update(item)

            rev_result = await reverse_engineer_prompt(
                video_path=video_path,
                output_dir=str(item_dir),
                extra_hint="",
                engine="mimo",
            )
            specific_prompt = rev_result.get("h3_prompt", "")
            item["reverse_result"] = rev_result
            item["voiceover_segments"] = rev_result.get("voiceover_segments", [])
            item["voiceover_text"] = rev_result.get("voiceover_text", "")
            item["duration"] = float(rev_result.get("duration") or 0)
            item["frames"] = rev_result.get("frames", [])
            item["progress"] = 70
            await _update(item)
        else:
            specific_prompt = existing_rev["h3_prompt"]
            item["status"] = "reversing"
            item["stage"] = "已有反推结果，跳过 MiMo 调用"
            item["progress"] = 70
            await _update(item)

        # Step 3: GPT 通用化
        item["status"] = "generalizing"
        item["stage"] = "GPT 通用化中..."
        item["progress"] = 80
        await _update(item)

        gen_result = await _gpt_generalize_prompt(specific_prompt)
        item["product_category"] = gen_result["product_category"]
        item["general_prompt"] = gen_result["general_prompt"]
        item["progress"] = 90
        await _update(item)

        # Step 4: 保存 .txt
        item["status"] = "saving"
        item["stage"] = "保存提示词..."
        item["progress"] = 95
        await _update(item)

        safe_name = item["product_category"].replace("/", "_").replace("\\", "_")[:20]
        txt_filename = f"{idx:02d}_{safe_name}.txt"
        txt_path = item_dir / txt_filename
        txt_path.write_text(item["general_prompt"], encoding="utf-8")
        item["prompt_file"] = str(txt_path)
        item["prompt_url"] = f"/outputs/batch_prompts/{work_dir.name}/{item_dir.name}/{txt_filename}"

        item["status"] = "done"
        item["stage"] = "完成"
        item["progress"] = 100
        await _update(item)

    except Exception as e:
        tb = traceback.format_exc()
        print(f"  [batch] item {idx} 失败：{e}\n{tb}", flush=True)
        item["status"] = "failed"
        item["stage"] = f"失败：{e}"
        item["error"] = str(e)
        await _update(item)

    return item


async def retry_item(
    batch_id: str,
    item_index: int,
    item: dict,
    links: list,
    video_paths: list,
    on_item_update: Optional[Callable] = None,
    trim_ranges: Optional[list] = None,
) -> dict:
    """重试单个失败的 item，从失败阶段续跑（断点续跑）。

    策略（参考 replica retry）：
    - 已有 reverse_result.h3_prompt → 跳过 download+reverse，从 generalize 续跑
    - 已有 source_video_path 但无 reverse_result → 从 reverse 续跑
    - 都没有 → 从头跑
    """
    work_dir = PROMPTS_DIR / batch_id

    # 重置错误状态
    item["status"] = "pending"
    item["stage"] = "准备重试..."
    item["progress"] = 0
    item["error"] = ""
    if on_item_update:
        try:
            await on_item_update(item)
        except Exception:
            pass

    return await _process_single_item(
        item=item,
        work_dir=work_dir,
        links=links,
        video_paths=video_paths,
        on_item_update=on_item_update,
        batch_id=batch_id,
        trim_ranges=trim_ranges,
    )


def list_saved_prompts() -> List[Dict[str, Any]]:
    """列出已保存的提示词文件"""
    result = []
    if not PROMPTS_DIR.exists():
        return result
    for batch_dir in sorted(PROMPTS_DIR.iterdir(), reverse=True):
        if not batch_dir.is_dir():
            continue
        for item_dir in sorted(batch_dir.iterdir()):
            if not item_dir.is_dir():
                continue
            txt_files = list(item_dir.glob("*.txt"))
            if not txt_files:
                continue
            txt_file = txt_files[0]
            # 从文件名提取序号和名称
            parts = txt_file.stem.split("_", 1)
            idx = parts[0] if parts else "00"
            name = parts[1] if len(parts) > 1 else txt_file.stem
            result.append({
                "batch_id": batch_dir.name,
                "index": int(idx) if idx.isdigit() else 0,
                "product_category": name,
                "prompt_file": str(txt_file),
                "prompt_url": f"/outputs/batch_prompts/{batch_dir.name}/{item_dir.name}/{txt_file.name}",
            })
    return result


def read_prompt_file(prompt_file: str) -> str:
    """读取提示词文件内容"""
    p = Path(prompt_file)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")
