#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""技能目录写锁闸（2026-10-10 用户判："只允许 Qoder 侧改 skill，禁止 agent 私自修改"）。

    python scripts/check-skill-write-lock.py            # 核对
    python scripts/check-skill-write-lock.py --bless    # 把当前状态认成基线（只有人/Qoder 改完才跑这条）

钉三件事：
  1) 指纹漂移：每档 SKILL.md 与它的 form:/contract: 段各有 sha8。漂了就必须【带着提交号】才算授权过，
     否则红 —— 这条是"该发生却没发生也能红"的那类判据（不靠任何人自觉，也不靠谁汇报）。
  2) 物理锁状态：真正拦得住 DSH agent 的只有一件事 —— skills 真身在工作区外，原地是 junction
     （内核 dsh-fs-sandbox:153 checkedTarget 当场 realpath 并要求落在可写根内）。
     🔴 ACL 分不开：Qoder 和 DSH agent 在这台机上是同一个 Administrator，拒它就是拒我自己。
     没装成之前，这一条【恒红】，是待办，不是装饰。
  3) 覆盖对照：8100 线上读到的档集合，必须等于闸枚举的集合（闸绿而线上读不到 = 假安全）。
"""
import ctypes
import glob
import hashlib
import io
import json
import os
import re
import subprocess
import sys

WS = r"C:\dsh"
LIVE = os.path.join(WS, ".dsh", "skills")
BASE = os.path.join(WS, ".dsh", "skill_lock.json")
REAL_HINT = r"D:\weeksir-skills"          # 装物理锁后 junction 该指到这里
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
PASS, FAIL, NOTES = [], [], []


def cprint(s):
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        sys.stdout.write(s + "\n")
    except UnicodeEncodeError:
        sys.stdout.buffer.write((s + "\n").encode(enc, "replace"))


def sha8(text):
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:8]


def read(p):
    try:
        return io.open(p, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


# 🔴 指纹一律用【服务端那两个解析器】算，闸里不再自己抠段（自己写一遍 = 第二份判据，
#    而这次 L6 就是被这条抓到：闸算出空集、线上有 3 档）。
sys.path.insert(0, r"D:\video-skill")
try:
    from backend import model_admin as MA                 # noqa: E402
    from backend import skill_contract as SC              # noqa: E402
    PARSER_ERR = ""
except Exception as e:                                    # noqa: BLE001
    MA = SC = None                       # 不许崩：闸崩 = 等于没跑，让它变成一条明写的红
    PARSER_ERR = "%s: %s" % (type(e).__name__, str(e)[:90])


def lanes(root):
    out = {}
    for d in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        p = os.path.join(root, d, "SKILL.md")
        if not os.path.isfile(p):
            continue
        src = read(p)
        row = {"sha8": sha8(src), "form8": "", "contract8": "", "bytes": os.stat(p).st_size}
        if MA is not None and SC is not None:
            row["form8"] = sha8(json.dumps(MA.parse_skill_form(src) or [], ensure_ascii=False, sort_keys=True))
            row["contract8"] = sha8(json.dumps(SC.parse_contract(src) or {}, ensure_ascii=False, sort_keys=True))
        out[d] = row
    return out


def last_commit(rel_path):
    """返回最近一次动过这个路径的提交号；空串 = 从没提交过（= 改动没进过版本控制）。"""
    try:
        r = subprocess.run(["git", "-C", WS, "log", "-1", "--format=%h", "--", rel_path],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        return (r.stdout or "").strip()
    except Exception:                                       # noqa: BLE001
        return ""


def is_reparse(p):
    try:
        return bool(ctypes.windll.kernel32.GetFileAttributesW(p) & FILE_ATTRIBUTE_REPARSE_POINT)
    except Exception:                                       # noqa: BLE001
        return False


def check(name, got, want, note=""):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    cprint("%-4s %-58s got=%r want=%r" % ("PASS" if ok else "FAIL", name[:58], got, want))
    if note:
        NOTES.append(note)


def decide(rows, base):
    """纯函数：把两份指纹表算成【漂移清单】。抽出来跑值表 —— 判据不许长在循环里。"""
    drift, added, gone = [], [], []
    for k, v in rows.items():
        b = base.get(k)
        if b is None:
            added.append(k)
        elif any(b.get(f) != v.get(f) for f in ("sha8", "form8", "contract8")):
            drift.append(k)
    for k in base:
        if k not in rows:
            gone.append(k)
    return drift, added, gone


def main(argv):
    bless = "--bless" in argv
    rows = lanes(LIVE)
    check("L0 指纹用服务端那两个解析器（拿到空档 = 闸在自己算，那条判据就废了）",
          PARSER_ERR or all(v["form8"] or v["contract8"] for v in rows.values()), True)
    check("L1 枚举到技能档（空集会让后面每一条都假绿）", len(rows) >= 8, True)

    if bless:
        doc = {"note": "技能目录指纹基线。只有人/Qoder 侧改完才重新 --bless。",
               "lanes": rows, "blessed_by": "check-skill-write-lock --bless"}
        os.makedirs(os.path.dirname(BASE), exist_ok=True)
        with open(BASE, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        cprint("已认基线：%d 档 → %s" % (len(rows), BASE))
        return 0

    base_doc = {}
    try:
        base_doc = json.load(open(BASE, encoding="utf-8"))
    except (OSError, ValueError):
        base_doc = {}
    base = base_doc.get("lanes") or {}
    check("L2 基线存在且非空（没基线时闸不能报绿）", bool(base), True)

    drift, added, gone = decide(rows, base)
    # 反向对照：同一套判据喂给"什么都不漂"必须全空，喂给造出来的漂移必须抓到（否则 L3 是恒绿的壳）
    check("L3a 对照：拿自己跟自己比 ⇒ 漂移/新增/删除全空",
          decide(rows, rows), ([], [], []))
    fake = json.loads(json.dumps(rows))
    hit = sorted(fake)[0]
    last = sorted(fake)[-1]
    fake[hit]["contract8"] = "deadbeef"
    check("L3b 对照：把某一档的合同指纹改一个字符 ⇒ 判据必须点到那一档",
          decide(rows, fake), ([hit], [], []))
    fewer = dict(rows)
    fewer.pop(last)
    check("L3c 对照：整档被删 ⇒ 判据必须抓到（本次实测 hypit 就是这么没的）",
          decide(fewer, rows), ([], [], [last]))
    more = json.loads(json.dumps(rows))
    more["brand-new-lane"] = more[hit]
    check("L3d 对照：凭空多出一档 ⇒ 抓到 added（新增档同样要过人）",
          decide(more, rows), ([], ["brand-new-lane"], []))
    check("L4 无未授权漂移：SKILL.md / form: / contract: 三处指纹都对得上基线",
          [drift, added, gone], [[], [], []])
    for k in drift:
        rel = ".dsh/skills/%s/SKILL.md" % k
        c = last_commit(rel)
        NOTES.append("漂移：%s（最近提交=%s）%s" % (k, c or "无", "" if c else "⇒ 从没进过版本控制"))
    for k in added:
        NOTES.append("基线里没有这档（新增）：%s" % k)
    for k in gone:
        NOTES.append("基线里有、盘上没了（被删）：%s ⇒ 整档消失不会有任何日志，只有这条看得见" % k)

    # ── 物理锁：唯一能区分"Qoder 可写 / DSH agent 不可写"的形态 ─────────────────────
    reparse = is_reparse(LIVE)
    resolved = os.path.realpath(LIVE)
    inside = resolved.lower().startswith(WS.lower() + os.sep)
    check("L5 🔴 物理锁已装：`.dsh/skills` 是 reparse point，且解析后【在工作区外】"
          "（内核 dsh-fs-sandbox:153 当场 realpath + 可写根containment ⇒ 这是唯一能区分两个写者的判据；"
          "ACL 分不开——同一个 Administrator）", [reparse, inside], [True, False])
    if not reparse:
        NOTES.append("物理锁未装（这条会长期红 = 待办，不是装饰）：要做的是把真身移到 %s，"
                     "原地 `mklink /J` 指过去；代价=skills 脱离本仓 git（要另立仓或纳入发版层 weeksir-skills）。"
                     % REAL_HINT)

    # ── 覆盖对照：闸枚举的 == 8100 线上读到的（少读一份就是假绿）─────────────────────
    if MA is not None:
        online = set((MA.skill_forms_view().get("forms") or {}))
        gate_has = {k for k in rows if MA.parse_skill_form(read(os.path.join(LIVE, k, "SKILL.md")))}
        check("L6 覆盖对照：闸用【服务端那份解析器】枚举的带表单档 == 8100 真读到的档",
              [sorted(gate_has), sorted(online), gate_has == online],
              [sorted(online), sorted(online), True])
    else:
        check("L6 覆盖对照：解析器不可用，无法核对", "skipped", "run")

    # ── 规则文本与判据不许两处漂 ──────────────────────────────────────────────────
    ag = read(os.path.join(WS, "AGENTS.md"))
    check("L7 工作区规则里有这条禁令（散文不是判据，但判据要有出处）",
          bool(re.search(r"只允许 Qoder|禁止.{0,8}agent.{0,8}(私自)?改.{0,6}skill", ag, re.I)), True)
    check("L8 基线只有一份（第二份迟早漂）", [BASE.endswith("skill_lock.json"),
                                          len(glob.glob(os.path.join(WS, ".dsh", "skill_lock*.json")))],
          [True, 1])
    # 🔴 L9 = 这次事故的真相：@4 是别的会话改的、而且 .dsh/skills 整棵树【从没被提交过】
    #    （实测：近 3 天 0 次提交，工作区未入库 = 31 新增 / 73 删除 / 10 修改）。
    #    不在版本控制里的判据 = 谁都能改、改完查不到、也没人能回退 ⇒ 这条恒红就是待办。
    try:
        r = subprocess.run(["git", "-C", WS, "status", "--porcelain=v1", "--", ".dsh/skills"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40)
        dirty = [x for x in (r.stdout or "").splitlines() if x.strip()]
    except Exception as e:                                    # noqa: BLE001
        dirty = ["读 git 失败 " + str(e)[:60]]
    kinds = {}
    for x in dirty:
        kinds[x[:2].strip() or "?"] = kinds.get(x[:2].strip() or "?", 0) + 1
    check("L9 🔴 skills 全部已入库：`.dsh/skills` 工作区必须干净（未入库的判据=改完查不到也没法回退）",
          [len(dirty), kinds], [0, {}])

    n = len(PASS) + len(FAIL)
    cprint("\n=== 技能写锁闸：%d/%d 通过 ===" % (len(PASS), n))
    for f in FAIL:
        cprint("   未通过：" + f)
    for m in NOTES:
        cprint("   · " + m)
    cprint("   命令：改完 skill 要重新认基线 python scripts/check-skill-write-lock.py --bless")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
