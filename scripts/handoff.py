#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一条命令完成"客户 → 工作流"的交接（2026-10-11 用户定：agent 只是过渡衔接，不许冗长分析）。

    python scripts/handoff.py <档> [任务名] [--session <会话id>]

它做四件事，全是机械动作，**一次模型都不调**：
  1) 登记意图（复用 scripts/intent.py ⇒ 判据与目录规则只有一份）；
  2) 把任务素材根里【散着】的文件归位到无歧义的槽（拿不准的一律留空，让客户在表上自己选）；
  3) 打印右栏那张表的地址与"还缺哪几格、为什么没替他填"；
  4) 打印 next：客户交完表会被叫醒 ⇒ 跑 `runner/run.py --from-form`。

🔴 为什么要有这条命令（现网形状 2026-10-11 session-71b9a32d）：客户 20:18 点了单，
   表到 21:02 才出现在他眼前，中间 44 分钟里 agent 在抽帧、看图、写 BRIEF/ANALYSIS、
   再手搓一个 driver 去回填（还把 key 猜成了另一条线的名字）。那 44 分钟没有一分钟在为他干活 ——
   因为"弹表"被写成了【分析之后】的动作，而分析根本没有预算这一说。
   这条命令把顺序倒过来：**先让表出现、先归位手里的料**，需要理解的部分留给交表之后的那一步。
"""
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
INTENT = os.path.join(HERE, "intent.py")
FORM_API = "http://127.0.0.1:8100/api/skills/form?session="


def _safe_print(s):
    """GBK 控制台上中文/箭头会让 print 当场崩 ⇒ 按输出流能力丢字，绝不 reconfigure 编码
    （那会造出"零输出 + exit 0"的静默假绿）。"""
    try:
        print(s)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(s.encode(enc, "replace").decode(enc, "replace"))


def family_of(path, media_fields):
    """这个文件能进哪些槽（判据 = 服务端下发的 accept 名单，不在脚本里抄第二份类型表）。"""
    ext = os.path.splitext(str(path))[1].lstrip(".").lower()
    return [f["key"] for f in media_fields if ext in (f.get("accept") or [])]


def slot_plan(media_fields, loose):
    """纯函数（闸直接跑值表）：哪些素材可以【无歧义】归位，哪些必须留给人选。

    规则三条，宁可留空也不猜：
      ① 一个文件只对一个槽 ⇒ 候选；对两个槽（图片既像产品图又像脸图）⇒ 留空；
      ② 单值槽（max=1）收到 2 个以上候选 ⇒ 整槽留空（挑哪一张是人的决定）；
      ③ 多值槽按 max 截断，超出的留空并点名。
    返回 (可回填的 files=[{field,src}], 留空说明={槽或族: 原因})。
    """
    by_key = {f["key"]: f for f in media_fields}
    cand = {}                                   # field -> [path]
    skipped = {}
    for p in loose:
        hits = family_of(p, media_fields)
        if len(hits) == 1:
            cand.setdefault(hits[0], []).append(p)
        elif not hits:
            skipped[os.path.basename(str(p))] = "扩展名不对上任何素材槽 ⇒ 不动它"
        else:
            skipped[os.path.basename(str(p))] = "同时可能是 %s 的素材 ⇒ 这一格留给客户选" % "/".join(hits)
    files = []
    for field, paths in sorted(cand.items()):
        mx = by_key[field].get("max")
        try:
            mx = int(mx) if mx not in (None, "", " ") else 0
        except (TypeError, ValueError):
            mx = 0
        if mx == 1 and len(paths) > 1:
            skipped[field] = ("单值槽却躺着 %d 个候选（%s）⇒ 整格留空，让客户挑那一个"
                              % (len(paths), "、".join(os.path.basename(p) for p in paths[:4])))
            continue
        if mx and len(paths) > mx:
            skipped[field] = "候选 %d 个超过上限 %d ⇒ 只挂前 %d 个，其余留给客户换" % (
                len(paths), mx, mx)
            paths = paths[:mx]
        files.extend({"field": field, "src": p} for p in sorted(paths))
    return files, skipped


def read_gate(sid):
    with urllib.request.urlopen(FORM_API + urllib.parse.quote(sid), timeout=8) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def loose_in(root):
    """素材根【顶层】散着的文件（已经进了某个槽目录的不算 —— 那是归位好的）。"""
    out = []
    if not root or not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if os.path.isfile(p):
            out.append(p)
    return out


def main(argv):
    args = [x for x in argv]
    sid = os.environ.get("DSH_SESSION_ID") or ""
    if "--session" in args:
        i = args.index("--session")
        if i + 1 >= len(args):
            _safe_print("--session 后面要跟会话 id")
            return 2
        sid = args[i + 1]
        del args[i:i + 2]
    if not args or args[0] in ("-h", "--help"):
        _safe_print(__doc__)
        return 0
    if not sid:
        _safe_print("handoff 要会话 id：$env:DSH_SESSION_ID 或 --session <会话id>"
                    "（没有会话就找不到那张表，绝不猜一个填进去）")
        return 2
    skill, task = args[0].strip(), (args[1].strip() if len(args) > 1 else "")

    # 1) 登记意图 —— 直接复用 intent.py（目录规则、TTL、排他全在那一份里，这里不抄第二份）
    r = subprocess.run([sys.executable, INTENT, skill] + ([task] if task else []) + ["--session", sid],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=WS)
    _safe_print("[1/4 登记意图] rc=%s %s" % (r.returncode, (r.stdout or r.stderr or "").strip()[:200]))
    if r.returncode:
        return r.returncode

    # 2) 读判定口：这条会话该填哪张表、素材根在哪、现在缺什么
    try:
        gate = read_gate(sid)
    except Exception as e:  # noqa: BLE001
        _safe_print("[2/4 读表] 判定口(8100)没回话：%s: %s ⇒ 表还没法归位（先确认 8100 在跑）"
                    % (type(e).__name__, str(e)[:120]))
        return 3
    if not gate.get("show"):
        _safe_print("[2/4 读表] 这条会话不该有表（reason=%s）⇒ 那条线没配字段表，handoff 不归它管"
                    % gate.get("reason"))
        return 4
    root = str(gate.get("upload_root") or "")
    media = [f for f in (gate.get("fields") or []) if f.get("accept")]
    files, skipped = slot_plan(media, loose_in(root))
    _safe_print("[2/4 读表] 槽 %d 个（素材槽 %d）｜素材根 %s"
                % (len(gate.get("fields") or []), len(media), root or "（没回）"))

    # 3) 归位 = 走同一条回填口（intent.py prefill）⇒ 复制、sha8 命名、服务端判 key 全在那一份里
    if files:
        body = {"files": files,
                "notes": {f["field"]: "登记时任务素材根里就是这个文件（无歧义：扩展名只对上这一格）"
                          for f in files}}
        p2 = subprocess.run([sys.executable, INTENT, "prefill", "--set", json.dumps(body, ensure_ascii=False),
                             "--session", sid],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=WS)
        _safe_print("[3/4 归位素材] rc=%s %s" % (p2.returncode, (p2.stdout or p2.stderr or "").strip()[:400]))
    else:
        _safe_print("[3/4 归位素材] 没有可无歧义归位的文件 ⇒ 一条都不写（拿不准就不回填，见下）")

    try:
        g2 = read_gate(sid)
    except Exception:  # noqa: BLE001
        g2 = gate
    _safe_print("[4/4 现在这张表] 已弹给会话 %s｜prefilled=%s submitted=%s"
                % (sid[-8:], g2.get("prefilled"), g2.get("submitted")))
    if skipped:
        _safe_print("  🔴 故意留空（不是漏了）：")
        for k, v in sorted(skipped.items()):
            _safe_print("     %s — %s" % (k, v))
    unf = [str(k) for k in (g2.get("unfilled_required") or [])]
    if unf:
        _safe_print("  还空的必填格：%s ⇒ 需要理解的那几格（产品名/需求描述）由 agent 按客户原话补，"
                    "其余请客户在表上直接选" % "、".join(unf))
    _safe_print("\n下一步（顺序是死的）：")
    _safe_print("  A) 需要看懂素材才能定的格，跑 python scripts/intent.py prefill --set @模板.json 补上"
                "（先 python scripts/intent.py fields 拿 key，别猜）；")
    _safe_print("  B) 等客户在右栏点【提交这张表】—— 会被叫醒，不要催、不要替他交；")
    _safe_print("  C) 交完跑：python .dsh/skills/%s/runner/run.py --from-form   # 值全部从表里读，不抄参数"
                % skill)
    _safe_print("     先 --stage compile（0 分）对完分镜与动作标签，再去掉它走报价那一刀。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
