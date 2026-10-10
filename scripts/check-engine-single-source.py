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

print(_safe("\n== 引擎单一源闸：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL))))
for f in FAIL:
    print(_safe("  ✗ " + f))
sys.exit(1 if FAIL else 0)
