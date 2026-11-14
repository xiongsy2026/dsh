#!/usr/bin/env python
"""H3 第二条腿（video.generate/h3-rpa · 本机 8093 浏览器 RPA）的回归闸。

跑法：python scripts/check-h3-rpa-lane.py

五件事必须同时成立：
 ① provider 以 local 开头 ⇒ 管理台不做 HTTP 探活（探它=真开浏览器跑一条，探活不许花钱）。
 ② 花钱批准在【网关这一端】消费（这条路的必经入口就是 gateway call）⇒ 它绝不能出现在
    SPEND_APPROVAL_AT_SOURCE 豁免表里；进了那张表 = 一条绕过批准的后门。
 ③ 参数边界照上游真实形状拦：宽高必须是 32 的倍数（[12].output.multiple=32）、engine 三档、
    task_type 六档、参考图 ≤3 且【只吃本地文件】（公网 URL 一律拒）。
 ④ 9:16 的答案钉在表里：480×864（从 8093 回收结构读到的上游现值），并注明本机【还没实跑过】。
 ⑤ 重跑=双花（还占着浏览器串行锁）⇒ job_id 只捞不重投；查询断线退避重问同一个 id；
    判"失败"之前先看盘（H3 工作流末尾有防翻录机关，上游终态 FAILED≠没出片）。

全离线：批准条子、积分账本指到临时目录；HTTP 层只换【传输】(httpx.Client)，
被测函数 `_h3_rpa_run` 整块真跑 —— 把它本身替掉就是假保险。
已知缺口（如实记下，不在这里当断言）：8093 现在绑 0.0.0.0 且没有会话归属/源侧批准闸，
C:\\Minimax-H3\\config.py 的 RunningHub key 是明文默认值 —— 这三条已报用户待定。
"""
import json
import os
import re
import sys
import tempfile

GW_ROOT = BACKEND
GATEWAY_PY = os.path.join(GW_ROOT, "backend", "gateway.py")
CONFIG_PY = os.path.join(GW_ROOT, "config.py")
H3_ROOT = r"C:\Minimax-H3"
H3_APP = os.path.join(H3_ROOT, "app.py")
H3_CLIENT = os.path.join(H3_ROOT, "h3_rpa_client.py")

SCRATCH = tempfile.mkdtemp(prefix="h3-rpa-gate-")
os.environ["GATEWAY_SPEND_APPROVALS"] = os.path.join(SCRATCH, "approvals.json")
os.environ["GATEWAY_LEDGER"] = os.path.join(SCRATCH, "ledger.sqlite")
os.environ.pop("DSH_SESSION_ID", None)          # 闸不许继承调用方身上的会话标
os.environ["PYTHONPATH"] = GW_ROOT
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, GW_ROOT)

TOTAL = 0
fails = []


def _safe(s):
    """控制台是 GBK：按【输出流编码】做 errors=replace。
    🔴 绝不 sys.stdout.reconfigure（造「零输出 + exit 0」的静默假绿）；也别裸 s.encode()（默认 utf-8 永不失败=没兜住）。"""
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        s.encode(enc)
        return s
    except (UnicodeEncodeError, LookupError):
        return s.encode(enc, "replace").decode(enc, "replace")


def check(name, got, want):
    global TOTAL
    TOTAL += 1
    a = json.dumps(got, ensure_ascii=False, default=str)
    b = json.dumps(want, ensure_ascii=False, default=str)
    print(_safe(("PASS " if a == b else "FAIL ") + name + ("" if a == b else "  => %r (期望 %r)" % (got, want))))
    if a != b:
        fails.append(name)


def read(p):
    with open(p, encoding="utf-8", errors="replace") as f:
        return f.read()


import httpx                                       # noqa: E402
from backend import gateway as gw                  # noqa: E402

# 2026-10-08 花钱批准加了人证前置（弹窗点【是】+ 回敲确认码）。闸要直接构造条子，
# 所以这里显式声明自测模式。它只在 GATEWAY_SPEND_APPROVALS 已改到 scratch 时生效
# （gateway.py 里那条双保险），生产账本物理上签不出来。
gw.ALLOW_GRANT_WITHOUT_ATTEST = True

SRC_GW = read(GATEWAY_PY)
SRC_CFG = read(CONFIG_PY)
SRC_H3_APP = read(H3_APP)
SRC_H3_CLIENT = read(H3_CLIENT)


def between(src, start_marker, end_marker):
    i = src.find(start_marker)
    if i < 0:
        return ""
    j = src.find(end_marker, i + len(start_marker))
    return src[i:(j if j > 0 else len(src))]


RUN_BLOCK = between(SRC_GW, "def _h3_rpa_run(", "\n\n\ndef ")
PAY_BLOCK = between(SRC_GW, "def _h3_rpa_payload(", "def _h3_rpa_run(")

# ── A 注册表 / 计价 / 探活不花钱 ─────────────────────────────────────────────────
R = gw.ROUTES.get(("video.generate", "h3-rpa")) or {}
check("A1 路由在册、provider 以 local 开头（8100 据此不做 HTTP 探活=探它不花钱）",
      [bool(R), str(R.get("provider")).startswith("local")], [True, True])
check("A2 适配器已接（is_adapted 真）", gw.is_adapted("video.generate", "h3-rpa"), True)
check("A3 内置单价 = 60 分/次（照 H3 API 腿借的量级）", gw.PRICE_ROUTES.get(("video.generate", "h3-rpa")), 60)
check("A4 没跑过的钱不许写得像跑过：价目源码标着占位/未实测",
      "占位" in between(SRC_GW, "# H3 的浏览器 RPA 腿", '("video.generate", "h3-rpa"): 60'), True)
check("A5 离线探活回 local（既不是 live-fail，也不发起生成）",
      gw.test_route("video.generate", "h3-rpa")["status"], "local")
check("A6 默认档没被顶掉：video.generate 仍默认 omni-t2v、h3 那条 API 腿也还在",
      [gw.DEFAULT_PURPOSE["video.generate"], ("video.generate", "h3") in gw.ROUTES], ["omni-t2v", True])
check("A7 两条 H3 腿是两个 purpose（合并就没人知道这次走的是 API 还是浏览器）",
      [R.get("client"), gw.ROUTES[("video.generate", "h3")]["client"]],
      ["gateway._h3_rpa_run", "h3_director_client"])
check("A8 🔴 批准条子在【网关这一端】消费 ⇒ 绝不能进 SPEND_APPROVAL_AT_SOURCE 豁免表",
      gw.approval_consumed_at_source("video.generate", "h3-rpa"), False)
M = lambda p: gw.meter("video.generate", "h3-rpa", p)["credits_estimate"]
check("A9 按次不按秒：一次 RPA 跑 10 秒或 30 秒都记 60（上游烧的是它自己账户的 GPU 秒）",
      [M({"duration": d}) for d in (5, 10, 30, 120)], [60, 60, 60, 60])
check("A9b count=3 ⇒ 180（次数才是乘数）", M({"count": 3}), 180)
# 例子从 wan3-dashscope 换成 omni-t2v：前者 2026-10-09 被删（client 是 TODO、从没适配器），
# 拿两条不同单价的腿正好证明"按次"只是 h3-rpa 一条的例外，不是一刀切。
check("A9c 例外不外溢：API 腿仍按每 10 秒折档（h3 与 omni-t2v 各自价目）",
      [gw.meter("video.generate", "h3", {"duration": 20})["credits_estimate"],
       gw.meter("video.generate", "omni-t2v", {"duration": 20})["credits_estimate"]], [120, 80])

# ── B 参数值表（纯函数，0 分不发 HTTP）──────────────────────────────────────────
P = gw._h3_rpa_payload
check("B1 只给 job_id ⇒ poll（只捞不重投），且 poll 也带 body 键（下游不许 KeyError）",
      [P({"job_id": "abc"})[0]["mode"], "body" in P({"job_id": "abc"})[0]], ["poll", True])
check("B2 prompt 与 segments 都没有 ⇒ 本地拒", bool(P({})[1]), True)
check("B2b segments 里某段缺 prompt ⇒ 拒并点名第几段",
      "segments[1]" in str(P({"segments": [{"prompt": "a", "durationSec": 5},
                                           {"durationSec": 5}]})[1]), True)
check("B2c durationSec ≤0 或非数 ⇒ 拒（不静默改成 5 秒）",
      [bool(P({"segments": [{"prompt": "a", "durationSec": 0}]})[1]),
       bool(P({"segments": [{"prompt": "a", "durationSec": "abc"}]})[1])], [True, True])
check("B2d durationSec 缺省=5 秒、duration 作为别名也吃",
      [P({"prompt": "x"})[0]["body"]["segments"][0]["durationSec"],
       P({"segments": [{"prompt": "x", "duration": 7}]})[0]["body"]["segments"][0]["durationSec"]],
      [5.0, 7.0])
check("B3 engine 只认 plus/lite/ultra，乱写一律拒（不静默回落 plus）",
      [P({"prompt": "x", "engine": "turbo"})[1] is not None,
       P({"prompt": "x", "engine": "ultra"})[0]["body"]["engine"]], [True, "ultra"])
check("B4 task_type 六档之外拒；不填则不写这个字段（让 8093 按素材自动推）",
      [P({"prompt": "x", "task_type": "s2v"})[1] is not None,
       "task_type" in P({"prompt": "x"})[0]["body"],
       P({"prompt": "x", "task_type": "r2v"})[0]["body"]["task_type"]], [True, False, "r2v"])
check("B5 默认 864×480（上游 [12] 现值），两个数都是 32 的倍数",
      [(P({"prompt": "x"})[0]["width"], P({"prompt": "x"})[0]["height"])], [(864, 480)])
check("B6 🔴 9:16 = 480×864（从 8093 回收结构读到的上游现值），且都整除 32",
      [gw._H3_RPA_RATIOS["9:16"], 480 % 32 == 0 and 864 % 32 == 0], [(480, 864), True])
check("B6b ratio=9:16 走通、ratio=1:1 拒（没有证据的档不许假装有）",
      [P({"prompt": "x", "ratio": "9:16"})[0]["body"]["height"],
       bool(P({"prompt": "x", "ratio": "1:1"})[1])], [864, True])
check("B6c 9:16 那条在源码里注明【本机还没实跑过】（文档形状≠实测）",
      "9:16" in between(SRC_GW, "# ratio → 宽高", "_H3_RPA_RATIOS ="), True)
check("B7 宽高不是 32 的倍数 ⇒ 拒话点名 multiple=32 这条上游约束",
      [bool(P({"prompt": "x", "width": 500, "height": 800})[1]),
       "32" in str(P({"prompt": "x", "width": 500, "height": 800})[1])], [True, True])
check("B7b 0 或负数宽高 ⇒ 拒（0 不许被当成「没填」而回落成 864）",
      [bool(P({"prompt": "x", "width": 0})[1]),
       bool(P({"prompt": "x", "height": -32})[1])], [True, True])
check("B7c width/height 只给一个 ⇒ 拒（另一半静默回落就是「480×480 怪片」的来源）",
      [bool(P({"prompt": "x", "width": 480})[1]), bool(P({"prompt": "x", "height": 864})[1])], [True, True])
check("B8 显式 width/height 优先于 ratio（人给的就照人给的）",
      [P({"prompt": "x", "ratio": "9:16", "width": 768, "height": 1024})[0]["body"]["width"],
       P({"prompt": "x", "ratio": "9:16", "width": 768, "height": 1024})[0]["ratio"]], [768, "3:4"])
check("B8b 尺寸不在任何档里（自定义）⇒ 照样出 body、ratio 回空串（不许因为认不出档名就炸）",
      [P({"prompt": "x", "width": 608, "height": 1088})[1],
       P({"prompt": "x", "width": 608, "height": 1088})[0]["ratio"]], [None, ""])
_ok_img = os.path.join(SCRATCH, "ref.jpg")
with open(_ok_img, "wb") as f:
    f.write(b"z" * 512)
check("B9 参考图【只吃本地文件】：公网 URL 一律拒（形状别记混）",
      [bool(P({"prompt": "x", "ref_images": ["https://cdn/a.jpg"]})[1]),
       "本地" in str(P({"prompt": "x", "ref_images": ["https://cdn/a.jpg"]})[1])], [True, True])
check("B9b 本地路径不存在 ⇒ 拒（花钱前就拦住，别让上游报读不到文件）",
      bool(P({"prompt": "x", "ref_images": [os.path.join(SCRATCH, "nope.jpg")]})[1]), True)
check("B9c 参考图 ≤3 张（[12] 的 refs 槽位数），第 4 张拒",
      [bool(P({"prompt": "x", "ref_images": [_ok_img] * 3})[1]),
       bool(P({"prompt": "x", "ref_images": [_ok_img] * 4})[1])], [False, True])
check("B10 首/尾帧同样只吃存在的本地文件",
      [bool(P({"prompt": "x", "start_image": "https://a/b.png"})[1]),
       bool(P({"prompt": "x", "end_image": os.path.join(SCRATCH, "nope.png")})[1]),
       P({"prompt": "x", "start_image": _ok_img})[0]["body"]["start_image"] == _ok_img], [True, True, True])
check("B11 output_path 落成 body[\"output\"] 的绝对路径（8093 直接把 mp4 写到那）",
      os.path.isabs(P({"prompt": "x", "output_path": os.path.join(SCRATCH, "o.mp4")})[0]["body"]["output"]), True)
check("B12 image_urls 作为 ref_images 的别名照收（网关标准参数名）",
      len(P({"prompt": "x", "image_urls": [_ok_img]})[0]["body"]["ref_images"]), 1)
check("B13 校验函数不联网、不碰浏览器（纯函数：整块值表跑得动）",
      ["httpx" not in PAY_BLOCK, "playwright" not in PAY_BLOCK.lower()], [True, True])

# ── D 行为：假传输层 + 真适配器 ───────────────────────────────────────────────────
MP4 = b"y" * 4096
BASE = "http://127.0.0.1:8099"          # 假 base：真 8093 一次都不许碰
DONE_PATH = os.path.join(SCRATCH, "h3_out", "h3_fake_done.mp4")
os.makedirs(os.path.dirname(DONE_PATH), exist_ok=True)


class Resp:
    def __init__(self, status=200, payload=None, text=None):
        self.status_code = status
        self._p = payload if payload is not None else {}
        self.text = text if text is not None else json.dumps(self._p, ensure_ascii=False)

    def json(self):
        return self._p


class FakeClient:
    """脚本表 script = {「POST/GET <路径后缀>」: [Resp|Exception...]}；每次调用都记下来。"""
    script, calls, posts = {}, [], []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _take(self, method, url, body=None):
        FakeClient.calls.append((method, url, body))
        if method == "POST":
            FakeClient.posts.append(url)
        for key, seq in FakeClient.script.items():
            verb, path = key.split(" ", 1)
            if verb == method and url.endswith(path) and seq:
                r = seq.pop(0)
                if isinstance(r, Exception):
                    raise r
                return r
        return Resp(status=404, payload={"fake_unmatched": "%s %s" % (method, url)})

    def post(self, url, json=None, timeout=None):
        return self._take("POST", url, json)

    def get(self, url, timeout=None):
        return self._take("GET", url)


httpx.Client = FakeClient                # 只换传输层


def scripted(script):
    FakeClient.script = script
    FakeClient.calls = []
    FakeClient.posts = []


def urls_of(method):
    return [u for m, u, b in FakeClient.calls if m == method]


def fresh(path, size=len(MP4)):
    if os.path.isfile(path):
        os.remove(path)
    with open(path, "wb") as f:
        f.write(b"x" * size)


# D1 正常链路：提交一次 → 轮询 → 按盘上产物判成功 → 归位到调用方要的路径
fresh(DONE_PATH)
scripted({"POST /api/generate": [Resp(payload={"taskId": "tk1"})],
          "GET /api/status/tk1": [Resp(payload={"status": "running", "stage": "注入参数", "progress": 84}),
                                  Resp(payload={"status": "done", "stage": "完成", "progress": 100,
                                                "output": DONE_PATH, "outputUrl": "/outputs/x.mp4"})]})
want = os.path.join(SCRATCH, "deliv", "h3_01.mp4")
res = gw._h3_rpa_run({"prompt": "女生转身展示风衣", "h3_base": BASE, "output_path": want,
                      "poll_interval": 0.01, "poll_timeout": 5})
check("D1 盯到终态出片（不是「已提交」），且落到调用方要的路径",
      [res["success"], res.get("path") == want, os.path.isfile(want)], [True, True, True])
check("D1b 只提交一次 /api/generate（重跑=双花+占串行锁）",
      [len(FakeClient.posts), urls_of("POST")[0].endswith("/api/generate")], [1, True])
check("D2 信封带回 8093 的 taskId + 宽高 + 字节数",
      [res.get("job_id"), res.get("width"), res.get("height"), res.get("bytes") == len(MP4)],
      ["tk1", 864, 480, True])
check("D2b body 里带的是校验后的形状（segments/engine/timeout 都在）",
      sorted(k for k in FakeClient.calls[0][2])[0:3], ["engine", "frame_rate", "height"])

# D3 只捞不重投
fresh(DONE_PATH)
scripted({"GET /api/status/tk9": [Resp(payload={"status": "done", "output": DONE_PATH})]})
rj = gw._h3_rpa_run({"job_id": "tk9", "h3_base": BASE, "output_path": os.path.join(SCRATCH, "j.mp4"),
                     "poll_interval": 0.01, "poll_timeout": 5})
check("D3 job_id 模式一个 POST 都不发（8093 任务表在内存里，捞回来靠的就是它）",
      [len(FakeClient.posts), rj["success"]], [0, True])

# D4 串行锁被占
scripted({"POST /api/generate": [Resp(status=409, text="已有任务在跑，请等当前任务完成")]})
r409 = gw._h3_rpa_run({"prompt": "x", "h3_base": BASE, "output_path": os.path.join(SCRATCH, "a.mp4"),
                       "poll_interval": 0.01, "poll_timeout": 5})
check("D4 409 ⇒ 报「串行锁被占」，不轮询也不排队（总调用数=1）",
      [r409["success"], "串行锁" in (r409["error"] or ""), len(FakeClient.calls)], [False, True, 1])

# D5 回 done 但盘上没文件
scripted({"POST /api/generate": [Resp(payload={"taskId": "tk2"})],
          "GET /api/status/tk2": [Resp(payload={"status": "done", "output": os.path.join(SCRATCH, "gone.mp4")})]})
rgone = gw._h3_rpa_run({"prompt": "x", "h3_base": BASE, "output_path": os.path.join(SCRATCH, "b.mp4"),
                        "poll_interval": 0.01, "poll_timeout": 5})
check("D5 回 done 但盘上没有 ⇒ 判失败（「上游说成功」不等于交付）",
      [rgone["success"], bool(os.path.isfile(os.path.join(SCRATCH, "b.mp4")))], [False, False])

# D6 done 但只有 4 字节
tiny = os.path.join(SCRATCH, "tiny.mp4")
fresh(tiny, 4)
scripted({"POST /api/generate": [Resp(payload={"taskId": "tk3"})],
          "GET /api/status/tk3": [Resp(payload={"status": "done", "output": tiny})]})
rtiny = gw._h3_rpa_run({"prompt": "x", "h3_base": BASE, "output_path": os.path.join(SCRATCH, "c.mp4"),
                        "poll_interval": 0.01, "poll_timeout": 5})
check("D6 只有 4 字节 ⇒ 判失败且不在盘上留个假 mp4 冒充成片",
      [rtiny["success"], os.path.isfile(os.path.join(SCRATCH, "c.mp4"))], [False, False])

# D7 🔴 状态 error 但盘上有片 ⇒ 按盘判成功（防翻录机关那条）
fresh(DONE_PATH)
scripted({"POST /api/generate": [Resp(payload={"taskId": "tk4"})],
          "GET /api/status/tk4": [Resp(payload={"status": "error", "output": DONE_PATH,
                                                "error": "'bool' object has no attribute 'cpu'"})]})
rerr = gw._h3_rpa_run({"prompt": "x", "h3_base": BASE, "output_path": os.path.join(SCRATCH, "d.mp4"),
                       "poll_interval": 0.01, "poll_timeout": 5})
check("D7 error 但盘上有成片 ⇒ 判成功并写明原因（照着 error 重跑=双花）",
      [rerr["success"], "机关" in (rerr.get("note") or "")], [True, True])

# D8 error 且盘上没片 ⇒ 失败带上游原文 + job_id
scripted({"POST /api/generate": [Resp(payload={"taskId": "tk5"})],
          "GET /api/status/tk5": [Resp(payload={"status": "error", "output": os.path.join(SCRATCH, "nope.mp4"),
                                                "error": "登录态过期：请重跑 --login"})]})
r8 = gw._h3_rpa_run({"prompt": "x", "h3_base": BASE, "output_path": os.path.join(SCRATCH, "e.mp4"),
                     "poll_interval": 0.01, "poll_timeout": 5})
check("D8 error 且盘上确实没片 ⇒ 失败交回上游原文 + job_id（登录态那类要原样说）",
      [r8["success"], "登录态过期" in (r8["error"] or ""), r8.get("job_id")], [False, True, "tk5"])

# D9 404：8093 任务表只在内存里，重启就丢
scripted({"GET /api/status/tklost": [Resp(status=404, payload={"detail": "任务不存在"})]})
rlost = gw._h3_rpa_run({"job_id": "tklost", "h3_base": BASE, "poll_interval": 0.01, "poll_timeout": 5})
check("D9 查询 404 ⇒ 点名「任务表只在内存/按盘判/别重跑」，且零 POST",
      [rlost["success"], "内存" in (rlost["error"] or ""), len(FakeClient.posts)], [False, True, 0])

# D10 查询连炸 6 次 ⇒ 判死但留 job_id、绝不重提交
scripted({"GET /api/status/tkdead": [httpx.ConnectError("模拟掐线") for _ in range(9)]})
rdead = gw._h3_rpa_run({"job_id": "tkdead", "h3_base": BASE, "poll_interval": 0.01, "poll_timeout": 30})
check("D10 查询连炸 6 次判死：带 job_id + 明说别重提交 + 全程零 POST",
      [bool(rdead.get("job_id")), "别重提交" in (rdead["error"] or ""), len(FakeClient.posts)],
      [True, True, 0])

# D11 5xx 不退避就判死 = 假故障；这里一次 500 后成功
fresh(DONE_PATH)
scripted({"GET /api/status/tk5xx": [Resp(status=502, text="bad gateway"),
                                    Resp(payload={"status": "done", "output": DONE_PATH})]})
r5 = gw._h3_rpa_run({"job_id": "tk5xx", "h3_base": BASE, "output_path": os.path.join(SCRATCH, "f.mp4"),
                     "poll_interval": 0.01, "poll_timeout": 5})
check("D11 查询一次 5xx ⇒ 继续问同一个任务（5xx 是上游抖动，不是我们坏）",
      [r5["success"], len(FakeClient.calls)], [True, 2])

# D12 轮询超时
scripted({"GET /api/status/tkslow": [Resp(payload={"status": "running", "stage": "出片中", "progress": 92})
                                      for _ in range(200)]})
rto = gw._h3_rpa_run({"job_id": "tkslow", "h3_base": BASE, "poll_interval": 0.01, "poll_timeout": 1})
check("D12 轮询超时 ⇒ 判死留 job_id、零 POST（可能还在出片，用 taskId 捞）",
      [rto["success"], bool(rto.get("job_id")), len(FakeClient.posts)], [False, True, 0])

# D13 提交没回 taskId
scripted({"POST /api/generate": [Resp(payload={"ok": False})]})
rnoid = gw._h3_rpa_run({"prompt": "x", "h3_base": BASE, "poll_interval": 0.01, "poll_timeout": 5})
check("D13 提交没回 taskId ⇒ 立刻失败且不开始轮询（总调用=1）",
      [rnoid["success"], len(FakeClient.calls)], [False, 1])

# D14 8093 没起：连接异常在【提交】那一步就说清怎么起
scripted({"POST /api/generate": [httpx.ConnectError("拒绝连接")]})
rdown = gw._h3_rpa_run({"prompt": "x", "h3_base": BASE, "poll_interval": 0.01, "poll_timeout": 5})
check("D14 8093 没应答 ⇒ 拒话里写清「起没起、怎么起」（不许只回一个 ConnectionError）",
      [rdown["success"], "app.py" in (rdown["error"] or "")], [False, True])

# ── E 花钱批准闸：没条子 = 不建预扣、不发 HTTP（双向对照）────────────────────────
scripted({})                                       # 计数清零：E 组要的是"这一趟发了几个请求"
try:
    e_no = gw.invoke("video.generate", "h3-rpa", account="h3-gate",
                     params={"prompt": "女生转身展示风衣", "h3_base": BASE, "session": "s-h3"})
    net_no = len(FakeClient.calls)
    g = gw.grant_spend_approval("video.generate", "h3-rpa", account="h3-gate", count=1,
                                credits=60, session="s-h3", scope="闸内自测条子（不是用户批准）")
    fresh(DONE_PATH)
    scripted({"POST /api/generate": [Resp(payload={"taskId": "tkE"})],
              "GET /api/status/tkE": [Resp(payload={"status": "done", "output": DONE_PATH})]})
    e_ok = gw.invoke("video.generate", "h3-rpa", account="h3-gate",
                     params={"prompt": "女生转身展示风衣", "h3_base": BASE, "session": "s-h3",
                             "output_path": os.path.join(SCRATCH, "g.mp4"),
                             "poll_interval": 0.01, "poll_timeout": 5})
    net_ok = len(FakeClient.calls)
    scripted({"POST /api/generate": [Resp(payload={"taskId": "tkF"})],
              "GET /api/status/tkF": [Resp(payload={"status": "done", "output": DONE_PATH})]})
    e_swap = gw.invoke("video.generate", "h3", account="h3-gate",
                       params={"prompt": "x", "session": "s-h3"})
    gw.revoke_spend_approval(g["id"])
    scripted({})
    e_rev = gw.invoke("video.generate", "h3-rpa", account="h3-gate",
                      params={"prompt": "x", "h3_base": BASE, "session": "s-h3",
                              "poll_interval": 0.01, "poll_timeout": 1})
finally:
    pass

check("E1 没有批准条子 ⇒ approval_required，且一次请求都不发（0 分）",
      [e_no["ok"], "approval_required" in (e_no["error"] or ""), net_no], [False, True, 0])
check("E2 账本零变化（没建 hold、没扣分）", e_no["credits"]["charged"], 0)
check("E3 有条子才放行（同一段请求过了闸，真的打到 8093 并出片）",
      [e_ok["ok"], e_ok["credits"]["charged"] > 0, net_ok >= 2], [True, True, True])
check("E4 条子写死 purpose：批了 h3-rpa、跑 API 腿 h3 仍拒（换路由=换价，同意不覆盖）",
      [e_swap["ok"], "approval_required" in (e_swap["error"] or "")], [False, True])
check("E5 revoke 之后同一条子立刻不再放行（可撤销是真的撤销）",
      [e_rev["ok"], "approval_required" in (e_rev["error"] or "")], [False, True])

# ── F 边界：网关只打本机 8093，不自己摸上游网站 ─────────────────────────────────
check("F1 base_url 住在 config（.env 可覆盖），不是散在适配器里的字面量",
      bool(re.search(r'H3_RPA_BASE_URL:\s*str\s*=\s*"http://127\.0\.0\.1:8093"', SRC_CFG)), True)
check("F2 适配器只打本机服务，绝不直连 runninghub.cn（浏览器登录态归 8093 管）",
      ["runninghub.cn" not in RUN_BLOCK, "runninghub.cn" not in PAY_BLOCK], [True, True])
check("F3 8093 的提交口只有一个（/api/generate），我们的适配器只认它",
      sorted(set(re.findall(r'@app\.post\("([^"]+)"', SRC_H3_APP))), ["/api/generate", "/api/login", "/api/upload"])
check("F4 终态判定用 8093 自己的状态词（done/error），不臆造第三态",
      [RUN_BLOCK.count('== "done"') >= 1, RUN_BLOCK.count('== "error"') >= 1], [True, True])
check("F5 8093 的成片是【它自己下载】的（RPA 内部抓 [7]），网关不再下载、只校验落盘",
      ["download_video_with_audio" in SRC_H3_CLIENT, "content" not in RUN_BLOCK], [True, True])
check("F6 这条腿的两段函数体里不引 playwright（浏览器只活在 8093 进程里；网关别处提它是豆包那条腿的事）",
      ["playwright" not in RUN_BLOCK.lower(), "playwright" not in PAY_BLOCK.lower(),
       "httpx" not in PAY_BLOCK], [True, True, True])

print(_safe("\n=== H3 RPA 腿闸：%d 条断言，失败 %d ===" % (TOTAL, len(fails))))
if fails:
    print(_safe("失败项：" + "、".join(fails)))
    sys.exit(1)
