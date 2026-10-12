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

sys.path.insert(0, r"D:\video-skill")
sys.path.insert(0, r"C:\dsh\scripts")

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

WS = r"C:\dsh"

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
src = io.open(r"D:\video-skill\backend\services\super_clone_wan3.py", encoding="utf-8").read()
i_verify, i_exec = src.find("_skc.verify_or_raise("), src.find("last_result = await _run_executor(")
check("T12a 提交前调了 verify_or_raise", i_verify > 0, True)
check("T12b 顺序：verify 在 _run_executor 之前", 0 < i_verify < i_exec, True)
check("T12c 回执由管线写（不是让人手抄）", "write_receipt(" in src[:i_verify], True)
check("T12d 反向对照：verify 写在提交之后要能被抓到", i_exec < i_verify, False)
# 只断提交不断捞回：查询/下载腿里不许出现 verify
for fn in ("query_task", "poll_until_done", "download_result"):
    seg = io.open(r"D:\video-skill\backend\services\wan3_client.py", encoding="utf-8").read()
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
sc.contract_for = lambda s: cs_hole                       # 只桩"读 SKILL.md"这一层，被测的是形状闸本身
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
_cr = io.open(os.path.join("D:" + chr(92), "video-skill", "backend", "skill_contract.py"), encoding="utf-8", errors="replace").read()
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
                         inputs=[vid, prod], impl=dict(IMPL_OK), contract=_c)
check("T26b \u5408\u540c\u7248\u672c\u4e00\u53d8 \u21d2 \u65e7\u7f72\u540d\u4e0d\u7ee7\u627f\uff08\u6b63\u5411\u5bf9\u7167\uff0c\u9632\u201c\u6c38\u8fdc\u7ee7\u627f\u201d\u5f0f\u5047\u8d26\uff09",
      sorted({s["session"] for s in rec_c["steps"]}), ["sess-C"])
wt_b = sc.who_table(rec_b)
check("T27 \u6ca1\u505a\u7684\u6b65\u4e0d\u8bb8\u6302\u7740\u67d0\u4e2a\u4f1a\u8bdd\u7684\u540d\u5b57\uff08\u274c \u884c\u8d23\u4efb\u5217\u5199\u201c\u8fd9\u6b65\u6ca1\u505a\u201d\uff09",
      ("\u2014\uff08\u8fd9\u6b65\u6ca1\u505a\uff09" in wt_b) and ("sess-C" not in wt_b), True)
os.environ.pop("DSH_SESSION_ID", None)
os.environ.pop(sc.CTX_ENV, None)
sc.set_ctx("super-clone", task, session="sess-D")                            # \u5e38\u9a7b\u8fdb\u7a0b\u6ca1\u6709 DSH_SESSION_ID
check("T28 \u5165\u53e3\u6ca1 DSH_SESSION_ID \u4f46 set_ctx \u663e\u5f0f\u5e26\u4f1a\u8bdd \u21d2 \u7167\u90a3\u4e2a\u7b7e\uff08\u4e5f\u5141\u8bb8\u670d\u52a1\u7aef\u63a5\u529b\uff09",
      sorted({s["session"] for s in make_receipt()["steps"]}), ["sess-D"])
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
_scw = io.open(os.path.join(r"D:" + chr(92), "video-skill", "backend", "services", "super_clone_wan3.py"),
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

# ---- T32 背景只走文字（2026-10-09 用户裁定："背景只能文字描述，或者图片识别成文字然后描述吧"）----
# 摘掉表单上那一格不解决问题：改之前 compiler-contract.md 明写"冲突时以图为准"，
# runner 也真把 --scene-image 喂给引擎 ⇒ 背景的事实源是一张图。所以判据钉在【合同 + 入口 + 清单】三处。
_BG_PATTERNS = (
    (r'add_argument\("--scene-image', "runner 还在收背景图入参"),
    (r'scene_image_path\s*=\s*a\.scene_image', "还在把背景图转给引擎"),
    (r'"scene_image"', "manifest 还把 scene_image 列为可选输入"),
    (r'冲突时以图为准|以该图为唯一事实源|背景定调以该图为准', "合同还写着背景以图为事实源"),
    (r'和/或 背景参考图|和/或 客户参考图', "文档还把背景图列为并列输入"),
)
_BG_FILES = ("SKILL.md", "runner/run.py", "manifest.json",
             "references/compiler-contract.md", "references/pipeline.md")


def bg_text_only_problems(files):
    """files = {相对路径: 正文}。返回仍然把"背景图"当生成入参的地方；空 = 已收口成只走文字。"""
    bad = []
    for fn, s in files.items():
        for pat, why in _BG_PATTERNS:
            if re.search(pat, s):
                bad.append((fn, why))
    return bad


_lane_files = {}
for _fn in _BG_FILES:
    _p = os.path.join(WS, ".dsh", "skills", "super-clone", *_fn.split("/"))
    if os.path.isfile(_p):
        _lane_files[_fn] = io.open(_p, encoding="utf-8").read()
check("T32 super-clone 背景【只走文字】：入口不收图、合同不再以图为准、清单不列 scene_image",
      bg_text_only_problems(_lane_files), [])
check("T32c 覆盖对照：五份文件真的都读到了（少读一份 = T32 会因为『没内容可匹配』而假绿）",
      sorted(_lane_files), sorted(_BG_FILES))
# 反向对照：把裁定前的原文形状喂进同一判据 ⇒ 五条全要报（绿不是因为判据根本没匹配过）
_OLD_SHAPE = {"references/compiler-contract.md": 'add_argument("--scene-image", default=None)\n'
                                                 'scene_image_path = a.scene_image\n'
                                                 '"optional": ["scene_image"]\n'
                                                 '冲突时以图为准\n'
                                                 '换背景 = 文字 和/或 客户参考图\n'}
check("T32b 反向对照：裁定前的形状喂进同一判据 ⇒ 五条全报（判据真的有牙）",
      len(bg_text_only_problems(_OLD_SHAPE)), 5)

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
check("T33b 入口面：--scene-text 在、--scene-image 不在（背景没有图像入参）",
      (_has_text, _has_image), (True, False))
check("T33c 反向对照：把冲突场景（两处背景）喂进真函数 ⇒ 必须只剩一条『背景要求』行",
      sum(1 for l in _fold("背景要求：旧\n背景要求：更旧", "新").splitlines() if l.startswith("背景要求")), 1)

# ---- T34 单段也要写物证/回执，且每一步的模型身份要看得见（2026-10-10 用户："回执也没有…不清楚是不是对应模型跑的"）----
_SVC = os.path.join(r"D:\video-skill", "backend", "services", "super_clone_wan3.py")
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
    _noStamp = dict(_stamp)
    _noStamp.pop("model")
    _SEG2 = os.path.join(_t34_dir, "step2_nomodel")
    _svc._contract_evidence(_SEG2, seg_board=REAL_SHOTS,
                            seg_prompt="…不得出现字幕、花字、水印…", compiled_seg=_noStamp,
                            seg_duration=10.0, ref_video=_ref, seg_product_paths=[_pro],
                            source_fit=_ref, log=None)
    _t2 = str((_check_receipt34.who_table(json.load(open(
        os.path.join(_SEG2, ".skill_receipt.json"), encoding="utf-8"))) or ""))
    check("T34e 反向对照：编译器没给模型名 ⇒ 那一格必须写「没记」，不许拿 source 函数标签冒充模型",
          [("没记" in _t2), ("step1_5_gpt_compiler@" in _t2)], [True, False])
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
_wc_src = io.open(os.path.join(r"D:\video-skill", "backend", "services", "wan3_client.py"),
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
    _p = os.path.join(r"D:\video-skill", *_f.split("/"))
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
_sfw = io.open(r"D:\video-skill\backend\services\super_clone_wan3.py", encoding="utf-8").read()
_i_stop = _sfw.index('stop_after not in (None, "", "compile")')
_i_exec = _sfw.index("step2 = await _run_executor(")
check("T36e 只编译档位：参数校验在调任何模型之前（拼错的档位不许静默当成「照常提交」）；"
      "两处提交腿各有一条 compile-only 早退（多段循环 + 单段），少一处会红",
      [0 < _i_stop < _i_exec, _sfw.count('stop_after == "compile"') >= 3,
       _sfw.count("compiled_only") >= 6], [True, True, True])
check("T36f 覆盖对照：两条提交路径都调 _contract_evidence（少一处 = 那个运行形状没有物证，@4 那次事故）",
      _sfw.count("_contract_evidence("), 3)   # 1 定义 + 2 调用

shutil.rmtree(TMP, ignore_errors=True)
print(_safe("\n=== skill 执行合同闸：%d/%d 通过 ===" % (len(PASS), len(PASS) + len(FAIL))))
for f in FAIL:
    print(_safe("  FAIL: " + str(f)))
sys.exit(1 if FAIL else 0)
