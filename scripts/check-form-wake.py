#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""表单提交 → 叫醒 agent（T13）的回归闸。跑法：python scripts/check-form-wake.py

钉住的东西（少一条就会退化成"表交了没人看见"或"每 5 秒重叫一次烧 token"）：
  A 判据值表：该叫 / 会话没开 / 正在跑 / 本进程已叫过 —— 整块抠 wakeDecision 出来跑，不复制逻辑
  B 服务端队列真跑：提交进队、ack 销账、【at 不符不清】、重交重新进队、回填不进队、过期不进队
  C 环回判据只有一份（新加的路由不许再抄元组）
  D 宿主插件形状：apply 不许抛、只 followup 不碰 composer、唤醒文案里必须写"提交≠批准"
  E 变异对照：把判据改坏 ⇒ A 必须红；把 ack 改成清全部 ⇒ B 必须红
临时文件全落在 scratch，绝不碰线上 .dsh 簿。
"""
import io
import json
import os
import re
import subprocess
import sys
import time

os.environ.setdefault("HYPIT_NO_AUTOSTART", "1")
SCRATCH = r"C:\dsh\_scratch_wake"
os.makedirs(SCRATCH, exist_ok=True)
PLUGIN = r"C:\dsh\plugins\ops-gateway\lib\index.js"
TOTAL = [0]
fails = []


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
        fails.append(name)


# ── A 判据值表：把 wakeDecision 整块抠进 node 跑（不复制一份逻辑到闸里）──────────────
NODE_A = r"""
import { pathToFileURL } from "node:url";
const m = await import(pathToFileURL(process.argv[2]).href);
const f = m.wakeDecision;
const rows = [
  ["正常：会话活着且 idle", f({ session: "s", at: 1 }, { status: "idle" }, false)],
  ["会话没开着（get 回 undefined）⇒ 不叫也不销账", f({ session: "s", at: 1 }, null, false)],
  ["agent 正在跑 ⇒ 不插话，等下一轮", f({ session: "s", at: 1 }, { status: "running" }, false)],
  ["本进程已叫过同一次提交 ⇒ 跳过（防每 5 秒重叫）", f({ session: "s", at: 1 }, { status: "idle" }, true)],
  ["脏输入：item 为 null 不许抛", f(null, { status: "idle" }, false)],
  ["脏输入：item 不是对象也不能抛", f("x", { status: "idle" }, false)],
];
console.log(JSON.stringify(rows.map(r => [r[0], r[1]])));
"""

# wakeDecision 在 ESM 里；node 直接 import 我们的插件（package.json type=module）
node_src = os.path.join(SCRATCH, "a.mjs")
io.open(node_src, "w", encoding="utf-8").write(NODE_A)
p = subprocess.run(["node", node_src, PLUGIN], capture_output=True, cwd=r"C:\dsh")
txt = (p.stdout or b"").decode("utf-8", "replace")
try:
    rowsA = json.loads(txt.strip().splitlines()[-1])
except (ValueError, IndexError):
    rowsA = None
    check("A0 wakeDecision 可被 import 并跑值表（拿不到结果=整组按失败计）",
          (p.returncode, txt[-200:], (p.stderr or b"").decode("utf-8", "replace")[-200:]), (0, "", ""))

if rowsA:
    want = [
        {"act": "wake", "why": ""},
        {"act": "wait", "why": "agent-not-live"},
        {"act": "wait", "why": "agent-busy"},
        {"act": "skip", "why": "woken-in-process"},
        {"act": "skip", "why": "no-session"},
        {"act": "skip", "why": "no-session"},
    ]
    for (label, got), w in zip(rowsA, want):
        check("A " + label, got, w)

# ── A 变异对照：把判据改坏（agent 不在也照叫 / 已叫过还叫）⇒ 值表必须变 ─────────────
for tag, old, new, expect_changed in [
    ("A-mut1 会话没开着也照叫（真 bug 形状，不是删守卫）", 'if (!agent) return { act: "wait", why: "agent-not-live" };',
     'if (!agent) return { act: "wake", why: "" };', True),
    ("A-mut2 去重被删（会反复叫醒）", "if (alreadyWoken) return { act: \"skip\", why: \"woken-in-process\" };",
     "if (false) return { act: \"skip\", why: \"woken-in-process\" };", True),
]:
    src = io.open(PLUGIN, encoding="utf-8", errors="replace").read()
    if src.count(old) != 1:
        check(tag + "（锚点唯一）", src.count(old), 1)
        continue
    mp = os.path.join(SCRATCH, "mut.mjs")
    bad = os.path.join(SCRATCH, "plugin-mutated.js")
    io.open(bad, "w", encoding="utf-8").write(src.replace(old, new, 1))
    io.open(mp, "w", encoding="utf-8").write(
        'import { pathToFileURL } from "node:url";\n'
        'const m = await import(pathToFileURL(process.argv[2]).href);\n'
        'console.log(JSON.stringify([m.wakeDecision({session:"s",at:1}, null, false),'
        ' m.wakeDecision({session:"s",at:1},{status:"idle"},true)]));\n')
    r = subprocess.run(["node", mp, bad], capture_output=True, cwd=r"C:\dsh")
    out = (r.stdout or b"").decode("utf-8", "replace").strip().splitlines()
    try:
        got = json.loads(out[-1])
    except (ValueError, IndexError):
        got = "RUN-ERR:" + (r.stderr or b"").decode("utf-8", "replace")[-120:]
    check(tag + " ⇒ 判据结果确实变了（证明 A 那两条有牙）",
          isinstance(got, list) and (got[0].get("act") != "wait" or got[1].get("act") != "skip"), True)
    os.remove(bad)

# ── B 服务端队列：真跑 form_submit / wake 视图 / ack，全落临时目录 ──────────────────
sys.path.insert(0, r"D:\video-skill")
from backend import hypit_bridge, model_admin as MA  # noqa: E402

MD = """---
name: probe-skill
intent: product-video
form: |
  vid|video|爆款视频|yes|1|video_path
  note|text|补充|no| |note
---
正文
"""
SB = os.path.join(SCRATCH, "skills")
os.makedirs(os.path.join(SB, "probe-skill"), exist_ok=True)
io.open(os.path.join(SB, "probe-skill", "SKILL.md"), "w", encoding="utf-8").write(MD)
g_payload = os.path.join(SCRATCH, "form_payload.json")
MA.SKILLS_DIR = SB
MA.FORM_PAYLOAD_PATH = g_payload
MA.FORM_PREFILL_PATH = os.path.join(SCRATCH, "form_prefill.json")
MA.FORM_UPLOAD_DIR = os.path.join(SCRATCH, "uploads")
# 🔴 批准那格队列也要在【任何一次读队列之前】换到临时文件：wake_view 是两份合并的，
#    漏换 = 把现网真条子混进 B 组那些"应当恰好 N 条"的断言里（随机红，且红的是别人的活）。
MA.APPROVAL_WAKE_PATH = os.path.join(SCRATCH, "approval_wake.json")
if os.path.exists(MA.APPROVAL_WAKE_PATH):
    os.remove(MA.APPROVAL_WAKE_PATH)
INTENT = {}
MA.hypit_bridge.intent_map = lambda: dict(INTENT)

PNG = {"field": "vid", "name": "v.mp4", "type": "video/mp4", "data": "AAAA"}


def submit(sid):
    return MA.form_submit({"session": sid, "skill": "probe-skill",
                           "values": {"note": "x"}, "files": [dict(PNG)]})


def queue():
    return [(x["session"], x["skill"]) for x in MA.form_wake_queue(MA._form_payloads())]


INTENT["w-live"] = {"skill": "product-video", "ts": time.time(), "task": "t"}
INTENT["w-old"] = {"skill": "product-video", "ts": time.time(), "task": "t"}
INTENT["w-pre"] = {"skill": "product-video", "ts": time.time(), "task": "t"}
INTENT["w-two"] = {"skill": "product-video", "ts": time.time(), "task": "t"}

if os.path.exists(g_payload):
    os.remove(g_payload)
r1 = submit("w-live")
check("B1 真提交 ⇒ 进待醒队列（一条，带技能名）", queue(), [["w-live", "probe-skill"]])
check("B2 落盘的记录带 pending 的 wake；回执【不带】（插件从 GET /wake 领，两处两份迟早漂）",
      ["wake" in r1, bool(MA._form_payloads()["w-live"]["wake"]["pending"])], [False, True])
at1 = MA._form_payloads()["w-live"]["wake"]["at"]
check("B3 ack 带【错的 at】⇒ 不清队列（防抹掉更新的提交）",
      [MA.form_wake_ack({"session": "w-live", "at": at1 + 99}).get("ok"), queue()],
      [False, [["w-live", "probe-skill"]]])
check("B3b ack 不带 at ⇒ 同样不清", [MA.form_wake_ack({"session": "w-live"}).get("ok"), bool(queue())], [False, True])
check("B4 ack 带对 at ⇒ 队列空（同一次提交只叫一次）",
      [MA.form_wake_ack({"session": "w-live", "at": at1}).get("ok"), queue()], [True, []])
check("B4b 重复 ack 同一个 at ⇒ 幂等 ok（不报错、不再进队）",
      [MA.form_wake_ack({"session": "w-live", "at": at1}).get("ok"), bool(queue())], [True, False])
time.sleep(0.02)
submit("w-live")
check("B5 客户改参数重交 ⇒ 重新进队（新 at）", queue(), [["w-live", "probe-skill"]])
q_after = MA.wake_view()["pending"]
check("B5b 新 at 与旧 ack 不匹配 ⇒ 旧 ack 不能提前销掉新那格",
      [len(q_after), q_after[0]["at"] != at1], [1, True])
rpre = MA.form_prefill({"session": "w-pre", "skill": "probe-skill", "values": {"note": "AI 给的"}})
check("B6 🔴 回填（prefill）绝不进待醒队列 —— prefill ≠ submitted", queue(), [["w-live", "probe-skill"]])
submit("w-old")
d = MA._form_payloads()
d["w-old"]["ts"] = time.time() - 400000          # 越过 intent TTL
d["w-old"]["wake"]["at"] = d["w-old"]["ts"]
io.open(g_payload, "w", encoding="utf-8").write(json.dumps(d))
check("B7 过期的提交不算数（几天前的表不该今天叫人）",
      [x[0] for x in queue()], ["w-live"])
submit("w-two")
check("B8 两条待醒按时间排序，且互不串会话",
      [x[0] for x in queue()], ["w-live", "w-two"])
check("B9 会话 id 不认识的 ack ⇒ 明确拒（不静默）",
      MA.form_wake_ack({"session": "nope", "at": 1}).get("ok"), False)

# ── B 变异对照：把 ack 改成"清全部"（不带 at 也清）⇒ B3 必须失效 ────────────────────
def ack_clear_all(body):
    data = MA._form_payloads()
    for sid, rec in data.items():
        if isinstance(rec, dict) and isinstance(rec.get("wake"), dict):
            rec["wake"]["pending"] = False
    io.open(g_payload, "w", encoding="utf-8").write(json.dumps(data))
    return {"ok": True}


_real_ack = MA.form_wake_ack
MA.form_wake_ack = ack_clear_all
submit("w-live")
at2 = MA._form_payloads()["w-live"]["wake"]["at"]
bad = MA.form_wake_ack({"session": "w-live", "at": at2 + 99})     # 错 at，正确实现不该清
check("B-mut 变异：把 ack 写成清全部 ⇒ 错 at 也把队列清空了（证明 B3 判的是 at 配对，不是恒拒）",
      [bad.get("ok"), MA.form_wake_queue(MA._form_payloads())], [True, []])
MA.form_wake_ack = _real_ack

# ── E 批准核销这一路（2026-10-10 用户："我点了审批不执行，还得回一句点了条子，这个逻辑有问题"）──
# 缺的就是这一格：表单提交有 wake.pending，批准核销只到 consume_by_click 就结束 ⇒ agent 不动。
ma_src = io.open(r"D:\video-skill\backend\model_admin.py", encoding="utf-8", errors="replace").read()
plg_src = io.open(PLUGIN, encoding="utf-8", errors="replace").read()


def _grant(gid, sid="s-ap", **kw):
    g = {"id": gid, "session": sid, "capability": "video.generate", "purpose": "motion-rpa",
         "account": "env-001", "count": 1, "credits": 120, "scope": "段1 动作迁移",
         "expires": time.time() + 3600}
    g.update(kw)
    return g


def _aqueue():
    return sorted((x["grant_id"], x["session"], x["kind"])
                  for x in MA.approval_wake_queue(MA._approval_wake_load()))


check("E1 人点批准 ⇒ 挂号待醒（带 kind=approval + 条子 id）",
      [MA.approval_wake_note(_grant("ok_a1")).get("ok"), _aqueue()],
      [True, [("ok_a1", "s-ap", "approval")]])
check("E2 条子缺会话 ⇒ 不挂号（不猜该叫谁：猜错就是替别人花批准额度，2026-10-06 那颗雷）",
      [MA.approval_wake_note(_grant("ok_x", session="")).get("ok"), _aqueue()],
      [False, [("ok_a1", "s-ap", "approval")]])
check("E2b 条子缺 id ⇒ 同样不挂号（没有幂等键就不许进队列）",
      [MA.approval_wake_note(_grant("", sid="s2")).get("ok"), len(_aqueue())], [False, 1])
MA.approval_wake_note(_grant("ok_a2"))
check("E3 🔴 同一会话连着批两条 ⇒ 两格各自独立（队列按条子存，不按会话存）",
      _aqueue(), [("ok_a1", "s-ap", "approval"), ("ok_a2", "s-ap", "approval")])
at_a1 = MA._approval_wake_load()["ok_a1"]["wake"]["at"]
check("E4 ack 带错 at ⇒ 不清（防抹掉人刚补批的那一条）",
      [MA.approval_wake_ack({"grant_id": "ok_a1", "at": at_a1 + 99}).get("ok"), len(_aqueue())], [False, 2])
check("E5 ack 不带 at ⇒ 同样不清", [MA.approval_wake_ack({"grant_id": "ok_a1"}).get("ok"), len(_aqueue())],
      [False, 2])
check("E6 ack 带对 at ⇒ 只清这一格，另一格照旧待醒（会话相同也不许串）",
      [MA.approval_wake_ack({"grant_id": "ok_a1", "at": at_a1}).get("ok"), _aqueue()],
      [True, [("ok_a2", "s-ap", "approval")]])
check("E6b 重复 ack 同一个 at ⇒ 幂等 ok（不报错、不再进队）",
      [MA.approval_wake_ack({"grant_id": "ok_a1", "at": at_a1}).get("ok"), _aqueue()],
      [True, [("ok_a2", "s-ap", "approval")]])
MA.approval_wake_note(_grant("ok_old", sid="s3"))
_od = MA._approval_wake_load()
_od["ok_old"]["ts"] = _od["ok_old"]["wake"]["at"] = time.time() - 99 * 3600
io.open(MA.APPROVAL_WAKE_PATH, "w", encoding="utf-8").write(json.dumps(_od, ensure_ascii=False))
check("E7 过期的批准不算数（几天前批的条子不该今天把人叫醒）", _aqueue(), [("ok_a2", "s-ap", "approval")])
check("E8 合并视图：两份来源一条队列、每条带 kind、按 at 排",
      [(x["kind"], x.get("grant_id") or x.get("skill")) for x in MA.wake_view()["pending"]],
      [("approval", "ok_a2")])
check("E9 wake_ack 不带 kind ⇒ 明确拒（不猜是哪份队列，猜错就是抹掉别人那格）",
      [MA.wake_ack({"session": "s-ap", "at": 1}).get("ok"),
       MA.wake_ack({"kind": "approval", "grant_id": "ok_a2", "at": MA._approval_wake_load()["ok_a2"]["wake"]["at"]}).get("ok"),
       MA.wake_view()["pending"]], [False, True, []])


def approve_hook_problems(src):
    """纯函数：批准口【核销成功之后】有没有人挂号。抽出来是为了能跑坏法。"""
    i = src.find("r = gateway.consume_by_click(pid)")
    j = src.find('self._send(200 if r.get("ok") else 409', i)
    seg = src[i:j] if i >= 0 and j > i else ""
    p = []
    if not seg:
        return ["找不到核销那一段（官方/服务端结构变了 ⇒ 先读代码再改锚点，别放宽判定）"]
    if "approval_wake_note" not in seg:
        p.append("核销成功之后没人挂号（= 人点了、agent 不动，只能等一句人话）")
    if 'if r.get("ok")' not in seg:
        p.append("挂号没被【成功】判据挡住 ⇒ 被拒的点击也会叫醒，那是假事件")
    return p


check("E10 服务端：/api/spend/approve 成功后必须挂号（问题清单为空）", approve_hook_problems(ma_src), [])
for _tag, _old, _new in [
    ("E10a 变异 摘掉挂号", "                    r[\"wake\"] = approval_wake_note(r.get(\"grant\") or {})", ""),
    ("E10b 变异 去掉成功判据（拒了也叫醒）", '                if r.get("ok"):\n', '                if True:\n'),
]:
    _mut = ma_src.replace(_old, _new, 1)
    check(_tag + " ⇒ 必须红（且锚点真命中，不是空跑）",
          [_mut != ma_src, bool(approve_hook_problems(_mut))], [True, True])

# 插件侧：两种来源的文案与销账
check("E11 插件销账按 kind 分流（批准那格用 grant_id，不用 session 当键）",
      ['kind: "approval", grant_id: item.grant_id' in plg_src, 'kind: "form", session: item.session' in plg_src],
      [True, True])
check("E12 批准文案：写死【不要再等确认、不要重新报价】，也写死【只覆盖这次范围】",
      all(s in plg_src for s in ("批准已生效", "不要再等一句确认", "也不要重新报价", "只覆盖上面写明的范围")), True)
check("E13 🔴 批准文案里不许出现硬编码价格数字（分数/段数一律从条子读）",
      bool(re.search(r'上限 \d+ 分|credits\s*=\s*\d', plg_src)), False)
# E14 真跑 wakeText —— 源码断言是弱证据：把分支改成不可达，字符串还在，判据照样绿。
# 副本必须叫 .mjs：SCRATCH 下没有 type=module 的 package.json，.js 会被 node 当 CJS 解析 ⇒ import 直接炸。
NODE_E = r"""
import { pathToFileURL } from "node:url";
const m = await import(pathToFileURL(process.argv[2]).href);
console.log(JSON.stringify(m.wakeText({ kind: "approval", session: "s", grant_id: "ok_z9",
  capability: "video.generate", purpose: "motion-rpa", count: 1, credits: 120, scope: "段1", at: 1 })));
"""
io.open(os.path.join(SCRATCH, "e.mjs"), "w", encoding="utf-8").write(NODE_E)


def _text_of(plugin_src, tag):
    cp = os.path.join(SCRATCH, "plugin-%s.mjs" % tag)
    io.open(cp, "w", encoding="utf-8").write(plugin_src)
    r = subprocess.run(["node", os.path.join(SCRATCH, "e.mjs"), cp], capture_output=True, cwd=r"C:\dsh")
    try:
        return json.loads((r.stdout or b"").decode("utf-8", "replace").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return ""


_t_real = _text_of(plg_src, "real")
check("E14 真跑 wakeText(批准那条) ⇒ 消息带条子 id / 路由 / 段数 / 分数（agent 不用再回去问批了什么）",
      [s in _t_real for s in ("批准已生效", "ok_z9", "video.generate/motion-rpa", "120")], [True] * 4)
_t_mut = _text_of(plg_src.replace('  if (kind === "approval") {', '  if (kind !== "approval") {', 1), "mut")
check("E14b 变异 反转 kind 判据 ⇒ 批准那格拿到的是【表单文案】（红才算这条判据咬得住）",
      ["表单已提交" in _t_mut, "批准已生效" in _t_mut], [True, False])

# ── C/D 源码形状 ─────────────────────────────────────────────────────────────────
# ma_src / plg_src 在 E 组开头读过一次，这里复用（同一份文件读两遍 = 两遍之间被改会自相矛盾）
plg = plg_src
check("C1 环回元组【只有一份】（新路由不许再抄，抄第 N 遍必漏一处）",
      ma_src.count('("127.0.0.1", "::1", "localhost")'), 1)
check("C2 两个唤醒口都走同一个谓词",
      [ma_src.count('if self._not_loopback("/api/skills/form/wake"):'),
       ma_src.count('if self._not_loopback("/api/skills/form/wake/ack"):')], [1, 1])
apply_tail = plg.split("export function apply(ctx) {", 1)[-1]
check("D1 apply() 第一句就是 try（apply 抛出去 = 整棵插件树失败 = 桌面端崩溃重启循环，2026-10-08 桌宠实测）",
      bool(re.match(r"\s*try\s*\{", apply_tail)), True)
# 🔴 判"有没有碰 composer"之前必须先剥注释：纪律本身就写在注释里，不剥必假红
plg_code = re.sub(r"/\*[\s\S]*?\*/", "", re.sub(r"//[^\n]*", "", plg))
check("D2 只走 followup：不碰 composer / 不 create 新会话 / 不代发（只看代码行）",
      [".followup(" in plg_code, "setDraft" in plg_code, "addAttachments" in plg_code,
       "agents.create" in plg_code], [True, False, False, False])
check("D3 叫醒消息的 source 是 plugin 形状（官方同款，别自造 kind）",
      'source: { kind: "plugin", plugin: "weeksir-form-wake" }' in plg, True)
check("D4 🔴 文案里必须写『提交表单不等于批准』（唤醒≠授权花钱）",
      all(k in plg for k in ("不等于批准", "/approval")), True)
check("D5 定时器一轮只跑一个（防请求叠罗汉），且有 rev 指纹可查跑的是哪版；"
      "🔴 跳过那一支不许是静默 return（2026-10-12 改成会出声，探针跟着钉形状不钉字面）",
      ["if (running) {" in plg, "WAKE_REV" in plg, "if (running) return;" in plg], [True, True, False])
check("D6 页面/插件里不出现价格数字（表单只收输入）",
      any(x in plg for x in ("分/段", "credits=", "225")), False)


# ── D7-D10 🔴 inject 与"守卫 ctx"：这次事故的形状必须变成闸，不能靠我记得 ──────────────
#    判据抽成纯函数（同一份判据既给静态断言用，也给变异对照用），字符串先剥注释再剥裸字符串：
#    否则注释里写一句"别读 ctx.logger"就会自己把自己判红。
def ctx_violations(src):
    code = re.sub(r"/\*[\s\S]*?\*/", "", re.sub(r"//[^\n]*", "", src))
    code = re.sub(r"(['\`])(?:\\.|(?!\1)[\s\S])*?\1", '""', code)      # 字符串/模板里的 ctx.x 不算属性读
    m = re.search(r"export const inject = \[([^\]]*)\]", code)
    if not m:
        return ["没声明 inject"], []
    allowed = set(re.findall(r'"([^"]+)"', m.group(1)))
    used = sorted(set(re.findall(r"ctx\.([A-Za-z_]\w*)", code)))
    return sorted(u for u in used if u not in allowed), sorted(allowed)


viol7, allowed7 = ctx_violations(plg)
check("D7 🔴 代码里读的每个 ctx 成员都必须在 inject 名单内（读没注入的成员 = apply 抛 = 崩溃循环）",
      [viol7, allowed7, bool(allowed7)], [[], ["agents"], True])

# 真跑一次：拿守卫 ctx 代理把插件 import 进来调 apply —— Cordis 就是这么拦的，读源码看不出来
NODE_GUARD = r"""
import { pathToFileURL } from "node:url";
const m = await import(pathToFileURL(process.argv[2]).href);
const ctx = new Proxy({ agents: new Map() }, {
  get(t, k) { if (typeof k === "symbol" || k === "then") return t[k];
              if (!(k in t)) throw new Error('cannot get property "' + String(k) + '" without inject');
              return t[k]; }
});
try { m.apply(ctx); console.log("NO-THROW"); }
catch (e) { console.log("THREW:" + e.message); }
setTimeout(() => process.exit(0), 120);        // apply 起了 5 秒定时器，等它一轮再退，别把测试挂住
"""
_guard = os.path.join(SCRATCH, "ctx_guard_probe.mjs")
io.open(_guard, "w", encoding="utf-8", newline="\n").write(NODE_GUARD)
_r8 = subprocess.run(["node", _guard, PLUGIN], capture_output=True, text=True,
                     encoding="utf-8", errors="replace", timeout=60)
check("D8 🔴 真跑守卫 ctx：apply(没 inject 的成员一律抛的 ctx) 必须不抛（读源码看不出这条）",
      (_r8.stdout or "").strip().splitlines()[-1:], ["NO-THROW"])

_v9 = ctx_violations(plg.replace('export const inject = ["agents"]', "export const inject = []"))
check("D9 反向对照：把 inject 清空 ⇒ 同一条谓词必须报违规（证明 D7 真在判，不是恒绿）",
      bool(_v9[0]), True)
_mut10 = plg.replace("export function apply(ctx) {",
                     'export function apply(ctx) {\n  ctx.logger.info("mut");')
_f10 = os.path.join(SCRATCH, "mut_unguarded_apply.js")
io.open(_f10, "w", encoding="utf-8", newline="\n").write(_mut10)
_r10 = subprocess.run(["node", _guard, _f10], capture_output=True, text=True,
                      encoding="utf-8", errors="replace", timeout=60)
check("D10 反向对照：把一次裸读挪到 try 外面 ⇒ 真跑必须抓到 THREW（证明 D8 不是空跑）",
      (_r10.stdout or "").startswith("THREW:"), True)
os.remove(_guard)
os.remove(_f10)

# ── Z 部署一致（2026-10-10 现网抓到）：前面 50 多条全绿，人那边照样"点了没反应" ──────────
# 根因不在判据里，在【跑的是哪版】：approval 那半是当天 11:08 之后写进磁盘的，harness 没重启 ⇒
# 内存里还是 form-wake-20261009b，它根本不领 kind=approval 那格。服务端挂号成功、队列永不过期、
# 闸读的是源码 ⇒ 全绿 + 客户干等。所以这条必须钉【已加载版本 == 源码版本】，不看端口、不看文件存在。
_HLOG = os.path.join(os.environ.get("APPDATA", "") or "", "dsh-desktop", "logs", "harness.log")


def wake_rev_verdict(src_text, boot_window, log_exists):
    """纯判据：最后一次启动打印的插件 rev 是否就是源码里那个 WAKE_REV。
    boot_window 只给【最后一次 `[desktop] starting` 之后】的行 —— 按行号切，
    拿时间戳字符串过滤会把没有 `[desktop]` 前缀的行全算进当天（踩过）。
    log_exists=False / 找不到启动行 ⇒ skip：这台机器没装或日志刚轮转，不许变成随机假红。"""
    m = re.search(r'WAKE_REV\s*=\s*"([^"]+)"', src_text or "")
    if not m:
        return {"state": "red", "source": "", "loaded": "",
                "why": "源码里没有 WAKE_REV ⇒ 装了也查不出跑哪版（先把指纹补回来）"}
    if not log_exists:
        return {"state": "skip", "source": m.group(1), "loaded": "", "why": "读不到 harness.log（没装/没日志），不判"}
    if not boot_window:
        return {"state": "skip", "source": m.group(1), "loaded": "", "why": "日志里没有 [desktop] starting 行，不判"}
    got = [re.search(r"ops-gateway host (\S+)", l) for l in boot_window]
    got = [g.group(1) for g in got if g]
    if not got:
        return {"state": "red", "source": m.group(1), "loaded": "",
                "why": "最后一次启动没有 ops-gateway 的 rev 行 ⇒ 宿主插件没加载，或加载的是没指纹的旧版"}
    if got[-1] != m.group(1):
        return {"state": "red", "source": m.group(1), "loaded": got[-1],
                "why": "已加载 %s ≠ 源码 %s ⇒ 代码改了但 harness 没重启，唤醒对人停摆（这条红 = 去重启，不是修代码）"
                       % (got[-1], m.group(1))}
    return {"state": "green", "source": m.group(1), "loaded": got[-1], "why": ""}


_hlog_exists = os.path.isfile(_HLOG)
_hlines = (io.open(_HLOG, encoding="utf-8", errors="replace").read().splitlines()
           if _hlog_exists else [])
_boot_i = [i for i, l in enumerate(_hlines) if "[desktop] starting" in l]
_window = _hlines[_boot_i[-1]:] if _boot_i else []
_z = wake_rev_verdict(plg_src, _window, _hlog_exists)
print(_safe("    [Z 取证] harness.log 行数=%d 启动行=%d 源码rev=%s 已加载rev=%s"
            % (len(_hlines), len(_boot_i), _z.get("source"), _z.get("loaded") or "-")))
check("Z1 🔴 已加载的宿主插件版本 == 源码版本（不一致=改了码没重启，客户点批准没人领）",
      _z["state"], "green")
check("Z1b 不一致时必须把两个 rev 都报出来（别让人只看到一个'不绿'）",
      (_z["state"] != "red") or bool(_z["loaded"] and _z["why"]), True)
check("Z2 覆盖对照：这条判据真的读到了启动窗口里的 rev 行（Z1 不是因为读不到东西而绿/红）",
      (_hlog_exists and bool(_boot_i)) or not _hlog_exists, True)
_zm = wake_rev_verdict(plg_src.replace('WAKE_REV = "' + _z["source"] + '"',
                                      'WAKE_REV = "mut-' + _z["source"] + '"', 1),
                       _window, _hlog_exists)
check("Z3 反向对照：只把源码 rev 改一个词 ⇒ 必须判不一致（证明 Z1 在比字符串，不是恒绿）",
      [_zm != _z, _zm["state"]], [True, "red"])
_zn = wake_rev_verdict(plg_src.replace('const WAKE_REV', 'const NO_REV', 1), _window, _hlog_exists)
check("Z4 反向对照：摘掉源码里的 rev 指纹 ⇒ 必须红（没指纹就不许混过去）", _zn["state"], "red")
_zs = wake_rev_verdict(plg_src, [l for l in _window if "ops-gateway host" not in l], _hlog_exists)
check("Z5 反向对照：把启动窗口里的 rev 行抹掉 ⇒ 必须红（不能当成'没这回事'）", _zs["state"], "red")


def wait_note_problems(src):
    """纯判据：会话没空 / 没开着 这两种【wait】有没有在日志里出声。抽出来是为了能跑坏法。"""
    i = src.find('if (d.act !== "wake") {')
    seg = src[i:i + 700] if i >= 0 else ""
    if not seg:
        return ["找不到 wait/skip 那段分支（插件形状变了 ⇒ 先读码再改锚点）"]
    p = []
    if 'd.act === "wait"' not in seg:
        p.append("wait 没被单独判出来（和'已经叫过'混成一条，报出来也是错的）")
    if "note(" not in seg:
        p.append("wait 不出声 ⇒ 人点了批准而 agent 不动时，日志里查不到原因，只能靠猜")
    if "has(key)" not in seg:
        p.append("没按 key 去重 ⇒ 每 5 秒刷一行，日志马上没人看")
    return p


check("Z6 🔴 wait 必须出声（这条就是这次排查花 40 分钟的原因：服务端挂号成功、插件一句不报）",
      wait_note_problems(plg_src), [])
_zwm = plg_src.replace('        note("warn", "form-wake 等着没叫: kind="', '        void 0; // note removed: "', 1)
check("Z6b 反向对照：把那条 note 摘掉 ⇒ 必须红（且锚点真命中，不是空跑）",
      [_zwm != plg_src, bool(wait_note_problems(_zwm))], [True, True])
check("Z7 WAKE_REV 必须留在【boot 时一定执行到的那条 note】里（否则 Z1 拿不到已加载版本）",
      'note("info", "ops-gateway host " + WAKE_REV)' in plg_src, True)

# ── W 组：轮询自己不许静默停摆（2026-10-12 现网：人 09:28:21 点了批准，队列那条到 09:36 没人领；
#    ops-gateway 最后一行日志停在昨晚 23:53:17 的 `form-wake: fetch failed`，而 harness 是 23:45 那个进程）──
_host_path = PLUGIN
_host = io.open(_host_path, encoding="utf-8", errors="replace").read()
_fj = _host[_host.index("async function fetchJson"): _host.index("export function wakeDecision")]
_run = _host[_host.index("let running = false"): _host.index("setInterval(run")]
check("W1 fetchJson 必须带超时（挂住的一次 fetch 会让「一轮只跑一个」的守卫永久锁死整条唤醒线）",
      ["AbortSignal.timeout" in _fj, "FETCH_TIMEOUT_MS" in _fj, "signal" in _fj], [True, True, True])
check("W2 兜底看门狗：整轮超过 ROUND_WATCHDOG_MS 没回来要【强制复位 running】（fetch 之外的 await 也会挂）",
      ["setTimeout(" in _run, "clearTimeout(" in _run, "running = false" in _run], [True, True, True])
check("W3 🔴 跳过本轮必须【出声】（静默 return = 八小时没人知道它死了，客户只能来问「我点了没反应」）",
      ["if (running) return;" in _run, "上一轮还没回来" in _run], [False, True])
check("W4 心跳：每 HEARTBEAT_EVERY 轮打一条 alive（让「还在跑」成为日志里查得到的事，不靠客户报修）",
      ["HEARTBEAT_EVERY" in _run, "心跳" in _run], [True, True])
check("W5 超时/看门狗/心跳三个数都真的定义了（不是引用了不存在的常量 —— 那会在运行时抛，比停摆更糟）",
      [_host.count("const FETCH_TIMEOUT_MS") == 1, _host.count("const ROUND_WATCHDOG_MS") == 1,
       _host.count("const HEARTBEAT_EVERY") == 1, _host.count("const TICK_MS") == 1],
      [True, True, True, True])
# 反向对照：W1/W3 这两组"不许/必须"不能是恒真 —— 拿旧形状（无超时 + 静默跳过）喂同一对探针
_old_fj = 'async function fetchJson(url, body) { const r = await fetch(url, { method: "GET" }); return r.json(); }'
_old_run = "let running = false;\n    const run = () => { if (running) return; running = true; };"
check("W6 反向对照：把【昨晚那一版】喂进同一组探针 ⇒ 必须全部不成立（证明 W1/W3 真的在咬形状）",
      ["AbortSignal.timeout" in _old_fj, "上一轮还没回来" in _old_run,
       "setTimeout(" in _old_run, "心跳" in _old_run], [False, False, False, False])
# 语法必须还能过（宿主插件抛在 apply 之外 = 整棵插件树失败 = 桌面端崩溃循环，见 D1）
_node = subprocess.run(["node", "-e",
                        "import('file:///" + os.path.abspath(_host_path).replace("\\", "/")
                        + "').then(()=>console.log('ok'),e=>{console.log('ERR '+e.message);process.exit(1)})"],
                       capture_output=True, text=True, timeout=60)
check("W7 这份宿主插件 import 得动（语法/引用错在桌面端会变成崩溃循环，不只是「这条线不工作」）",
      (_node.stdout or "").strip(), "ok")

print(_safe("\n=== 表单唤醒闸：%d 条断言，失败 %d ===" % (TOTAL[0], len(fails))))
if fails:
    print(_safe("失败清单: " + " | ".join(f.split()[0] for f in fails)))
sys.exit(1 if fails else 0)
