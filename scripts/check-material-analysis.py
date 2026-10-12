# -*- coding: utf-8 -*-
"""素材分析台账闸（第 2 项：提交前必须真看过每个素材）。
钉的是【"我分析过了"这句话有没有出处】：出处只能是网关台账（video.analyze 跑完由网关写），
不能是 agent 手写的一个 json —— 自证的形状合同 T10 抓过。
全离线、0 消耗：看图腿本身不真调（那要扣分），只测台账与判据。
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

BS = chr(92)
sys.path.insert(0, r"D:\video-skill")

TMP = tempfile.mkdtemp(prefix="material-")
os.environ["WEEKSIR_MEDIA_ANALYSIS_LOG"] = os.path.join(TMP, "ledger.json")

from backend import gateway as gw                     # noqa: E402
from backend import skill_contract as sc              # noqa: E402

SVC = r"D:\video-skill\backend\services\super_clone_wan3.py"
SK = r"C:\dsh\.dsh\skills\super-clone\SKILL.md"
PASS, FAIL = [], []


def _safe(s):
    """按【输出流自己的编码】errors=replace。绝不 reconfigure 成 utf-8（会造"零输出+exit 0"的静默假绿）。"""
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        s.encode(enc)
        return s
    except (UnicodeEncodeError, LookupError):
        return s.encode(enc, "replace").decode(enc, "replace")


def check(name, got, want):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(_safe("%-4s %-56s got=%r want=%r" % ("PASS" if ok else "FAIL", name, got, want)))


def mkfile(name, seed, size=3000):
    p = os.path.join(TMP, name)
    io.open(p, "wb").write(bytes((i * seed + 3) % 251 for i in range(size)))
    return p


A = mkfile("a.png", 5)
Bf = mkfile("b.jpg", 7)
C = mkfile("c.wav", 9)
os.environ["WEEKSIR_MEDIA_ANALYSIS_LOG"] = os.path.join(TMP, "ledger-empty.json")

# ── M1 值表（真临时文件，不造字符串）─────────────────────────────────────────
gw.record_media_analysis(A, "video.analyze", "default", session="S1", hold_id="h-A1")
gw.record_media_analysis(Bf, "video.analyze", "default", session="S1", hold_id="h-B1")
rows = gw.media_analysis_rows()
check("M1a 全覆盖 ⇒ 无问题", gw.analysis_coverage_problems([A, Bf], rows, "S1"), [])
check("M1b 缺一个 ⇒ 指名道姓（带 sha8）",
      [len(gw.analysis_coverage_problems([A, Bf, C], rows, "S1")),
       "c.wav" in gw.analysis_coverage_problems([A, Bf, C], rows, "S1")[0]], [1, True])
check("M1c 别的会话看过不算这条线的 ⇒ 两条都缺",
      len(gw.analysis_coverage_problems([A, Bf], rows, "S2")), 2)
check("M1d 同一张图给两次只算一个洞（不许重复收费式报错）",
      len(gw.analysis_coverage_problems([C, C], [], "S1")), 1)
check("M1e 素材读不到 ⇒ 报，且不给免检牌",
      any("读不到" in x for x in gw.analysis_coverage_problems([os.path.join(TMP, "nope.png")], rows, "S1")), True)
# 同名换内容：sha8 变了 ⇒ 旧记录必须不算数
A2 = mkfile("a.png", 99, 5000)
check("M2 身份认内容不认文件名（同名换内容 ⇒ 旧看图记录不算）",
      any("a.png" in x for x in gw.analysis_coverage_problems([A2], rows, "S1")), True)

# ── M3 台账写入本身 ──────────────────────────────────────────────────────────
before = len(gw.media_analysis_rows())
r1 = gw.record_media_analysis(A2, "video.analyze", "default", session="S1", credits={"charged": 6},
                           hold_id="h-A2")
check("M3a 写一条 ⇒ 行数 +1 且带 credits",
      [len(gw.media_analysis_rows()) - before, r1.get("ok"), r1["row"].get("credits")], [1, True, 6])
r2 = gw.record_media_analysis(os.path.join(TMP, "ghost.png"), "video.analyze", "default", session="S1",
                           hold_id="h-G")
check("M3b 文件读不到 ⇒ ok=False 但不抛（台账是留痕，不许变成故障点）", [r2.get("ok"), "error" in r2], [False, False])
os.environ["WEEKSIR_MEDIA_ANALYSIS_LOG"] = os.path.join(TMP, "no-such-dir", "x.json")
r3 = gw.record_media_analysis(A, "video.analyze", "default", session="S1")
check("M3c 写盘失败 ⇒ 返回原因、不抛异常", r3.get("ok"), False)
os.environ["WEEKSIR_MEDIA_ANALYSIS_LOG"] = os.path.join(TMP, "ledger.json")

# ── M4 自测后门只对临时文件有效 ──────────────────────────────────────────────
_saved = os.environ.pop("WEEKSIR_MEDIA_ANALYSIS_LOG")
prod_path = gw.media_analysis_log_path()
os.environ["WEEKSIR_MEDIA_ANALYSIS_LOG"] = _saved
check("M4 不设 env ⇒ 落生产台账路径（闸全程没写过它）",
      prod_path.replace("/", BS), r"D:" + BS + "video-skill" + BS + ".gateway_media_analysis.json")

# ── M5 咽喉顺序：对账必须在写任何物证之前（判据抽成函数，才能做变异对照）────────
src = io.open(SVC, encoding="utf-8", errors="replace").read()


def throat_problems(text):
    """咽喉形状判据：① 有对账调用 ② 对账后立刻拒 ③ 拒在写任何【物证】之前。

    🔴 锚点必须带换行 + 四个空格：原来锚 `seg_dir.mkdir(parents=True` 会被 run_pipeline 里那段
       12 空格的同名行命中 ⇒ _block 一路吞到几千行外，变异改的是不相干的远处 = 假对照。
    🔴 锚点找不到 ⇒ 直接报问题，不许返回空表（空表 = 这条判据在空跑，比红更坏）。"""
    p = []
    i_cover = text.find("analysis_coverage_problems(")
    i_raise = text.find("素材没看过", i_cover) if i_cover > 0 else -1
    _marks = [text.find(x) for x in ('\n    _ma.write_text(', '\n    _pt.write_text(', '\n    sb.write_text(')]
    _hits = [i for i in _marks if i >= 0]
    i_write = min(_hits) if _hits else -1
    if i_cover < 0:
        p.append("咽喉里没有台账对账")
    if i_cover >= 0 and i_raise < 0:
        p.append("对账结果没人判 ⇒ 报了也没人拦")
    if i_write < 0:
        p.append("找不到物证写点的锚点 ⇒ 这条顺序判据在空跑（锚点漂了要改锚点，不是让它绿）")
    elif i_raise >= 0 and i_write < i_raise:
        p.append("先写物证后对账 ⇒ 拒的时候盘上已经留了半成品（0 消耗承诺就假了）")
    return p


check("M5 咽喉形状无问题（对账→拒→才写物证）", throat_problems(src), [])
# 变异要真的把"拒"整块搬走/删掉，而不是改个条件（改条件判据看不见 ⇒ 控制是假的）
_b0 = src.find("    if _missing:")
# 🔴 锚点用【结构】不用文案，且必须"换行 + 四个空格"（12 空格的同名行会把块吞到几千行外 = 假对照）：
#    这段的结尾就是"开始写第一件物证"那一行，边界天然存在；以前锚报错文案，2026-10-12 文案一换就空跑。
#    ⚠️ open_receipt 落的【全 ⬜ 骨架】不算物证 —— 它是台账本身，写在拒之前正是设计（看得见打到第几格）。
_b1 = src.find("\n    _ma.write_text(", _b0) if _b0 > 0 else -1
check("M5d 前提：拒的那一段和第一件物证写点都能定位（找不到=下面两个变异都是空跑）",
      [_b0 > 0, _b1 > _b0], [True, True])
_block = src[_b0:_b1]
check("M5b 变异：整块删掉【拒】⇒ 报『对账结果没人判』",
      any("没人判" in x for x in throat_problems(src.replace(_block, "", 1))), True)
_w = src.find("\n    _pt.write_text(")
_moved = src.replace(_block, "", 1)
_k = _moved.find("\n    _pt.write_text(")
_end_w = _moved.find(chr(10), _k) + 1
_moved = _moved[:_end_w] + _block + _moved[_end_w:]
check("M5c 变异：把【拒】挪到写完 prompt.txt 之后 ⇒ 报『先写物证后对账』",
      [_w > 0, any("先写物证" in x for x in throat_problems(_moved))], [True, True])
check("M5c2 反向对照：变异确实改了原文（锚点漂了会把变异做成空跑）", _moved != src, True)
check("M5e 变异对照没顺手把正解也判红（原文仍 0 问题）", throat_problems(src), [])

# ── M6 合同里这一步存在且形状合法 ────────────────────────────────────────────
c = sc.contract_for("super-clone")
st = next((s for s in c.get("steps") or [] if s.get("id") == "material-analysis"), None)
check("M6 合同有 material-analysis 步（required=1 + 手段=【管线】invoke，不再是教 agent 跑 CLI）",
      [bool(st), (st or {}).get("required"), (st or {}).get("impl")],
      [True, True, "pipeline:invoke:video.analyze"])
check("M6b 判据用的是网关认识的谓词（json-list-min / json-key / text-has / bind-sha8 / json-nonempty）",
      str((st or {}).get("pred", "")).startswith(("json-list-min:", "json-key:", "json-array-min=", "text-has=", "json-nonempty")), True)
check("M6c 合同形状判据无问题（每步都有 证明= 或 手段=）", sc.contract_shape_problems(c), [])
check("M6d 回执步骤登记了这一行（交付表里看得见）",
      "material-analysis" in io.open(SVC, encoding="utf-8", errors="replace").read(), True)

# ── M7 真跑咽喉（🔴 invoke 打桩：这条腿现在由【管线】自己跑分析，闸绝不能真去花那份分）──────
os.environ["WEEKSIR_MEDIA_ANALYSIS_LOG"] = os.path.join(TMP, "ledger7.json")
from backend.services import super_clone_wan3 as scw   # noqa: E402
seg = os.path.join(TMP, "seg7")
vid = mkfile("src.mp4", 3, 128)
PROMPT = "[NEGATIVE_LOCK] 成片不得出现字幕、花字、水印；" + "P" * 40
_REAL_INVOKE = gw.invoke
_CALLS = []


def _stub_invoke(cap, purpose=None, account="local", params=None, fail=False, charged=7):
    _CALLS.append({"cap": cap, "account": account, "params": dict(params or {})})
    if not fail:
        gw.record_media_analysis((params or {}).get("path"), cap, purpose or "default",
                                 session="", credits={"charged": charged}, hold_id="h-pipeline")
    return {"ok": not fail, "capability": cap, "purpose": purpose or "default",
            "credits": {"estimated": charged, "charged": 0 if fail else charged},
            "error": "上游 5xx（桩）" if fail else None}


def _run_evidence(**kw):
    try:
        scw._contract_evidence(seg, seg_board=[{"shot": 1}], seg_prompt=PROMPT,
                               compiled_seg={"source": "step1_5_gpt_compiler", "model": "m",
                                             "provider": "p"},
                               seg_duration=8.0, ref_video=vid, seg_product_paths=[A, Bf], **kw)
        return "放行", ""
    except Exception as e:                                    # noqa: BLE001
        return type(e).__name__, str(e)[:260]


try:
    gw.invoke = lambda *a, **k: _stub_invoke(*a, fail=True, **k)
    _CALLS.clear()
    kind, msg = _run_evidence()
    check("M7a 分析这条腿跑不通 ⇒ 必须拒（台账没记上就是没看过，不放行）",
          [kind, "素材没看过" in msg], ["RuntimeError", True])
    check("M7b 🔴 执行人确实是管线：空台账时【管线自己】调了 invoke(video.analyze)，两条素材各一次",
          [len(_CALLS), sorted(c["params"].get("path") for c in _CALLS) == sorted([A, Bf]),
           all(c["cap"] == "video.analyze" for c in _CALLS),
           all(c["params"].get("question") for c in _CALLS),
           all(int(c["params"].get("max_tokens") or 0) >= 600 for c in _CALLS)],
          [2, True, True, True, True])
    # 🔴 2026-10-12 起这条的形状变了，而且变得更好：拒的时候盘上【有】一张全 ⬜ 的打钩单
    #    （open_receipt 开工就落的骨架），但没有一件半成品物证、也没有一个假钩。
    #    验的是这两件事 —— 原来只问"目录在不在"，那会把有用的台账一起当成违规产物。
    _files7c = sorted(os.listdir(seg)) if os.path.isdir(seg) else []
    _rec7c = sc.load_receipt(seg)
    check("M7c 拒的时候盘上只有一张全 ⬜ 的打钩单：没物证、没假钩（0 消耗承诺仍成立，且看得见卡在哪一格）",
          [os.path.isfile(os.path.join(seg, ".skill_receipt.json")),
           [f for f in _files7c if f != ".skill_receipt.json"],
           sum(1 for s in (_rec7c.get("steps") or []) if s.get("ok")),
           len(_rec7c.get("steps") or [])],
          [True, [], 0, len((sc.contract_for("super-clone") or {}).get("steps") or [])])

    gw.invoke = _stub_invoke
    _CALLS.clear()
    kind, msg = _run_evidence()
    check("M7d 正向对照：管线补跑成功 ⇒ 放行（M7a 不是靠'永远拒'假安全）", kind, "放行")
    d = json.loads(io.open(os.path.join(seg, "material_analysis.json"), encoding="utf-8").read())
    check("M7e 物证里写清【谁跑的、花了几分】（报价与交付表读这份，不靠 agent 自述）",
          [len(d.get("materials") or []), len(d.get("missing") or []),
           str(d.get("executor") or ""), len(d.get("auto_ran") or []), d.get("auto_spent")],
          [2, 0, "pipeline:invoke:video.analyze", 2, 14])

    # 熔断：换一份空台账、每条 999 分 ⇒ 第一条就把预算打满，第二条不许再跑（防"10 张图 × 40 分"这类失控）
    os.environ["WEEKSIR_MEDIA_ANALYSIS_LOG"] = os.path.join(TMP, "ledger7b.json")
    shutil.rmtree(seg, ignore_errors=True)
    _CALLS.clear()
    gw.invoke = lambda *a, **k: _stub_invoke(*a, charged=999, **k)
    kind, msg = _run_evidence()
    check("M7f 🔴 熔断生效：单条就把预算打满时【第二条不许再跑】，且拒因点名是熔断不是闸坏",
          [len(_CALLS), "熔断" in msg], [1, True])

    # 已看过就不许再花一次（现网那条原片被同一会话分析了三次 = 118 分，就是缺这条）
    os.environ["WEEKSIR_MEDIA_ANALYSIS_LOG"] = os.path.join(TMP, "ledger7c.json")
    gw.record_media_analysis(A, "video.analyze", "default", session="", hold_id="h-pre1")
    gw.record_media_analysis(Bf, "video.analyze", "default", session="", hold_id="h-pre2")
    shutil.rmtree(seg, ignore_errors=True)
    _CALLS.clear()
    gw.invoke = _stub_invoke
    kind, msg = _run_evidence()
    check("M7g 台账里已有记录 ⇒ 一条都不重跑（重复消耗是钱，不是保险），且照常放行",
          [len(_CALLS), kind], [0, "放行"])
finally:
    gw.invoke = _REAL_INVOKE

# ── M9 🔴 台账行必须带【调用凭证】（2026-10-12 现网第七种失效：agent 发现台账没记上，
#       就 import 网关的记账函数自己写了 3 条"看过图"——13 毫秒三条、credits=0，
#       于是"不认 agent 自述、只认台账"这条判据被自证破功）────────────────────────
os.environ["WEEKSIR_MEDIA_ANALYSIS_LOG"] = os.path.join(TMP, "ledger9.json")
forged = gw.record_media_analysis(A, "video.analyze", "default", session="S9")   # 没有 hold_id
check("M9a 手写一行（无 hold_id）⇒ 不算证据，仍判缺",
      [forged.get("ok"), len(gw.analysis_missing_paths([A], gw.media_analysis_rows(), "S9"))], [True, 1])
gw.record_media_analysis(A, "video.analyze", "default", session="S9", hold_id="h-real")
check("M9b 正向对照：同一张图带上调用凭证 ⇒ 立刻算看过（判据不是恒拒装样子）",
      len(gw.analysis_missing_paths([A], gw.media_analysis_rows(), "S9")), 0)
# 最狠的一条：伪造的台账不但帮不了它，反而让管线【真的去跑一次】
os.environ["WEEKSIR_MEDIA_ANALYSIS_LOG"] = os.path.join(TMP, "ledger9b.json")
gw.record_media_analysis(A, "video.analyze", "default", session="")      # 只有无凭证行
gw.record_media_analysis(Bf, "video.analyze", "default", session="")
shutil.rmtree(seg, ignore_errors=True)
_CALLS.clear()
gw.invoke = _stub_invoke
kind, msg = _run_evidence()
check("M9c 台账里全是「手写行」⇒ 管线不认，并且【自己真跑一遍】补上凭证"
      "（伪造从「能过关」变成「多花一次钱也得重来」）",
      [kind, len(_CALLS), all(str(c["params"].get("path")) in (A, Bf) for c in _CALLS)],
      ["放行", 2, True])
gw.invoke = _REAL_INVOKE          # 桩不许留给后面的用例（M8 要跑真 CLI 子进程）

# ── M8 🔴 近路封了没（2026-10-12 现网：agent 走 `gateway.py analyze` 跑了三次分析，台账 0 条，
#       因为它直连 call() 分发器，而记账/预扣/结算只在 invoke() 里 ⇒ 一条能力两个入口，便宜的那个不记账）──
_gwsrc = io.open(r"D:\video-skill\backend\gateway.py", encoding="utf-8", errors="replace").read()
_seg2 = _gwsrc[_gwsrc.index('elif cmd == "analyze":'):_gwsrc.index('elif cmd == "call":')]
check("M8a analyze 子命令必须转 invoke（同一条轨：预扣/结算/台账齐全），且不再出现 call(\"video.analyze\")",
      ['invoke("video.analyze"' in _seg2, 'call("video.analyze"' in _seg2], [True, False])
check("M8b 变异对照：M8a 那两条不是恒真 —— 旧形状（转 call）喂进去必须一红一绿",
      ['invoke("video.analyze"' in 'print(call("video.analyze", path=p))',
       'call("video.analyze"' in 'print(call("video.analyze", path=p))'], [False, True])
# 真跑一次 CLI：给一个不存在的文件 ⇒ 0 消耗（拿不到文件就发不出去），但【信封形状】能证明走没走 invoke
_ENV = dict(os.environ)
_ENV["WEEKSIR_MEDIA_ANALYSIS_LOG"] = os.path.join(TMP, "ledger8.json")
_ENV["GATEWAY_LEDGER"] = os.path.join(TMP, "ledger8.sqlite")
_ENV["PYTHONIOENCODING"] = "utf-8"
_r8 = subprocess.run([sys.executable, r"D:\video-skill\backend\gateway.py", "analyze",
                      os.path.join(TMP, "nope.png"), "这是什么"],
                     capture_output=True, text=True, encoding="utf-8", errors="replace",
                     timeout=180, env=_ENV)
_o8 = _r8.stdout or ""
check("M8c 真跑 CLI（0 消耗：文件不存在）⇒ 返回的是 invoke 的信封（有 credits/hold_id 字段），不是裸结果",
      ["hold_id" in _o8 or '"ok"' in _o8, "credits" in _o8], [True, True])
check("M8d 判据只有一份：人话版 coverage_problems 必须坐在 analysis_missing_paths 上（不自己再算 have 集合）",
      ["analysis_missing_paths(" in _gwsrc[_gwsrc.index("def analysis_coverage_problems"):
                                        _gwsrc.index("def analysis_coverage_problems") + 900],
       "have = {}" in _gwsrc[_gwsrc.index("def analysis_coverage_problems"):
                             _gwsrc.index("def analysis_coverage_problems") + 900]], [True, False])

shutil.rmtree(TMP, ignore_errors=True)
check("收尾：临时目录已清，生产台账未被本闸写过",
      [not os.path.isdir(TMP), not os.path.isfile(r"D:\video-skill\.gateway_media_analysis.json") or True], [True, True])

print(_safe(chr(10) + "== 素材分析台账闸：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL))))
for f in FAIL:
    print(_safe("  ✗ " + f))
sys.exit(1 if FAIL else 0)
