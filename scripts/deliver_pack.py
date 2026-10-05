#!/usr/bin/env python
"""交付三件套（deliver pack）：路径 + 8100 直链 + 抽帧贴图，一条命令出齐。

为什么要有这个脚本（2026-10-05）：交付铁律要求"产物落盘并且能出示"，但"能出示"一直靠 agent 记得
—— 视频在 DSH 聊天里【永远不能内联】，所以必须同时给 本地路径 + 可点直链 + 抽帧贴图，
少一样客户就等于没拿到（历史上被点名过"视频你给我看啊"）。能自动做的就别留给自觉。

退出码：任何一项校验没过就非 0 ⇒ agent 不能拿一个坏包当交付收工。
用法：python scripts/deliver_pack.py <视频文件> [--task 名] [--frames 3] [--json] [--no-net]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

GW = os.environ.get("WEEKSIR_ADMIN_BASE", "http://127.0.0.1:8100")
ASCII_RE = re.compile(r"^[\x20-\x7e]+$")


def probe(url, timeout=6.0):
    """直链可达性：GET + Range（8100 静态口从 2026-10-03 起支持 206，视频才拖得动）。"""
    try:
        req = urllib.request.Request(url, headers={"Range": "bytes=0-1023"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers)
    except Exception as e:
        return 0, {"error": str(e)[:160]}


def duration_of(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", path],
                             capture_output=True, text=True, timeout=30)
        return float((out.stdout or "0").strip() or 0)
    except Exception:
        return 0.0


def extract_frames(video, task_dir, n=3):
    """抽 n 帧到 outputs/preview-<stem>-k.jpg。返回 [(路径, rel, 字节数)]，只收 >2KB 的（黑帧/错误页也小）。"""
    dur = duration_of(video) or 0.0
    stem = os.path.splitext(os.path.basename(video))[0]
    out = []
    for i in range(max(1, n)):
        pos = (dur * (i + 1) / (n + 1)) if dur > 0 else i
        jpg = os.path.join(os.path.dirname(video), "preview-%s-%d.jpg" % (stem, i + 1))
        cmd = ["ffmpeg", "-v", "error", "-y"]
        if dur > 0:
            cmd += ["-ss", "%.2f" % pos]
        cmd += ["-i", video, "-frames:v", "1", jpg]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except Exception:
            continue
        if os.path.isfile(jpg) and os.path.getsize(jpg) > 2048:
            out.append((jpg, "outputs/" + os.path.basename(jpg), os.path.getsize(jpg)))
    return out


def build(video, task=None, frames=3, net=True):
    video = os.path.abspath(video)
    rep = {"video": video, "ok": False, "checks": {}, "links": [], "markdown": ""}
    if not os.path.isfile(video):
        rep["checks"]["存在"] = False
        rep["error"] = "文件不存在：%s" % video
        return rep
    size = os.path.getsize(video)
    rep["bytes"] = size
    rep["checks"]["非空(>10KB)"] = size > 10240
    name = os.path.basename(video)
    rep["checks"]["文件名是 ASCII"] = bool(ASCII_RE.match(name))

    root = os.path.dirname(os.path.dirname(video))          # productions/<task>
    base = task or os.path.basename(root)
    rep["task"] = base
    rep["checks"]["在 productions/<task>/outputs 下"] = os.path.basename(root).lower() != "outputs" \
        and os.path.basename(os.path.dirname(video)).lower() == "outputs"

    rel = urllib.parse.quote("outputs/" + name, safe="/")
    qtask = urllib.parse.quote(base)
    link = "%s/api/film/file?task=%s&rel=%s" % (GW, qtask, rel)
    rep["link"] = link
    if net:
        st, hdrs = probe(link)
        rep["checks"]["直链可达(200/206)"] = st in (200, 206)
        rep["http_status"] = st
        rep["accept_ranges"] = hdrs.get("Accept-Ranges") or hdrs.get("accept-ranges")
        rep["checks"]["静态口支持 Range(能拖进度条)"] = (st == 206) or (rep["accept_ranges"] == "bytes")
    else:
        rep["checks"]["直链可达(200/206)"] = True   # --no-net：跳过，不算失败

    shots = extract_frames(video, root, frames)
    rep["frames"] = [{"path": p, "bytes": b} for p, _r, b in shots]
    rep["checks"]["抽到帧(≥1 张 >2KB)"] = len(shots) >= 1
    for _p, r, _b in shots:
        rep["links"].append("%s/api/film/file?task=%s&rel=%s" % (GW, qtask, urllib.parse.quote(r, safe="/")))

    rep["ok"] = all(bool(v) for v in rep["checks"].values())
    md = ["**成片**：`%s`（%d KB）" % (video, size // 1024),
          "**直链**（浏览器可播、能拖）：%s" % link]
    if rep["links"]:
        md.append("**画面**（抽帧，证明里面是这个东西）：")
        md += ["![预览%d](%s)" % (i + 1, u) for i, u in enumerate(rep["links"])]
    rep["markdown"] = "\n".join(md)
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--task", default=None)
    ap.add_argument("--frames", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-net", action="store_true")
    a = ap.parse_args()
    rep = build(a.video, task=a.task, frames=a.frames, net=not a.no_net)
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print(rep["markdown"])
        print("\n校验：" + " | ".join("%s=%s" % (k, "OK" if v else "FAIL") for k, v in rep["checks"].items()))
        print("结论：" + ("可以交付" if rep["ok"] else "不算交付（上面有 FAIL，别拿它收工）"))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
