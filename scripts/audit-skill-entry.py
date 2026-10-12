# -*- coding: utf-8 -*-
"""点单审计器：客户点了某档【带引擎的】skill，流程到底走没走 —— 把"静默"变成"看得见"。

为什么要有它（2026-10-11 现网两条会话同一形状）：客户发 `/super-clone 替换人物` + 两个附件，
agent 没登记意图、没建任务目录、素材没归位、表没回填 ⇒ 右栏一张表都不出（判定口回 `no-intent`），
它转头在对话里问客户 6 个问题（其中 5 项表上本来就有格子）。
🔴 关键在于：**盘上零条红**。现有判据（合同回执、回填覆盖率、报价出处）全都要求"先有意图/目录"才生效，
第一步没走 ⇒ 没有任何一个闸被调用。可选的步骤等于不存在的步骤，而"没人发现它不存在"就是不稳定本身。

信号取哪个：转写里 `<skill_content name="x">` = 内核真的把该 skill 装进了上下文（覆盖手打斜杠与
从技能面板点两种），`user/message` 以 `/<lane>` 开头 = 客户自己打的，作旁证。只审 LANES（带引擎的档），
纯提示词类不登记、不挂台，本来就不该出现在这张表里。

判据是纯函数 `entry_verdict()`（不读盘 ⇒ 能被整块抠出来跑真值表），I/O 只在 `scan()` 里。
用法：  python scripts/audit-skill-entry.py [--hours 24] [--all]   # 只读，退出码 0=没有静默单
        python scripts/audit-skill-entry.py --selftest            # 真产物 + 变异对照，不碰盘
"""
from __future__ import annotations

import ast
import glob
import io
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(HERE)
INTENT_PY = os.path.join(HERE, "intent.py")
SESSIONS = os.path.expandvars(r"%APPDATA%\dsh-desktop\harness\sessions")
MAGIC = b"\x28\xb5\x2f\xfd"
WINDOW = 1800.0          # 点单后 30 分钟内一件事实都没发生，才判"静默"
# 🔴 登记意图是【点单之后第一条命令】：建目录 + 写一行 JSON，不调模型、不读素材 ⇒ 纯机械。
#    超过 3 分钟只可能是它在动手之前先写了一篇分析（2026-10-11 现网：20:18 点单，
#    20:36–20:53 抽帧/看图/写 BRIEF/写 ANALYSIS，表 21:02 才出现，客户全程对着空右栏）。
#    而"表什么时候出现"现在等价于"意图什么时候登记"—— 登记 + 配了字段表就弹（2026-10-11 改判）。
INTENT_SLOW = 180.0
CELLS = ("意图", "素材", "回填", "交表", "回执")


def lanes() -> tuple:
    """唯一源 = scripts/intent.py 的 LANES。用 ast 取字面量，绝不 import 它
    （intent.py 一 import 就 reconfigure stdout，会把本脚本的 GBK 崩溃掩盖成"零输出+exit 0"）。"""
    tree = ast.parse(io.open(INTENT_PY, encoding="utf-8", errors="replace").read())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in ("LANES", "TASK_DIR_LANES"):
                    try:
                        return tuple(ast.literal_eval(node.value))
                    except Exception:
                        pass
    raise SystemExit("读不到 intent.py 里的 LANES ⇒ 审计器不猜名单，直接停")


def workspace_slug() -> str:
    """C:\\dsh → --C-dsh--（DSH 按工作区路径分会话目录）。找不到就出声，不跨工作区乱审。"""
    p = WORKSPACE.replace(":", "").replace("/", os.sep)
    guess = "--" + re.sub(r"[\\/]+", "-", p).strip("-") + "--"
    if os.path.isdir(os.path.join(SESSIONS, guess)):
        return guess
    hits = [d for d in glob.glob(os.path.join(SESSIONS, "*"))
            if os.path.isdir(d) and re.search(r"-%s-$" % re.escape(os.path.basename(WORKSPACE)), d)]
    if len(hits) == 1:
        return os.path.basename(hits[0])
    raise SystemExit("会话目录里对不上工作区 %s（猜的是 %s，命中 %d 个）⇒ 不猜，先修这条映射"
                     % (WORKSPACE, guess, len(hits)))


def decode(path: str) -> str:
    """session.v3.jsonl.zstd 是多帧 zstd 拼接：按帧边界切开逐帧解（一次只解一帧会拿到半份历史）。"""
    raw = open(path, "rb").read()
    if MAGIC not in raw:
        return raw.decode("utf-8", "replace")
    js = ("const fs=require('fs'),z=require('zlib');"
          "const b=fs.readFileSync(process.argv[1]);const M=Buffer.from([0x28,0xb5,0x2f,0xfd]);"
          "let i=0,o=[];for(;;){const a=b.indexOf(M,i);if(a<0)break;let n=b.indexOf(M,a+4);"
          "const e=n<0?b.length:n;try{o.push(z.zstdDecompressSync(b.subarray(a,e)));}catch(x){}"
          "if(n<0)break;i=n;}process.stdout.write(Buffer.concat(o));")
    r = subprocess.run(["node", "-e", js, path], capture_output=True, timeout=120)
    if r.returncode != 0 or not r.stdout:
        raise SystemExit("解不开 %s（node 不可用？）⇒ 审计器宁可报错，也不许把'读不到'当成'没有单'"
                         % path)
    return r.stdout.decode("utf-8", "replace")


def clicks_of(text: str, want: tuple) -> dict:
    """纯解析：从一条转写里挑出【被真正装载过的引擎档】→ 最早一次的时间戳与原文前缀。"""
    hit = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] != "{":
            continue
        try:
            j = json.loads(line)
        except Exception:
            continue
        if j.get("type") not in ("user/message", "system/message"):
            continue
        for c in (j.get("data") or {}).get("content") or []:
            t = str(c.get("text") or "")
            for lane in want:
                got = None
                m = re.search(r'<skill_content name="%s"' % re.escape(lane), t)
                if m:
                    got = "装载"
                elif re.match(r"^/%s(\s|$)" % re.escape(lane), t.strip()):
                    got = "斜杠"
                if got and (lane not in hit or float(j.get("time") or 0) < hit[lane]["ts"]):
                    hit[lane] = {"ts": float(j.get("time") or 0) / 1000.0, "how": got,
                                 "text": t.strip()[:24]}
    return hit


def form_lanes() -> set:
    """哪些档【配了表单】：唯一源 = 各 SKILL.md 的 `form:` 段 + frontmatter 的 `intent:` 键。
    🔴 必须分档判 —— 2026-10-11 盘上只有 super-clone / product-video(one-shot-video) / prompt-reverse 三档有表，
       omni、hypit、film、motion-transfer 都没有表单。拿"有没有回填/交表"去判它们 = 永远红，
       那叫把合格对象判红的假红，比不判更坏。"""
    out = set()
    for p in glob.glob(os.path.join(WORKSPACE, ".dsh", "skills", "*", "SKILL.md")):
        t = io.open(p, encoding="utf-8", errors="replace").read()
        parts = t.split("---")
        if len(parts) < 3 or "form: |" not in t:
            continue
        m = re.search(r"^intent:\s*(\S+)", parts[1], re.M)
        if m:
            out.add(m.group(1).strip().strip("\"'"))
    return out


def intent_records() -> dict:
    """两份登记簿合并读，同一会话取 ts 更新的那个 —— 与 8100 判定口同一条规则。
    只读工作区那份会漏掉"agent 直接写了网关簿"的会话 ⇒ 把走通了流程的单判成静默单。"""
    out = {}
    for path in (os.path.join(WORKSPACE, ".dsh", "intent.json"),
                 r"D:\video-skill\.gateway_intent.json"):
        try:
            j = json.load(io.open(path, encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if not isinstance(j, dict):
            continue
        for sid, rec in j.items():
            if isinstance(rec, dict) and float(rec.get("ts") or 0) >= float((out.get(sid) or {}).get("ts") or 0):
                out[sid] = rec
    return out


def collect_facts(sid: str, clicked_ts: float, lane: str, ints: dict) -> dict:
    """读四份事实（全是只读）：意图簿 / 任务目录 inputs 最新文件 / 回填 / 已交载荷 / 合同回执。"""
    def _ts(path, key):
        try:
            j = json.load(io.open(path, encoding="utf-8", errors="replace"))
        except Exception:
            return 0.0
        rec = j.get(sid) or {}
        return float(rec.get(key) or 0.0)

    intent_ts = float((ints.get(sid) or {}).get("ts") or 0.0)
    prefill_ts = _ts(os.path.join(WORKSPACE, ".dsh", "form_prefill.json"), "ts")
    payload_ts = _ts(os.path.join(WORKSPACE, ".dsh", "form_payload.json"), "ts")
    task_dir = str((ints.get(sid) or {}).get("task_dir") or "")
    newest = lambda pat: max([os.path.getmtime(f) for f in glob.glob(pat)] or [0.0])
    material_ts = (newest(os.path.join(task_dir, "inputs", "**", "*")) if task_dir else 0.0)
    receipt_ts = (newest(os.path.join(task_dir, "**", ".skill_receipt.json")) if task_dir else 0.0)
    return {"intent": intent_ts, "material": material_ts, "prefill": prefill_ts,
            "payload": payload_ts, "receipt": receipt_ts, "task_dir": task_dir}


def slow_steps(clicked_ts: float, facts: dict, limit: float = INTENT_SLOW) -> list:
    """纯函数：点单 → 登记意图 的耗时超预算 = 它在动手之前先写了一篇分析。
    没登记的情况不在这里报（那是 SILENT/HALF 的活），免得同一件事两个判据各红一次。"""
    it = (facts or {}).get("intent")
    if not (isinstance(it, float) and it and it >= clicked_ts):
        return []
    d = it - clicked_ts
    return [] if d <= limit else ["点单→登记意图 %d 秒（预算 %d 秒）" % (int(d), int(limit))]


def entry_verdict(clicked_ts: float, facts: dict, now: float, has_form: bool = True,
                  window: float = WINDOW) -> dict:
    v = _entry_verdict_core(clicked_ts, facts, now, has_form, window)
    v["slow"] = slow_steps(clicked_ts, facts)
    return v


def _entry_verdict_core(clicked_ts: float, facts: dict, now: float, has_form: bool = True,
                        window: float = WINDOW) -> dict:
    """纯函数：点单之后，该出现的几件事实出现了没有。
    SILENT = 点单之后【一件都没发生】（今天那几条就是这个形状）
    HALF   = 登记了意图，但既没回填也没交表 —— 流程停在半路，右栏永远不出表【仅有表单的档才这么判】
    OK     = 有表单的档：点单之后有回填或交表；无表单的档：点单之后登记了意图就算进了流程
    RUNNING= 还在窗口内，不判（免得把"agent 正在分析"当静默）"""
    if now - clicked_ts < window:
        return {"state": "RUNNING", "missing": [], "na": [], "why": "点单还没过 %d 分钟，不判"
                 % int(window / 60)}
    after = {k: v for k, v in facts.items() if isinstance(v, float) and v and v >= clicked_ts}
    na = [] if has_form else ["回填", "交表"]        # 没表单的档，这两格根本不该被要求
    if not after:
        return {"state": "SILENT", "na": na,
                "missing": [c for c in CELLS if c not in na],
                "why": "点了 skill 之后盘上零动作：没登记意图、素材没进 inputs、表没回填也没交表 "
                       "⇒ 右栏一张表都不会出，任何闸都没被调用（这就是静默单）"}
    if not has_form:
        cells = [c for c in ("回执",) if not after.get("receipt")]
        return {"state": "OK" if after.get("intent") else "SILENT", "missing": cells, "na": na,
                "why": ("无表单档：登记了意图就算进了流程（回执格单列显示）" if after.get("intent")
                        else "只有素材/回填动作，却没有意图登记 ⇒ 这单没有主人，任务目录与右栏都挂不上")}
    if not (after.get("prefill") or after.get("payload")):
        cells = [c for c in CELLS if c not in na and not after.get(
            {"意图": "intent", "素材": "material", "回填": "prefill", "交表": "payload",
             "回执": "receipt"}[c])]
        return {"state": "HALF", "missing": cells, "na": na,
                "why": "只登记了意图，没收料：没回填（表不弹）、也没交表 ⇒ 流程停在入口第一步之后"}
    cells = [c for c in CELLS if c not in na and not after.get(
        {"意图": "intent", "素材": "material", "回填": "prefill", "交表": "payload",
         "回执": "receipt"}[c])]
    return {"state": "OK", "missing": cells, "na": na,
            "why": "点单后有回填/交表（回执缺失不算静默，那是合同层的事，格子单列显示）"}


def scan(hours: float = 24.0, show_all: bool = False) -> list:
    want = lanes()
    forms = form_lanes()
    ints = intent_records()
    root = os.path.join(SESSIONS, workspace_slug())
    now = time.time()
    rows = []
    for d in sorted(glob.glob(os.path.join(root, "session-*"))):
        f = os.path.join(d, "session.v3.jsonl.zstd")
        if not os.path.isfile(f) or (not show_all and now - os.path.getmtime(f) > hours * 3600):
            continue
        sid = os.path.basename(d)
        try:
            clicks = clicks_of(decode(f), want)
        except SystemExit as e:
            print("跳过 %s：%s" % (sid, e))
            continue
        for lane, info in sorted(clicks.items(), key=lambda kv: kv[1]["ts"]):
            facts = collect_facts(sid, info["ts"], lane, ints)
            v = entry_verdict(info["ts"], facts, now, has_form=lane in forms)
            rows.append({"session": sid, "lane": lane, "clicked": info["ts"], "how": info["how"],
                         "text": info["text"], "verdict": v, "facts": facts})
    return rows


def _fmt(ts):
    return time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "—"


def main() -> int:
    args = sys.argv[1:]
    if "--selftest" in args:
        return selftest()
    hours = 24.0
    if "--hours" in args:
        hours = float(args[args.index("--hours") + 1])
    rows = scan(hours=hours, show_all="--all" in args)
    enc = getattr(sys.stdout, "encoding", None) or "ascii"

    def safe(s):
        try:
            s.encode(enc); return s
        except (UnicodeEncodeError, LookupError):
            return s.encode(enc, "replace").decode(enc, "replace")

    print(safe("点单审计：工作区 %s · 最近 %.0f 小时 · 命中 %d 条" % (WORKSPACE, hours, len(rows))))
    bad = 0
    for r in rows:
        v = r["verdict"]
        na = v.get("na") or []
        marks = "".join("—" if c in na else ("✓" if c not in v["missing"] else "✗") for c in CELLS)
        print(safe("  %-8s %s [%s] 点单 %s(%s) 「%s」 %s"
                   % (v["state"], r["lane"], marks, _fmt(r["clicked"]), r["how"], r["text"],
                      "" if v["state"] == "OK" else "→ " + v["why"]))[:230])
        if v.get("slow"):
            print(safe("        ⏱ 超预算：%s ⇒ 客户这段时间对着空右栏（登记是第一条命令，纯机械）"
                       % "、".join(v["slow"])))
        if v["state"] in ("SILENT", "HALF") or v.get("slow"):
            bad += 1
    print(safe("=== 静默/半路/超预算单：%d 条 ===" % bad))
    return 1 if bad else 0


def selftest() -> int:
    """真产物对照 + 变异对照：判据改空转必须失配，否则它就是摆设。"""
    out = []

    def ck(name, got, want):
        ok = got == want
        out.append(ok)
        print("%-4s %s  got=%r want=%r" % ("PASS" if ok else "FAIL", name, got, want))

    T = 1791700000.0                       # 假想的"现在"
    P = T - 3600.0                         # 点单在 1 小时前（已过 30 分钟窗口，才进判据）
    # 14:40 现网那条：点单之后盘上零动作（意图/回填/交表的时间戳全是当天更早或 0）
    silent = {"intent": P - 90000, "material": 0.0, "prefill": P - 52000, "payload": P - 60000,
              "receipt": 0.0}
    ck("S1 真产物形状·点单后一件没发生 ⇒ SILENT",
       entry_verdict(P, silent, T)["state"], "SILENT")
    ck("S2 反向对照·同一份事实但点单在更早 ⇒ OK（证明判的是先后，不是有没有）",
       entry_verdict(P - 90000, silent, T)["state"], "OK")
    ck("S3 只登记意图、没回填没交表 ⇒ HALF（08:23 那单的另一半形状）",
       entry_verdict(P, dict(silent, intent=P + 100), T)["state"], "HALF")
    ck("S4 登记 + 回填 ⇒ OK，且回执那一格仍单独显示缺",
       [entry_verdict(P, dict(silent, intent=P + 100, prefill=P + 200), T)[k]
        for k in ("state", "missing")], ["OK", ["素材", "交表", "回执"]])
    ck("S5 窗口内不判（免得把正在分析当成静默）",
       entry_verdict(T - 60, silent, T)["state"], "RUNNING")
    stub = lambda clicked, facts, now: {"state": "OK", "missing": [], "why": ""}
    ck("S6 变异·同一条真产物喂给恒 OK 的桩 ⇒ 与真判据结果不同（证明 S1 咬的是真判据不是摆设）",
       [stub(P, silent, T)["state"], entry_verdict(P, silent, T)["state"]], ["OK", "SILENT"])
    ck("S7 lanes 唯一源读得到（读空=整张表永远零条=假绿）",
       "super-clone" in lanes(), True)
    # 无表单的档（omni / hypit / film / motion-transfer）：要求"回填/交表"就是把合格对象判红
    ck("S8 无表单档·点单后登记了意图 ⇒ OK，且回填/交表算不适用（不是缺）",
       [entry_verdict(P, dict(silent, intent=P + 100), T, has_form=False)[k]
        for k in ("state", "na", "missing")], ["OK", ["回填", "交表"], ["回执"]])
    ck("S9 无表单档·点单后零动作 ⇒ 仍判 SILENT，但缺的格不含回填/交表",
       [entry_verdict(P, silent, T, has_form=False)[k] for k in ("state", "missing")],
       ["SILENT", ["意图", "素材", "回执"]])
    fl = form_lanes()
    ck("S10 配了表单的档必须都在 LANES 里（有表却没登记口=表永远不弹的静默洞）",
       bool(fl) and fl.issubset(set(lanes())), True)
    merged = intent_records()
    only_outer = set((json.load(io.open(r"D:\video-skill\.gateway_intent.json",
                                        encoding="utf-8", errors="replace")) or {})) - set(
        (json.load(io.open(os.path.join(WORKSPACE, ".dsh", "intent.json"),
                           encoding="utf-8", errors="replace")) or {}))
    ck("S11 两份登记簿合并读（只读工作区那份会把走网关簿的单误判成静默）",
       only_outer.issubset(set(merged)), True)
    ck("S12 超预算判据：点单→登记 10 分钟 ⇒ 点名（登记是纯机械的第一条命令，慢=它在写分析）",
       [bool(slow_steps(P, {"intent": P + 600.0})), slow_steps(P, {"intent": P + 600.0})[0].split("（")[0]],
       [True, "点单→登记意图 600 秒"])
    ck("S13 预算内不响（30 秒）；没登记也不在这里响（那是 SILENT/HALF 的活，一条事实一个判据）",
       [slow_steps(P, {"intent": P + 30.0}), slow_steps(P, {}), slow_steps(P, {"intent": P - 9.0}),
        slow_steps(P, {"intent": 0}), slow_steps(P, {"prefill": P + 900.0})],
       [[], [], [], [], []])
    ck("S14 包装层把 slow 挂到【所有状态】上（RUNNING 也要看得见，否则窗口内的慢单永远不出声）",
       [bool(entry_verdict(P, {"intent": P + 600.0, "prefill": P + 610.0}, T, has_form=True)["slow"]),
        bool(entry_verdict(P, {"intent": P + 600.0}, T - 60.0, has_form=True)["slow"]),
        entry_verdict(P, {"intent": P + 5.0, "prefill": P + 6.0}, T, has_form=True)["slow"]],
       [True, True, []])
    print("\n=== 点单审计自测：%d/%d 通过 ===" % (sum(out), len(out)))
    return 0 if all(out) else 1


if __name__ == "__main__":
    sys.exit(main())
