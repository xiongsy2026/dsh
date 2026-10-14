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
"""
import io
import os
import re
import sys
import ast

sys.path.insert(0, r"D:\video-skill")
sys.path.insert(0, r"C:\dsh\scripts")

from backend import gateway as gw                      # noqa: E402
from backend import skill_contract as sc               # noqa: E402
from backend import model_admin as ma                  # noqa: E402

WS = r"C:\dsh"
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
WAN3 = r"D:\video-skill\backend\services\wan3_client.py"
SRC = r"D:\video-skill\backend\services\super_clone_wan3.py"


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
check("G0 前提：clone-r2v 确实不在 ROUTES 里（在的话这条反向对照就没牙）", BAD_SPEND in ROUTES, False)
bad = lane_problems(BAD_SPEND, [{"key": "engine_lock", "type": "locked", "label": u"生成引擎",
                                "options": "yz-prime / wan3.0-video-prime"}],
                    ROUTES, False, True)
check("G1 反向对照：spend 换成 clone-r2v ⇒ 报路由缺失",
      any(u"ROUTES" in x for x in bad), True)
check("G2 反向对照：同一份输入 ⇒ 问题条数 >= 2（缺路由 + 吃兜底价）", len(bad) >= 2, True)
mut_price = lane_problems(sp, [{"key": "engine_lock", "type": "locked", "label": u"生成引擎",
                               "options": "yz-prime / wan3.0-video-prime"}], ROUTES, False, True)
check("G3 变异：把单价登记摘掉 ⇒ 报兜底", any(u"兜底" in x for x in mut_price), True)
mut_lock = lane_problems(sp, [{"key": "engine_lock", "type": "locked", "label": u"生成引擎",
                              "options": "someother / wan3.0"}], ROUTES, True, True)
check("G4 变异：engine_lock 换成别的 provider ⇒ 报不一致", any("provider" in x for x in mut_lock), True)
mut_pick = lane_problems(sp, [{"key": "engine_lock", "type": "locked", "label": u"生成引擎",
                              "options": "yz-prime / wan3.0-video-prime"},
                             {"key": "engine", "type": "select", "label": u"生成引擎"}],
                        ROUTES, True, True)
check("G5 变异：表单多一个引擎 select ⇒ 判据报『还在让客户选』", any(u"让客户选" in x for x in mut_pick), True)
check("G6 变异对照没把正解一起判红（正解输入仍是 0 问题）",
      lane_problems(sp, [{"key": "engine_lock", "type": "locked", "label": u"生成引擎",
                         "options": "yz-prime / wan3.0-video-prime"}], ROUTES, True, True), [])

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
_py = [sys.executable, os.path.join(r"D:\video-skill\backend", "gateway.py"), "prices"]
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
SVC_ENG = r"D:\video-skill\backend\services\super_clone_wan3.py"
RUN_ENG = r"C:\dsh\.dsh\skills\super-clone\runner\run.py"
APP_ENG = r"D:\video-skill\app.py"
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
        seg = app_text[i_ep:i_ep + 3500]
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

# J12：audio / reference_audio 到底送没送 —— 判"送没送"要用真函数的返回值，不看注释也不看转储。
_wc_src = io.open(WAN3, encoding="utf-8", errors="replace").read()
_v12, _e12 = gw._yz_prime_payload({"prompt": "x", "duration": 15, "output_path": "C:/x/a.mp4",
                                  "audio": False, "audio_urls": ["https://d.example/a.wav"]})
_v12t, _ = gw._yz_prime_payload({"prompt": "x", "duration": 15, "output_path": "C:/x/a.mp4",
                                 "audio": True})
_v12n, _ = gw._yz_prime_payload({"prompt": "x", "duration": 15, "output_path": "C:/x/a.mp4"})
check("J12 上游参数卡合法的两个音频入参【真送出去】：audio 是布尔、reference_audio 落进 media",
      [_e12, _v12["body"].get("audio"),
       [m["type"] for m in _v12["body"].get("media") or [] if m["type"] == "reference_audio"]],
      [None, False, ["reference_audio"]])
check("J12b audio=true 也照送；调用方没提这个参数时【不凭空塞一个】（丢参与瞎补都错）",
      [_v12t["body"].get("audio"), "audio" in _v12n["body"]], [True, False])
check("J12c 提交腿把上层的 audio 一路带到 params（以前它收了参数就丢）",
      '"audio": bool(audio)' in _wc_src, True)
check("J12d 转储要说真话：现在确实送上了，同时必须写明『语义未实测 ⇒ 出片要 ASR 复验』",
      ['"audio_param_sent_to_lane": True' in src_svc, "ASR 复验" in src_svc,
       '"reference_audio_sent": bool(audio_urls)' in src_svc], [True, True, True])

print(_safe("\n== 引擎单一源闸：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL))))
for f in FAIL:
    print(_safe("  ✗ " + f))
sys.exit(1 if FAIL else 0)
