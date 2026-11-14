"""
============================================================================
⚠️ 爆款复刻工作流 - 受保护文件（整文件保护）
参见 BOUNDARY.md。修改本文件必须经过显式确认：
  1. 运行 python scripts/check_replica_boundary.py 记录变更
  2. 运行 pytest tests/test_replica_boundary.py -v 验证行为
  3. 运行 python scripts/update_replica_boundary.py 更新签名基准
未经流程确认的修改将破坏工作流稳定性。
============================================================================

Step2: FFmpeg 抽帧 + 分镜拼图
输入: 视频文件 + timeline.json
输出: frames/ 目录 + storyboard.jpg
"""
import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import List, Dict, Optional
from PIL import Image, ImageDraw, ImageFont


def _run_ffmpeg(video: Path, shots: list, frames_dir: Path, ffmpeg_bin: str = "ffmpeg") -> list:
    """用 ffmpeg 抽帧，失败则 fallback 到 opencv"""
    frames_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    ffmpeg_path = shutil.which(ffmpeg_bin)

    if ffmpeg_path:
        for item in shots:
            shot = int(item["shot"])
            t = float(item["time"])
            out = frames_dir / f"shot_{shot:02d}.jpg"
            cmd = [ffmpeg_path, "-y", "-ss", str(t), "-i", str(video),
                   "-vframes", "1", "-q:v", "2", str(out)]
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                outputs.append({"shot": shot, "time": t, "path": str(out)})
            except subprocess.CalledProcessError:
                outputs.extend(_run_opencv(video, shots, frames_dir))
                return outputs
        return outputs

    return _run_opencv(video, shots, frames_dir)


def _run_opencv(video: Path, shots: list, frames_dir: Path) -> list:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("未找到 ffmpeg 和 opencv-python，无法抽帧") from exc

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频: {video}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    outputs = []
    try:
        for item in shots:
            shot = int(item["shot"])
            t = float(item["time"])
            frame_idx = max(0, int(round(t * fps)))
            if frame_count:
                frame_idx = min(frame_idx, int(frame_count) - 1)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError(f"抽帧失败: shot={shot}, time={t}")
            out = frames_dir / f"shot_{shot:02d}.jpg"
            cv2.imwrite(str(out), frame)
            outputs.append({"shot": shot, "time": t, "path": str(out)})
    finally:
        cap.release()
    return outputs


def _layout_for_count(count: int):
    if count <= 5:
        cols = min(count, 3)
    elif count <= 8:
        cols = 4
    else:
        cols = 4
    rows = math.ceil(count / cols)
    return cols, rows


def _fit_image(img: Image.Image, tw: int, th: int) -> Image.Image:
    img = img.convert("RGB")
    ratio = min(tw / img.width, th / img.height)
    new = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (tw, th), "white")
    canvas.paste(new, ((tw - new.width) // 2, (th - new.height) // 2))
    return canvas


def _make_storyboard(frame_paths: list, out_path: Path, cell_width: int = 540) -> dict:
    loaded = [(fp, Image.open(fp)) for fp in frame_paths]
    if not loaded:
        return {}
    aspect = loaded[0][1].height / loaded[0][1].width
    cell_height = int(cell_width * aspect)
    cols, rows = _layout_for_count(len(loaded))
    canvas = Image.new("RGB", (cell_width * cols, cell_height * rows), "white")
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("arial.ttf", 28)
    except Exception:
        font = ImageFont.load_default()

    for idx, (fp, img) in enumerate(loaded):
        x = (idx % cols) * cell_width
        y = (idx // cols) * cell_height
        fitted = _fit_image(img, cell_width, cell_height)
        canvas.paste(fitted, (x, y))
        draw.rectangle([x, y, x + cell_width - 1, y + cell_height - 1], outline=(220, 220, 220), width=2)
        draw.rectangle([x + 10, y + 10, x + 145, y + 52], fill=(0, 0, 0))
        draw.text((x + 18, y + 17), f"Shot {idx+1:02d}", fill="white", font=font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, quality=95)
    return {"path": str(out_path), "cols": cols, "rows": rows, "count": len(loaded)}


def run(
    video_path: str,
    timeline: list,
    output_dir: str,
    ffmpeg_bin: str = "ffmpeg",
    cell_width: int = 540,
) -> dict:
    """
    Step2: 抽帧 + 拼图

    Args:
        video_path: 视频文件路径
        timeline: 时间轴列表 [{"shot": 1, "time": 0.83}, ...]
        output_dir: 输出目录
        ffmpeg_bin: ffmpeg 路径
        cell_width: 拼图单元格宽度

    Returns:
        {"frames": [...], "storyboard": {...}, "output_dir": str}
    """
    out = Path(output_dir)
    frames_dir = out / "frames"
    storyboard_path = out / "storyboard.jpg"

    # 抽帧
    frames = _run_ffmpeg(Path(video_path), timeline, frames_dir, ffmpeg_bin)
    frame_paths = [f["path"] for f in frames]

    # 拼图
    storyboard = _make_storyboard(frame_paths, storyboard_path, cell_width)

    # 保存结果
    result = {"frames": frames, "storyboard": storyboard, "output_dir": str(out)}
    with open(out / "step2_result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return result
