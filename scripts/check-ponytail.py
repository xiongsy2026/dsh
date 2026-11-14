#!/usr/bin/env python
"""ponytail skill 的边界闸：豁免清单必须写在 SKILL.md 里，豁免对象必须原样在盘上。

为什么钉这么死：ponytail 的本职是砍过度工程，YAGNI 梯子一旦越过边界，
productions 四件套 / docs 合同 / check-*.py 回归闸会被当成"没人要的样板"清掉——
那是交付链本体，不是代码。动过 .dsh/skills/ponytail/ 或做过"大扫除"就跑这条。
"""
import io
import os
import re
import sys
WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = WS
SKILL = os.path.join(ROOT, ".dsh", "skills", "ponytail", "SKILL.md")
out = []


def check(name, got, want):
    ok = got == want
    out.append(ok)
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else " => %r (期望 %r)" % (got, want)))


src = open(SKILL, encoding="utf-8").read() if os.path.isfile(SKILL) else ""
check("1 SKILL.md 在盘上", bool(src), True)

fm = re.match(r"^---\n(.*?)\n---", src, re.S)
check("2 frontmatter 三件齐（name / description / whenToUse）",
      bool(fm) and all(re.search(r"^%s:" % k, fm.group(1), re.M)
                       for k in ("name", "description", "whenToUse")), True)
check("3 name = ponytail", bool(fm) and bool(re.search(r"^name:\s*ponytail\s*$", fm.group(1), re.M)), True)

# ---- 规则本体：七级阶梯 + 两条最容易被"顺手优化"掉的纪律 ----
check("4 七级阶梯在", "七级阶梯" in src, True)
check("5 修 bug = 修根因（grep 所有调用方）", "根因" in src and "调用方" in src, True)
check("6 刻意偷懒留 ponytail: 注释", "ponytail:" in src, True)
check("7 非平凡逻辑留可跑的最小检查", "可跑的最小检查" in src, True)

# ---- 豁免清单：条款 + 对象一个都不能少 ----
check("8 有豁免清单章节", "豁免清单" in src, True)
for kw in ("productions/", "LOOP.md", "docs/", "check-*.py", "SKILL.md", "溯源", "intent.json"):
    check("9 豁免清单含 %s" % kw, kw in src, True)

# ---- 不适用范围：创作面/提示词面必须显式退场 ----
check("10 有不适用章节", "不适用" in src, True)
check("11 提示词工程显式退场", "提示词" in src, True)
check("12 创作交付显式退场", "创作" in src, True)

# ---- 豁免对象真在盘上（防止条款写了、东西却被清了）----
check("13 LOOP.md（状态机中枢）在盘上",
      os.path.isfile(os.path.join(ROOT, "productions", "_template", "LOOP.md")), True)
check("14 docs 合同层在盘上",
      os.path.isfile(os.path.join(ROOT, "docs", "PRD-weeksir-platform.md")), True)
check("15 工作区宪法 AGENTS.md 在盘上",
      os.path.isfile(os.path.join(ROOT, "AGENTS.md")), True)
gates = [f for f in os.listdir(os.path.join(ROOT, "scripts")) if re.match(r"check-.+\.py$", f)]
check("16 回归闸一个没少（>=5 条）", len(gates) >= 5, True)

print("-" * 60)
if all(out):
    print("全部通过 %d 条 —— ponytail 边界完好。" % len(out))
    sys.exit(0)
print("【失败】共 %d 条，失败 %d 条 —— 豁免边界被动过，先修再交付。" % (len(out), out.count(False)))
sys.exit(1)
