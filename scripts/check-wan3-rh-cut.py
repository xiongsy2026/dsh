#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""闸：RunningHub wan3.0 的咽喉切断 + 批准要求（2026-10-09 用户两条指令）。

背景（实测，不是猜）：11:01:13 有人提交了 wan3 任务、11:17:39 出片，而网关账本 10:32 之后一行都没有
⇒ 那条腿走的是 8090 的 /api/super-clone-wan3/*（app.py:10230+）直连 wan3_client，
   从不经 gateway.invoke() ⇒ 长在"前门"的批准闸对它根本不存在。
本闸钉的是：闸搬到了唯一咽喉 wan3_client.submit_reference_to_video 上。

K1 默认断开 ⇒ 抛错且【一次 HTTP 都没发出】
K2 打开开关但没批准条子 ⇒ 同样抛、同样 0 HTTP
K3 打开 + 有条子 ⇒ 真的放行到 requests.post（正向对照，防"永远拒"式假安全）
K4 顺序：guard 必须在 requests.post 之前（反向对照：post 在前必须判红）
K5 只断提交：query_task / poll / download 不许带 guard（断之前已提交的任务要用 taskId 捞回）
K6 网关那条 clone-r2v 已从 ROUTES【删除】（不是屏蔽）：拒因必须是"无此路由"+ 变异对照
K7 开关默认 False；K8 自检异常按【拒】处理
"""
import importlib
import io
import json
import os
import sys
import tempfile

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, WS)
sys.path.insert(0, BACKEND)

SCRATCH = tempfile.mkdtemp(prefix="wan3-cut-")
os.environ["GATEWAY_SPEND_APPROVALS"] = os.path.join(SCRATCH, "approvals.json")
os.environ["GATEWAY_LEDGER"] = os.path.join(SCRATCH, "ledger.sqlite")
os.environ.pop("DSH_SESSION_ID", None)

PASS, FAIL = [], []


def _safe(s):
    """按【输出流自己的编码】做 errors=replace。绝不 reconfigure 成 utf-8（那会造"零输出 + exit 0"的静默假绿）。
    2026-10-10 实测：这条 print 撞上 cp936 的 ⇒ 直接抛 UnicodeEncodeError ⇒ 闸【崩】而不是【红】，
    退出码也是 1，但没有一条判据跑完 ⇒ 谁都不知道剩下那几十条到底过没过。"""
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


from backend import gateway as gw          # noqa: E402
from backend.services import wan3_client as wc   # noqa: E402
from config import settings                # noqa: E402

gw.ALLOW_GRANT_WITHOUT_ATTEST = True       # 只对临时条子文件有效（双保险在 gateway 里）

CALLS = []


class FakeResp:
    def __init__(self, payload):
        self.status_code = 200
        self._p = payload

    def json(self):
        return self._p


class FakeRequests:
    def post(self, url, **kw):
        CALLS.append(("POST", url))
        # 真代码读的是【顶层】 taskId（wan3_client: data = resp.json(); task_id = data.get("taskId")）
        return FakeResp({"code": 0, "message": "success", "data": None, "taskId": "FAKE-T-1"})

    def get(self, url, **kw):
        CALLS.append(("GET", url))
        return FakeResp({"success": True, "data": {}, "code": 0})


wc.requests = FakeRequests()               # 🔴 只换传输层，函数体保持真身

SRC = io.open(os.path.join(BACKEND, r"services\wan3_client.py"), encoding="utf-8").read()
SUBMIT = SRC[SRC.index("def submit_reference_to_video("):]
SUBMIT = SUBMIT[:SUBMIT.index("\ndef ")]


def try_submit():
    CALLS.clear()
    try:
        wc.submit_reference_to_video("测试提示词", ["https://example.invalid/a.mp4"])
        return None
    except Exception as e:                                  # noqa: BLE001
        return "%s: %s" % (type(e).__name__, str(e)[:120])


# ---- K1 默认断开 ----
settings.WAN3_RH_ENABLED = False
err = try_submit()
check("K1 默认断开 ⇒ 抛 WanParamError", (err or "").startswith("WanParamError"), True)
check("K1b 断开原因写清是【用户指令】不是坏了", "已按 2026-10-09 指令断开" in (err or ""), True)
check("K1c 一次 HTTP 都没发出（不是发出去再报错）", len(CALLS), 0)
check("K1d 提示已提交任务仍可捞回", "query_task" in (err or ""), True)

# ---- K2 打开但没条子 ----
settings.WAN3_RH_ENABLED = True
err2 = try_submit()
check("K2 打开+无条子 ⇒ 仍抛", (err2 or "").startswith("WanParamError"), True)
check("K2b 0 HTTP", len(CALLS), 0)
check("K2c 点名要人去批准页点一次", "approval" in (err2 or ""), True)

# ---- K3 打开 + 有条子 ⇒ 真放行（正向对照，防"永远拒"式假安全）----
# 注：用闸专用后门 ALLOW_GRANT_WITHOUT_ATTEST 直接建【可用】条子。
# 🔴 "只有 pending（人没点）不算数"不在这里重测 —— 那是 check-approval-attest.py 44 条的地盘；
#    两处各测一遍只会漂移成两份互相矛盾的"人证"定义。
est = int((gw.meter("video.generate", "clone-r2v", {}) or {}).get("credits_estimate") or 0)
gr = gw.grant_spend_approval("video.generate", "clone-r2v", "local", 1, max(est, 1) or 1, "自测条子")
check("K3a 自测条子建出来了", bool((gr or {}).get("ok") or (gr or {}).get("id") or (gr or {}).get("pending")), True)
err4 = try_submit()
posts = [u for m, u in CALLS if m == "POST" and "127.0.0.1:7777" not in u]   # 7777 那条是代码里的 debug 点
check("K3b 有条子 ⇒ 恰好 1 次真提交（不是永远拒）", len(posts), 1)
check("K3c 放行时不抛", err4, None)

# ---- K4 顺序 ----
i_guard, i_post = SUBMIT.find("_wan3_submit_guard()"), SUBMIT.find("requests.post")
check("K4 guard 在 requests.post 之前", 0 <= i_guard < i_post, True)
check("K4b 反向对照：post 在前的形状必须判红", ("requests.post" in SUBMIT and SUBMIT.find("requests.post") < SUBMIT.find("_wan3_submit_guard()")), False)

# ---- K5 只断提交 ----
for fn in ("query_task", "poll_until_done", "download_result", "upload_media"):
    seg = SRC[SRC.index("def " + fn + "("):]
    seg = seg[:seg.index("\ndef ") if "\ndef " in seg else len(seg)]
    check("K5 %s 里没有 guard（孤儿任务/上传不受影响）" % fn, "_wan3_submit_guard" in seg, False)

# ---- K6/K7/K8 ----
pj = json.load(io.open(os.path.join(BACKEND, r"model_prices.json"), encoding="utf-8"))
KEY = ("video.generate", "clone-r2v")
# 🔴 2026-10-09 二次修订：这条腿已经从 ROUTES 里【删掉】了，不是"屏蔽"。旧断言只查 disabled 表 ⇒
#    lane 删了它照样绿（表里剩个墓碑），而"有人把 lane 加回 ROUTES、忘了清墓碑"它同样看不见。
#    ⇒ 改成钉【真删了 + 拒因必须是"无此路由"】，并配一条变异对照证明这个判据吃得住。
check("K6a ROUTES 里没有 clone-r2v（真删，不是屏蔽）", KEY in gw.ROUTES, False)
check("K6b PRICE_ROUTES 里也没有（不留价目孤儿）", KEY in gw.PRICE_ROUTES, False)
check("K6c disabled 表留着墓碑（防有人从 model_prices.json 复活它）",
      "video.generate/clone-r2v" in (pj.get("disabled") or {}), True)
try:
    gw.resolve("video.generate", "clone-r2v")
    rmsg, rraised = "", False
except Exception as e:                                          # noqa: BLE001
    rmsg, rraised = str(e), True
check("K6d resolve 真的拒（在预扣之前，0 hold 0 分）", rraised, True)
check("K6e 拒因是【无此路由】而非【已屏蔽】",
      ("无 purpose=clone-r2v" in rmsg, "已在管理台屏蔽" in rmsg), (True, False))
try:
    gw.ROUTES[KEY] = {"provider": "runninghub", "client": "x", "fn": "x", "fields": {}}
    try:
        gw.resolve("video.generate", "clone-r2v")
        mut_msg = ""
    except Exception as e2:                                     # noqa: BLE001
        mut_msg = str(e2)
    check("K6f 变异对照：lane 塞回 ROUTES ⇒ 拒因必须换成【已屏蔽】",
          "已在管理台屏蔽" in mut_msg, True)
finally:
    gw.ROUTES.pop(KEY, None)
check("K6g 变异对照没在进程里留残渣", KEY in gw.ROUTES, False)
cfg = io.open(os.path.join(BACKEND, r"config.py"), encoding="utf-8").read()
check("K7 config 默认 False（默认就是断的）", "WAN3_RH_ENABLED: bool = False" in cfg, True)
check("K8 自检异常按【拒】处理（闸坏了不许当成闸没拦）",
      "except Exception as e:" in SRC[SRC.index("def _wan3_submit_guard"):SRC.index("def submit_reference_to_video")]
      and "WanParamError(\"wan3 批准闸自检失败" in SRC, True)
check("K9 批准清单里 video.generate 仍在（生视频必须同意这条没被动）",
      gw.needs_spend_approval("video.generate"), True)

# ---- K10 wan3 路线的"假选项"不许留在门上（2026-10-09 用户指令删 wan3-dashscope）----
#    那条 lane 的 client 字段一直是 TODO、fn="-"，model 还写着 wan3.0-video-prime ⇒ 选了必失败，
#    但它出现在 caps/resolve 的清单里，人和 agent 都会以为能跑。删干净 + 落墓碑 + 钉拒因。
from backend.services import runninghub_client as rhc          # noqa: E402
for lane, want in (("wan3-dashscope", False), ("wan3-prime", True)):
    check("K10 ROUTES 里 video.generate/%s 应为 %s" % (lane, want),
          ("video.generate", lane) in gw.ROUTES, want)
check("K10b 删掉的这条在 disabled 表留墓碑（防有人从价目表复活它）",
      "video.generate/wan3-dashscope" in (pj.get("disabled") or {}), True)
try:
    gw.resolve("video.generate", "wan3-dashscope")
    k10msg = ""
except Exception as e:                                          # noqa: BLE001
    k10msg = str(e)
check("K10c 拒因是【无此路由】而非【已屏蔽】（钉的是删除，不是屏蔽）",
      ("无 purpose=wan3-dashscope" in k10msg, "已在管理台屏蔽" in k10msg), (True, False))
check("K10d 禁令文案里不许再列这条已删的 lane（被拦的人会照抄替代路）",
      "wan3-dashscope" in rhc.RH_VIDEO_BANNED, False)
# 真变异对照（不是把 K10 再念一遍）：把 lane 塞回 ROUTES ⇒ 拒因必须从"无此路由"变成"已屏蔽"
try:
    gw.ROUTES[("video.generate", "wan3-dashscope")] = {"provider": "dashscope", "client": "x",
                                                       "fn": "x", "fields": {}}
    try:
        gw.resolve("video.generate", "wan3-dashscope")
        mut10 = ""
    except Exception as e2:                                     # noqa: BLE001
        mut10 = str(e2)
    check("K10e 变异对照：lane 回到 ROUTES ⇒ 拒因换成【已屏蔽】（K10c 判的是删除不是空转）",
          "已在管理台屏蔽" in mut10, True)
finally:
    gw.ROUTES.pop(("video.generate", "wan3-dashscope"), None)
check("K10f 变异对照没在进程里留残渣", ("video.generate", "wan3-dashscope") in gw.ROUTES, False)

print(_safe("\n=== wan3 咽喉切断闸：%d/%d 通过 ===" % (len(PASS), len(PASS) + len(FAIL))))
for f in FAIL:
    print(_safe("  FAIL: " + str(f)))
print(_safe("临时目录: " + str(SCRATCH)))
sys.exit(1 if FAIL else 0)
