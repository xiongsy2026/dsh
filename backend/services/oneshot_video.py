"""
一句话生视频 - 视频生成服务

调用 pipeline/step5_video_generate.py 的 run() 函数，复用爆款复刻的
Seedance 2.0 RPA（weeksir.com/video）。

设计要点：
- 不在前台 asyncio 任务中直接跑（Playwright 与 FastAPI 事件循环有冲突），
  而是启动子进程执行 step5_video_generate.run()，避免阻塞主服务。
- 通过 task_store 持久化状态，支持服务重启后恢复。
- 日志通过子进程 stdout 流式收集，写入 task_store.add_log。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from config import settings
from backend.services import task_store


# ── 任务类型常量 ──
TASK_TYPE = "one_shot_video"

# 输出根目录
OUTPUT_ROOT = Path(settings.OUTPUT_DIR) / "one_shot_video"


# ── 防重复提交：基于输入指纹 ──
def _dedup_key(
    compiled_prompt: str,
    product_image_path: Optional[str],
    aspect_ratio: str = "9:16",
    duration: str = "15s",
    resolution: str = "720p",
    product_image_paths: Optional[list] = None,
) -> str:
    """生成任务指纹。同一 prompt + 同一图片(含多图) + 同一参数视为重复。"""
    h = hashlib.md5()
    h.update(compiled_prompt.encode("utf-8"))
    h.update(b"|")
    h.update((product_image_path or "").encode("utf-8"))
    if product_image_path and os.path.exists(product_image_path):
        with open(product_image_path, "rb") as f:
            h.update(f.read())
    # 多图参与指纹
    if product_image_paths:
        for p in product_image_paths:
            h.update(b"|")
            h.update((p or "").encode("utf-8"))
            if p and os.path.exists(p):
                with open(p, "rb") as f:
                    h.update(f.read())
    h.update(b"|")
    h.update(f"{aspect_ratio}|{duration}|{resolution}".encode("utf-8"))
    return h.hexdigest()


def find_running_by_dedup(dedup_key: str) -> Optional[dict]:
    """查找同指纹的运行中任务，返回 task_id 或 None。"""
    for t in task_store.list_running():
        if t.get("task_type") != TASK_TYPE:
            continue
        if t.get("extra", {}).get("dedup_key") == dedup_key:
            return t
    return None


# ── 创建任务 ──
def create_task(
    user_intent: str,
    compiled_prompt: str,
    metadata: Dict[str, Any],
    product_image_path: Optional[str] = None,
    product_image_paths: Optional[list] = None,
    aspect_ratio: str = "9:16",
    duration: str = "15s",
    resolution: str = "720p",
) -> Dict[str, Any]:
    """创建一句话生视频任务并落盘。返回任务字典。"""
    task_id = uuid.uuid4().hex[:12]
    dkey = _dedup_key(
        compiled_prompt, product_image_path,
        aspect_ratio, duration, resolution,
        product_image_paths=product_image_paths,
    )

    work_dir = OUTPUT_ROOT / task_id
    work_dir.mkdir(parents=True, exist_ok=True)

    params = {
        "user_intent": user_intent,
        "compiled_prompt": compiled_prompt,
        "metadata": metadata,
        "product_image_path": product_image_path or "",
        "product_image_paths": product_image_paths or [],
        "aspect_ratio": aspect_ratio,
        "duration": duration,
        "resolution": resolution,
    }

    return task_store.create(
        task_id=task_id,
        task_type=TASK_TYPE,
        status="PENDING",
        progress=0,
        stage="已创建，等待启动",
        params=params,
        work_dir=str(work_dir),
        extra={"dedup_key": dkey, "spec_version": metadata.get("specVersion", "")},
    )


# ── 启动视频生成子进程 ──
async def start_generation(task_id: str) -> None:
    """启动子进程跑 step5_video_generate.run()。

    通过子进程而非 asyncio.create_task，避免 Playwright 与主服务事件循环冲突。
    """
    task = task_store.get(task_id)
    if not task:
        raise ValueError(f"任务不存在: {task_id}")

    params = task.get("params", {})
    compiled_prompt = params.get("compiled_prompt", "")
    product_image_path = params.get("product_image_path") or None
    product_image_paths = params.get("product_image_paths") or []
    aspect_ratio = params.get("aspect_ratio", "9:16")
    duration = params.get("duration", "15s")
    resolution = params.get("resolution", "720p")
    work_dir = Path(task.get("work_dir", str(OUTPUT_ROOT / task_id)))
    work_dir.mkdir(parents=True, exist_ok=True)

    task_store.update(task_id, status="RUNNING", progress=5, stage="启动视频生成子进程")
    task_store.add_log(task_id, f"启动子进程生成视频，工作目录: {work_dir}")
    if product_image_paths:
        task_store.add_log(task_id, f"商品参考图: {len(product_image_paths)} 张")

    # 构造子进程 runner 脚本
    param_file = work_dir / "_params.json"
    result_file = work_dir / "_result.json"

    param_data = {
        "compiled_prompt": compiled_prompt,
        "product_image_path": product_image_path,
        "product_image_paths": product_image_paths,
        "aspect_ratio": aspect_ratio,
        "duration": duration,
        "resolution": resolution,
        "output_dir": str(work_dir),
    }
    param_file.write_text(json.dumps(param_data, ensure_ascii=False), encoding="utf-8")

    runner = work_dir / "_runner.py"
    runner.write_text(_runner_script(param_file, result_file), encoding="utf-8")

    # 启动子进程
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(Path(__file__).parent.parent.parent)

    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-X", "utf8", str(runner),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            cwd=str(Path(__file__).parent.parent.parent),
        )
    except Exception as e:
        task_store.update(task_id, status="FAILED", error=f"子进程启动失败: {e}", stage="失败")
        task_store.add_log(task_id, f"子进程启动失败: {e}", level="error")
        return

    # 流式读取日志
    timeout_sec = 1200  # 20分钟总超时
    start_time = asyncio.get_event_loop().time()
    last_progress_time = start_time

    try:
        while True:
            try:
                line_bytes = await asyncio.wait_for(process.stdout.readline(), timeout=15.0)
            except asyncio.TimeoutError:
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed > timeout_sec:
                    process.kill()
                    task_store.update(
                        task_id, status="FAILED",
                        error=f"视频生成超时（{int(elapsed)}秒）",
                        stage="超时",
                        progress=0,
                    )
                    task_store.add_log(task_id, f"超时 {int(elapsed)}s，已终止", level="error")
                    return
                # 15s 没新日志但未超时，写心跳（SSE 能看到）
                task_store.add_log(task_id, f"... 等待中（已运行 {int(elapsed)}s）")
                continue

            if not line_bytes:
                break

            line = line_bytes.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue

            task_store.add_log(task_id, line)

            # 阶段进度推断
            stage, progress = _infer_stage_progress(line)
            if stage:
                task_store.update(task_id, stage=stage, progress=progress)
                last_progress_time = asyncio.get_event_loop().time()

        await process.wait()
    except Exception as e:
        task_store.update(task_id, status="FAILED", error=f"子进程异常: {e}", stage="异常")
        task_store.add_log(task_id, f"子进程异常: {e}", level="error")
        return

    # 读取结果
    if result_file.exists():
        try:
            result = json.loads(result_file.read_text(encoding="utf-8"))
        except Exception as e:
            task_store.update(task_id, status="FAILED", error=f"结果解析失败: {e}", stage="失败")
            task_store.add_log(task_id, f"结果解析失败: {e}", level="error")
            return
    else:
        task_store.update(task_id, status="FAILED", error="结果文件未生成", stage="失败")
        task_store.add_log(task_id, "结果文件未生成", level="error")
        return

    if result.get("success"):
        r = result.get("result", {})
        video_url = r.get("video_url")
        local_video = r.get("local_video")

        # 视频 URL 转成可访问的 /outputs 路径
        video_serve_url = None
        if local_video and os.path.exists(local_video):
            try:
                p = Path(local_video).resolve()
                base = Path(settings.OUTPUT_DIR).resolve()
                if str(p).startswith(str(base)):
                    video_serve_url = "/outputs/" + str(p.relative_to(base)).replace("\\", "/")
            except Exception:
                pass

        task_store.update(
            task_id,
            status="SUCCESS",
            progress=100,
            stage="视频生成完成",
            outputs=[{
                "video_url": video_url,
                "local_video": local_video,
                "video_serve_url": video_serve_url,
                "screenshot": r.get("final_screenshot"),
            }],
            intermediate={
                "page_url": r.get("page_url"),
                "task_uuid": r.get("task_uuid"),
                "prompt": r.get("prompt"),
            },
        )
        task_store.add_log(task_id, f"✓ 视频生成完成: {video_serve_url or local_video}")
    else:
        err = result.get("error", "未知错误")
        tb = result.get("traceback", "")
        task_store.update(task_id, status="FAILED", error=err, stage="失败")
        task_store.add_log(task_id, f"✗ 视频生成失败: {err}", level="error")
        if tb:
            task_store.add_log(task_id, tb, level="error")


# ── 阶段进度推断 ──
def _infer_stage_progress(line: str):
    """根据子进程 stdout 推断当前阶段和进度。"""
    line_lower = line.lower()
    if "导航到" in line or "page loaded" in line_lower:
        return "打开视频生成页面", 10
    if "登录" in line and "完成" in line:
        return "登录 weeksir", 15
    if "填写提示词" in line:
        return "填写提示词", 25
    if "上传参考图" in line or "上传成功" in line:
        return "上传商品图", 35
    if "选择模型" in line or "模型已切换" in line:
        return "选择 Seedance 2.0", 45
    if "设置参数" in line or "比例已设置" in line or "时长已设置" in line:
        return "设置参数（9:16 / 15s / 720p）", 55
    if "点击" in line and "生成" in line:
        return "已点击开始生成", 65
    if "检测到任务" in line or "task_uuid" in line_lower:
        return "任务已提交到 weeksir", 70
    if "生成中" in line or "等待视频" in line:
        return "等待视频生成", 80
    if "找到视频" in line or "下载视频" in line:
        return "下载视频", 90
    if "视频已保存" in line:
        return "视频已保存", 95
    if "step5 完成" in line:
        return "完成", 100
    return None, None


# ── 子进程 runner 脚本模板 ──
def _runner_script(param_file: Path, result_file: Path) -> str:
    """生成子进程执行的 Python 脚本。"""
    return f'''# -*- coding: utf-8 -*-
import json, sys, os, io, logging, traceback
os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, r"{Path(__file__).parent.parent.parent}")

# UTF-8 stdout
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

# 抑制噪音日志
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("playwright").setLevel(logging.WARNING)

params = json.loads(open(r"{param_file}", encoding="utf-8").read())
try:
    from pipeline.step5_video_generate import run
    # 多张商品图：第一张作为主图 product_image_path，其余作为 extra_images
    _main_img = params.get("product_image_path") or None
    _extra_imgs = [p for p in (params.get("product_image_paths") or []) if p and p != _main_img]
    # 如果没有主图但有多图，取第一张作为主图
    if not _main_img and _extra_imgs:
        _main_img = _extra_imgs.pop(0)
    result = run(
        video_path="",  # /video 页面不需要原视频
        product_image_path=_main_img,
        custom_prompt=params["compiled_prompt"],
        output_dir=params["output_dir"],
        headless=False,
        wait_for_result=True,
        timeout=900,
        aspect_ratio=params.get("aspect_ratio", "9:16"),
        duration=params.get("duration", "15s"),
        resolution=params.get("resolution", "720p"),
        extra_images=_extra_imgs or None,
    )
    with open(r"{result_file}", "w", encoding="utf-8") as f:
        json.dump({{"success": True, "result": result}}, f, ensure_ascii=False, default=str)
except Exception as e:
    tb = traceback.format_exc()
    with open(r"{result_file}", "w", encoding="utf-8") as f:
        json.dump({{"success": False, "error": str(e), "traceback": tb}}, f, ensure_ascii=False)
    print(f"ERROR: {{e}}", flush=True)
    print(tb, flush=True)
'''


# ── 历史记录 ──
def list_history(limit: int = 30) -> list:
    """列出历史任务。"""
    return task_store.list_by_type(TASK_TYPE, limit=limit)
