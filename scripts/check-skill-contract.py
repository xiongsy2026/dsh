# -*- coding: utf-8 -*-
"""skill 执行合同闸（设计见 docs/ADR-skill-contract.md）。

钉的是【机制还在、且判据只有一份】，不是"某个 skill 今天跑通了"。全离线：不联网、不花钱、
临时目录里造产物。T8 用假传输层 + 假回执，把"绕过入口"这条形状真的走一遍。

T1 判据唯一源：模块在、LANE_DIRS 覆盖全部意图档（权威源 = hypit_bridge.KNOWN_SKILLS）
T2 两份名单不许漂：intent.py 的 LANES == KNOWN_SKILLS == LANE_DIRS 的键
T3 合同解析：super-clone 的 contract: 段解析得出版本/入口/步骤，且 steps 非空
T4 无合同 ⇒ 拒：contract_for(不存在的档) 为空，verify_or_raise 必须抛（不许静默放行）
T5 无回执 ⇒ 拒：有合同、有上下文、没有回执 ⇒ 抛，且文案点名该跑哪个入口
T6 合格回执 ⇒ 放行（正向对照，防"永远拒"式假安全）
T7 版本不符 ⇒ 拒（合同改过，旧回执作废）
T8 输入指纹变了 ⇒ 拒（拿旧 prompt 配新素材 = 事故形状）
T9 偏离必须带原因：deviations 里 reason 为空 ⇒ 拒
T10 回执被手写改过：证据文件 sha8 不符 ⇒ 拒
T11 交付打印：summarize() 把缺步/跳过原因原样印出来
T12 落点：super_clone_wan3 在提交之前调 verify_or_raise，且顺序在 _run_executor 之前
T13 全局层 §8 在、且没被抄进工作区层（两处各写一份迟早漂）
"""
import io
import importlib.util
import inspect
import json
import os
import re
import shutil
import sys
import tempfile

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WS)
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(WS, r"scripts"))

TMP = tempfile.mkdtemp(prefix="skill-contract-")
PASS, FAIL = [], []


def _safe(s):
    """按【输出流自己的编码】做 errors=replace；绝不 reconfigure 成 utf-8
    （那会造"零输出 + exit 0"的静默假绿）。"""
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


from backend import skill_contract as sc                        # noqa: E402
from backend import hypit_bridge                                # noqa: E402


# ---- T1/T2 名单对齐（唯一源三处，漂了就红）----
check("T1 LANE_DIRS 存在且非空", len(sc.LANE_DIRS) > 0, True)
known = [s for s in getattr(hypit_bridge, "KNOWN_SKILLS", []) if s != "prefill"]
lanes_src = io.open(os.path.join(WS, "scripts", "intent.py"), encoding="utf-8").read()
m = re.search(r"(?m)^LANES = \(([^)]*)\)", lanes_src)
lanes = re.findall(r'"([^"]+)"', m.group(1)) if m else []
check("T2a intent.LANES == hypit_bridge.KNOWN_SKILLS", sorted(lanes), sorted(known))
check("T2b 每条意图档都有 LANE_DIRS 映射（没有映射=没法核验=漏网）",
      sorted([x for x in lanes if x not in sc.LANE_DIRS]), [])
check("T2c LANE_DIRS 不认账外档（防止凭空多一档没人管）",
      sorted([x for x in sc.LANE_DIRS if x not in lanes]), [])
for lane in lanes:
    d = sc.LANE_DIRS.get(lane)
    p = os.path.join(WS, ".dsh", "skills", d or "", "SKILL.md")
    check("T1b 档 %s 的 SKILL.md 在盘上" % lane, os.path.isfile(p), True)

# ---- T3 合同解析 ----
c = sc.contract_for("super-clone")
check("T3a super-clone 合同有版本", bool(re.match(r"^[\w.-]+@\d+$", c.get("version", ""))), True)
check("T3b 合同声明了入口（entry）", "runner/run.py" in c.get("entry", ""), True)
check("T3c 合同声明了花钱那一刀（spend）", c.get("spend", "").startswith("video.generate/"), True)
check("T3d steps 非空且每条有 id+证明", all(s["id"] and s["proof"] for s in c.get("steps", [])) and len(c.get("steps", [])) >= 1, True)
check("T3e 至少一条 required=1（全是可选=合同是空的）",
      any(s["required"] for s in c.get("steps", [])), True)

# ---- T4 无合同 ⇒ 拒 ----
sc.LANE_DIRS["ghost-lane"] = "does-not-exist-skill"
try:
    sc.verify_or_raise("ghost-lane", TMP, inputs=[])
    t4 = "没抛"
except sc.SkillContractError as e:
    t4 = "拒" if "没有 contract" in str(e) else "抛错但理由不对:" + str(e)[:60]
except Exception as e:                                          # noqa: BLE001
    t4 = "错类型:%s" % type(e).__name__
finally:
    sc.LANE_DIRS.pop("ghost-lane", None)
check("T4 没有合同的档 ⇒ 直接拒（不许静默放行）", t4, "拒")

# ---- 造一份真产物：原片 + 产品图 + prompt.txt ----
task = os.path.join(TMP, "productions", "t1", "seg1")
os.makedirs(task)
vid = os.path.join(task, "trimmed_source.mp4")
prod = os.path.join(task, "p1.jpg")
io.open(vid, "wb").write(b"\x00" * 4096)
io.open(prod, "wb").write(b"\xff\xd8" + b"y" * 2048)
prompt = os.path.join(task, "prompt.txt")


def write_prompt(text="①商品迁移 ②口播 ③模特 ④场景 ⑤图N绑定 ⑥格式编译\n"
                 "画面中不得出现字幕、花字、贴纸、账号水印或原片烧录文字。\n"):
    io.open(prompt, "w", encoding="utf-8").write(text)


sb = os.path.join(task, "storyboard.json")          # Step1 真产物（喂给编译器的那一份）
meta = os.path.join(task, "prompt_meta.json")       # 编译器自己写的来源指纹
mat = os.path.join(task, "material_analysis.json")  # 素材台账对账物证（合同 @5 起的必需步）


# 🔴 盘上真产物那个形状（2026-10-10 从 productions/…/step1_mimo/storyboard.json 抄，键名逐字）：
#    时间住在 时间区间 里，shot 顶层【没有】start/end —— 全仓唯一读法是 super_clone_wan3._shot_time_range。
#    闸以前的 fixture 是 {"shot":1,"start":0.0,"end":5.0}，那是闸自己凭想象造的产物形状：
#    判据要求 start/end、fixture 就给 start/end ⇒ 闸全绿，真跑必拒（今天线上就是这一刀）。
REAL_SHOTS = [
    {"shot": 1, "时间区间": {"开始时间": "00:00.00", "结束时间": "00:03.85", "镜头时长": "3.85s"},
     "场景环境": {"环境": "客厅", "主色调": "暖木色"}, "口播": "这双鞋我穿了三个月"},
    {"shot": 2, "时间区间": {"开始时间": "00:03.85", "结束时间": "00:10.15", "镜头时长": "6.30s"},
     "场景环境": {"环境": "玄关", "主色调": "白墙"}, "口播": "鞋底软硬适中"},
]


def write_storyboard(shots=None):
    if shots is None:
        shots = REAL_SHOTS
    io.open(sb, "w", encoding="utf-8").write(json.dumps(shots, ensure_ascii=False))


def write_meta(bind_to=None, source="step1_5_gpt_compiler"):
    io.open(meta, "w", encoding="utf-8").write(json.dumps(
        {"source": source, "storyboard_sha8": sc.sha8(bind_to or sb), "prompt_chars": 30},
        ensure_ascii=False))


# storyboard-fact-check 的物证（合同 @6 起的必需步）：真产物由 _contract_evidence 写，
# 这里照它的形状造一份 —— 关键是 source 与 storyboard_sha8，判据 json-nonempty 只断"有内容"。
sbc = os.path.join(task, "storyboard_check.json")


def write_sbc(bind_to=None, note="0-1s 正面口播微笑；约 1.0s 双手抬至胸前比框后放下"):
    io.open(sbc, "w", encoding="utf-8").write(json.dumps(
        {"source": "step1_mimo+runner-extra-note", "shot_count": 1, "duration": 5.1,
         "note_present": bool(note), "note_chars": len(note or ""),
         "note_sha8": sc.sha8(sb), "storyboard_sha8": sc.sha8(bind_to or sb)},
        ensure_ascii=False))


IMPL_OK = {"negative-lock": "compiler-negative-lock",
           "material-analysis": "pipeline:invoke:video.analyze",
           "storyboard-fact-check": "runner:--extra-note"}


def write_material():
    """material_analysis.json 的合格形状（合同新加的 required 步：每个素材都要有看图记录）。
    真判据在网关台账那边（check-material-analysis.py 跑的是真对账）；这里只造【物证文件】。"""
    io.open(mat, "w", encoding="utf-8").write(json.dumps({
        "session": "gate-fixture", "missing": [], "ledger_rows": 2,
        "materials": [{"ok": True, "sha8": "aaaa1111", "bytes": 123, "name": os.path.basename(prod)}]},
        ensure_ascii=False))
    return mat


def make_receipt(deviations=(), with_sb=True, impl=None, with_factcheck=True):
    if with_sb:
        write_storyboard()
        write_meta()
        if with_factcheck:
            write_sbc()
    else:
        for f in (sb, meta):
            if os.path.exists(f):
                os.remove(f)
    return sc.write_receipt("super-clone", task,
                            steps={"material-analysis": (write_material() if with_sb else None),
                                   "compile-prompt": prompt, "storyboard": sb,
                                   "storyboard-fact-check": (sbc if with_factcheck else None),
                                   "compile-bound": meta, "negative-lock": prompt,
                                   "source-fit": vid,
                                   "reference-clean": None},
                            inputs=[vid, prod], deviations=list(deviations),
                            # 🔴 每一格都得有模型名才打得上钩（2026-10-12 用户口径）⇒ 夹具照真产物的口径给：
                            #    网关腿/账本外腿给真模型串，本地步写 NO_MODEL。
                            models={"material-analysis": "mimo-v2.6-flash@网关 · 网关账本内",
                                    "storyboard": "mimo-v2.5@token-plan-cn.xiaomimimo.com · 账本外·直连厂商",
                                    "storyboard-fact-check": sc.NO_MODEL,
                                    "compile-prompt": "gpt-5.6-sol@hub.nocannobb.com · 账本外·直连厂商",
                                    "compile-bound": "gpt-5.6-sol@hub.nocannobb.com · 账本外·直连厂商",
                                    "negative-lock": "gpt-5.6-sol@hub.nocannobb.com · 账本外·直连厂商",
                                    "source-fit": sc.NO_MODEL},
                            impl=dict(IMPL_OK, **(impl or {})))


# ---- T5 无回执 ⇒ 拒 ----
write_prompt()
try:
    sc.verify_or_raise("super-clone", task, inputs=[vid, prod])
    t5 = "没抛"
except sc.SkillContractError as e:
    t5 = "点名入口" if "runner/run.py" in str(e) else "文案不含入口:" + str(e)[:60]
check("T5 有合同无回执 ⇒ 拒，且文案点名该跑的入口", t5, "点名入口")

# ---- T6 合格回执 ⇒ 放行（正向对照）----
make_receipt()
try:
    out = sc.verify_or_raise("super-clone", task, inputs=[vid, prod])
    t6 = ("放行", out.get("ok"))
except sc.SkillContractError as e:
    t6 = ("误拒", str(e)[:70])
check("T6 合格回执 ⇒ 真放行（防永远拒式假安全）", t6, ("放行", True))

# ---- T7 版本不符 ----
bad = json.loads(io.open(os.path.join(task, sc.RECEIPT_NAME), encoding="utf-8").read())
bad["contract_version"] = "super-clone-wan3@0"
io.open(os.path.join(task, sc.RECEIPT_NAME), "w", encoding="utf-8").write(json.dumps(bad, ensure_ascii=False))
try:
    sc.verify_or_raise("super-clone", task, inputs=[vid, prod])
    t7 = "没抛"
except sc.SkillContractError as e:
    t7 = "版本" if "版本不符" in str(e) else str(e)[:60]
check("T7 合同版本不符 ⇒ 拒（合同改过旧回执作废）", t7, "版本")
make_receipt()

# ---- T8 输入指纹变了（换了一张产品图，回执还是旧的）----
prod2 = os.path.join(task, "p2.png")
io.open(prod2, "wb").write(b"\x89PNG" + b"z" * 1024)
try:
    sc.verify_or_raise("super-clone", task, inputs=[vid, prod2])
    t8 = "没抛"
except sc.SkillContractError as e:
    t8 = "指纹" if "不在回执里" in str(e) else str(e)[:60]
check("T8 提交的文件与编译时不是同一批 ⇒ 拒", t8, "指纹")
# 同一批文件、内容被改：回执里的 sha8 对不上
io.open(prod, "wb").write(b"\xff\xd8" + b"x" * 2048)
try:
    sc.verify_or_raise("super-clone", task, inputs=[vid, prod])
    t8b = "没抛"
except sc.SkillContractError as e:
    t8b = "内容变了" if "内容变了" in str(e) else str(e)[:60]
check("T8b 同一输入文件内容变了 ⇒ 拒（旧 prompt 配新素材）", t8b, "内容变了")
make_receipt()

# ---- T9 偏离必须带原因 ----
make_receipt(deviations=[{"step": "compile-prompt", "reason": ""}])
try:
    sc.verify_or_raise("super-clone", task, inputs=[vid, prod])
    t9 = "没抛"
except sc.SkillContractError as e:
    t9 = "没原因" if "没写原因" in str(e) else str(e)[:60]
check("T9 跳过步骤不写原因 ⇒ 拒（豁免不许静默）", t9, "没原因")
make_receipt(deviations=[{"step": "compile-prompt", "reason": "客户要求只跑单段测试"}])
try:
    sc.verify_or_raise("super-clone", task, inputs=[vid, prod])
    t9b = "放行"
except sc.SkillContractError as e:
    t9b = str(e)[:60]
check("T9b 写明原因的跳过 ⇒ 允许（但仍会被打印）", t9b, "放行")

# ---- T10 回执被手写改过（证据文件 sha8 不符）----
make_receipt()
io.open(prompt, "w", encoding="utf-8").write("agent 自己手写的一段 prompt，没走编译器\n" * 30)
try:
    sc.verify_or_raise("super-clone", task, inputs=[vid, prod])
    t10 = "没抛"
except sc.SkillContractError as e:
    t10 = "被改过" if "被改过" in str(e) else str(e)[:60]
check("T10 编译器产物事后被手写替换 ⇒ 拒（sha8 不符）", t10, "被改过")
write_prompt()

# ---- T11 交付打印 ----
make_receipt(deviations=[{"step": "reference-clean", "reason": "该段无需无产品帧"}])
os.remove(os.path.join(task, "prompt.txt"))          # 制造"缺证据"的形状
txt = sc.summarize(os.path.dirname(task))
check("T11a summarize 找到段目录里的回执，且印的是【执行表】（交付只拿到 task 根）",
      ["流程执行表" in txt, "流程回执" not in txt], [True, True])
check("T11b 缺的步被印出来", "compile-prompt" in txt, True)
check("T11c 跳过原因被印出来", "该段无需无产品帧" in txt, True)
check("T11d 交付打印里带【谁做的（会话）】这一列（用户要的就是这个）",
      "谁做的（会话）" in txt, True)
check("T11e summarize 不再堆要点列表（表格与要点两种形状不叠加）",
      "\n- ✅" not in txt, True)
write_prompt()

# ---- T12 落点：核验必须在提交之前 ----
src = io.open(os.path.join(BACKEND, r"services\super_clone_wan3.py"), encoding="utf-8").read()
i_verify, i_exec = src.find("_skc.verify_or_raise("), src.find("last_result = await _run_executor(")
check("T12a 提交前调了 verify_or_raise", i_verify > 0, True)
check("T12b 顺序：verify 在 _run_executor 之前", 0 < i_verify < i_exec, True)
check("T12c 回执由管线逐格打钩（开工就落骨架 + 每步 tick，不是让人手抄、也不是跑完一次性补）",
      all(x in src[:i_verify] for x in ("open_receipt(", "_tk(", "_skc.tick(")), True)
check("T12d 反向对照：verify 写在提交之后要能被抓到", i_exec < i_verify, False)
# 只断提交不断捞回：查询/下载腿里不许出现 verify
for fn in ("query_task", "poll_until_done", "download_result"):
    seg = io.open(os.path.join(BACKEND, r"services\wan3_client.py"), encoding="utf-8").read()
    i = seg.find("def " + fn)
    body = seg[i:i + 900] if i >= 0 else ""
    check("T12e %s 里没有合同核验（已提交的任务要能捞回=不是双花）" % fn,
          "verify_or_raise" in body, False)

# ---- T13 全局层 §8 ----
g = io.open(os.path.expandvars(r"%APPDATA%\dsh-desktop\harness\AGENTS.md"), encoding="utf-8").read()
w = io.open(os.path.join(WS, "agents.md"), encoding="utf-8").read()
sec8 = g[g.index("## 8"):] if "## 8" in g else ""
check("T13a 全局层 §8 在且两条硬形状齐", [x in sec8 for x in ("contract:", "直接拒", "不许静默跳")], [True] * 3)
check("T13b §8 没被抄进工作区层（两处各写一份迟早漂）", "执行 skill 必须走它声明的入口" in w, False)

# ---- T15-T17 🔴 内容判据：今天那条事故形状必须被拒（"证明"不能只是文件在）----
write_prompt()                                            # 手写一个 prompt.txt，Step1 分镜根本没跑
make_receipt(with_sb=False)
try:
    sc.verify_or_raise("super-clone", task, inputs=[vid, prod])
    t15 = "没抛"
except sc.SkillContractError as e:
    t15 = "缺步" if "缺少步 storyboard" in str(e) else str(e)[:70]
check("T15 只手写 prompt.txt、Step1 分镜没跑 ⇒ 拒（今天真实发生的形状，旧合同放行）", t15, "缺步")

make_receipt()                                            # 补齐真物证，确认基线是放行
sc.verify_or_raise("super-clone", task, inputs=[vid, prod])
write_storyboard([REAL_SHOTS[1]])                         # 换了一份分镜（真形状，只是内容不同），meta 还绑着旧的
try:
    sc.verify_or_raise("super-clone", task, inputs=[vid, prod])
    t16 = "没抛"
except sc.SkillContractError as e:
    t16 = "指纹不符" if "不符" in str(e) else str(e)[:70]
check("T16 prompt 绑的 storyboard 与实际文件不符（事后换料/跳步重跑）⇒ 拒", t16, "指纹不符")

# T17 订正（2026-10-10）：原来这条测的是"shot 缺顶层 start/end ⇒ 拒"，而那个字段是闸凭空造的 ⇒
# 它测的是一件真产物永远不会发生的事，真产物反倒过不了核验。现在钉回两件事：
make_receipt()
write_storyboard(REAL_SHOTS)                              # 盘上真产物那个形状，一个字都没改
try:
    sc.verify_or_raise("super-clone", task, inputs=[vid, prod])
    t17 = "放行"
except sc.SkillContractError as e:
    t17 = str(e)[:70]
check("T17 🔴 真产物形状（时间住在 时间区间、无顶层 start/end）必须过全链核验", t17, "放行")
make_receipt()
write_storyboard([])                                      # 空数组 = 分镜是空的，这条判据还得咬得住
try:
    sc.verify_or_raise("super-clone", task, inputs=[vid, prod])
    t17e = "没抛"
except sc.SkillContractError as e:
    t17e = "空分镜被拒" if "不成立" in str(e) else str(e)[:70]
check("T17e 数组判据仍然咬得住：storyboard 是空数组 ⇒ 拒", t17e, "空分镜被拒")

make_receipt()
write_meta(source="hand_written")                         # 编译器没跑，来源对不上
try:
    sc.verify_or_raise("super-clone", task, inputs=[vid, prod])
    t17b = "没抛"
except sc.SkillContractError as e:
    t17b = "来源不符" if "source" in str(e) or "step1_5" in str(e) else str(e)[:70]
check("T17b prompt_meta.source 不是编译器写的那个 ⇒ 拒（防手写 prompt 冒充编译产物）", t17b, "来源不符")
make_receipt()

# ---- T18-T20 🔴 条款形状与手段白名单（2026-10-09 事故根因：合同只写结果、手段留给 agent）----
MD_SHAPE_OK = """---
name: shape-ok
intent: product-video
contract:
  version: shape@1
  entry: python runner/run.py
  steps:
    - id=a | 证明=prompt.txt 非空 | 判据=json-nonempty | required=1
    - id=b | 手段=换源,manual-preprocess | required=1
---
"""
MD_SHAPE_HOLE = """---
name: shape-hole
intent: product-video
contract:
  version: hole@1
  entry: python runner/run.py
  steps:
    - id=clean | required=1
---
"""
cs_ok = sc.parse_contract(MD_SHAPE_OK)
cs_hole = sc.parse_contract(MD_SHAPE_HOLE)
check("T18a 合同条款形状合格（每条至少有物证或手段）⇒ 无问题",
      sc.contract_shape_problems(cs_ok), [])
check("T18b 只写结果不写手段/物证的条款 ⇒ 报出来（今天那条「删除字幕水印」就是这个形状）",
      [len(sc.contract_shape_problems(cs_hole)), "clean" in str(sc.contract_shape_problems(cs_hole))], [1, True])
check("T18c 真机上 super-clone 的合同必须是合格形状",
      sc.contract_shape_problems(sc.contract_for("super-clone")), [])
check("T19a 手段白名单：自造的 ffmpeg ⇒ 报「不在合同白名单内」",
      bool([x for x in sc.impl_problems(cs_ok, {"b": "ffmpeg-delogo"}) if "不在合同白名单" in x]), True)
check("T19b 白名单内的手段 ⇒ 放行（防永远拒式假安全）",
      sc.impl_problems(cs_ok, {"b": "换源"}), [])
check("T19c 限定了手段却没申报 ⇒ 也报（不许沉默通过）",
      bool([x for x in sc.impl_problems(cs_ok, {}) if "没申报" in x]), True)
_real_cf = sc.contract_for
sc.contract_for = lambda s, **kw: cs_hole                  # 只桩"读 SKILL.md"这一层（生产会带 variant= 实参）
try:
    try:
        sc.verify_or_raise("shape-hole", task, inputs=[vid, prod])
        t20 = "没抛"
    except sc.SkillContractError as e:
        t20 = "合同有洞就拒绝提交" if "合同不合格" in str(e) else str(e)[:70]
    except Exception as e:                                # 其他异常 = 闸成了故障点
        t20 = "RAISED:" + type(e).__name__
finally:
    sc.contract_for = _real_cf
check("T20 合同存在空洞时 verify_or_raise 直接拒（不是提醒，是拒绝花钱）", t20, "合同有洞就拒绝提交")

# ---- T21-T22 text-has 判据（C 方案：字幕/水印由提示词负向锁定控制，物证就是 prompt.txt 里那段话）----
MD_TH = """---
name: th
intent: product-video
contract:
  version: th@1
  entry: python runner/run.py
  steps:
    - id=x | 证明=t | 判据=text-has=不得出现字幕;水印 | required=1
---
"""
cs_th = sc.parse_contract(MD_TH)
_th_pred = cs_th["steps"][0]["pred"]
p_ok = os.path.join(task, "th_ok.txt")
io.open(p_ok, "w", encoding="utf-8").write("前文… 画面中不得出现字幕、花字、账号水印。")
check("T21a 产物里含全部指定片段 ⇒ 放行（正向对照，防永远拒）", sc.check_proof(_th_pred, p_ok, {}), "")
io.open(p_ok, "w", encoding="utf-8").write("前文… 画面干净。")        # 禁令没进产物 = 等于没锁
check("T21b 片段缺失 ⇒ 报『该约束没真进产物』（不是只看文件在不在）",
      bool(sc.check_proof(_th_pred, p_ok, {})), True)
os.remove(p_ok)
check("T21c 证据文件不存在 ⇒ 也报（不许把『读不到』当通过）",
      bool(sc.check_proof(_th_pred, os.path.join(task, "nope.txt"), {})), True)
_nl = [x for x in sc.contract_for("super-clone")["steps"] if x["id"] == "negative-lock"]
check("T22 🔴 super-clone 的 negative-lock 必须【限定手段】+ 带 text-has 物证（不许退回纯结果条款）",
      [len(_nl), _nl[0]["required"] if _nl else None, bool(_nl and _nl[0]["impl"]),
       bool(_nl and "text-has=" in _nl[0]["pred"])], [1, True, True, True])

# ---- T23-T25 两张表 + 手段白名单自动判（用户要求：提交前看得见"每一步是谁做的"）----
pt = sc.plan_table("super-clone")
ids = [x["id"] for x in sc.contract_for("super-clone")["steps"]]
check("T23 计划表：合同每一步都在表里，且带表头/合同版本/入口（报价消息要引用它）",
      [all(("`%s`" % i) in pt for i in ids), "| 步 | 必需 | 授权手段" in pt,
       "super-clone-wan3@" in pt, "runner/run.py" in pt], [True, True, True, True])
wt = sc.who_table(make_receipt())
check("T24 执行表：每步一行、带手段/物证/指纹列，会话取不到时明写『未知』（不许留空糊过去）",
      [all(("`%s`" % i) in wt for i in ids), "| 步 | 必需 | 谁做的（会话） |" in wt,
       "未知" in wt, "指纹" in wt], [True, True, True, True])
check("T24b verify_or_raise 把执行表带回来（提交前那一刀就能印给人看）",
      bool(sc.verify_or_raise("super-clone", task, inputs=[vid, prod]).get("table")), True)
make_receipt(impl={"negative-lock": "ffmpeg-delogo"})     # 自造手段：合同名单里没有
try:
    sc.verify_or_raise("super-clone", task, inputs=[vid, prod])
    t25 = "没抛"
except sc.SkillContractError as e:
    t25 = "自造手段被拒" if "不在合同白名单" in str(e) else str(e)[:70]
check("T25 回执里申报的手段不在合同白名单 ⇒ 拒（这次 agent 手搓 ffmpeg 的形状）", t25, "自造手段被拒")
make_receipt(impl={"negative-lock": ""})                  # 限定了手段却没申报
try:
    sc.verify_or_raise("super-clone", task, inputs=[vid, prod])
    t25b = "没抛"
except sc.SkillContractError as e:
    t25b = "没申报也被拒" if "没申报" in str(e) else str(e)[:70]
check("T25b 合同限定手段但【没申报】⇒ 也拒（不许沉默通过）", t25b, "没申报也被拒")
make_receipt()
check("T25c 白名单内的手段 ⇒ 放行（正向对照，防永远拒式假安全）",
      sc.verify_or_raise("super-clone", task, inputs=[vid, prod]).get("ok"), True)
_cr = io.open(os.path.join(BACKEND, "skill_contract.py"), encoding="utf-8", errors="replace").read()
check("T25d 白名单是 check_receipt 内部自动跑的（不是要求调用方『记得调』——靠自觉的判据等于没有）",
      "bad += impl_problems(c, used_impl)" in _cr.split("def verify_or_raise")[0], True)

# ---- T26-T30 逐行署名："每一步是谁做的"这张表的事实来源 ----
os.environ["DSH_SESSION_ID"] = "sess-A"
make_receipt()
io.open(prompt, "a", encoding="utf-8").write("\n\u91cd\u7f16\u8bd1\uff1a\u52a0\u4e86\u8fd0\u955c\u8bf4\u660e\n")
os.environ["DSH_SESSION_ID"] = "sess-B"                                      # \u53ea\u91cd\u8dd1\u7f16\u8bd1
rec_b = make_receipt()
rb = {s["id"]: s for s in rec_b["steps"]}
check("T26 \u63a5\u529b\u53ea\u6539\u7f16\u8bd1 \u21d2 \u5206\u955c\u4ecd\u7f72 A\u3001prompt \u7f72 B\uff08\u53f0\u8d26\u8bb0\u4e8b\u5b9e\uff0c\u4e0d\u662f\u6700\u540e\u4e00\u4e2a\u78b0\u6587\u4ef6\u7684\u4eba\uff09",
      [rb["storyboard"]["session"], rb["compile-prompt"]["session"]], ["sess-A", "sess-B"])
_c = dict(sc.contract_for("super-clone"))
_c["version"] = "ghost@9"
os.environ["DSH_SESSION_ID"] = "sess-C"
rec_c = sc.write_receipt("super-clone", task, steps={"storyboard": sb, "compile-prompt": prompt,
                                                     "compile-bound": meta, "negative-lock": prompt},
                         inputs=[vid, prod], impl=dict(IMPL_OK), contract=_c,
                         models={"storyboard": "mimo-v2.5@x · 账本外", "compile-prompt": "gpt@x",
                                 "compile-bound": "gpt@x", "negative-lock": "gpt@x"})
check("T26b \u5408\u540c\u7248\u672c\u4e00\u53d8 \u21d2 \u65e7\u7f72\u540d\u4e0d\u7ee7\u627f\uff1a\u8fd9\u4e00\u8f6e\u6253\u4e0a\u7684\u683c\u5b50\u5168\u7b7e sess-C\uff0c\u6ca1\u6253\u4e0a\u7684\u683c\u5b50\u3010\u4e0d\u6302\u4efb\u4f55\u4eba\u7684\u540d\u5b57\u3011"
      "\uff08\u65e7\u5199\u6cd5\u4f1a\u628a A/B \u7684\u7f72\u540d\u7559\u5728\u6ca1\u505a\u7684\u884c\u4e0a = \u53f0\u8d26\u91cc\u51fa\u73b0\u4e86\u6ca1\u4eba\u505a\u8fc7\u7684\u6b65\uff09",
      [sorted({s["session"] for s in rec_c["steps"] if s.get("ok")}),
       sorted({s["session"] for s in rec_c["steps"] if not s.get("ok")}),
       any(s.get("session") in ("sess-A", "sess-B") for s in rec_c["steps"])],
      [["sess-C"], [""], False])
wt_b = sc.who_table(rec_b)
check("T27 \u6ca1\u505a\u7684\u6b65\u4e0d\u8bb8\u6302\u7740\u67d0\u4e2a\u4f1a\u8bdd\u7684\u540d\u5b57\uff08\u2b1c \u884c\u8d23\u4efb\u5217\u5199\u201c\u8fd9\u6b65\u6ca1\u505a\u201d\uff09",
      ("\u2014\uff08\u8fd9\u6b65\u6ca1\u505a\uff09" in wt_b) and ("sess-C" not in wt_b), True)
os.environ.pop("DSH_SESSION_ID", None)
os.environ.pop(sc.CTX_ENV, None)
sc.set_ctx("super-clone", task, session="sess-D")                            # \u5e38\u9a7b\u8fdb\u7a0b\u6ca1\u6709 DSH_SESSION_ID
os.remove(os.path.join(task, sc.RECEIPT_NAME))   # \u4ece\u7a7a\u767d\u6253\u94a9\u5355\u5f00\u59cb\uff1a\u8fd9\u6761\u9a8c\u7684\u662f"\u8fd9\u4e00\u683c\u7b7e\u8c01"\uff0c\u4e0d\u662f\u7ee7\u627f
rec_d = make_receipt()
check("T28 \u5165\u53e3\u6ca1 DSH_SESSION_ID \u4f46 set_ctx \u663e\u5f0f\u5e26\u4f1a\u8bdd \u21d2 \u7167\u90a3\u4e2a\u7b7e\uff08\u4e5f\u5141\u8bb8\u670d\u52a1\u7aef\u63a5\u529b\uff09",
      [sorted({s["session"] for s in rec_d["steps"] if s.get("ok")}),
       sorted({s["session"] for s in rec_d["steps"] if not s.get("ok")})],
      [["sess-D"], [""]])
os.remove(os.path.join(task, sc.RECEIPT_NAME))   # 清掉上一份，否则逐行署名会继承 sess-D
check("T28b 两者都没（也没上一份回执）⇒ 签名留空，但表里明写未知，不许猜会话 id",
      (os.environ.pop(sc.CTX_ENV, None),
       sorted({s["session"] for s in make_receipt()["steps"]}),
       "未知" in sc.who_table(sc.load_receipt(task)))[1:],
      ([''], True))
write_prompt()
make_receipt()

# ---- T29/T30 \u4e24\u5f20\u8868\u7684\u843d\u70b9\uff1a\u62a5\u4ef7\u5e26\u8ba1\u5212\u8868\u3001\u63d0\u4ea4\u524d\u5370\u6267\u884c\u8868 ----
_wa = io.open(os.path.join(WS, "AGENTS.md"), encoding="utf-8", errors="replace").read()
check("T29 AGENTS.md \u5199\u6b7b\u4e86\u3010\u62a5\u4ef7\u5e26 plan_table / \u63d0\u4ea4\u524d\u5370 who_table\u3011\u4e24\u4e2a\u51fd\u6570\u540d",
      [x in _wa for x in ("plan_table", "who_table")], [True, True])
check("T29b AGENTS.md 还得给出命令行（只写函数名 = agent 要自己拼 python -c，等于没给）",
      "scripts/contract_tables.py" in _wa, True)
_scw = io.open(os.path.join(BACKEND, "services", "super_clone_wan3.py"),
               encoding="utf-8", errors="replace").read()
_i_tbl = _scw.find("\u5408\u540c\u6838\u9a8c")
_i_sub = _scw.find("last_result = await _run_executor(")
check("T30 \u7ba1\u7ebf\u628a\u6267\u884c\u8868\u6253\u5728\u63d0\u4ea4\u524d（log \u2018\u5408\u540c\u6838\u9a8c\u2019 \u5728 submit \u4e4b\u524d）",
      0 < _i_tbl < _i_sub, True)
check("T30b \u53cd\u5411\u5bf9\u7167\uff1a\u6253\u5370\u6362\u5230\u63d0\u4ea4\u4e4b\u540e\u8981\u80fd\u88ab\u6293\u5230",
      _i_sub < _i_tbl, False)

# ---- T31 出表命令真跑（子进程、只读、0 消耗）----
import subprocess                                          # noqa: E402
_CLI = os.path.join(WS, "scripts", "contract_tables.py")
_r = subprocess.run([sys.executable, _CLI, "super-clone"], capture_output=True)
_o = _r.stdout.decode("utf-8", "replace") + _r.stderr.decode("utf-8", "replace")
check("T31a contract_tables 超级复刻 ⇒ 退 0 且打出计划表（GBK 控制台不准崩）",
      [_r.returncode, "**流程计划**" in _o, "| 步 | 必需 |" in _o], [0, True, True])
os.makedirs(os.path.join(TMP, "no-receipt-here"))   # 真空目录：TMP 自己藏着 fixtures 的回执
_r2 = subprocess.run([sys.executable, _CLI, "super-clone", os.path.join(TMP, "no-receipt-here")], capture_output=True)
_o2 = _r2.stdout.decode("utf-8", "replace") + _r2.stderr.decode("utf-8", "replace")
check("T31b 目录里没回执 ⇒ 退 1 且明说“没有回执”（不准给空表糊弄）",
      [_r2.returncode, "没有回执" in _o2], [1, True])
_r3 = subprocess.run([sys.executable, _CLI, "super-clone", task], capture_output=True)
_o3 = _r3.stdout.decode("utf-8", "replace") + _r3.stderr.decode("utf-8", "replace")
check("T31c 有回执 ⇒ 打出执行表且带【谁做的】列",
      ["流程执行表" in _o3, "谁做的（会话）" in _o3], [True, True])


# ---- T14 每条意图档都必须有合同（用户 2026-10-09："所有现有的和新的 skill 都必须遵守"）----
# 🔴 这条【现在就是红的】，而且是故意的：没交合同的档 = 待办清单，不是"将来该遵守"。
#    谁嫌红就去补 contract: 段；把这条断言删掉或改成"只查 super-clone"，等于把铁律降级成建议。
no_contract = [l for l in lanes
               if not any(s["required"] for s in sc.contract_for(l).get("steps", []))]
check("T14 所有带引擎的档都交了【含必需步骤】的合同（红=还有档没交，见下面清单）", no_contract, [])
if no_contract:
    print(_safe("  待补合同的档（%d/%d）：%s" % (len(no_contract), len(lanes), ", ".join(no_contract))))

# ---- T32 背景参考图必须真能进 wan3.0（2026-10-31 用户裁定："背景就一个背景，本地要能传进wan3.0"
#      —— 翻掉 2026-10-09 的"背景只走文字"。引擎侧 scene_image_path 从未拆掉：_build_media_manifest
#      把场景图编成 图N，上传后与产品/模特图一起进 wan3.0 的 image_urls；编译器任务4 以图为背景事实源。
#      判据钉在【入口 + 表单 + 文档口径】三处：入口要收图并转给引擎，表单要列 scene_image，
#      文档不许再留"只走文字/图不进引擎"的旧裁定。----
_BG_REQUIRED = (
    ("runner/run.py", r'add_argument\("--scene-image', "runner 不再收背景图入参"),
    ("runner/run.py", r'scene_image_path\s*=\s*vals\.get\("scene_image"', "runner 没把背景图转给 run_pipeline"),
    ("SKILL.md", r'^\s*scene_image\|images\|', "form: 段没列 scene_image 格"),
)
_BG_FORBIDDEN = (
    (r'背景只走文字|图本身不进引擎|背景没有图像入参|参考图不进引擎|图不作为生成入参|本 lane 恒为 null',
     "文档还留着 2026-10-09 的旧裁定（背景只走文字）"),
)
_BG_FILES = ("SKILL.md", "runner/run.py", "manifest.json",
             "references/compiler-contract.md", "references/pipeline.md")


def scene_image_wired_problems(files):
    """files = {相对路径: 正文}。返回"背景图这条链断了/旧口径没清"的地方；空 = 已按 2026-10-31 裁定接通。"""
    bad = []
    for fn, pat, why in _BG_REQUIRED:
        if not re.search(pat, files.get(fn, ""), re.M):
            bad.append((fn, why))
    for fn, s in files.items():
        for pat, why in _BG_FORBIDDEN:
            if re.search(pat, s):
                bad.append((fn, why))
    return bad


_lane_files = {}
for _fn in _BG_FILES:
    _p = os.path.join(WS, ".dsh", "skills", "super-clone", *_fn.split("/"))
    if os.path.isfile(_p):
        _lane_files[_fn] = io.open(_p, encoding="utf-8").read()
check("T32 super-clone 背景图【真能进 wan3.0】：入口收图、转给引擎、表单列格、旧口径清零",
      scene_image_wired_problems(_lane_files), [])
check("T32c 覆盖对照：五份文件真的都读到了（少读一份 = T32 会因为『没内容可匹配』而假绿）",
      sorted(_lane_files), sorted(_BG_FILES))
# 反向对照：把裁定前（2026-10-09 只走文字）的形状喂进同一判据 ⇒ 必须全报（绿不是因为判据根本没匹配过）
_OLD_SHAPE = {"runner/run.py": 'p.add_argument("--scene-text", default=None)\n'
                               'result = run_pipeline(task_id=t)\n',
              "SKILL.md": '  scene_text|textarea|场景替换描述|no| |scene_text|hint=背景只走文字：参考图不进引擎\n'}
check("T32b 反向对照：裁定前的形状（不收图 + 只走文字）喂进同一判据 ⇒ 四处全报（判据真的有牙）",
      len(scene_image_wired_problems(_OLD_SHAPE)), 4)

# ---- T33 背景描述只有一个输入口（2026-10-09 用户："在脸图底下加一个多文本框 场景替换描述"）----
# 光加一格控件 = "改了也不生效的控件"，所以这条钉的是【格子 → 编译器真读的那一段】整条链，
# 而且直接跑 run.py 里的真函数值表（不是 grep 源码）。
_RUN = os.path.join(WS, ".dsh", "skills", "super-clone", "runner", "run.py")
_spec = importlib.util.spec_from_file_location("_sc_lane_run", _RUN)
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)
_fold = _m.fold_scene_text
check("T33 场景描述折进编译器唯一读的那一段（背景要求）；给了 --scene-text 就【赢】掉旧的",
      [_fold("客户要竖版", None), _fold(None, "米白客厅"), _fold("背景要求：旧背景\n其他要求", "新背景")],
      ["客户要竖版", "背景要求：米白客厅", "其他要求\n背景要求：新背景"])
_run_src = io.open(_RUN, encoding="utf-8").read()
_has_text = ('add_argument("--scene-text"' in _run_src)
_has_image = ('add_argument("--scene-image"' in _run_src)
check("T33b 入口面：--scene-text 在、--scene-image 也在（背景 = 图为事实源 + 文字补细节，2026-10-31 翻案）",
      (_has_text, _has_image), (True, True))
check("T33c 反向对照：把冲突场景（两处背景）喂进真函数 ⇒ 必须只剩一条『背景要求』行",
      sum(1 for l in _fold("背景要求：旧\n背景要求：更旧", "新").splitlines() if l.startswith("背景要求")), 1)

# ---- T34 单段也要写物证/回执，且每一步的模型身份要看得见（2026-10-10 用户："回执也没有…不清楚是不是对应模型跑的"）----
_SVC = os.path.join(BACKEND, "services", "super_clone_wan3.py")
_svc_src = io.open(_SVC, encoding="utf-8", errors="replace").read()
_t34_dir = os.path.join(TMP, "t34")
os.makedirs(_t34_dir, exist_ok=True)
try:
    from backend.services import super_clone_wan3 as _svc        # noqa: E402
    _imp_err = ""
except Exception as _e:                                          # noqa: BLE001
    _svc, _imp_err = None, "%s: %s" % (type(_e).__name__, str(_e)[:90])
_ref = os.path.join(_t34_dir, "ref.mp4")
_pro = os.path.join(_t34_dir, "p.png")
open(_ref, "wb").write(b"fake-video")
open(_pro, "wb").write(b"fake-product")
_SEG = os.path.join(_t34_dir, "step2_wan3")
# 合同 @5 起，_contract_evidence 会先对【素材看图台账】⇒ 夹具必须有对应记录，
# 否则这里拿到的永远是"素材没看过"而不是它真正要验的那三件事（真判据见 check-material-analysis.py）。
from backend import gateway as _g34                              # noqa: E402
os.environ["WEEKSIR_MEDIA_ANALYSIS_LOG"] = os.path.join(_t34_dir, "ledger.json")
# 合同 @7 起台账行必须带【调用凭证】(hold_id)，手写的一律不算 ⇒ 夹具也要给凭证，不然这条验不到东西。
_g34.record_media_analysis(_pro, "video.analyze", "default", session="", hold_id="h-T34")
# 🔴 再加一层保险：把 invoke 换成"必失败"的桩。万一台账漂了（判据认为缺料），管线会自己去补跑
#    —— 那条腿是真花钱的，闸绝不许去调上游（真扣钱 + 拖慢 + 结果不可复现）。
_real34 = _g34.invoke
_g34.invoke = lambda *a, **k: {"ok": False, "capability": "video.analyze", "purpose": "default",
                               "credits": {"estimated": 0, "charged": 0},
                               "error": "闸里不许真调上游（T34 桩）"}
_stamp = {"prompt": "…不得出现字幕、花字、水印…", "source": "step1_5_gpt_compiler",
          "model": "gpt-5.6-sol", "provider": "hub.nocannobb.com", "ledger": "off"}
try:
    _v34 = _svc._contract_evidence(_SEG, seg_board=REAL_SHOTS,
                                   seg_prompt="…不得出现字幕、花字、水印…", compiled_seg=_stamp,
                                   seg_duration=10.0, ref_video=_ref, seg_product_paths=[_pro],
                                   source_fit=_ref, log=None) if _svc else {}
    _imp_err = _imp_err or ("导入失败" if not _svc else "")
except Exception as _e:                                          # noqa: BLE001
    _v34, _imp_err = {}, "%s: %s" % (type(_e).__name__, str(_e)[:90])
finally:
    _g34.invoke = _real34
_t34_files = {f: os.path.isfile(os.path.join(_SEG, f)) for f in
              ("prompt.txt", "storyboard.json", "prompt_meta.json", ".skill_receipt.json")}
check("T34 入口外的物证写者实调成功（%s）：四件套全落盘" % (_imp_err or "ok"),
      [_t34_files[f] for f in ("prompt.txt", "storyboard.json", "prompt_meta.json",
                               ".skill_receipt.json")], [True, True, True, True])
_meta34 = {}
try:
    _meta34 = json.load(open(os.path.join(_SEG, "prompt_meta.json"), encoding="utf-8"))
except (OSError, ValueError):
    _meta34 = {}
check("T34b prompt_meta 带模型身份三键（model/provider/ledger），不是只有 source",
      [_meta34.get("model"), _meta34.get("provider"), _meta34.get("ledger")],
      ["gpt-5.6-sol", "hub.nocannobb.com", "off"])
_t34_tbl = str(_v34.get("table") or "")
check("T34c 执行表有「模型/路由」这一列，且印的是真模型名 + 如实标【账本外·直连厂商】",
      ["模型/路由" in _t34_tbl, "gpt-5.6-sol@hub.nocannobb.com" in _t34_tbl,
       "账本外·直连厂商" in _t34_tbl], [True, True, True])
_check_receipt34 = None
try:
    from backend import skill_contract as _sc34                  # noqa: E402
    _check_receipt34 = _sc34
except Exception:                                                # noqa: BLE001
    _check_receipt34 = None
_rec34 = {}
try:
    _rec34 = json.load(open(os.path.join(_SEG, ".skill_receipt.json"), encoding="utf-8"))
except (OSError, ValueError):
    _rec34 = {}
check("T34d 回执行的 model 键齐全（交付时那一列有出处，不是渲染器现编）",
      all("model" in s for s in _rec34.get("steps", [])), True)
if _check_receipt34:
    # 🔴 2026-10-12 用户口径："这个回执还是要，回执的写法是每一次进行打钩，有模型名" ⇒
    #    没模型名不再是"那一格少填一列"，而是【这一格打不上钩】：必需步没打 ⇒ 花钱之前拒（0 分）。
    _noStamp = dict(_stamp)
    _noStamp.pop("model")
    _SEG2 = os.path.join(_t34_dir, "step2_nomodel")
    _e34 = ""
    try:
        _svc._contract_evidence(_SEG2, seg_board=REAL_SHOTS,
                                seg_prompt="…不得出现字幕、花字、水印…", compiled_seg=_noStamp,
                                seg_duration=10.0, ref_video=_ref, seg_product_paths=[_pro],
                                source_fit=_ref, log=None)
    except sc.SkillContractError as _x34:
        _e34 = str(_x34)
    check("T34e 反向对照：编译器没给模型名 ⇒ 编译那三格打不上钩 ⇒ 花钱那一刀之前拒（不是少填一列）",
          [("缺少步 compile-prompt" in _e34), ("缺少步 compile-bound" in _e34),
           ("缺少步 negative-lock" in _e34)], [True, True, True])
    _t2 = {}
    try:
        _t2 = json.load(open(os.path.join(_SEG2, ".skill_receipt.json"), encoding="utf-8"))
    except (OSError, ValueError):
        _t2 = {}
    _rows2 = {s.get("id"): s for s in _t2.get("steps", []) if isinstance(s, dict)}
    check("T34e2 拒的时候盘上留的是【打钩单】：做过的格子照样打上了，只有没模型名的那三格是空的",
          [bool(_rows2.get("material-analysis", {}).get("ok")),
           bool(_rows2.get("storyboard", {}).get("ok")),
           bool(_rows2.get("storyboard-fact-check", {}).get("ok")),
           bool(_rows2.get("source-fit", {}).get("ok")),
           bool(_rows2.get("compile-prompt", {}).get("ok"))],
          [True, True, True, True, False])
    _t2txt = str(sc.who_table(_t2) or "")
    check("T34e3 反向对照：没打钩那一格不许拿 source 函数标签冒充模型名（表上写的是「还没做」）",
          [("step1_5_gpt_compiler@" in _t2txt), ("还没做" in _t2txt)], [False, True])
    _tk_dir = os.path.join(_t34_dir, "t34_tick")
    sc.open_receipt("super-clone", _tk_dir, inputs=[])
    _bad_tk = sc.tick("super-clone", _tk_dir, "compile-prompt",
                      evidence=os.path.join(_SEG, "prompt.txt"), model="")
    _ok_tk = sc.tick("super-clone", _tk_dir, "source-fit",
                     evidence=os.path.join(_SEG, "prompt.txt"), model=sc.NO_MODEL)
    _ghost_tk = sc.tick("super-clone", _tk_dir, " invented-step", evidence=True, model=sc.NO_MODEL)
    check("T34e4 tick 自己那两条：空模型 ⇒ 不打钩且点名要模型名；『不用模型』⇒ 打得钩（别把本地步逼成编一个型号）",
          [_bad_tk.get("ok"), ("模型名" in str(_bad_tk.get("error"))), bool(_ok_tk.get("ok"))],
          [False, True, True])
    check("T34e5 合同外的步打不上钩（打了就是替这条流程伪造一格合格状态）",
          [_ghost_tk.get("ok"), ("不在合同" in str(_ghost_tk.get("error")))], [False, True])
    _hand = json.loads(io.open(os.path.join(_SEG, ".skill_receipt.json"), encoding="utf-8").read())
    for _r5 in _hand.get("steps", []):
        _r5["model"] = ""
    _p5 = sc.check_receipt("super-clone", _hand, inputs=[_ref, _pro])
    check("T34e6 手改出来的「打了钩却没模型名」必须被 check_receipt 拒 —— 回执是个 JSON 文件，"
          "只守打钩那一刻挡不住手写（2026-10-12 现网 agent 就是手写台账行伪造过）",
          [any("打了钩却没记模型名" in x for x in _p5), any("缺少步" in x for x in _p5)], [True, False])
    check("T34e7 执行表头行带【打钩 必需 x/y】进度（交付时人一眼看出跑到第几格，不是只看终态）",
          ("打钩 必需" in _t2txt), True)
    _seg_src34 = _svc_src[_svc_src.index("def _contract_evidence("):_svc_src.index("async def run_pipeline(")]
    check("T34e8 🔴 唯一打钩入口：_contract_evidence 里只剩 tick()，write_receipt 一次都不许出现"
          "（两套打钩规则 = 第二套必然被绕过）",
          [("write_receipt(" in _seg_src34), _seg_src34.count("_tk(") >= 6], [False, True])
    check("T34e9 open_receipt 必须排在任何物证落盘之前（骨架先落 ⇒ 中途崩了看得见打到第几格）",
          _seg_src34.index("open_receipt(") < _seg_src34.index("_ma.write_text("), True)
# 覆盖对照：只修一条路 = 假修。两条提交路径都必须经过同一个写者。
_run_src34 = _svc_src[_svc_src.index("async def run_pipeline("):] if "async def run_pipeline(" in _svc_src else ""
check("T34f 🔴 覆盖对照：run_pipeline 里【多段循环】与【单段 else 分支】各调一次写者（少一处=又只剩一条路有回执）",
      _run_src34.count("_contract_evidence("), 2)
# 这次的真事故：verify_or_raise 没有 impl 形参，调用方却传了 ⇒ 每次多段提交都 TypeError 崩在那一行。
# 🔴 判据走 AST，不走正则：正则 `verify_or_raise\(([^)]*)\)` 会在 `str(seg_dir)` 那个右括号就截断
#    —— 拿修好前的备份实测过，那样写【恒绿】，正是要抓的那条崩它看不见（假保险比没有更坏）。
def _bad_kwargs(src, fname):
    import ast
    sig = inspect.signature(getattr(_check_receipt34, fname))
    acc = {n for n, q in sig.parameters.items()
           if q.kind in (inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)}
    out = []
    tree = ast.parse(src)
    for nd in ast.walk(tree):
        if isinstance(nd, ast.Call):
            fn = nd.func
            nm = getattr(fn, "attr", "") or getattr(fn, "id", "")
            if nm == fname:
                for kw in nd.keywords:
                    if kw.arg and kw.arg not in acc:
                        out.append("%s@L%d" % (kw.arg, nd.lineno))
    return sorted(set(out))


check("T34g 🔴 调用签名核对：引擎传给 verify_or_raise 的关键字必须都在它的形参名单里"
      "（多传一个 = 花钱前那一步直接 TypeError；判据用 AST，正则那条会在第一个右括号截断=恒绿）",
      _bad_kwargs(_svc_src, "verify_or_raise"), [])
check("T34g2 反向对照：同一条断言打在【修好前的备份】上必须抓到 impl（抓不到=这条是假保险）",
      [x.split("@")[0] for x in _bad_kwargs(
          io.open(os.path.join(WS, "scratch", "_bak-20261010-contract", "super_clone_wan3.py"),
                  encoding="utf-8", errors="replace").read(), "verify_or_raise")],
      ["impl"])

# ---- T35 提交链的"用了却没 import"（2026-10-10 现网：wan3_client 模块层没有 os，
#      run_one_segment_prime 走到 :500 就 NameError ⇒ 三次提交全崩在进网关之前，0 出片、闸全绿）----
_STD = {"os", "sys", "re", "json", "time", "io", "subprocess", "shutil", "hashlib", "base64",
        "urllib", "math", "uuid", "tempfile", "sqlite3", "threading", "asyncio", "glob", "copy",
        "stat", "platform", "traceback", "struct", "binascii", "contextlib", "secrets", "zlib",
        "string", "textwrap", "random", "datetime", "socket", "mimetypes", "importlib"}


def _unbound_std_src(src):
    """作用域感知的"用了却没绑定"。两件事必须避开：
       ① 模块级扫描不算函数内的 import（那是局部绑定，出不了函数）；
       ② 函数内的 import 对它自己是合法的（现网就误报过 167 那一处）。"""
    import ast
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return ["SyntaxError"]

    def bound(stmts, deep=False):
        """deep=False：模块作用域用，【不下钻进 def/class】—— 函数里的 import 是局部绑定，
           漏进模块集合就是这次自查出的失效形状（T35c 因此对真 bug 恒绿）。
           deep=True：函数作用域用，把函数体里所有层级的 import/赋值都算上。"""
        names = set()

        def push(node):
            stack.extend(getattr(node, "body", []) or [])

        stack = list(stmts)
        while stack:
            st = stack.pop()
            if isinstance(st, (ast.Import, ast.ImportFrom)):
                for al in st.names:
                    names.add((al.asname or al.name).split(".")[0])
                continue
            if isinstance(st, ast.ClassDef):
                names.add(st.name)
                if deep:
                    stack.extend(st.body)
                continue
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(st.name)
                a = st.args
                names.update(a.arg for a in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs))
                if a.vararg:
                    names.add(a.vararg.arg)
                if a.kwarg:
                    names.add(a.kwarg.arg)
                if deep:
                    stack.extend(st.body)
                continue
            if isinstance(st, ast.Name) and isinstance(st.ctx, ast.Store):
                names.add(st.id)
            if isinstance(st, ast.Global):
                names.update(st.names)
            if isinstance(st, ast.Try):
                stack.extend(st.body + st.finalbody + st.handlers)
                continue
            if isinstance(st, (ast.If, ast.While, ast.For, ast.AsyncFor, ast.With, ast.AsyncWith)):
                stack.extend(getattr(st, "body", []) or [])
                stack.extend(getattr(st, "orelse", []) or [])
                continue
        return names

    mod = bound(tree.body)                       # 浅层：函数里的 import 不算模块绑定
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    fbound = {id(f): bound(f.body, deep=True) for f in funcs}
    out = []
    for nd in ast.walk(tree):
        if not (isinstance(nd, ast.Attribute) and isinstance(nd.value, ast.Name)):
            continue
        nm = nd.value.id
        if nm not in _STD or nm in mod:
            continue
        inside = [f for f in funcs if f.lineno <= nd.lineno <= getattr(f, "end_lineno", f.lineno)]
        if any(nm in fbound[id(f)] for f in inside):
            continue
        out.append("%s@L%d" % (nm, nd.lineno))
    return sorted(set(out))


def _unbound_std_file(path):
    try:
        return _unbound_std_src(io.open(path, encoding="utf-8", errors="replace").read())
    except Exception as e:                                    # noqa: BLE001
        # 🔴 闸不许需要人守着才能跑：扫描器自己出问题必须吃成【一条红】，不是抛出去把整条闸弄崩
        #    （闸崩 = 等于没跑，比红更坏）。
        return ["SCAN-BROKE %s: %s" % (type(e).__name__, str(e)[:70])]


check("T35a 对照：合成一段「函数外用了 os 却没 import」的源码 ⇒ 必须报出来（这条得有牙）",
      _unbound_std_src("import time\ndef f(p, o):\n    return os.path.join(p, o), time.time()\n"),
      ["os@L3"])
check("T35b 对照：函数内自己 import 的【不许误报】（现网就误判过 167 那一处）",
      _unbound_std_src("def g():\n    import os\n    return os.sep\n"), [])
# 🔴 T35d = 证明 T35c 有牙的唯一办法：拿【真提交链源码】剥掉那行 import os 再跑同一条判据。
#    第一版扫描器把函数内的 import 漏算成模块绑定 ⇒ 这条当场没通过（T35c 对真 bug 恒绿），
#    就是它逼着把 bound() 改成浅层/深层两种走法。
_wc_src = io.open(os.path.join(BACKEND, "services", "wan3_client.py"),
                  encoding="utf-8", errors="replace").read()
_wx_src = _wc_src.replace(chr(10) + "import os" + chr(10), chr(10), 1)
_d_with, _d_wo = _unbound_std_src(_wc_src), _unbound_std_src(_wx_src)
# ⚠️ 断言里【不写行号】：那是要人守着的现场（我第一版写死 L500/L517，加了三行注释就自己假红了）。
#    钉的是规则：带 import ⇒ 干净；剥掉 ⇒ 只多出一批 os@ 项，别的都不许多。
check("T35d 真产物对照：同一份源码【带 import os】= 空；剥掉那行 ⇒ 只多报 os 的用处"
      "（行号不参与断言）",
      [len(_d_with), bool(_d_wo), all(x.startswith("os@") for x in _d_wo),
       set(_d_with) <= set(_d_wo)],
      [0, True, True, True])
for _f in ("backend/services/wan3_client.py", "backend/services/super_clone_wan3.py",
           "backend/gateway.py", "backend/skill_contract.py", "backend/services/runninghub_client.py"):
    _p = os.path.join(WS, *_f.split("/"))
    if not os.path.isfile(_p):
        check("T35c 提交链文件在盘上：" + _f, "missing", "present")
        continue
    check("T35c 提交链没有「用了却没绑定」：%s" % _f.split("/")[-1], _unbound_std_file(_p), [])

# ---- T36 storyboard-fact-check（合同 @6：分镜失真要有【授权手段】+ 物证，别再逼 agent 绕道）----
# 现网形状：MiMo 把 5.1s 单镜头解析成 2 镜，SKILL.md 要求"按秒写死时间轴压过它"，
# 但那一步没登记手段 ⇒ agent 只能绕开入口手搓 driver 直调 run_pipeline（2026-10-11 21:23）。
make_receipt()
try:
    sc.verify_or_raise("super-clone", task, inputs=[vid, prod])
    t36a = "放行"
except sc.SkillContractError as e:
    t36a = "误拒:" + str(e)[:60]
check("T36a 正向对照：合法回执（含 storyboard_check.json + 手段 runner:--extra-note）⇒ 真放行", t36a, "放行")

os.remove(sbc)
make_receipt(with_factcheck=False)         # 回执里这一步的证据没了 ⇒ 必须点名缺这一步
try:
    sc.verify_or_raise("super-clone", task, inputs=[vid, prod])
    t36b = "没抛"
except sc.SkillContractError as e:
    t36b = "缺步" if "缺少步 storyboard-fact-check" in str(e) else str(e)[:60]
check("T36b 分镜对账物证没落盘 ⇒ 拒（「改了分镜却查不到改了什么」就是原来那个洞）", t36b, "缺步")

make_receipt(impl={"storyboard-fact-check": "hand-driver:run_pipeline"})
try:
    sc.verify_or_raise("super-clone", task, inputs=[vid, prod])
    t36c = "没抛"
except sc.SkillContractError as e:
    t36c = "手段不符" if "storyboard-fact-check" in str(e) else str(e)[:60]
check("T36c 🔴 手段白名单只认 runner:--extra-note：拿「绕开入口手搓 driver」这条来申报 ⇒ 拒"
      "（这才是把 21:23 那个形状钉死的地方，不靠散文）", t36c, "手段不符")

make_receipt(impl={"storyboard-fact-check": None})
try:
    sc.verify_or_raise("super-clone", task, inputs=[vid, prod])
    t36d = "没抛"
except sc.SkillContractError as e:
    t36d = "没申报" if "storyboard-fact-check" in str(e) else str(e)[:60]
check("T36d 反向对照：这一步【根本没申报手段】也拒（白名单不许被「沉默」绕过）", t36d, "没申报")

# 档位：--stage compile 必须【不进执行器】，且两条款都算合法出口
_sfw = io.open(os.path.join(BACKEND, r"services\super_clone_wan3.py"), encoding="utf-8").read()
_i_stop = _sfw.index('stop_after not in (None, "", "compile")')
_i_exec = _sfw.index("step2 = await _run_executor(")
check("T36e 只编译档位：参数校验在调任何模型之前（拼错的档位不许静默当成「照常提交」）；"
      "两处提交腿各有一条 compile-only 早退（多段循环 + 单段），少一处会红",
      [0 < _i_stop < _i_exec, _sfw.count('stop_after == "compile"') >= 3,
       _sfw.count("compiled_only") >= 6], [True, True, True])
check("T36f 覆盖对照：两条提交路径都调 _contract_evidence（少一处 = 那个运行形状没有物证，@4 那次事故）",
      _sfw.count("_contract_evidence("), 3)   # 1 定义 + 2 调用

shutil.rmtree(TMP, ignore_errors=True)
# ── N 组：改口播【直出】那一格（2026-10-14 @11）───────────────────────────────
# 钉的是"客户的改稿真的走到了 prompt 的台词位"，不是"参数收到了"。全程本地字符串对账，0 消耗。
if _svc:
    _nd = tempfile.mkdtemp(prefix="nd-")

    def _sb():
        return [{"shot": 1, "时间区间": {"开始时间": 0, "结束时间": 3}, "口播": "原片第一句很长的一段话"},
                {"shot": 2, "时间区间": {"开始时间": 3, "结束时间": 7}, "口播": "原片第二句也很长一段话"}]

    _NEW = "新稿第一句要换掉它\n新稿第二句也要换掉它"
    _r1 = _svc.apply_customer_narration(_sb(), _NEW, _nd)
    check("N1 真函数：新稿灌进各镜口播位，覆盖率与残留双向都是 1",
          [_r1["coverage"], _r1["stale"], _r1["problems"],
           os.path.isfile(os.path.join(_nd, "voice_script_binding.json"))],
          [1.0, 1.0, [], True])
    check("N2 空稿 = 什么都不做（返回 None，不许把「没给稿」写成「已按新稿改好」）",
          _svc.apply_customer_narration(_sb(), "   ", _nd) is None, True)
    try:
        _svc.apply_customer_narration([{"shot": 1, "时间区间": {"开始时间": 0, "结束时间": 3},
                                        "口播": "无"}], "有稿", _nd)
        _n3 = "没拒"
    except RuntimeError as _e:
        _n3 = "拒" if "全空" in str(_e) else ("拒因不对:" + str(_e)[:40])
    check("N3 口播位全空 ⇒ 拒（没有原句就没有对齐底子，硬塞第一镜是蒙）", _n3, "拒")
    _r4 = _svc.apply_customer_narration(_sb(), "新稿第一句要换掉它新稿第二句也要换掉它再加一段更长的", _nd)
    check("N4 新稿比原稿长也必须全覆盖（漏字就是漏词，不是差不多）",
          [_r4["coverage"], _r4["stale"]], [1.0, 1.0])
    check("N5 纯判据值表：空格与换行不算差异、缺字要掉比例",
          [_svc.narration_coverage("甲 乙\n丙", "甲乙丙"), _svc.narration_coverage("甲乙丙", "甲乙"),
           _svc.narration_coverage("", "随便")], [1.0, 0.6667, 1.0])
    # 🔴 现网形状（2026-10-14 抓到）：ASR 7 段、客户稿 6 行 ⇒ 字符 diff 让最后一镜没分到字，
    #    盘上出现"第 7 镜还在说原片旧词"（残留 19.3%）。按镜时长占比切分必须双向都满、拼回=原稿。
    _uneven = [{"shot": i + 1, "时间区间": {"开始时间": a, "结束时间": a + d},
                "口播": "原片第%d句大约这么长的一段话" % (i + 1)}
               for i, (a, d) in enumerate([(0.0, 3.1), (3.1, 2.2), (5.3, 1.4), (6.7, 2.9),
                                           (9.6, 1.8), (11.4, 2.0), (13.4, 1.6)])]
    _NL = chr(10)
    _SIX = _NL.join(["第一行台词稍长一些用来占两个镜的时长", "第二行", "第三行也长一点凑凑字数",
                     "第四行", "第五行", "第六行收尾"])
    _r_uneven = _svc.apply_customer_narration(_uneven, _SIX, _nd)
    _landed = "".join(x["口播"] for x in _uneven if x["口播"] != "无")
    check("N6 段数≠行数（现网那个形状：7 镜 vs 6 行）⇒ 双向必须满，拼回去就是客户那份稿",
          [_r_uneven["coverage"], _r_uneven["stale"], _svc.narration_coverage(_landed, _SIX),
           any(x["新口播"] == "无" for x in _r_uneven["shots"])],
          [1.0, 1.0, 1.0, True])
    check("N6b 切分函数本身：连续切片拼回必须等于原稿（顺序=客户稿行序，不许跳着拿）",
          "".join(_svc.split_script_by_durations("ABCDEFGHIJ" * 6, [3.0, 1.0, 2.0, 4.0])) == "ABCDEFGHIJ" * 6, True)
    # 🔴 变异（真变异，不是自我比对）：把干活的那一步换成空转 ⇒ 判据必须当场拒
    _real_avs = _svc.split_script_by_durations
    _svc.split_script_by_durations = lambda text, durs: [""] * len(durs)
    try:
        _svc.apply_customer_narration(_sb(), _NEW, _nd)
        _n6 = "空转也能过（判据没咬）"
    except RuntimeError as _e:
        _n6 = "拒" if "只落位" in str(_e) else ("拒因不对:" + str(_e)[:40])
    finally:
        _svc.split_script_by_durations = _real_avs
    check("N6c 变异：摘掉切分那一步 ⇒ 覆盖率判据必须拒（证明它不是白写的）", _n6, "拒")

    _l_call = next((i for i, l in enumerate(_svc_src.split(chr(10)), 1)
                    if "apply_customer_narration(storyboard" in l), 0)
    _l_asr = next((i for i, l in enumerate(_svc_src.split(chr(10)), 1)
                   if "FunASR 口播已覆盖 storyboard" in l), 0)
    _l_cmp = next((i for i, l in enumerate(_svc_src.split(chr(10)), 1)
                   if "Step 1.5/4: 编译器" in l), 0)
    check("N7 顺序：ASR 写回【之后】、编译之前（放在前面会被原片词盖掉 = 静默冒充）"
          "（锚点 2026-10-23 跟着日志改名换过一次：原来钉的是 `Step 1.5/4: Grok 编译器`，"
          "那条腿早就不跑 Grok ⇒ 打厂商标会对人撒谎，改成中性前缀时这个锚点必须一起换）",
          [bool(_l_call) and _l_asr < _l_call < _l_cmp, _l_asr > 0, _l_cmp > 0], [True, True, True])
    check("N7b 编译器那几行日志里不许再出现厂商标（Grok/GPT 换过好几家，写死名字=账本外的一句谎）",
          [l for l in _svc_src.split(chr(10))
           if "Step 1.5/4" in l and ("Grok" in l or "GPT" in l)], [])
    check("N8 一段一条稿：整条自动切多段时带着整稿 ⇒ 点名拒（每段各灌一遍=每段都念整稿）",
          ("if voice_script and len(windows) > 1" in _svc_src and "逐段跑" in _svc_src), True)
    check("N9 覆盖对照：两条提交路径都带 narration_binding（少一处=那种运行形状没这一格）",
          _svc_src.count("narration_binding=str(Path(output_dir)"), 2)
    check("N9b 入口把那格接到 run_pipeline 的 voice_script（不接=这根线在入口就断）",
          'voice_script=str(vals.get("voice_script")' in _run_src, True)
    _st_nd = next((s for s in (c.get("steps") or []) if s.get("id") == "narration-direct"), {})
    check("N10 合同里有 narration-direct 那一格：手段/物证/非必需",
          [_st_nd.get("impl"), _st_nd.get("proof", "")[:20], _st_nd.get("required")],
          ["pipeline:apply_customer_narration", "voice_script_binding", False])
    # 真调物证写者：带物证 ⇒ 当场打钩且模型名=不用模型；不带 ⇒ 那一格必须还是 ⬜
    os.makedirs(_SEG, exist_ok=True)          # T34 那一组可能已经清掉了，这一组自己建
    os.makedirs(_t34_dir, exist_ok=True)
    # 🔴 夹具要自己重建：T34 收尾把 ref/产品图与台账都清过，素材读不到时 _contract_evidence
    #    会在【对账那一刀】就抛 ⇒ 这里拿到的错和"直出这一格"无关，会把好判据演成假红。
    open(_ref, "wb").write(b"fake-video")
    open(_pro, "wb").write(b"fake-product")
    os.environ["WEEKSIR_MEDIA_ANALYSIS_LOG"] = os.path.join(_t34_dir, "ledger.json")
    _g34.record_media_analysis(_pro, "video.analyze", "default", session="", hold_id="h-N")
    _bind = os.path.join(_SEG, "voice_script_binding.json")
    io.open(_bind, "w", encoding="utf-8").write(json.dumps(
        {"coverage": 1.0, "stale": 1.0, "problems": [], "shots": []}, ensure_ascii=False))
    _g34.invoke = lambda *a, **k: {"ok": False, "credits": {"estimated": 0, "charged": 0},
                                   "error": "闸里不许真调上游（N 组桩）"}

    def _row(b):
        try:
            _svc._contract_evidence(_SEG, seg_board=REAL_SHOTS,
                                    seg_prompt="…不得出现字幕、花字、水印…", compiled_seg=_stamp,
                                    seg_duration=10.0, ref_video=_ref, seg_product_paths=[_pro],
                                    source_fit=_ref, narration_binding=b, log=None)
            _rc = json.load(io.open(os.path.join(_SEG, ".skill_receipt.json"), encoding="utf-8"))
            return next((x for x in _rc.get("steps") or []
                         if x.get("id") == "narration-direct"), {})
        except Exception as _e:                                # noqa: BLE001
            _row_err.append("%s: %s" % (type(_e).__name__, str(_e)[:150]))
            return {"_err": str(_e)[:150]}
    _row_err = []
    _with = _row(_bind)
    check("N11 带了物证 ⇒ 那一格打上钩、模型名写「不用模型」（本地对账步不许留空冒充）｜%s"
          % ("；".join(_row_err) or "no-err"),
          [_with.get("ok"), _with.get("model")], [True, sc.NO_MODEL])
    _without = _row(os.path.join(_nd, "nope.json"))
    check("N12 没带物证 ⇒ 那一格必须还是未钩（不许拿上一次的钩冒充这次做过）",
          [_without.get("ok"), _without.get("id", "narration-direct")], [False, "narration-direct"])
    _g34.invoke = _real34                                       # 🔴 桩必须还原：留着会让后面的组全部走空
    check("N12b 桩还原了（invoke 又变回真函数 = 闸自己不带着假腿跑完）",
          _g34.invoke is _real34, True)
    shutil.rmtree(_nd, ignore_errors=True)
    check("N13 收尾：这一组的临时目录真的没了（闸自己不许留残留）",
          os.path.isdir(_nd), False)
    check("N14 直出与换轨互斥：物证在 ⇒ apply_narration_swap 早退（再换一次=一条片子两条声+重复花克隆钱）",
          ["_nd = direct_narration_done(video_path)" in _run_src,
           "不再走换轨" in _run_src,
           _run_src.count('if os.path.isfile(cand)')], [True, True, 1])
    check("N14b 半截的物证不算数：coverage<0.999 或有 problems ⇒ 还会走换轨（不许拿旧文件冒充做过）",
          'float(rec.get("coverage") or 0) >= 0.999 and not (rec.get("problems") or [])' in _run_src, True)
    check("N15 口型那一刀与【有没有换轨】无关：两个交付口都拿 换轨片或直出片 去对齐（少一处=那种形状没口型）",
          [_run_src.count("_lip = apply_lip_sync(_sw or _film"), _run_src.count("_tgt = _sw or path"),
           _run_src.count("_lip = apply_lip_sync(_tgt")], [1, 1, 1])
    # N16–N18：口型「重画哪几段」这份窗口是谁算出来的（2026-10-14 现网：15s 里只有 0–4.8s 有正脸，
    # 而上一版只读 TTS 换轨台账 ⇒ 客户整稿音频那种形状读不到 ⇒ 静默退成整条模式，
    # 等于花 20 分让 LatentSync 去重画手和产品特写）。fixture 自己写，不依赖 productions/ 还在。
    _ld = tempfile.mkdtemp(prefix="lipseg-")
    def _ls(shots, swap=None, sub=""):
        d = os.path.join(_ld, sub or "s")
        os.makedirs(d, exist_ok=True)
        f = os.path.join(d, "film.mp4")
        io.open(f, "w").close()
        if shots is not None:
            io.open(os.path.join(d, "storyboard.json"), "w", encoding="utf-8").write(
                json.dumps(shots, ensure_ascii=False))
        if swap is not None:
            io.open(os.path.join(d, "narration_swap.json"), "w", encoding="utf-8").write(
                json.dumps(swap, ensure_ascii=False))
        return f
    def _sh(a, b, look):
        return {"时间区间": {"开始时间": a, "结束时间": b}, "面部微表情": {"眼神方向": look}}
    _mixed = _ls([_sh(0.0, "00:03.00", "直视镜头"), _sh(3.0, 4.8, "直视镜头"),
                  _sh(4.8, 9.0, "未入镜"), _sh(9.0, 15.0, "看不清")])
    check("N16a 有正脸+没读到说话台账 ⇒ 只交正脸窗口（mm:ss 也要能解析）",
          _m._lip_segments(_mixed), ([{"start": 0.0, "end": 3.0}, {"start": 3.0, "end": 4.8}], "face"))
    check("N16b 逐镜都写未入镜 ⇒ no-face（不是『读不到所以整条送』）",
          _m._lip_segments(_ls([_sh(0.0, 5.0, "未入镜")], sub="nf")), ([], "no-face"))
    check("N16c 有台账 ⇒ 与正脸窗口取交集（嘴不在画面上的那段绝不重画）",
          _m._lip_segments(_ls([_sh(0.0, 5.0, "直视镜头")], {"rows": [{"start": 4.0, "end": 12.0}]},
                             sub="bo")), ([{"start": 4.0, "end": 5.0}], "face+speech"))
    check("N16d 两份事实源都没有 ⇒ unknown（调用方才允许退成整条，且要出声）",
          _m._lip_segments(os.path.join(_ld, "nothing", "x.mp4")), ([], "unknown"))
    check("N16e 反向对照：同一份 fixture 把『未入镜』改成有表情 ⇒ 窗口必须出现（证明那一格真在读它）",
          _m._lip_segments(_ls([_sh(0.0, 5.0, "直视镜头")], sub="rv"))[1], "face")
    check("N17 no-face 是【不做这一刀】不是退成整条：入口先判 how 再 invoke",
          ['segs, how = _lip_segments(src)' in _run_src,
           'if how == "no-face":' in _run_src,
           _run_src.index('if how == "no-face":') < _run_src.index("env = gw.invoke(LIPSYNC_CAP")],
          [True, True, True])
    check("N18 旧那条只读 TTS 台账的近路已封（读不到就静默整条=白花钱），且物证记下窗口是怎么定的",
          [_run_src.count("_speech_segments("), "_segments_from" in _run_src or '"segments_from": how' in _run_src],
          [0, True])
    shutil.rmtree(_ld, ignore_errors=True)
    check("N18b 收尾：这一组的临时目录真的没了", os.path.isdir(_ld), False)
    # ---- CP 组：编译产物缓存（2026-10-14 需求单 1+2「一次编译、多次复用」）----
    # fixture 自己造，不读 productions/ 里的真产物（那条会被清理 ⇒ 闸随机假红）
    _cp_facts = {"trim_start": 16.0, "trim_end": 30.0, "target_duration": 14.0,
                 "resolution": None, "aspect_ratio": "3:4", "voice_mode": None,
                 "voice_script": "A", "extra_note": "", "customer_request": "R",
                 "product_name": "P", "media_manifest": [], "source_sha8": "704e22bc",
                 "prompt_rev": "rev-fine"}
    _cp_fp = _svc.compile_fingerprint(_cp_facts)
    _cp_d = tempfile.mkdtemp(prefix="cp-")

    def _cp_build(prompt="X" * 40, req_ok=True, fp=None):
        io.open(os.path.join(_cp_d, "prompt.txt"), "w", encoding="utf-8").write(prompt)
        io.open(os.path.join(_cp_d, "storyboard.json"), "w", encoding="utf-8").write('[{"shot":1}]')
        json.dump({"skill": "super-clone", "contract_version": "super-clone-wan3@12",
                   "steps": [{"id": "storyboard", "required": True, "ok": req_ok},
                             {"id": "lip-sync", "required": False, "ok": False}]},
                  io.open(os.path.join(_cp_d, ".skill_receipt.json"), "w", encoding="utf-8"))
        json.dump({"compile_fp": (_cp_fp if fp is None else fp),
                   "storyboard_sha8": sc.sha8(os.path.join(_cp_d, "storyboard.json")),
                   "prompt_sha8": sc.sha8(os.path.join(_cp_d, "prompt.txt"))},
                  io.open(os.path.join(_cp_d, "prompt_meta.json"), "w", encoding="utf-8"))
        return _cp_d

    _cp_hit = _svc.compile_cache_state(_cp_build(), _cp_facts)
    check("CP1 三件齐（指纹同 + 物证 sha8 对得上 + 必需步全勾）⇒ 命中",
          [_cp_hit[0], _cp_hit[1].startswith("命中")], [True, True])
    _cp_diff = [_svc.compile_cache_state(_cp_build(), dict(_cp_facts, voice_script="B"))[0],
                _svc.compile_cache_state(_cp_build(), dict(_cp_facts, trim_end=29.0))[0],
                _svc.compile_cache_state(_cp_build(), dict(_cp_facts, extra_note="改动作"))[0],
                _svc.compile_cache_state(_cp_build(), dict(_cp_facts, source_sha8="deadbeef"))[0],
                _svc.compile_cache_state(_cp_build(), dict(_cp_facts, prompt_rev="rev-coarse"))[0]]
    check("CP2 改稿/改窗口/改 extra-note/换原片/换提示词版 任一 ⇒ 全部不命中（必须重编）", _cp_diff, [False] * 5)
    _cp_build()
    io.open(os.path.join(_cp_d, "prompt.txt"), "w", encoding="utf-8").write("Y" * 40)
    _cp_drift = _svc.compile_cache_state(_cp_d, _cp_facts)
    check("CP3 🔴 物证漂移：prompt.txt 被偷偷改了 ⇒ 不命中，而【只比指纹会命中】（证明那两行 sha8 在管）",
          [_cp_drift[0], "被改过" in _cp_drift[1], _svc.compile_fingerprint(_cp_facts) == _cp_fp],
          [False, True, True])
    check("CP4 回执缺格 ⇒ 不命中（缺=那次没走完，不许复用）",
          _svc.compile_cache_state(_cp_build(req_ok=False), _cp_facts)[0], False)
    io.open(os.path.join(_cp_d, ".skill_receipt.json"), "w", encoding="utf-8").write('[{"id":"x"}]')
    check("CP4b 回执形状不对（顶层是列表）⇒ 判【不命中】而不是把异常抛出去（闸崩≠闸红、判据不许成为故障点）",
          _svc.compile_cache_state(_cp_d, _cp_facts)[0], False)
    check("CP5 旧版本产物（没写 compile_fp）⇒ 不命中，fail-closed",
          _svc.compile_cache_state(_cp_build(fp=""), _cp_facts)[0], False)
    check("CP6 目录里没有产物 ⇒ 不命中",
          _svc.compile_cache_state(os.path.join(_cp_d, "nope"), _cp_facts)[0], False)
    _cp7 = [_scw.find("if reuse_compile and not _cc_hit:"),
            _scw.find("_cc_use = bool(_cc_hit and reuse_compile)"),
            _scw.find("await step1_mimo_director(")]
    check("CP7 接线：复用判定（【命没命中】+【这一档要不要复用】两步都算）必须排在 MiMo 真调用点之前，"
          "且 --reuse-compile 不命中就当场拒（判定搬到解析之后 = 判定白做、token 已经烧了）",
          [all(i >= 0 for i in _cp7), _cp7[0] < _cp7[1] < _cp7[2]], [True, True])
    check("CP8 命中时不重灌口播、不重跑编译器，且【只有单段路径】写指纹（多段不写=永不复用，宁可重编）",
          ["跳过 apply_customer_narration" in _scw, _scw.count("compile_facts=_cc_facts"),
           'compiled = {"prompt": prompt,' in _scw],
          [True, 1, True])
    shutil.rmtree(_cp_d, ignore_errors=True)
    check("CP9 收尾：这一组的临时目录真的没了", os.path.isdir(_cp_d), False)
    def _cp10_ok(_src):
        _i = _src.find("reuse_compile=(")
        return ('add_argument("--reuse-compile"' in _src and
                'add_argument("--reuse-compile-if-hit"' in _src and
                _src.count("reuse_compile=(") == 1 and
                'a.stage != "compile"' in _src[_i:_i + 260])
    check("CP10 入口面：硬档 --reuse-compile 与软档 --reuse-compile-if-hit 都在，且 --stage compile 永远不复用"
          "（那一档的语义就是「把编译走一遍」，让它命中 = 把重编译的合法出口做成假出口）",
          [_cp10_ok(_run_src),
           _cp10_ok(_run_src.replace('and a.stage != "compile"),', "),", 1))],
          [True, False])
    # CP11 🔴 抓"函数里用了某个名字、但那个名字在这个模块根本没有模块层导入"这一整族。
    #      同一个文件为这件事崩过三次：wan3_client 缺 `import os`、同处缺 `import json`、
    #      10-14 深夜我加的指纹缓存在 run_pipeline 里用 `_skc.sha8` 而 `_skc` 是【函数内延迟导入】
    #      ⇒ 10-15 09:05 那次【批准即点火】起的提交腿三段全死在 NameError，人批了、系统没投。
    import ast as _ast_cp
    _tree_cp = _ast_cp.parse(_scw)
    _mod_names = set()
    for _n in _tree_cp.body:
        if isinstance(_n, _ast_cp.Import):
            _mod_names |= {(_a.asname or _a.name).split(".")[0] for _a in _n.names}
        elif isinstance(_n, _ast_cp.ImportFrom):
            _mod_names |= {(_a.asname or _a.name) for _a in _n.names}
    _bad_cp = []
    for _fn in [n for n in _tree_cp.body
                if isinstance(n, (_ast_cp.FunctionDef, _ast_cp.AsyncFunctionDef))]:
        _loc = set()
        for _g in _ast_cp.walk(_fn):
            if isinstance(_g, _ast_cp.ImportFrom):
                _loc |= {(_a.asname or _a.name) for _a in _g.names}
            elif isinstance(_g, _ast_cp.Import):
                _loc |= {(_a.asname or _a.name).split(".")[0] for _a in _g.names}
        _used = {g.id for g in _ast_cp.walk(_fn)
                 if isinstance(g, _ast_cp.Name) and isinstance(g.ctx, _ast_cp.Load)}
        for _nm in sorted((_used - _mod_names - _loc) & {"_skc", "_skc2", "hashlib", "os", "json", "re"}):
            _bad_cp.append("%s() 用了 %s 但没有本地/模块层导入" % (_fn.name, _nm))
    check("CP11 🔴 名字必须有主人：函数里 Load 到的 `_skc/os/json/re/hashlib` 必须能在模块层或本函数里找到导入"
          "（缺一个就是运行到那一行才 NameError —— 而那一行常常在花钱之后、或在批准点火那条腿上）",
          _bad_cp, [])

    def _cp11_scan(_src_text):
        _t = _ast_cp.parse(_src_text)
        _m = set()
        for _n in _t.body:
            if isinstance(_n, _ast_cp.Import):
                _m |= {(_a.asname or _a.name).split(".")[0] for _a in _n.names}
            elif isinstance(_n, _ast_cp.ImportFrom):
                _m |= {(_a.asname or _a.name) for _a in _n.names}
        _bad = []
        for _f in [n for n in _t.body if isinstance(n, (_ast_cp.FunctionDef, _ast_cp.AsyncFunctionDef))]:
            _l = set()
            for _g in _ast_cp.walk(_f):
                if isinstance(_g, _ast_cp.ImportFrom):
                    _l |= {(_a.asname or _a.name) for _a in _g.names}
                elif isinstance(_g, _ast_cp.Import):
                    _l |= {(_a.asname or _a.name).split(".")[0] for _a in _g.names}
            _u = {g.id for g in _ast_cp.walk(_f)
                  if isinstance(g, _ast_cp.Name) and isinstance(g.ctx, _ast_cp.Load)}
            _bad += ["%s:%s" % (_f.name, x) for x in sorted((_u - _m - _l) & {"_skc", "os", "json"})]
        return _bad

    # 反向对照：把我这次补的那句 import 摘掉 ⇒ 必须重新抓到 run_pipeline 里的 _skc（否则 CP11 是空跑）
    check("CP11b 反向对照：摘掉 run_pipeline 里那句 `import skill_contract as _skc` ⇒ 必须立刻抓到"
          "（证明这条不是写完就恒绿的摆设）",
          _cp11_scan(_scw.replace("    from backend import skill_contract as _skc\n    _cc_facts = {",
                                  "    _cc_facts = {", 1)), ["run_pipeline:_skc"])

    # ---- PR 组：提示词【输出粒度】只有一档（2026-10-23 用户定）----
    # 为什么这一组必须存在：那天给编译腿加了收紧节之后重编译，产出 sha8 一字未变，
    #   而盘上没有任何一条闸能看见"改了系统提示词却没进模型"这件事 ⇒ 遵守散文靠不住，只能上闸。
    # 2026-10-23 晚用户再定："怎么还搞一个粗档和细档，直接按粗档不就好了" ⇒ fine/coarse 开关整条删除。
    #   这一组因此从"两档各自对不对"改成"只有一档、且没有第二个入口"。
    _pr_settings = _svc.settings
    _pr_sb_args = (14.0, _svc._cfg()["mimo_split_mode"])
    check("PR0 开关整条不存在：模块上没有 prompt_granularity()、config 没有那个字段、.env 也没有这行"
          "（一个只有默认值的开关不是配置，是第二个入口）",
          [hasattr(_svc, "prompt_granularity"),
           hasattr(_pr_settings, "REPLICA_WAN3_PROMPT_GRANULARITY"),
           any(l.startswith("REPLICA_WAN3_PROMPT_GRANULARITY=")
               for l in io.open(os.path.join(BACKEND, r".env"), encoding="utf-8", errors="replace"))],
          [False, False, False])
    _pr_p = (_svc.step1_5_system_prompt(), _svc._storyboard_system_prompt(*_pr_sb_args))
    check("PR1 两条腿【都】无条件带上收紧节 —— 只改编译腿不够（散文是从分镜腿灌进来的）",
          [("【输出粒度收紧" in _pr_p[0]), ("【超级复刻·输出粒度收紧" in _pr_p[1])], [True, True])
    check("PR2 收紧节是追加不是替换：上面【强制输出语法】/【唯一输出 Schema】还在，"
          "且收紧节排在它们【之后】（分镜腿基座没有模块级常量，所以比先后而不是比长度）",
          [("【强制输出语法】" in _pr_p[0]), ("【唯一输出 Schema】" in _pr_p[1]),
           len(_pr_p[0]) > len(_svc.STEP1_5_SYSTEM_PROMPT),
           0 < _pr_p[1].find("【唯一输出 Schema】") < _pr_p[1].find("【超级复刻·输出粒度收紧")],
          [True, True, True, True])
    # 反向对照（哨兵）：证明"带上了"读的是那两处拼接本身，不是某句常驻文本 —— 换掉常量，产出必须跟着换。
    _pr_c1, _pr_c2 = _svc.COMPILE_COMPACT_BLOCK, _svc.STORYBOARD_COMPACT_BLOCK
    try:
        _svc.COMPILE_COMPACT_BLOCK = "\nPR-SENTINEL-A\n"
        _svc.STORYBOARD_COMPACT_BLOCK = "\nPR-SENTINEL-B\n"
        check("PR2b 反向对照：把两节常量换成哨兵 ⇒ 提示词里出现的必须是哨兵，且整体变短"
              "（若这条还绿着=PR1 是恒绿的摆设）",
              [("PR-SENTINEL-A" in _svc.step1_5_system_prompt()),
               ("PR-SENTINEL-B" in _svc._storyboard_system_prompt(*_pr_sb_args)),
               len(_svc._storyboard_system_prompt(*_pr_sb_args)) < len(_pr_p[1])], [True, True, True])
        _pr_rev_sent = _svc.prompt_revision(*_pr_sb_args)
    finally:
        _svc.COMPILE_COMPACT_BLOCK, _svc.STORYBOARD_COMPACT_BLOCK = _pr_c1, _pr_c2
    check("PR6 prompt_rev 必须真的由两条腿当场要发的提示词算出来：换哨兵 ⇒ rev 必须换"
          "（不然改了系统提示词却仍被缓存当成同一份输入、直接复用旧稿 —— 那天现网就是这个形状）",
          bool(_pr_rev_sent) and _pr_rev_sent != _svc.prompt_revision(*_pr_sb_args), True)
    # 收紧档不许松的四类事实：逐字钉在该那一行上（关键词断言必须绑到具体那一行）
    _pr_kept = ["「口播」：逐字", '不可见仍严格填"无"', "只能填 强/中/弱/无",
                "不改 Schema、不改 Shot 数、不改时间轴、不改任何判定值",
                "动作真实存在就不许为了省字填", "所有叶子字段仍然必须是非空字符串", "≤150 字"]
    check("PR3 收紧只压修饰语：判定格（口播逐字/产品无/情绪枚举/Schema 与时间轴）一条都不许松",
          [k in _svc.STORYBOARD_COMPACT_BLOCK for k in _pr_kept], [True] * len(_pr_kept))
    check("PR4 自定义分镜提示词会让 user 消息里那两句消失 ⇒ target_duration 与末镜覆盖必须补回来"
          "（Checkpoint A 靠它，收紧节不许把它挤掉）",
          [("target_duration = 14.00 秒" in _pr_p[1]), ("必须等于 14.00 秒" in _pr_p[1]),
           (0 <= _pr_p[1].find("【超级复刻·调用侧补强】") < _pr_p[1].find("【超级复刻·输出粒度收紧"))],
          [True, True, True])
    check("PR5 声音六项不再逐镜塞进 prompt（这一刀在后端 force_verbatim_speech，不在提示词里）",
          [_svc._voice_block_from_shot({"声音语气": {"语速": "中等", "情绪": "兴奋"}}),
           "语速=" in _svc._voice_block_from_shot({"声音语气": {"语速": "中等"}})],
          ["[character1]", False])
    check("PR6b 🔴 这条陷阱钉住：`skill_contract.sha8` 的入参是【文件路径】，喂正文只会恒回 unreadable"
          "（谁把 prompt_rev 改回用它 = 指纹变成常数、改版看不见）",
          [sc.sha8("这是一段正文不是路径"), sc.sha8(os.path.abspath(__file__)) != "unreadable"],
          ["unreadable", True])
    check("PR7 接线：prompt_rev 既在指纹键名单里、又在 _cc_facts 里真给了（缺一处 = 算了个没人读的数）",
          [("prompt_rev" in _svc._COMPILE_FP_KEYS), '"prompt_rev": prompt_revision(' in _scw], [True, True])
    # 🔴 抽帧补充腿（2026-10-30 用户定"改成这样的流程"）：它的产出会影响 prompt ⇒ 同一条规矩必须也落在它身上。
    #    两个方向分开钉：① 键在名单里 + ② 那份 dict 真给了值（只给键不给值 = 指纹永远是 None，
    #    换了事实照样命中缓存、拿旧 prompt 去花 60 分 —— 和 10-23 那次 prompt_rev 一模一样的形状）。
    #    ③ 两个编译出口都得带上这条腿的事实（单段 + 分段）：少一处 = 从那一口出的片子永远不补实物细节。
    #    ④ 整条方案只许花一次：真跑那一句 `frame_facts_pass(` 在 run_pipeline 里只能出现 1 次
    #    （分段编译复用同一份事实 ⇒ 出现第 2 次就是 40s 的片白烧 3×178 分）。
    check("PR9 抽帧补充腿接线：键在指纹名单 + _cc_facts 真给值 + 两个编译出口都带 + 只跑一次",
          [("frame_facts" in _svc._COMPILE_FP_KEYS),
           '"frame_facts": str(_frame_facts or "")' in _scw,
           _scw.count("frame_facts=_frame_facts,"),
           _scw.count("= frame_facts_pass(")],
          [True, True, 2, 1])
    check("PR9b 反向对照：把指纹里那个键摘掉 ⇒ 上面那条必须立刻抓到（证明钉的是接线不是关键词）",
          [("frame_facts" in _svc._COMPILE_FP_KEYS),
           '"frame_facts": str(_frame_facts or "")' in
           _scw.replace('"frame_facts": str(_frame_facts or "")', "", 1)],
          [True, False])
    check("PR8 唯一出口：两条腿各只有一处把提示词交出去（不许在调用点再拼一份第二事实源）",
          [_scw.count('"content": step1_5_system_prompt(),'),
           _scw.count("system_prompt=_storyboard_system_prompt("),
           _scw.count("STEP1_5_SYSTEM_PROMPT +"), _scw.count("return SYSTEM_PROMPT +")],
          [1, 1, 1, 1])

    # ---- MG 组：粗档的【装配口】（全局声明由后端写一次，不逐镜让模型重写）----
    # 为什么单独一组：2026-10-23 A/B 实测粗档逐镜行 153 字/镜（比细档短 34%），总长却从 2291 涨到 3124 ——
    # 8 次逐镜调用各写一份措辞不同的全局块，合并层按整行去重合不掉。修法是"常量归代码"，
    # 那就必须有闸盯着"它真的只出现一份"，否则下一次漂移还是靠人肉数字发现。
    _mg_shots = ["Shot 1 [0.0-00:02.00]: 特写, 双手托玻璃碟; \"台词一\" [character1];\n"
                 "全局替换声明：原片的一切商品替换为图1。\n" + _svc.STEP1_5_GLOBAL_CONSTRAINT,
                 "Shot 2 [2.0-00:04.00]: 特写, 倾倒油液; \"台词二\" [character1]; Hard cut transition;\n"
                 "全局：原片的一切商品一律替换为图1。\n" + _svc.STEP1_5_GLOBAL_CONSTRAINT]
    _mg_glob = _svc.compact_global_block("植小妞 保湿盈润护理油",
                                        [{"image_id": "图3", "type": "product"},
                                         {"image_id": "图5", "type": "model"}])
    _mg_coarse = _svc.merge_compiled_shot_prompts(_mg_shots, compact_global=_mg_glob)
    _mg_fine = _svc.merge_compiled_shot_prompts(_mg_shots)
    check("MG1 粗档装配：全局块只有一份、Shot 行不丢、最后一行仍是那句固定约束（逐字相等）",
          [_mg_coarse.count("【全局替换】"), sum(1 for l in _mg_coarse.splitlines() if l.startswith("Shot")),
           _mg_coarse.splitlines()[-1] == _svc.STEP1_5_GLOBAL_CONSTRAINT,
           "替换为图1" not in _mg_coarse.replace(_mg_glob, "")],
          [1, 2, True, True])
    check("MG2 反向对照：不传 compact_global ⇒ 两份全局块必须原样保留"
          "（钉住合并函数的另一支没被这次改动误伤 —— 生产两处调用都给，这一支只给单测）",
          [sum(1 for l in _mg_fine.splitlines() if l.startswith("Shot")),
           _mg_fine.count("全局"), _mg_fine.splitlines()[-1] == _svc.STEP1_5_GLOBAL_CONSTRAINT],
          [2, 2, True])
    check("MG3 图N 现读 media_manifest，不硬编码图1/图2（编号归属是 Step 0 定的，写死就会漂）",
          [("图3" in _mg_glob) and ("图5" in _mg_glob) and ("图1" not in _mg_glob),
           "植小妞 保湿盈润护理油" in _mg_glob], [True, True])
    _mg_voice = {"声音语气": {"语速": "中等", "语调": "轻快平稳", "重音": "关键词重读",
                              "停顿": "短停顿", "情绪": "中", "呼吸感": "自然呼吸"}}
    # 原来这一条是"两向"（粗档只回 [character1]、细档六项照旧）。开关整条删除后没有第二向，
    # 判据改成【钉死单向】：不管分镜里写了多少声音属性，后端都不许把那六项塞回 prompt。
    _mg_v = _svc._voice_block_from_shot(_mg_voice)
    check("MG4 声音六项一律不进 prompt（这一刀在后端，提示词管不到它）",
          [_mg_v, any(k in _mg_v for k in ("语速", "语调", "重音", "停顿", "情绪", "呼吸感"))],
          ["[character1]", False])
    check("MG5 提示词与装配口对齐：1.5 明写「全局声明你不要写」，且两个调用点（多镜/单镜）都接了 compact_global_block",
          [("你不要写" in _svc.COMPILE_COMPACT_BLOCK), _scw.count("compact_global_block(product_name, media_manifest")],
          [True, 2])
    check("MG6 漏镜兜底：粗档过滤只认行首 Shot，且装配后紧跟时间轴逐镜校验（缺镜必须抛，不许静默少一镜）",
          ['lines = [l for l in lines if l.startswith("Shot")]' in _scw,
           0 <= _scw.find("compact_global_block(product_name, media_manifest")
           < _scw.find("prompt = force_verbatim_speech(prompt, storyboard)")],
          [True, True])
    # MG7 去文字水印这句【必须同时有负向和正向填充】：2026-10-14 实测原片字幕是被 wan3 重新渲染成伪文字的，
    #     只写"不得出现字幕"压不住（它照样补一堆乱码笔画）⇒ 钉住"填满 + 不许黑块/马赛克 + 不许伪文字"三样。
    _mg_tail = _svc.STEP1_5_GLOBAL_CONSTRAINT
    check("MG7 后置约束那句去文字水印：负向（不得出现字幕/水印）+ 正向（区域用连续画面填满）+ 反伪文字三样齐",
          [all(k in _mg_tail for k in ("不得出现字幕", "水印", "用连续的真实画面内容填满",
                                       "马赛克", "伪文字")),
           "不得出现字幕" in _mg_tail and "水印" in _mg_tail],   # 合同判据 text-has= 认的两个词不许掉
          [True, True])
    check("MG7b 粗档全局块也带这句（后端写一次，不靠模型逐镜自觉）+ 逐镜行被明令不许抄原片屏幕文字",
          ["伪文字" in _mg_glob, "不许出现任何关于画面文字的描述" in _svc.COMPILE_COMPACT_BLOCK],
          [True, True])
    # ---- MG8/MG9：水印与画面文字这条规则【住在哪】----
    # 为什么单独钉：这条规则散在 5 个地方（1.0 补强、1.5 严禁事项、1.5 后置约束段、粗档全局块、粗档覆盖节）。
    # 2026-10-23 现网漂过两次：① 常量升级了、正文里那句"必须 exact match"的示例尾行还是旧短版
    # ② "禁止逐镜行写画面文字"只写在收紧节里 ⇒ 默认档没有这条。两处都是"改了 A 没改 B"。
    _sp8 = _svc.step1_5_system_prompt()
    _sb8 = _svc._storyboard_system_prompt(14.0, "scene_cut")
    check("MG8 1.5 正文的示例尾行由常量注入（占位符必须已被替换；正文与后端常量逐字同源）",
          ["{{GLOBAL_CONSTRAINT}}" not in _sp8,
           _svc.STEP1_5_GLOBAL_CONSTRAINT in _sp8,
           'STEP1_5_SYSTEM_PROMPT.replace("{{GLOBAL_CONSTRAINT}}", STEP1_5_GLOBAL_CONSTRAINT)' in _scw],
          [True, True, True])
    check("MG8b 去水印三样必须住在【主提示词】里（两档共用），不许只住在 COARSE 里（换档就丢规则）",
          [all(k in _sp8 for k in ("用连续的真实画面内容填满", "伪文字", "文字层不属于要继承的内容")),
           all(k in _svc.COMPILE_COMPACT_BLOCK or k in _sp8 for k in ("字幕", "水印")),
           ("用连续的真实画面内容填满" in _sp8.split("【输出粒度覆盖")[0])],
          [True, True, True])
    check("MG8c 1.0 分镜腿同一条规则在【调用侧补强】里（fine 档也有），且口播字段被明确豁免",
          ["不许抄画面上出现的文字内容" in _sb8, "「口播」字段不受此限" in _sb8,
           "不许抄画面上出现的文字内容" in _sb8.split("【超级复刻·粗档补强")[0]],
          [True, True, True])
    _mg9_p = ('Shot 1 [0.0-00:02.00]: 特写, 双手托碟; "它的基底只用植物油，" [character1];\n'
              'Shot 2 [2.0-00:04.00]: 特写, 屏幕下方字幕写着新上架; "台词" [character1];\n'
              'Shot 3 [4.0-00:06.00]: 特写, 画面带台标; "台词三" [character1];\n'
              'Shot 4 [6.0-00:08.00]: 特写, 双手揉推小腿; "屏幕上写着优惠" [character1];\n'
              + _svc.STEP1_5_GLOBAL_CONSTRAINT)
    check("MG9 编译期文本闸：逐镜行写了画面文字就点名重发；台词位与固定约束段里的『字幕/屏幕写着』不许算（防误伤口播）",
          [_svc.burned_text_lines(_mg9_p), _svc.burned_text_lines("Shot 1 [0.0-00:02.00]: 特写, 双手托碟; \"台词\" [character1];")],
          [[2, 3], []])
    check("MG9b 反向对照：把引号剥离那步去掉 ⇒ Shot 4 会被误判（证明判据真的在看非台词部分，不是关键词表扫全文）",
          bool(re.search(r"字幕|花字|水印", _mg9_p.split(chr(34))[3] or "")), False)

    # ---- MG10/MG11：提示词不许硬编码行业/性别/品类（这条腿要能跑 3C·食品·家居·工具·宠物）----
    _mg10 = _svc.compact_global_block("任意产品名", [{"image_id": "图1", "type": "product"},
                                                    {"image_id": "图2", "type": "model"}])
    check("MG10 后端全局块不写死性别、不写死穿戴：主体人物=模特，外观含『穿戴或手持』两种；示例品类不许进正文",
          ["女性" not in _mg10, "主体人物一律替换为模特" in _mg10, "手持" in _mg10,
           "任意产品名" in _mg10], [True, True, True, True])
    check("MG10b 粗档覆盖节与 1.5 正文都不留『图N 中的女性』这类性别化弱引用示例",
          ["女性" not in _svc.COMPILE_COMPACT_BLOCK,
           "图N 中的女性" not in _sp8, "图N 中的那个人" in _sp8], [True, True, True])
    check("MG10c 示例节必须自带『只演示格式、不是事实源』守卫（否则模型会把示例里的航空杯/露营桌抄进真实分镜）",
          "都不是事实源" in _sp8 and "只演示格式" in _sp8, True)
    _box_mid = [[330, 1075], [390, 1075], [390, 1110], [330, 1110]]      # 竖屏 720×1280 的水平居中字幕
    _y_real = _svc.classify_frame_text([("字幕内容", 0.99, _box_mid)], [], frame_size=(720, 1280))[0]
    _y_none = _svc.classify_frame_text([("字幕内容", 0.99, _box_mid)], [])[0]
    _y_wrong = _svc.classify_frame_text([("字幕内容", 0.99, _box_mid)], [], frame_size=(1920, 1080))[0]
    check("MG11 坐标按这一帧的真实分辨率归一：竖屏 720×1280 里水平居中的字幕 ⇒ x≈0.5、y≈0.854",
          [abs((_y_real["center"][0] or 0) - 0.5) < 0.01, abs((_y_real["center"][1] or 0) - 0.854) < 0.01],
          [True, True])
    check("MG11b 拿不到尺寸就输出 null（不猜）；传错尺寸就跟着错 ⇒ 证明归一化真在读 frame_size，不是写死的",
          [_y_none["center"] == [None, None], abs(_y_wrong["center"][0] - 0.1875) < 0.01], [True, True])
    _mg_keys = ("不得出现字幕", "水印", "用连续的真实画面内容填满", "马赛克", "伪文字")

    check("MG7c 反向对照：摘掉【正向填充】那半句，MG7 那组关键词必须凑不齐（证明钉的是实质不是关键词表）",
          all(k in _mg_tail.replace(
              "原片里被这些烧录文字占据的区域（画面下方与中部的横条、角落角标）必须用连续的真实画面内容填满：", "")
              for k in _mg_keys), False)
else:
    check("N0 super_clone_wan3 没导进来（整组没跑 = 假绿，直接红）: %s" % _imp_err, False, True)

print(_safe("\n=== skill 执行合同闸：%d/%d 通过 ===" % (len(PASS), len(PASS) + len(FAIL))))
for f in FAIL:
    print(_safe("  FAIL: " + str(f)))
sys.exit(1 if FAIL else 0)
