#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""本地插件的 ctx 成员读取闸（2026-10-09 事故泛化）。跑法：python scripts/check-plugin-inject.py

钉的是形状，不是"某个插件今天能起"：
  A 枚举 C:\dsh\plugins\*\lib\*.js —— 读了没 inject 的 ctx 成员就红
     （Cordis 守卫代理一读就抛 "cannot get property X without inject"：宿主侧抛 = 整棵插件树失败
      = 桌面端崩溃重启循环；客户端侧抛 = 被自己的 try 咽掉 = 功能静默死，renderer 日志不进 harness.log）
  B 正向对照：每个被判定文件都真读到至少一个已注入成员（证明扫描没吃空——上一版我的审计脚本
     先剥字符串，把 ctx.agents 整段吃掉还报"违规=无"，那就是假绿）
  C 反向对照：追加一句 ctx.bogusMember ⇒ 必须报（证明 A 会红，不是恒绿）
  D 真跑守卫 ctx：宿主半边 apply 在"读未注入成员即抛"的 ctx 下不许抛
  E 核心成员白名单要有出处：白名单里每个成员都得在官方包里普遍使用；
     而这次真出事的 timer/logger/setInterval 永远不许进白名单（必须靠 inject）
全程离线、0 分、不碰线上状态。
"""
import glob
import io
import json
import os
import re
import subprocess
import sys

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = os.path.join(WS, "_scratch_inject")
os.makedirs(SCRATCH, exist_ok=True)
TOTAL = [0]
FAILS = []

# Cordis 给【每个条目】的核心成员（不是服务 ⇒ 不需要 inject）。合法性由 E1 用官方包证据钉。
CORE_CTX_MEMBERS = {"effect"}

INJECT_RE = re.compile(r"(?:export\s+const|const|exports\.)\s*inject\s*=\s*\[([^\]]*)\]")


def _safe(s):
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        s.encode(enc)
        return s
    except (UnicodeEncodeError, LookupError):
        return s.encode(enc, "replace").decode(enc, "replace")


def check(name, got, want):
    TOTAL[0] += 1
    ok = json.dumps(got, ensure_ascii=False, default=str) == json.dumps(want, ensure_ascii=False, default=str)
    print(_safe(("PASS " if ok else "FAIL ") + name + ("" if ok else "  => %r (期望 %r)" % (got, want))))
    if not ok:
        FAILS.append(name)


def strip_comments(src):
    """只剥注释，【不剥字符串】：字符串里的 ctx.x 顶多造成一次假红（人会看见、会来问），
       而剥字符串撞上中文里的引号会把整段代码吃掉 ⇒ 造成假绿，假绿才是要命的那种。"""
    s = re.sub(r"/\*[\s\S]*?\*/", "", src)
    return re.sub(r"(?m)^(\s*)//.*$", "", s)


def audit_src(src):
    code = strip_comments(src)
    ms = list(INJECT_RE.finditer(code))
    if not ms:
        return None, None, None            # 不声明 inject 的文件（工具模块等）⇒ 不参与判定
    allowed = sorted(set(re.findall(r'"([^"]+)"', ms[-1].group(1))))
    used = sorted(set(re.findall(r"ctx\.([A-Za-z_]\w*)", code)))
    roots = {a.split(".")[0] for a in allowed} | CORE_CTX_MEMBERS
    return allowed, used, [u for u in used if u not in roots]


FILES = sorted(glob.glob(os.path.join(WS, "plugins", "*", "lib", "*.js")))
check("A0 至少扫到 3 个插件文件（枚举空了就是闸自己失焦）", len(FILES) >= 3, True)

bad_all, files_with_inject = [], []
for f in FILES:
    src = io.open(f, encoding="utf-8", errors="replace").read()
    allowed, _used, bad = audit_src(src)
    if allowed is None:
        continue
    files_with_inject.append(f)
    for b in bad or []:
        bad_all.append("%s: ctx.%s 不在 inject %s" % (
            os.path.basename(os.path.dirname(os.path.dirname(f))) + "/" + os.path.basename(f), b, allowed))
check("A1 🔴 没有任何插件读【没 inject】的 ctx 成员（宿主侧抛=崩溃循环，客户端侧抛=功能静默死）",
      bad_all, [])
check("A2 参与判定的文件数 >= 3（少于 3 个说明枚举或解析失焦了）", len(files_with_inject) >= 3, True)

seen = 0
for f in files_with_inject:
    src = io.open(f, encoding="utf-8", errors="replace").read()
    allowed, used, _bad = audit_src(src)
    if allowed and used and set(used) & ({a.split(".")[0] for a in allowed} | CORE_CTX_MEMBERS):
        seen += 1
check("B 正向对照：每个被判定文件都至少真读到 1 个已注入/核心成员（证明扫描没吃空）",
      [seen == len(files_with_inject), seen > 0], [True, True])

_mut = io.open(files_with_inject[0], encoding="utf-8", errors="replace").read()
_mp = os.path.join(SCRATCH, "mutated_plugin.js")
# 变异用"追加一个探针函数"，不靠替换某个特定函数名（不同文件形状不同，替换会静默不生效=假对照）
io.open(_mp, "w", encoding="utf-8", newline="\n").write(
    _mut + "\nfunction mut_probe(ctx) { ctx.bogusMember.tick(); }\n")
check("C 反向对照：加一句 ctx.bogusMember ⇒ 谓词必须报（A1 会红，不是恒绿）",
      "bogusMember" in (audit_src(io.open(_mp, encoding="utf-8", errors="replace").read())[2] or []), True)

HOST = os.path.join(WS, "plugins", "ops-gateway", "lib", "index.js")
NODE = r"""
import { pathToFileURL } from "node:url";
const m = await import(pathToFileURL(process.argv[2]).href);
const ctx = new Proxy({ agents: new Map() }, {
  get(t, k) { if (typeof k === "symbol" || k === "then") return t[k];
              if (!(k in t)) throw new Error('cannot get property "' + String(k) + '" without inject');
              return t[k]; }
});
try { m.apply(ctx); console.log("NO-THROW"); } catch (e) { console.log("THREW:" + e.message); }
setTimeout(() => process.exit(0), 120);
"""
_np = os.path.join(SCRATCH, "guard_ctx.mjs")
io.open(_np, "w", encoding="utf-8", newline="\n").write(NODE)
_r = subprocess.run(["node", _np, HOST], capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=60)
check("D 真跑守卫 ctx：ops-gateway 宿主 apply 不许抛（读源码看不出这条）",
      (_r.stdout or "").strip().splitlines()[-1:], ["NO-THROW"])

_app = os.path.expandvars("%LOCALAPPDATA%") + os.sep + "Programs" + os.sep + "DSH Desktop" + os.sep + \
       "resources" + os.sep + "app" + os.sep + "node_modules" + os.sep + "@deepseek-ai"
_blob = []
for _f in glob.glob(os.path.join(_app, "**", "lib", "*.js"), recursive=True)[:600]:
    try:
        _blob.append(io.open(_f, encoding="utf-8", errors="replace").read())
    except OSError:
        pass
_blob = "\n".join(_blob)
check("E1 白名单每个成员都在【官方包】里普遍使用（>=20 处 ctx.effect( 这种），否则不许进名单",
      [_blob.count("ctx." + c + "(") >= 20 for c in CORE_CTX_MEMBERS], [True])
check("E2 这次真抛过的成员（timer/logger/setInterval）永远不进白名单——它们必须靠 inject",
      any(c in CORE_CTX_MEMBERS for c in ("timer", "logger", "setInterval")), False)

# ── G 以启动日志为权威：这类雷的真判据不是"我猜谁提供服务"，而是内核自己报的那一行 ──
#    （试过静态扫"谁 provide 哪个服务"：官方前端是压缩 bundle，slots/timer/agents 这些
#      明明在用却扫不到提供方 ⇒ 拿它当判据天天误伤，反而让人开始忽略闸。已放弃那条路。）
_HLOG = os.path.expandvars("%APPDATA%") + os.sep + "dsh-desktop" + os.sep + "logs" + os.sep + "harness.log"
PEND_RE = re.compile(r"([\w./@-]+): pending \(waiting for service: ([\w.-]+)\)")


def pending_services(log_text):
    """取【最后一次启动之后】的日志，回 [[条目, 缺的服务]]。空表 = 那次启动没卡在服务上。"""
    lines = log_text.split("\n")
    starts = [i for i, l in enumerate(lines) if re.search(r"\[desktop\] starting \d{4}-", l)]
    seg = "\n".join(lines[starts[-1]:]) if starts else log_text      # 没有启动标记就整份看（宁可多报）
    return [[a, b] for a, b in PEND_RE.findall(seg)]


if os.path.isfile(_HLOG):
    _pend = pending_services(io.open(_HLOG, encoding="utf-8", errors="replace").read())
    check("G1 🔴 最近一次启动没有条目卡在 waiting for service（卡住 = 整棵树失败，桌面端起不来）",
          _pend, [])
else:
    check("G1 读不到 harness.log ⇒ 这条判据不许静默跳过（要红，别当过了）", False, True)

# G2 反向对照：把这份日志里真出现过的形状（whale-girl/configForms，10-08 的记录）喂进去 ⇒ 必须报出来
#    测的是谓词本身，不是「今天到底谁坏了」——后者只由 G1 读最近一次启动来判。
_ACC = ("[x] [desktop] starting 2026-10-09T13:45:16.995Z\n"
        "[x] [renderer] whale-girl: pending (waiting for service: configForms)\n"
        "[x] [renderer] web boot: 1 entry did not activate\n")
check("G2 反向对照：事故日志原文喂进去必须报（whale-girl / configForms）",
      pending_services(_ACC), [["whale-girl", "configForms"]])
# G3 正向对照：修好之后的新启动里不许再把旧记录算进来（防永远红）
_CLEAN = _ACC + "[y] [desktop] starting 2026-10-09T14:00:00.000Z\n[y] [desktop] Harness is ready\n"
check("G3 只看最后一次启动：修好之后旧记录不再钉红（永远红的判据 = 没有判据）",
      pending_services(_CLEAN), [])

print(_safe("\n=== 插件 ctx 注入闸：%d 条断言，失败 %d ===" % (TOTAL[0], len(FAILS))))
if FAILS:
    print(_safe("失败清单: " + " | ".join(f.split()[0] for f in FAILS)))
sys.exit(1 if FAILS else 0)
