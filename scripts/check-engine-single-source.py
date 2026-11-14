# -*- coding: utf-8 -*-
"""引擎单一源闸（设计见 docs/ADR-skill-contract.md「补：引擎只有一个，所以三方必须指同一条 lane」）。

钉的是 2026-10-10 那次事故的【形状】，不是那条线的名字：客户填的表锁定 yz-prime、合同 spend 写着
clone-r2v（网关里根本没有这条路由 ⇒ 预估掉进兜底价）、入口实现直连 RunningHub —— 三个源各说一套，
agent 就把"RunningHub wan3.0"念给了客户。

判据一律是【函数】，输入全部现读盘上真源（合同 `contract:` / 表单 `form:` / 网关 ROUTES+价目 / 提交腿源码），
所以任何一条源被改坏都会红，而不是"文档里写着要一致"。

E 组  三方比对（每条同时有 form 段和 contract 段的线都跑，不点名 skill）
F 组  提交腿：合同 spend 的 cap/purpose 必须就是代码里 gw.invoke 的那一对
G 组  反向对照 + 变异对照（把当年那个错形状喂进同一个判据）
H 组  散文不许造第二个上游：SKILL.md 里"报价来源"那几行点名的 provider 必须 == 路由 provider
J 组  口型对齐这条腿（2026-10-14）：合同那一格 ↔ ROUTES ↔ 显式单价 ↔ 默认要批 ↔ runner 的两个交付口
      —— 它以前挂在 ROUTES/价目上却没有适配器（call() 一路落到 NotImplementedError ⇒ 账本 0 次调用），
      钉这里是为了"合同写了这一步，代码里真的走得通"，不是又一份散文。
L 组  magpie 三条 free 腿（2026-10-20 换脑：小米那把 429 死了）：腿序唯一源 / 带图不含 glm /
      预算下限 / 429 不换腿 / 单价显式 / 分镜腿不许用丢视频的腿。
"""
import io
import os
import re
import sys
import ast

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WS)
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(WS, r"scripts"))

from backend import gateway as gw                      # noqa: E402
from backend import skill_contract as sc               # noqa: E402
from backend import model_admin as ma                  # noqa: E402

SKILLS_DIR = os.path.join(WS, ".dsh", "skills")
PASS, FAIL = [], []


def _safe(s):
    """按【输出流自己的编码】errors=replace；绝不 reconfigure 成 utf-8（会造"零输出+exit 0"的静默假绿）。"""
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        s.encode(enc)
        return s
    except (UnicodeEncodeError, LookupError):
        return s.encode(enc, "replace").decode(enc, "replace")


def check(name, got, want):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    
    print(_safe("%-4s %-58s got=%r want=%r" % ("PASS" if ok else "FAIL", name, got, want)))


def routes_table():
    """网关路由的唯一源。返回 {cap/purpose: provider}——只取 provider 名，绝不外带 base_url/api_key。"""
    return {"%s/%s" % (c, p): (r or {}).get("provider", "")
            for (c, p), r in gw.ROUTES.items()}


def explicit_price(cap, purpose):
    """价目必须是【显式登记】的，不能是 PRICE_BY_CAP 兜底：兜底值会报出一个谁都没写过的数。
    （2026-10-10 那次 clone-r2v 就是靠兜底报了个假单价。）"""
    ext = gw.external_config().get("prices") or {}
    if ("%s/%s" % (cap, purpose)) in ext:
        return True
    if (cap, purpose) in gw.PRICE_ROUTES:
        return True
    return False


def lock_value(fields, key):
    """取 form 段里某个 locked 行的"值"列（第 5 列 = 选项/区间位，locked 用它放展示值）。"""
    for f in fields or []:
        if f.get("key") == key and f.get("type") == "locked":
            return str(f.get("value") if f.get("value") is not None else f.get("options") or "")
    return ""


def lock_value_raw(fields, key):
    """取 locked 行【字段表原文那一串】（engine_lock 行被 parse_skill_form 现读网关覆盖前的值）。

    🔴 为什么 M5 要读这份而不是 lock_value：engine_lock 的 value 会被 `engine_lock_view` 现读覆盖，
    2026-10-22 用户把候选收成一条后，单模型分支会把真名放进 value（那正是它该做的），于是读 value 的 M5
    把【自己的正确行为】判成红。M5 要钉的是"字段表里有没有写死模型名 = 第二份真相"，读覆盖前的原文才对。
    没有 value_raw（不是 engine_lock 行 / 老代码）⇒ 退回 value，行为不变。
    """
    for f in fields or []:
        if f.get("key") == key and f.get("type") == "locked":
            if f.get("value_raw") is not None:
                return str(f.get("value_raw") or "")
            return str(f.get("value") if f.get("value") is not None else f.get("options") or "")
    return ""


def engine_pickers(fields):
    """表单里有没有还在让客户挑引擎的口子（select 且标签像引擎/模型）。"""
    out = []
    for f in fields or []:
        if f.get("type") == "select" and re.search(u"引擎|模型|engine", str(f.get("label", "")), re.I):
            out.append(f.get("key"))
    return out


def lane_problems(spend, fields, routes, price_ok, has_lock):
    """三方比对判据（纯函数：喂假输入就能做变异对照）。返回问题列表，空 = 一致。"""
    p = []
    if not spend or "/" not in spend:
        return [u"合同没写 spend（cap/purpose）"]
    if spend not in routes:
        p.append(u"合同 spend=%s 在网关 ROUTES 里没有这条路由" % spend)
    provider = routes.get(spend) or ""
    # 单价登记【独立判】：路由没了也要报"这条没人登记过价"，否则一条废路由反而把价目问题藏掉。
    if not price_ok:
        p.append(u"%s 的单价不是显式登记（会掉进 PRICE_BY_CAP 兜底 ⇒ 报价是编的）" % spend)
    if provider:
        if has_lock and provider.lower() not in fields_lock_text(fields).lower():
            p.append(u"表单锁定位与路由 provider=%s 不一致" % provider)
        if has_lock and engine_pickers(fields):
            # 只有【这条线自己声明了引擎锁定位】才算矛盾：没锁定位的线允许客户挑模型（那是另一条产品口径）。
            p.append(u"合同说唯一引擎，但表单还在让客户选（%s）" % ",".join(engine_pickers(fields)))
    return p


def fields_lock_text(fields):
    """把全部 locked 行的值拼成一句，供 provider 比对（不区分哪个锁定位写的）。"""
    return " ".join(lock_value(fields, f.get("key")) for f in (fields or [])
                    if f.get("type") == "locked")


# ---- 枚举：盘上所有【同时有 form 段和 contract 段】的线（不点名 skill，加一档自动进闸）----
lanes = []
skipped = []
for name in sorted(os.listdir(SKILLS_DIR)):
    p = os.path.join(SKILLS_DIR, name, "SKILL.md")
    if not os.path.isfile(p):
        continue
    text = io.open(p, encoding="utf-8", errors="replace").read()
    fields = ma.parse_skill_form(text)
    # 用 parse_contract 直接读这个文件（不用 contract_for：它按【意图档名】查 LANE_DIRS，
    # 目录名 ≠ 档名的线会被静默跳过 = 闸漏一条还不报）。
    contract = sc.parse_contract(text)
    if fields and contract.get("spend"):
        lanes.append((name, contract["spend"], fields, text))
    else:
        skipped.append(name)
ROUTES = routes_table()

print(_safe("== 参与三方比对的线：%d 条（无 form 或无 spend 的：%s）=="
            % (len(lanes), ", ".join(skipped) or "-")))
check("E0 至少有一条线参与（一条都没有=闸在空跑）", len(lanes) >= 1, True)

for name, spend, fields, text in lanes:
    cap, purpose = spend.split("/", 1)
    provider = ROUTES.get(spend, "")
    lk = lock_value(fields, "engine_lock")
    problems = lane_problems(spend, fields, ROUTES, explicit_price(cap, purpose), bool(lk))
    check("E1 %s 合同 spend 能在网关解析成路由" % name, spend in ROUTES, True)
    check("E2 %s 单价是显式登记（不许吃兜底）" % name, explicit_price(cap, purpose), True)
    if lk:
        check("E3 %s 表单 engine_lock=%r 里的 provider == 路由 provider=%r"
              % (name, lk, provider), provider.lower() in lk.lower(), True)
    if lk:
        check("E4 %s 有引擎锁定位 ⇒ 表单不许再有引擎选择口" % name, engine_pickers(fields), [])
    check("E5 %s 三方判据合计无问题" % name, problems, [])

# ---- F 组：提交腿源码 == 合同 spend（这条才是"合同改了、代码没改"的 detector）----
WAN3 = os.path.join(BACKEND, r"services\wan3_client.py")
SRC = os.path.join(BACKEND, r"services\super_clone_wan3.py")


def invoke_pairs_in_func(path, fn):
    """AST 找一个函数体里出现的 gw.invoke("cap","purpose") 对（不看注释、不看别的函数）。"""
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef,)) and node.name == fn:
            pairs = []
            for call in ast.walk(node):
                if isinstance(call, ast.Call):
                    f = call.func
                    name_attr = getattr(f, "attr", "")
                    if name_attr == "invoke" and len(call.args) >= 2:
                        a0, a1 = [getattr(x, "value", None) for x in call.args[:2]]
                        if isinstance(a0, str):
                            pairs.append("%s/%s" % (a0, a1))
            return pairs
    return []


sc_contract = sc.contract_for("super-clone")
sp = sc_contract.get("spend", "")
pairs = invoke_pairs_in_func(WAN3, "run_one_segment_prime")
check("F1 wan3_client.run_one_segment_prime 存在且能 AST 解析出 invoke 对", len(pairs) >= 1, True)
check("F2 提交腿 invoke 的 cap/purpose == 合同 spend", pairs, [sp])
src_entry = io.open(SRC, encoding="utf-8").read()
check("F3 入口真的提交到 prime 腿（run_one_segment_prime 出现次数）",
      len(re.findall(r"wan3_client\.run_one_segment_prime\b", src_entry)) >= 1, True)
check("F4 入口里没有直连厂商那条腿的调用点（run_one_segment 后面不带 _prime 就算越界）",
      re.findall(r"wan3_client\.run_one_segment\b", src_entry), [])

# ---- G 组：反向对照（当年那个错形状必须被同一个判据抓到）----
BAD_SPEND = "video.generate/clone-r2v"
# 🔴 正解输入里的 provider【不能写死】：写死 = 把当年那条渠道名当规则，换芯就把自己的正解判红（2026-10-30 现踩）。
#     这里现读路由；G4 那条变异反留一个字面量，不然"换成别的 provider"就没有对照物。
_G_PROVIDER = str(ROUTES.get(sp) or "").strip() or "tianyue"
_G_LOCK = _G_PROVIDER + " / gate-现读-model"
check("G0 前提：clone-r2v 确实不在 ROUTES 里（在的话这条反向对照就没牙）", BAD_SPEND in ROUTES, False)
bad = lane_problems(BAD_SPEND, [{"key": "engine_lock", "type": "locked", "label": u"生成引擎",
                                "options": _G_LOCK}],
                    ROUTES, False, True)
check("G1 反向对照：spend 换成 clone-r2v ⇒ 报路由缺失",
      any(u"ROUTES" in x for x in bad), True)
check("G2 反向对照：同一份输入 ⇒ 问题条数 >= 2（缺路由 + 吃兜底价）", len(bad) >= 2, True)
mut_price = lane_problems(sp, [{"key": "engine_lock", "type": "locked", "label": u"生成引擎",
                               "options": _G_LOCK}], ROUTES, False, True)
check("G3 变异：把单价登记摘掉 ⇒ 报兜底", any(u"兜底" in x for x in mut_price), True)
mut_lock = lane_problems(sp, [{"key": "engine_lock", "type": "locked", "label": u"生成引擎",
                              "options": "someother / wan3.0"}], ROUTES, True, True)
check("G4 变异：engine_lock 换成别的 provider ⇒ 报不一致", any("provider" in x for x in mut_lock), True)
mut_pick = lane_problems(sp, [{"key": "engine_lock", "type": "locked", "label": u"生成引擎",
                              "options": _G_LOCK},
                             {"key": "engine", "type": "select", "label": u"生成引擎"}],
                        ROUTES, True, True)
check("G5 变异：表单多一个引擎 select ⇒ 判据报『还在让客户选』", any(u"让客户选" in x for x in mut_pick), True)
check("G6 变异对照没把正解一起判红（正解输入仍是 0 问题）",
      lane_problems(sp, [{"key": "engine_lock", "type": "locked", "label": u"生成引擎",
                         "options": _G_LOCK}], ROUTES, True, True), [])

# ---- H 组：报价时能【现读到真相】，散文不许宣称另一个上游 ----
# H1 只抓【报价来源】那一类断言行（含锚点词）；行文里提到 RunningHub 不算（禁令段、复盘段本来就要提它）。
# H4/H5 钉的是事故的【根因】：agent 当时没有一条"可现读"的口，只能凭记忆念厂商标 ⇒
#    文档必须写出现读命令，而那条命令【真跑一次】必须能把这条 lane 的 provider 与单价打印出来。
QUOTE_ANCHORS = (u"上游 =", u"上游=", u"唯一实现", u"照抄", u"唯一来源", u"路由", u"上游与单价")
ALL_PROVIDERS = sorted({v for v in ROUTES.values() if v})
true_provider = ROUTES.get(sp, "")


def bad_quote_lines(text, provider):
    out = []
    for ln in text.split("\n"):
        if not any(a in ln for a in QUOTE_ANCHORS):
            continue
        low = ln.lower()
        for other in ALL_PROVIDERS:
            # 别名只补【人写得出手、但不是 provider 名】的厂商标；youzan666.vip 就是 yz-prime 的家，不算第二个上游。
            if other and other.lower() != provider.lower() and other.lower() in low:
                out.append(ln.strip()[:120])
                break
    return sorted(set(out))


for name, spend, fields, text in lanes:
    provider = ROUTES.get(spend, "")
    hb = bad_quote_lines(text, provider)
    check("H1 %s 的报价来源行没有第二个上游（provider=%s）" % (name, provider), hb, [])
check("H2 反向对照：报价来源行写成 RunningHub ⇒ 判据必须抓到",
      len(bad_quote_lines(u"- 路由（上游 = 本 lane 唯一实现 `RunningHub Wan 3.0-R2V`", true_provider)) >= 1, True)
check("H3 变异对照：正确那行不报（否则 H1 是靠匹配失败假绿）",
      bad_quote_lines(u"- 路由（上游 = 本 lane 唯一实现 `yz-prime`", true_provider), [])
check("H4 文档给了【现读单价】的命令（不写就只能凭记忆报价）",
      any("gateway.py prices" in io.open(os.path.join(SKILLS_DIR, n, "SKILL.md"),
          encoding="utf-8", errors="replace").read() for n, _s, _f, _t in lanes), True)

# H5 真跑一次 prices（只读、0 消耗）：这条 lane 必须出现在输出里，且带 provider 与单价
import subprocess                                            # noqa: E402
_py = [sys.executable, os.path.join(BACKEND, "gateway.py"), "prices"]
r = subprocess.run(_py, capture_output=True, text=True, encoding="utf-8", errors="replace",
                   stdin=subprocess.DEVNULL, timeout=90)
prtxt = (r.stdout or "") + (r.stderr or "")
for name, spend, fields, text in lanes:
    cap, purpose = spend.split("/", 1)
    provider = ROUTES.get(spend, "")
    blob = prtxt.lower()
    check("H5 %s：`gateway.py prices` 里读得到这条 lane（%s）" % (name, spend),
          purpose.lower() in blob, True)
    if provider:
        check("H6 %s：prices 输出里能看到 provider=%s" % (name, provider), provider.lower() in blob, True)
    check("H7 %s：prices 退出码 0（非 0 = 这条现读口本身是坏的，H5 全绿也没用）" % name, r.returncode, 0)

# ══ I 组：引擎锁（2026-10-10 用户裁定"唯一的 wan3.0 只有一个"之后的第二层）════════
# E/F 组钉的是【花钱那一刀走哪条 lane】；I 组钉的是【别人还能不能从入口换 lane】。
# 起因：run_pipeline(engine=...) 没有白名单、runner 的 --engine 没有 choices ⇒ engine=h3
# 一路能走到 RunningHub 导演台，而合同/表单/上面那些判据全都只看着 wan3-prime 那一条腿。
SVC_ENG = os.path.join(BACKEND, r"services\super_clone_wan3.py")
RUN_ENG = os.path.join(WS, r".dsh\skills\super-clone\runner\run.py")
APP_ENG = os.path.join(BACKEND, r"app.py")
src_svc = io.open(SVC_ENG, encoding="utf-8", errors="replace").read()
src_run = io.open(RUN_ENG, encoding="utf-8", errors="replace").read()
src_app = io.open(APP_ENG, encoding="utf-8", errors="replace").read()
Q = chr(34)


def parse_lane_engines(svc_text):
    m = re.search(r"(?m)^LANE_ENGINES\s*=\s*\(([^)]*)\)", svc_text)
    if not m:
        return None
    return tuple(x.strip().strip(Q).strip(chr(39)) for x in m.group(1).split(",") if x.strip())


def lock_problems(svc_text, run_text, app_text):
    """纯判据（喂假文本就能做变异对照）。返回问题列表，空 = 锁装好了。"""
    p = []
    engines = parse_lane_engines(svc_text)
    if engines is None:
        return ["服务层没有 LANE_ENGINES 名单（唯一源丢了）"]
    if "def engine_lock_error(" not in svc_text:
        p.append("没有 engine_lock_error() 这个纯判据 ⇒ 名单没人问，等于可以绕过")
    # ① 入口第一道就是锁：AST 看 run_pipeline 体开头（注释不是节点，所以 body[0] 必须是那次调用）
    try:
        tree = ast.parse(svc_text)
        fn = next((n for n in tree.body
                   if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_pipeline"), None)
        if fn is None:
            p.append("找不到 run_pipeline（改名了？判据要跟着改）")
        else:
            head = fn.body[1:] if (fn.body and isinstance(fn.body[0], ast.Expr)
                                   and isinstance(fn.body[0].value, ast.Constant)
                                   and isinstance(fn.body[0].value.value, str)) else fn.body
            # body[0] 是 docstring（我第一版没跳它，把正确的锁判成"没装"）
            if not head or "engine_lock_error" not in ast.dump(head[0]):
                p.append("run_pipeline 第一条真语句不是引擎锁 ⇒ 拒之前可能已经建目录/读盘/调模型")
    except SyntaxError as e:
        p.append("服务层解析不出来：%s" % e)
    # ② runner 的 --engine 必须有 choices，且集合 == 服务层名单（两份各抄一份迟早漂）
    rm = re.search(r"add_argument\(\s*" + Q + "--engine" + Q + r"[^)]*?choices=\[([^\]]*)\]",
                   run_text, re.S)
    if not rm:
        p.append("runner 的 --engine 没有 choices ⇒ 敲命令的人可以随意切引擎")
    else:
        choices = tuple(x.strip().strip(Q).strip(chr(39)) for x in rm.group(1).split(",") if x.strip())
        if choices != engines:
            p.append("runner choices=%s 与服务层 LANE_ENGINES=%s 不一致" % (list(choices), list(engines)))
    # ③ 服务层里除了锁，不许再有"按 engine 选到白名单之外执行器"的活路
    # 白名单之外的分支不要求"立刻删干净"（那是别人写的 H3 导演台实现，删它得单独拍板），
    # 但必须【走不到】：它的源码位置要在锁之后。锁在 run_pipeline 第一道 ⇒ 任何 engine 分派都在它下游。
    lock_at = svc_text.find("_lock = engine_lock_error(engine)")
    # 只看 run_pipeline 自己那段：文件里另有一个同名局部参数 engine（_compiler_llm 的 grok/gpt），
    # 全文扫会把"选哪个推理脑"误判成"选哪个生成引擎"（我第一版就红了在 grok 上）。
    seg_lo = svc_text.find("async def run_pipeline(")
    seg_hi = svc_text.find(chr(10) + "def ", seg_lo)
    seg = svc_text[seg_lo: seg_hi if seg_hi > 0 else len(svc_text)]
    seg_off = seg_lo
    bad = []
    for m in re.finditer(r"if\s+engine\s*==\s*" + Q + r"([^" + Q + r"]+)" + Q, seg):
        if m.group(1) not in engines and (lock_at < 0 or seg_off + m.start() < lock_at):
            bad.append(m.group(1))
    if bad:
        p.append("白名单之外的 engine 分支排在锁【之前】或锁没了：%s ⇒ 从模块内部就能绕开" % bad)
    # ④ 8090 提交口：建任务记录之前先问锁（否则人看到的是一串已排队的任务）
    i_ep = app_text.find("@app.post(" + Q + "/api/super-clone-wan3/submit" + Q + ")")
    if i_ep < 0:
        p.append("找不到 super-clone-wan3 提交口（端点改名了？判据要跟着改）")
    else:
        # 🔴 窗口不能写死字符数：这个提交口 2026-10-22 已经长到 5802 字（加了口播/口型那几格），
        #    原来切 3500 字 ⇒ "建任务记录"那行落在窗外、b=-1 ⇒ 这条判据变成空跑的绿，
        #    I8 的变异也因此不再报（现象是"锁挪到建任务之后却没人红"）。按下一个 @app. 收口才是对的边界。
        _nxt = app_text.find("@app.", i_ep + 10)
        seg = app_text[i_ep:(_nxt if _nxt > 0 else i_ep + 3500)]
        a = seg.find("engine_lock_error(")
        b = seg.find("super_clone_wan3_tasks[task_id] =")
        if a < 0:
            p.append("提交口没问引擎锁")
        elif 0 <= b < a:
            p.append("提交口先建任务记录才问锁 ⇒ 拒得晚，盘上已经多一条任务")
    return p


def extract_lock_fn(svc_text):
    """把"常量 + engine_lock_error"整块抠出来 exec（不 import 整个重模块：它会拉起 playwright 等）。
    边界取"函数之后的第一个顶层定义"，不取固定空行数（第一版按三个换行找，把 def _cfg 的
    Dict 注解也卷进来 ⇒ NameError；第二版从函数起切，又漏掉常量 ⇒ KeyError）。"""
    i = svc_text.index("LANE_ENGINES =")
    j0 = svc_text.index("def engine_lock_error(")
    assert i < j0, "常量在函数之后，切法要改"
    rest = svc_text[i:]
    off = j0 - i + 1
    j = len(rest)
    for marker in (chr(10) + "def ", chr(10) + "@", chr(10) + "class "):
        k = rest.find(marker, off)
        if 0 < k < j:
            j = k
    ns = {}
    exec(compile(rest[:j], "<engine_lock_error>", "exec"), ns)
    return ns["engine_lock_error"]


_lock_fn = extract_lock_fn(src_svc)
problems = lock_problems(src_svc, src_run, src_app)
check("I1 引擎锁判据合计无问题", problems, [])
check("I2 真跑判据：wan3 放行，h3/H3/seedance/空/None 全拒",
      [_lock_fn(e) == "" for e in ("wan3", "h3", "H3", "seedance", "", None)],
      [True, False, False, False, False, False])
check("I2b 拒因里带得上『该去哪』：点名 lane 与『换 skill、重新报价』",
      all(s in _lock_fn("h3") for s in ("video.generate/wan3-prime", "换 skill", "重新报价")), True)
# ── 变异对照：三处锁分别拆掉，判据必须各报一条（否则 I1 是靠"没东西可查"假绿）──
check("I3 变异：判据函数改名（等于没人问）⇒ 报",
      any("engine_lock_error" in x for x in lock_problems(
          src_svc.replace("def engine_lock_error(", "def _lock_disabled(", 1), src_run, src_app)), True)
check("I4 变异：runner choices 多出 h3 ⇒ 报不一致",
      any("choices" in x for x in lock_problems(
          src_svc, src_run.replace("choices=[" + Q + "wan3" + Q + "]",
                                   "choices=[" + Q + "wan3" + Q + "," + Q + "h3" + Q + "]", 1), src_app)), True)
check("I5 前提：服务层确实还留着 engine==h3 分支（没有的话 I6 就是空跑）",
      ("if engine == " + Q + "h3" + Q + ":") in src_svc, True)
check("I6 变异：白名单被改大（加 h3）⇒ 分支不再算越界，但 runner choices 必须跟着报",
      any("choices" in x for x in lock_problems(
          src_svc.replace("LANE_ENGINES = (" + Q + "wan3" + Q + ",)",
                          "LANE_ENGINES = (" + Q + "wan3" + Q + "," + Q + "h3" + Q + ",)", 1), src_run, src_app)), True)
check("I7 变异：提交口把锁问成空串 ⇒ 报",
      any(("锁" in x or "提交口" in x) for x in lock_problems(
          src_svc, src_run, src_app.replace("_lock = engine_lock_error(req.engine)", "_lock = " + Q + Q, 1))), True)
_lock_src = ("    _lock = engine_lock_error(req.engine)" + chr(10)
             + "    if _lock:" + chr(10) + "        raise HTTPException(400, _lock)" + chr(10))
_i = src_app.find(_lock_src)
check("I8a 前提：提交口里那段锁源码按预期形状存在（找不到=变异是空跑，别给假绿）", _i > 0, True)
# 变异要【在端点那一段里】挪顺序：全文 find 会先撞上别的端点（同名写法出现 2 次），
# 于是锁被挪到这条线之外 ⇒ 判据报的是"没问锁"而不是"拒得晚"，I8 就成了为错的理由通过。
_ep = src_app.find("@app.post(" + Q + "/api/super-clone-wan3/submit" + Q + ")")
_end = src_app.find("@app.", _ep + 10)
_seg = src_app[_ep:_end if _end > 0 else len(src_app)]
_j = _seg.find("super_clone_wan3_tasks[task_id] =")
check("I8 前提：提交口确实有【建任务记录】那一行（没有的话 I8 是空跑）", _j > 0, True)
_seg_m = _seg.replace(_lock_src, "", 1)
_k = _seg_m.find("super_clone_wan3_tasks[task_id] =")
# 插在"建任务那一行的【之后】"——我第一版插在 _k 处，等于还是插在那行前面，
# 位置比 a 小 ⇒ 变异根本没发生，判据当然不报（假"控制无效"）。
_anchor_line = "super_clone_wan3_tasks[task_id] ="
_seg_m = _seg_m[:_k + len(_anchor_line)] + chr(10) + _lock_src + _seg_m[_k + len(_anchor_line):]
_moved = src_app[:_ep] + _seg_m + src_app[_end if _end > 0 else len(src_app):]
check("I8 变异：把锁挪到建任务之后 ⇒ 必须报『拒得晚』",
      any("拒得晚" in x for x in lock_problems(src_svc, src_run, _moved)), True)


# ── J 组：口型对齐这条腿（2026-10-14 接入；它以前挂在 ROUTES 上但没有适配器）──────────
# 钉的是"合同那一格 ↔ 网关那条腿 ↔ 价目 ↔ 单价"四方对得上，不是某个数字。
RUN_PY = os.path.join(SKILLS_DIR, "super-clone", "runner", "run.py")
_src_run_j = io.open(RUN_PY, encoding="utf-8", errors="replace").read()
_c_j = sc.contract_for("super-clone")
_step_j = next((s for s in (_c_j.get("steps") or []) if s.get("id") == "lip-sync"), None)
check("J1 合同里有 lip-sync 那一格，且手段就是网关轨（不是 backend 直调、更不是手搓 ffmpeg）",
      [bool(_step_j), (_step_j or {}).get("impl"), (_step_j or {}).get("required")],
      [True, "gateway:invoke:lipsync", 0])
check("J2 网关认这条路由：ROUTES 里有 lipsync/default，且已接适配器（不再落 NotImplementedError）",
      ["lipsync/default" in routes_table(), gw.is_adapted("lipsync", "default")], [True, True])
check("J3 单价是【显式登记】的数（拿 PRICE_BY_CAP 兜底 = 报出一个谁都没写过的价）",
      [explicit_price("lipsync", "default"), gw.PRICE_ROUTES.get(("lipsync", "default")),
       int(gw.unit_price("lipsync", "default"))], [True, 20, 20])
check("J4 默认要批：lipsync 不在免批白名单里 ⇒ 没条子就不许发起",
      gw.needs_spend_approval("lipsync", "default"), True)
# 参数闸门值表：缺参/未知 key/segments 形状错 ⇒ 本地拒（0 分，一个请求都不发）
# 🔴 全用【盘上真文件】算：不然"文件不在盘"那一刀会混进计数里，值表就对不上规则了（我自己第一次就写错在这）。
_V = os.path.abspath(__file__)
check("J5 入参值表：空 / 只给一个 / 三个都给 / 多一个未知 key / segments 形状错",
      [len(gw.lipsync_problems({})),
       len(gw.lipsync_problems({"video_path": _V})),
       len(gw.lipsync_problems({"video_path": _V, "audio_path": _V, "output_dir": _V})),
       len(gw.lipsync_problems({"video_path": _V, "audio_path": _V,
                               "output_dir": _V, "reference_audio": _V})),
       len(gw.lipsync_problems({"video_path": _V, "audio_path": _V,
                               "output_dir": _V, "segments": {"start": 0}}))],
      [3, 2, 0, 1, 1])
check("J5b 文件不在盘要单独点名（不许和「缺参数」混成一条，那是两种修法）",
      any("不在盘上" in x for x in gw.lipsync_problems(
          {"video_path": "C:/no-such/vid.mp4", "audio_path": "C:/no-such/a.wav",
           "output_dir": "C:/no-such"})), True)
check("J5c 合法形状（含给了 segments 列表）⇒ 0 个问题：上面那五条不许把合格对象也判红",
      [gw.lipsync_problems({"video_path": _V, "audio_path": _V, "output_dir": _V}),
       gw.lipsync_problems({"video_path": _V, "audio_path": _V, "output_dir": _V,
                           "segments": [{"start": 0.0, "end": 2.0}]})], [[], []])
_rj = gw.call("lipsync", "default", video_path="C:/x/vid.mp4", audio_path="C:/x/a.wav",
              output_dir="C:/x")
check("J6 真跑分发：参数不合格 ⇒ success=False 且【没发出任何请求】（0 分、不建任务）",
      [_rj.get("success"), "0 分未扣" in str(_rj.get("error") or "")], [False, True])
check("J7 接线：runner 用合同写的那个手段调网关（写了合同却没有调用点 = 那一格永远打不上钩）",
      [_src_run_j.count('gw.invoke(LIPSYNC_CAP[0], LIPSYNC_CAP[1]'),
       _src_run_j.count('"lipsync", "default"'),
       _src_run_j.count("apply_lip_sync(")], [1, 1, 4])
check("J7b 两个交付口都接了这条腿（--stage wait 与 submit 各一处；少一处 = 那种形状没有口型）",
      [_src_run_j.count("_lip = apply_lip_sync(")], [2])
check("J8 目标音频是【本地抽成片自己的音轨】，不另引一次 TTS/克隆（再引一次 = 重复花钱且不是那条要贴的声）",
      "_extract_audio_track(src, audio)" in _src_run_j and "voice_clone_to_file" not in _src_run_j, True)
_swap_j = next((x for x in (_c_j.get("steps") or []) if x.get("id") == "narration-swap"), {})
check("J9 两格不是一张：narration-swap(换音) 与 lip-sync(对口型) 手段/物证各写各的（合成一格=交付时看不出哪半没做）",
      [[_swap_j.get("impl"), (_step_j or {}).get("impl")],
       [_swap_j.get("proof", "")[:19], (_step_j or {}).get("proof", "")[:13]]],
      [["backend:invoke:voiceover_replace.run_narration_swap", "gateway:invoke:lipsync"],
       ["narration_swap.json", "lip_sync.json"]])
check("J10 变异：把 runner 里的 invoke 换成直调 client ⇒ J7 的计数立刻失配（证明它数的是真接线）",
      _src_run_j.replace("gw.invoke(LIPSYNC_CAP[0], LIPSYNC_CAP[1]",
                         "vtp.run_lip_sync(").count("gw.invoke(LIPSYNC_CAP[0], LIPSYNC_CAP[1]"), 0)

check("J11 这条腿落的仍是 R10 登记过的那份【加工腿】文件（换成生成类工作流 = 绕过 RunningHub 视频禁令）",
      (gw.ROUTES.get(("lipsync", "default")) or {}).get("client"), "video_translate_pipeline")

# J12：音频入参到底送没送 —— 判"送没送"要用真函数的返回值，不看注释也不看转储。
# 🔴 2026-10-30 换芯到 yz30 后 `_tianyue_payload` 这种独立 body 构造器已随 tianyue lane 整条拆除：
#    音频现在走 media 数组（reference_audio），外链在 _yz30_media_item 里【0 请求】原样引用，
#    body 只有 model/prompt/duration/resolution/ratio/media 六个键、没有 audio 布尔开关。
_wc_src = io.open(WAN3, encoding="utf-8", errors="replace").read()
_gw_src = io.open(os.path.join(BACKEND, "gateway.py"), encoding="utf-8", errors="replace").read()
_yz_seg = _gw_src.split("def _yz30_video_run", 1)[1].split("\ndef ", 1)[0]
_yz_body_lit = _yz_seg.split("body = _json.dumps({", 1)[1].split("},", 1)[0]
_a12, _n12, _e12 = gw._yz30_media_item("reference_audio", "https://d.example/a.wav", "https://yz.example", "k")
_a12n, _n12n, _e12n = gw._yz30_media_item("reference_audio", "", "https://yz.example", "k")
check("J12 音频入参【真送出去】：audio_urls 经 _yz30_media_item 落成 media 的 reference_audio（外链 0 请求原样引用）",
      [_e12, (_a12 or {}).get("type"), (_a12 or {}).get("url")],
      [None, "reference_audio", "https://d.example/a.wav"])
check("J12b yz30 没有 audio 布尔开关 ⇒ body 不许凭空塞 audio；没给音频就没有 reference_audio 条目（丢参与瞎补都错）",
      [_e12n, _a12n, '"audio"' in _yz_body_lit, '("reference_audio", "audio_urls")' in _yz_seg],
      [None, None, False, True])
check("J12c 提交腿把上层的 audio 一路带到 params（以前它收了参数就丢）",
      '"audio": bool(audio)' in _wc_src, True)
check("J12d 转储要说真话：现在确实送上了，同时必须写明『语义未实测 ⇒ 出片要 ASR 复验』",
      ['"audio_param_sent_to_lane": True' in src_svc, "ASR 复验" in src_svc,
       '"reference_audio_sent": bool(audio_urls)' in src_svc], [True, True, True])

# ── K 组：RunningHub 那条工作流号只许有一份，且右栏不许再有"映"画面那套（2026-10-16 用户判镜像不行）──
RH_JS = os.path.join(WS, r"plugins\ops-gateway\lib\client.js")
RH_PY = r"D:\nzdh\backend\services\runninghub_rpa_client.py"


def _rh_fns(js_text):
    """右栏那一格的【代码】区域 = rhLink + RhWebBody 两个函数体（注释一律不在里面）。
    为什么不扫全文：别处本来就有 iframe（omni/hypit 的引擎台），而顶上那段历史注释写着
    8123/tools/rh_panel 是"当年为什么这么做"，扫它会自己咬自己。"""
    return "\n".join(re.findall(r"function (?:rhLink|RhWebBody)\([^)]*\) \{.*?\n    \}", js_text or "", re.S))


def rh_open_problems(js_text, py_text):
    """右栏那一格与 8091 的 RPA 必须指【同一条】工作流，而且右栏只许把真页交给系统浏览器。"""
    bad = []
    m1 = re.search(r'const RH_WORKFLOW = "([^"]+)"', js_text or "")
    m2 = re.search(r'^WORKFLOW_URL = "([^"]+)"', py_text or "", re.M)
    if not m1:
        bad.append("右栏里没有 RH_WORKFLOW 这条常量（链接是从哪来的？）")
    if not m2:
        bad.append("8091 的 RPA 里没有 WORKFLOW_URL（唯一源挪走了要同步改这里）")
    if m1 and m2 and m1.group(1) != m2.group(1):
        bad.append("两边不同：右栏=%s / RPA=%s" % (m1.group(1), m2.group(1)))
    seg = _rh_fns(js_text)
    if "RhWebBody" not in seg or "rhLink" not in seg:
        bad.append("读不到 RhWebBody / rhLink 这两个函数（改名了？判据就空跑了）")
        return bad
    for needle, why in (('iframe', "右栏不许再内嵌 RunningHub —— SAMEORIGIN 本来就嵌不进，只会画出一个空白框"),
                        ('8123', "不许把画面接回无头镜像面板 8123（它 launch_persistent_context 占的就是 8091 RPA 那份画像）"),
                        ('rh_panel', "不许再引 tools/rh_panel（2026-10-16 已摘）"),
                        ('shell.openExternal', "渲染层不许自己去碰内核 API（外链交给 target=_blank，内核 main/index.js:14098 会转外部浏览器）")):
        if needle in seg:
            bad.append("那一格里出现了「%s」：%s" % (needle, why))
    if 'target: "_blank"' not in seg:
        bad.append("没有 target=\"_blank\" 的链接 = 这一格没有能把真页打开的地方")
    if "rhLink(RH_WORKFLOW" not in seg:
        bad.append("RhWebBody 没有把 RH_WORKFLOW 挂成链接（常量在、但页面上点不到）")
    return bad


_rh_js = io.open(RH_JS, encoding="utf-8", errors="replace").read()
_rh_py = io.open(RH_PY, encoding="utf-8", errors="replace").read()
_rh_seg = _rh_fns(_rh_js)
check("K1 右栏的工作流号 == 8091 RPA 的 WORKFLOW_URL，且那一格里没有 iframe / 8123 / rh_panel（纯函数判据）",
      rh_open_problems(_rh_js, _rh_py), [])
check("K2 真链接就是那条工作流本身（不是首页、不是猜出来的地址）",
      ["rhLink(RH_WORKFLOW" in _rh_seg,
       (re.search(r'const RH_WORKFLOW = "([^"]+)"', _rh_js) or type("X", (), {"group": lambda s, n: ""})()).group(1)],
      [True, "https://www.runninghub.cn/workflow/2106377827540103169?source=workspace"])
# 反向对照：三种坏法各来一次，每次都要点得出名字（一次只测一个方向等于没测；K1 不许是空跑的绿）
_old_body = ('function RhWebBody() {\n'
             '      return h("iframe", { src: "http://127.0.0.1:8123/rh-view", title: "RunningHub 网页" });\n'
             '    }')
_p_iframe = rh_open_problems(re.sub(r"function RhWebBody\([^)]*\) \{.*?\n    \}",
                                   lambda _m: _old_body, _rh_js, flags=re.S), _rh_py)
_p_drift = rh_open_problems(_rh_js.replace("2106377827540103169", "9999999999999999999"), _rh_py)
_p_nolink = rh_open_problems(_rh_js.replace('target: "_blank"', 'target: "_self"'), _rh_py)
check("K2b 变异：整格换回【iframe + 8123 镜像】那版 ⇒ iframe 与 8123 都要被点名",
      [[any("iframe" in x for x in _p_iframe), any("8123" in x for x in _p_iframe)], len(_p_iframe) >= 2],
      [[True, True], True])
check("K2c 变异：只把工作流号漂一个数字 ⇒ 必须只红在【两边不同】这一条",
      [any("两边不同" in x for x in _p_drift), len(_p_drift)], [True, 1])
check("K2d 变异：链接没有 target=_blank ⇒ 红（那一格就打不开真页，等于没入口）",
      [any("_blank" in x for x in _p_nolink), len(_p_nolink)], [True, 1])

# ==== L 组：magpie 三条 free 腿（2026-10-20 换脑）====
# 钉五件事：腿序只有一份、带图腿【不含 glm】、预算下限真夹住、429 是抖动不算额度（不换腿）、
# 以及那条最容易被写错的禁令 —— 分镜腿吃视频，而 magpie 的桥会丢 video part。
def _purps(kind):
    return [l["purpose"] for l in gw.magpie_lane_specs("magpie", kind)]


check("L1 腿序唯一源在 config，且【实测定序】：文本腿 deepseek→mimo→(兜底 glmflash)；带图腿 2026-10-22 夜改成 qfmodel→mimo→deepseek"
      "（真产品图四方对照：qfmodel 认出旋盖+读对 jio+不补画面外，deepseek 把有盖看成无盖，dfmodel 同错且 374s）；"
      "glm 已从两张表整条摘掉（真 payload 3 次 0 合格）。"
      "表尾的 magpie_glmflash = 2026-10-30 用户点名的 mimo 兜底（qoder-cn/gfmodel，模型卡 GLM-5.3-Flash）："
      "实测文本 1.8–4.3s 且 finish=stop【正文有字】（opencode 那条 glm 烧光预算⇒0 字的坑没复现）、"
      "带图 11.8s 答对瓶色 ⇒ 只许进这两张表的【末位】；它不吃视频（Δ=0），所以分镜腿另有禁用族硬拦。",
      [_purps("text"), _purps("vision"), "magpie_glm" in _purps("text") or "magpie_glm" in _purps("vision")],
      [["magpie_deepseek", "magpie_mimo", "magpie_glmflash"],
       ["magpie_qfmodel", "magpie_mimo", "magpie_deepseek", "magpie_glmflash"], False])

_ts0 = gw.settings.MAGPIE_TEXT_LANES
try:
    gw.settings.MAGPIE_TEXT_LANES = "magpie_glm:3,magpie_deepseek:3"   # 变异：把 0/3 那条摆回主腿位
    _glm = [l["purpose"] for l in gw.magpie_lane_specs("magpie", "text")]
finally:
    gw.settings.MAGPIE_TEXT_LANES = _ts0
check("L1b 变异：把 glm 塞回主腿位 ⇒ 立刻看得见（这条钉的是【实测结论】，不是当时那行字符串没人动）",
      _glm, ["magpie_glm", "magpie_deepseek"])

_seen = []


def _cap(base, key, model, msgs, temperature=0.7, max_tokens=0, extra=None):
    _seen.append({"model": model, "max_tokens": max_tokens, "extra": extra})
    return "好", None, {"total_tokens": 1}


gw._magpie_chat("http://127.0.0.1:3425/v1", "k", gw.magpie_lane_specs("magpie", "text"), [],
                max_tokens=60, _call=_cap)
check("L2 预算下限真夹住：喂 max_tokens=60 ⇒ 发出去必须 ≥ MAGPIE_MIN_BUDGET"
      "（实测 60 预算时 60 tok 整段进 reasoning、正文 None，看起来像模型不会说话）",
      [all(c["max_tokens"] >= gw.MAGPIE_MIN_BUDGET for c in _seen), len(_seen)], [True, 1])

_fl = []


def _flaky(base, key, model, msgs, temperature=0.7, max_tokens=0, extra=None):
    _fl.append({"model": model, "extra": extra})
    return (None, "HTTP 429: rate_limit_error", {}) if len(_fl) < 3 else ("好", None, {})


gw._magpie_chat("http://x/v1", "k", gw.magpie_lane_specs("magpie", "text"), [], sleep=0, _call=_flaky)
check("L3 它的 429 = 抖动 ⇒ 同一条腿退避重打（不换腿），且第 2 次起带上那对关思考旋钮",
      [len({c["model"] for c in _fl}), _fl[0]["extra"], bool(_fl[1]["extra"] and "reasoning_effort" in _fl[1]["extra"])],
      [1, None, True])

_sw = []


def _boom(base, key, model, msgs, temperature=0.7, max_tokens=0, extra=None):
    _sw.append(model)
    return (None, "HTTP 500: boom", {}) if len(_sw) == 1 else ("好", None, {})


gw._magpie_chat("http://x/v1", "k", gw.magpie_lane_specs("magpie", "text"), [], sleep=0, _call=_boom)
check("L4 【非抖动】的失败才换腿，且换腿顺序就是表序（500 ⇒ 立刻下一条，不空转三次）",
      _sw, [gw.magpie_lane_specs("magpie", "text")[0]["model"],
            gw.magpie_lane_specs("magpie", "text")[1]["model"]])

# 🔴 泛化版：不再点名数腿 —— 枚举 ROUTES 里【provider=magpie 的每一条】都要有显式单价。
#    原来那条写死了五个名字，加一条腿（2026-10-22 的 magpie_qfmodel/magpie_mimo）它就不看，
#    而"没登记单价"的表现正是 10-10 那次：报价里冒出一个谁都没写过的数。
_magpie_routes = sorted((c, p) for (c, p) in gw.ROUTES if str((gw.ROUTES.get((c, p)) or {}).get("provider")) == "magpie")
check("L5 magpie 的每条路由都【显式登记单价】（枚举 ROUTES，不点名：漏一条=报价会报出一个谁都没写过的数）",
      [(c + "/" + p) for (c, p) in _magpie_routes if not explicit_price(c, p)], [])
check("L5b 反向对照：临时摘掉看图主腿的单价 ⇒ 上面那条必须抓得到（证明它数的是真表）",
      [bool(_magpie_routes), explicit_price("video.analyze", "magpie_qfmodel")], [True, True])

try:
    from backend.services import super_clone_wan3 as scw
except Exception:                                              # noqa: BLE001
    scw = None

_dp = gw.default_purpose
_eng0 = getattr(gw.settings, "REPLICA_WAN3_STORYBOARD_ENGINE", None)
try:
    # 🔴 必须显式把档位钉成 gateway 再变异：`_storyboard_lane()` 第一行就读这个开关，
    #    而 .env 现在写的是 mimo（2026-10-22 另一会话按用户指令切了小米直连）⇒
    #    不钉住的话 magpie 那个变异根本走不到网关分支，L6 会【假红】——
    #    依赖现场配置的断言不是保险，是随机故障（这条教训 AGENTS.md 里写过，今天又撞一次）。
    gw.settings.REPLICA_WAN3_STORYBOARD_ENGINE = "gateway"
    gw.default_purpose = lambda cap: "magpie"                  # 变异：把网关默认路由指成丢视频的那条
    _sb_err = str((scw._storyboard_lane() if scw else {}).get("error") or "")
finally:
    gw.default_purpose = _dp
    if _eng0 is not None:
        gw.settings.REPLICA_WAN3_STORYBOARD_ENGINE = _eng0
check("L6 🔴 分镜腿那条禁令有机器形状：网关默认路由一旦被指成 magpie（桥丢 video part）⇒ 必须拒，不许照跑"
      "（测前显式钉 engine=gateway，不跟着 .env 的当前档位漂）",
      [bool(_sb_err), "magpie" in _sb_err, scw is not None], [True, True, True])

_sv_text = io.open(os.path.join(BACKEND, "services", "super_clone_wan3.py"),
                   encoding="utf-8", errors="replace").read() if scw else ""
check("L7 看图腿的【链】只有一份源：真 invoke 读链上的一项（`ln[\"purpose\"]`），源码里不再写死某一条 purpose 名；"
      "回执格读 `_analyze_purpose()`（= 链的第一条），两处都指向 config 那一行",
      [_sv_text.count("_analyze_lanes()"), _sv_text.count("_analyze_purpose()"),
       bool(re.search(r'invoke\(\s*"video\.analyze",\s*"mimo_pro"', _sv_text)),
       bool(re.search(r'invoke\(\s*"video\.analyze",\s*ln\["purpose"\]', _sv_text))],
      [4, 2, False, True])

# 🔴 看图链的作废规则必须有机器形状（2026-10-22 起这一刀是一条【有序链】，不再是一个 purpose）：
#    串里点名了网关没有的腿 ⇒ 整条链作废、一条都不发 —— 半条链会拿一条没人指定的腿跑完，
#    再把回执署成主腿的名字，那正是"批的不是跑的"在推理腿上的翻版。
try:
    from backend.services import super_clone_wan3 as _scw
except Exception:                                              # noqa: BLE001
    _scw = None
_ac0 = gw.settings.REPLICA_WAN3_ANALYZE_PURPOSE
try:
    gw.settings.REPLICA_WAN3_ANALYZE_PURPOSE = "magpie_qfmodel:1,magpie_typo:1"
    _chain_bad = _scw._analyze_lanes() if _scw else ["读不到函数"]
    gw.settings.REPLICA_WAN3_ANALYZE_PURPOSE = "mimo_pro"       # 老写法（裸一条）
    _chain_one = [l["purpose"] for l in (_scw._analyze_lanes() if _scw else [])]
    gw.settings.REPLICA_WAN3_ANALYZE_PURPOSE = "magpie_qfmodel:2,mimo:1"
    _chain_two = [(l["purpose"], l["tries"]) for l in (_scw._analyze_lanes() if _scw else [])]
finally:
    gw.settings.REPLICA_WAN3_ANALYZE_PURPOSE = _ac0
check("L11 链里点名了网关 video.analyze 没有的腿 ⇒ 整条链作废（空表 ⇒ 调用方在发任何请求之前拒）",
      _chain_bad, [])
check("L12 老写法（裸一条 `mimo_pro`）仍然合法 = 只有一条腿的链，不用改码就能回退",
      _chain_one, ["mimo_pro"])
check("L12b 串形 `purpose:次数` 真被解析（次数不是装饰：链上第一条挂了要重试几次由它定）",
      _chain_two, [("magpie_qfmodel", 2), ("mimo", 1)])
check("L12c 真读一次：现在生效的链就是用户拍的那三条、按他拍的序（qfmodel 主 → opencode mimo → 小米直连 flash）",
      [l["purpose"] for l in (_scw._analyze_lanes() if _scw else [])],
      ["magpie_qfmodel", "magpie_mimo", "mimo"])

_vs = gw.settings.MAGPIE_VISION_LANES
try:
    gw.settings.MAGPIE_VISION_LANES = _vs + ",magpie_glm:2"      # 变异：往带图腿表里塞 glm
    _mut = "magpie_glm" in _purps("vision")
finally:
    gw.settings.MAGPIE_VISION_LANES = _vs
check("L8 变异：config 的带图腿串里塞 glm ⇒ L1 立刻抓到（证明它数的是真表，不是抄死在断言里的字符串）", _mut, True)

_ts = gw.settings.MAGPIE_TEXT_LANES
try:
    gw.settings.MAGPIE_TEXT_LANES = "magpie_unknown:3"           # 坏腿名（拼错/以后改名）
    _bad = [gw.magpie_lane_specs("magpie", "text"), gw._magpie_chat(
        "http://x/v1", "k", gw.magpie_lane_specs("magpie", "text"), [], sleep=0,
        _call=lambda *a, **kw: ("不该被调到", None, {}))[0]]
finally:
    gw.settings.MAGPIE_TEXT_LANES = _ts
check("L9 腿名认错 ⇒ 整张表作废并【一条都不发】（半张表更危险：会悄悄用一条没人指定过的腿跑）",
      _bad, [[], None])

_p403 = ("HTTP 403: {\"message\": \"OpenCode Zen: free models aren't available from your current network "
         "because we detected abusive traffic from this IP address\"}")
_fake403 = lambda base, key, model, msgs, temperature=0.7, max_tokens=0, extra=None: (None, _p403, {})
_tx, _e10, _u10, _l10 = gw._magpie_chat("http://x/v1", "k", gw.magpie_lane_specs("magpie", "text"), [],
                                       sleep=0, _call=_fake403)
check("L10 🔴 真接线（假传输层回 403，原话含 abusive traffic/current network）⇒ 拼出的拒因必须点出【出口 IP】"
      "（今天整段时间没人看得出这条腿死在 IP 封禁，而不是模型坏了）",
      [("出口 IP" in _e10), ("abusive traffic" in _e10)], [True, True])
check("L10c 403 不是抖动 ⇒ 每条腿各试一次就换（数出来 = 表里的腿数，不是退避空转）",
      [_e10.count("magpie_deepseek"), _e10.count("magpie_mimo")], [1, 1])
check("L10b 反向对照：同一份样例走当年那版字符切片（[-3:]）⇒ 结论整个丢掉（证明 L10 咬的是真串，不是它自己的注释）",
      ("出口 IP" in ("magpie 三条 free 腿全部失败：%s" % " | ".join([_p403])[-3:])), False)

# ── M 组：🔒 引擎锁定位必须【现读唯一源】，字段表里不许写死模型名（2026-10-22 现网不一致）──
#    现网形状：2026-10-21 把 1080P 改成"按段长二选一"（唯一源 = .env 两键 + wan30_pick_1080p_by_duration），
#    而 SKILL.md 的 engine_lock 行还写死改名前的 `ME-wan30-1080p-1055` —— 那个名字此刻就在网关的【不接名单】里。
#    E3 只比 provider 子串，所以它对不上也照样绿 ⇒ 这一组补的就是"模型名"这一半。
_LOCK_MODEL_RE = re.compile(r"(?i)\b(?:ME|PIX|S|X|J)[-_]?WAN3\w*")


def lock_literal_problems(pairs):
    """pairs = [(档名, 锁定位显示值)]。锁定位里写死【模型 id】= 第二份真相。

    🔴 2026-10-30 换芯补的第二判：原来只有一条正则（`(ME|PIX|S|X|J)…WAN3`，那是 tianyue 那批名字的形状），
       换芯后这条 lane 的模型 id 变成 `wan3.0-video-prime` —— 老正则匹配不到它 ⇒ 谁把【现役那个 id】
       写死进字段表，M5 会安静地放行（判据跟不上对象 = 假保险）。
       第二判不写新字面量，而是拿 `gateway.resolve()` 现读出来的那个 id 去比：
       钉的规则是"字段表里不许出现此刻的模型 id"，换渠道/改名都自动继续有效。
       （档位族名如 "Wan 3.0 1080P" 不算模型 id —— 那是给客户看的类别词，写死它不会漂。）
    """
    bad = []
    live = ""
    try:
        live = str(gw.resolve("video.generate", "wan3-prime").get("model") or "").strip()
    except Exception:                                            # noqa: BLE001
        live = ""
    for name, val in pairs:
        hits = sorted(set(_LOCK_MODEL_RE.findall(val or "")))
        if live and live.lower() in str(val or "").lower():
            hits = sorted(set(hits) | {live})
        if hits:
            bad.append("%s 的 🔒 锁定位写死了模型名 %s ⇒ 唯一源一改就漂（该由 engine_lock_view 现读）" % (name, hits))
    return bad


def _lock_view(provider="yz30", model="wan3.0-video-prime", price=4):
    """跑【真函数 engine_lock_view】，只把它的【输入源】打桩：路由 provider + resolve 的模型名 + unit_price 单价。

    🔴 2026-10-30 换芯后 tianyue lane 整条拆除，`wan30_accepted_models` 那类候选表函数已不存在；
       现役口径 = 按路由 provider 分流，yz30 从 resolve()/unit_price() 现读模型与单价。
       M 组钉的就是这套现役规则（别再拿已删渠道的旧名单当期望值 —— 那是"断言为错的理由通过"）。
    """
    key = ("video.generate", "wan3-prime")
    real_route = dict(gw.ROUTES.get(key) or {})
    real_res, real_up = gw.resolve, gw.unit_price
    gw.ROUTES[key] = dict(real_route, provider=provider)
    gw.resolve = lambda *a, **kw: {"model": model, "provider": provider}
    gw.unit_price = lambda *a, **kw: price
    try:
        return ma.engine_lock_view("回落值", "回落说明")
    finally:
        gw.ROUTES[key] = real_route
        gw.resolve, gw.unit_price = real_res, real_up


_two = ["ME-WAN3-prime-1080p-1055", "PIX-Wan30-pro-1080p-1055-se"]
_v2, _n2 = _lock_view(provider="yz30", model="wan3.0-video-prime", price=4)
check("M1 provider=yz30 ⇒ 锁定位现读这家的模型名与【按秒】单价，且【不出现】别家的旧模型名"
      "（串味就是'表单写 A、机器跑 B'的展示版）",
      [_v2, "按秒计费 4 分/秒" in _n2, "分/秒" in _n2, "占位" in _n2,
       _two[0] not in _v2 + _n2, _two[1] not in _v2 + _n2],
      ["yz30 / wan3.0-video-prime", True, True, True, True, True])
_v0, _n0 = _lock_view(provider="yz30", model="", price=4)
check("M2 yz30 但【读不到模型名】 ⇒ 原样回落字段表那串，绝不自己编一个模型名（宁可旧，不可假）",
      [_v0, _n0], ["回落值", "回落说明"])
_v3, _n3 = _lock_view(provider="tianyue", model=_two[0], price=60)
check("M3 provider 不是 yz30（tianyue lane 已拆除）⇒ 回落字段表，不许拿别家名单当现役显示",
      [_v3, _n3], ["回落值", "回落说明"])
# ---- M7：单价也走同一份现读（改价不改码，表单跟着变） ----
_v7, _n7 = _lock_view(provider="yz30", model="wan3.0-video-prime", price=7)
check("M7 单价现读 unit_price：值是这家模型、说明里是【7 分/秒】",
      [_v7, "按秒计费 7 分/秒" in _n7], ["yz30 / wan3.0-video-prime", True])
check("M7b 反向对照：这一家【读不到模型名】⇒ 必须回落到字段表那串，不许自己编一个"
      "（宁可旧，不可假 —— 与 M3 同一条规矩，换谁都守）",
      list((lambda _res_real, _key, _real_route: (
          gw.__setattr__("resolve", lambda *a, **kw: {"model": "", "provider": "yz30"}),
          gw.ROUTES.__setitem__(_key, dict(_real_route, provider="yz30")),
          ma.engine_lock_view("回落值", "回落说明"),
          gw.ROUTES.__setitem__(_key, _real_route),
          gw.__setattr__("resolve", _res_real))[2])(
          gw.resolve, ("video.generate", "wan3-prime"),
          dict(gw.ROUTES.get(("video.generate", "wan3-prime")) or {}))),
      ["回落值", "回落说明"])
_lv, _ln = ma.engine_lock_view("回落值", "回落说明")          # 不打桩 = 真唯一源（现役那条 lane）
_now = [str(m).strip() for m in ([gw.resolve("video.generate", "wan3-prime").get("model")] or []) if m]
check("M4 真读一次：表单显示的模型名 == 网关【此刻这条路由】解析出的那一条（改了 .env 表单跟着改）",
      # 🔴 原来写的是 `[m in (...) for m in models] and len(models) > 0` —— 一个非空 list 永远是真值，
      #    所以 `[False] and True` 回的是 True ⇒ 这条断言【从来没牙】（今天才发现的假绿，改成 all()）。
      [bool(_now), all(m in (_lv + _ln) for m in _now)], [True, True])
check("M4b 反向对照：现役这条 lane 的模型名被写进锁定位字段表原文 ⇒ M5 必须抓到（第二份真相）",
      bool(lock_literal_problems([("super-clone",
                                   str(gw.resolve("video.generate", "wan3-prime").get("model") or "x"))])), True)
check("M5 各档的 🔒 锁定位里不许出现 wan30 模型字面量（值交给现读，字段表只留回落串）",
      lock_literal_problems([(n, lock_value_raw(f, "engine_lock")) for n, _s, f, _t in lanes]), [])
check("M5b 反向对照：把改名前那条旧模型名塞回锁定位 ⇒ M5 必须红（不然它是空跑的绿）",
      bool(lock_literal_problems([("super-clone", "tianyue / ME-wan30-1080p-1055")])), True)
check("M5c 反向对照：现读覆盖后的 value 里【有】模型名时，M5 读原文不误伤（钉的是字段表，不是现读结果）",
      [lock_value_raw([{"key": "engine_lock", "type": "locked", "value": "tianyue / ME-WAN3-prime-1080p-1055",
                        "value_raw": "tianyue / Wan 3.0 1080P（模型名由网关现读）"}], "engine_lock"),
       lock_value([{"key": "engine_lock", "type": "locked", "value": "tianyue / ME-WAN3-prime-1080p-1055",
                    "value_raw": "tianyue / Wan 3.0 1080P（模型名由网关现读）"}], "engine_lock")],
      ["tianyue / Wan 3.0 1080P（模型名由网关现读）", "tianyue / ME-WAN3-prime-1080p-1055"])
_m6 = str(gw.resolve("video.generate", "wan3-prime").get("model") or "").strip()
check("M6 现役 lane 固定一条模型：路由解析出的模型 == 网关常量 _YZ30_MODEL，且提交腿【当场拒】换档"
      "（源码里有 model != _YZ30_MODEL 的 0 分拒绝，换档=重新报价而不是悄悄切）",
      [_m6 == gw._YZ30_MODEL, "if model != _YZ30_MODEL" in _gw_src], [True, True])

print(_safe("\n== 引擎单一源闸：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL))))
for f in FAIL:
    print(_safe("  ✗ " + f))
sys.exit(1 if FAIL else 0)
