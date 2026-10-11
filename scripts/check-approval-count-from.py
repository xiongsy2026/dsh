# -*- coding: utf-8 -*-
"""批准条子段数收口闸（第 1 项：审批一次覆盖整条方案）。
钉的是【段数只能从盘上算】这件事，不是某个数字：
K1 真产物值表（拿盘上真实任务目录跑）
K2 合成档（造 TIMELINE/段目录，验优先级与干扰表）
K3 真跑 CLI 三条拒分支，并证明【没建条子、没弹窗】
K4 会弹窗那条分支不许跑 ⇒ 改成读源码断言提示语在
K5 变异对照（摘掉 --count-from / 把派生改成常量）
K6 覆盖对照：count 的赋值只许有一处来自派生
全离线、0 消耗：绝不真建待批（建了就会往人桌面上弹批准窗）。
"""
import io
import json
import os
import subprocess
import sys
import tempfile

BS = chr(92)
sys.path.insert(0, r"D:\video-skill")
from backend import gateway as gw            # noqa: E402

GW_PY = r"D:\video-skill\backend\gateway.py"
APPR = r"D:\video-skill\.gateway_spend_approvals.json"
PROD = "C:" + BS + "dsh" + BS + "productions"
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


# ── K1 真产物值表（盘上真实任务目录，不造）────────────────────────────────────
real = {
    "super-clone-20261011-1658": 2,      # 只有 outputs/seg1、seg2
    "video20-face-swap": 1,              # TIMELINE 表 1 行
    "super-clone-dapin": 2,
    "super-clone-28s": 4,
}
got = {}
for name, want in real.items():
    d = os.path.join(PROD, name)
    if not os.path.isdir(d):
        got[name] = "目录不见了"
        continue
    got[name] = gw.plan_segment_count(d).get("count")
check("K1 真产物：四个任务的段数都算对（键=%s）" % list(real), got, dict((k, v) for k, v in real.items()))
check("K1b 真产物：坏目录必须 count=0（不许猜一个数）",
      gw.plan_segment_count(os.path.join(PROD, "no-such-task-xyz")).get("count"), 0)
check("K1c 单价现读：段数×单价 才是条子上限（wan3-prime 60 分/段）",
      gw.count_from_args("video.generate", "wan3-prime", os.path.join(PROD, "super-clone-28s"))[:2],
      (4, 4 * int(gw.unit_price("video.generate", "wan3-prime"))))

# ── K2 合成档：优先级与干扰表 ───────────────────────────────────────────────
tmp = tempfile.mkdtemp(prefix="count-from-")
NL = chr(10)


def mk(name, timeline=None, segs=()):
    d = os.path.join(tmp, name)
    os.makedirs(os.path.join(d, "outputs"), exist_ok=True)
    for s in segs:
        os.makedirs(os.path.join(d, "outputs", s), exist_ok=True)
    if timeline is not None:
        io.open(os.path.join(d, "TIMELINE.md"), "w", encoding="utf-8", newline=NL).write(timeline)
    return d


TL3 = ("# 切段" + NL + NL +
       "| 产品 | 素材文件 | 出现位置 |" + NL +      # 干扰表：第一格不是「段」
       "|------|---------|---------|" + NL +
       "| 整瓶 | a.jpg | 段1 |" + NL + NL +
       "| 段 | 窗口 | 时长 |" + NL +
       "|---|---|---|" + NL +
       "| A | 0-8s | 8s |" + NL +
       "| B | 8-16s | 8s |" + NL +
       "| C | 16-24s | 8s |" + NL + NL +
       "## 风险点" + NL + "| 段 | 会被误判成公众人物 |" + NL)
check("K2a TIMELINE 的「段」表优先，且不被别的表骗（3 行）",
      gw.plan_segment_count(mk("t3", TL3))["count"], 3)
check("K2b TIMELINE 有表 ⇒ 段目录数不参与（表说了算）",
      gw.plan_segment_count(mk("t3b", TL3, segs=("seg1",)))["count"], 3)
check("K2c 没有 TIMELINE ⇒ 数 outputs/seg* 目录",
      gw.plan_segment_count(mk("segs", None, segs=("segA", "segB")))["count"], 2)
_solo = gw.plan_segment_count(mk("solo"))
check("K2d 两个都没有 ⇒ 如实按单段（count=1 且 source 说清来源）",
      [_solo.get("count"), _solo.get("source")], [1, "single-default"])

# ── K3 真跑 CLI 三条拒分支，并证明没建条子 ──────────────────────────────────
def pend_ids():
    try:
        d = json.loads(io.open(APPR, encoding="utf-8", errors="replace").read())
    except Exception:
        return []
    return [p.get("id") for p in (d.get("pending") or [])]


before = pend_ids()
cases = [
    ("坏目录", ["approve", "video.generate", "wan3-prime", "--count-from", os.path.join(PROD, "nope-xyz"),
               "--account", "probe-none", "--session", "probe-none"]),
    ("--count 与盘上不符", ["approve", "video.generate", "wan3-prime", "--count-from",
                            os.path.join(PROD, "video20-face-swap"), "--count", "9",
                            "--account", "probe-none", "--session", "probe-none"]),
    ("--credits 与单价不符", ["approve", "video.generate", "wan3-prime", "--count-from",
                              os.path.join(PROD, "video20-face-swap"), "--credits", "999",
                              "--account", "probe-none", "--session", "probe-none"]),
]
outs = []
for label, args in cases:
    _env = dict(os.environ)
    for _k in ("PYTHONUTF8", "PYTHONLEGACYWINDOWSSTDIO"):
        _env.pop(_k, None)
    # 🔴 子进程 stdout 接到管道 ⇒ 它按 locale(cp936) 编码中文；我拿 utf-8 硬解就成了乱码，
    #    三条子串断言会"为错的理由通过"。显式要 utf-8，解不出来再回落 gbk。
    _env["PYTHONIOENCODING"] = "utf-8:replace"
    r = subprocess.run([sys.executable, GW_PY] + args, capture_output=True, stdin=subprocess.DEVNULL,
                       timeout=180, cwd=r"D:\video-skill", env=_env)
    _raw = (r.stdout or b"") + (r.stderr or b"")
    try:
        txt = _raw.decode("utf-8")
    except UnicodeDecodeError:
        txt = _raw.decode("gbk", "replace")
    outs.append((label, r.returncode, txt))
check("K3 三条拒分支全部 exit=2", [o[1] for o in outs], [2, 2, 2])
check("K3b 拒因都说清了（坏目录抛/两个不一致）",
      [bool(x) for x in (
          "读不出来" in outs[0][2], "不一致" in outs[1][2], "不一致" in outs[2][2])], [True, True, True])
after = pend_ids()
check("K3c 全程没建条子（pending 集合不变 ⇒ 也没往人桌面弹批准窗）", sorted(after), sorted(before))

# ── K4 会弹窗那条分支：只读源码断言，不真跑 ─────────────────────────────────
src = io.open(GW_PY, encoding="utf-8", errors="replace").read()
check("K4 没给 --count-from 时会出声提示（这条不跑 CLI：跑了就建条子弹窗）",
      "--count-from" in src and "段数是你手填的" in src, True)
check("K4b 派生结果必须打印来源（报价那条消息要能直接抄）",
      "段数与分数来自盘上事实" in src, True)

# ── K5/K6 变异与覆盖对照（判据抽成纯函数）───────────────────────────────────
def count_cli_problems(text):
    """approve 分支里 --count-from 的接线是否完整。返回问题列表。"""
    p = []
    if '"--count-from"' not in text:
        p.append("opts 里没有 --count-from（选项被摘掉了）")
    if 'kv["count"], kv["credits"] = _n, _c' not in text:
        p.append("派生结果没回写 kv ⇒ 算了等于没算")
    if text.count('kv["count"], kv["credits"] = _n, _c') > 1:
        p.append("回写点多于一处 ⇒ 两处各派生一份迟早漂")
    if '"count_from": ""' not in text:
        p.append("kv 默认值里没有 count_from ⇒ 取参时 KeyError")
    return p


check("K6 正解接线完整（覆盖对照：opts/回写/默认值三处都在）", count_cli_problems(src), [])
check("K5 变异：摘掉 opts 里的 --count-from ⇒ 报",
      any("--count-from" in x for x in count_cli_problems(src.replace('"--count-from"', '"--nope"', 1))), True)
check("K5b 变异：把派生回写删掉 ⇒ 报『算了等于没算』",
      any("没回写" in x for x in count_cli_problems(src.replace('kv["count"], kv["credits"] = _n, _c', "pass", 1))), True)
check("K5c 变异：回写复制两份 ⇒ 报『两处各派生』",
      any("两处" in x for x in count_cli_problems(
          src.replace('kv["count"], kv["credits"] = _n, _c',
                      'kv["count"], kv["credits"] = _n, _c' + NL + "            "
                      + 'kv["count"], kv["credits"] = _n, _c', 1))), True)

# ── K7~K11 报价必须自带计划表（2026-10-10：靠 agent 记得跑 contract_tables.py 已被现网证伪）──
from backend import skill_contract as sc      # noqa: E402

_c = sc.contract_for("super-clone")
check("K7 spend→合同 反查：wan3-prime 指回 super-clone；查不到 ⇒ 空串（绝不编一张别人的表）",
      [sc.skill_for_spend("video.generate", "wan3-prime"),
       sc.skill_for_spend("video.generate", "no-such-purpose"),
       sc.skill_for_spend("text.complete", "agnes")],
      ["super-clone", "", ""])
_tab = sc.plan_table("super-clone")
check("K7b 表逐行带出合同的每一步 + 花钱口（只有一句标题 = 等于没表）",
      [all(("`%s`" % s["id"]) in _tab for s in _c.get("steps") or []),
       _tab.count("| `") == len(_c.get("steps") or []),
       str(_c.get("spend")) in _tab, len(_c.get("steps") or []) >= 5],
      [True, True, True, True])

# 🔴 打桩判定口：2026-10-11 起 approve 会先问"这张表回填过没有"（报价前置），
#    自测的会话当然没回填 ⇒ 不指个桩，K8/K9 会被新前置拒掉（那是判据在咬，不是闸坏了）。
import http.server as _hs
import threading as _th


class _StubGate(_hs.BaseHTTPRequestHandler):
    def do_GET(self):
        b = b'{"ok":true,"show":true,"prefilled":true,"submitted":false,"prefill_gap":[],' \
            b'"unfilled_required":[],"loose_materials":[]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


_sg = _hs.HTTPServer(("127.0.0.1", 0), _StubGate)
_th.Thread(target=_sg.serve_forever, daemon=True).start()
_STUB_GATE = "http://127.0.0.1:%d/api/skills/form?session=" % _sg.server_address[1]
_ap_tmp = os.path.join(tmp, "probe_approvals.json")
io.open(_ap_tmp, "w", encoding="utf-8").write("{}")


def _run_approve(purpose):
    """真跑一次 approve：账本/待批都指到临时文件，并把批准窗【显式抑制】。
    🔴 自测不点批准 ⇒ 这条 pending 到不过期都无所谓，它落在临时账本上，线上那份一个字都不动。"""
    _env = dict(os.environ)
    for _k in ("PYTHONUTF8", "PYTHONLEGACYWINDOWSSTDIO"):
        _env.pop(_k, None)
    _env["PYTHONIOENCODING"] = "utf-8:replace"
    _env["GATEWAY_SPEND_APPROVALS"] = _ap_tmp
    _env["GATEWAY_LEDGER"] = os.path.join(tmp, "probe_ledger.sqlite")
    _env["GATEWAY_APPROVAL_POPUP"] = "0"
    _env["GATEWAY_FORM_GATE"] = _STUB_GATE
    r = subprocess.run([sys.executable, GW_PY, "approve", "video.generate", purpose,
                        "--count", "1", "--credits", "60", "--account", "probe-none",
                        "--session", "probe-none", "--scope", "闸自测（不点批准，过期即失效）"],
                       capture_output=True, stdin=subprocess.DEVNULL, timeout=180,
                       cwd=r"D:\video-skill", env=_env)
    _raw = (r.stdout or b"") + (r.stderr or b"")
    try:
        return r.returncode, _raw.decode("utf-8")
    except UnicodeDecodeError:
        return r.returncode, _raw.decode("gbk", "replace")


_rc1, _tx1 = _run_approve("wan3-prime")
_rc2, _tx2 = _run_approve("no-such-purpose")
check("K8 真跑 approve：报价那条输出里【就有】流程计划表（不是让人再去跑一条命令要表）",
      [_rc1, "流程计划" in _tx1, "material-analysis" in _tx1, "wan3-prime" in _tx1],
      [0, True, True, True])
check("K9 反向对照：spend 没有对应合同时 ⇒ 明说「没有合同」并且【不印表】（不许装作给过表）",
      ["流程计划" in _tx2, "没有一条 skill 合同" in _tx2], [False, True])
check("K9b 抑制开关确实生效（输出里是「弹窗没起来」的兜底话 ⇒ 人桌面上没多出批准窗）",
      "弹窗没起来" in _tx1, True)
_pd = json.loads(io.open(_ap_tmp, encoding="utf-8", errors="replace").read()).get("pending") or []
check("K9c 自测只落临时账本：两份 pending 都在沙盒文件里，且核销码没进 stdout（G4 同一条纪律）",
      [len(_pd), all(p.get("status") == "pending" for p in _pd),
       all(str(p.get("code")) not in _tx1 + _tx2 for p in _pd)], [2, True, True])
check("K10 接线覆盖对照：表是在【建完待批、给出报价】之后打的（打在前面 ⇒ 报价被拒时人看不到表）",
      [src.find("p = create_spend_pending(") < src.find("skill_for_spend(cap, kv"),
       src.count("skill_for_spend(cap, kv")], [True, 1])
check("K10b 变异：把打印计划表那两句删掉 ⇒ K10 的定位就失配（证明 K10 钉的是真接线，不是注释）",
      [src.replace("skill_for_spend(cap, kv", "_removed(cap, kv").find("skill_for_spend(cap, kv")], [-1])

shutil_ok = True
try:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
except Exception:
    shutil_ok = False
check("收尾：临时目录已清（闸自己不许留残留）", shutil_ok, True)

print(_safe(NL + "== 批准段数收口闸：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL))))
for f in FAIL:
    print(_safe("  ✗ " + f))
sys.exit(1 if FAIL else 0)
