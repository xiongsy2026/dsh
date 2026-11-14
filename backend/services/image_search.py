"""
图片检索与生成（混合方案）
1. 原视频抽帧：从视频中提取关键帧作为配图候选
2. AI 生图：用 Image2 API 根据文案关键词生成补充配图
3. 选择策略：优先抽帧，不足部分用 AI 生图补齐
"""
import os
import asyncio
import subprocess
import httpx
from typing import Optional
from pathlib import Path
from config import settings


# ═══════════════════════════════════════
# 1. 视频抽帧
# ═══════════════════════════════════════

async def extract_key_frames(
    video_path: str,
    output_dir: str,
    count: int = 5,
    status_callback=None,
) -> list[str]:
    """
    从视频中均匀抽取 N 帧作为配图
    返回图片路径列表
    """
    if status_callback:
        await status_callback(f"从视频抽取 {count} 张关键帧...")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 先获取视频时长
    probe_cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    try:
        proc = subprocess.run(probe_cmd, capture_output=True, text=True, encoding="utf-8")
        duration = float(proc.stdout.strip())
    except Exception:
        duration = 30.0  # 默认假设30秒

    if duration <= 0:
        duration = 30.0

    # 均匀分点（避开开头0.5秒和结尾0.5秒）
    step = (duration - 1.0) / max(count, 1)
    timestamps = [0.5 + i * step for i in range(count)]

    paths = []
    for i, ts in enumerate(timestamps):
        out_file = output_dir / f"frame_{i:02d}_{int(ts)}s.jpg"
        cmd = [
            "ffmpeg", "-ss", f"{ts:.2f}", "-i", video_path,
            "-vframes", "1", "-q:v", "3",
            "-y", str(out_file),
        ]
        try:
            subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=30)
            if out_file.exists() and out_file.stat().st_size > 1024:
                paths.append(str(out_file))
        except Exception:
            continue

    if status_callback:
        await status_callback(f"抽帧完成：{len(paths)} 张")

    return paths


# ═══════════════════════════════════════
# 2. AI 生图（复用 Image2）
# ═══════════════════════════════════════

async def generate_image_by_keyword(
    keyword: str,
    output_dir: str,
    size: str = "1024x1024",
    status_callback=None,
) -> str:
    """根据关键词用 Image2 API 生成图片"""
    if status_callback:
        await status_callback(f"AI 生图：{keyword}")

    from backend.services.image2_client import image2_client

    result = await image2_client.text_to_image_sync(
        prompt=f"高质量摄影图：{keyword}，清晰、明亮、构图美观",
        size=size,
    )

    image_url = image2_client._get_result_image(result)

    # 下载图片到本地
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"ai_{abs(hash(keyword))}.png"

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(image_url)
        resp.raise_for_status()
        output_path.write_bytes(resp.content)

    return str(output_path)


# ═══════════════════════════════════════
# 3. 混合配图策略
# ═══════════════════════════════════════

async def select_images_for_article(
    video_path: str,
    story_keywords: list[str],
    output_dir: str,
    target_count: int = 5,
    status_callback=None,
) -> list[str]:
    """
    混合配图：
    - 先从原视频抽帧（保留原视频素材）
    - 不足部分用 AI 生图（根据关键词）
    """
    # 抽帧
    frame_count = min(target_count, max(2, target_count - len(story_keywords) // 2))
    frame_paths = await extract_key_frames(
        video_path, output_dir, count=frame_count, status_callback=status_callback
    )

    # 不足部分用 AI 生图补齐
    ai_paths = []
    needed = max(0, target_count - len(frame_paths))
    if needed > 0 and story_keywords:
        # 取前 needed 个关键词
        selected_keywords = story_keywords[:needed]
        tasks = [
            generate_image_by_keyword(kw, output_dir, status_callback=status_callback)
            for kw in selected_keywords
        ]
        try:
            ai_paths = await asyncio.gather(*tasks, return_exceptions=True)
            ai_paths = [p for p in ai_paths if isinstance(p, str)]
        except Exception as e:
            if status_callback:
                await status_callback(f"AI 生图部分失败: {e}")

    all_paths = frame_paths + ai_paths
    return all_paths[:target_count]


if __name__ == "__main__":
    # 自测
    import sys
    result = asyncio.run(extract_key_frames(sys.argv[1], "outputs/toutiao_test/frames", 3))
    print(result)
