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
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRATCH = os.path.abspath(os.path.join(HERE, "..", "scratch", "attest-gate"))
os.makedirs(SCRATCH, exist_ok=True)

GW_DIR = BACKEND
os.environ["GATEWAY_SPEND_APPROVALS"] = os.path.join(SCRATCH, "approvals.json")
os.environ["GATEWAY_LEDGER"] = os.path.join(SCRATCH, "ledger.sqlite")
os.environ["PYTHONPATH"] = GW_DIR
AP = os.environ["GATEWAY_SPEND_APPROVALS"]
for _p in (AP, os.environ["GATEWAY_LEDGER"]):
    if os.path.exists(_p):
        os.remove(_p)

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, WS)
sys.path.insert(0, GW_DIR)
from backend import gateway as gw  # noqa: E402

assert gw.SPEND_APPROVALS_PATH != gw._APPROVALS_PATH_DEFAULT, "隔离没生效：写的是生产账本"
GW_SRC = open(os.path.join(GW_DIR, "gateway.py"), encoding="utf-8").read()

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
def _code_only(src):
    """剥注释要用 tokenizer，不能只丢"整行是注释"的那些 —— 行尾注释里写着
    "当初这里换成了 MessageBoxW" 这种**教训记录**，字符串比对会把它当成违规代码（2026-10-14 就红过一次，
    红的是文档不是码）。tokenize 只丢 COMMENT token，代码里的字符串一个字都不动。"""
    import io as _io2
    import tokenize as _tok
    try:
        buf = []
        for t in _tok.generate_tokens(_io2.StringIO(src).readline):
            if t.type == _tok.COMMENT:
                continue
            if t.type in (_tok.NEWLINE, _tok.NL):
                buf.append("\n")
            else:
                buf.append(t.string)
        return "".join(buf)
    except Exception:                                       # noqa: BLE001
        return "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))


CODE_LINES = _code_only(GW_SRC)


def _dialog_problems(code):
    """这份码里还【弹得出窗】吗。判据抽成函数是为了能跑反向对照（只看终态会靠奇偶假绿）。
    两格：① windll 白名单 —— 只许 kernel32（2026-10-14 起 `pid_alive` 拿 GetExitCodeProcess 判活，
    那是正当用途）；② 弹窗那套的【调用形状】一个都不许在（`MessageBox*(` / MB_* 常量）。
    🔴 不钉『文件里出现过 import ctypes / 这个词』：那是把当年事故的实现方式当规则 ——
    既拦住一个正当调用，也会被 docstring 里记的那句教训误判红（今天就是这样红了 A2）。"""
    return [set(re.findall(r"windll\.(\w+)", code)) <= {"kernel32"},
            not bool(re.search(r"MessageBox\w*\s*\(|MB_DEFBUTTON2|_MB_YESNO|MB_TIMEOUT", code))]


chk("A1", "没有 MessageBoxW 调用（钉【调用形状】，不是钉这个词出现过——教训写在注释里不算代码）",
      bool(re.search(r"MessageBox\w*\s*\(", CODE_LINES)), False)
chk("A2", "ctypes 只许碰 kernel32 + 弹窗调用形状零残留（白名单，不是黑名单）",
      _dialog_problems(CODE_LINES), [True, True])
chk("A2b", "反向对照：把当年那版弹窗代码原样塞进同一个判据 ⇒ 两格都必须报警（不响就说明 A2 是摆设）",
    _dialog_problems("import ctypes\n_h = ctypes.windll.user32\n"
                     "MessageBoxW(0, 'x', 'y', MB_DEFBUTTON2 | _MB_YESNO)\n"), [False, False])
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
_real_default = gw._APPROVALS_PATH_DEFAULT


def _sha_of(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:8] if os.path.isfile(path) else "无文件"


try:
    # 🔴 2026-10-22 修这条反向对照自己的隐患。原来它把 gw.SPEND_APPROVALS_PATH 直接指向
    #    【真·生产文件】去证"后门签不出生产账本" —— 可一旦被测能力在免批名单里，
    #    `needs_spend_approval` 为假 ⇒ 那条"必须有人证"的判据【整段被跳过】⇒ 这条对照就真的往生产写。
    #    现网后果：video.generate 免批那段时间（10-21~10-22），每跑一次闸落 1 条 gate-c 假条子，
    #    今天数到 4 条才把它清掉；而 E2 读的就是生产，于是它恒红 —— 假样本既把闸弄红，
    #    又在生产账本里留下"扣了 50 分成功"那种能被人看见的东西。
    #    现在改成【scratch 里的假装生产文件】：两个路径常量一起指过去，
    #    `path == default` 这条等式仍然成立 ⇒ 被测判据一个字没变，但字节永远不落生产。
    _fake = os.path.join(SCRATCH, "approvals-as-if-production.json")
    if os.path.exists(_fake):
        os.remove(_fake)
    _prod_sha = _sha_of(_real_default)
    gw.SPEND_APPROVALS_PATH = _fake
    gw._APPROVALS_PATH_DEFAULT = _fake
    gw.ALLOW_GRANT_WITHOUT_ATTEST = True
    c = gw.grant_spend_approval("video.generate", "omni-t2v", "gate-c", 1, 50, "E 组")
    chk("E2", "指向『默认路径』时开关=True 也签不出（在 scratch 的假装生产上测，判据等价、不碰真账本）",
        (c.get("reason"), grants_of("gate-c")), ("no-attestation", []))
    chk("E2b", "🔴 跑完这条对照，真·生产条子文件【一个字节都没变】（防它哪天又开始往生产写）",
        _sha_of(_real_default), _prod_sha)
    # 变异：把 video.generate 塞回免批名单 ⇒ "必须有人证"那段被整段跳过 ⇒ 假装生产文件里会多出 1 条。
    # 这证明 E2 咬的是那条判据本身，不是恒真。⚠️ 名单必须在 finally 之外也还原（漏了就污染后面的断言）。
    _exempt_backup = gw.SPEND_APPROVAL_EXEMPT
    try:
        gw.SPEND_APPROVAL_EXEMPT = tuple(gw.SPEND_APPROVAL_EXEMPT) + ("video.generate",)
        gw.grant_spend_approval("video.generate", "omni-t2v", "gate-c2", 1, 50, "E2c 变异")
        _c2 = len(grants_of("gate-c2"))
    finally:
        gw.SPEND_APPROVAL_EXEMPT = _exempt_backup
    chk("E2c", "反向对照：能力在免批名单里 ⇒ 人证判据被跳过、条子签得出来（E2 不是恒真）", _c2, 1)
    chk("E2d", "变异只改了名单这一处：还原后 video.generate 重新要批（名单没被这条对照留在脏状态）",
        gw.needs_spend_approval("video.generate", "wan3-prime"), True)
finally:
    gw.ALLOW_GRANT_WITHOUT_ATTEST = False
    gw.SPEND_APPROVALS_PATH = _prod
    gw._APPROVALS_PATH_DEFAULT = _real_default
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
g7 = subprocess.run([sys.executable, os.path.join(GW_DIR, "gateway.py"),
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
# ---------- G6 一次点击：approve 的 stdout 只许给客户【一条链接】 ----------
# 原来这里连着印三条路（置顶窗 / DSH 审批卡 / 在自己终端跑 `gateway.py code …`）⇒ 人要点三次、
# agent 还要追问"批了吗"；而那句 code 命令本身违反既有禁令（跑 approve 的就是 agent）。
chk("G6", "🔴 stdout 里不再出现 `gateway.py code`（不把核销命令递给能自己执行它的一方）",
    "gateway.py code" in out, False)
chk("G6b", "面向客户的批准链接【只有一条】，且带 ?session=（多会话并发时他能替别的方案点头）",
    [out.count("127.0.0.1:8100/approval"), "approval?session=sess-g" in out], [1, True])
chk("G6c", "不再出现「备选」那种多路并存的措辞（一条链接一次点击是产品口径，不是选项菜单）",
    "备选" in out, False)
chk("G6d", "反向对照：把旧的三条路措辞塞回去 ⇒ 上面几条必须翻红（证明它们不是空跑）",
    [("备选" in (out + "   备选：① … ② gateway.py code x")),
     (("gateway.py code" in (out + "gateway.py code")))], [True, True])
_pid = re.search(r"(pend_[a-z0-9]{8})", out).group(1)
g8 = subprocess.run([sys.executable, os.path.join(GW_DIR, "gateway.py"), "code", _pid],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    env=env, timeout=120, cwd=GW_DIR)
o8 = g8.stdout.decode("utf-8", "replace")
chk("G6", "`code <id>` 这条【会】给码（人自己终端的兜底路径）",
    bool(re.search(r"核销码[:：]\s*[A-Z0-9]{6}", o8)), True)
_code = re.search(r"核销码[:：]\s*([A-Z0-9]{6})", o8).group(1)
g9 = subprocess.run([sys.executable, os.path.join(GW_DIR, "gateway.py"),
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
_hr = gw.check_spend_approval("video.generate", "omni-t2v", "gate-h", count=1, credits=50, session="sess-h")
_hd = json.load(open(AP, encoding="utf-8"))
chk("H1", "消费一张条子之后，待批还在（旧写法会把 pending 键整份抹掉）",
    [x["id"] for x in _hd.get("pending") or [] if x["id"] == _h["id"]], [_h["id"]])
chk("H2", "消费之后 denials 键仍在", "denials" in _hd, True)
# 🔴 H3 原来只印 used=[0] —— 看不出是"没消费"还是"消费被拒"，等于把定性推回给人猜。
#    现在把 check_spend_approval 的返回信封一起带上（ok + error），红了当场就知道死因。
chk("H3", "消费确实记了账（那张条子 used=1）；没记就把 check_spend_approval 的拒因一起打出来",
    [g.get("used") for g in _hd.get("grants", []) if g["id"] == _hg["id"]],
    [1] if (_hr or {}).get("ok") else ["消费被拒:", str((_hr or {}).get("error"))[:160]])
_h2 = gw.consume_by_click(_h["id"])
chk("H4", "consume_by_click：只给 pending_id 就能核销（码由服务端自己取）",
    (_h2.get("ok"), (_h2.get("grant") or {}).get("attest", {}).get("method")),
    (True, "dsh-card-click"))
_h3 = gw.deny_pending("pend_nope")
chk("H5", "deny 不存在的 pending ⇒ 拒", _h3.get("reason"), "pending-not-found")

# ---- P 组：审批只有【一条链接】，不许再有第二种壳（2026-10-22 用户指令）--------------
#     原话："审批的时候不要弹窗了，就是一个审批链接即可，点了审批链接就是直接提交任务，
#            别再让 agent 提交到 wan3.0。"
#     摘掉置顶窗【不削弱人证】：窗从来不是第二种人证 —— 它和批准页走的是同一个
#     POST /api/spend/approve，额度只有服务端 consume_by_click 签得出。删的是壳，不是判定。
#     所以这一组从"窗画得对不对"整体换成"还有没有第二条路 + 链接是不是唯一入口"。
import io as _io
import re as _re
_gw = _io.open(os.path.join(BACKEND, r"gateway.py"), encoding="utf-8").read()
# 🔴 扫之前先剥三引号串与行尾注释：gateway 与本文件的注释里到处写着"弹窗/置顶批准窗"，
#    整段扫会被自己的注释判红（这条教训就写在上面第 283 行原来那版里）。
_gw_code = _re.sub(r"(?m)#.*$", "", _gw)     # 只剥行尾注释：三引号串里也可能有被判断的代码，别顺手吞掉
_ap0 = _gw_code.find("p = create_spend_pending(")
_ap = _gw_code[_ap0:_gw_code.find('elif cmd', _ap0)] if _ap0 > 0 else ""

chk("P1", "置顶批准窗整条已摘除：gateway 代码里 `pop_approval_window` 零出现，那个文件也不在盘上",
    ["pop_approval_window" in _gw_code,
     os.path.isfile(os.path.join(BACKEND, r"approval_popup.py"))], [False, False])
chk("P1b", "锚点真的在（找不到建待批那一行 = 下面 P3~P5 全是在比空气，必须先红在这里）",
    _ap0 > 0 and len(_ap) > 200, True)
_mut = _gw_code.replace("def _cli(argv):", 'def _cli(argv):\n    pop_approval_window("x")\n', 1)
chk("P2", "反向对照：把当年那行调用原样塞回【同一份判据】⇒ 必须报警（不响就说明 P1 是摆设）",
    ["pop_approval_window" in _mut, "pop_approval_window" in _gw_code], [True, False])
chk("P3", "approve 只给一条地址，且必须带 ?session=（不带 = 客户看到全局列表，能替别的方案点头）",
    ["approval?session=%s" in _ap, _ap.count("http://127.0.0.1:8100")], [True, 1])
chk("P4", "approve 的输出里不许再出现第二种壳的话术（置顶窗/另一层壳/弹窗）——口径只有一条路",
    [x in _ap for x in ("置顶批准窗", "另一层壳", "弹窗")], [False, False, False])
chk("P5", "核销码与 `gateway.py code` 这类命令绝不进 approve 输出（跑 approve 的就是 agent，"
    "链接成为唯一入口后这条更要钉死）",
    [x in _ap for x in ("gateway.py code", "consumed ")], [False, False])

# ---- P6~P8：链接那一下到底发生了什么（把"点链接 = 服务端直接提交"钉成判据）----
#     原来 P9~P15 钉的是那张置顶窗画得对不对（居中算术、geometry 带不带尺寸、widget 选项名）。
#     窗已经整条摘掉，那些断言全部作废 —— 留下它们就是"闸在守一个不存在的东西"的假保险。
#     换成钉这三件：签发只有一处、点下去先起提交腿再挂号、链接必须在 pending 建出来之后才印。
_MA_CODE = _re.sub(r"(?m)#.*$", "",
                   _io.open(os.path.join(BACKEND, r"model_admin.py"), encoding="utf-8",
                            errors="replace").read())
_c0 = _MA_CODE.find("gateway.consume_by_click(pid)")
chk("P6", "签发只有服务端一处：批准页那个口调 consume_by_click，且 approval_popup 零残留"
    "（将来谁再加壳，也必须调这同一个口，不许各写一份签发）",
    [_c0 > 0, "approval_popup" in _MA_CODE], [True, False])
_seg = _MA_CODE[_c0:][:3000] if _c0 > 0 else ""
_k, _w = _seg.find("kickoff_submit("), _seg.find("approval_wake_note(")
chk("P7", "点链接那一下顺序是死的：先起提交腿 ⇒ 再挂号（反过来 = agent 醒来看到盘上没人提交、"
    "自己再 call 一次 = 双提交，2026-10-12 现网那个形状）", [_k > 0, _w > 0, _k < _w], [True, True, True])
_i_pend, _i_url = _ap.find("create_spend_pending("), _ap.find("appr_url =")
chk("P8", "approve 那一段：先建 pending ⇒ 再打印那条链接（没有待批可点 = 链接指向空气，"
    "2026-10-11 现网那次客户点进去是空位）", [_i_pend > 0, _i_url > 0, _i_pend < _i_url],
    [True, True, True])

# ---- P16~P20 批准卡上的「点下去之后」（2026-10-12 用户："说明批准后发起任务没法撤回"）----
#     钉两件事：① 四格后果齐（只写"不能撤回"不写"失败不扣"= 拿恐惧换点头，也算红）；
#     ② 文案只有一份 —— 置顶窗与批准页各自写一遍，迟早漂（这两处今天就是各写一遍漂掉的）。
_N16 = gw.approval_notice({"capability": "video.generate", "purpose": "wan3-prime", "account": "local",
                           "count": 3, "credits": 180, "scope": "闸里的假方案", "session": "session-x"})
# 批准页那张卡的 HTML：从 model_admin 源码里切出 card() 那一段（整份扫会被别的字符串喂绿）
_MA_SRC = _io.open(os.path.join(BACKEND, r"model_admin.py"), encoding="utf-8", errors="replace").read()
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
chk("P18", "后果文案只有一份：批准页 HTML 里不许出现『不能撤回/不可撤回』的字面（那份话只能来自 "
    "approval_notice 那一处；窗已摘除，但「文案两份迟早漂」这条判据留着）",
    ["不能撤回" not in _pp_html, "不可撤回" not in _pp_html], [True, True])
chk("P19", "余额读不到就不编一个：账户不存在 ⇒ 正文里不许出现『最低』那个凭空数",
    [any("最低" in b for b in gw.approval_notice({"capability": "video.generate",
                                                  "purpose": "wan3-prime", "account": "__no_such__",
                                                  "count": 1, "credits": 60})["bullets"])],
    [False])
chk("P20", "反向对照：P16 的四格是数出来的，把 bullets 砍到三条就必须红（不是恒真断言）",
    [len([b for b in _N16["bullets"] if "不能撤回" in b]), len(_N16["bullets"][:3])], [1, 3])

# ── 原 P21~P21d（批准窗 widget 选项名必须 Tk 真认识）已于 2026-10-22 随窗整条摘除。
#    留这条注释是为了不重犯：当年 `Label(align="top")` 一构造就 TclError ⇒ 窗从来弹不出来，
#    而 P 组全绿 —— 那些断言只测文案与居中算法，一条都没碰到 widget 构造。
#    教训=「闸全绿却藏着一条必然崩溃的分支」；将来若再加任何 GUI 壳，这条判据要连着一起回来。

n_ok = sum(1 for _, ok in RESULTS if ok)
cprint("\n=== 人证闸（pending+核销版）汇总：%d/%d 通过 ===" % (n_ok, len(RESULTS)))
for cid, ok in RESULTS:
    if not ok:
        cprint("   未通过：%s" % cid)
sys.exit(0 if n_ok == len(RESULTS) else 1)
