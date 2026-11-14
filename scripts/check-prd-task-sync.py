#!/usr/bin/env python
"""PRD ↔ 任务清单的匹配度闸（2026-10-05 自建，现覆盖三份 PRD：delivery-loop / chat-progress / composer-entry）。

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

# 每对 = (PRD, JSON, 验收条目前缀, 前缀后的序号列表, 边界比法)
#   边界比法 "exact" = PRD 非目标与 JSON hard_boundaries 必须逐条同序同数；
#            "subset" = PRD 那份是 JSON 的【子集】（JSON 还含实现级边界，条数本来就不同）。
#            2026-10-09 审核抓到 input-form 那对写的是"逐字同步"却 10 vs 18 条 —— 与其把假话删掉，
#            不如把判据改成能真的：子集关系照样钉得住（PRD 冒出一条 JSON 没有的边界 = 红）。
PAIRS = [
    ("docs/PRD-delivery-loop.md", "docs/tasks-delivery-loop.json", "V", [1, 2, 3, 4, 5, 6, 7], "exact"),
    ("docs/PRD-chat-progress.md", "docs/tasks-chat-progress.json", "W", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], "exact"),
    ("docs/PRD-composer-entry.md", "docs/tasks-composer-entry.json", "X", [1, 2, 3, 4], "exact"),
    ("docs/PRD-input-form-single-port.md", "docs/tasks-input-form-single-port.json", "F", [1, 2, 3, 4, 5, 6], "subset"),
]
out = []


def check(name, got, want):
    ok = json.dumps(got, ensure_ascii=False, default=str) == json.dumps(want, ensure_ascii=False, default=str)
    out.append((ok, name))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else " => %r (期望 %r)" % (got, want)))


def norm(s):
    """抹掉 markdown 强调、编号与中英文标点再比内容（允许只差符号，不允许只差字）。"""
    return re.sub(r"[\s*`（）()「」【】\"'—\-。，、；：！？!?:;]+", "", s).strip()


def norm2(s):
    """norm + 剥掉【强调性符号】(🔴⚠️✅🟢)。它们是"这条多严重"的记号，不是内容：
       一份文档给某条加了 🔴 而另一份没加，不该被判成"PRD 冒出一条 JSON 没有的规则"。"""
    return norm("".join(ch for ch in s if ch not in "🔴⚠️✅🟢"))


for prdRel, jsnRel, pre, nums, bmode in PAIRS:
    prd = (ROOT / prdRel).read_text(encoding="utf-8")
    # S0 🔴 JSON 语法错必须【吃成一条红】，不许抛异常把整闸打死（2026-10-09 我连漏两个逗号，
    #    闸是"非零退出"了，但打印的是 Python traceback —— 看起来像闸坏了，不像清单写错了）。
    try:
        d = json.loads((ROOT / jsnRel).read_text(encoding="utf-8"))
        parse_err = ""
    except ValueError as e:
        d, parse_err = {}, str(e)[:160]
    tag0 = prdRel.split("/")[-1].replace(".md", "")
    check(tag0 + " S0 任务清单是合法 JSON（语法错=闸先说人话，别抛 traceback）", parse_err, "")
    if parse_err:
        continue
    tasks, ids = d["tasks"], [t["id"] for t in d["tasks"]]
    tag = prdRel.split("/")[-1].replace(".md", "")
    print("\n---- " + tag)

    # 0) 控制字符：heredoc 会把路径里的   吃成控制字符（2026-10-05 审核抓到；
    #    JSON 里存成  肉眼看不见，所以要解析后按字符判 —— 这里刻意不用反斜杠字面量）
    CTRL = set(chr(c) for c in list(range(0, 9)) + [11, 12] + list(range(14, 32)) + [127])
    ctrl = []

    def _walk(x, path):
        if isinstance(x, dict):
            for k, v in x.items():
                _walk(v, path + "/" + str(k))
        elif isinstance(x, list):
            for i, v in enumerate(x):
                _walk(v, path + "[%d]" % i)
        elif isinstance(x, str):
            for ch in x:
                if ch in CTRL:
                    ctrl.append(path + ":" + hex(ord(ch)))

    _walk(d, "")
    check(tag + " C0 JSON 里零控制字符（路径一律写正斜杠，防 heredoc 吃字符）", ctrl, [])

    # C0b 🔴 同一把扫法要覆盖 PRD 正文：2026-10-09 实测 PRD §6.5 里 `D:/nzdh` 被写成
    #       `D:` + 换行 + `zdh`（\n 被吃掉）、另一处 `form:` 字样整段丢失 —— 老闸只扫 JSON，
    #       所以这类缺陷在 markdown 里一直是没人管的。控制字符 + "断掉的 Windows 路径"两种形状都扫。
    prd_ctrl = [hex(ord(c)) for c in set(prd) if (ord(c) < 9 or (10 < ord(c) < 32) or ord(c) == 127)]
    broken = [m.group(0).replace("\n", "\\n") for m in re.finditer(r"[A-Za-z]:[^\n]{0,3}\n[a-z]{1,8}\b", prd)]
    check(tag + " C0b PRD 正文零控制字符、无被吃成换行的 Windows 路径", [prd_ctrl, broken], [[], []])

    # 结构自洽
    # 🔴 S1 原本只读 depends_on，而 input-form 那份用的是 depends ⇒ 那条断言一直在【空跑】
    #    （空跑=恒绿的假保险）。两种键名都读，且写清单的人必须让依赖真的存在。
    def deps_of(t):
        return list(t.get("depends_on") or []) + list(t.get("depends") or [])
    check(tag + " S1 depends/depends_on 都指到真实任务",
          [x for t in tasks for x in deps_of(t) if x not in ids], [])
    check(tag + " S2 里程碑里的任务 id 都存在",
          [x for ms, arr in d["milestones"].items() for x in arr if x not in ids], [])
    check(tag + " S3 done* 之外的任务都排进了里程碑",
          sorted([i for i in ids if not any(i in a for a in d["milestones"].values())
                  and not next(t for t in tasks if t["id"] == i)["status"].startswith("done")]), [])
    check(tag + " S4 每个任务都有 verify（全局层 §4：动手前先写验收）", [t["id"] for t in tasks if not t.get("verify")], [])
    check(tag + " S5 JSON 指向的 PRD 就是这份", d["prd"], prdRel)

    # 边界一致性（只取「非目标」段到下一个 ## 之间）
    sec = re.search(r"\*\*非目标.*?\n(.*?)\n## ", prd, re.S)
    prdB = [norm(m.group(1)) for m in re.finditer(r"^\d+\. (.+)$", sec.group(1) if sec else "", re.M)]
    jsnB = [norm(x) for x in d["hard_boundaries"]]
    if bmode == "exact":
        check(tag + " B1 两边边界条数相同", [len(prdB), len(jsnB)], [len(jsnB), len(jsnB)])
        check(tag + " B2 边界内容逐条一致（漂移=按 JSON 干出 PRD 禁止的事）",
              [x for a, b in zip(prdB, jsnB) if a != b for x in [(a[:20], b[:20])]], [])
    else:
        # subset 模式：PRD 那份是 JSON 的子集 ⇒ 判【PRD 不许冒出一条 JSON 里没有的规则】，
        # 条数不同不算红。取每条开头 14 字（抹标点与 🔴 后）当主题指纹：
        # 🔴 这比"逐字相等"弱，但弱得【有向】—— 只放过措辞差异，不放过"PRD 凭空多一条禁令"。
        p2 = [norm2(x) for x in prdB]
        j2 = [norm2(x) for x in jsnB]
        orphan = [a[:14] for a in p2 if not any(a[:14] in b or b[:14] in a for b in j2)]
        check(tag + " B1s PRD 每条非目标都能在 JSON hard_boundaries 找到同源条目（无孤儿=按 JSON 干活不会干出 PRD 禁止的事）",
              orphan, [])
        check(tag + " B2s JSON 边界数 ≥ PRD 非目标数（只许 JSON 更严，不许 PRD 更严）",
              len(jsnB) >= len(prdB), True)

    # 验收条目互指
    found = sorted({int(n) for n in re.findall(r"\b" + pre + r"(\d+)\b", prd)})
    check(tag + " C1 PRD 验收条目 = " + pre + "1.." + pre + str(max(nums)), found, nums)
    # 🔴 verify 既可能是字符串也可能是数组（input-form 那份是字符串）。原写法
    #    " ".join(v for t in tasks for v in t.get("verify", [])) 在字符串上会【逐字符】迭代再拼空格
    #    ⇒ "F1" 变成 "F 1"，C2 恒红 —— 假红比假绿更难查（它让人以为"引用没写"）。
    allv = " ".join(s for t in tasks
                    for s in ([t["verify"]] if isinstance(t.get("verify"), str)
                              else [str(x) for x in (t.get("verify") or [])]))
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
