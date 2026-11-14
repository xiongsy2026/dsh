"""钩子延展工作流（基于 zexapi Omni 续接 API）

工作流：
  Step 1: 裁剪原视频前 5 秒作为钩子段
  Step 2: 上传钩子段到 CDN，获得可访问 URL
  Step 3: 调用 omni_flash-10s 提交任务（video_url=钩子 + prompt=延展描述）
  Step 4: 轮询任务状态
  Step 5: 下载结果视频到 outputs 目录

输入约束：
  - 原视频：任意时长（前端可选 0-X 秒作为钩子起点）
  - 钩子段长度：默认 5 秒（≤10 秒，跟 omni_flash-10s 输出 10 秒相加 ≤ 20 秒）
  - prompt：用户输入延展描述（描述钩子之后发生什么）

输出：
  - 1 个 10 秒延展视频（前段衔接钩子，后段为延展内容）
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from config import settings
from backend.services import omni_client, wan3_client
from backend.services.super_clone_wan3 import _ffprobe_duration, trim_video

logger = logging.getLogger(__name__)

# 默认钩子段长度（秒）
DEFAULT_HOOK_DURATION = 5.0
# Omni 输出固定 10 秒
OMNI_OUTPUT_DURATION = 10.0


def step1_extract_hook(
    video_path: str,
    hook_duration: float,
    output_dir: str,
    hook_start: float = 0.0,
) -> str:
    """Step 1: 从原视频裁剪钩子段。

    参数：
        video_path: 原视频路径
        hook_duration: 钩子段时长（秒）
        output_dir: 输出目录
        hook_start: 钩子段起点（默认 0，即原视频开头）

    返回：
        钩子段本地路径
    """
    src_dur = _ffprobe_duration(video_path) or 0.0
    if src_dur <= 0:
        raise ValueError(f"无法探测视频时长: {video_path}")

    hook_end = min(hook_start + hook_duration, src_dur)
    actual_duration = hook_end - hook_start
    if actual_duration < 1.0:
        raise ValueError(f"钩子段过短（{actual_duration:.2f}s），原视频时长 {src_dur:.2f}s")

    hook_path = str(Path(output_dir) / "hook_segment.mp4")
    trim_video(video_path, hook_start, hook_end, hook_path)
    return hook_path


def step2_upload_hook(hook_path: str) -> str:
    """Step 2: 上传钩子段到 CDN（复用 wan3_client 的上传函数）。

    返回：
        公网可访问的钩子段 URL
    """
    return wan3_client.upload_media(hook_path)


def step3_submit_omni(
    prompt: str,
    hook_url: str,
    image_urls: Optional[list] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Step 3: 提交 omni 视频生成任务。

    返回：
        {"task_id": str, "raw": <完整响应>}
    """
    return omni_client.submit_omni_task(
        prompt=prompt,
        video_url=hook_url,
        image_urls=image_urls,
        model=model,
    )


def step4_poll_until_done(
    task_id: str,
    timeout: int = 600,
    on_progress: Optional[Callable[[str, int, str], None]] = None,
) -> Dict[str, Any]:
    """Step 4: 轮询任务直到完成。

    返回：
        omni_client.fetch_omni_task 标准化结果
    """
    return omni_client.wait_for_completion(
        task_id=task_id,
        timeout=timeout,
        poll_interval=8,
        on_progress=on_progress,
    )


def step5_download_result(
    video_url: str,
    output_dir: str,
    filename: str = "hook_extend_result.mp4",
) -> str:
    """Step 5: 下载结果视频到本地。

    返回：
        本地结果视频路径
    """
    save_path = str(Path(output_dir) / filename)
    return omni_client.download_video(video_url, save_path)


async def run_pipeline(
    task_id: str,
    video_path: str,
    extend_prompt: str,
    output_dir: str,
    hook_duration: float = DEFAULT_HOOK_DURATION,
    hook_start: float = 0.0,
    image_urls: Optional[list] = None,
    model: Optional[str] = None,
    on_log: Optional[Callable[[str], None]] = None,
    on_phase: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """钩子延展顶层编排。

    工作流：
      Step 1: 裁剪原视频前 5 秒作钩子段
      Step 2: 上传钩子段到 CDN
      Step 3: 提交 omni 任务（video_url=钩子 + prompt=延展）
      Step 4: 轮询任务状态
      Step 5: 下载结果视频

    参数：
        task_id: 任务 ID
        video_path: 原视频本地路径
        extend_prompt: 延展描述（描述钩子之后发生什么）
        output_dir: 输出目录
        hook_duration: 钩子段时长（秒，默认 5）
        hook_start: 钩子段起点（秒，默认 0）
        image_urls: 参考图片 URL 列表（可选）
        model: omni 模型名（默认用配置里的 omni_flash-10s）
        on_log: 日志回调
        on_phase: 阶段回调

    返回：
        {
            "task_id": str,
            "hook_path": str,
            "hook_url": str,
            "omni_task_id": str,
            "result_url": str,
            "result_path": str,
            "duration": float,
        }
    """
    def _log(msg: str, level: str = "info"):
        if on_log:
            on_log(f"[{level.upper()}] {msg}")
        else:
            logger.info(msg)

    def _phase(phase: str, data: Optional[dict] = None):
        if on_phase:
            on_phase(phase, data or {})

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    _log(f"开始钩子延展任务 {task_id}")
    _log(f"原视频: {video_path}")
    _log(f"延展 prompt: {extend_prompt[:80]}...")

    # ── Step 1: 裁剪钩子段 ──
    _phase("step1", {"description": "裁剪钩子段"})
    _log("Step 1: 裁剪钩子段...")
    hook_path = await asyncio.to_thread(
        step1_extract_hook,
        video_path, hook_duration, output_dir, hook_start,
    )
    hook_actual_dur = _ffprobe_duration(hook_path) or 0.0
    _log(f"✓ 钩子段裁剪完成: {hook_path} ({hook_actual_dur:.2f}s)", "success")
    _phase("step1_done", {
        "hook_path": hook_path,
        "hook_duration": hook_actual_dur,
    })

    # ── Step 2: 上传钩子段 ──
    _phase("step2", {"description": "上传钩子段到 CDN"})
    _log("Step 2: 上传钩子段到 CDN...")
    hook_url = await asyncio.to_thread(step2_upload_hook, hook_path)
    _log(f"✓ 钩子段已上传: {hook_url[:80]}...", "success")
    _phase("step2_done", {"hook_url": hook_url})

    # ── Step 3: 提交 omni 任务 ──
    _phase("step3", {"description": "提交 Omni 续接任务"})
    _log(f"Step 3: 提交 omni 任务（model={model or 'default'}）...")
    submit_resp = await asyncio.to_thread(
        step3_submit_omni, extend_prompt, hook_url, image_urls, model,
    )
    omni_task_id = submit_resp["task_id"]
    _log(f"✓ Omni 任务已提交: {omni_task_id}", "success")
    _phase("step3_done", {"omni_task_id": omni_task_id})

    # ── Step 4: 轮询任务状态 ──
    _phase("step4", {"description": "Omni 生成中（约 90-120 秒）"})

    def _on_progress(status: str, progress: int, msg: str):
        _log(f"Omni {msg}")
        if on_phase:
            on_phase("step4_progress", {"status": status, "progress": progress, "msg": msg})

    result = await asyncio.to_thread(
        step4_poll_until_done, omni_task_id, 600, _on_progress,
    )

    if result["status"] != "completed":
        err = result.get("error") or "未知错误"
        _log(f"✗ Omni 任务失败: {err}", "error")
        _phase("step4_failed", {"error": err})
        raise RuntimeError(f"Omni 任务失败: {err}")

    result_url = result.get("video_url")
    if not result_url:
        err = "Omni 任务完成但未返回 video_url"
        _log(f"✗ {err}", "error")
        _phase("step4_failed", {"error": err})
        raise RuntimeError(err)

    _log(f"✓ Omni 任务完成: {result_url[:80]}...", "success")
    _phase("step4_done", {"result_url": result_url})

    # ── Step 5: 下载结果视频 ──
    _phase("step5", {"description": "下载结果视频"})
    _log("Step 5: 下载结果视频...")
    timestamp = int(time.time())
    filename = f"hook_extend_{task_id}_{timestamp}.mp4"
    result_path = await asyncio.to_thread(
        step5_download_result, result_url, output_dir, filename,
    )
    result_duration = _ffprobe_duration(result_path) or 0.0
    _log(f"✓ 结果已下载: {result_path} ({result_duration:.2f}s)", "success")
    _phase("step5_done", {
        "result_path": result_path,
        "result_url": result_url,
        "duration": result_duration,
    })

    return {
        "task_id": task_id,
        "hook_path": hook_path,
        "hook_url": hook_url,
        "omni_task_id": omni_task_id,
        "result_url": result_url,
        "result_path": result_path,
        "duration": result_duration,
    }
