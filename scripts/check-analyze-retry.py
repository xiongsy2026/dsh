#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""闸：看图看片（video.analyze）的抖动重试契约。

起因（2026-10-08 实测）：同一个 8.25MB 的 .mov 经 agnes-3.0-flash 视频理解，3 成 2 败
（败形两种：HTTP 200 空 content、HTTP 500 AgnesAI_error），而 analyze_media 一次不中就返回失败
⇒ DSH 里的 agent 照着"这个模型不能识别视频"收工，把上游故障误报成能力缺失。

R 组 = 死因分类纯函数值表（含反向对照：4xx 必须不重试）。
T 组 = 真跑 analyze_media，只换 urlopen 这一层传输（🔴 不许整块替换被测函数——那样全绿也漏网）。
S 组 = 源码形状（旧的一次性返回形态必须消失、常量/退避/回显次数在位）。
"""
import io
import os
import re
import sys
import types
import urllib.error
import urllib.request   # 🔴 必须显式 import：只 import urllib.error 拿不到 urllib.request（AttributeError）
import json as _json                # run_with 造假响应用（2026-10-09 被我连带删过一次 ⇒ 闸自己 NameError，全组假红）
WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")

GW = os.environ.get("GATEWAY_PY") or os.path.join(BACKEND, r"gateway.py")
SRC = io.open(GW, encoding="utf-8").read()

PASS, FAIL = [], []


def check(name, got, want):
    (PASS if got == want else FAIL).append(name)
    flag = "PASS" if got == want else "FAIL"
    print(f"{flag:4} {name:58} got={got!r} want={want!r}")


def slice_fn(name):
    """按 def 边界切片，整块抠出来独立跑（不 import gateway：它 import 期就读 .env/config）。"""
    i = SRC.index("def %s(" % name)
    j = SRC.find("\ndef ", i + 1)
    return SRC[i:j if j > 0 else len(SRC)]


# ---------------- R 组：死因分类值表 ----------------
ns = {}
exec(slice_fn("_analyze_retryable"), ns)
is_retry = ns["_analyze_retryable"]

R_TRUE = [
    "上游返回空内容",
    "HTTP 500: {\"error\":{\"code\":\"do_request_failed\"}}",
    "HTTP 502: Bad Gateway",
    "HTTP 503: 请求上游失败，请稍后重试",
    "URLError: <urlopen error _ssl.c:1000: The handshake operation timed out>",
    "TimeoutError: timed out",
    "ConnectionResetError: [WinError 10054] 远程主机强迫关闭了一个现有的连接",
    "OSError: [WinError 10053] 你的主机中的软件中止了一个已建立的连接",
]
R_FALSE = [
    "HTTP 400: 参数不合法",
    "HTTP 401: Unauthorized",
    "HTTP 403: 额度不足",
    "HTTP 404: model not found",
    "HTTP 422: unprocessable content",
    "文件不存在: C:\\nope.mp4",
    "analyze 缺少 base_url/api_key（检查 .env 的 AGNES/MIMO 配置）",
    "",
    None,
    0,
    "完全没见过的第三种报错",
]
for e in R_TRUE:
    check("R 抖动类应重试: %s" % str(e)[:34], is_retry(e), True)
for e in R_FALSE:
    check("R 非抖动类不得重试: %s" % str(e)[:34], is_retry(e), False)
# 反向对照：判据不能是"非空即重试"（那等于没有分类）
check("R 反向对照 4xx 与 5xx 必须分开", is_retry("HTTP 400: x") != is_retry("HTTP 500: x"), True)

# ---------------- T 组：真跑 analyze_media，只换传输层 ----------------
CONST = {}
m = re.search(r"^ANALYZE_ATTEMPTS\s*=\s*(\d+)", SRC, re.M)
check("S 重试次数常量在模块层", bool(m), True)
ATTEMPTS = int(m.group(1)) if m else 0

ns2 = {}
# 🔴 用【真 gateway 模块的 globals】做命名空间 exec 被测函数，一个符号都不手工喂。
#    上一版这里手写 ns2["time"]=桩、ns2["BROWSER_UA"]="..."、ns2["_os"]=os —— 于是
#    "函数里用了模块没 import 的名字"（真跑 NameError）被我的桩全绿盖掉了（2026-10-09 就这么漏过一条）。
sys.path.insert(0, WS)
sys.path.insert(0, BACKEND)
from backend import gateway as GW      # 只 import，不发网络；它读 .env/config 是本地动作
ns2.update(dict(vars(GW)))
exec(slice_fn("analyze_media"), ns2)
SLEEPS = []


class Resp:
    def __init__(self, body):
        self._b = body.encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def run_with(responses):
    """responses: 每项是 ('ok', dict) 或 ('http', code, body) 或 ('exc', Exception)。返回 (结果, 调用次数)。

    🔴 只换 urlopen 这一层传输：analyze_media 里是【函数内】`import urllib.request`，
       所以往模块命名空间塞假 urllib 会被真 import 覆盖掉（第一版就栽在这，测了个寂寞）
       ⇒ 直接补丁真模块属性，finally 还原。
    """
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        item = responses[min(len(calls) - 1, len(responses) - 1)]
        kind = item[0]
        if kind == "ok":
            return Resp(_json.dumps(item[1]))
        if kind == "http":
            raise urllib.error.HTTPError(req.full_url, item[1], "HTTP %d" % item[1],
                                         {}, io.BytesIO(item[2].encode("utf-8")))
        raise item[1]

    real = urllib.request.urlopen
    import time as _t
    real_sleep = _t.sleep
    urllib.request.urlopen = fake_urlopen
    _t.sleep = lambda s: SLEEPS.append(s)     # 🔴 桩真模块属性（函数里是【函数内 import time】，注命名空间没用）
    SLEEPS.clear()
    try:
        out = ns2["analyze_media"](PROBE, "q",
                                   base_url="https://upstream.invalid/v1", api_key="k")
    finally:
        urllib.request.urlopen = real
        _t.sleep = real_sleep
    if os.environ.get("WK_DEBUG"): print("   DEBUG out=", {k: str(v)[:110] for k, v in out.items()}, "calls=", len(calls))
    return out, len(calls)


def ok_body(text):
    return ("ok", {"choices": [{"message": {"content": text}}], "usage": {"total_tokens": 42}})


import urllib  # noqa: E402  (run_with 里要用 HTTPError)

# 先造一个真存在的小文件：analyze_media 会先校验路径
PROBE = os.path.join(WS, r"scratch\_analyze_probe.mp4")
if not os.path.exists(PROBE):
    io.open(PROBE, "wb").write(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)

# T1 一次就成 ⇒ 只发 1 次、不睡
out, n = run_with([ok_body("一条袜子")])
check("T1 首次即成功：只发 1 次", n, 1)
check("T1 首次即成功：success", out.get("success"), True)
check("T1 首次即成功：attempts", out.get("attempts"), 1)

# T2 抖两次（空内容 + 500）后成功 ⇒ 必须发 3 次并退避 2 次
out, n = run_with([("ok", {"choices": [{"message": {"content": ""}}], "usage": {}}),
                   ("http", 500, "请求上游失败"),
                   ok_body("恢复")])
check("T2 空内容+500 后成功：发 3 次", n, 3)
check("T2 空内容+500 后成功：attempts", out.get("attempts"), 3)
check("T2 空内容+500 后成功：退避 2 次", len(SLEEPS), 2)
check("T2 空内容+500 后成功：success", out.get("success"), True)

# T3 一直 5xx ⇒ 上限就是 ANALYZE_ATTEMPTS，且报错里要说清"已试几次/是上游故障"
out, n = run_with([("http", 503, "请求上游失败，请稍后重试")])
check("T3 持续 5xx：不超上限", n, ATTEMPTS)
check("T3 持续 5xx：success=False", out.get("success"), False)
check("T3 持续 5xx：error 回显尝试次数", ("已试 %d/%d" % (ATTEMPTS, ATTEMPTS)) in str(out.get("error")), True)
check("T3 持续 5xx：error 指明上游故障而非格式", ("上游故障" in str(out.get("error"))), True)

# T4 参数/鉴权类 4xx ⇒ 只发 1 次（反向对照：不许无脑重试）
out, n = run_with([("http", 401, "Unauthorized")])
check("T4 401：只发 1 次不重发", n, 1)
check("T4 401：error 保留原样", ("HTTP 401" in str(out.get("error"))), True)

# T5 连接层异常（超时）⇒ 要重试
out, n = run_with([("exc", TimeoutError("timed out")), ok_body("回来了")])
check("T5 超时：重试后成功", (n, out.get("success")), (2, True))

# T6 路径不存在 ⇒ 直接拒，不发请求（0 次调用 = 0 消耗）
out6 = ns2["analyze_media"]("C:\\dsh\\scratch\\_no_such_file.mp4", "q",
                            base_url="https://upstream.invalid/v1", api_key="k")
check("T6 文件不存在：不发起请求", out6.get("success"), False)
check("T6 文件不存在：error 说清是没找到文件", ("文件不存在" in str(out6.get("error"))), True)

# ---------------- S 组：源码形状 ----------------
body = slice_fn("analyze_media")
check("S1 旧的一次性返回形态已消失（success: bool(text)）", "bool(text)" in body, False)
check("S2 循环用常量而非硬编码次数", "range(ANALYZE_ATTEMPTS)" in body, True)
check("S3 退避在循环内、按次数递增", bool(re.search(r"time\.sleep\(3\.0 \* \(attempt \+ 1\)\)", body)), True)
check("S4 不可重试立刻 break（不白烧上传）", "break" in body, True)
check("S5 返回信封带 attempts（agent 能据此说'重试过'）", '"attempts"' in body, True)
check("S6 视频仍走 video_url、图片走 image_url", ("video_url" in body and "image_url" in body), True)
check("S7 未新增第二处 urlopen（只有一条请求腿）", body.count("urlopen("), 1)
# 2026-10-09 真事故：重试腿调 time.sleep，而 gateway 模块层只有 `import time as _time`
# ⇒ 只有【走到重试】的那条路才 NameError，一次成功的调用探不出来（我就是这么漏过去的）。
check("S8 analyze_media 自己 import time（退避腿必须能跑）", "import time" in body, True)

# ==== T 组：看图腿的降采样（2026-10-20）====
# 立项理由不是"快"——公平对照实测：同一张 7.6MB 真产品图、同一个真题，agnes 原图直发 68.7s、
# 缩到长边 1560 反而 90.3s，两边 image_tokens 都是 1014 ⇒ 上游本来就把图压到同样的视觉 token，
# 墙钟的主导项是【生成】不是【上传】。真正值得管的是：每条素材都往内存里塞 10MB base64，
# 而 magpie 那条腿在 10MB 时观测到 220–380s 与 420s 超时。
_big = open(os.path.join(WS, r"productions\super-clone-20261013-2050\inputs\01-482695CF-product-1.jpg"), "rb").read()
_nb, _rz = GW._shrink_image_bytes(_big, 1560)
from io import BytesIO as _BIO                                    # noqa: E402
try:
    from PIL import Image as _PI
    _sz = max(_PI.open(_BIO(_nb)).size)
except Exception:                                                  # noqa: BLE001
    _sz = -1
check("T1 真产品图 7.6MB 缩到长边 ≤1560 且字节真的变小（判据打在纯函数上，不依赖网络）",
      [_rz, _sz <= 1560, len(_nb) < len(_big)], [True, True, True])
check("T2 缩不动/不是图 ⇒ 【原样返回】而不是抛（PIL 打不开一段假 mp4 字节也得照常发）",
      GW._shrink_image_bytes(b"\x00\xffnot-an-image", 1560), (b"\x00\xffnot-an-image", False))
check("T3 max_edge=0 或 PIL 缺失 ⇒ 也不许拦下这次看图（thumbnail((0,0)) 会抛 ⇒ 必须走 except 原样发）",
      GW._shrink_image_bytes(_big, 0)[1], False)
check("T4 mime 跟【真发出去的字节】一致：缩过就必须写 image/jpeg，还挂 image/png 是假类型",
      'image/jpeg" if resized' in body, True)
check("T5 视频一律不缩（那是分镜的像素事实，不在这个闸范围里）",
      "if not is_video and max_edge" in body, True)
check("T6 反向对照：把 resized 判断摘掉（缩放后仍报原 mime）⇒ T4 立刻红",
      'image/jpeg" if resized' in body.replace('image/jpeg" if resized', 'image/jpeg"', 1), False)
check("T7 物证三格齐（bytes_before/after/resized）—— 没有它们，「这次发的是原图还是缩过的」只能猜",
      [body.count('"bytes_before": bytes_before'), body.count('"bytes_after": bytes_after'),
       body.count('"resized": resized')], [2, 2, 2])

print()
print("=== 看图看片重试闸汇总：%d/%d 通过 ===" % (len(PASS), len(PASS) + len(FAIL)))
if FAIL:
    for f in FAIL:
        print("  FAIL:", f)
    sys.exit(1)
