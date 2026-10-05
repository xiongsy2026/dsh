#!/usr/bin/env python
r"""对话框常驻技能 chips 的回归闸（PRD-composer-entry §6 的 X3/X4/X5/X6 可自动判部分）。

跑法：python scripts/check-composer-entry.py
钉的是【规则】不是现场：
  - 名单只有一个源（SKILL_DOCK_META），且每条都真能在 .dsh/skills 里找到同名 SKILL.md ⇒ 再也不会出现 `/hypit ` 这种空炮；
  - chips 必须复用 setSkillPrompt（不许自己拼命令，否则 X2 的"清旧前缀/命令置前"语义会分叉）；
  - 改动范围不许越到内核包与 SKILL.md。
X1/X2（点一下输入框出现 /omni、旧前缀被清）是运行时行为，由真界面实测覆盖（T4/X7 截图），这里不假装能静态判。
"""
import io
import json
import os
import pathlib
import re
import subprocess
import sys

CLIENT = pathlib.Path(r"C:\dsh\plugins\super-clone-ui\lib\client.js")
SKILLS_DIR = pathlib.Path(r"C:\dsh\.dsh\skills")
TOTAL = 0
fails = []


def _safe(s):
    """GBK 控制台上 ⇒/🔴 这类字符会让 print 抛 UnicodeEncodeError 直接崩掉整个闸（2026-10-05 实测：
    这个闸在真控制台上从来没绿过，之前"绿"是因为测量环境继承了 PYTHONIOENCODING=utf-8 把 bug 遮住了）。
    🔴 只按【输出流的编码】做 errors='replace'，绝不 sys.stdout.reconfigure —— 后者会造成"零输出 + exit 0"的静默假绿。
    ⚠️ 别写 s.encode()：str.encode() 默认就是 utf-8，永远不失败，等于没兜住。"""
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        s.encode(enc)
        return s
    except (UnicodeEncodeError, LookupError):
        return s.encode(enc, "replace").decode(enc, "replace")


def check(name, got, want):
    global TOTAL
    TOTAL += 1
    a = __import__("json").dumps(got, ensure_ascii=False, default=str)
    b = __import__("json").dumps(want, ensure_ascii=False, default=str)
    print(_safe(("PASS " if a == b else "FAIL ") + name + ("" if a == b else "  => %r (期望 %r)" % (got, want))))
    if a != b:
        fails.append(name)


src = CLIENT.read_text(encoding="utf-8")

# ---- A 组：名单单源 + 对齐实况（X3）----
check("A1 SKILLS 不再手写条目（只剩空壳 + 派生）",
      [bool(re.search(r"const SKILLS = \[\s*\];", src)), "SKILLS.push({ id: id, label: SKILL_DOCK_META[id].title" in src],
      [True, True])
check("A2 派生顺序照 SKILL_DOCK_META.order",
      ".sort((a, b) => SKILL_DOCK_META[a].order - SKILL_DOCK_META[b].order)" in src, True)
check("A3 旧的空炮命令不再出现在源码里（/hypit 带尾空格）",
      '"/hypit "' in src or "prompt: \"/hypit \"" in src, False)

meta = re.search(r"const SKILL_DOCK_META = \{(.*?)\n    \};", src, re.S)
ids = re.findall(r'^\s*"?([\w-]+)"?:\s*\{', meta.group(1), re.M) if meta else []
check("A4 dock 白名单 5 条齐全（客户可见面 = SKILL_DOCK_ALLOW）",
      sorted(ids), sorted(["super-clone", "hypit-pipeline", "omni", "short-drama", "video-dewatermark"]))

# 每条 id 必须真能在技能目录里找到、且 SKILL.md 的 name 字段就是它
missing, mismatched = [], []
for i in ids:
    p = SKILLS_DIR / i / "SKILL.md"
    if not p.is_file():
        missing.append(i)
        continue
    nm = re.search(r"^name:\s*(.+)$", p.read_text(encoding="utf-8", errors="replace")[:600], re.M)
    got = nm.group(1).strip().strip('"\'') if nm else None
    if got != i:
        mismatched.append([i, got])
check("A5 每个 id 都有对应 .dsh/skills/<id>/SKILL.md（零条指向不存在的命令）", missing, [])
check("A6 每个 id 与 SKILL.md 的 name 字段逐字一致", mismatched, [])

# ---- B 组：技能选择框本身（下拉）留在原位 + 写入路径纪律 ----
# 🔴 2026-10-05 用户判定：常驻 chips 把工具栏搞乱了，chips 整块撤销；技能选择回到"只有一个下拉"。
#    所以这里钉的是：① 下拉必须仍然被挂载（3 个调用点）② 名单只有一个源（A 组已钉）
#    ③ 点技能要真能写进输入框（setSkillPrompt 的 selectAll 那版在框里已有文字时静默失效）。
calls = len(re.findall(r"(?<!function )mountNativeSkillPicker\(\)", src))
check("B1 技能下拉仍被挂载（首挂 + 两处节流重挂 = 3 个调用点）", calls, 3)
check("B2 chips 已彻底撤销（源码里不留第二入口，也不留死代码）",
      ["SKILL_CHIPS_REV" in src, "mountSkillChips" in src, "data-ws-skill-chip" in src], [False, False, False])
sp = src[src.find("function setSkillPrompt"):src.find("function setSkillPrompt") + 1600]
sp_code = "\n".join(l for l in sp.splitlines() if not l.strip().startswith("//"))
check("B3 setSkillPrompt 不再用 execCommand('selectAll')（框里有字时选不中 ⇒ 点技能没反应）",
      'document.execCommand("selectAll"' in sp_code, False)
check("B3b 改用显式 Range 覆盖整个输入区再 insertText",
      "rg.selectNodeContents(ce);" in sp_code and 'document.execCommand("insertText", false, out)' in sp_code, True)
check("B4 命令置前语义未变（清旧前缀 → prompt + 余文）",
      'const out = txt ? (prompt + txt) : prompt;' in sp_code, True)
check("B5 下拉行仍由 SKILLS 单源生成（没有另写一份菜单项）",
      "const skillRows = SKILLS.map(function (s)" in src, True)

# ---- C 组：改动范围（X6）----
# ⚠️ 这个仓库有并发写入者：`.dsh/skills/**` 里那批改动是【另一个会话】的在途工作（删 hypit 重复技能等），
#    拿"全量 diff 里不许出现 SKILL.md"当判据 = 一条永远红的假保险。所以这里只钉本任务真正能越界的两条硬边界，
#    SKILL.md 的实质风险（命令指向不存在的技能）已经由 A5/A6 从内容侧钉住了。
# 🔴 必须读 `git diff HEAD`（= 已暂存 + 未暂存）：只读未暂存的话，`git add` 之后 C2/C3 会凭空变红
#    —— 那是闸的毛病不是代码的毛病（2026-10-05 实测踩过一次）。断言一律写成"禁集为空"，不写"集合必须恰好等于 X"。
r = subprocess.run(["git", "-C", r"C:\dsh", "diff", "HEAD", "--name-only"],
                   capture_output=True, text=True, encoding="utf-8")
changed = [l.strip().replace("\\", "/") for l in (r.stdout or "").splitlines() if l.strip()]
check("C0 diff 真读到了（git 成功 + 非空集），防止拿空集真空判绿",
      [r.returncode == 0, bool(changed)], [True, True])
check("C1 内核包零改动（node_modules / @deepseek-ai 一律不碰）",
      [f for f in changed if "node_modules" in f or "@deepseek-ai" in f], [])
check("C2 plugins/ 下的越界改动为空（只允许 super-clone-ui 这个插件被改）",
      sorted({f.split("/")[1] for f in changed if f.startswith("plugins/")} - {"super-clone-ui"}), [])
check("C3 本任务确实落在 client.js 上（不是只写了文档）",
      "plugins/super-clone-ui/lib/client.js" in changed, True)

# 🔴 agent 脑准入判据（账号桥不许当脑 / 桥地址不许硬编码 / 官方 GLM·Kimi 入口在册）不在本闸：
#    那是另一个规则族，写在 scripts/check-agent-brain-lane.py，别把两个 PRD 的销账混在一个闸里。

print(_safe("\n=== 对话框技能入口闸：%d 条断言，失败 %d ===" % (TOTAL, len(fails))))
print(_safe("X1/X2/X7 靠真界面实测与截图销账（本闸不假装能静态判运行时行为）。"))
if fails:
    print(_safe("失败清单: " + " | ".join(fails)))
sys.exit(1 if fails else 0)
