"""视频去 logo 管线
================
Pipeline:
  1. ffmpeg 抽帧
  2. SAM2 视频分割（在种子帧点击 → 自动追踪到全部帧）
  3. ProPainter 视频修复
  4. ffmpeg 合成（复用原视频音频）

用法：
  python -m pipeline.remove_video_logo --video path/to/video.mp4 --output out.mp4 \
      --seed-frame 150 --seed-x 285 --seed-y 662

也可以只跑某一步：
  python -m pipeline.remove_video_logo --video ... --step extract
  python -m pipeline.remove_video_logo --step segment --work-dir work/
  python -m pipeline.remove_video_logo --step inpaint --work-dir work/
  python -m pipeline.remove_video_logo --step assemble --work-dir work/ --video ...
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

# 路径常量
ROOT = Path(__file__).resolve().parent.parent
SAM2_REPO = ROOT / "models" / "sam2"
SAM2_CKPT = SAM2_REPO / "checkpoints" / "sam2.1_hiera_small.pt"
SAM2_CFG = "configs/sam2.1/sam2.1_hiera_s.yaml"
PROPAINTER_DIR = ROOT / "models" / "ProPainter"


def run(cmd: list[str], **kw):
    print(f"[RUN] {' '.join(str(c) for c in cmd)}")
    return subprocess.run(cmd, check=True, **kw)


# ---------- Step 1: 抽帧 ----------
def extract_frames(video: Path, frames_dir: Path):
    """用 ffmpeg 抽帧为 00000.jpg / 00001.jpg / ..."""
    frames_dir.mkdir(parents=True, exist_ok=True)
    # 清空
    for p in frames_dir.glob("*.jpg"):
        p.unlink()

    # 获取 fps
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate,nb_frames",
         "-of", "default=nokey=1:noprint_wrappers=1", str(video)],
        capture_output=True, text=True,
    )
    fps_info = probe.stdout.strip().split("\n")
    fps_str = fps_info[0] if fps_info else "30/1"
    # 例如 30/1
    if "/" in fps_str:
        num, den = fps_str.split("/")
        fps = float(num) / float(den) if float(den) != 0 else 30.0
    else:
        fps = float(fps_str) if fps_str else 30.0
    print(f"[INFO] fps={fps}")

    # 抽帧（保持原始 fps）
    run([
        "ffmpeg", "-i", str(video),
        "-vf", "scale=-1:-1",  # 不缩放
        "-q:v", "2",  # 高质量 jpeg
        "-y",
        str(frames_dir / "%05d.jpg"),
    ], capture_output=True)
    # 注意 ffmpeg 编号从 00001 开始，SAM2 期望从 0 开始
    # 重命名为 0-based
    files = sorted(frames_dir.glob("*.jpg"))
    # 先全部加前缀
    for f in files:
        f.rename(f.with_name("_" + f.name))
    for i, f in enumerate(sorted(frames_dir.glob("*.jpg"))):
        f.rename(frames_dir / f"{i:05d}.jpg")

    n = len(list(frames_dir.glob("*.jpg")))
    print(f"[INFO] 抽帧完成: {n} 帧 -> {frames_dir}")
    return fps, n


# ---------- Step 2: SAM2 分割 ----------
def segment_logo_with_sam2(
    frames_dir: Path,
    masks_dir: Path,
    seed_frame: int,
    seed_x: int,
    seed_y: int,
    vis_dir: Path | None = None,
):
    """用 SAM2 video predictor 分割 logo 并传播到所有帧。

    Args:
        frames_dir: 帧目录（含 00000.jpg, 00001.jpg, ...）
        masks_dir: 输出 mask 目录
        seed_frame: 种子帧索引（0-based）
        seed_x, seed_y: 种子点坐标
        vis_dir: 可视化目录（可选）
    """
    # 把 sam2 加入 sys.path
    if str(SAM2_REPO) not in sys.path:
        sys.path.insert(0, str(SAM2_REPO))

    import torch
    from sam2.build_sam import build_sam2_video_predictor

    masks_dir.mkdir(parents=True, exist_ok=True)
    for p in masks_dir.glob("*.png"):
        p.unlink()
    if vis_dir:
        vis_dir.mkdir(parents=True, exist_ok=True)
        for p in vis_dir.glob("*.png"):
            p.unlink()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[INFO] device={device}")
    print(f"[INFO] 加载 SAM2: {SAM2_CKPT.name}")

    predictor = build_sam2_video_predictor(
        SAM2_CFG, str(SAM2_CKPT), device=device
    )

    # init_state
    inference_state = predictor.init_state(video_path=str(frames_dir))

    # 在种子帧添加点击
    print(f"[INFO] 种子: frame={seed_frame} point=({seed_x},{seed_y})")
    points = np.array([[seed_x, seed_y]], dtype=np.float32)
    labels = np.array([1], dtype=np.int32)  # 1 = 正向点击
    _, out_obj_ids, out_mask_logits = predictor.add_new_points_or_box(
        inference_state=inference_state,
        frame_idx=seed_frame,
        obj_id=1,
        points=points,
        labels=labels,
    )

    # 查看种子帧 mask
    seed_mask = (out_mask_logits[0] > 0.0).cpu().numpy()
    if seed_mask.ndim == 3:
        seed_mask = seed_mask[0]
    print(f"[INFO] 种子 mask: shape={seed_mask.shape} 像素数={int(seed_mask.sum())}")
    if int(seed_mask.sum()) == 0:
        raise RuntimeError("种子点未生成 mask，请检查坐标")

    # 传播到所有帧
    print(f"[INFO] 传播 mask 到所有帧...")
    video_segments = {}  # frame_idx -> {obj_id: mask}
    for out_frame_idx, out_obj_ids, out_mask_logits in predictor.propagate_in_video(inference_state):
        video_segments[out_frame_idx] = {
            oid: (out_mask_logits[i] > 0.0).cpu().numpy()
            for i, oid in enumerate(out_obj_ids)
        }

    print(f"[INFO] 传播完成: {len(video_segments)} 帧")

    # 保存 mask（white=logo）
    frame_files = sorted(frames_dir.glob("*.jpg"))
    for idx in range(len(frame_files)):
        mask_data = video_segments.get(idx, {}).get(1)
        h, w = 1000, 750  # 占位，会被实际尺寸覆盖
        if mask_data is not None:
            if mask_data.ndim == 3:
                mask_data = mask_data[0]
            mask_uint8 = (mask_data.astype(np.uint8) * 255)
        else:
            # 没追踪到的帧，保存空 mask
            mask_uint8 = np.zeros((h, w), dtype=np.uint8)
        Image.fromarray(mask_uint8).save(masks_dir / f"{idx:05d}.png")

        # 可视化
        if vis_dir and mask_data is not None:
            frame_img = Image.open(frame_files[idx]).convert("RGB")
            vis = np.array(frame_img).copy()
            mask_bool = mask_data if mask_data.ndim == 2 else mask_data[0]
            vis[mask_bool] = [255, 0, 0]  # 红色高亮 logo
            Image.fromarray(vis).save(vis_dir / f"{idx:05d}.png")

    # 统计
    non_empty = sum(1 for p in masks_dir.glob("*.png") if np.array(Image.open(p)).sum() > 0)
    print(f"[INFO] mask 保存到: {masks_dir}（非空 {non_empty}/{len(frame_files)}）")
    if vis_dir:
        print(f"[INFO] 可视化保存到: {vis_dir}")


# ---------- Step 3: ProPainter 修复 ----------
def inpaint_with_propainter(
    frames_dir: Path,
    masks_dir: Path,
    output_dir: Path,
    fps: float,
    mask_dilation: int = 4,
):
    """调用 ProPainter 修复。

    输出: output_dir/<video_name>_inpaint_out.mp4
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    for p in output_dir.glob("*"):
        p.unlink()

    # 调用 ProPainter 的 inference_propainter.py
    cmd = [
        sys.executable, "inference_propainter.py",
        "-i", str(frames_dir),
        "-m", str(masks_dir),
        "-o", str(output_dir),
        "--mask_dilation", str(mask_dilation),
        "--save_fps", str(int(round(fps))),
        "--mode", "video_inpainting",
    ]
    print(f"[INFO] 调用 ProPainter: {' '.join(cmd)}")
    run(cmd, cwd=str(PROPAINTER_DIR))

    # ProPainter 输出文件名形如 <folder_name>_inpaint_out.mp4
    out_videos = list(output_dir.glob("*.mp4"))
    if not out_videos:
        # 可能是 .avi 等
        out_videos = list(output_dir.glob("*.mp4")) + list(output_dir.glob("*.avi"))
    if not out_videos:
        raise RuntimeError(f"ProPainter 输出未找到视频: {output_dir}")
    out_video = out_videos[0]
    print(f"[INFO] ProPainter 输出: {out_video}")
    return out_video


# ---------- Step 4: 合成 + 原音频 ----------
def assemble_with_audio(propainter_video: Path, original_video: Path, output: Path):
    """把 ProPainter 视频与原视频的音频合并"""
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(propainter_video),
        "-i", str(original_video),
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-c:a", "aac", "-b:a", "192k",
        "-map", "0:v:0", "-map", "1:a:0",  # 视频取自 ProPainter，音频取自原视频
        "-shortest",
        str(output),
    ]
    run(cmd, capture_output=True)
    print(f"[INFO] 最终输出: {output}")
    return output


# ---------- 主流程 ----------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=str, help="输入视频路径")
    parser.add_argument("--output", type=str, help="输出视频路径")
    parser.add_argument("--work-dir", type=str, default="work_remove_logo", help="工作目录")
    parser.add_argument("--seed-frame", type=int, default=150, help="种子帧索引（0-based）")
    parser.add_argument("--seed-x", type=int, default=285, help="种子点 x")
    parser.add_argument("--seed-y", type=int, default=662, help="种子点 y")
    parser.add_argument("--mask-dilation", type=int, default=4, help="ProPainter mask 膨胀像素")
    parser.add_argument("--step", choices=["all", "extract", "segment", "inpaint", "assemble"],
                        default="all", help="只跑某一步")
    parser.add_argument("--no-vis", action="store_true", help="不生成可视化")
    args = parser.parse_args()

    work = Path(args.work_dir).resolve()
    frames_dir = work / "frames"
    masks_dir = work / "masks"
    vis_dir = None if args.no_vis else work / "vis"
    output_dir = work / "inpaint_out"
    output = Path(args.output).resolve() if args.output else work / "out.mp4"

    # 状态文件记录 fps
    fps_file = work / "fps.txt"
    fps = 30.0

    # Step 1
    if args.step in ("all", "extract"):
        if not args.video:
            print("[ERROR] --video 必填（extract 步骤）")
            sys.exit(1)
        fps, n = extract_frames(Path(args.video), frames_dir)
        fps_file.write_text(str(fps))
        if args.step == "extract":
            return

    if fps_file.exists():
        fps = float(fps_file.read_text().strip())

    # Step 2
    if args.step in ("all", "segment"):
        if not frames_dir.exists() or not list(frames_dir.glob("*.jpg")):
            print(f"[ERROR] 帧目录为空: {frames_dir}，先跑 extract")
            sys.exit(1)
        segment_logo_with_sam2(
            frames_dir, masks_dir,
            seed_frame=args.seed_frame,
            seed_x=args.seed_x, seed_y=args.seed_y,
            vis_dir=vis_dir,
        )
        if args.step == "segment":
            return

    # Step 3
    propainter_video = None
    if args.step in ("all", "inpaint"):
        if not masks_dir.exists() or not list(masks_dir.glob("*.png")):
            print(f"[ERROR] mask 目录为空: {masks_dir}，先跑 segment")
            sys.exit(1)
        propainter_video = inpaint_with_propainter(
            frames_dir, masks_dir, output_dir, fps,
            mask_dilation=args.mask_dilation,
        )
        if args.step == "inpaint":
            return

    # Step 4
    if args.step in ("all", "assemble"):
        if propainter_video is None:
            # 找已存在的 ProPainter 输出
            videos = list(output_dir.glob("*.mp4"))
            if not videos:
                print(f"[ERROR] 找不到 ProPainter 输出: {output_dir}，先跑 inpaint")
                sys.exit(1)
            propainter_video = videos[0]
        if not args.video:
            print("[ERROR] --video 必填（assemble 步骤需要原视频取音频）")
            sys.exit(1)
        assemble_with_audio(propainter_video, Path(args.video), output)
        if args.step == "assemble":
            return

    print(f"\n✓ 完成。输出: {output}")


if __name__ == "__main__":
    main()
