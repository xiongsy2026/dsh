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

# ── ④ agnes-v（Flash·上游免费额度）分发契约：全程假客户端，一次网络都不碰 ──────────
import types                                                 # noqa: E402,F401
from backend.services import agnes_video_client as av        # noqa: E402

seen = {}


async def fake_create(prompt, *, seconds="5", aspect_ratio="9:16", images=None, seed=None):
    seen["create"] = {"prompt": prompt, "seconds": seconds, "ratio": aspect_ratio, "images": images}
    return {"data": [{"id": "vid-777"}]}                    # 故意用嵌套形状，测 _dig 能不能挖到


async def fake_wait(vid, **kw):
    seen["wait"] = vid
    return {"status": "completed", "data": [{"video_url": "http://up/a.mp4"}]}


async def fake_dl(url, dest):
    seen["dl"] = [url, dest]
    return dest


real_av = (av.create_flash_task, av.wait_flash_task, av.download_file)
av.create_flash_task, av.wait_flash_task, av.download_file = fake_create, fake_wait, fake_dl
try:
    res = gw.call("video.generate", "agnes-v", prompt="一条自测段", duration=5,
                  image_urls=["http://i/1.jpg"], output_path="C:/tmp/a.mp4")
    check("4 提交→轮询→落盘全通（id 从嵌套 data[0].id 里挖到）",
          [res.get("success"), res.get("job_id"), res.get("path")], [True, "vid-777", "C:/tmp/a.mp4"])
    check("4b 参数按标准名转给客户端（duration→seconds、image_urls→images）",
          [seen["create"]["seconds"], seen["create"]["images"]], ["5", ["http://i/1.jpg"]])
    check("4c 下载用的是轮询回来的 url", seen["dl"], ["http://up/a.mp4", "C:/tmp/a.mp4"])

    async def boom_wait(vid, **kw):
        raise RuntimeError("失败: 内容不合规")

    av.wait_flash_task = boom_wait
    r2 = gw.call("video.generate", "agnes-v", prompt="x")
    check("4d 失败仍带 job_id + 说清可据此查上游（不让人重新盲跑）",
          [r2.get("success"), r2.get("job_id"), "可据此查" in str(r2.get("error"))], [False, "vid-777", True])

    async def no_id(prompt, **kw):
        return {"status": "queued"}

    av.create_flash_task, av.wait_flash_task = no_id, fake_wait
    r3 = gw.call("video.generate", "agnes-v", prompt="x")
    check("4e 上游没给 id 就明说，不假装在跑", [r3.get("success"), "video_id" in str(r3.get("error"))], [False, True])

    r4 = gw.call("video.generate", "agnes-v", prompt="x", video_urls=["http://v/1.mp4"])
    r5 = gw.call("video.generate", "agnes-v", prompt="x", first_frame="http://i/f.png")
    check("4f Flash 不收参考视频/首尾帧：本地就拒、不发请求（不支持的字段绝不静默丢）",
          ["不支持参考视频" in str(r4.get("error")), "没有首尾帧模式" in str(r5.get("error"))], [True, True])
    check("4g 已登记为『已接线』（管理台不再显示成空头路由）", gw.is_adapted("video.generate", "agnes-v"), True)
    # 只取 model/价格两项断言，绝不打印 resolve()（它会连 api_key 一起回显，今天已经泄过一次）
    check("4h 模型名来自配置、单价在册（上游免费 ≠ 我们不记账）",
          [bool(gw.resolve("video.generate", "agnes-v").get("model")),
           gw.unit_price("video.generate", "agnes-v")], [True, 30])
finally:
    av.create_flash_task, av.wait_flash_task, av.download_file = real_av
print("\n=== %d 条断言，失败 %d ===" % (total[0], len(fails)))
for n, g, w in fails:
    print("  FAIL", n, "got", g, "want", w)
sys.exit(1 if fails else 0)
