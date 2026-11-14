"""
剪气口工具
─────────────────────────────────────────────────
对指定视频做静音检测(气口) → 切片保留有效口播段 → 无损拼接

用途：
  给视频翻译工作流(LatentSync 对口型)准备"剪好气口的纯主播视频"。
  video_translate_pipeline 要求人脸覆盖率 ≥ 70%，且气口会导致口型同步错位，
  故先用本工具把 92 号纯主播视频的气口剪掉。

用法：
  python pipeline/cut_breath.py "<视频路径>" [noise_dB] [min_duration_s]

  noise_dB        静音阈值,默认 -35dB
  min_duration_s  静音最短持续秒数才被算作气口,默认 0.5s

输出(与源同目录)：
  <basename>_no_breath.mp4           - 剪掉气口后的视频(无损 copy)
  <basename>_no_breath.silence.json  - 检测到的静音段
  <basename>_no_breath.keep.json     - 保留的有效段

实现要点：
  - 切片用 -c copy 保留原编码/分辨率/fps/音轨(LatentSync 需要原音轨)
  - -ss 在 -i 之前 → 快速定位(关键帧对齐,气口边界会保守向语音侧靠,避免切到字)
  - concat demuxer + -c copy → 无损拼接
  - 若 concat copy 失败(编码不一致),自动 fallback 重编码(libx264 + aac)
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def probe_duration(video_path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json", video_path,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return float(json.loads(p.stdout)["format"]["duration"])


def detect_silence(video_path: str, noise_db: str, min_dur: float) -> list:
    """ffmpeg silencedetect → 解析 stderr 的 silence_start/silence_end。"""
    cmd = [
        "ffmpeg", "-i", video_path,
        "-af", f"silencedetect=noise={noise_db}:d={min_dur}",
        "-f", "null", "-",
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    err = p.stderr or ""

    starts = re.findall(r"silence_start: ([\d.]+)", err)
    ends = re.findall(r"silence_end: ([\d.]+)", err)
    segs = []
    n = min(len(starts), len(ends))
    for i in range(n):
        s, e = float(starts[i]), float(ends[i])
        segs.append({"start": round(s, 2), "end": round(e, 2),
                     "duration": round(e - s, 2)})
    return segs


def compute_keep_segments(silence_segs: list, duration: float) -> list:
    """静音段之外的就是保留段。"""
    keep = []
    cursor = 0.0
    for sil in silence_segs:
        if sil["start"] > cursor + 0.1:
            keep.append({"start": round(cursor, 2), "end": round(sil["start"], 2)})
        cursor = sil["end"]
    if cursor < duration - 0.1:
        keep.append({"start": round(cursor, 2), "end": round(duration, 2)})
    return keep


def cut_and_concat(video_path: str, keep_segs: list, out_path: Path,
                  work_dir: Path) -> str:
    """精确切片(-ss 在 -i 后,重编码) → concat 重编码。

    为什么不用 -c copy:
      -ss 在 -i 之前会做关键帧对齐,切片边界向最近的最近关键帧靠,
      导致气口边界不精确(可能多保留 1-2s 静音)。
      对于"剪气口"用途,边界精度比无损更重要——
      LatentSync 对口型会重编码,这里用 crf 18(接近无损)重编码一次完全可接受。
    """
    seg_dir = work_dir / "segs"
    seg_dir.mkdir(exist_ok=True)
    seg_files = []

    for i, ks in enumerate(keep_segs):
        start, end = ks["start"], ks["end"]
        if end - start <= 0.1:
            continue
        out = seg_dir / f"seg_{i:03d}.mp4"
        # -ss 在 -i 之后 → 精确解码到目标位置(慢但边界精确)
        # -t 指定持续时长,避免 -to 在某些 ffmpeg 版本下行为不一致
        duration = end - start
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{start:.3f}",
            "-i", video_path,
            "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(out),
        ]
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if p.returncode != 0 or not out.exists():
            print(f"  [seg {i:03d}] 切片失败: {p.stderr[-200:]}")
            continue
        seg_files.append(str(out))
        print(f"  [seg {i:03d}] ✓ {start:6.2f}-{end:6.2f} ({duration:5.2f}s) → {out.name}")

    if not seg_files:
        raise RuntimeError("没有有效的切片段")

    # concat list
    concat_list = work_dir / "concat_list.txt"
    concat_list.write_text(
        "\n".join(f"file '{f}'" for f in seg_files), encoding="utf-8"
    )

    # concat 重编码(各段规格一致,理论上 copy 也行,但重编码更稳)
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(out_path),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise RuntimeError(f"拼接失败: {p.stderr[-500:]}")
    return str(out_path)


def main():
    if len(sys.argv) < 2:
        print("用法: python pipeline/cut_breath.py <视频路径> [noise_dB] [min_duration_s]")
        sys.exit(1)

    video_path = os.path.abspath(sys.argv[1])
    noise_db = sys.argv[2] if len(sys.argv) > 2 else "-35dB"
    min_dur = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5

    if not os.path.isfile(video_path):
        print(f"❌ 文件不存在: {video_path}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"剪气口: {os.path.basename(video_path)}")
    print(f"参数: noise={noise_db}  min_silence={min_dur}s")
    print(f"{'='*60}")

    # 工作目录: 与源同目录,新建子目录
    src_dir = Path(os.path.dirname(video_path))
    stem = Path(video_path).stem  # video(92)
    work_dir = src_dir / f"_cut_breath_{stem}"
    work_dir.mkdir(exist_ok=True)

    out_path = src_dir / f"{stem}_no_breath.mp4"
    silence_path = src_dir / f"{stem}_no_breath.silence.json"
    keep_path = src_dir / f"{stem}_no_breath.keep.json"

    # Step 1: 时长
    duration = probe_duration(video_path)
    print(f"\n[Step1] 视频时长: {duration:.2f}s")

    # Step 2: 静音检测
    print(f"\n[Step2] silencedetect 检测静音段(气口)...")
    silence_segs = detect_silence(video_path, noise_db, min_dur)
    total_sil = sum(s["duration"] for s in silence_segs)
    print(f"  → 检测到 {len(silence_segs)} 个静音段,累计 {total_sil:.2f}s")
    silence_path.write_text(
        json.dumps(silence_segs, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if not silence_segs:
        print("\n  ⚠ 未检测到气口,直接复制源文件作为产物")
        import shutil
        shutil.copy2(video_path, out_path)
        print(f"\n✓ 产物: {out_path}")
        return

    # Step 3: 计算保留段
    keep_segs = compute_keep_segments(silence_segs, duration)
    total_keep = sum(k["end"] - k["start"] for k in keep_segs)
    print(f"\n[Step3] 保留 {len(keep_segs)} 个有效段,累计 {total_keep:.2f}s")
    keep_path.write_text(
        json.dumps(keep_segs, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Step 4: 切片 + 拼接
    print(f"\n[Step4] 切片 + 拼接...")
    result = cut_and_concat(video_path, keep_segs, out_path, work_dir)

    # 校验
    out_dur = probe_duration(result)
    print(f"\n{'='*60}")
    print(f"✓ 完成")
    print(f"  原时长:   {duration:.2f}s")
    print(f"  剪掉气口: {total_sil:.2f}s ({len(silence_segs)} 段)")
    print(f"  产物时长: {out_dur:.2f}s")
    print(f"  产物路径: {result}")
    print(f"  静音段:   {silence_path}")
    print(f"  保留段:   {keep_path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
