#!/usr/bin/env python
"""PRD ↔ 任务清单的匹配度闸（2026-10-05 自查时建，现覆盖两份 PRD）。

为什么钉这条：PRD 和任务清单是人写的两份文档，一旦漂移，"按 JSON 干活"就会做出 PRD 禁止的事。
第一次自查抓出 6 处不一致（边界条数不同、实测任务被 P1 挡住、任务在 PRD 里没归属、
"8100 读写口"用词与实现相反、验收条目没标归属、PRD 没标闸名）。

只读、不改文件。跑法：python scripts/check-prd-task-sync.py
"""
import io
import json
import pathlib
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = pathlib.Path(__file__).resolve().parents[1]

# 每对 = (PRD, JSON, 验收条目前缀, 前缀后的序号列表)
PAIRS = [
    ("docs/PRD-delivery-loop.md", "docs/tasks-delivery-loop.json", "V", [1, 2, 3, 4, 5, 6, 7]),
    ("docs/PRD-chat-progress.md", "docs/tasks-chat-progress.json", "W", [1, 2, 3, 4, 5, 6, 7, 8]),
]
out = []


def check(name, got, want):
    ok = json.dumps(got, ensure_ascii=False, default=str) == json.dumps(want, ensure_ascii=False, default=str)
    out.append((ok, name))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else " => %r (期望 %r)" % (got, want)))


def norm(s):
    """抹掉 markdown 强调、编号与中英文标点再比内容（允许只差符号，不允许只差字）。"""
    return re.sub(r"[\s*`（）()「」【】\"'—\-。，、；：！？!?:;]+", "", s).strip()


for prdRel, jsnRel, pre, nums in PAIRS:
    prd = (ROOT / prdRel).read_text(encoding="utf-8")
    d = json.loads((ROOT / jsnRel).read_text(encoding="utf-8"))
    tasks, ids = d["tasks"], [t["id"] for t in d["tasks"]]
    tag = prdRel.split("/")[-1].replace(".md", "")
    print("\n---- " + tag)

    # 结构自洽
    check(tag + " S1 depends_on 都指到真实任务",
          [x for t in tasks for x in t.get("depends_on", []) if x not in ids], [])
    check(tag + " S2 里程碑里的任务 id 都存在",
          [x for ms, arr in d["milestones"].items() for x in arr if x not in ids], [])
    check(tag + " S3 非 done 的任务都排进了里程碑",
          sorted([i for i in ids if not any(i in a for a in d["milestones"].values())
                  and next(t for t in tasks if t["id"] == i)["status"] not in ("done", "done-code-unverified")]), [])
    check(tag + " S4 每个任务都有 verify（全局层 §4：动手前先写验收）", [t["id"] for t in tasks if not t.get("verify")], [])
    check(tag + " S5 JSON 指向的 PRD 就是这份", d["prd"], prdRel)

    # 边界逐字一致（只取「非目标」段到下一个 ## 之间）
    sec = re.search(r"\*\*非目标.*?\n(.*?)\n## ", prd, re.S)
    prdB = [norm(m.group(1)) for m in re.finditer(r"^\d+\. (.+)$", sec.group(1) if sec else "", re.M)]
    jsnB = [norm(x) for x in d["hard_boundaries"]]
    check(tag + " B1 两边边界条数相同", [len(prdB), len(jsnB)], [len(jsnB), len(jsnB)])
    check(tag + " B2 边界内容逐条一致（漂移=按 JSON 干出 PRD 禁止的事）",
          [x for a, b in zip(prdB, jsnB) if a != b for x in [(a[:20], b[:20])]], [])

    # 验收条目互指
    found = sorted({int(n) for n in re.findall(r"\b" + pre + r"(\d)\b", prd)})
    check(tag + " C1 PRD 验收条目 = " + pre + "1.." + pre + str(max(nums)), found, nums)
    allv = " ".join(v for t in tasks for v in t.get("verify", []))
    check(tag + " C2 每条 " + pre + " 都被某任务的 verify 引用",
          [pre + str(n) for n in nums if pre + str(n) not in allv], [])

    # 编号互指（不许有孤儿）
    prdT = sorted({int(n) for n in re.findall(r"\bT(\d+)\b", prd)})
    check(tag + " D1 PRD 引用的任务编号都存在", ["T%d" % n for n in prdT if "T%d" % n not in ids], [])
    check(tag + " D2 JSON 任务都在 PRD 出现过（没归属=没人知道为什么做）",
          [i for i in ids if i not in prd], [])

# 交付闭环专属：用词与 mock 纪律
prd1 = (ROOT / PAIRS[0][0]).read_text(encoding="utf-8")
d1 = json.loads((ROOT / PAIRS[0][1]).read_text(encoding="utf-8"))
check("E1 PRD 不许再写「8100 读写口」（写由提交方直接落文件，8100 只开读口）", "8100 读写口" in prd1, False)
check("E2 mock 纪律两边都在（只换传输层，别整块替换被测函数）",
      ["不许把被测函数整块替掉" in prd1, "整块替掉" in json.dumps(d1, ensure_ascii=False)], [True, True])

bad = [n for ok, n in out if not ok]
print("\n=== PRD↔任务清单匹配度闸：%d 条断言，失败 %d ===" % (len(out), len(bad)))
print("改任何一边都要同步；跑不过就别开工。")
sys.exit(1 if bad else 0)
