"""生成泳道韧性回归：本地闸门（时长白名单）+ 轮询掐线不重提交（0 分、全程假 urlopen）。

契约见 docs/ADR-model-gateway.md 与 docs/ADR-workbench-presence.md「第五次修订」。
跑法：PYTHONIOENCODING=utf-8 python scripts/check-lane-resilience.py
"""
import json
import os
import sys
import urllib.error
import urllib.request

os.environ.setdefault("HYPIT_JOBS_DB", os.path.join(WS, r"_scratch_b\lane_selftest.sqlite"))
WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, WS)
sys.path.insert(0, BACKEND)
sys.path.insert(0, WS)
sys.path.insert(0, BACKEND)

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

# ── ④′ 查询腿的 URL 归谁：④ 的假客户端把 get_task 整块替掉了，所以当年
#     「提交 200、查询 401 Invalid token」（查询打了 apihub 另一个鉴权域）全绿却漏网。
#     🔴 症状会被误读成"key 坏了"，其实是两条腿不同源。下面这两条只认真实请求形状。
import asyncio                                              # noqa: E402
import inspect                                              # noqa: E402
from config import settings as _cfg                         # noqa: E402

# 只查 get_task 自己的函数体（别的函数的注释里可能提到过旧主机名）
check("4i get_task 函数体里不许出现写死的 URL（必须经 _base() 拼）",
      "://" in inspect.getsource(av.get_task), False)

_seen_req = {}


class _FakeResp:
    status_code = 200

    def json(self):
        return {"status": "completed", "progress": 100}


class _FakeAsyncClient:
    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None, params=None):
        _seen_req["url"] = url
        # 只断言"带没带 Bearer"，绝不把 key 内容放进断言值（失败会打印 got）
        _seen_req["bearer"] = str((headers or {}).get("Authorization") or "").startswith("Bearer ")
        return _FakeResp()


_real_ac = av.httpx.AsyncClient
av.httpx.AsyncClient = _FakeAsyncClient
try:
    _got = asyncio.run(av.get_task("vid-777"))
finally:
    av.httpx.AsyncClient = _real_ac
check("4j 查询打到创建腿同一个 base（GET <base>/videos/<id>）", _seen_req.get("url"),
      _cfg.AGNES_BASE_URL.rstrip("/") + "/videos/vid-777")
check("4k 查询带上同一个 Bearer", _seen_req.get("bearer"), True)
check("4l completed 状态原样返回给上层", _got.get("status"), "completed")

# ── ⑤ H3（RunningHub 导演台）分发契约：假客户端，0 网络、0 花费 ───────────────────
from backend.services import h3_director_client as h3d       # noqa: E402
from backend.services import runninghub_client as rh         # noqa: E402

h5 = {}
# H3 现在会先验参考文件真的在盘上（不存在的文件白烧一次 GPU + 一笔预扣），所以夹具得是真文件
_SCR = os.path.join(WS, r"_scratch_b")
os.makedirs(_SCR, exist_ok=True)
A1, A2 = os.path.join(_SCR, "a.jpg"), os.path.join(_SCR, "b.jpg")
for _f in (A1, A2):
    open(_f, "wb").write(bytes(16))


async def fake_submit(task_type=None, segments=None, **kw):
    h5["submit"] = {"task_type": task_type, "seg": segments[0],
                    "wf": h3d.DIRECTOR_WORKFLOW_ID}
    return {"taskId": "2013508786110730241", "workflow_id": h5["submit"]["wf"], "unet_type": "ref2va"}


async def fake_poll(tid, out, timeout=None, **kw):
    h5["poll"] = [tid, out, timeout]
    return out


class FakeRH:
    def __init__(self, *a, **kw):
        pass

    async def query_task(self, tid):
        return {"taskStatus": "SUCCESS", "results": [{"url": "https://rh/x.mp4", "outputType": "mp4"}]}


real_h3 = (h3d.submit_h3_director, h3d.poll_and_download, rh.RunningHubClient)
h3d.submit_h3_director, h3d.poll_and_download, rh.RunningHubClient = fake_submit, fake_poll, FakeRH
try:
    r = gw.call("video.generate", "h3", prompt="产品旋转一周", duration=5,
                image_paths=[A1, A2], output_path="C:/tmp/h3.mp4")
    check("5 提交→轮询→落盘全通，并带回 job_id/workflow_id/unet_type",
          [r.get("success"), r.get("job_id"), r.get("path"), r.get("workflow_id"), r.get("unet_type")],
          [True, "2013508786110730241", "C:/tmp/h3.mp4", h3d.DIRECTOR_WORKFLOW_ID, "ref2va"])
    check("5b 补查一次拿回上游链接（落盘才算交付，链接 24h 就失效）", r.get("url"), "https://rh/x.mp4")
    check("5c 两张参考图 = r2v + refImagePaths",
          [h5["submit"]["task_type"], h5["submit"]["seg"].get("refImagePaths")],
          ["r2v", [A1, A2]])

    gw.call("video.generate", "h3", prompt="x", image_paths=[A1], output_path="C:/t.mp4")
    check("5d 单张图自动降成 i2v + startImagePath（不硬塞 refImagePaths）",
          [h5["submit"]["task_type"], h5["submit"]["seg"].get("startImagePath")], ["i2v", A1])
    gw.call("video.generate", "h3", prompt="x", output_path="C:/t.mp4")
    check("5e 无图 = t2v", h5["submit"]["task_type"], "t2v")

    r = gw.call("video.generate", "h3", prompt="x", image_paths=[os.path.join(_SCR, "ghost.jpg")],
                output_path="C:/t.mp4")
    check("5h2 参考文件不在盘上就拒（不白烧 GPU、不白占预扣）",
          [r.get("success"), "不存在" in str(r.get("error"))], [False, True])
    r = gw.call("video.generate", "h3", prompt="x", image_urls=["http://i/1.jpg"], output_path="C:/t.mp4")
    check("5f 公网 URL 本地就拒（导演台只吃上传后的 fileName，不静默丢字段）",
          [r.get("success"), "本地" in str(r.get("error"))], [False, True])
    r = gw.call("video.generate", "h3", prompt="x", image_paths=["C:/a.jpg"])
    check("5g 不给 output_path 就拒（不落盘=24 小时后什么都没有）",
          [r.get("success"), "output_path" in str(r.get("error"))], [False, True])

    async def boom_submit(task_type=None, segments=None, **kw):
        raise RuntimeError("未配置 RUNNINGHUB_H3_DIRECTOR_WORKFLOW_ID")

    h3d.submit_h3_director = boom_submit
    r = gw.call("video.generate", "h3", prompt="x", output_path="C:/t.mp4")
    check("5h 提交炸了回错误类别，不抛穿", [r.get("success"), "RuntimeError" in str(r.get("error"))], [False, True])

    async def boom_poll(tid, out, timeout=None, **kw):
        raise RuntimeError("H3 导演台任务失败: CUDA out of memory")

    h3d.submit_h3_director, h3d.poll_and_download = fake_submit, boom_poll
    r = gw.call("video.generate", "h3", prompt="x", output_path="C:/t.mp4")
    check("5i 轮询失败仍带 job_id（能据此查上游，不让人盲跑重烧一次 GPU）",
          [r.get("success"), r.get("job_id"), "out of memory" in str(r.get("error"))],
          [False, "2013508786110730241", True])

    # ── ⑥ rv2v：视频出动作 + 图片换主体（socks 那种"动作照原片、鞋换新鞋"）──────────
    SCR = os.path.join(WS, r"_scratch_b")
    os.makedirs(SCR, exist_ok=True)
    CLIP = os.path.join(SCR, "rv2v_clip.mp4")
    IMG1 = os.path.join(SCR, "shoe1.jpg")
    IMG2 = os.path.join(SCR, "shoe2.jpg")
    for f in (CLIP, IMG1, IMG2):
        with open(f, "wb") as fh:
            fh.write(b"\x00" * 32)

    gw.call("video.generate", "h3", prompt="新鞋跟原片动作", duration=5,
            output_path=os.path.join(SCR, "o1.mp4"), video_paths=[CLIP], image_paths=[IMG1, IMG2])
    seg = h5["submit"]["seg"]
    check("6 视频+图片 ⇒ task_type=rv2v，段里带 referenceVideoPath + refImagePaths",
          [h5["submit"]["task_type"], seg.get("referenceVideoPath") == CLIP, seg.get("refImagePaths") == [IMG1, IMG2]],
          ["rv2v", True, True])
    check("6b rv2v 不再塞 startImagePath（首帧形态和参考形态不能混）", "startImagePath" in seg, False)

    gw.call("video.generate", "h3", prompt="只照原片动", duration=5,
            output_path=os.path.join(SCR, "o2.mp4"), video_paths=[CLIP])
    check("6c 只给视频 ⇒ v2v（refs 为空）",
          [h5["submit"]["task_type"], h5["submit"]["seg"].get("refImagePaths")], ["v2v", None])

    bad = gw.call("video.generate", "h3", prompt="x", output_path=os.path.join(SCR, "o3.mp4"),
                  video_paths=[CLIP, CLIP, CLIP, CLIP], image_paths=[IMG1])
    check("6d 参考视频 >3 条本地就拒（上游端口表 3 条）", [bad.get("success"), "3 条" in str(bad.get("error"))],
          [False, True])
    bad = gw.call("video.generate", "h3", prompt="x", output_path=os.path.join(SCR, "o4.mp4"),
                  image_paths=[IMG1] * 10, video_paths=[CLIP])
    check("6e 参考图片 >9 条本地就拒", [bad.get("success"), "9 条" in str(bad.get("error"))], [False, True])
    bad = gw.call("video.generate", "h3", prompt="x", output_path=os.path.join(SCR, "o5.mp4"),
                  image_paths=["https://img/x.jpg"])
    check("6f 🔴 image_paths 塞公网 URL 在提交前就挡（过去要到客户端才 FileNotFoundError）",
          [bad.get("success"), "只收本地文件" in str(bad.get("error"))], [False, True])
    bad = gw.call("video.generate", "h3", prompt="x", output_path=os.path.join(SCR, "o6.mp4"),
                  video_paths=[os.path.join(SCR, "nope.mp4")])
    check("6g 参考文件不存在 ⇒ 本地拒，不浪费一次 GPU", [bad.get("success"), "不存在" in str(bad.get("error"))],
          [False, True])

    H3B = {"model": "h3", "prompt": "p", "duration": 5, "references": [{"url": "http://i/a.jpg"}]}
    check("6h 桥：h3 收视频参考（rv2v 现在真带得动）",
          hb.check_support(dict(H3B, videoReferences=[{"url": "http://i/c.mp4"}]))[0], True)
    ok, why = hb.check_support(dict(H3B, audioReferences=["http://i/a.mp3"]))
    check("6i 桥：h3 仍不收音频参考（没实测过带音频的形态）", [ok, "audio" in why], [False, True])
    ok, why = hb.check_support({"model": "seedance-2-mini", "prompt": "p", "duration": 5,
                                 "videoReferences": [{"url": "http://i/c.mp4"}]})
    check("6j 桥：seedance 路照旧拦视频参考（放开只针对 h3，不是一刀切）",
          [ok, "只带得动 image 参考" in why], [False, True])

    up_dir = os.path.join(SCR, "uploads")
    os.makedirs(up_dir, exist_ok=True)
    with open(os.path.join(up_dir, "vid123.mp4"), "wb") as fh:
        fh.write(b"\x00" * 16)
    saved = (hb.UPLOAD_DIR, hb.OUT_DIR)
    hb.UPLOAD_DIR, hb.OUT_DIR = up_dir, SCR
    try:
        got = hb._materialize_refs({"videoReferences": [{"url": "http://localhost:8100/api/hypit/uploads/files/vid123.mp4"}]},
                                   "t1", ("videoReferences",))
        check("6k 自家 /uploads 的回环地址直接指回磁盘那份，不再绕本机下载一遍（也不会把 .mp4 认成 .jpg）",
              [got == [os.path.normpath(os.path.join(up_dir, "vid123.mp4"))], got[0].endswith(".mp4")], [True, True])
    finally:
        hb.UPLOAD_DIR, hb.OUT_DIR = saved

    check("5j 已登记为『已接线』（管理台不再算空头路由）", gw.is_adapted("video.generate", "h3"), True)
    check("5l v2v/rv2v 会切到 ref2va 那条（.env 目前两条同号 ⇒ 只钉『会切』这件事，别当成两条都实测过）",
          h3d.REF2VA_TASK_TYPES >= {"v2v", "rv2v"}, True)
    # 只取价格与工作流号，绝不打印 resolve()（它连 api_key 一起回显）
    check("5k 单价 60 分在册，且用的号是 .env 覆盖后那条（探针真出过片的）",
          [gw.unit_price("video.generate", "h3"), h3d.DIRECTOR_WORKFLOW_ID,
           h3d.DIRECTOR_REF2VA_WORKFLOW_ID == h3d.DIRECTOR_WORKFLOW_ID],
          [60, "2095474072795865090", True])
finally:
    h3d.submit_h3_director, h3d.poll_and_download, rh.RunningHubClient = real_h3

print("\n=== %d 条断言，失败 %d ===" % (total[0], len(fails)))
for n, g, w in fails:
    print("  FAIL", n, "got", g, "want", w)
sys.exit(1 if fails else 0)
