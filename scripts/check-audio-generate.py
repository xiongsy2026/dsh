#!/usr/bin/env python
"""音频生成腿（火山 seed-audio）接入闸。

默认 0 网络 0 花费：只核四件事——
  A 注册表五张表都点到这条（漏一张就会"能 resolve 不能 call"或"面板里是死路由"）
  B 参数/凭证解析对（key 只验长度与是否就绪，绝不打印）
  C 本地拦截：缺 prompt / 缺 output_path 必须在【发请求之前】拒（防"提交成功但拿不到东西还扣分"）
  D 计费口径：上游按字符 ⇒ 单价是"分/千字"，1200 字要按 2 个千字单位算
--live 才真跑一条最短的（会真花钱，见脚本末尾的报价提示）。
"""
import io
import json
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, WS)
sys.path.insert(0, BACKEND)
from backend import gateway as g  # noqa: E402

SRC = open(os.path.join(BACKEND, r"gateway.py"), encoding="utf-8").read()
LIVE = "--live" in sys.argv
out = []


def check(name, got, want):
    ok = json.dumps(got, ensure_ascii=False, default=str) == json.dumps(want, ensure_ascii=False, default=str)
    out.append((ok, name, got))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else " => %r (期望 %r)" % (got, want)))


# ---- A 五张表 ----
check("A1 CAPABILITIES 有 audio.generate", "audio.generate" in g.CAPABILITIES, True)
check("A2 ROUTES 注册了 seed-audio", ("audio.generate", "seed-audio") in g.ROUTES, True)
check("A3 DEFAULT_PURPOSE 指到它", g.DEFAULT_PURPOSE.get("audio.generate"), "seed-audio")
check("A4 PRICE_ROUTES 有这一条（0=未定价也要在册）", ("audio.generate", "seed-audio") in g.PRICE_ROUTES, True)
check("A5 PRICE_BY_CAP 兜底也在册", "audio.generate" in g.PRICE_BY_CAP, True)
check("A6 _ADAPTED 声明已接（面板据此判断能不能调）", g.is_adapted("audio.generate", "seed-audio"), True)

# ---- B 解析 ----
r = g.resolve("audio.generate")
check("B1 base_url", r["base_url"], "https://openspeech.bytedance.com")
check("B2 model 来自 config 不是硬编码", r["model"], "seed-audio-1.0")
check("B3 密钥就绪（只验长度，不打印）", 30 < len(r["api_key"] or "") < 60, True)
check("B4 摘要里不含密钥明文", (lambda s: all(x not in s for x in ("api_key", r["api_key"])))
      (json.dumps(g.safe_summary(r), ensure_ascii=False)), True)
check("B5 鉴权走 X-Api-Key 头", '"X-Api-Key": api_key' in SRC, True)
check("B6 只有 401/403 才换 Bearer 重试一次", re.search(r'e\.code in \(401, 403\) and attempt == 0', SRC) is not None, True)
check("B7 call() 里有分发分支", 'capability == "audio.generate" and r["purpose"] == "seed-audio"' in SRC, True)

# ---- C 本地拦截（0 网络） ----
check("C1 缺 prompt ⇒ 本地拒", g.call("audio.generate", prompt="  ")["success"], False)
check("C2 拒因说的是缺文本", "prompt" in (g.call("audio.generate", prompt="")["error"] or ""), True)
check("C3 缺 output_path ⇒ 本地拒（二进制体不落盘=白花钱）",
      g.call("audio.generate", prompt="测试")["success"], False)
check("C4 拒因点名 output_path", "output_path" in (g.call("audio.generate", prompt="测试")["error"] or ""), True)
check("C5 空体不落假成功（<128 字节判失败）", "len(data) < 128" in SRC, True)

# ---- D 计费口径 ----
orig = g.unit_price
try:
    g.unit_price = lambda c, p: 5
    check("D1 50 字 = 1 个千字单位", g.meter("audio.generate", "seed-audio", {"prompt": "字" * 50})["credits_estimate"], 5)
    check("D2 1200 字 = 2 个千字单位", g.meter("audio.generate", "seed-audio", {"prompt": "字" * 1200})["credits_estimate"], 10)
    check("D3 text_prompt 别名也认", g.meter("audio.generate", "seed-audio", {"text_prompt": "字" * 1200})["credits_estimate"], 10)
finally:
    g.unit_price = orig
check("D4 不按 token 结算（无 usage 字段 ⇒ basis=flat）",
      g.settle_credits("audio.generate", "seed-audio", {"success": True, "chars": 1200}, 10)[:3], (10, 0, "flat"))
check("D5 音频没有 token 费率（别被当文本脑按 token 扣）", g.token_rate("audio.generate"), None)

if LIVE:
    import tempfile
    dst = os.path.join(tempfile.gettempdir(), "gw-audio-probe.mp3")
    print("\n--live：提交一条最短请求（真花钱·上游按字符计费）→", dst)
    res = g.invoke("audio.generate", account="local",
                   params={"prompt": "你好，这是一条网关连通性测试。", "output_path": dst})
    body = res.get("result") or {}
    check("L1 信封 ok", res.get("ok"), True)
    check("L2 落盘存在且非空", os.path.isfile(dst) and os.path.getsize(dst) > 1000, True)
    print("   content_type=%s bytes=%s auth=%s ms=%s" % (body.get("content_type"), body.get("bytes"),
                                                        body.get("auth"), body.get("ms")))
    if not res.get("ok"):
        print("   错误：", res.get("error"))

bad = [n for ok, n, _ in out if not ok]
print("\n=== 音频生成腿：%d 条断言，失败 %d ===" % (len(out), len(bad)))
sys.exit(1 if bad else 0)
