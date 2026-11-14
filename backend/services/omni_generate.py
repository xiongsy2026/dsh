"""Omni 出片高层入口（Weeksirs omni skill P2 约定）

包装 omni_client 的原子函数，提供 AGENTS.md 约定的调用形状：

    from backend.services.omni_generate import run_omni
    result = run_omni(
        prompt="In a single continuous 10-second shot, ...",
        model="omni_flash-10s",
        duration=10,
        aspect_ratio="16:9",
        output_dir="productions/<task>/outputs/",
    )
    # -> {"success": True, "video_path": "...", "task_id": "..."}

约定：
- 不打开 8090，不另起服务；直接 HTTP 调 zexapi.com/v1。
- duration 仅允许 5/8/10（omni_flash-10s 单段上限 10s）。
- 超时 600s；轮询间隔 8s；完成后自动下载 mp4 到 output_dir。
- 客户端失败（HTTP/网络/缺 task_id）抛出时返回 success=False，不抛异常。
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Any, Dict, Optional

from backend.services.omni_client import (
    OmniError,
    download_video,
    submit_omni_task,
    wait_for_completion,
)


def run_omni(
    prompt: str,
    model: str = "omni_flash-10s",
    duration: int = 10,
    aspect_ratio: str = "16:9",
    output_dir: Optional[str] = None,
    poll_timeout: int = 600,
    poll_interval: int = 8,
    api_key: Optional[str] = None,
    video_url: Optional[str] = None,
    image_urls: Optional[list] = None,
) -> Dict[str, Any]:
    """提交一次 omni_flash-10s 生成并等待完成。

    参数：
        prompt: 英文 prompt（中文仅产品名/印花/口播引号）
        model: 默认 omni_flash-10s
        duration: 5/8/10（模型上限 10s；更长按 skill 拆段 + ffmpeg concat）
        aspect_ratio: 16:9 / 9:16 / 1:1
        output_dir: 成片 mp4 落盘目录；None 则下载到当前工作目录
        video_url / image_urls: 可选参考（本 skill 纯 t2v 时不传）

    返回：
        {"success": bool, "video_path": str|None, "task_id": str|None,
         "status": str, "error": str|None, "raw": dict|None}
    """
    if duration not in (5, 8, 10):
        raise ValueError(f"duration must be 5/8/10, got {duration}")
    if not (prompt or "").strip():
        raise ValueError("prompt is empty")

    try:
        submitted = submit_omni_task(
            prompt=prompt,
            video_url=video_url,
            image_urls=image_urls,
            model=model,
            api_key=api_key,
        )
    except OmniError as e:
        return {"success": False, "video_path": None, "task_id": None,
                "status": "submit_failed", "error": str(e), "raw": None}

    task_id = submitted["task_id"]
    final = wait_for_completion(
        task_id,
        timeout=poll_timeout,
        poll_interval=poll_interval,
        api_key=api_key,
    )

    if final["status"] != "completed" or not final.get("video_url"):
        return {"success": False, "video_path": None, "task_id": task_id,
                "status": final["status"], "error": final.get("error"),
                "raw": final.get("raw")}

    out_dir = output_dir or "."
    os.makedirs(out_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    save_path = os.path.join(out_dir, f"omni-{ts}-{uuid.uuid4().hex[:6]}.mp4")

    try:
        download_video(final["video_url"], save_path, api_key=api_key)
    except OmniError as e:
        return {"success": False, "video_path": None, "task_id": task_id,
                "status": "download_failed", "error": str(e),
                "raw": final.get("raw")}

    return {"success": True, "video_path": save_path, "task_id": task_id,
            "status": "completed", "error": None, "raw": final.get("raw")}
