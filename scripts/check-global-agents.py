#!/usr/bin/env python
"""全局行为层（$DSH_HOME/AGENTS.md）的闸。

为什么钉这条：2026-10-05 才发现我们所有"铁律"都写在 C:\\dsh\\agents.md，那是**工作区级**
（内核只沿 projectRoot 找文件），客户机根本读不到 ⇒ "对整个 agent 执行进行指导"其实没生效。
真正的跨工作区层是 `$DSH_HOME/AGENTS.md`（dsh-agent-instructions/lib/index.js:561-568 会 stat 并加载，
:130 拼成 `Instructions from: …`，:1229-1233 注入 next-step 且 touchedPath 会重算 ⇒ 不用重启）。

这个核只读、不改任何东西，管四件事：
  A 全局文件在、且没被写回啰嗦（行数/字节上限 = 每轮都进 prompt 的钱）
  B 口径一致：全局 §1 的 95% 阈值与工作区 agents.md 必须同数；旧口径"禁止向用户提问"不许回潮
  C 内核那层读文件的契约还在（升级内核后若被删，本闸要红，别等客户机静默失约束）
  D 客户机拿得到：全局文件必须同时存在于 weeksir-skills 骨架里（发版要写进装机路径）
"""
import io
import json
import pathlib
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

GLOBAL = pathlib.Path(r"C:\Users\Administrator\AppData\Roaming\dsh-desktop\harness\AGENTS.md")
WS = pathlib.Path(r"C:\dsh\agents.md")
KERNEL = pathlib.Path(
    r"C:\Users\Administrator\AppData\Local\Programs\DSH Desktop\resources\app\node_modules"
    r"\@deepseek-ai\dsh-agent-instructions\lib\index.js")
BUNDLE = pathlib.Path(r"C:\dsh\weeksir-skills\AGENTS.global.md")
out = []


def check(name, got, want):
    ok = json.dumps(got, ensure_ascii=False, default=str) == json.dumps(want, ensure_ascii=False, default=str)
    out.append((ok, name))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else " => %r (期望 %r)" % (got, want)))


g = GLOBAL.read_text(encoding="utf-8") if GLOBAL.is_file() else ""
w = WS.read_text(encoding="utf-8") if WS.is_file() else ""
k = KERNEL.read_text(encoding="utf-8", errors="replace") if KERNEL.is_file() else ""

# ---- A 全局层存在且不啰嗦（每轮都进 system prompt ⇒ 按 1 分≈100 token 计价）----
check("A1 全局 AGENTS.md 存在", GLOBAL.is_file(), True)
lines = [l for l in g.splitlines() if l.strip()]
check("A2 非空行 ≤ 30（超了就是又开始啰嗦；这条每轮都要付 token）", len(lines) <= 30, True)
check("A3 全文 ≤ 4200 字节（2026-10-09 两次加节：§7 →3351、§8 →3777，≈900→≈1260 token/轮；"
      "2026-10-12 加『免批只能登记这条腿自己的 purpose』+ 两份表都要点名 →4074，≈1260→≈1360 token/轮，"
      "成本已报给人看；再加节先报成本再动这个数）",
      len(g.encode("utf-8")) <= 4200, True)

# ---- B 口径：95% 阈值 + 四类必停 + 不许旧口径回潮 ----
check("B1 全局 §1 写的是 95% 把握阈值", bool(re.search(r"95%\s*以上把握", g)), True)
check("B2 工作区 agents.md 用同一个数（两处不同数=模型挑省事的读）",
      bool(re.search(r"95%\s*(把握|以上把握)", w)), True)
check("B3 旧口径『禁止向用户提问确认』已从两处清掉（它和 95% 直接对打）",
      ["禁止向用户提问确认" in g, "禁止向用户提问确认" in w], [False, False])
gates = ["方案", "报价", "客服"]
check("B4 必停四类在全局里点名（缺了就会被当成可跳过）",
      [x in g for x in gates], [True] * len(gates))
check("B5 交付判据在全局：文件没落到盘上并且能出示 = 没完成",
      ["没落到盘上并且能出示" in g, "路径 + 直链 + 贴图" in g], [True, True])
check("B6 可自动化的两条留了硬判据（每行可追溯 / 先写 verify）",
      ["追溯到这次的请求" in g, "步骤 → verify" in g], [True, True])
# §7「结论必须有出处」：钉的是【可判据的输出形状】。态度词（仔细/认真/注意）对快档模型不落地，
# 只会每轮白付 token，所以这里反过来禁止它们进 §7。
sec7 = g[g.index("## 7"):] if "## 7" in g else ""
check("B7 §7 三条硬形状都在（出处 / 未验证兜底 / 生效性复验 / 人眼可查）",
      [x in sec7 for x in ("文件:行号", "未验证", "启动时间", "人眼可查")], [True] * 4)
check("B7b §7 里不许有态度词（'仔细/认真/注意'不可判据 = 白付 token）",
      [w in sec7 for w in ("仔细", "认真", "注意")], [False, False, False])
# 🔴 2026-10-09 自查补：§7 最初写的是"审计口径 ≥ 0.9"，而全仓没有任何脚本在算它（§6 的 ≤ 0.3 同样没人算）
#    ⇒ 那是"文档写了没实现的承诺"，和装饰没区别。要留数字阈值，必须先有量具；否则写成人眼可查的形状。
check("B7d §7 里不许有没有实现者的数字阈值（要阈值先建量具）",
      re.findall(r"[≥≤]\s*0?\.\d+", sec7), [])
check("B7c §7 不许被搬进工作区层（两处各写一份迟早漂移；工作区只写本工作区特有的）",
      "结论必须有出处" in w, False)

# ---- C 内核那层还在读（升级后若改机制，本闸必须红）----
check("C1 内核仍认 `$DSH_HOME/AGENTS.md` 这个用户全局层",
      ['const USER_GLOBAL_FILE = "AGENTS.md"' in k, "join(config.dshHome, USER_GLOBAL_FILE)" in k], [True, True])
check("C2 项目层仍是工作区级（所以客户机读不到 C:\\dsh\\agents.md，别再往那儿写全局规则）",
      "ancestorChain(projectRoot, cwd)" in k, True)
check("C3 注入形式没变（排查时按这个字符串去账本里找）", "Instructions from: " in k, True)

# ---- D 发版：客户机要拿得到这份文件 ----
check("D1 weeksir-skills 骨架里存了一份（装机脚本要往 $DSH_HOME 写的那份）", BUNDLE.is_file(), True)
if BUNDLE.is_file():
    check("D2 骨架里那份与本机生效这份逐字一致（防漂移：改一处必须同步）",
          BUNDLE.read_text(encoding="utf-8") == g, True)

bad = [n for ok, n in out if not ok]
print("\n=== 全局行为层闸：%d 条断言，失败 %d ===" % (len(out), len(bad)))
print("生效方式：会话内 touchedPath 重算（不用重启）；客户机由发版塑形脚本写 %s" % GLOBAL)
sys.exit(1 if bad else 0)
