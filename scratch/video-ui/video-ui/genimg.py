"""关键帧生成：gflow-cli（Google Flow nano-pro）为主，zexapi nano_banana_pro-2K 为备。"""

import ctypes
import json
import os
import re
import subprocess
import time
from pathlib import Path

from PIL import Image, ImageDraw

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


def _ascii_path(path):
    """H:\\AI数据\\ 这种中文目录传给 exe 容易被编码问题咬，先转 8.3 短路径。"""
    try:
        buf = ctypes.create_unicode_buffer(260)
        n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 260)
        if 0 < n < 260:
            return buf.value
    except Exception:
        pass
    return str(path)


def _extract_json(text):
    start = text.find("{")
    if start < 0:
        return None
    for end in range(len(text), start, -1):
        chunk = text[start:end]
        if not chunk.rstrip().endswith("}"):
            continue
        try:
            return json.loads(chunk)
        except Exception:
            continue
    try:
        return json.loads(text[start:] + "}")
    except Exception:
        return None


def _json_paths(obj, found=None):
    if found is None:
        found = []
    if isinstance(obj, str):
        if obj.lower().endswith(IMAGE_EXTS) and Path(obj).exists():
            found.append(obj)
        return found
    if isinstance(obj, dict):
        for val in obj.values():
            _json_paths(val, found)
    elif isinstance(obj, list):
        for val in obj:
            _json_paths(val, found)
    return found


def gflow_generate(cfg, prompt, refs, out_dir, aspect="9:16", log=None):
    """返回生成图片的本地路径。refs 为空则走 t2i。"""
    exe = cfg["gflow_exe"]
    if not Path(exe).exists():
        raise RuntimeError("找不到 gflow-cli: %s" % exe)
    project = (cfg.get("gflow_project") or "").strip()
    if not project:
        raise RuntimeError("未配置 Flow project id（设置里填，不传 --project 会 401 AuthExpired）")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_ascii = _ascii_path(out_dir)
    started = time.time() - 2

    def build(ref_list):
        cmd = [exe, "image", "i2i" if ref_list else "t2i", prompt]
        for ref in ref_list:
            cmd += ["--ref", _ascii_path(ref)]
        cmd += ["--model", cfg.get("image_model", "nano-pro"), "--aspect", aspect,
                "--project", project, "--out", out_ascii, "--json"]
        return cmd

    env = dict(os.environ)
    env["GFLOW_CLI_UPDATE_CHECK"] = "0"
    env.setdefault("PYTHONIOENCODING", "utf-8")

    ref_list = [str(r) for r in refs]
    attempt = 0
    while True:
        attempt += 1
        cmd = build(ref_list)
        if log:
            log("gflow %s (%d 张参考图)" % ("i2i" if ref_list else "t2i", len(ref_list)))
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=900, env=env)
        except subprocess.TimeoutExpired:
            raise RuntimeError("gflow 900s 超时")
        stdout = (proc.stdout or "") + (proc.stderr or "")
        newest = _newest(out_dir, started)
        if proc.returncode == 0 and newest:
            return newest
        if len(ref_list) > 1:
            if log:
                log("gflow 多参考图失败（rc=%s），降级为单张上一帧重试：%s" % (proc.returncode, stdout[-200:]))
            ref_list = ref_list[-1:]
            continue
        raise RuntimeError("gflow 生成失败 rc=%s: %s" % (proc.returncode, stdout[-500:] or "无输出"))


def _newest(out_dir, since):
    cand = []
    for p in Path(out_dir).glob("*"):
        if not p.is_file() or p.suffix.lower() not in IMAGE_EXTS:
            continue
        mt = p.stat().st_mtime
        if mt >= since and (time.time() - mt) < 15 * 60:
            cand.append((mt, p))
    if not cand:
        return None
    return max(cand)[1]


def zex_generate(cfg, prompt, refs, dest, on_update=None):
    """备选通道：zexapi nano_banana_pro-2K，支持多参考图。"""
    import zexp
    images = [zexp.data_uri(r, max_edge=768) for r in refs]
    task_id, _ = zexp.submit_image(prompt, images=images or None,
                                   model=cfg.get("zexapi_image_model", "nano_banana_pro-2K"))
    url, _ = zexp.poll(task_id, interval=8, timeout=600,
                       on_update=(lambda s, p: on_update("poll %s %s" % (s, p))) if on_update else None)
    zexp.download(url, dest)
    return dest, url


def normalize_frame(src, run, idx):
    """落 frames/frame_NN.png + 640px 缩样 frames/thumb/frame_NN.jpg。src 可以是路径或 PIL 图。"""
    rel = "frames/frame_%02d.png" % idx
    thumb_rel = "frames/thumb/frame_%02d.jpg" % idx
    dst = run.root / rel
    img = src if isinstance(src, Image.Image) else Image.open(str(src))
    img = img.convert("RGB")
    img.save(str(dst), format="PNG", optimize=True)
    t = img.copy()
    t.thumbnail((640, 640), Image.LANCZOS)
    t.save(str(run.root / thumb_rel), format="JPEG", quality=88)
    return rel, thumb_rel


def make_thumb(src, dst, edge=640):
    img = Image.open(str(src)).convert("RGB")
    img.thumbnail((edge, edge), Image.LANCZOS)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    img.save(str(dst), format="JPEG", quality=88)
    return dst


def doubao_generate(prompt, refs, dest, log=None, timeout=300, model="Seedream 4.5"):
    """豆包网页版生图（免费，会开一个 Chrome 窗口，约 20-90 秒/帧）。"""
    import doubao
    return doubao.generate(prompt, refs, dest, headless=False, log=log, timeout=timeout, model=model)


def dry_frame(run, idx, label=""):
    """VIDEO_UI_DRYRUN=1 时的占位帧：本地画一张，不调任何生图通道。"""
    img = Image.new("RGB", (1080, 1920), (28 + idx * 6, 30, 42))
    dr = ImageDraw.Draw(img)
    dr.text((60, 140), "DRY RUN  frame %02d" % idx, fill=(225, 228, 236))
    dr.text((60, 180), label[:70], fill=(150, 158, 175))
    rel, thumb_rel = normalize_frame(img, run, idx)
    return rel, thumb_rel


def product_identity(product_path):
    """产品图基本信息，供 UI 展示。"""
    try:
        img = Image.open(str(product_path))
        return {"w": img.size[0], "h": img.size[1], "bytes": int(Path(product_path).stat().st_size)}
    except Exception as exc:
        return {"error": str(exc)}
