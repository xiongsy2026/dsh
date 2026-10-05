#!/usr/bin/env python
"""执行中分组（chat-progress）的闸 —— 钉的是"三条自律"，不是界面好不好看。

为什么这么钉（docs/PRD-chat-progress.md §3）：这段逻辑靠遍历内核 DOM，最容易坏的两件事是
① 选了 CSS Modules 的哈希 class（内核一升级全漂，界面静默回到刷屏）；
② 搬了 React 管理的节点（和调和过程打架，可能整块崩）。
所以这里只扫源码 + 扫内核契约，不依赖 DSH 进程活着（那类断言是假保险）。
跑法：python scripts/check-chat-progress.py
"""
import io
import json
import os
import pathlib
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
SRC = pathlib.Path(r"C:\dsh\plugins\super-clone-ui\lib\client.js")
CHAT = pathlib.Path(
    r"C:\Users\Administrator\AppData\Local\Programs\DSH Desktop\resources\app\node_modules"
    r"\@deepseek-ai\dsh-client-ui-chat\lib\client.js")
out = []


def check(name, got, want):
    ok = json.dumps(got, ensure_ascii=False, default=str) == json.dumps(want, ensure_ascii=False, default=str)
    out.append((ok, name))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else " => %r 期望 %r" % (got, want)))


src = SRC.read_text(encoding="utf-8")
i0 = src.find("===== 执行中分组")
i1 = src.find("function apply(ctx)")
block = src[i0:i1] if i0 >= 0 and i1 > i0 else ""

# ---- A 功能在、且挂在 apply 里 ----
check("A1 分组逻辑存在", i0 > 0, True)
check("A2 apply() 里真的挂了（不是写完没人调）", "mountTurnFold(ctx);" in src[i1:], True)
check("A3 ctx.setInterval 要在 inject 里声明 timer，否则 guard 静默拒 ⇒ 秒数不动",
      '"timer"' in src[src.find("const inject = ["):src.find("const inject = [") + 120], True)

# ---- W5 只用 data-* 语义属性，不许碰哈希 class ----
hashed = re.findall(r"[\"'.]([A-Za-z0-9]{6}_[A-Za-z][A-Za-z0-9]*)", block)
check("W5 分组段里零个 CSS Modules 哈希 class", hashed, [])
check("W5b 选择器只用 data-*", block.count("[data-chat-flow-kind][data-chat-turn]") >= 1, True)

# ---- W6 不搬 React 节点 ----
check("W6 insertBefore 只出现一次（仅组首插一个头）", block.count("insertBefore("), 1)
check("W6b 不许 removeChild / replaceChild（搬节点=和调和打架）",
      ["removeChild(" in block, "replaceChild(" in block], [False, False])

# ---- 三条产品纪律（PRD 非目标）----
keep = re.search(r"WS_TURN_KEEP = \[([^\]]*)\]", block)
klist = re.findall(r'"([a-z-]+)"', keep.group(1)) if keep else []
check("W3 用户消息与报错永远不收（user/steering/turn-error 必须在 KEEP 里）",
      [k in klist for k in ("user", "steering", "turn-error")], [True, True, True])
check("W2 最后一步永远露着（全收起来客户以为卡死）",
      "proc[proc.length - 1].removeAttribute(\"data-ws-fold\")" in block, True)
check("W4 官方折过的立刻让位（不做第二套折叠）", "data-turn-process-hidden" in block, True)
check("W1 组头带步数与秒数", ("执行中 · " in block and " 步 · " in block), True)
check("W1b 计时靠第一次见到该轮的时间戳（不是每帧重算）", "turnFirstSeen" in block, True)
check("W4b 让位时会撤掉自己的头（不留双头）", "if (head) head.remove();" in block, True)

# ---- W7 内核契约漂移要红 ----
chat = CHAT.read_text(encoding="utf-8", errors="replace") if CHAT.is_file() else ""
check("W7 内核仍在发这四个语义属性",
      [('"data-chat-turn"' in chat), ('"data-chat-flow-kind"' in chat),
       ('"data-turn-process-member"' in chat), ('"data-turn-process-hidden"' in chat)],
      [True, True, True, True])
check("W7b 折叠仍要等 turnClosed（这正是我们要补的那一格，别哪天内核自己做了还留着两套）",
      "turnClosed" in chat, True)
check("W7c 官方 compact 仍是默认（我们只补执行中，不改它的语义）",
      'DEFAULT_TRANSCRIPT_VIEW_MODE = "compact"' in chat, True)

# 行为真值表（假 DOM 跑 turnFoldTick）另有一篇，这里聚合成一个入口
r = __import__("subprocess").run(["node", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "check-chat-progress-dom.mjs")], capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
tail = [x for x in (r.stdout or "").splitlines() if "条，失败" in x]
out.append((r.returncode == 0, "行为真值表 check-chat-progress-dom.mjs " + (tail[-1] if tail else "(无输出)")))

import os
bad = [n for ok, n in out if not ok]
print(chr(10) + "=== 执行中分组闸（源码形状 + 行为真值表）：%d 组，失败 %d ===" % (len(out), len(bad)))
print("生效方式：Ctrl+R 重载窗口（super-clone-ui 的 client.js 走工作区路径）；真界面验证见任务 T4。")
sys.exit(1 if bad else 0)
