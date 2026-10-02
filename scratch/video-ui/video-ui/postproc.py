"""成片后处理：ffmpeg 拼接 + delogo 去 omni 右下角水印。"""

import json
import shutil
import subprocess
from pathlib import Path

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

# Windows 下子进程默认会闪一个控制台窗口。右栏工作台每 2.5 秒轮一次状态，闪起来没完 ⇒ 一律禁掉。
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(cmd, timeout=1800):
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, creationflags=_NO_WINDOW)
    return proc


def capabilities():
    caps = {"ffmpeg": False, "ffprobe": False, "concat": False, "delogo": False, "libx264": False}
    try:
        caps["ffmpeg"] = bool(_run([FFMPEG, "-version"], timeout=30).stdout.strip())
    except Exception:
        return caps
    try:
        caps["ffprobe"] = bool(_run([FFPROBE, "-version"], timeout=30).stdout.strip())
    except Exception:
        pass
    filters = _run([FFMPEG, "-hide_banner", "-filters"], timeout=60).stdout or ""
    caps["concat"] = bool(search_filter(filters, "concat"))
    caps["delogo"] = bool(search_filter(filters, "delogo"))
    encs = _run([FFMPEG, "-hide_banner", "-encoders"], timeout=60).stdout or ""
    caps["libx264"] = "libx264" in encs
    return caps


def search_filter(text, name):
    for line in text.splitlines():
        cols = line.split()
        if len(cols) >= 2 and cols[1] == name:
            return line
    return ""


def probe(path):
    proc = _run([FFPROBE, "-v", "error", "-print_format", "json",
                 "-show_format", "-show_streams", str(path)], timeout=120)
    try:
        data = json.loads(proc.stdout or "{}")
    except Exception:
        return {}
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})
    return {
        "duration": float(data.get("format", {}).get("duration") or 0),
        "width": v.get("width"), "height": v.get("height"),
        "vcodec": v.get("codec_name"), "acodec": a.get("codec_name"),
        "fps": eval_rate(v.get("r_frame_rate")),
    }


def eval_rate(rate):
    try:
        num, den = str(rate).split("/")
        return round(float(num) / float(den), 3)
    except Exception:
        return None


def concat_copy(segments, out_path):
    """首选：concat demuxer + -c copy，秒级无损，要求各段编码一致。"""
    lst = Path(out_path).parent / "concat_list.txt"
    lst.write_text("".join("file '%s'\n" % str(Path(s).as_posix()).replace("'", "'\\''")
                           for s in segments), encoding="utf-8")
    proc = _run([FFMPEG, "-y", "-hide_banner", "-f", "concat", "-safe", "0", "-i", str(lst),
                 "-c", "copy", str(out_path)], timeout=600)
    if proc.returncode != 0:
        raise RuntimeError("concat copy 失败: %s" % (proc.stderr or "")[-500:])
    return out_path


def concat_reencode(segments, out_path, size="1080x1920", fps=30):
    """回退：统一分辨率/帧率再重编码。"""
    w, h = size.split("x")
    inputs = []
    for s in segments:
        inputs += ["-i", str(s)]
    parts = "".join("[%d:v]scale=%s:force_original_aspect_ratio=decrease,pad=%s:(ow-iw)/2:(oh-ih)/2,"
                    "setsar=1,fps=%d[v%d];[%d:a]aresample=async=1[a%d];" % (i, w + ":" + h, w + ":" + h,
                                                                            fps, i, i, i)
                    for i in range(len(segments)))
    chain = "".join("[v%d][a%d]" % (i, i) for i in range(len(segments)))
    filt = "%s%sconcat=n=%d:v=1:a=1[outv][outa]" % (parts, chain, len(segments))
    cmd = [FFMPEG, "-y", "-hide_banner"] + inputs + ["-filter_complex", filt,
           "-map", "[outv]", "-map", "[outa]", "-c:v", "libx264", "-crf", "18",
           "-preset", "medium", "-c:a", "aac", "-b:a", "192k", str(out_path)]
    proc = _run(cmd, timeout=3600)
    if proc.returncode != 0:
        raise RuntimeError("concat re-encode 失败: %s" % (proc.stderr or "")[-800:])
    return out_path


def concat(segments, out_path, size="1080x1920", fps=30):
    """两级回退。返回 (路径, 用的策略)。"""
    try:
        return concat_copy(segments, out_path), "ffmpeg concat -c copy"
    except Exception as exc:
        first = exc
    try:
        return concat_reencode(segments, out_path, size, fps), "ffmpeg concat filter re-encode"
    except Exception as exc:
        raise RuntimeError("拼接失败。copy: %s | re-encode: %s" % (str(first)[-200:], str(exc)[-400:]))


def delogo(src, dst, box, copy_audio=True):
    """去掉 omni 右下角银色四角星水印。box = {x,y,w,h}。"""
    b = Path(src).parent / ("noaudio_" + Path(src).stem + ".mp4")
    vf = "delogo=x=%d:y=%d:w=%d:h=%d" % (int(box["x"]), int(box["y"]), int(box["w"]), int(box["h"]))
    cmd = [FFMPEG, "-y", "-hide_banner", "-i", str(src), "-vf", vf,
           "-c:v", "libx264", "-crf", "18", "-preset", "medium"]
    cmd += ["-c:a", "copy"] if copy_audio else ["-an"]
    cmd += [str(dst)]
    proc = _run(cmd, timeout=3600)
    if proc.returncode != 0:
        cmd2 = [FFMPEG, "-y", "-hide_banner", "-i", str(src), "-vf", vf,
                "-c:v", "libx264", "-crf", "18", "-an", str(b)]
        proc2 = _run(cmd2, timeout=3600)
        if proc2.returncode != 0:
            raise RuntimeError("delogo 失败: %s" % (proc.stderr or proc2.stderr or "")[-600:])
        return merge_audio(b, src, dst), "ffmpeg delogo (v) + 原音频合并"
    return dst, "ffmpeg delogo"


def merge_audio(video_only, audio_src, dst):
    cmd = [FFMPEG, "-y", "-hide_banner", "-i", str(video_only), "-i", str(audio_src),
           "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
           "-shortest", str(dst)]
    proc = _run(cmd, timeout=1800)
    if proc.returncode != 0:
        raise RuntimeError("合并音频失败: %s" % (proc.stderr or "")[-500:])
    try:
        Path(video_only).unlink()
    except OSError:
        pass
    return dst


def dry_segment(dest, seconds=10, size="1080x1920"):
    """VIDEO_UI_DRYRUN=1 时的占位段：本地 ffmpeg 生成，不调 omni。"""
    w, h = size.split("x")
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
           "testsrc=size=%dx%d:rate=30:duration=%d" % (int(w), int(h), int(seconds)),
           "-f", "lavfi", "-i", "sine=frequency=320:duration=%d" % seconds,
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(dest)]
    proc = _run(cmd, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError("占位段生成失败: %s" % (proc.stderr or "")[-300:])
    return dest


def _lines(text):
    return [l.strip() for l in (text or "").splitlines() if l.strip()]


def blackdetect(path, min_dur=0.05):
    """返回黑场区间列表（秒）。空列表 = 无黑帧/黑转过场。"""
    proc = _run([FFMPEG, "-hide_banner", "-i", str(path), "-vf",
                 "blackdetect=d=%s:pix_th=0.10" % min_dur, "-an", "-f", "null", "-"], timeout=1800)
    out = []
    for line in _lines(proc.stderr):
        if "blackdetect" not in line or "black_start" not in line:
            continue
        try:
            seg = line.split("black_start:")[1].split("black_end:")[1].split("black_ratio:")[0]
            out.append([float(seg.split()[0]), float(seg.split()[1])])
        except (IndexError, ValueError):
            continue
    return out


def freezedetect(path, min_dur=2.5):
    proc = _run([FFMPEG, "-hide_banner", "-i", str(path), "-vf",
                 "freezedetect=n=-60dB:d=%s" % min_dur, "-map", "[v]", "-an", "-f", "null", "-"], timeout=1800)
    hits = [l for l in _lines(proc.stderr) if "freeze_start" in l]
    return len(hits)


def mean_volume(path):
    proc = _run([FFMPEG, "-hide_banner", "-i", str(path), "-af", "volumedetect", "-vn",
                 "-f", "null", "-"], timeout=1800)
    for line in _lines(proc.stderr):
        if "mean_volume" in line:
            try:
                return float(line.split("mean_volume:")[1].strip().replace("dB", ""))
            except ValueError:
                return None
    return None


def grab_frame(path, at, out, box=None):
    """抽一帧（可选裁剪到水印框区域）。返回 PIL Image。"""
    from PIL import Image
    import io
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-ss", str(at), "-i", str(path),
           "-frames:v", "1"]
    if box:
        cmd += ["-vf", "crop=%d:%d:%d:%d" % (int(box["w"]), int(box["h"]), int(box["x"]), int(box["y"]))]
    cmd += ["-f", "image2pipe", "-vcodec", "png", "-"]
    proc = subprocess.run(cmd, capture_output=True, timeout=300, creationflags=_NO_WINDOW)
    if proc.returncode != 0 or not proc.stdout:
        return None
    return Image.open(io.BytesIO(proc.stdout)).convert("L")


def region_diff(path_a, path_b, box, at=3.0):
    """两张视频在同一位置的像素平均差。用于确认 delogo 真的动了那一块。"""
    import numpy as np
    a = grab_frame(path_a, at, None, box)
    b = grab_frame(path_b, at, None, box)
    if a is None or b is None:
        return None
    h = min(a.size[1], b.size[1])
    w = min(a.size[0], b.size[0])
    aa = np.asarray(a.crop((0, 0, w, h)), dtype="int16")
    bb = np.asarray(b.crop((0, 0, w, h)), dtype="int16")
    return float(abs(aa - bb).mean())


def copy(src, dst):
    shutil.copyfile(str(src), str(dst))
    return dst
