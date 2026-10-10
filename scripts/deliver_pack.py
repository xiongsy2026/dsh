#!/usr/bin/env python
"""交付三件套（deliver pack）：路径 + 8100 直链 + 抽帧贴图，一条命令出齐。

为什么要有这个脚本（2026-10-05）：交付铁律要求"产物落盘并且能出示"，但"能出示"一直靠 agent 记得
—— 视频在 DSH 聊天里【永远不能内联】，所以必须同时给 本地路径 + 可点直链 + 抽帧贴图，
少一样客户就等于没拿到（历史上被点名过"视频你给我看啊"）。能自动做的就别留给自觉。

退出码：任何一项校验没过就非 0 ⇒ agent 不能拿一个坏包当交付收工。
用法：python scripts/deliver_pack.py <视频文件> [--task 名] [--frames 3] [--json] [--no-net]
"""
import argparse
import hashlib
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
                             capture_output=True, text=True, timeout=30,
                             stdin=subprocess.DEVNULL)      # 🔴 不接 stdin：管道环境下它会阻塞在读键盘上
        return float((out.stdout or "0").strip() or 0)
    except Exception:
        return 0.0


def presented_copy(video):
    """查这个文件有没有被 DSH `present` 过 —— 只有 present 过，对话里才有那张卡
    （打开 / 下载视频 / 默认播放器）。客户体验的关键一步，所以它是**硬校验**，不是提示。

    机制（读内核代码得到，不是猜）：`@deepseek-ai/dsh-attachment-local` 把 present/附件的文件
    原样复制到 `%APPDATA%\\dsh-desktop\\harness\\attachments\\v1\\files\\<原名>`。
    🔴 必须比内容哈希：文件名会撞（聊天里上传的附件也叫 video(16).mp4），只看名字会把"别人的文件"
       当成"我这条交付已经 present"。
    返回 (状态, 说明)：状态 ∈ {"presented", "missing", "name-collided", "unchecked"}。
    """
    if os.environ.get("DELIVER_PACK_NO_PRESENT"):
        return "unchecked", "已按 DELIVER_PACK_NO_PRESENT 跳过（只给非 DSH 环境自测用）"
    appdata = os.environ.get("APPDATA") or ""
    root = os.environ.get("DSH_ATTACHMENTS_DIR") or os.path.join(
        appdata, "dsh-desktop", "harness", "attachments", "v1", "files")
    if not root or not os.path.isdir(root):
        return "missing", "找不到 DSH 附件存储（%s）⇒ 无法确认 present 过" % root
    name = os.path.basename(video)
    want = _sha256(video)
    twin = os.path.join(root, name)
    if os.path.isfile(twin):
        if _sha256(twin) == want:
            return "presented", twin
        return "name-collided", "%s 同名但内容不是这个文件（多半是聊天附件）" % twin
    return "missing", "attachments/v1/files 里没有 %s ⇒ 这条没 present，对话里不会有预览卡" % name


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def extract_frames(video, rel_dir, n=3):
    """抽 n 帧到视频同目录的 preview-<stem>-k.jpg。返回 [(路径, rel, 字节数)]，只收 >2KB 的（黑帧/错误页也小）。

    🔴 rel 用传进来的 rel_dir（相对 productions/<task>），不要再写死 "outputs/"：
       段目录 productions/<task>/prime-3seg/outputs/ 下面写死 outputs/ 会拼出 404 链 ⇒ 直链和贴图一起点不开。
    """
    dur = duration_of(video) or 0.0
    stem = os.path.splitext(os.path.basename(video))[0]
    out = []
    for i in range(max(1, n)):
        pos = (dur * (i + 1) / (n + 1)) if dur > 0 else i
        jpg = os.path.join(os.path.dirname(video), "preview-%s-%d.jpg" % (stem, i + 1))
        cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y"]   # -nostdin + 下面 stdin=DEVNULL，双保险
        if dur > 0:
            cmd += ["-ss", "%.2f" % pos]
        cmd += ["-i", video, "-frames:v", "1", jpg]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                         stdin=subprocess.DEVNULL)
        except Exception:
            continue
        if os.path.isfile(jpg) and os.path.getsize(jpg) > 2048:
            out.append((jpg, "%s/%s" % (rel_dir, os.path.basename(jpg)), os.path.getsize(jpg)))
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

    # 🔴 按路径里的 "productions" 那一段切，不要"往上两层"猜：段目录
    #    productions/<task>/prime-3seg/outputs/x.mp4 用猜法会把 task 落到 prime-3seg、rel 丢掉中间层 ⇒ 直链 404。
    parts = os.path.normpath(video).split(os.sep)
    low = [p.lower() for p in parts]
    under_prod = "productions" in low          # 不在 productions/<任务>/ 下 ⇒ 不拿它去扫盘找回执
    if under_prod:
        _i = low.index("productions")
        root = os.sep.join(parts[:_i + 2])
        base = task or (parts[_i + 1] if _i + 1 < len(parts) else "")
        rel_dir = "/".join(parts[_i + 2:-1])                 # task 之后、文件名之前的全部中间层
    else:
        root = os.path.dirname(os.path.dirname(video))
        base = task or os.path.basename(root)
        rel_dir = "outputs"
    rep["task"] = base
    rep["rel_dir"] = rel_dir
    rep["checks"]["在 productions/<task>/…/outputs 下"] = os.path.basename(root).lower() != "outputs" \
        and os.path.basename(os.path.dirname(video)).lower() == "outputs" and rel_dir.endswith("outputs")

    rel = urllib.parse.quote("%s/%s" % (rel_dir, name), safe="/")
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

    shots = extract_frames(video, rel_dir, frames)
    rep["frames"] = [{"path": p, "bytes": b} for p, _r, b in shots]
    rep["checks"]["抽到帧(≥1 张 >2KB)"] = len(shots) >= 1
    for _p, r, _b in shots:
        rep["links"].append("%s/api/film/file?task=%s&rel=%s" % (GW, qtask, urllib.parse.quote(r, safe="/")))

    # 🔴 present 是硬校验（2026-10-09 用户指令"必须强制预览，客户体验感好"）：
    #    没 present ⇒ 对话里只有裸链接，没有那张「打开 / 下载视频 / 默认播放器」的卡 ⇒ 这条不算交付完。
    #    顺序因此是固定的：出片 → present → 跑本脚本 → 原样贴 markdown。
    pstat, pmsg = presented_copy(video)
    rep["presented"] = {"status": pstat, "detail": pmsg}
    rep["checks"]["已 present(预览卡可用)"] = pstat in ("presented", "unchecked")
    # ⚠️ 抽帧图【不加】present 硬校验（2026-10-10 我加了又撤）：读装上的这版内核确认 markdown 图是能渲染的 —
    #    `dsh-client-ui-primitives/lib/index.js:7359 renderImage` → `:7104 imageSource` → `:7078 remoteImageUrl`
    #    放过 http/https 绝对 URL ⇒ `![x](http://127.0.0.1:8100/…)` 出 `<img>`。
    #    "客户只看见一个链接"的病因在【写法】（裸 URL / 反引号代码串不是图）或【加载失败】
    #    （`MarkdownImage` onError 会回落成 alt 文字），不在渲染器不支持 ⇒ 拿"必须 present 每张帧图"
    #    去治它 = 凭空多加一道收工闸门。直链照旧给（它同时是加载失败时的备份）。
    rep["ok"] = all(bool(v) for v in rep["checks"].values())
    md = ["**成片**：`%s`（%d KB）" % (video, size // 1024),
          "**直链**（浏览器可播、能拖）：%s" % link]
    if rep["links"]:
        md.append("**画面**（抽帧，证明里面是这个东西）：")
        md += ["![预览%d](%s)" % (i + 1, u) for i, u in enumerate(rep["links"])]
    if pstat not in ("presented", "unchecked"):
        md.append("🔴 **没 present ⇒ 这条不算交付完**（%s）。先对这个 mp4 调一次 present，再重跑本脚本："
                  "客户要的是那张【打开 / 下载视频 / 默认播放器】的卡，不是一条裸链接。" % pmsg)
    # 🔴 skill 执行合同回执：交付消息里必须现形（缺步/跳过原因一起印出来）。
    #    这里只"照"不"拦" —— 拦是在花钱那一刀（skill_contract.verify_or_raise）。
    #    没有回执的产物（非 skill 出的文件）留空，不判失败。
    _rc, _rec = "", {}                     # 🔴 先给空壳：上面 try 里任何一步炸了，下面也不能 NameError
    try:
        sys.path.insert(0, r"D:\video-skill")
        from backend import skill_contract as _skc

        # 🔴 只有真在 productions/<任务>/ 下才核对：否则 root 会算成盘根（C:\），
        #    那是"扫整块盘找 .skill_receipt.json"= 交付脚本看着像卡死（2026-10-10 实测）。
        #    🔴 取回执的根先【视频自己那一格】、再退回任务根：latest_receipt 按"最新"挑，
        #       交 seg1 的 mp4 却贴 seg2 的执行表/提示词 = 张冠李戴（2026-10-10 实测踩到）。
        _here = os.path.dirname(video)
        _rc = (_skc.summarize(_here) or _skc.summarize(root)) if under_prod else ""
        _rec = (_skc.latest_receipt(_here) or _skc.latest_receipt(root)) if (under_prod and _rc) else {}
        if not under_prod:
            rep["checks"]["成片在 productions/<任务>/ 下"] = False
            md0 = ["🔴 **这条不在 `productions/<任务>/…` 下**（文件在 `%s`）⇒ 不做合同回执核对，"
                   "下载按钮也不会出现（服务端只发 productions/ 根内的文件）。"
                   "按交付口径挪到 `productions/<任务>/outputs/` 再重跑本脚本。" % os.path.dirname(video)]
            rep.setdefault("notes", []).extend(md0)
    except Exception as _e:                                   # noqa: BLE001
        _rc = "**流程回执**：读不到（%s: %s）⇒ 这条没被合同覆盖，别当成「合同通过」" % (
            type(_e).__name__, str(_e)[:80])
    if _rc:
        md.append(_rc)
        rep["receipt"] = _rc
    # 🔴 提示词进交付（2026-10-10 用户口径："跑完流程…相关的提示词也没有展示"）：
    #    交付里必须看得见【哪一段、哪个模型跑的、提示词全文在哪】。原来这四样只在盘上，
    #    而且单段路径连 prompt.txt 都没写 —— 不是"没展示"，是根本没有。
    #    `_rec` 只在上面那一次取（先视频自己那一格、再退任务根）；这里原来又写了一遍
    #    `latest_receipt(root)` = 第二个写者，会把 scoped 的那份覆盖掉 ⇒ 交 seg1 贴 seg2 的提示词（闸 20 抓到）。
    _pd = str(_rec.get("task_dir") or "")
    if _pd and os.path.isdir(_pd):
        _pf = os.path.join(_pd, "prompt.txt")
        _mf = os.path.join(_pd, "prompt_meta.json")
        _cell = {"model": "没记", "ledger": "没记"}
        if os.path.isfile(_mf):
            try:
                with open(_mf, encoding="utf-8", errors="replace") as _fh:
                    _cell = json.load(_fh) or {}
            except (OSError, ValueError):
                _cell = {}
        _led = "网关账本内" if str(_cell.get("ledger")) == "on" else (
            "账本外·直连厂商" if _cell.get("ledger") else "没记")
        _model = ("%s@%s" % (_cell.get("model"), _cell.get("provider"))) if _cell.get("model") else "没记"
        _head = ""
        if os.path.isfile(_pf):
            with open(_pf, encoding="utf-8", errors="replace") as _fh:
                _head = " ".join(_fh.read().split())[:90]
        _relp = "/".join(os.path.normpath(_pf).split(os.sep)[low.index("productions") + 2:])
        md.append("**这一段喂给引擎的提示词**（%s · %s · %d 字 · sha8 `%s`）：\n"
                  "开头：`%s…`\n全文：[%s](%s)" % (
                      _model, _led, os.path.getsize(_pf) if os.path.isfile(_pf) else 0,
                      _skc.sha8(_pf) if os.path.isfile(_pf) else "?", _head,
                      os.path.basename(_pf), "%s/api/film/file?task=%s&rel=%s" % (
                          GW, qtask, urllib.parse.quote(_relp, safe="/"))))
        rep["prompt"] = {"path": _pf, "model": _model, "ledger": _led,
                         "link": "%s/api/film/file?task=%s&rel=%s" % (
                             GW, qtask, urllib.parse.quote(_relp, safe="/"))}
        if not os.path.isfile(_pf):
            rep["checks"]["带提示词全文"] = False
            rep.setdefault("notes", []).append(
                "🔴 回执在但没有 prompt.txt ⇒ 这条是【旧运行形状】出的（单段路径以前不落提示词），"
                "重跑一次才看得见提示词。")
        else:
            rep["checks"]["带提示词全文"] = True
    md.extend(rep.get("notes") or [])
    rep["markdown"] = "\n".join(md)
    return rep


def emit(text):
    """按 UTF-8 字节写 stdout。

    🔴 不能用 print：Windows 下 stdout 接管道时走【控制台代码页（GBK）】，
    而调用方（闸、agent 读工具输出）一律按 UTF-8 解 ⇒ 中文键名与 markdown 全成乱码，
    三件套等于没交付（2026-10-09 闸 #3/#7/#8b 三条红就是这个，不是脚本逻辑错）。
    """
    data = (text + chr(10)).encode("utf-8", "replace")
    buf = getattr(sys.stdout, "buffer", None)
    if buf is not None:
        buf.write(data)
        buf.flush()
    else:
        sys.stdout.write(text + chr(10))


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
        emit(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        emit(rep["markdown"])
        emit(chr(10) + "校验：" + " | ".join("%s=%s" % (k, "OK" if v else "FAIL") for k, v in rep["checks"].items()))
        emit("结论：" + ("可以交付" if rep["ok"] else "不算交付（上面有 FAIL，别拿它收工）"))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
