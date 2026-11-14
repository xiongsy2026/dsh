# -*- coding: utf-8 -*-
"""报价出处闸（2026-10-11 现网 session-241ea3f7 08:23 复盘）。

钉两条规则，不钉某一天的数字：
  ① 报价五格（单价/段数/预计总扣分/余额/实扣）必须能在盘上找到【出处】——
     单价→价目表、余额→账本、段数与总扣分→该会话的 pending（只有 approve 建得出来）、
     实扣→素材分析台账的【合计】（报单次 = 少报重复消耗）。
  ② 消息里出现「批准入口/批准页/approval」字样 ⇒ 该会话必须有 pending 或未过期条子。
     拦的是那个形状：印一条 /approval 链接，而页面上没有属于这条会话的可批项（客户点进空位）。

A 组 纯函数真值表（判据自己不读盘，能整块抠出来跑）
B 组 真跑 approve（临时 approvals + 临时 ledger + 抑制弹窗）⇒ 照抄它的输出必须全绿
C 组 现网真产物：08:23 那条消息 + 当时盘上的 facts 快照 ⇒ 必须红在正确的四个字段
D 组 接线与「判据只一份」对照（批准页不许自己再实现一套核对）
E 组 变异对照：摘掉判据 / 把批准页的会话筛选删掉 ⇒ 对应断言必须失配
全离线、0 消耗：绝不真建到线上账本，绝不弹真窗（GATEWAY_APPROVAL_POPUP=0）。
"""
import io
import json
import os
import re
import subprocess
import sys
import tempfile

BS = chr(92)
WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, WS)
sys.path.insert(0, BACKEND)
from backend import gateway as gw            # noqa: E402

GW_PY = os.path.join(BACKEND, r"gateway.py")
MA_PY = os.path.join(BACKEND, r"model_admin.py")
PASS, FAIL = [], []


def _safe(s):
    """按【输出流自己的编码】errors=replace。绝不 reconfigure（会造"零输出+exit 0"的静默假绿）。"""
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        s.encode(enc)
        return s
    except (UnicodeEncodeError, LookupError):
        return s.encode(enc, "replace").decode(enc, "replace")


def check(name, got, want):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(_safe("%-4s %-54s got=%r want=%r" % ("PASS" if ok else "FAIL", name, got, want)))


def fields_of(problems):
    """把问题列表归成【哪个字段出的问题】—— 断言钉字段，不钉条数（条数会随文案漂）。"""
    out = set()
    for p in problems:
        m = re.match(r"^(单价|段数|预计总扣分|余额|实扣|没把|消息里)", p)
        out.add(m.group(1) if m and m.group(1) != "没把" else ("原文未贴" if p.startswith("没把") else "批准入口"))
    return sorted(out)


# 现网 08:23 那条消息（真产物，逐字抄自客户面会话；数字与文案就是被判的对象）
REAL_MSG = (
    "报价（现读 2026-10-10）\n\n"
    "项\t值\n"
    "路由\tvideo.generate/wan3-prime · provider yz-prime · 1080P\n"
    "单价\t60 分/段\n"
    "段数\t1 段（15.02s 整片）\n"
    "预计总扣分\t60 分\n"
    "素材分析\t已入账（video.analyze 实扣 40 分，免批但记台账）\n"
    "当前余额\t99544 分\n\n"
    "批准入口：\nhttp://127.0.0.1:8100/approval\n — 人点【批准，开始跑】后我自动提交。\n\n跑不跑？")
# 当时盘上读到的出处（quote_facts 的真返回，快照进 fixture ⇒ 判的是规则，不是今天的盘）
REAL_FACTS = {"session": "session-241ea3f7-f449-470a-96a8-faa81af340e2", "account": "default",
              "pending_id": "", "quote_text": "", "count": None, "credits": None,
              "grant_ids": [], "balance": 99532, "charged": [40, 40, 38], "charged_total": 118}

# ── A 组 纯函数真值表 ────────────────────────────────────────────────────────────
check("A1 五格抓取·别名不重复计数（预计总扣分含预计扣分）",
      gw.quote_numbers_in(REAL_MSG),
      [("单价", 60), ("段数", 1), ("预计总扣分", 60), ("余额", 99544), ("实扣", 40)])
check("A1b 反向对照·表格与全角冒号两种写法都抓得到",
      [n for _, n in gw.quote_numbers_in("单价：60 分/段 | 段数 | 1 | 当前余额：99532")], [60, 1, 99532])
check("A2 无 pending·段数与总扣分无出处（单价/余额各有自己的出处，不牵连）",
      set(fields_of(gw.quote_problems(REAL_MSG, REAL_FACTS, unit=60))), {"段数", "预计总扣分", "余额", "实扣"})
check("A3 单价现读对上⇒绿；错一分⇒红；价目表读不到⇒红（不拿消息当自己的出处）",
      [gw.quote_problems("单价 60 分/段", REAL_FACTS, unit=60) == [],
       len(gw.quote_problems("单价 61 分/段", REAL_FACTS, unit=60)) == 1,
       len(gw.quote_problems("单价 60 分/段", REAL_FACTS, unit=None)) == 1], [True, True, True])
check("A4 余额：对 99532 绿、错 99544 红、账户读不到（None）也红",
      [gw.quote_problems("当前余额 99532 分", REAL_FACTS, unit=60) == [],
       len(gw.quote_problems("当前余额 99544 分", REAL_FACTS, unit=60)) == 1,
       len(gw.quote_problems("当前余额 1 分", dict(REAL_FACTS, balance=None), unit=60)) == 1],
      [True, True, True])
check("A5 实扣只能等于台账【合计】：报单次 40 红、报 118 绿、台账空也红",
      [len(gw.quote_problems("实扣 40 分", REAL_FACTS, unit=60)) == 1,
       gw.quote_problems("实扣 118 分", REAL_FACTS, unit=60) == [],
       len(gw.quote_problems("实扣 0 分", dict(REAL_FACTS, charged=[], charged_total=0), unit=60)) == 1],
      [True, True, True])
PEND = {"session": "s1", "account": "default", "pending_id": "pend_x",
        "quote_text": "路由：video.generate/wan3-prime\n单价：60 分/段\n段数：2\n预计扣分：120\n当前余额：99532",
        "count": 2, "credits": 120, "grant_ids": [], "balance": 99532, "charged": [6], "charged_total": 6}
GOOD_MSG = ("路由：video.generate/wan3-prime\n单价：60 分/段\n段数：2\n预计扣分：120\n当前余额：99532\n"
            "实扣 6 分\n批准入口：http://127.0.0.1:8100/approval?session=s1")
check("A6 有 pending·五格照抄 approve 的输出 ⇒ 全绿（判据不许把合规报价也拦下）",
      gw.quote_problems(GOOD_MSG, PEND, unit=60), [])
check("A7 有 pending 但没贴原始报价行 ⇒ 只红在「原文未贴」这一条",
      fields_of(gw.quote_problems("段数 2 预计总扣分 120 余额 99532 单价 60", PEND, unit=60)), ["原文未贴"])
check("A8 批准入口：有 pending 绿、无 pending 红、散文里的「批准」不误伤",
      [gw.approval_entry_problems(GOOD_MSG, PEND) == [],
       len(gw.approval_entry_problems(REAL_MSG, REAL_FACTS)) == 1,
       gw.approval_entry_problems("等你批准这条方案我就跑", REAL_FACTS) == []], [True, True, True])

# ── B 组 真跑 approve（临时 approvals/ledger + 抑制弹窗），证「照抄就绿」 ──────────
# 🔴 打桩判定口：2026-10-11 加了"报价前必须回填过表"的前置（approve 会问 8100），
#    闸必须有测试缝 —— 直接打线上 8100 等于测一次就往生产状态里写一条真回填。
import http.server                                             # noqa: E402
import threading                                               # noqa: E402

STUB = {"payload": {"ok": True, "show": True, "prefilled": True, "submitted": False,
                    "prefill_gap": [], "unfilled_required": [], "loose_materials": []}}


class _StubH(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        b = json.dumps(STUB["payload"], ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


_srv = http.server.HTTPServer(("127.0.0.1", 0), _StubH)
threading.Thread(target=_srv.serve_forever, daemon=True).start()
STUB_URL = "http://127.0.0.1:%d/api/skills/form?session=" % _srv.server_address[1]
gw.FORM_GATE_URL = STUB_URL

tmp = tempfile.mkdtemp(prefix="quote-prov-")
APPR = os.path.join(tmp, "appr.json")
LEDG = os.path.join(tmp, "ledger.sqlite")
env = dict(os.environ, GATEWAY_SPEND_APPROVALS=APPR, GATEWAY_LEDGER=LEDG,
           GATEWAY_APPROVAL_POPUP="0", PYTHONIOENCODING="utf-8:replace",
           GATEWAY_FORM_GATE=STUB_URL)
env.pop("PYTHONUTF8", None)
TASK = r"C:" + BS + "dsh" + BS + "productions" + BS + "ef1ba285"
# 🔴 2026-10-30 换芯补的一步：`video.generate/wan3-prime` 现在是【按秒】计价的 lane（provider yz30，
#    4 分/秒），而 `count_from_args` 对按秒的 lane 只认盘上的提交计划 —— 真产物 ef1ba285 是 10-11 那批，
#    根本没有 `_submit_plan.json` ⇒ approve 会在"算不出秒数"那一支就先拒建，
#    B1/B3/B5/B6/B7/B8 全部从"报价五格的出处"退化成"被更早的拒因打断"（测不到本意，还看着像换芯弄坏了）。
#    所以这里给闸自己造一份 15 秒单窗的计划：单段 15s × 4 分/秒 = 60 分 ——
#    与换芯前那条 60 分/段 的数字【恰好同量级】，B3 的期望值一个都不必改（改了就等于把新口径写死进闸）。
TASK = os.path.join(tmp, "quote-task-15s")
os.makedirs(os.path.join(TASK, "outputs"), exist_ok=True)
with open(os.path.join(TASK, "outputs", "_submit_plan.json"), "w", encoding="utf-8") as _pf:
    json.dump({"count": 1, "windows": [{"start": 0.0, "end": 15.0}], "target_duration": 15.0,
               "trim": None, "task_id": "quote-prov-fixture"}, _pf)
SID = "session-quoteprov"
# 先把账户种进【临时账本】：真账本里 default/local 早就有行，而 `Ledger.balance()` 对没行的账户
# 会**自动插一行 100000**（这就是"凭空余额"的来源）。2026-10-12 余额改成 known_balance 之后，
# 没行的账户报价正文会印"当前余额：?" —— 那是诚实，但 fixture 必须演的是现网形状，所以这里显式种行。
_seed = ("import sys;sys.path.insert(0,r'" + WS + "');"
         "from backend import gateway as g;"
         "print('seeded', g.Ledger(g._LEDGER_PATH).balance('default'))")
_ps = subprocess.run([sys.executable, "-c", _seed], capture_output=True, text=True,
                     encoding="utf-8", errors="replace", env=env, timeout=90)
check("B0 临时账本里种好 default 账户（不种=报价正文只能印 '?'，那是另一条断言 B8 要管的形状）",
      (_ps.stdout or "").strip().startswith("seeded"), True)
r = subprocess.run([sys.executable, GW_PY, "approve", "video.generate", "wan3-prime",
                    "--count-from", TASK, "--account", "default", "--session", SID,
                    "--scope", "出处闸自测：单段 15s 整片"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=180)
out = r.stdout or ""
check("B1 真跑 approve 退出码 0（临时账本、弹窗已抑制）", r.returncode, 0)
check("B2 stdout 里不含核销码（码进管道=agent 能自己伪造人证）",
      bool(re.search(r"\b[0-9A-Z]{6}\b.*核销|核销码", out)), False)
# facts 必须在【同一份临时账本】里读：本进程的 SPEND_APPROVALS_PATH/_LEDGER_PATH 指向线上文件，
# 拿它读临时条子会得到"没建上"的假红 ⇒ 用同样的 env 起一个只读子进程问 quote_facts。
_PROBE = ("import json,sys;sys.path.insert(0,r'" + WS + "');"
          "from backend import gateway as gw;print(json.dumps(gw.quote_facts(sys.argv[1],sys.argv[2])))")
r2 = subprocess.run([sys.executable, "-c", _PROBE, SID, "default"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    env=env, timeout=120)
facts_b = json.loads(r2.stdout.strip().splitlines()[-1])
check("B3 approve 建的那张 pending 现读得到（pending_id / count / credits 齐）",
      [bool(facts_b["pending_id"]), facts_b["count"], facts_b["credits"]], [True, 1, 60])
CANON = facts_b["quote_text"]
check("B4 把 approve 打出的报价正文原样贴进消息 ⇒ 五格全绿（照抄的人不该被拦）",
      gw.quote_problems(CANON, facts_b, unit=gw.unit_price("video.generate", "wan3-prime")), [])
_tampered_b5 = CANON.replace("当前余额：%s" % facts_b["balance"], "当前余额：1")
check("B5 变异：只把余额改成别的数 ⇒ 余额红（且改过的行不再等于原文，「原文未贴」同红=一条数改两处喊）\n"
      "     🔴 第一格先钉『这一刀真的改动了吗』：改动没生效（facts 里的余额根本没出现在正文里）时，\n"
      "     后面那格会拿一个空集合上期望之外的值假绿 —— 2026-10-12 加 known_balance 之后就是 this。",
      [_tampered_b5 != CANON,
       set(fields_of(gw.quote_problems(_tampered_b5, facts_b,
                                       unit=gw.unit_price("video.generate", "wan3-prime")))),
       "当前余额：" in CANON, str(facts_b.get("balance"))[:12]],
      [True, {"余额", "原文未贴"}, True, str(facts_b.get("balance"))[:12]])
check("B6 变异：段数与总扣分各改一分 ⇒ 恰好这两格红（不牵连单价/余额）",
      set(fields_of(gw.quote_problems(
          CANON.replace("段数：1", "段数：3").replace("预计扣分：60", "预计扣分：180"), facts_b,
          unit=gw.unit_price("video.generate", "wan3-prime")))), {"段数", "预计总扣分", "原文未贴"})
check("B7 approve 的 stdout 里那条批准地址带 ?session=（裸链接从此不是默认形状）",
      ("approval?session=" + SID) in out, True)
check("B8 🔴 余额那格写的不是数（'?' / 读不到）⇒ 必须红：数字解析器会跳过它，"
    "于是『五格全绿』其实是五格全空 —— 2026-10-12 换成 known_balance 后就是这个形状"
    "（正文印 当前余额：? 而判据读到自动补的 100000，B5 因改不动文本静默拿空集合）",
    [set(fields_of(gw.quote_problems(
        CANON.replace("当前余额：%s" % facts_b["balance"], "当前余额：?"), facts_b,
        unit=gw.unit_price("video.generate", "wan3-prime")))),
     gw.quote_problems("单价：60\n段数：1\n预计总扣分：60\n当前余额：?\n实扣：0",
                       dict(facts_b, pending_id="ok_x"), unit=60) != []],
    [{"余额", "原文未贴"}, True])

# ── F 组 报价前置：没回填过表 ⇒ 不给建批准条子（2026-10-11 16:30 现网那条形状）──────────
def blockers(payload):
    STUB["payload"] = payload
    return gw.form_prefill_blockers("session-x")


OKP = {"ok": True, "show": True, "prefilled": True, "submitted": False,
       "prefill_gap": [], "unfilled_required": [], "loose_materials": []}
check("F1 回填过且缺口为空 ⇒ 放行", blockers(dict(OKP)), [])
_p2 = blockers(dict(OKP, prefilled=False))
check("F2 从没回填过 ⇒ 拒一条，且给得出下一步命令（不是只说不行）",
      [len(_p2), "从没回填过" in _p2[0], "intent.py prefill" in _p2[0]], [1, True, True])
check("F3 客户自己交齐了（submitted）⇒ 算满足，不必先回填",
      blockers(dict(OKP, prefilled=False, submitted=True)), [])
_p4 = blockers(dict(OKP, prefill_gap=["video"],
                    loose_materials=[r"C:" + BS + "dsh" + BS + "productions" + BS + "t" + BS + "src" + BS + "a.mp4"]))
check("F4 缺口非空（素材躺在 src 没归位）⇒ 拒，并点名槽与闲料",
      [len(_p4), "video" in _p4[0], "a.mp4" in _p4[0]], [1, True, True])
_p5 = blockers(dict(OKP, unfilled_required=["product_name"]))
check("F5 必填格还空着 ⇒ 单独一条红因（与「素材没归位」分开，改法不一样）",
      [len(_p5), "product_name" in _p5[0]], [1, True])
check("F6 这条线没配表单 ⇒ 不在射程（hypit/omni 不能被这条规则误伤）",
      blockers({"ok": True, "show": False, "reason": "intent-has-no-form"}), [])
_p7 = blockers({"ok": True, "show": False, "reason": "no-intent"})
check("F7 没登记意图 ⇒ 拒（第一步没走，报价没有依据可核）",
      [len(_p7), "意图" in _p7[0]], [1, True])
check("F8 approve 没带 --session ⇒ 拒（不猜一个会话 id 去问判定口）",
      len(gw.form_prefill_blockers("")), 1)
_keep_url = gw.FORM_GATE_URL
gw.FORM_GATE_URL = "http://127.0.0.1:1/api/skills/form?session="
_p9 = gw.form_prefill_blockers("session-x")
gw.FORM_GATE_URL = _keep_url
check("F9 8100 真没起来 ⇒ 明说「判定口没回话」，不谎报成「这条会话没回填」（fail-closed 但归因要准）",
      [len(_p9), "没回话" in _p9[0], "8100" in _p9[0]], [1, True, True])
STUB["payload"] = dict(OKP, prefilled=False)
_before = len((json.load(open(APPR, encoding="utf-8")).get("pending") or []) if os.path.isfile(APPR) else [])
_r1 = subprocess.run([sys.executable, GW_PY, "approve", "video.generate", "wan3-prime",
                      "--count-from", TASK, "--account", "default", "--session", "session-noprefill",
                      "--scope", "F10 现网形状：没回填就想报价"],
                     capture_output=True, text=True, encoding="utf-8", errors="replace",
                     env=dict(env, GATEWAY_FORM_GATE=STUB_URL), timeout=180)
_after = len((json.load(open(APPR, encoding="utf-8")).get("pending") or []) if os.path.isfile(APPR) else [])
check("F10 真跑 approve·没回填 ⇒ 退出码非 0、pending 一张都没多（0 额度）",
      [_r1.returncode != 0, _after - _before, "从没回填过" in (_r1.stdout or "")], [True, 0, True])
STUB["payload"] = dict(OKP)

# ── C 组 现网真产物：那条消息必须红在正确的字段 ─────────────────────────────────
cp = gw.quote_problems(REAL_MSG, REAL_FACTS, unit=60)
check("C1 08:23 真消息红在【段数/预计总扣分/余额/实扣】四格（单价 60 与价目表一致，不牵连）",
      set(fields_of(cp)), {"段数", "预计总扣分", "余额", "实扣"})
check("C2 它同时红在「没跑 approve 却印批准入口」",
      len(gw.approval_entry_problems(REAL_MSG, REAL_FACTS)), 1)
check("C3 红因里逐字带着盘上出处（余额现读 99532、实扣合计 118）——给人对着改的，不是天书",
      [any("99532" in p for p in cp), any("118" in p for p in cp)], [True, True])

# ── D 组 判据只一份 + 页面接线 ──────────────────────────────────────────────────
ma = io.open(MA_PY, encoding="utf-8", errors="replace").read()
_s = ma.index("APPROVAL_PAGE = r")
# 🔴 这块是【两段拼接】：r"""…""" + skill_form_page.WS_TOKENS + r"""…""" —— 拿"第一个三引号"当结尾
#    只会切到第一段（JS 全在第二段），D1 就变成对空串断言的真空判绿。整条赋值语句切到底才对。
page = ma[_s:ma.index('\n"""\n', _s)]
check("D0 页面块真的取到了（切空=后面三条全成真空判绿）",
      [len(page) > 3000, "URLSearchParams" in page], [True, True])
check("D1 批准页按 ?session= 筛卡，空结果必须【说明是哪一种空】（不是空列表、也不是一句死话术）",
      [("URLSearchParams(location.search).get('session')" in page), ("session_state" in page),
       ("没有待批条子" in page), ("会话 ${E(tail(p.session))}" in page)], [True, True, True, True])
gwsrc = io.open(GW_PY, encoding="utf-8", errors="replace").read()
check("D2 反向对照·页面里不许长出第二份数字核对（判据只在 gateway 一份）",
      [k in page for k in ("unit_price(", "quote_problems(", "charged_total")], [False, False, False])

# ── D4/D5/D6（2026-10-14 现网）：批准页的空态分五种状态，判据只在 gateway 一份 ──
# 现场：客户 16:14 就在批准窗点了【批准，开始跑】，条子 ok_cda1bfd1 处于"已批准·0/1 段待消费"，
#      而页面只读 /api/spend/pending（那时已空），于是对所有空结果一律印
#      "Agent 还没跑 gateway.py approve" —— 那句当场是错的，还会把人推去"再建一张"（批两遍）。
_NOW = 1791965700.0                       # 2026-10-14 16:15:00，固定住免得闸随时间漂
_LIVE = 1791969300.0                      # 明天到期
_DEAD = 1791962100.0                      # 一小时前就过期
def _g(used, count, exp, **kw):
    d = {"id": "ok_test", "capability": "video.generate", "purpose": "wan3-prime", "account": "local",
         "session": "s-a", "count": count, "used": used, "credits": 60, "used_credits": used * 60,
         "created": _NOW - 60, "expires": exp, "attest": {"method": "dsh-card-click", "at": _NOW - 36}}
    d.update(kw)
    return d
_ST_FIX = {
    "pending": ({"pending": [{"id": "pend_1", "session": "s-a", "capability": "video.generate",
                              "purpose": "wan3-prime", "account": "local", "count": 1,
                              "credits": 60, "expires": _LIVE}], "grants": []}, "pending"),
    "approved-waiting": ({"pending": [], "grants": [_g(0, 1, _LIVE)]}, "approved-waiting"),
    "consumed": ({"pending": [], "grants": [_g(1, 1, _LIVE)]}, "consumed"),
    "expired": ({"pending": [], "grants": [_g(0, 1, _DEAD)]}, "expired"),
    "none": ({"pending": [], "grants": []}, "none"),
}
_stgot = {k: gw.approval_session_state("s-a", data=v, now=_NOW) for k, (v, _) in _ST_FIX.items()}
check("D4 五种状态各判各的（现网那条必须落 approved-waiting，不是 none）",
      [_stgot[k]["state"] for k in _ST_FIX], [v for _, v in _ST_FIX.values()])
check("D5 五态互不相同 + 话术对得上：已批过的那句要明说「不用再点」，从没建过的那句才提 approve",
      [len({v["state"] for v in _stgot.values()}) == 5,
       "不用再点" in _stgot["approved-waiting"]["text"],
       "approve" in _stgot["none"]["text"],
       _stgot["approved-waiting"]["head"] != _stgot["none"]["head"]], [True, True, True, True])
check("D5b 脚注也分状态：已批过的那一态【不许】再出现「让它先建条子」，"
      "而从没建过的那一态必须有 —— 否则只是把误导挪了个地方",
      [all("先建条子" not in x for x in _stgot["approved-waiting"]["foot"]),
       any("先跑 gateway.py approve" in x or "建条子" in x for x in _stgot["none"]["foot"]),
       all(isinstance(v["foot"], list) and v["foot"] for v in _stgot.values()),
       any("作废" in x for x in _stgot["pending"]["foot"])], [True, True, True, True])
check("D5c 已批准那一态不许借用【待批】的 15 分钟 TTL 说事（现网页脚就是这么误导的）",
      [any("15 分钟" in x for x in _stgot["approved-waiting"]["foot"]),
       "还剩 24 小时" in _stgot["approved-waiting"]["text"] or "还剩" in _stgot["approved-waiting"]["text"]],
      [False, True])
check("D6 变异对照·同一张条子只把到期时间挪到过去 ⇒ 必须从 approved-waiting 变 expired"
      "（证明它真在读 expires/used，不是把五句写死）",
      gw.approval_session_state("s-a", data={"pending": [], "grants": [_g(0, 1, _DEAD)]},
                                now=_NOW)["state"], "expired")
check("D6b 变异对照·把 grants 整份清空 ⇒ 同一会话必须退回 none（= 旧页面那句），"
      "说明现网那句只对【从没建过条子】成立",
      gw.approval_session_state("s-a", data={"pending": [], "grants": []}, now=_NOW)["state"], "none")
check("D6c 判据只有一份：状态词与段数核对都只在 gateway 里，页面拿到什么就念什么"
      "（页面里出现 approved-waiting / used 比较 = 长出第二份分类器）",
      [("approved-waiting" in gwsrc), ("approved-waiting" in page),
       ("- p.used" in page), ("approval_session_state(" in gwsrc)], [True, False, False, True])
check("D6d 接线：/api/spend/pending 带 session 时必须把判定一起回（handler 漏调 ⇒ 页面静默退回旧那句），"
      "且页面 fetch 真的把 session 带上",
      [("session_state" in ma and "approval_session_state(_qs)" in ma and "_qs" in ma),
       ("'/api/spend/pending'+(MYSID?'?session=" in page)], [True, True])
check("D6e 页面只渲染服务端给的 head/text/foot（不再自己判状态、也不再写死脚注），"
      "且兜底仍保留「没有待批条子」字样（D1 不许因为改造变空）",
      [("st&&st.head" in page), ("st&&st.text" in page), ("st.foot" in page),
       ("没有待批条子" in page), ("让它先建条子" in page)], [True, True, True, True, False])


check("D3 gateway 侧四个判据函数都在，且 approve 分支引用的是【带会话号】那条 URL",
      [all(("def " + fn) in gwsrc for fn in ("quote_numbers_in", "quote_facts",
                                            "quote_problems", "approval_entry_problems")),
       ("approval?session=" in gwsrc and gwsrc.count("% appr_url") >= 1),
       "approval 点一次" not in gwsrc], [True, True, True])

# ── E 组 变异对照：判据改空转 ⇒ C 组必须失配（证明它咬人） ──────────────────────
_ns = {"quote_problems": lambda *a, **k: [], "approval_entry_problems": lambda *a, **k: []}
check("E1 变异·把两个判据换成恒 [] ⇒ 现网那条消息就查不出来了（所以 C 组是真判据不是摆设）",
      [fields_of(_ns["quote_problems"](REAL_MSG, REAL_FACTS, unit=60)) == [],
       _ns["approval_entry_problems"](REAL_MSG, REAL_FACTS) == []], [True, True])
check("E2 变异·单价出处换成消息自己 ⇒ 「凭记忆写价」不再被看见（钉 unit 必须来自价目表）",
      len(gw.quote_problems("单价 999 分/段", REAL_FACTS, unit=60)) == 1, True)

import shutil  # noqa: E402
shutil.rmtree(tmp, ignore_errors=True)
check("收尾 临时目录已清（闸自己不许留残留）", os.path.isdir(tmp), False)

n = len(PASS)
print(_safe("\n=== 报价出处闸：%d 通过 / %d 失败 ===" % (n, len(FAIL))))
if FAIL:
    print(_safe("失败清单: " + " | ".join(FAIL)))
sys.exit(1 if FAIL else 0)
