"""
字幕消除模块（基于 ProPainter 视频修复）

流程：
  1. 检测字幕区域（OCR 精确定位 / 底部默认区域）
  2. 生成 mask（白色=待修复区域，黑色=保留区域）
  3. 调用 ProPainter 进行视频 inpainting 修复
  4. 输出无字幕的干净视频

依赖：
  - ProPainter (sczhou/ProPainter) 克隆到项目目录
  - PaddleOCR（可选，用于精确定位字幕区域）
  - ffmpeg / ffprobe
  - PyTorch + CUDA（ProPainter 推理需要 GPU）
"""
import os
import sys
import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional, List, Tuple

# 添加项目根目录到 path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ═══════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════

def _get_video_info(video_path: str) -> dict:
    """获取视频信息（宽高、时长、帧率）"""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format",
        video_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe 失败: {proc.stderr}")

    data = json.loads(proc.stdout)
    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    return {
        "width": int(video_stream.get("width", 0)),
        "height": int(video_stream.get("height", 0)),
        "fps": _parse_fps(video_stream.get("r_frame_rate", "30/1")),
        "duration": float(data.get("format", {}).get("duration", 0)),
        "nb_frames": int(video_stream.get("nb_frames", 0)),
    }


def _parse_fps(rate_str: str) -> float:
    try:
        num, den = rate_str.split("/")
        return float(num) / float(den) if float(den) != 0 else 30.0
    except Exception:
        return 30.0


# ═══════════════════════════════════════
# Step A: 检测字幕区域
# ═══════════════════════════════════════

def detect_subtitle_region(
    video_path: str,
    method: str = "auto",
    sample_count: int = 5,
) -> List[Tuple[int, int, int, int]]:
    """
    检测视频中的字幕区域

    method:
      - "bottom": 默认底部区域（快，不依赖OCR）
      - "ocr": 用 PaddleOCR 精确定位（准，需要额外依赖）
      - "auto": 优先 OCR，失败回退底部区域

    返回: [(x, y, w, h), ...] 字幕矩形区域列表
    """
    info = _get_video_info(video_path)
    w, h = info["width"], info["height"]

    if method == "bottom":
        return [_default_bottom_region(w, h)]

    # 尝试 OCR
    try:
        regions = _detect_with_ocr(video_path, info, sample_count)
        if regions:
            return regions
    except Exception as e:
        print(f"  ⚠ OCR 检测失败: {e}，回退到默认底部区域")

    return [_default_bottom_region(w, h)]


def _default_bottom_region(width: int, height: int) -> Tuple[int, int, int, int]:
    """默认字幕区域：底部 15%-92% 高度，左右留 5% 边距"""
    x = int(width * 0.05)
    y = int(height * 0.78)
    w = int(width * 0.90)
    h = int(height * 0.20)
    return (x, y, w, h)


def _detect_with_ocr(
    video_path: str,
    info: dict,
    sample_count: int,
) -> List[Tuple[int, int, int, int]]:
    """用 PaddleOCR 在多帧上检测文字，合并得到字幕区域"""
    from paddleocr import PaddleOCR
    import cv2

    ocr = PaddleOCR(use_angle_cls=False, lang="ch", show_log=False)

    # 均匀采样帧
    duration = info["duration"]
    sample_times = [
        duration * (i + 1) / (sample_count + 1) for i in range(sample_count)
    ]

    all_boxes: List[List[float]] = []
    for t in sample_times:
        frame_path = _extract_frame(video_path, t)
        if not frame_path:
            continue
        result = ocr.ocr(frame_path, cls=False)
        if result and result[0]:
            for line in result[0]:
                box = line[0]  # [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
                all_boxes.append(box)
        os.remove(frame_path)

    if not all_boxes:
        return []

    # 合并所有检测框，取外接矩形
    xs = []
    ys = []
    for box in all_boxes:
        for px, py in box:
            xs.append(px)
            ys.append(py)

    if not xs:
        return []

    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)

    # 扩展边距避免边缘残留
    pad_x = int(info["width"] * 0.02)
    pad_y = int(info["height"] * 0.02)
    x = max(0, int(x_min) - pad_x)
    y = max(0, int(y_min) - pad_y)
    w = min(info["width"] - x, int(x_max - x_min) + pad_x * 2)
    h = min(info["height"] - y, int(y_max - y_min) + pad_y * 2)

    return [(x, y, w, h)]


def _extract_frame(video_path: str, timestamp: float) -> Optional[str]:
    """提取指定时间点的单帧"""
    import tempfile
    fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    cmd = [
        "ffmpeg", "-y", "-ss", str(timestamp),
        "-i", video_path, "-frames:v", "1",
        "-q:v", "2", tmp_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0 or not os.path.exists(tmp_path):
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return None
    return tmp_path


# ═══════════════════════════════════════
# Step B: 生成 mask
# ═══════════════════════════════════════

def generate_mask(
    video_path: str,
    regions: List[Tuple[int, int, int, int]],
    output_path: str,
) -> str:
    """
    生成与视频同尺寸的 mask 视频（白色=待修复，黑色=保留）

    输出 mask 视频路径
    """
    info = _get_video_info(video_path)
    w, h = info["width"], info["height"]

    # 先生成单张 mask 图片，再生成视频
    mask_img = output_path.replace(".mp4", ".png")
    _draw_mask_image(w, h, regions, mask_img)

    # 用 ffmpeg 将单帧 mask 图片转为与原视频等长的 mask 视频
    fps = info["fps"]
    duration = info["duration"]
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", mask_img,
        "-t", str(duration),
        "-r", str(fps),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-vf", f"scale={w}:{h}",
        output_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"mask 视频生成失败: {proc.stderr}")

    # 清理临时图片
    if os.path.exists(mask_img):
        os.remove(mask_img)

    print(f"  ✓ mask 视频已生成: {output_path}")
    return output_path


def _draw_mask_image(
    width: int,
    height: int,
    regions: List[Tuple[int, int, int, int]],
    output_path: str,
):
    """绘制黑白 mask 图片"""
    try:
        import numpy as np
        from PIL import Image, ImageDraw
    except ImportError:
        raise RuntimeError("需要安装 numpy 和 Pillow: pip install numpy pillow")

    img = Image.new("L", (width, height), 0)  # 全黑
    draw = ImageDraw.Draw(img)
    for (x, y, w, h) in regions:
        draw.rectangle([x, y, x + w, y + h], fill=255)  # 白色区域=待修复
    img.save(output_path)


# ═══════════════════════════════════════
# Step C: ProPainter 修复
# ═══════════════════════════════════════

def run_propainter(
    video_path: str,
    mask_path: str,
    output_dir: str,
    propainter_dir: Optional[str] = None,
    device: str = "cuda:0",
    neighbor_length: int = 10,
    subvideo_length: int = 80,
    raft_iter: int = 20,
) -> str:
    """
    调用 ProPainter 进行视频修复

    propainter_dir: ProPainter 仓库根目录，未指定则从 config 读取
    返回: 修复后的视频路径
    """
    from config import settings

    if propainter_dir is None:
        propainter_dir = getattr(settings, "PROPAINTER_DIR", "")

    if not propainter_dir or not os.path.isdir(propainter_dir):
        raise RuntimeError(
            "ProPainter 未安装。请克隆仓库: "
            "git clone https://github.com/sczhou/ProPainter.git"
        )

    propainter_dir = os.path.abspath(propainter_dir)
    os.makedirs(output_dir, exist_ok=True)

    # 准备输入（ProPainter 需要图片序列或视频 + mask）
    # 方式1: 直接传视频和 mask 视频给 inference_propainter.py
    infer_script = os.path.join(propainter_dir, "inference_propainter.py")

    cmd = [
        sys.executable, infer_script,
        "--video", os.path.abspath(video_path),
        "--mask", os.path.abspath(mask_path),
        "--output_dir", os.path.abspath(output_dir),
        "--mask_dilation", "4",
        "--neighbor_length", str(neighbor_length),
        "--subvideo_length", str(subvideo_length),
        "--raft_iter", str(raft_iter),
        "--save_fps", "30",
        "--device", device,
    ]

    print(f"  → 调用 ProPainter: {propainter_dir}")
    print(f"    命令: {' '.join(cmd)}")

    proc = subprocess.run(
        cmd,
        cwd=propainter_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    if proc.returncode != 0:
        raise RuntimeError(f"ProPainter 执行失败:\n{proc.stderr}")

    # ProPainter 输出在 output_dir 下，视频名通常为原视频名
    # 查找生成的 mp4
    result_video = None
    for root, dirs, files in os.walk(output_dir):
        for f in files:
            if f.endswith(".mp4"):
                result_video = os.path.join(root, f)
                break
        if result_video:
            break

    if not result_video:
        raise RuntimeError(f"ProPainter 未输出视频，检查输出目录: {output_dir}")

    print(f"  ✓ ProPainter 修复完成: {result_video}")
    return result_video


# ═══════════════════════════════════════
# 主入口
# ═══════════════════════════════════════

async def remove_subtitle(
    video_path: str,
    output_dir: str,
    detect_method: str = "auto",
    propainter_dir: Optional[str] = None,
    device: str = "cuda:0",
    status_callback=None,
) -> dict:
    """
    消除视频字幕（完整流程）

    参数:
      video_path: 原视频路径
      output_dir: 输出目录
      detect_method: "auto" | "ocr" | "bottom"
      propainter_dir: ProPainter 仓库目录
      device: 推理设备
      status_callback: 回调 (step, progress, message)

    返回: {
        "clean_video_path": str,
        "mask_path": str,
        "regions": list,
    }
    """
    os.makedirs(output_dir, exist_ok=True)

    def update(step, progress, message):
        if status_callback:
            status_callback(step, progress, message)

    # Step 1: 检测字幕区域
    update(1, 10, "正在检测字幕区域...")
    regions = detect_subtitle_region(video_path, method=detect_method)
    update(1, 30, f"检测到 {len(regions)} 个字幕区域: {regions}")

    # 保存检测信息
    with open(os.path.join(output_dir, "regions.json"), "w", encoding="utf-8") as f:
        json.dump(regions, f, ensure_ascii=False, indent=2)

    # Step 2: 生成 mask
    update(2, 40, "正在生成 mask...")
    mask_path = os.path.join(output_dir, "subtitle_mask.mp4")
    generate_mask(video_path, regions, mask_path)
    update(2, 55, "mask 生成完成")

    # Step 3: ProPainter 修复
    update(3, 60, "正在调用 ProPainter 进行视频修复（可能需要数分钟）...")
    propainter_output = os.path.join(output_dir, "propainter_out")
    clean_video = run_propainter(
        video_path=video_path,
        mask_path=mask_path,
        output_dir=propainter_output,
        propainter_dir=propainter_dir,
        device=device,
    )
    update(3, 95, "视频修复完成")

    # 复制到标准输出位置
    final_path = os.path.join(output_dir, "clean_video.mp4")
    if clean_video != final_path:
        shutil.copy2(clean_video, final_path)

    update(3, 100, "字幕消除完成")

    return {
        "clean_video_path": final_path,
        "mask_path": mask_path,
        "regions": regions,
    }
