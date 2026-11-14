"""RunningHub 视频换脸服务 - 基于 AI 应用 1969708911165140993

使用方式:
    from backend.services.video_face_swap import run_face_swap
    result = await run_face_swap(
        video_path="/path/to/video.mp4",
        face_image_path="/path/to/face.png",
        output_dir="/path/to/output",
    )
    # result = {"output_path": "...", "task_id": "...", "duration": float}

API 文档: https://www.runninghub.ai/openapi/v2/run/ai-app/1969708911165140993
"""
from __future__ import annotations

import asyncio
import subprocess
import logging
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ── 常量 ──
APP_ID = "1969708911165140993"

# 节点映射（来自 RunningHub API 文档）
NODE_VIDEO = "448"       # 原视频（Character video）
NODE_FACE_IMAGE = "451"  # 新人脸图（半身正面效果更好）
NODE_DURATION = "447"    # 生成秒数（非会员 ≤10s）
NODE_WIDTH = "443"       # 输出宽度
NODE_HEIGHT = "444"      # 输出高度

# 默认参数
DEFAULT_WIDTH = 576
DEFAULT_HEIGHT = 1024
MAX_DURATION_NON_MEMBER = 10  # 非会员最大秒数


def _ffprobe_duration(video_path: str) -> float:
    """获取视频时长（秒）"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, encoding="utf-8",
        )
        return float(result.stdout.strip()) if result.stdout.strip() else 0.0
    except Exception:
        return 0.0


async def run_face_swap(
    video_path: str,
    face_image_path: str,
    output_dir: str,
    duration: Optional[float] = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    instance_type: str = "plus",
    api_key: Optional[str] = None,
    on_log: Optional[Callable[[str], None]] = None,
) -> dict:
    """视频换脸主函数

    Args:
        video_path: 原视频路径（含人脸的视频）
        face_image_path: 新人脸图片路径（半身正面效果更好）
        output_dir: 输出目录
        duration: 生成秒数（默认取原视频时长，非会员 ≤10s）
        width: 输出宽度（默认 576）
        height: 输出高度（默认 1024）
        instance_type: 实例类型（default/plus/ultra）
        api_key: RunningHub API Key（默认用 config 里的）
        on_log: 日志回调

    Returns:
        {
            "output_path": str,   # 换脸后的视频路径
            "task_id": str,       # RunningHub 任务 ID
            "duration": float,    # 生成时长
        }

    Raises:
        RuntimeError: 上传失败、提交失败、生成失败
    """
    from backend.services.video_translate_pipeline import (
        _upload_runninghub_ai_resource,
        _run_runninghub_ai_app,
    )
    from config import settings

    api_key = api_key or settings.RUNNINGHUB_ENTERPRISE_API_KEY
    if not api_key:
        raise RuntimeError("RunningHub API Key 未配置")

    def log(msg: str) -> None:
        if on_log:
            on_log(msg)
        logger.info(f"[FaceSwap] {msg}")

    # ── 校验输入文件 ──
    if not Path(video_path).exists():
        raise RuntimeError(f"视频不存在: {video_path}")
    if not Path(face_image_path).exists():
        raise RuntimeError(f"人脸图片不存在: {face_image_path}")

    # ── 确定时长 ──
    if duration is None:
        duration = _ffprobe_duration(video_path)
        if duration <= 0:
            duration = MAX_DURATION_NON_MEMBER
            log(f"⚠ 无法获取原视频时长，使用默认 {duration}s")
    duration = min(duration, MAX_DURATION_NON_MEMBER)
    duration = max(1, round(duration))
    log(f"生成时长: {duration}s (instance={instance_type})")

    # ── 准备输出目录 ──
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = str(out_dir / "face_swapped.mp4")

    # ── 上传素材 ──
    log("上传原视频...")
    video_resource = await _upload_runninghub_ai_resource(video_path, api_key=api_key)
    log(f"  视频: {str(video_resource)[:60]}...")

    log("上传新人脸图...")
    face_resource = await _upload_runninghub_ai_resource(face_image_path, api_key=api_key)
    log(f"  人脸: {str(face_resource)[:60]}...")

    # ── 构建节点参数 ──
    node_info_list = [
        {
            "nodeId": NODE_VIDEO,
            "fieldName": "video",
            "fieldValue": video_resource,
            "description": "Character video",
        },
        {
            "nodeId": NODE_FACE_IMAGE,
            "fieldName": "image",
            "fieldValue": face_resource,
            "description": "New face (half-body, frontal view effect is better)",
        },
        {
            "nodeId": NODE_DURATION,
            "fieldName": "value",
            "fieldValue": str(duration),
            "description": "Generate seconds",
        },
        {
            "nodeId": NODE_WIDTH,
            "fieldName": "value",
            "fieldValue": str(width),
            "description": "Width",
        },
        {
            "nodeId": NODE_HEIGHT,
            "fieldName": "value",
            "fieldValue": str(height),
            "description": "Height",
        },
    ]

    # ── 提交任务并等待完成 ──
    log(f"提交换脸任务 (app_id={APP_ID})...")
    result_path = await _run_runninghub_ai_app(
        app_id=APP_ID,
        node_info_list=node_info_list,
        output_path=output_path,
        api_key=api_key,
    )

    if not result_path or not Path(result_path).exists():
        raise RuntimeError("换脸任务完成但未生成输出文件")

    actual_dur = _ffprobe_duration(result_path)
    log(f"✓ 换脸完成 → {result_path} ({actual_dur:.1f}s)")

    return {
        "output_path": result_path,
        "task_id": Path(result_path).stem,  # 从文件名提取 task_id
        "duration": actual_dur,
    }


# ── 长视频分段换脸 ──

async def run_face_swap_segmented(
    video_path: str,
    face_image_path: str,
    output_dir: str,
    segment_seconds: float = 10,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    instance_type: str = "plus",
    api_key: Optional[str] = None,
    on_log: Optional[Callable[[str], None]] = None,
) -> dict:
    """长视频分段换脸：超过 segment_seconds 的视频自动切段，逐段换脸再拼接。

    Args:
        video_path: 原视频路径
        face_image_path: 新人脸图片路径
        output_dir: 输出目录
        segment_seconds: 每段最大时长（默认 10 秒，非会员上限）
        width: 输出宽度
        height: 输出高度
        instance_type: 实例类型
        api_key: RunningHub API Key
        on_log: 日志回调

    Returns:
        {
            "output_path": str,       # 最终拼接后的视频
            "segment_paths": list,    # 各段视频路径
            "total_duration": float,  # 总时长
        }
    """
    import subprocess

    def log(msg: str) -> None:
        if on_log:
            on_log(msg)
        logger.info(f"[FaceSwap] {msg}")

    total_dur = _ffprobe_duration(video_path)
    if total_dur <= 0:
        raise RuntimeError(f"无法获取视频时长: {video_path}")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 短视频：直接整段换脸
    if total_dur <= segment_seconds + 0.5:
        log(f"短视频 ({total_dur:.1f}s ≤ {segment_seconds}s)，直接整段换脸")
        result = await run_face_swap(
            video_path=video_path,
            face_image_path=face_image_path,
            output_dir=str(out_dir / "single"),
            duration=total_dur,
            width=width,
            height=height,
            instance_type=instance_type,
            api_key=api_key,
            on_log=on_log,
        )
        return {
            "output_path": result["output_path"],
            "segment_paths": [result["output_path"]],
            "total_duration": result["duration"],
        }

    # 长视频：分段处理
    import math
    n_segments = math.ceil(total_dur / segment_seconds)
    actual_seg_dur = total_dur / n_segments
    log(f"长视频 ({total_dur:.1f}s)，拆为 {n_segments} 段，每段 {actual_seg_dur:.1f}s")

    segment_paths = []
    for i in range(n_segments):
        start = i * actual_seg_dur
        end = min((i + 1) * actual_seg_dur, total_dur)
        seg_dur = end - start

        seg_dir = out_dir / f"seg_{i:02d}"
        seg_dir.mkdir(parents=True, exist_ok=True)

        # 切段
        seg_video = str(seg_dir / "input.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error",
             "-ss", f"{start:.3f}", "-t", f"{seg_dur:.3f}",
             "-i", video_path,
             "-c:v", "libx264", "-preset", "fast", "-crf", "18",
             "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "128k",
             seg_video],
            check=True,
        )

        log(f"段 {i+1}/{n_segments}: {start:.1f}-{end:.1f}s ({seg_dur:.1f}s)")
        result = await run_face_swap(
            video_path=seg_video,
            face_image_path=face_image_path,
            output_dir=str(seg_dir),
            duration=seg_dur,
            width=width,
            height=height,
            instance_type=instance_type,
            api_key=api_key,
            on_log=on_log,
        )
        segment_paths.append(result["output_path"])

    # 拼接
    final_path = str(out_dir / "face_swapped_full.mp4")
    concat_list = out_dir / "concat.txt"
    concat_list.write_text(
        "".join(f"file '{Path(p).as_posix()}'\n" for p in segment_paths),
        encoding="utf-8",
    )
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "concat", "-safe", "0", "-i", str(concat_list),
         "-c:v", "libx264", "-preset", "fast", "-crf", "18",
         "-c:a", "aac", "-b:a", "128k",
         "-movflags", "+faststart",
         final_path],
        check=True,
    )

    final_dur = _ffprobe_duration(final_path)
    log(f"✓ 分段换脸完成 → {final_path} ({final_dur:.1f}s, {n_segments} 段)")

    return {
        "output_path": final_path,
        "segment_paths": segment_paths,
        "total_duration": final_dur,
    }
