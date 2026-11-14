#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""闸：桌宠「任务完成 / 活跃时长」的回合记账判据（2026-10-09 方案 1）。

为什么钉这条（读账本实测，不是猜）：state.json 里 tasksDone=1、activeMs=0、sessions=10 ——
两条账都挂在 ctx.jobs（内核"后台任务"服务）上，而用户在会话里让 agent 干活是【前台回合】，
不产生 job ⇒ 计数长期个位数、活跃时长恒 0、四个靠时长的称号永远拿不到。

T 组 = 真跑补丁里的 turnAccount（node 跑函数真身，不是 Python 重写一遍判据）。
S 组 = 形状（旧的双计入口必须摘掉、调用点位置、账本容器、备份在盘可回退）。
"""
import io
import json
import os
import subprocess
import sys
import tempfile

TARGET = (r"C:\Users\Administrator\AppData\Roaming\dsh-desktop\harness\profiles\web\node_modules"
          r"\whale-girl\lib\index.mjs")
BAK = TARGET + ".bak-weeksir-turn-account-2026009"
BAK2 = TARGET + ".bak-weeksir-turn-account-20261009"
STATE = r"C:\Users\Administrator\AppData\Roaming\dsh-desktop\harness\data\whale-girl\state.json"
REV = "weeksir-turn-account-20261009a"

PASS, FAIL = [], []


def check(name, got, want):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print("%-4s %-54s got=%r want=%r" % ("PASS" if ok else "FAIL", name, got, want))


def read(p):
    return io.open(p, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")


def slice_fn(src, name):
    i = src.index("function %s(" % name)
    j = src.index("// ==== end weeksir 补丁 ====", i)
    return src[i:j]


def main():
    if not os.path.exists(TARGET):
        print("FAIL 找不到:", TARGET)
        return 1
    src = read(TARGET)
    if REV not in src:
        print("FAIL 补丁不在位（rev 缺失）")
        return 1
    body = slice_fn(src, "turnAccount")
    mod = os.path.join(tempfile.gettempdir(), "wk_turn_account.mjs")
    io.open(mod, "w", encoding="utf-8").write(body + "\nexport { turnAccount };\n")

    runner = r"""
import { turnAccount } from %r;const out = [];
const run = (name, steps) => {
  const ledger = new Map();
  const acc = [];
  try {
    for (const [type, data, t] of steps) {
      const r = turnAccount(ledger, 's1', { type, data }, t);
      if (r !== null) acc.push(r);
    }
  } catch (e) { acc.push('THREW:' + e.message); }
  out.push([name, JSON.stringify(acc)]);
};
const T0 = 1000;
run('T1 动过手且正常收尾 = 计一次 + 累加时长', [['turn/start', null, T0], ['tool/call', {name:'Bash'}, T0+1000], ['turn/end', {reason:{kind:'end_turn'}}, T0+61000]]);
run('T2 纯问答（没动工具）绝不计', [['turn/start', null, T0], ['turn/end', {reason:{kind:'end_turn'}}, T0+9000]]);
run('T3 等批准(blocked)的收尾不算完成', [['turn/start', null, T0], ['tool/call', {name:'Bash'}, T0+500], ['turn/end', {reason:{kind:'blocked'}}, T0+4000]]);
run('T4 漏收 turn/start 但见过工具 = 仍计（时长 0）', [['tool/call', {name:'Read'}, T0], ['turn/end', {reason:{kind:'end_turn'}}, T0+3000]]);
run('T5 漏 start 且没见过工具 = 不计', [['turn/end', {reason:{kind:'end_turn'}}, T0]]);
run('T6 两条 turn/end 不双计', [['turn/start', null, T0], ['tool/call', {name:'Bash'}, T0+1], ['turn/end', {reason:{kind:'end_turn'}}, T0+2000], ['turn/end', {reason:{kind:'end_turn'}}, T0+3000]]);
run('T7 无关事件不动账本', [['session/title', {title:'x'}, T0], ['turn/start', null, T0], ['tool/call', {name:'Bash'}, T0+10], ['turn/end', {reason:{kind:'end_turn'}}, T0+2010]]);
run('T8 时钟回拨：时长取 0 不取负', [['turn/start', null, T0+9000], ['tool/call', {name:'Bash'}, T0+9001], ['turn/end', {reason:{kind:'end_turn'}}, T0]]);
run('T9 多工具只算一次任务', [['turn/start', null, T0], ['tool/call', {name:'Bash'}, T0+1], ['tool/call', {name:'Write'}, T0+2], ['turn/end', {reason:{kind:'end_turn'}}, T0+5000]]);
const dirty = [
  ['T10 脏输入 event=null', null],
  ['T11 脏输入 event={}', {}],
  ['T12 脏输入 type 非字符串', { type: 7 }],
  ['T13 脏输入 data 非对象', { type: 'turn/end', data: 'x' }],
  ['T14 脏输入 reason 是字符串', { type: 'turn/end', data: { reason: 'blocked' } }],
];
for (const [name, ev] of dirty) {
  let r;
  try { r = JSON.stringify(turnAccount(new Map(), 's', ev, Date.now())); }
  catch (e) { r = 'THREW:' + e.message; }
  out.push([name, r]);
}
console.log(JSON.stringify(out));
""" % ("file:///" + mod.replace(os.sep, "/").lstrip("/").replace(" ", "%20"))
    rp = os.path.join(tempfile.gettempdir(), "wk_turn_runner.mjs")
    io.open(rp, "w", encoding="utf-8").write(runner)
    # 🔴 `text=True` 不带 encoding ⇒ 按 locale(cp936) 解 node 的 UTF-8 中文输出 ⇒ reader 线程崩、
    #    p.stdout 变成 None，下一条 json.loads 抛 TypeError（闸崩 ≠ 闸红：整条判据根本没跑完）。
    p = subprocess.run(["node", rp], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    if p.returncode != 0:
        print("FAIL node 跑真身失败:", (p.stderr or p.stdout)[:400])
        return 1
    got = dict(json.loads(p.stdout))

    def W(counted, ms):
        return '[{"counted":%s,"activeMs":%d}]' % ("true" if counted else "false", ms)

    check("T1 动过手且正常收尾 = 计一次 + 累加时长", got["T1 动过手且正常收尾 = 计一次 + 累加时长"], W(True, 61000))
    check("T2 纯问答（没动工具）绝不计", got["T2 纯问答（没动工具）绝不计"], W(False, 9000))
    check("T3 等批准(blocked)的收尾不算完成", got["T3 等批准(blocked)的收尾不算完成"], W(False, 4000))
    check("T4 漏收 turn/start 但见过工具 = 仍计", got["T4 漏收 turn/start 但见过工具 = 仍计（时长 0）"], W(True, 3000))
    check("T5 漏 start 且没见过工具 = 不计", got["T5 漏 start 且没见过工具 = 不计"], W(False, 0))
    check("T6 两条 turn/end 不双计（第二条必 false）", got["T6 两条 turn/end 不双计"],
          '[{"counted":true,"activeMs":2000},{"counted":false,"activeMs":0}]')
    check("T7 无关事件不动账本", got["T7 无关事件不动账本"], W(True, 2010))
    check("T8 时钟回拨：时长取 0 不取负", got["T8 时钟回拨：时长取 0 不取负"], W(True, 0))
    check("T9 多工具只算一次任务", got["T9 多工具只算一次任务"], W(True, 5000))
    # null = 这条事件与记账无关（非 turn 事件）；turn/end 一律给判定，哪怕 data/reason 形状不对
    for name in ("T10 脏输入 event=null", "T11 脏输入 event={}", "T12 脏输入 type 非字符串"):
        check(name, got[name], "null")
    ONE = '{"counted":false,"activeMs":0}'   # 脏输入组是单事件直调，不套 run() 的数组
    check("T13 脏输入 data 非对象 = 不抛、按未动手处理", got["T13 脏输入 data 非对象"], ONE)
    check("T14 脏输入 reason 是字符串 = 不抛、不误判 blocked", got["T14 脏输入 reason 是字符串"], ONE)

    # ---- S 组：形状 ----
    seg = src.split("snapshot.status === 'completed'")[1].split("} else if")[0] if "snapshot.status === 'completed'" in src else ""
    # 🔴 剥掉注释再扫：我自己在补丁注释里写了"不再 recordTaskCompleted"，整段搜会被自己的注释判红
    NL = chr(10)
    code = NL.join(l for l in seg.split(NL) if not l.strip().startswith("//"))
    check("S1 后台 job 完成不再计任务（防一次工作计两遍）", "recordTaskCompleted" in code, False)
    check("S2 job 失败仍记 failures（那条腿没动）", "recordFailure" in src.split("snapshot.status === 'failed'")[1][:200] if "snapshot.status === 'failed'" in src else False, True)
    i_call = src.find("const wacc = turnAccount(")
    i_parse = src.find("const parsed = parseTurnEvent(event)")
    check("S3 记账点在 parseTurnEvent 之前（否则 tool/call 看不到）", 0 < i_call < i_parse, True)
    check("S4 回合账本是 Map（按会话隔离）", "const weeksirTurns = new Map()" in src, True)
    check("S5 时长走 recordActive（沿用作者 5 分钟单次封顶）", "recordActive(state, wacc.activeMs" in src, True)
    check("S6 任务走 recordTaskCompleted（沿用作者 XP/称号/回忆）", "recordTaskCompleted(state, view.title" in src, True)
    check("S7 记完就落盘+广播（不等 15s 轮询）", "if (wchanged) {" in src, True)
    check("S8 原版备份在盘（可回退）", os.path.exists(BAK2) or os.path.exists(BAK), True)
    check("S9 判据是纯函数（模块层、不碰 state）", "\nfunction turnAccount(ledger, sessionId, event, nowMs) {" in src, True)

    if os.path.exists(STATE):
        st = json.loads(read(STATE)).get("stats", {})
        print("      现场账本（改前基线，供对照）: tasksDone=%s activeMs=%s sessions=%s" % (
            st.get("tasksDone"), st.get("activeMs"), st.get("sessions")))

    print("\n=== 桌宠回合记账闸：%d/%d 通过 ===" % (len(PASS), len(PASS) + len(FAIL)))
    for f in FAIL:
        print("  FAIL:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
