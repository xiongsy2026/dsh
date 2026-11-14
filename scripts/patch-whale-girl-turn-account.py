# -*- coding: utf-8 -*-
"""whale-girl 第 5 处补丁：任务/活跃时长的主人从「后台 job」改成「回合」。

为什么必须改（2026-10-09 读账本实测，不是猜）：
  state.json = tasksDone 1 / activeMs 0 / sessions 10。
  · tasksDone 唯一来源是 ctx.jobs.onJobDone（index.mjs:378-385）—— 内核的【后台任务】服务；
    你在会话里让 agent 干活是【前台回合】，不产生 job ⇒ 长期个位数。
  · activeMs 只在 derived.working 为真时累加（:277-280），而 working 由 collectTasks() 推（:69-86）
    —— 同样只看 jobs ⇒ 前台跑二十分钟一点不计，「常驻伙伴」(6h) 永远拿不到。

用户拍定方案 1 的判据（写成纯函数 turnAccount，闸整块抠出来跑值表）：
  一个回合【动过手（本轮有 tool/call）且正常收尾（turn/end 且 reason 非 blocked）】= 一次任务；
  活跃时长 = 同回合 turn/start→turn/end 的墙钟（recordActive 内部再按 ACTIVE_CAP_MS 封顶）。
  后台 job 完成不再计第二次（它必然由某个回合发起，那个回合已经计过）—— 只保留庆祝窗口。
"""
import io
import os
import shutil
import subprocess
import sys

TARGET = (r"C:\Users\Administrator\AppData\Roaming\dsh-desktop\harness\profiles\web\node_modules"
          r"\whale-girl\lib\index.mjs")
BAK = TARGET + ".bak-weeksir-turn-account-20261009"
REV = "weeksir-turn-account-20261009a"

# ---- 1) 纯函数 + 每会话回合账本：插在 collectTasks 之前（模块层） ----
ANCHOR_PURE = "/** 收集宿主全部任务：owned（按 agent 遍历，绕过 owner fence）+ unowned，按 id 去重。 */"
PURE = '''// ==== weeksir 补丁 %s：回合记账判据（方案 1，用户 2026-10-09 拍定）====
// 纯函数：不碰 state、不碰 IO ⇒ 闸能整块抠出来跑值表。
// 返回 null = 这条事件与记账无关；否则 { counted, activeMs }。
// counted 只在【本轮有 tool/call 且 turn/end 的 reason 不是 blocked】时为真：
//   纯问答（没动过工具）不算任务，等用户批准的回合还没结束也不算，
//   漏收 turn/start（订阅前就开跑的会话）不算 —— 宁少计，别把一次工作计两遍。
function turnAccount(ledger, sessionId, event, nowMs) {
  const type = event !== null && typeof event === "object" && typeof event.type === "string" ? event.type : ""
  if (type === "") return null
  if (type === "turn/start") {
    ledger.set(sessionId, { startedAt: nowMs, hadTool: false })
    return null
  }
  if (type === "tool/call") {
    const rec = ledger.get(sessionId)
    if (rec !== undefined) rec.hadTool = true
    else ledger.set(sessionId, { startedAt: nowMs, hadTool: true })
    return null
  }
  if (type !== "turn/end") return null
  const rec2 = ledger.get(sessionId)
  ledger.delete(sessionId)
  const data = event !== null && typeof event === "object" && event.data !== null && typeof event.data === "object" ? event.data : null
  const reason = data !== null ? data.reason : null
  const blocked = reason !== null && typeof reason === "object" && reason.kind === "blocked"
  const activeMs = rec2 !== undefined && typeof rec2.startedAt === "number" ? Math.max(0, nowMs - rec2.startedAt) : 0
  if (rec2 === undefined || blocked === true || rec2.hadTool !== true) return { counted: false, activeMs }
  return { counted: true, activeMs }
}
// ==== end weeksir 补丁 ====

''' % REV

# ---- 2) 调用点：session/event 里 turn 边沿处记账 ----
ANCHOR_CALL = "        const parsed = parseTurnEvent(event)"
CALL_PRE = """        // weeksir %s：回合记账（任务 + 活跃时长）。放在 parseTurnEvent 之前，
        // 这样 tool/call 也能被同一份账本看到（parseTurnEvent 只认 turn/start·turn/end）。
        const wacc = turnAccount(weeksirTurns, id, event, Date.now())
        if (wacc !== null) {
          const wnow = Date.now()
          let wchanged = false
          if (wacc.activeMs > 0) {
            state = recordActive(state, wacc.activeMs, wnow).state
            wchanged = true
          }
          if (wacc.counted) {
            const wresult = recordTaskCompleted(state, view.title || base.title || '未命名任务', wnow)
            state = wresult.state
            celebrateUntil = Math.max(celebrateUntil, wnow + configRef.celebrateMs)
            if (wresult.leveledUp) emitSignal('levelUp', { level: state.level })
            emitSignal('celebrate', { label: view.title || base.title || '未命名任务', level: state.level })
            wchanged = true
          }
          if (wchanged) {
            scheduleSave()
            broadcastEvent()
          }
        }
""" % REV

# ---- 3) 回合账本容器：挂在 apply() 里已有的活动变量旁 ----
ANCHOR_LEDGER = "  const signals = createSignals()"
LEDGER = "  const weeksirTurns = new Map() // weeksir %s：会话 id → { startedAt, hadTool }\n" % REV

# ---- 4) 后台 job 完成不再计第二次（保留庆祝），失败仍记 failures ----
OLD_COMPLETED = """        if (snapshot.status === 'completed') {
          const result = recordTaskCompleted(state, snapshot.label ?? '未命名任务', now)
          state = result.state"""
NEW_COMPLETED = """        if (snapshot.status === 'completed') {
          // weeksir %s：不再 recordTaskCompleted —— 发起它的回合已经计过一次任务，
          // 两处各计 = 一次工作记两遍（用户口径"同一个事不许留两个入口"）。这里只留庆祝窗口。
          const result = { state, leveledUp: false }""" % REV

DECL = "import { turnAccount }"


def main():
    if not os.path.exists(TARGET):
        print("FAIL 找不到:", TARGET)
        return 1
    raw = io.open(TARGET, "rb").read()
    crlf = raw.count(b"\r\n") > 0 and raw.count(b"\r\n") == raw.count(b"\n")
    src = raw.decode("utf-8").replace("\r\n", "\n")
    if REV in src:
        print("SKIP 已打过")
        return 0

    def eq(lines, needle):
        return [i for i, l in enumerate(lines) if l == needle]

    def ins(lines, i, block):
        lines[i:i] = block

    lines = src.split("\n")
    for name, hits in (("collectTasks 锚点", eq(lines, ANCHOR_PURE)),
                       ("parseTurnEvent 锚点", eq(lines, ANCHOR_CALL)),
                       ("signals 锚点", eq(lines, ANCHOR_LEDGER))):
        if len(hits) != 1:
            print("FAIL %s 命中 %d 处（要求 1）" % (name, len(hits)))
            return 1
    if src.count(OLD_COMPLETED) != 1:
        print("FAIL job-completed 块命中 %d 处" % src.count(OLD_COMPLETED))
        return 1

    # 从后往前插，避免前面的插入让后面的行号漂移
    i_call = eq(lines, ANCHOR_CALL)[0]
    ins(lines, i_call, CALL_PRE.split("\n"))
    old_lines = OLD_COMPLETED.split("\n")
    hits = [k for k in range(len(lines) - len(old_lines) + 1) if lines[k:k + len(old_lines)] == old_lines]
    if len(hits) != 1:
        print("FAIL job-completed 块行扫命中 %d 处" % len(hits))
        return 1
    i_cmp = hits[0]
    lines[i_cmp:i_cmp + len(old_lines)] = NEW_COMPLETED.split("\n")
    i_sig = eq(lines, ANCHOR_LEDGER)[0]
    ins(lines, i_sig + 1, [LEDGER.rstrip("\n")])
    i_pure = eq(lines, ANCHOR_PURE)[0]
    ins(lines, i_pure, PURE.rstrip("\n").split("\n"))

    out = "\n".join(lines)
    # 自检：判据函数在、调用点在、旧的双计已摘掉
    for must in ("function turnAccount(", "const wacc = turnAccount(", "const weeksirTurns = new Map()"):
        if must not in out:
            print("FAIL 缺: " + must)
            return 1
    if "const result = recordTaskCompleted(state, snapshot.label" in out:
        print("FAIL job 完成那条还在计任务（会双计）")
        return 1

    if not os.path.exists(BAK):
        shutil.copy2(TARGET, BAK)
    tmp = TARGET + ".weeksir-tmp"
    with io.open(tmp, "wb") as f:
        f.write((out.replace("\n", "\r\n") if crlf else out).encode("utf-8"))
    # 语法不过就不落盘（mjs 用 --input-type=module 检查）
    chk = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
    if chk.returncode != 0:
        # node --check 对 .mjs 后缀才按模块解析；改名再试一次
        mjs = os.path.join(os.path.dirname(tmp), "wk_check.mjs")
        shutil.move(tmp, mjs)
        chk = subprocess.run(["node", "--check", mjs], capture_output=True, text=True)
        if chk.returncode != 0:
            print("FAIL 语法不过，未落盘:", (chk.stderr or chk.stdout)[:400])
            os.remove(mjs)
            return 1
        tmp = mjs
    os.replace(tmp, TARGET)
    print("OK  %d → %d 字节  备份=%s" % (len(src), len(out), os.path.basename(BAK)))
    print("\n=== whale-girl 回合记账补丁 %s 已落盘 ===" % REV)
    return 0


if __name__ == "__main__":
    sys.exit(main())
