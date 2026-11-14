"""
Step3（原创管线）: Image2 并发生成 N 张关键帧

输入: storyboard.json + 产品图
输出: keyframes/shot_01.jpg ~ shot_N.jpg

关键决策:
  - 调用 image_edit_sync（图生图），不是 text_to_image
  - 首帧生成后作为后续帧的参考图，确保视觉一致性
  - 并发上限 4（避免 Image2 QPS 限制）
  - 失败重试 3 次（2s/4s 退避），最终失败抛异常
"""
import os
import sys
import asyncio
import base64
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable

# 修复 Windows 中文编码问题
if sys.platform == "win32":
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    import io
    if hasattr(sys.stdout, 'buffer') and getattr(sys.stdout, 'encoding', '').lower() not in ('utf-8', 'utf8'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'buffer') and getattr(sys.stderr, 'encoding', '').lower() not in ('utf-8', 'utf8'):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.services.image2_client import Image2Client
from pipeline.step3_replace_product import _img_to_b64, _resize_to_max, _sanitize_desc, _download_image


# ─────────────────────────────────────
# 关键帧生成提示词
# ─────────────────────────────────────
KEYFRAME_PROMPT_TEMPLATE = """请基于产品参考图生成一张广告分镜关键帧。

要求：
1. 产品外观必须严格还原参考图：logo、文字、配色、形状、材质 100% 一致，不变形不扭曲
2. 产品在画面中的位置、大小、透视需自然融入场景
3. 人物（如有）动作自然，光线方向一致
4. 画面比例 {aspect_ratio}，4K 超清细节，TVC 广告质感
5. 只输出一张图，不添加编号、字幕、水印或说明文字

分镜描述：{desc}
"""


def _build_shot_desc(shot: Dict[str, Any]) -> str:
    """从 storyboard shot 构建关键帧画面描述（与 step5_multi_shot 字段一致）。"""
    scene_env = shot.get("场景环境", {})
    lens = shot.get("镜头语言", {})
    action = shot.get("画面动作", {})
    people_product = shot.get("人物与产品", {})

    desc = (
        f"场景:{scene_env.get('场景', '')} "
        f"背景:{scene_env.get('背景细节', '')} "
        f"光影:{scene_env.get('光影设定', '')} "
        f"画面:{action.get('画面内容', '')} "
        f"人物动作:{action.get('人物动作', '')} "
        f"产品动作:{action.get('产品动作', '')} "
        f"人物:{people_product.get('人物', '')} "
        f"景别:{lens.get('景别', '')} "
        f"构图:{lens.get('构图', '')} "
        f"运镜:{lens.get('运镜', '')}"
    )
    return desc


def _size_from_ratio(aspect_ratio: str) -> str:
    """画面比例 → Image2 size 参数。"""
    if aspect_ratio == "9:16":
        return "1024x1792"
    elif aspect_ratio == "16:9":
        return "1792x1024"
    elif aspect_ratio == "1:1":
        return "1024x1024"
    elif aspect_ratio == "3:4":
        return "1024x1368"
    else:
        return "1024x1792"  # 默认竖版


async def _generate_one_keyframe(
    client: Image2Client,
    shot_idx: int,
    shot: Dict[str, Any],
    product_image_paths: List[str],
    prev_keyframe_path: Optional[str],
    output_path: Path,
    aspect_ratio: str,
    sem: asyncio.Semaphore,
    frame_role: str = "start",  # "start" | "end"（仅 fl2v 模式生成 end 帧）
    progress_callback: Optional[Callable] = None,
) -> str:
    """生成单张关键帧。

    Args:
        prev_keyframe_path: 前一帧生成结果路径（首帧为 None）
        frame_role: "start"（首帧）或 "end"（尾帧，仅 fl2v 模式）
    """
    async def _do() -> str:
        role_label = "首帧" if frame_role == "start" else "尾帧"
        if progress_callback:
            progress_callback("keyframe_generating", shot_idx,
                              f"生成关键帧 {shot_idx:02d} {role_label}")

        # 构造 images 列表：产品图 + (可选)前一帧
        images: List[str] = []
        # 产品图（只取第 1 张作为参考）
        if product_image_paths and Path(product_image_paths[0]).exists():
            prod_b64 = _resize_to_max(_img_to_b64(Path(product_image_paths[0])), max_px=1536)
            images.append(f"data:image/jpeg;base64,{prod_b64}")
        else:
            raise RuntimeError(f"产品图不存在或为空: {product_image_paths}")

        # 后续帧：加入前一帧作为视觉引导，确保风格/光线/人物一致
        if prev_keyframe_path and Path(prev_keyframe_path).exists():
            prev_b64 = _resize_to_max(_img_to_b64(Path(prev_keyframe_path)), max_px=1024)
            images.append(f"data:image/jpeg;base64,{prev_b64}")

        # 构建 prompt
        desc = _build_shot_desc(shot)
        desc_clean = _sanitize_desc(desc) if desc else "产品展示分镜"
        prompt = KEYFRAME_PROMPT_TEMPLATE.format(aspect_ratio=aspect_ratio, desc=desc_clean)

        # 尾帧额外约束（fl2v 模式）：描述画面结束状态
        if frame_role == "end":
            prompt += "\n重要：这是该镜头的【尾帧】（结束画面），请描述动作结束后的状态，与首帧形成自然过渡。"

        # 后续帧增加一致性约束
        if prev_keyframe_path:
            prompt += "\n重要：请保持与第 2 张参考帧（前一帧）的人物形象、服装、光线方向、画面风格高度一致，确保连续镜头的视觉连贯性。"

        size = _size_from_ratio(aspect_ratio)

        # 调用 Image2 图生图
        result = await client.image_edit_sync(prompt=prompt, images=images, size=size, n=1)
        data_list = result.get("data", [])
        if not data_list:
            raise RuntimeError(f"Image2 返回 data 为空（shot {shot_idx} {role_label}）")
        url = data_list[0].get("url", "")
        b64 = data_list[0].get("b64_json", "")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if url:
            _download_image(url, output_path)
        elif b64:
            output_path.write_bytes(base64.b64decode(b64))
        else:
            raise RuntimeError(f"Image2 未返回图片（shot {shot_idx} {role_label}）")

        print(f"  ✓ 关键帧 {shot_idx:02d} {role_label} 生成完成: {output_path.name}")
        return str(output_path)

    async with sem:
        return await _do()


async def _generate_all_keyframes(
    storyboard_json: List[Dict[str, Any]],
    product_image_paths: List[str],
    output_dir: Path,
    aspect_ratio: str,
    concurrency: int = 4,
    progress_callback: Optional[Callable] = None,
) -> Dict[str, List[str]]:
    """按 gen_mode 混合生成关键帧。

    - fl2v 模式 shot：生成 2 张（首帧 + 尾帧）
    - i2v 模式 shot：生成 1 张（仅首帧）

    共享帧策略：
    - fl2v → fl2v：shot_i 尾帧 = shot_{i+1} 首帧（共享，只生成 1 张）
    - fl2v → i2v：shot_i 尾帧独立生成，shot_{i+1} 首帧独立生成
    - i2v → fl2v：shot_{i+1} 首帧独立生成，尾帧独立生成
    - i2v → i2v：各自独立生成首帧

    Returns:
        {
            "start_frames": [path1, path2, ...],  # 每 shot 的首帧路径
            "end_frames": [path1_or_None, path2_or_None, ...],  # 每 shot 的尾帧路径（i2v 为 None）
        }
    """
    sem = asyncio.Semaphore(concurrency)
    output_dir.mkdir(parents=True, exist_ok=True)

    n_shots = len(storyboard_json)
    start_frames: List[str] = []
    end_frames: List[Optional[str]] = []

    for i, shot in enumerate(storyboard_json):
        gen_mode = shot.get("gen_mode", "fl2v")
        out_start = output_dir / f"shot_{i+1:02d}_start.jpg"
        out_end = output_dir / f"shot_{i+1:02d}_end.jpg"

        # 决定首帧的参考帧：前一 shot 的尾帧（若存在）或前一 shot 的首帧
        prev_frame = end_frames[-1] if (end_frames and end_frames[-1]) else (start_frames[-1] if start_frames else None)

        # 生成首帧
        last_err: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                if progress_callback:
                    progress_callback("keyframe_generating", i+1,
                                      f"关键帧 {i+1:02d} 首帧（{gen_mode}，第{attempt}次）")
                path = await _generate_one_keyframe(
                    client=Image2Client(),
                    shot_idx=i+1,
                    shot=shot,
                    product_image_paths=product_image_paths,
                    prev_keyframe_path=prev_frame,
                    output_path=out_start,
                    aspect_ratio=aspect_ratio,
                    sem=sem,
                    frame_role="start",
                    progress_callback=progress_callback,
                )
                start_frames.append(path)
                break
            except Exception as e:
                last_err = e
                print(f"  ⚠ 关键帧 {i+1:02d} 首帧第{attempt}次生成失败: {e}")
                if attempt < 3:
                    await asyncio.sleep(2 * attempt)
        else:
            raise RuntimeError(f"关键帧 {i+1:02d} 首帧 3 次重试均失败: {last_err}")

        # fl2v 模式：额外生成尾帧
        if gen_mode == "fl2v":
            # 检查下一 shot 是否也是 fl2v（若是，则本 shot 尾帧 = 下一 shot 首帧，先占位 None，最后回填）
            next_shot = storyboard_json[i+1] if i+1 < n_shots else None
            next_is_fl2v = next_shot and next_shot.get("gen_mode", "fl2v") == "fl2v"

            if next_is_fl2v:
                # 占位：尾帧 = 下一 shot 首帧（稍后回填）
                end_frames.append(None)
                print(f"  ⏭ 关键帧 {i+1:02d} 尾帧占位（将与 shot {i+2} 首帧共享）")
            else:
                # 独立生成尾帧（下一 shot 是 i2v 或最后一 shot）
                for attempt in range(1, 4):
                    try:
                        if progress_callback:
                            progress_callback("keyframe_generating", i+1,
                                              f"关键帧 {i+1:02d} 尾帧（{gen_mode}，第{attempt}次）")
                        path = await _generate_one_keyframe(
                            client=Image2Client(),
                            shot_idx=i+1,
                            shot=shot,
                            product_image_paths=product_image_paths,
                            prev_keyframe_path=start_frames[-1],  # 尾帧参考本 shot 首帧
                            output_path=out_end,
                            aspect_ratio=aspect_ratio,
                            sem=sem,
                            frame_role="end",
                            progress_callback=progress_callback,
                        )
                        end_frames.append(path)
                        break
                    except Exception as e:
                        last_err = e
                        print(f"  ⚠ 关键帧 {i+1:02d} 尾帧第{attempt}次生成失败: {e}")
                        if attempt < 3:
                            await asyncio.sleep(2 * attempt)
                else:
                    raise RuntimeError(f"关键帧 {i+1:02d} 尾帧 3 次重试均失败: {last_err}")
        else:
            # i2v 模式：无尾帧
            end_frames.append(None)

    # 回填共享帧：fl2v → fl2v 时，shot_i 尾帧 = shot_{i+1} 首帧
    for i in range(n_shots - 1):
        if end_frames[i] is None:
            # 当前 shot 是 fl2v 且下一 shot 也是 fl2v → 共享下一 shot 首帧
            cur_gm = storyboard_json[i].get("gen_mode", "fl2v")
            next_gm = storyboard_json[i+1].get("gen_mode", "fl2v")
            if cur_gm == "fl2v" and next_gm == "fl2v":
                end_frames[i] = start_frames[i+1]
                print(f"  🔗 回填共享帧: shot_{i+1:02d}_end = shot_{i+2:02d}_start")

    return {"start_frames": start_frames, "end_frames": end_frames}


def generate_original_keyframes(
    storyboard_json: list,
    product_image_paths: List[str],
    output_dir: str,
    aspect_ratio: str = "9:16",
    progress_callback: Optional[Callable] = None,
) -> Dict[str, Any]:
    """原创管线 Step3: Image2 并发生成 N 张关键帧

    Args:
        storyboard_json: 分镜 JSON 列表（来自 T1）
        product_image_paths: 产品图路径列表（第 1 张作为参考图）
        output_dir: 输出目录（会在其下创建 keyframes/ 子目录）
        aspect_ratio: 画面比例（默认 9:16）
        progress_callback: 进度回调

    Returns:
        {
            "status": "success",
            "keyframes": [path1, path2, ...],
            "keyframes_dir": str,
            "count": int,
        }
    """
    print("\n" + "=" * 60)
    print("Step3 原创管线：Image2 生成关键帧")
    print("=" * 60)

    if not storyboard_json:
        raise ValueError("storyboard_json 为空")
    if not product_image_paths:
        raise ValueError("product_image_paths 为空")

    n_frames = len(storyboard_json)
    fl2v_count = sum(1 for s in storyboard_json if s.get("gen_mode", "fl2v") == "fl2v")
    i2v_count = n_frames - fl2v_count
    print(f"  → 关键帧数: {n_frames}（fl2v: {fl2v_count} 段, i2v: {i2v_count} 段）")
    print(f"  → 产品图: {len(product_image_paths)} 张")
    print(f"  → 画面比例: {aspect_ratio}")

    out = Path(output_dir)
    keyframes_dir = out / "keyframes"
    keyframes_dir.mkdir(parents=True, exist_ok=True)

    if progress_callback:
        progress_callback("keyframes_generating", 0, f"生成 {n_frames} 个 shot 的关键帧（混合模式）...")

    frames_result = asyncio.run(_generate_all_keyframes(
        storyboard_json=storyboard_json,
        product_image_paths=product_image_paths,
        output_dir=keyframes_dir,
        aspect_ratio=aspect_ratio,
        concurrency=4,
        progress_callback=progress_callback,
    ))

    start_frames = frames_result["start_frames"]
    end_frames = frames_result["end_frames"]
    # 兼容下游：构造 keyframes 列表（仅首帧）
    keyframe_paths = start_frames

    print(f"\n✓ Step3 原创关键帧生成完成")
    print(f"  首帧数: {len(start_frames)}")
    print(f"  尾帧数: {sum(1 for e in end_frames if e)}（fl2v 模式段）")
    print(f"  输出目录: {keyframes_dir}")

    return {
        "status": "success",
        "keyframes": keyframe_paths,  # 兼容字段：首帧列表
        "start_frames": start_frames,
        "end_frames": end_frames,  # i2v 段为 None
        "keyframes_dir": str(keyframes_dir),
        "count": len(keyframe_paths),
        "gen_modes": [s.get("gen_mode", "fl2v") for s in storyboard_json],
    }


if __name__ == "__main__":
    # 手动测试
    import argparse
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("--storyboard", required=True, help="storyboard.json 路径")
    parser.add_argument("--product-image", action="append", required=True)
    parser.add_argument("--out", default="outputs/original_test")
    parser.add_argument("--ratio", default="9:16")
    args = parser.parse_args()

    with open(args.storyboard, "r", encoding="utf-8") as f:
        storyboard = json.load(f)

    result = generate_original_keyframes(
        storyboard_json=storyboard,
        product_image_paths=args.product_image,
        output_dir=args.out,
        aspect_ratio=args.ratio,
    )
    print(f"\n结果: {result['status']}, 关键帧数: {result['count']}")
    for p in result["keyframes"]:
        print(f"  - {p}")
