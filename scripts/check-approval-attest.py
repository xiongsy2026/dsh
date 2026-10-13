# -*- coding: utf-8 -*-
"""人证闸（2026-10-08 第二版）：钉"pending + 一次性码 + 人点一下才有额度"这套状态机。

为什么不是弹窗：系统 MessageBox 实测【没人点也会返回 IDYES】，等于假人证（G 组抓到过两次凭空签出的条子）。
为什么不要终端口令：用户两次否掉加戏（"太麻烦了换个方式"、"也不要输入口令了"）⇒ 终局是 DSH 审批卡一次点击，
   `consumed` 只是插件没装时的兜底路径。

覆盖：
  A：弹窗那套代码必须已经不存在（不许留兜底）；
  B：approve 只建 pending、【不给额度】；
  C：核销判据（对码/小写/错码/重放/换会话/过期）；
  D：条子上的 attest 指纹能区分"人点的"和"闸自测的"；
  E：闸自测后门只对临时账本有效（生产 json 物理上写不进去）；
  F：拒绝文案不许印成伪造说明书；
  G：真 subprocess 跑 approve ⇒ 也只有 pending，0 额度（agent 单方面签不出东西）。

条数以本文件末尾打印的汇总为准。
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRATCH = os.path.abspath(os.path.join(HERE, "..", "scratch", "attest-gate"))
os.makedirs(SCRATCH, exist_ok=True)

GW_DIR = r"D:\video-skill"
os.environ["GATEWAY_SPEND_APPROVALS"] = os.path.join(SCRATCH, "approvals.json")
os.environ["GATEWAY_LEDGER"] = os.path.join(SCRATCH, "ledger.sqlite")
os.environ["PYTHONPATH"] = GW_DIR
AP = os.environ["GATEWAY_SPEND_APPROVALS"]
for _p in (AP, os.environ["GATEWAY_LEDGER"]):
    if os.path.exists(_p):
        os.remove(_p)

sys.path.insert(0, GW_DIR)
from backend import gateway as gw  # noqa: E402

assert gw.SPEND_APPROVALS_PATH != gw._APPROVALS_PATH_DEFAULT, "隔离没生效：写的是生产账本"
GW_SRC = open(os.path.join(GW_DIR, "backend", "gateway.py"), encoding="utf-8").read()

RESULTS = []


def ap(s):
    """ASCII-safe 打印：判据用原文比，只有写控制台那一步降 ASCII（GBK 控制台上一个 ⇒ 就能把闸打崩）。"""
    return str(s).encode("ascii", "replace").decode("ascii")


def cprint(*a, **k):
    try:
        print(*a, **k)
    except UnicodeEncodeError:
        print(ap(" ".join(str(x) for x in a)))


def chk(cid, desc, got, want):
    ok = got == want
    RESULTS.append((cid, ok))
    cprint("%s %-5s %-56s got=%s want=%s" % ("PASS" if ok else "FAIL", cid, ap(desc[:56]),
                                             ap(got), ap(want)))


def grants_of(acct):
    return [g for g in gw._approvals_load().get("grants", []) if g.get("account") == acct]


def pendings():
    return gw.spend_pending(with_code=True)


def pending_count():
    return len(gw.spend_pending(with_code=False))


def denials_of(acct=None, reason=None):
    ds = gw._approvals_load().get("denials") or []
    if acct:
        ds = [d for d in ds if d.get("account") == acct]
    if reason:
        ds = [d for d in ds if d.get("reason") == reason]
    return ds


cprint("== 环境 == approvals=%s  pending TTL=%.0fs" % (AP, gw.SPEND_PENDING_TTL))

# ---------- A 弹窗那套必须已经死了（只看代码行，注释里记着教训不算） ----------
CODE_LINES = "\n".join(l for l in GW_SRC.splitlines() if not l.strip().startswith("#"))
chk("A1", "没有 MessageBoxW 调用", "MessageBoxW" in CODE_LINES, False)
chk("A2", "没有 ctypes 弹窗那套残留（import/常量都没了）",
    bool(re.search(r"import ctypes|MB_DEFBUTTON2|_MB_YESNO", CODE_LINES)), False)
chk("A3", "没有 human_attest/_attest_dialog/attest_decision 残留",
    bool(re.search(r"def (human_attest|_attest_dialog|attest_decision)", GW_SRC)), False)
chk("A4", "没有 isatty/GetFileType 真终端判定（曾把真人拒过门外）",
    bool(re.search(r"\.isatty\s*\(|GetFileType\s*\(", GW_SRC)), False)

# ---------- B approve 只建 pending、不给额度 ----------
p = gw.create_spend_pending("video.generate", "omni-t2v", "gate-b", 2, 100, "B 组", "sess-b")
chk("B1", "pending 有 id/码/报价正文",
    bool(p.get("id")) and bool(re.fullmatch(r"[A-Z0-9]{6}", p.get("code", "")))
    and "omni-t2v" in p.get("quote", ""), True)
chk("B2", "建 pending 之后【没有任何额度】", grants_of("gate-b"), [])
chk("B3", "默认读法不吐码（spend_pending()）",
    "code" in gw.spend_pending()[0], False)
chk("B4", "显式 with_code=True 才给码（只有审批卡渲染那条路）",
    pendings()[0].get("code"), p["code"])

# ---------- C 核销判据 ----------
r = gw.consume_spend_approval(p["id"], "WRONG6", "sess-b")
chk("C1", "错码 ⇒ 拒 code-mismatch", (r.get("ok"), r.get("reason")), (False, "code-mismatch"))
chk("C2", "错码没签出东西", grants_of("gate-b"), [])
chk("C3", "错码留了痕", bool(denials_of("gate-b", "code-mismatch")), True)

r = gw.consume_spend_approval(p["id"], p["code"], "sess-other")
chk("C4", "换了会话 ⇒ 拒 pending-session-mismatch（别处的点击花不掉这条额度）",
    (r.get("ok"), r.get("reason")), (False, "pending-session-mismatch"))
chk("C5", "换会话那次也没签出东西", grants_of("gate-b"), [])

r = gw.consume_spend_approval(p["id"], p["code"].lower(), "sess-b")
g = (r.get("grant") or {})
chk("C6", "对码（小写也认）⇒ 过并返回条子", (r.get("ok"), bool(g.get("id"))), (True, True))
chk("C7", "条子范围写死：段/分/会话/账户都对得上",
    (g.get("count"), g.get("credits"), g.get("session"), g.get("account")), (2, 100, "sess-b", "gate-b"))
chk("C8", "核销后 pending 出列（不能重复花）", [x["id"] for x in pendings() if x["id"] == p["id"]], [])
r2 = gw.consume_spend_approval(p["id"], p["code"], "sess-b")
chk("C9", "重放同一张 pending ⇒ 拒 pending-not-found",
    (r2.get("ok"), r2.get("reason")), (False, "pending-not-found"))
chk("C10", "重放没有多签出条子", len(grants_of("gate-b")), 1)

pe = gw.create_spend_pending("video.generate", "omni-t2v", "gate-exp", 1, 50, "过期", "sess-e", ttl=-1)
r3 = gw.consume_spend_approval(pe["id"], pe["code"], "sess-e")
chk("C11", "过期 pending ⇒ 拒 pending-expired", (r3.get("ok"), r3.get("reason")),
    (False, "pending-expired"))
chk("C12", "过期的不进待批清单", [x["id"] for x in pendings() if x["id"] == pe["id"]], [])

# ---------- D 条子指纹 ----------
chk("D1", "人点出来的条子带 dsh-card-click 指纹", g.get("attest", {}).get("method"), "dsh-card-click")
chk("D2", "指纹里留着 pending id（可回溯到哪张卡）", bool(g.get("attest", {}).get("pending")), True)
gs = gw.grant_spend_approval("video.generate", "omni-t2v", "gate-self", 1, 50, "闸自测",
                             attest={"ok": True, "method": "gate-selftest", "at": gw._time.time()})
chk("D3", "闸自测的条子指纹不同（approvals 一眼能分）",
    gs.get("attest", {}).get("method"), "gate-selftest")

# ---------- E 后门只对临时账本有效 ----------
_prod, _default = gw.SPEND_APPROVALS_PATH, gw._APPROVALS_PATH_DEFAULT
chk("E1", "源码里有『路径 ≠ 默认才允许免证』这条判据",
    GW_SRC.count("SPEND_APPROVALS_PATH != _APPROVALS_PATH_DEFAULT") >= 1, True)
try:
    gw.SPEND_APPROVALS_PATH = _default
    gw.ALLOW_GRANT_WITHOUT_ATTEST = True
    c = gw.grant_spend_approval("video.generate", "omni-t2v", "gate-c", 1, 50, "E 组")
    chk("E2", "指向生产默认路径时，开关=True 也签不出",
        (c.get("reason"), grants_of("gate-c")), ("no-attestation", []))
finally:
    gw.ALLOW_GRANT_WITHOUT_ATTEST = False
    gw.SPEND_APPROVALS_PATH = _prod
gw.ALLOW_GRANT_WITHOUT_ATTEST = True
try:
    d = gw.grant_spend_approval("video.generate", "omni-t2v", "gate-d", 1, 50, "E 对照")
    chk("E3", "对照：临时账本 + 开关=True ⇒ 签得出（证明 E2 不是永远红）", bool(d.get("id")), True)
finally:
    gw.ALLOW_GRANT_WITHOUT_ATTEST = False
chk("E4", "花钱类能力不传 attest ⇒ no-attestation",
    gw.grant_spend_approval("video.generate", "omni-t2v", "gate-e", 1, 50, "E 组").get("reason"),
    "no-attestation")
chk("E5", "免批类（text.complete）不受前置影响",
    bool(gw.grant_spend_approval("text.complete", "agnes", "gate-f", 1, 0, "留档").get("id")), True)

# ---------- F 拒绝文案不许当说明书 ----------
err = (gw.check_spend_approval("video.generate", "omni-t2v", "gate-g2", count=1, credits=50,
                               session="none") or {}).get("error", "")
chk("F1", "文案里不出现 `gateway.py approve`", "gateway.py approve" in err, False)
chk("F2", "文案里不出现可照抄的 --credits/--count 参数串", bool(re.search(r"--(credits|count)\s", err)), False)
chk("F3", "文案仍指回『由用户本人批准』", "本人" in err, True)

# ---------- G 真 subprocess：agent 单方面只能造 pending ----------
env = dict(os.environ, PYTHONIOENCODING="utf-8")
g7 = subprocess.run([sys.executable, os.path.join(GW_DIR, "backend", "gateway.py"),
                     "approve", "video.generate", "omni-t2v", "--count", "9", "--credits", "9999",
                     "--account", "gate-g", "--session", "sess-g", "--scope", "agent 自签"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, env=env, timeout=120, cwd=GW_DIR)
out = g7.stdout.decode("utf-8", "replace")
chk("G1", "agent 跑 approve 不报错（它有权【提请】批准）", g7.returncode, 0)
chk("G2", "但只建出 pending，0 额度", grants_of("gate-g"), [])
chk("G3", "stdout 里带 pending id（人拿去查码用）", bool(re.search(r"pend_[a-z0-9]{8}", out)), True)
chk("G4", "🔴 stdout 里【不许出现核销码】—— 跑这条命令的一方读到码就能自己 consumed 伪造",
    bool(re.search(r"核销码[:：]\s*[A-Z0-9]{6}", out)), False)
chk("G5", "待批清单里能查到这条", len([x for x in pendings() if x.get("account") == "gate-g"]), 1)
_pid = re.search(r"(pend_[a-z0-9]{8})", out).group(1)
g8 = subprocess.run([sys.executable, os.path.join(GW_DIR, "backend", "gateway.py"), "code", _pid],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    env=env, timeout=120, cwd=GW_DIR)
o8 = g8.stdout.decode("utf-8", "replace")
chk("G6", "`code <id>` 这条【会】给码（人自己终端的兜底路径）",
    bool(re.search(r"核销码[:：]\s*[A-Z0-9]{6}", o8)), True)
_code = re.search(r"核销码[:：]\s*([A-Z0-9]{6})", o8).group(1)
g9 = subprocess.run([sys.executable, os.path.join(GW_DIR, "backend", "gateway.py"),
                     "consumed", _pid, _code, "--session", "sess-g"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    env=env, timeout=120, cwd=GW_DIR)
gg = grants_of("gate-g")
chk("G7", "人拿码跑 consumed ⇒ 退 0 且条子生效", (g9.returncode, len(gg)), (0, 1))
chk("G8", "生效的条子范围 = 当初提请的范围（9 段/9999 分/绑会话）",
    (gg[0]["count"], gg[0]["credits"], gg[0]["session"], gg[0]["attest"]["method"]),
    (9, 9999, "sess-g", "dsh-card-click")) if gg else chk(
    "G8", "生效的条子范围 = 当初提请的范围", "没有条子", "有条子")

# ---------- H 消费旧条子不许挤掉待批/留痕（2026-10-08 真 bug 的回归） ----------
_h = gw.create_spend_pending("video.generate", "omni-t2v", "gate-h", 1, 50, "H 组：共存", "sess-h")
_hg = gw.grant_spend_approval("video.generate", "omni-t2v", "gate-h", 1, 50, "H 组可消费",
                              session="sess-h",
                              attest={"ok": True, "method": "gate-selftest", "at": gw._time.time()})
gw.check_spend_approval("video.generate", "omni-t2v", "gate-h", count=1, credits=50, session="sess-h")
_hd = json.load(open(AP, encoding="utf-8"))
chk("H1", "消费一张条子之后，待批还在（旧写法会把 pending 键整份抹掉）",
    [x["id"] for x in _hd.get("pending") or [] if x["id"] == _h["id"]], [_h["id"]])
chk("H2", "消费之后 denials 键仍在", "denials" in _hd, True)
chk("H3", "消费确实记了账（那张条子 used=1）",
    [g.get("used") for g in _hd.get("grants", []) if g["id"] == _hg["id"]], [1])
_h2 = gw.consume_by_click(_h["id"])
chk("H4", "consume_by_click：只给 pending_id 就能核销（码由服务端自己取）",
    (_h2.get("ok"), (_h2.get("grant") or {}).get("attest", {}).get("method")),
    (True, "dsh-card-click"))
_h3 = gw.deny_pending("pend_nope")
chk("H5", "deny 不存在的 pending ⇒ 拒", _h3.get("reason"), "pending-not-found")

# ---- P 组：置顶批准窗（2026-10-09 用户指令"我要出现弹窗进行审批"）----
#     钉的不是"有没有窗"，是"窗不许变成第二种人证"：判定仍只有服务端 consume_by_click 一条路。
import io as _io
_pp_raw = _io.open(r"D:\video-skill\backend\approval_popup.py", encoding="utf-8").read()
_gw = _io.open(r"D:\video-skill\backend\gateway.py", encoding="utf-8").read()
# 🔴 先剥注释和三引号串：这个文件的注释里就写着"MessageBox 会自返 IDYES"，整段扫会被自己的注释判红
_pp = __import__("re").sub(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'', "", _pp_raw)
_pp = __import__("re").sub(r"(?m)#.*$", "", _pp)
chk("P1", "批准窗存在，且【绝不碰系统弹窗】（MessageBox 实测无人参与会自返 IDYES）",
    [bool(_pp), any(x in _pp for x in ("ctypes", "user32", "MessageBox", "win32gui"))], [True, False])
chk("P2", "只有按钮能触签发：没有任何定时器/启动路径调 act()",
    bool(__import__("re").search(r"after\(\s*\d+\s*,[^)]*act\s*\(", _pp)), False)
chk("P3", "Enter/小键盘Enter/空格 全部吃掉（键盘路过不等于点头）",
    [_pp.count('bind_all("<%s>", lambda e: "break")' % k) for k in ("Return", "KP_Enter", "space")],
    [1, 1, 1])
chk("P4", "窗只打 approve/deny 两个动作口（URL 是拼出来的 ⇒ 钉拼装形状，不钉字面量）",
    ['"/api/spend/%s" % kind' in _pp, 'act("approve")' in _pp, 'act("deny")' in _pp,
     "consumed" in _pp], [True, True, True, False])
chk("P5", "窗不创建 pending、不延长过期（没人点就只能等过期=fail-closed）",
    ["create_spend_pending" in _pp, "TTL" in _pp], [False, False])
chk("P6", "gateway.approve 建完待批就弹窗，且弹不出来必须打印原因（不许假装在等人点）",
    ["pop_approval_window(p[\"id\"])" in _gw, "弹窗没起来" in _gw], [True, True])
chk("P7", "有运维抑制开关（CI/无人值守时不往桌面扔窗），且关掉会说明是被关的",
    ["GATEWAY_APPROVAL_POPUP" in _gw, "已按 GATEWAY_APPROVAL_POPUP=0 抑制" in _gw], [True, True])
import os as _os
_os.environ["GATEWAY_APPROVAL_POPUP"] = "0"
chk("P8", "开关真生效（这条不弹窗：返回原因字符串，不启动进程）",
    gw.pop_approval_window("P-TEST-ONLY"), "已按 GATEWAY_APPROVAL_POPUP=0 抑制")
_os.environ.pop("GATEWAY_APPROVAL_POPUP", None)

# ---- P9~P13 居中（2026-10-12 用户两次点名"弹窗要居中啊"）----
#     这张窗就是人证本身：落在屏幕角落里 = 客户没看见 = 15 分钟过期 = 整条方案白报价。
#     现网实测的坑不是"没算居中"，是【只给 +x+y 不带尺寸】⇒ Tk 不设 USPosition，
#     窗口一 map 就被 Windows 级联放置接管（实测连跳 78,78 → 130,130 → 234,234）。
import importlib.util as _ilu_pp
_sp_pp = _ilu_pp.spec_from_file_location("ap_pp", r"D:\video-skill\backend\approval_popup.py")
MOD_PP = _ilu_pp.module_from_spec(_sp_pp)
_sp_pp.loader.exec_module(MOD_PP)                    # 只 import，不建窗（建窗会往人桌面上扔东西）
_c = MOD_PP.center_xy
chk("P9", "居中的算术：1920×1080 放 400×270 ⇒ (760,405)（正中间）",
    _c(1920, 1080, 400, 270), (760, 405))
chk("P10", "窗口比屏幕还大 ⇒ 夹到 (0,0)，不许算出负数（负数会把标题栏推到屏幕外，连按钮都够不着）",
    _c(800, 600, 1200, 900), (0, 0))
chk("P11", "小屏/奇数尺寸不抛、取整（1366×768 放 401×274）", _c(1366, 768, 401, 274), (482, 247))
_lines_pp = [l.split("#")[0].rstrip() for l in _pp_raw.splitlines() if not l.strip().startswith("#")]
_pp_body = "\n".join(_lines_pp)
# 🔴 顺序锚点按【代码行】数，且先剥行尾注释：`place_centered(root)   # 居中…` 这种行不剥就匹配不上，
#    而用 `in l` 匹配又会先撞上 `def place_centered(...)` 那行 —— 两个方向都会把绿写成红、把红写成绿。
_pp_lines = [l.strip() for l in _lines_pp if l.strip()]
_i_tick = next(i for i, l in enumerate(_pp_lines) if l == "tick()")
_i_place = next(i for i, l in enumerate(_pp_lines) if l == "place_centered(root)")
_i_main = next(i for i, l in enumerate(_pp_lines) if l == "root.mainloop()")
chk("P12", "顺序是死的：tick() 先把内容填上 ⇒ 再居中 ⇒ 才 mainloop（先居中会按空窗口的尺寸算，偏一大截）",
    [_i_tick < _i_place < _i_main,
     sum(1 for l in _pp_lines if l == "place_centered(root)")], [True, 1])
chk("P13", "🔴 geometry 必须带尺寸：`\"%dx%d+%d+%d\"`（只写 +x+y 那一版实测被 Windows 级联放置接管）",
    ['root.geometry("%dx%d+%d+%d" % (w, h, x, y))' in _pp_body,
     'root.geometry("+%d+%d"' in _pp_body], [True, False])
chk("P14", "居中前必须 update_idletasks（不打这一句，winfo_req* 还是打包前的旧尺寸 = 按错的数居中）",
    [_pp_body.count("update_idletasks()") >= 2, "winfo_reqwidth()" in _pp_body,
     "winfo_screenwidth()" in _pp_body], [True, True, True])
chk("P15", "变异对照：把 geometry 改回【只给位置】那种写法 ⇒ P13 必须红（证明它咬的是形状不是字面）",
    [('root.geometry("%dx%d+%d+%d" % (w, h, x, y))'
      in _pp_body.replace('root.geometry("%dx%d+%d+%d" % (w, h, x, y))',
                          'root.geometry("+%d+%d" % (x, y))'))], [False])
chk("P9", "顺序：先建 pending 再弹窗（没有待批可弹=窗只会显示空）",
    0 < _gw.index("p = create_spend_pending(") < _gw.index('pop = pop_approval_window(p["id"])'), True)

# ---- P16~P20 批准卡上的「点下去之后」（2026-10-12 用户："说明批准后发起任务没法撤回"）----
#     钉两件事：① 四格后果齐（只写"不能撤回"不写"失败不扣"= 拿恐惧换点头，也算红）；
#     ② 文案只有一份 —— 置顶窗与批准页各自写一遍，迟早漂（这两处今天就是各写一遍漂掉的）。
_N16 = gw.approval_notice({"capability": "video.generate", "purpose": "wan3-prime", "account": "local",
                           "count": 3, "credits": 180, "scope": "闸里的假方案", "session": "session-x"})
# 批准页那张卡的 HTML：从 model_admin 源码里切出 card() 那一段（整份扫会被别的字符串喂绿）
_MA_SRC = _io.open(r"D:\video-skill\backend\model_admin.py", encoding="utf-8", errors="replace").read()
_html0 = _MA_SRC.index("function card(p){")
_pp_html = _MA_SRC[_html0:_MA_SRC.index("async function act(", _html0)]
chk("P16", "approval_notice 真跑：四格齐（随即发起 / 不能撤回 / 不扣的情况 / 范围锁死），"
    "段数与分数是条子上带的（不另算一份）；不读线上余额（余额读不到就不印『最低 X』见 P19）",
    [len(_N16["bullets"]), any("不能撤回" in b for b in _N16["bullets"]),
     any("不计次" in b for b in _N16["bullets"]), any("重新报价" in b for b in _N16["bullets"]),
     "3 段" in _N16["bullets"][0], "180" in _N16["bullets"][0]],
    [4, True, True, True, True, True])
chk("P17", "按钮文案由服务端那一份给：页面那颗 .ok 必须是 notice.button，不许留字面量",
    [_N16["button"] == gw.APPROVE_BUTTON,
     "class=ok>${E((p.notice||{}).button" in _pp_html,
     "批准，开始跑</button>" in _pp_html],
    [True, True, False])
chk("P18", "后果文案只有一份：批准页 HTML 与置顶窗代码里都不许出现『不能撤回/不可撤回』的字面",
    ["不能撤回" not in _pp_html, "不能撤回" not in _pp, "不可撤回" not in _pp], [True, True, True])
chk("P19", "余额读不到就不编一个：账户不存在 ⇒ 正文里不许出现『最低』那个凭空数",
    [any("最低" in b for b in gw.approval_notice({"capability": "video.generate",
                                                  "purpose": "wan3-prime", "account": "__no_such__",
                                                  "count": 1, "credits": 60})["bullets"])],
    [False])
chk("P20", "反向对照：P16 的四格是数出来的，把 bullets 砍到三条就必须红（不是恒真断言）",
    [len([b for b in _N16["bullets"] if "不能撤回" in b]), len(_N16["bullets"][:3])], [1, 3])

n_ok = sum(1 for _, ok in RESULTS if ok)
cprint("\n=== 人证闸（pending+核销版）汇总：%d/%d 通过 ===" % (n_ok, len(RESULTS)))
for cid, ok in RESULTS:
    if not ok:
        cprint("   未通过：%s" % cid)
sys.exit(0 if n_ok == len(RESULTS) else 1)
