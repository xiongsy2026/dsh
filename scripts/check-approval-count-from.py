# -*- coding: utf-8 -*-
"""批准条子段数收口闸（第 1 项：审批一次覆盖整条方案）。
钉的是【段数只能从盘上算】这件事，不是某个数字：
K1 真产物值表（拿盘上真实任务目录跑）
K2 合成档（造 TIMELINE/段目录，验优先级与干扰表）
K3 真跑 CLI 三条拒分支，并证明【没建条子、没弹窗】
K4 会弹窗那条分支不许跑 ⇒ 改成读源码断言提示语在
K5 变异对照（摘掉 --count-from / 把派生改成常量）
K6 覆盖对照：count 的赋值只许有一处来自派生
K2e…K2q（2026-10-13）段数要有主人：管线落的 _submit_plan.json 优先，打架/坏文件一律 count=0，
        兜底认【嵌套】chunk_*，并用真写侧↔真读侧 round-trip 证两边对得上。
M 组（2026-10-13）批准的范围认指纹：条子签的是【那一份】切段计划，改窗口/改段数 ⇒ 提交那一刀拒
        （现网形状：批「段1 0-15s」、实提交 chunk_03 的 6.3s 尾段）。全程临时账本 + 临时 ledger。
全离线、0 消耗：绝不真建待批（建了就会往人桌面上弹批准窗）。
"""
import io
import json
import os
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
SVC_PY = os.path.join(BACKEND, r"services\super_clone_wan3.py")
APPR = os.path.join(BACKEND, r".gateway_spend_approvals.json")
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
# ── K1c 单价现读 + 【计费口径分岔】（2026-10-30 wan3-prime 换芯后新增）─────────────
# 按段的 lane：条子上限 = 段数 × 分/段（tianyue 那条 wan30-studio 还是这个口径）。
check("K1c 按段计价的 lane：段数×单价 才是条子上限（wan30-studio 60 分/段）",
      gw.count_from_args("video.generate", "wan30-studio", os.path.join(PROD, "super-clone-28s"))[:2],
      (4, 4 * int(gw.unit_price("video.generate", "wan30-studio"))))
# 🔴 按秒的 lane（现在 wan3-prime = yz30）：还按"段数 × 单价"算 ⇒ 一条 40s 三段片批 12 分、
#    真要花 160 分 = 【批的比花的少】，第 2 段被自己的批准闸拦停（这不是手滑，是口径没主人）。
#    所以这里钉两半：① 有计划 ⇒ 上限 = Σ秒 × 分/秒；② 没计划 ⇒ 拒建条子，绝不按段估一个数。
_plan_dir = os.path.join(tmp if False else tempfile.gettempdir(), "count-from-persec")
os.makedirs(os.path.join(_plan_dir, "outputs"), exist_ok=True)
with open(os.path.join(_plan_dir, "outputs", "_submit_plan.json"), "w", encoding="utf-8") as _pf:
    json.dump({"count": 3, "windows": [{"start": 0.0, "end": 15.0}, {"start": 15.0, "end": 30.0},
                                        {"start": 30.0, "end": 40.0}]}, _pf)
_n3, _c3, _w3 = gw.count_from_args("video.generate", "wan3-prime", _plan_dir)
check("K1c2 按秒的 lane：上限 = Σ秒 × 分/秒（40 秒三段 × 4 分 = 160 分，不是 3×4=12 分）",
      [_n3, _c3, "按【秒】" in _w3], [3, 160, True])
try:
    gw.count_from_args("video.generate", "wan3-prime", os.path.join(PROD, "super-clone-28s"))
    _raised = None
except ValueError as _e:
    _raised = str(_e)
check("K1c3 按秒的 lane 但这份任务没有提交计划 ⇒ 拒建条子（不许退回按段估，那正好算少）",
      [bool(_raised), "按秒" in (_raised or "")], [True, True])
check("K1c4 单位词只有一处定义：报价正文与 prices 都读 lane_price_unit_label（别处再拼一份『分/段』就是第二份真相）",
      ["分/秒" in gw.lane_price_unit_label("video.generate", "wan3-prime"),
       "分/段" in gw.lane_price_unit_label("video.generate", "wan30-studio"),
       gw.lane_price_unit_label("video.generate", "wan3-prime") in
       gw.build_quote_summary("video.generate", "wan3-prime", "local", 3, 160, "s")],
      [True, True, True])

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

# ── K2e…K2k 段数要有主人（2026-10-13 现网：批 1 段、机器跑 3 段）────────────────
PLAN = gw.SUBMIT_PLAN_NAME


def mk2(name, timeline=None, segs=(), plan=None, deep=()):
    """segs 落在 outputs/ 下；deep 落在 outputs/step2_wan3/ 下（这条线真实的嵌套形状）。"""
    d = mk(name, timeline=timeline, segs=segs)
    for s in deep:
        os.makedirs(os.path.join(d, "outputs", "step2_wan3", s), exist_ok=True)
    if plan is not None:
        io.open(os.path.join(d, "outputs", PLAN), "w", encoding="utf-8", newline=NL).write(plan)
    return d


PLAN3 = json.dumps({"count": 3, "windows": [{"start": 0.0, "end": 15.0},
                                            {"start": 15.0, "end": 30.0},
                                            {"start": 30.0, "end": 36.4}],
                    "target_duration": 36.4, "trim": None, "source_sha8": "abcd1234"})
_r = gw.plan_segment_count(mk2("p3", segs=("seg1",), plan=PLAN3))
check("K2e 管线落的提交计划优先（盘上只有 1 个段目录也按计划的 3 段）",
      [_r.get("count"), _r.get("source")], [3, PLAN])
check("K2e2 计划里的窗口要出现在 detail 里（段数不能是个没有来历的数）",
      "0.00-15.00s" in (_r.get("detail") or "") and "30.00-36.40s" in (_r.get("detail") or ""), True)
_r0 = gw.plan_segment_count(mk2("p0", timeline=TL3, plan=json.dumps({"count": 2, "windows": []})))
check("K2f 计划与 TIMELINE 打架 ⇒ count=0（两份事实不许挑一份）", [_r0.get("count"), _r0.get("source")],
      [0, "plan-vs-timeline"])
_rb = gw.plan_segment_count(mk2("pb", plan="{count: 3"))
check("K2g 计划是坏 JSON ⇒ count=0 并指回去重编译（读不出来不等于没段）",
      [_rb.get("count"), "compile" in (_rb.get("detail") or "")], [0, True])
_re_ = gw.plan_segment_count(mk2("pe", plan=json.dumps({"count": 0, "windows": []})))
check("K2h 计划里 count=0 ⇒ 同样拒（不许回落成 1 段偷偷批）", _re_.get("count"), 0)
_rd = gw.plan_segment_count(mk2("deep", deep=("chunk_01", "chunk_02", "chunk_03")))
check("K2i 没有计划时兜底认【嵌套】chunk_*（现网那条就数漏在这）",
      [_rd.get("count"), _rd.get("source")], [3, "outputs/段目录"])
check("K2j 兜底那条要自己承认是猜的（detail 里得有「猜的兜底」，让人看得出该重编译）",
      "猜的兜底" in (_rd.get("detail") or ""), True)
_deeper = mk2("deep4", deep=("chunk_01",))
os.makedirs(os.path.join(_deeper, "outputs", "step2_wan3", "chunk_01", "x", "chunk_09"), exist_ok=True)
check("K2k 反向对照：段目录嵌到第 4 层就不数了（证明 max_depth 真在生效，不是碰巧走全树）",
      gw._seg_dirs_under(os.path.join(_deeper, "outputs")), ["chunk_01"])
_real_new = os.path.join(PROD, "super-clone-20261013-2050")
_rn = gw.plan_segment_count(_real_new)
_plan_here = os.path.join(_real_new, "outputs", PLAN)
# 🔴 这条钉的是【规则】不是某个数字：那条任务目录里现在躺着什么计划，取决于最近一次真跑了几段
#    （10-13 那次切了 3 段、10-14 这一单只要 0-15s ⇒ 计划就是 1 段）。写死"3"会把读对了的判据演成假红。
_want_here = None
if os.path.isfile(_plan_here):
    try:
        _want_here = int((json.loads(io.open(_plan_here, encoding="utf-8").read()) or {}).get("count"))
    except (ValueError, TypeError):
        _want_here = "坏计划"
check("K2l 真产物：有提交计划的那条任务，段数必须【等于计划自己写的数】且来源是计划文件"
      "（不写死数字——计划跟着真跑走）",
      [_rn.get("source"), _rn.get("count"), _want_here],
      [PLAN, _want_here, _want_here])
check("K2l2 真产物：那份计划必须带窗口且算得出指纹（段数没有来历 = 和当年『按单段建』一样不可用）"
      "—— 只钉【有没有写出窗口】这条规则，不钉 0-15s 那个数（同一任务今晚又跑了 16-30s，钉数字=假红）",
      [bool(gw.submit_plan_id(_real_new)) == bool(_want_here),
       "_submit_plan.json" in (_rn.get("detail") or ""),
       bool(__import__("re").search(r"\d+\.\d+-\d+\.\d+s", _rn.get("detail") or ""))
       if _want_here else True],
      [True, True, True])

import ast as _ast                                                   
_wsrc = io.open(SVC_PY, encoding="utf-8", errors="replace").read()
check("K2m 文件名常量两边同一个（写侧改了名，读侧会静默数不到）",
      gw.SUBMIT_PLAN_NAME,
      _ast.literal_eval(next(n.value for n in _ast.parse(_wsrc).body
                             if isinstance(n, _ast.Assign)
                             and getattr(n.targets[0], "id", "") == "SUBMIT_PLAN_NAME")))
_calls = [n.lineno for n in _ast.walk(_ast.parse(_wsrc))
          if isinstance(n, _ast.Call) and getattr(n.func, "id", "") == "write_submit_plan"]
_segdir_line = next((i for i, l in enumerate(_wsrc.split(chr(10)), 1) if 'f"chunk_{i:02d}"' in l), 0)
check("K2n 管线真的调 write_submit_plan，且排在切段之前（先有计划再有段目录）",
      [len(_calls) >= 1, bool(_segdir_line) and _calls[0] < _segdir_line], [True, True])
RT = os.path.join(tmp, "roundtrip")
os.makedirs(RT, exist_ok=True)
from backend.services import super_clone_wan3 as _svc    # noqa: E402
_w = _svc.write_submit_plan(RT, task_id="t", windows=_svc.plan_15s_windows(36.4),
                            target_duration=36.4, trim_range=None,
                            source_video=os.path.join(RT, "src.mp4"), as_duration_chunk=False)
_rr = gw.plan_segment_count(RT)
check("K2p 真写侧 ↔ 真读侧对得上（管线算 3 段 ⇒ 网关读出 3 段，两模块各解一遍必漂）",
      [_w["count"], _rr["count"], _rr["source"], bool(gw.submit_plan_id(RT))],
      [3, 3, PLAN, True])
_w1 = _svc.write_submit_plan(RT, task_id="t", windows=[], target_duration=15.0,
                             trim_range={"start": 0.0, "end": 15.0},
                             source_video=os.path.join(RT, "src.mp4"), as_duration_chunk=True)
check("K2q 只做一段（难单 / --as-duration-chunk）⇒ 计划里就是 1 段，不再走「猜的兜底」那条路",
      [_w1["count"], gw.plan_segment_count(RT)["count"], gw.plan_segment_count(RT)["source"]],
      [1, 1, PLAN])
_keep_reader = gw._submit_plan_read
gw._submit_plan_read = lambda d: None                    # 变异：把"读计划"这一刀摘掉
_m = gw.plan_segment_count(mk2("pmut", segs=("seg1",), plan=PLAN3))
gw._submit_plan_read = _keep_reader
check("K2o 变异对照：摘掉读计划 ⇒ K2e 那个断言必须换个理由红（证明它不是靠段目录蒙对的）",
      [_m.get("count"), _m.get("source") != PLAN], [1, True])
check("K2o2 变异只改了内存里那份，读回来还是好的（闸自己不许污染被测码）",
      gw.plan_segment_count(mk2("pmut", segs=("seg1",), plan=PLAN3)).get("source"), PLAN)

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
    # 🔴 后两支【故意用 wan30-studio（按段那条）】：wan3-prime 今天按【秒】计价，
    #    而这些真产物目录里没有 `_submit_plan.json` ⇒ 按秒那条会在【更早】的地方就拒建（K1c3 钉的就是那一支），
    #    于是这两支要测的"数量与盘上不符"根本没被执行到 —— 拒是拒了，但拒的不是它宣称的那个原因（假有效）。
    ("--count 与盘上不符", ["approve", "video.generate", "wan30-studio", "--count-from",
                            os.path.join(PROD, "video20-face-swap"), "--count", "9",
                            "--account", "probe-none", "--session", "probe-none"]),
    ("--credits 与单价不符", ["approve", "video.generate", "wan30-studio", "--count-from",
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
                       timeout=180, cwd=BACKEND, env=_env)
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
                       cwd=BACKEND, env=_env)
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
check("K9b approve 的输出结构：【只一条链接、带 session、不递码、不再承诺弹窗】"
      "（🔴 这条原来钉的是『明说窗没起来 + 给链接』—— 2026-10-22 用户定「审批不要弹窗，就是一个审批链接」之后"
      "置顶窗整条摘了，那句话永远不会再出现，于是它从那天起一直红着【不是今天换芯弄的】。"
      "今天把它改成钉当下的结构：一条地址、一次点击、stdout 里不许有 gateway.py code（那等于把"
      "自核销的说明书递给能自己批的人，闸 G4 同一条纪律））",
      [_tx1.count("127.0.0.1:8100/approval?session="), "approval?session=" in _tx1,
       "gateway.py code" in _tx1, "弹窗" in _tx1],
      [1, True, False, False])
_pd = json.loads(io.open(_ap_tmp, encoding="utf-8", errors="replace").read()).get("pending") or []
check("K9c 自测只落临时账本：两份 pending 都在沙盒文件里，且核销码没进 stdout（G4 同一条纪律）",
      [len(_pd), all(p.get("status") == "pending" for p in _pd),
       all(str(p.get("code")) not in _tx1 + _tx2 for p in _pd)], [2, True, True])
check("K10 接线覆盖对照：表是在【建完待批、给出报价】之后打的（打在前面 ⇒ 报价被拒时人看不到表）",
      [src.find("p = create_spend_pending(") < src.find("skill_for_spend(cap, kv"),
       src.count("skill_for_spend(cap, kv")], [True, 1])
check("K10b 变异：把打印计划表那两句删掉 ⇒ K10 的定位就失配（证明 K10 钉的是真接线，不是注释）",
      [src.replace("skill_for_spend(cap, kv", "_removed(cap, kv").find("skill_for_spend(cap, kv")], [-1])

M_TMP = tempfile.mkdtemp(prefix="plan-drift-")
M_APPR = os.path.join(M_TMP, "approvals.json")
io.open(M_APPR, "w", encoding="utf-8").write("{}")
_m_task = os.path.join(M_TMP, "task")
os.makedirs(os.path.join(_m_task, "outputs", "step2_wan3", "chunk_01", "step2_wan3"), exist_ok=True)
io.open(os.path.join(_m_task, "outputs", PLAN), "w", encoding="utf-8").write(PLAN3)
MPID = gw.submit_plan_id(_m_task)
_out = os.path.join(_m_task, "outputs", "step2_wan3", "chunk_01", "step2_wan3", "seg01-prime.mp4")
PARAMS = {"output_path": _out, "job_id": ""}

_sa, _sl, _sp2 = gw.SPEND_APPROVALS_PATH, gw._ledger, gw.ALLOW_GRANT_WITHOUT_ATTEST
gw.SPEND_APPROVALS_PATH = M_APPR
gw._ledger = gw.Ledger(path=os.path.join(M_TMP, "ledger.sqlite"))


def _sign(plan_id, count=3):
    """建待批 → 拿服务端自己那份码核销 ⇒ 得到一张【带指纹】的活条子（走的就是现网那条路）。"""
    p = gw.create_spend_pending("video.generate", "wan3-prime", "probe", count=count,
                                credits=count * 60, scope="闸自测", session="probe-s", plan_id=plan_id)
    code = next(x["code"] for x in json.loads(io.open(M_APPR, encoding="utf-8").read())["pending"]
                if x["id"] == p["id"])
    r = gw.consume_spend_approval(p["id"], code, "probe-s")
    return (r.get("grant") or {}).get("plan_id", "（没带上）")


check("M1 计划指纹算得出来，且只由 count/windows/trim/参考片决定（换个时间戳不该换指纹）",
      [bool(MPID), gw.submit_plan_id(_m_task) == MPID,
       io.open(os.path.join(_m_task, "outputs", PLAN), encoding="utf-8").read().count("written_at")],
      [True, True, 0])
check("M2 核销把指纹从待批带到条子上（漏这一步，M3 之后全是空判据）", _sign(MPID), MPID)
check("M3 同指纹 ⇒ 放行（0 个问题）",
      gw.plan_drift_problems("video.generate", "wan3-prime", PARAMS, "probe", "probe-s"), [])
io.open(os.path.join(_m_task, "outputs", PLAN), "w", encoding="utf-8").write(
    json.dumps({"count": 1, "windows": [{"start": 30.1, "end": 36.4}], "trim": None,
                "source_sha8": "abcd1234"}))
_p = gw.plan_drift_problems("video.generate", "wan3-prime", PARAMS, "probe", "probe-s")
check("M4 现网那一刀：条子批的是 3 段，提交改成【只有尾段 30-36s】⇒ 拒",
      [len(_p) == 1, "30.10-36.40s" in _p[0], "重新报价" in _p[0]], [True, True, True])
io.open(os.path.join(_m_task, "outputs", PLAN), "w", encoding="utf-8").write(PLAN3)
check("M5 计划改回来 ⇒ 又放行（判据盯的是【是不是同一份】，不是一次性黑名单）",
      gw.plan_drift_problems("video.generate", "wan3-prime", PARAMS, "probe", "probe-s"), [])
_old = _sign("")
check("M6 迁移期：没有指纹的老条子一律放行（不许把在途单子炸掉），但字段确实空着",
      [_old, gw.plan_drift_problems("video.generate", "wan3-prime", PARAMS, "probe", "probe-s")],
      ["", []])
os.makedirs(os.path.join(M_TMP, "no-plan", "outputs"), exist_ok=True)
check("M7 不在射程要如实放行：落点附近没有提交计划（别的线）",
      gw.plan_drift_problems("video.generate", "wan3-prime",
                             {"output_path": os.path.join(M_TMP, "no-plan", "outputs", "a.mp4")},
                             "probe", "probe-s"), [])
check("M8 不在射程：免批能力（看图/文本）不参与比对",
      gw.plan_drift_problems("video.analyze", "mimo", PARAMS, "probe", "probe-s"), [])
gw.SPEND_APPROVALS_PATH, gw._ledger, gw.ALLOW_GRANT_WITHOUT_ATTEST = _sa, _sl, _sp2

src = io.open(GW_PY, encoding="utf-8", errors="replace").read()      # 重读一次（上面 K4 那份是加指纹前切的）
check("M9 接线：invoke 里真的调了 plan_drift_problems，且判在【消费批准之前】（顺序错了先吃掉别人的额度）",
      [src.count("_dp = plan_drift_problems("),
       src.find("_dp = plan_drift_problems(") < src.find("gate = check_spend_approval(capability, r[")],
      [1, True])
check("M9b 接线：核销那条路把 plan_id 传给了条子（不传 ⇒ M2 之外全是空判据）",
      [src.count("plan_id=p.get(\"plan_id\", \"\")"), src.count('kv["plan_id"] = submit_plan_id(')], [1, 1])
check("M10 变异：把【批的就是这一份计划】这一刀摘掉 ⇒ M9 的定位立刻失配（证明它钉的是真接线，不是注释）",
      src.replace("_dp = plan_drift_problems(", "_dp = [] or plan_drift_problems(", 1)
         .count("_dp = plan_drift_problems("), 0)

shutil_ok = True
try:
    import shutil
    shutil.rmtree(M_TMP, ignore_errors=True)
    shutil.rmtree(tmp, ignore_errors=True)
except Exception:
    shutil_ok = False
check("收尾：临时目录已清（闸自己不许留残留）", shutil_ok, True)

print(_safe(NL + "== 批准段数收口闸：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL))))
for f in FAIL:
    print(_safe("  ✗ " + f))
sys.exit(1 if FAIL else 0)
