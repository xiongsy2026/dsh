#!/usr/bin/env python
"""网页/CLI 型生图腿（豆包网页版 + Google Flow 命令行）接入闸。默认 0 浏览器、0 生成、0 花费。

这两条腿和其余路由的根本区别：**没有 API key，只有一个登录态，而且是驱动网页**。
所以这里重点不是"能不能连上"，而是三件容易做错的事：
  A 五张表都点到（漏 _ADAPTED 面板就显示"不能调"，漏 PRICE 就按 PRICE_BY_CAP=4 分瞎扣）
  B 探活不许把它们误报成"密钥未配置"（那是 HTTP 腿的判法）
  C 参数不全时必须在【起子进程之前】拒 —— 网页腿一次要 20-90 秒还弹一个 Chrome 窗口，
    白跑一次的代价是用户看到浏览器闪一下然后什么也没有
"""
import io
import json
import os
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, WS)
sys.path.insert(0, BACKEND)
from backend import gateway as g  # noqa: E402

SRC = open(os.path.join(BACKEND, r"gateway.py"), encoding="utf-8").read()
out = []


def check(name, got, want):
    ok = json.dumps(got, ensure_ascii=False, default=str) == json.dumps(want, ensure_ascii=False, default=str)
    out.append((ok, name))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else " => %r (期望 %r)" % (got, want)))


for lane in ("doubao", "gflow"):
    cap = "image.generate"
    check("A1 %s 在 ROUTES" % lane, (cap, lane) in g.ROUTES, True)
    check("A2 %s 声明已接（_ADAPTED）" % lane, g.is_adapted(cap, lane), True)
    check("A3 %s 单价在册且=0（未定价不许按图片默认 4 分瞎扣）" % lane, g.unit_price(cap, lane), 0)
    r = g.resolve(cap, lane)
    check("A4 %s provider 是网页/CLI 型（不是某家中转）" % lane, r["provider"] in ("doubao-web", "gflow-cli"), True)
    check("A5 %s 不假装有密钥" % lane, r["api_key"], "")
    check("A6 %s 有模型名（走 config，不硬编码在调用方）" % lane, bool(r["model"]), True)
    # B 探活：这两条不该被"没密钥"判死
    t = g.test_route(cap, lane)
    check("B1 %s 探活不误报 no-creds" % lane, t["status"] != "no-creds", True)
    check("B2 %s 探活状态在已知档里" % lane,
          t["status"] in ("live-ok", "reachable", "auth-fail", "config-error", "unreachable"), True)
    check("B3 %s 探活说人话（健康要说清是什么在，坏要给下一步动作）" % lane,
          any(k in (t["detail"] or "") for k in ("实跑", "login", "不存在", "没登录", "登录态", "会话在")), True)
    # C 本地拦截必须在起子进程之前
    for label, params in (("缺 prompt", {"prompt": "  "}), ("缺 output_path", {"prompt": "一张测试图"}),
                          ("参考图不存在", {"prompt": "x", "images": ["Z:\\没有这个文件.jpg"],
                                            "output_path": "C:\\dsh\\.tmp-nope.png"})):
        t0 = time.time()
        res = g.call(cap, lane, **params)
        el = round((time.time() - t0) * 1000)
        check("C %s·%s ⇒ 本地拒" % (lane, label), res.get("success"), False)
        check("C %s·%s ⇒ 拒得动（<1 秒=没起浏览器）" % (lane, label), el < 1000, True)

# D 实现层面的三条硬约束
check("D1 两条腿共用一把串行锁（一个登录态不能并发抢同一个 Chrome）",
      SRC.count("with _BROWSER_LOCK") >= 2, True)
check("D2 子进程走参数数组、绝不 shell=True", "shell=True" in SRC, False)
check("D3 gflow 关掉升级横幅（否则 --json 被污染成解析失败）",
      'e["GFLOW_CLI_UPDATE_CHECK"] = "0"' in SRC, True)
check("D4 gflow 落盘有目录扫描兜底（版本改了 --json 字段也不瞎）", "not in before" in SRC, True)
check("D5 豆包默认仍有头（新版无头能不能过要等实测，默认不赌）",
      "timeout=300.0, headless=False," in SRC, True)
check("D5b doubao.py 那条硬编码已放开（headless 照传给 chromium.launch）",
      "p.chromium.launch(headless=headless, **launch_kw)" in
      open(os.path.join(WS, r"scratch\video-ui\video-ui\doubao.py"), encoding="utf-8").read(), True)
check("D6 空文件不落假成功（>1024 字节才算出图）", bool(re.search(r"getsize\(out_path\) > 1024", SRC)), True)
check("D7 探活分支排在'没密钥就 no-creds'之前",
      SRC.index('if r["provider"] == "gflow-cli":') < SRC.index('if not r["api_key"] or not r["base_url"]:'), True)
# E "不弹浏览器"这条要求的真实落地形态
check("E1 gflow 默认有头，且把真实死因写进代码（profile 被打分 unusual activity，不是 headless/账号）",
      'timeout=300.0,\n                 headless=False) -> dict' in SRC
      and "PUBLIC_ERROR_UNUSUAL_ACTIVITY" in SRC, True)
check("E1b 但开关仍透传 GFLOW_CLI_HEADLESS（想省眼睛可以显式要无头，只是别当默认）",
      'env = {"GFLOW_CLI_HEADLESS"' in SRC, True)
check("E2 豆包窗口位置只走环境变量、且注释钉死'只能屏内'（挪出屏幕=未渲染=掉登录）",
      'env["DOUBAO_WIN_POS"]' in SRC and "只能屏内" in SRC, True)
check("E3 call() 的 gflow headless 默认 False（要无头得显式传）",
      'headless=bool(params.get("headless", False))' in SRC, True)
check("E4 豆包超时不甩锅给'慢'：看现场截图区分'卡在等回图'（实测=验证码墙）",
      'gen_05_sent.png' in SRC and "图形验证码" in SRC, True)
check("E5 无头默认关掉的理由写在代码里（有证据，不靠记忆）", "2026-10-04 实测无头跑到" in SRC, True)

bad = [n for ok, n in out if not ok]
print("\n=== 网页生图腿：%d 条断言，失败 %d ===" % (len(out), len(bad)))
sys.exit(1 if bad else 0)
