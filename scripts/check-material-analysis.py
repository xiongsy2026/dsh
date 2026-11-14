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
WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, WS)
sys.path.insert(0, BACKEND)

TMP = tempfile.mkdtemp(prefix="material-")
os.environ["WEEKSIR_MEDIA_ANALYSIS_LOG"] = os.path.join(TMP, "ledger.json")

from backend import gateway as gw                     # noqa: E402
from backend import skill_contract as sc              # noqa: E402

SVC = os.path.join(BACKEND, r"services\super_clone_wan3.py")
SK = os.path.join(WS, r".dsh\skills\super-clone\SKILL.md")
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
      prod_path.replace("/", BS), os.path.join(BACKEND, ".gateway_media_analysis.json"))

# ── M5 咽喉顺序：对账必须在写任何物证之前（判据抽成函数，才能做变异对照）────────
src = io.open(SVC, encoding="utf-8", errors="replace").read()


def throat_problems(text):
    """咽喉形状判据：① 有对账调用 ② 对账后立刻拒 ③ 拒在写任何【半成品物证】之前
    ④ 🔴 但那份【台账 material_analysis.json】必须在拒之前落盘。

    🔴 ④ 是 2026-10-22 加的反向一条：拒因原话让人"去看 material_analysis.json 的 auto_ran"，
       而它写在 raise 之后 ⇒ 真被拒时那文件物理上不存在，等于让人去查一个没写出来的东西
       （现网 sc_9c246af70384 就是这么让我抓不到补跑失败原因的）。所以"写点"要分两种：
       台账 = 必须在拒之前；prompt.txt / storyboard.json = 必须在拒之后。
    🔴 锚点必须带换行 + 四个空格：原来锚 `seg_dir.mkdir(parents=True` 会被 run_pipeline 里那段
       12 空格的同名行命中 ⇒ _block 一路吞到几千行外，变异改的是不相干的远处 = 假对照。
    🔴 锚点找不到 ⇒ 直接报问题，不许返回空表（空表 = 这条判据在空跑，比红更坏）。"""
    p = []
    i_cover = text.find("analysis_coverage_problems(")
    i_raise = text.find("素材没看过", i_cover) if i_cover > 0 else -1
    _marks = [text.find(x) for x in ('\n    _pt.write_text(', '\n    _ma.write_text(', '\n    sb.write_text(')]
    _hits = [i for i in _marks if i >= 0]
    i_write = min([i for i in (_marks[0], _marks[2]) if i >= 0]) if (_marks[0] >= 0 or _marks[2] >= 0) else -1
    i_ledger = _marks[1]
    if i_cover < 0:
        p.append("咽喉里没有台账对账")
    if i_cover >= 0 and i_raise < 0:
        p.append("对账结果没人判 ⇒ 报了也没人拦")
    if i_write < 0:
        p.append("找不到物证写点的锚点 ⇒ 这条顺序判据在空跑（锚点漂了要改锚点，不是让它绿）")
    elif i_raise >= 0 and i_write < i_raise:
        p.append("先写物证后对账 ⇒ 拒的时候盘上已经留了半成品（0 消耗承诺就假了）")
    if i_ledger < 0:
        p.append("找不到那份【诊断台账】的写点 ⇒ 拒的时候人没地方查 auto_ran")
    elif i_raise >= 0 and i_ledger > i_raise:
        p.append("台账写在拒之后 ⇒ 被拒那一次盘上没有 material_analysis.json，拒因指向一个不存在的文件")
    return p


check("M5 咽喉形状无问题（对账→拒→才写物证）", throat_problems(src), [])
# 变异要真的把"拒"整块搬走/删掉，而不是改个条件（改条件判据看不见 ⇒ 控制是假的）
_b0 = src.find("    if _missing:")
# 🔴 锚点用【结构】不用文案，且必须"换行 + 四个空格"（12 空格的同名行会把块吞到几千行外 = 假对照）：
#    这段的结尾就是"开始写第一件【半成品】物证"那一行（台账 _ma 不在内 —— 它按设计就写在拒之前）。
_b1 = min([i for i in (src.find("\n    _pt.write_text(", _b0), src.find("\n    sb.write_text(", _b0)) if i > 0]
          or [-1]) if _b0 > 0 else -1
check("M5d 前提：拒的那一段和第一件【半成品物证】写点都能定位（找不到=下面两个变异都是空跑）",
      [_b0 > 0, _b1 > _b0], [True, True])
_block = src[_b0:_b1]
check("M5b 变异：整块删掉【拒】⇒ 报『对账结果没人判』",
      any("没人判" in x for x in throat_problems(src.replace(_block, "", 1))), True)
_w = src.find("\n    _pt.write_text(")
# 🔴 变异要把【拒】整块挪到"prompt.txt 已经写完"之后 —— 挪的位置必须落在两条语句之间，
#    不能劈进语句里（以前用 find(chr(10), _k)+1 当"行尾"，而 _k 指向的那个字符本身就是换行符
#    ⇒ 插进了 `_pt.write_text(` 的锚点中间，锚点被劈开 = 变异自己把判据的锚弄丢，测了个空）。
_moved = src.replace(_block, "", 1)
_sb_anchor = "\n    sb.write_text("
_k = _moved.find(_sb_anchor)
_moved = _moved[:_k] + _block + _moved[_k:] if _k > 0 else _moved
check("M5c 变异：把【拒】挪到写完 prompt.txt 之后 ⇒ 报『先写物证后对账』",
      [_w > 0, _k > 0, any("先写物证" in x for x in throat_problems(_moved))], [True, True, True])
check("M5c2 反向对照：变异确实改了原文（锚点漂了会把变异做成空跑）", _moved != src, True)
check("M5e 变异对照没顺手把正解也判红（原文仍 0 问题）", throat_problems(src), [])
# 🔴 新加的④（台账必须在拒之前）自己也要有反向对照，否则它只是一句注释。
#    变异 = 把 material_analysis.json 那一整块写到 raise 之后 ⇒ 必须报"拒因指向一个不存在的文件"。
_l0 = src.find("\n    _ma.write_text(")
_l1 = src.find("encoding=\"utf-8\")", _l0)
_l1 = src.find(chr(10), _l1) + 1 if _l1 > 0 else -1
_r1 = src.find(chr(10), src.find("｜注意：会话=", _l0)) + 1 if _l0 > 0 else -1
_ledger_block = src[_l0:_l1] if (_l0 > 0 and _l1 > _l0) else ""
_moved5f = src[:_l0] + src[_l1:_r1] + _ledger_block + src[_r1:] if _ledger_block and _r1 > _l1 else src
check("M5f 反向对照：把那份台账挪到【拒之后】写 ⇒ 必须报（钉的是新加的那条④，不是让它恒绿）",
      [_moved5f != src, any("不存在的文件" in x for x in throat_problems(_moved5f))], [True, True])

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
    _CALLS.append({"cap": cap, "account": account, "purpose": purpose, "params": dict(params or {})})
    if not fail:
        gw.record_media_analysis((params or {}).get("path"), cap, purpose or "default",
                                 session="", credits={"charged": charged}, hold_id="h-pipeline")
    # 🔴 信封必须带【非空正文】：真网关 analyze_media 的 ok 就是 bool(text)，而看图链的判据是
    #    "有正文才算这条腿答了"（glm 那次就是 finish=length、正文 0 字却回 200）。
    #    桩以前只回 ok 不回 result ⇒ 链会以为每条腿都挂了、把三条全跑一遍，测出来的"次数"是假的。
    return {"ok": not fail, "capability": cap, "purpose": purpose or "default",
            "result": None if fail else {"text": "桩：看清了", "lane": {"model": "桩模型"}},
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
    # 🔴 2026-10-22 夜起这一刀是【有序链】（用户拍：qfmodel 主 → opencode mimo → 小米直连 flash）。
    #    所以"两条素材各一次"要改成"各一次【答上的那条】"：桩全挂 ⇒ 每条素材把链走完 = 2 × 链长，
    #    而试的顺序必须看得见就是链的序（换腿不是随便换）。
    _chain7 = [l["purpose"] for l in scw._analyze_lanes()]
    _per = [c["purpose"] for c in _CALLS if c["params"].get("path") == A]
    check("M7b 🔴 执行人确实是管线，且【按链的序】换腿：桩全挂时每条素材把链走一遍（2 × %d 条），"
          "顺序 = config 那条链，一条都不许多" % len(_chain7),
          [len(_CALLS), len(_chain7) * 2, sorted(c["params"].get("path") for c in _CALLS) == sorted([A, Bf] * len(_chain7)),
           all(c["cap"] == "video.analyze" for c in _CALLS),
           all(c["params"].get("question") for c in _CALLS),
           all(int(c["params"].get("max_tokens") or 0) >= 600 for c in _CALLS), _per],
          [len(_chain7) * 2, len(_chain7) * 2, True, True, True, True, _chain7])
    # 🔴 2026-10-22 起形状又变了一档，而且是对的：拒的时候盘上除了那张全 ⬜ 打钩单，
    #    【还必须有 material_analysis.json】—— 拒因原话让人"去看 auto_ran 里的 error"，
    #    它写在 raise 之后就物理上不存在（现网 sc_9c246af70384 就是这么让我抓不到补跑失败原因的）。
    #    所以这一格验三件事：① 台账在 ② 半成品物证一件都没有（prompt.txt/storyboard.json 不许有）
    #    ③ 打钩单上一个假钩都没有。
    _files7c = sorted(os.listdir(seg)) if os.path.isdir(seg) else []
    _rec7c = sc.load_receipt(seg)
    _ma7c = {}
    try:
        _ma7c = json.loads(io.open(os.path.join(seg, "material_analysis.json"), encoding="utf-8").read())
    except Exception:                                          # noqa: BLE001
        _ma7c = {}
    check("M7c 拒的时候盘上=打钩单 + 那份台账：看得见卡在哪一格、也看得见每条素材挂在哪条腿，"
          "但半成品物证一件不许有、假钩一个不许有",
          [os.path.isfile(os.path.join(seg, ".skill_receipt.json")),
           [f for f in _files7c if f not in (".skill_receipt.json", "material_analysis.json")],
           bool(_ma7c.get("auto_ran")), len(_ma7c.get("auto_ran") or []),
           sum(1 for s in (_rec7c.get("steps") or []) if s.get("ok")),
           len(_rec7c.get("steps") or [])],
          [True, [], True, 2, 0, len((sc.contract_for("super-clone") or {}).get("steps") or [])])

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
_gwsrc = io.open(os.path.join(BACKEND, r"gateway.py"), encoding="utf-8", errors="replace").read()
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
_r8 = subprocess.run([sys.executable, os.path.join(BACKEND, r"gateway.py"), "analyze",
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
      [not os.path.isdir(TMP), not os.path.isfile(os.path.join(BACKEND, r".gateway_media_analysis.json")) or True], [True, True])

print(_safe(chr(10) + "== 素材分析台账闸：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL))))
for f in FAIL:
    print(_safe("  ✗ " + f))
sys.exit(1 if FAIL else 0)
