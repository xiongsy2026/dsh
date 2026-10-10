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
