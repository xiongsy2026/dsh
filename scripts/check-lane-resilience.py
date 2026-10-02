"""生成泳道韧性回归：本地闸门（时长白名单）+ 轮询掐线不重提交（0 分、全程假 urlopen）。

契约见 docs/ADR-model-gateway.md 与 docs/ADR-workbench-presence.md「第五次修订」。
跑法：PYTHONIOENCODING=utf-8 python scripts/check-lane-resilience.py
"""
import json
import os
import sys
import urllib.error
import urllib.request

os.environ.setdefault("HYPIT_JOBS_DB", r"C:\dsh\_scratch_b\lane_selftest.sqlite")
sys.path.insert(0, r"D:\video-skill")
sys.path.insert(0, r"D:\video-skill\backend")

import hypit_bridge as hb        # noqa: E402
from backend import gateway as gw  # noqa: E402

fails, total = [], [0]


def check(name, got, want):
    total[0] += 1
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name, "=>", json.dumps(got, ensure_ascii=False)[:140])
    if not ok:
        fails.append((name, got, want))


BASE = {"model": "seedance-2-mini", "prompt": "一条自测段", "resolution": "720p", "ratio": "9:16"}

# ── ① 时长白名单：账本里成功的段全是 5s，4/6/8s 三条都被上游 422（文案还是误导性的"请填写提示词"）──
check("1 5 秒（实测唯一验证过的时长）放行", hb.check_support(dict(BASE, duration=5))[0], True)
for bad in (4, 6, 8):
    ok, why = hb.check_support(dict(BASE, duration=bad))
    check("1b %d 秒本地就拦（不浪费一次上游往返）" % bad, [ok, "只有 5 秒" in why], [False, True])
check("1c 超过 10 秒仍按上限拦", hb.check_support(dict(BASE, duration=12))[0], False)
saved = hb.ALLOWED_SECONDS
hb.ALLOWED_SECONDS = (5.0, 10.0)
check("1d 白名单可通过 HYPIT_ALLOWED_SECONDS 扩（验过才扩，不默认放行）",
      hb.check_support(dict(BASE, duration=10))[0], True)
hb.ALLOWED_SECONDS = saved

# ── ② 轮询被掐线：继续问同一个任务，绝不重发提交（重提交=真生成第二条、花两份钱）──────────
calls = {"POST": 0, "GET": 0}


class FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(req, timeout=None):
    url = str(req.full_url)
    if getattr(req, "method", "GET") == "POST":
        calls["POST"] += 1
        return FakeResp({"id": "up-stream-123"})
    if "signed_url" in url:
        return FakeResp({"url": "http://fake-vinted/file.mp4"})     # out_path 不给 ⇒ 不会真去下载
    calls["GET"] += 1
    if calls["GET"] <= 2:                                   # 前两次轮询撞上 SSL UNEXPECTED_EOF
        raise urllib.error.URLError("[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol")
    return FakeResp({"status": "succeeded"})


real_urlopen = gw.urllib.request.urlopen if hasattr(gw, "urllib") else urllib.request.urlopen
target = gw.urllib if hasattr(gw, "urllib") else urllib
target.request.urlopen = fake_urlopen
try:
    res = gw._vinted_video("http://fake-vinted", "test-key", "一条自测段", model="seedance2.0fast",
                           duration=5, poll_interval=0.01, poll_timeout=30)
finally:
    target.request.urlopen = real_urlopen

check("2 掐线两次后仍拿到结果（这段不白烧）", [res.get("success"), res.get("job_id")], [True, "up-stream-123"])
check("2b 提交只发生一次（没有重复花费）", calls["POST"], 1)
check("2c 轮询确实重试了", calls["GET"] >= 3, True)

# 一直掐线：要判死，但必须带上上游任务号（人还能去查它到底出没出片）
def always_dead(req, timeout=None):
    if getattr(req, "method", "GET") == "POST":
        return FakeResp({"id": "up-stream-999"})
    raise urllib.error.URLError("[SSL: UNEXPECTED_EOF_WHILE_READING]")


target.request.urlopen = always_dead
try:
    res2 = gw._vinted_video("http://fake-vinted", "test-key", "一条自测段", model="seedance2.0fast",
                            duration=5, poll_interval=0.01, poll_timeout=30)
finally:
    target.request.urlopen = real_urlopen
check("3 连续 6 次问不上才判死", [res2.get("success"), "连接失败" in str(res2.get("error"))], [False, True])
check("3b 判死仍保留上游任务号", res2.get("job_id"), "up-stream-999")

print("\n=== %d 条断言，失败 %d ===" % (total[0], len(fails)))
for n, g, w in fails:
    print("  FAIL", n, "got", g, "want", w)
sys.exit(1 if fails else 0)
