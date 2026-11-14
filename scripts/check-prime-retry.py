#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""闸：wan3.0 出片腿（`video.generate/wan3-prime`）的防双花不变量。

🔴 2026-10-30 起这个文件里有【两组】，钉的是两条不同的腿，别看错对象：
  · R1–R10 打的是 `_tianyue_video_run`（api.tianyue.xyz，按次、没有对账口）—— 它今天只剩
    `video.generate/wan30-studio` 这一条身份在用。
  · Y1–Y11 打的是 `_yz30_video_run`（youzan666 Prime，按秒、有 Idempotency-Key、素材走 /v1/assets）——
    这才是 `video.generate/wan3-prime` 现在真正走的那条。
  为什么两组都留着：这条闸的名字与用途写着"wan3-prime"，而 10-30 换芯后 wan3-prime 换了适配器；
  不补 Y 组 = 名字守 A、身体守 B 的假保险（记忆里"断言为错的理由通过"那一类）。

2026-10-16 换芯重写：原来这条钉的是 yz-prime 那条【带对账口的 4 次重试环】（"失败不计次"是它平台的性质）。
用户定"新的api，只接着一个wan3.0到网关上，之前的wan3.0 满血，删除" ⇒ yz-prime 连适配器一起删了，
而新腿（api.tianyue.xyz）**没有对账口**（/v1/balance、GET /v1/videos 都 404）⇒ 结果未知时重投就是双花，
所以新腿是【单次尝试、只捞不重投】。闸跟着对象走，钉的是换了谁都在的那几条：

  1. 号落盘：一拿到任务号就写 wan3_job.json（成片旁边）；号不落盘，进程一死只能重投 = 双花
  2. 带 job_id = 只捞：一个 POST 都不发（只 GET）；带 task_id 不算（会当新提交 ⇒ 必须被 prompt 判据拒）
  3. 结果未知不重发：POST 没回话 / 回 200 没号 ⇒ submit_unknown 且【只发了一次 POST】
  4. 参数在本地就拒（本地路径素材 / 首尾帧 / 非 1080P / duration<=0）⇒ 0 个 POST
  5. 提交被拒（403 quota 等）⇒ 原文带出、不重试
  6. 落盘纪律：<1KB 判失败；同名不静默覆盖（旧的留 .prev-<sha8>）
  7. 跨域重定向剥凭据（Authorization 不跟到别的主机）
  8. 调用方自报 model 与路由不一致 ⇒ 拒（"mini→h3 马甲"那条教训）
  9. （Y 组新增）幂等编号由参数派生且真带上；素材 kind 回执不一致要停在建单之前；
     "到手"= ffprobe 读得出【视频流】，不是 completed、也不是 200。

只换【传输层】：`_wan30_http` / `_yz30_http` 换成脚本化假响应，外加下载与 ffprobe 两处打桩。
不打网络、不花钱、不落仓库（输出写临时目录）。
"""
import io
import json
import os
import shutil
import sys
import tempfile

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, WS)
sys.path.insert(0, BACKEND)
os.environ.setdefault("TIANYUE_API_KEY", "gate-selftest-not-a-real-key")

PASS, FAIL = [], []


def check(name, got, want):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print("%-4s %-56s got=%r want=%r" % ("PASS" if ok else "FAIL", name, got, want))


def _safe(s):
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        s.encode(enc)
        return s
    except (UnicodeEncodeError, LookupError):
        return s.encode(enc, "replace").decode(enc, "replace")


import backend.gateway as gw                                          # noqa: E402

REAL_HTTP = gw._wan30_http
TMP = tempfile.mkdtemp(prefix="wan30-gate-")


def run_once(script, params=None, model="ME-wan30-1080p-1055"):
    """跑一次真 _tianyue_video_run，传输层按 script 应答。返回 (结果, POST 次数, GET 次数, 看到的请求)。"""
    calls = []

    def fake(method, url, headers=None, body=None, timeout=60.0):
        calls.append({"method": method, "url": url, "headers": dict(headers or {}),
                      "body": (body or b"")[:200]})
        return script(method, url, headers or {}, body)

    gw._wan30_http = fake
    try:
        p = dict(params or {})
        if "output_path" not in p:
            p["output_path"] = os.path.join(TMP, "out.mp4")
        r = gw._tianyue_video_run(p, {"model": model, "base_url": "https://api.tianyue.xyz",
                                      "api_key": "gate-key"})
    finally:
        gw._wan30_http = REAL_HTTP
    posts = [c for c in calls if c["method"] == "POST"]
    gets = [c for c in calls if c["method"] == "GET"]
    return r, len(posts), len(gets), calls


def _ok_submit(method, url, headers, body):
    if method == "POST":
        return 200, {}, json.dumps({"id": "T-1", "task_id": "T-1", "status": "in_progress"}).encode()
    return 200, {}, json.dumps({"id": "T-1", "status": "completed", "url": "https://cdn/x.mp4"}).encode()


# ---- 2/3：只捞不重投 & 判据字段 ----
r, posts, gets, _ = run_once(lambda m, u, h, b: (200, {}, json.dumps(
    {"id": "J-9", "status": "in_progress"}).encode()),
    {"job_id": "J-9"})
check("R2 带 job_id = 只捞：0 个 POST、只 GET", [r.get("success"), posts, gets], [False, 0, 1])
r, posts, _, _ = run_once(_ok_submit, {"task_id": "T-1", "duration": 5})
check("R3 判据字段是 job_id 不是 task_id：只给 task_id ⇒ 被 prompt 判据拒（不会静默当新提交发出去）",
      [posts, "prompt" in str(r.get("error"))], [0, True])

# ---- 4：参数本地拒（0 请求）----
r, posts, _, _ = run_once(_ok_submit, {"prompt": "p", "duration": 5, "image_urls": ["C:/local.png"]})
check("R4 本地路径素材 ⇒ 本地拒、0 个 POST（平台抓不到的 URL 不许发出去碰运气）",
      [posts, "外链" in str(r.get("error"))], [0, True])
r, posts, _, _ = run_once(_ok_submit, {"prompt": "p", "duration": 5, "resolution": "480P"})
check("R4b 非 1080P ⇒ 本地拒（model 写死 1080p，别静默改档）", [posts, "1080P" in str(r.get("error"))], [0, True])

# ---- 3：结果未知不重发（POST 只发一次）----
def _no_reply(method, url, headers, body):
    return None, {}, b"socket timeout"


r, posts, _, _ = run_once(_no_reply, {"prompt": "p", "duration": 5})
check("R5 POST 没回话 ⇒ submit_unknown 且只发了 1 次 POST（结果未知重投=双花）",
      [r.get("kind"), posts, "别直接重发" in str(r.get("error"))], ["submit_unknown", 1, True])
r, posts, _, _ = run_once(lambda m, u, h, b: (200, {}, b"{}"), {"prompt": "p", "duration": 5})
check("R5b 回 200 但没号 ⇒ submit_unknown、1 次 POST", [r.get("kind"), posts], ["submit_unknown", 1])

# ---- 1：号落盘 ----
out = os.path.join(TMP, "seg", "seg1.mp4")
os.makedirs(os.path.dirname(out), exist_ok=True)
r, posts, _, _ = run_once(_ok_submit, {"prompt": "p", "duration": 5, "output_path": out})
job_file = os.path.join(TMP, "seg", "wan3_job.json")
jid = ""
if os.path.isfile(job_file):
    jid = (json.load(io.open(job_file, encoding="utf-8")) or {}).get("task_id") or ""
check("R6 提交一拿到号就落盘 wan3_job.json（成片旁边），里面是真任务号",
      [posts, jid], [1, "T-1"])

# ---- 5：被拒带原文、不重试 ----
r, posts, _, _ = run_once(lambda m, u, h, b: (403, {}, json.dumps(
    {"code": "insufficient_user_quota", "message": "用户额度不足, 剩余额度: ¥0.000000"}).encode()),
    {"prompt": "p", "duration": 5})
check("R7 提交被拒 ⇒ 原文带出（含 quota 字样）且 1 次 POST（不拿用户的额度赌重试）",
      [r.get("success"), posts, "insufficient_user_quota" in str(r.get("error"))], [False, 1, True])

# ---- 6：落盘纪律（直测下载器：这两条纪律长在 _wan30_download 身上）----
def run_dl(script, url, dest, base="https://api.tianyue.xyz"):
    def fake(method, u, headers=None, body=None, timeout=60.0):
        return script(method, u, headers or {}, body)
    gw._wan30_http = fake
    try:
        return gw._wan30_download(url, dest, base, "gate-key")
    finally:
        gw._wan30_http = REAL_HTTP


tiny = os.path.join(TMP, "tiny.mp4")
path, n, err = run_dl(lambda m, u, h, b: (200, {}, b"tiny"), "https://cdn/x.mp4", tiny)
check("R8 <1KB 不算交付（回 200 但只有一丁点字节 ⇒ 判失败）", [path, "字节" in err], [None, True])

good = os.path.join(TMP, "good.mp4")
with io.open(good, "wb") as f:
    f.write(b"A" * 4096)
_sha_old = __import__("hashlib").sha256(b"A" * 4096).hexdigest()[:8]
path, n, err = run_dl(lambda m, u, h, b: (200, {}, b"B" * 8192), "https://cdn/x.mp4", good)
check("R8b 同名不静默覆盖：旧的留 .prev-<sha8>，新片落原名",
      [os.path.isfile(good + ".prev-" + _sha_old), io.open(good, "rb").read()[:1] == b"B", path],
      [True, True, good])

# ---- 7：跨域重定向剥凭据（真 _wan30_http + 本机两个 socket，不打外网）----
import threading                                                          # noqa: E402
from http.server import BaseHTTPRequestHandler, HTTPServer                 # noqa: E402

_seen = {}


class _B(BaseHTTPRequestHandler):
    def do_GET(self):
        _seen["b"] = self.headers.get("Authorization")
        self.send_response(200)
        self.send_header("Content-Length", "4")
        self.end_headers()
        self.wfile.write(b"done")

    def log_message(self, *a):
        pass


class _A(BaseHTTPRequestHandler):
    def do_GET(self):
        _seen["a"] = self.headers.get("Authorization")
        self.send_response(302)
        self.send_header("Location", "http://127.0.0.1:%d/x" % _B_PORT)
        self.end_headers()

    def log_message(self, *a):
        pass


_hb = HTTPServer(("127.0.0.1", 0), _B)
_B_PORT = _hb.server_address[1]
_ha = HTTPServer(("127.0.0.1", 0), _A)
for _h in (_ha, _hb):
    threading.Thread(target=_h.serve_forever, daemon=True).start()
try:
    st, _, raw = REAL_HTTP("GET", "http://127.0.0.1:%d/redirect" % _ha.server_address[1],
                           headers={"Authorization": "Bearer gate-key", "User-Agent": "Mozilla/5.0"},
                           timeout=20.0)
    check("R9 跨主机重定向不递凭据：A 收到 Authorization、B 那边必须是空",
          [st, raw, _seen.get("a") is not None, _seen.get("b")], [200, b"done", True, None])
finally:
    _ha.shutdown()
    _hb.shutdown()
cross = REAL_HTTP.__globals__["_wan30_http"] if False else None

# ---- 8：model 马甲防 ----
r, posts, _, _ = run_once(_ok_submit, {"prompt": "p", "duration": 5}, model="ME-wan30-1080p-1055")
r2, posts2, _, _ = run_once(_ok_submit, {"prompt": "p", "duration": 5, "model": "别的模型"})
# 🔴 这一条原来钉的是拒因里的字面『只出』——那是 `_tianyue_video_once` 那一处的文案，而 `.env` 只配一条模型后
#    拒绝发生在更外一层（`_tianyue_video_run` 的"只认"）。钉字面 = 把当年那处文案当规则，一改文案就假红。
#    钉实质：0 次 POST + 失败 + 拒因点名【被拒的那个模型名】（两处拒因都满足，换措辞也不假红）。
check("R10 调用方自报 model 与路由不一致 ⇒ 拒（0 POST、拒因点名那条被拒的名字，绝不悄悄按别的模型跑）",
      [posts, posts2, "别的模型" in str(r2.get("error")), bool(r2.get("success") is False)],
      [1, 0, True, True])

# ═══════════════════════════════════════════════════════════════════════════
# Y 组：2026-10-30 换芯后的【新那条】出片腿 —— `video.generate/wan3-prime` = youzan666 Prime
#
# 🔴 为什么必须补这一组：上面 R1–R10 钉的是 `_tianyue_video_run`（它今天降级成只服务 wan30-studio）。
#    本闸的文件名与文档串都写着"wan3-prime 的防双花不变量"，而 wan3-prime 现在走 `_yz30_video_run`
#    ⇒ 不加这一组，这个闸就成了"名字守着 A、身体守着 B"的假保险（记忆里那条"断言为错的理由通过"）。
#    换芯真正换来的三样东西也要被钉住：① 平台侧幂等（key 必须由参数派生、稳定）
#    ② 素材可以传它自己的 /v1/assets（回执 kind 不一致要停）③ "到手"= ffprobe 读得出【视频流】。
# ═══════════════════════════════════════════════════════════════════════════
REAL_YZ = gw._yz30_http
REAL_DL = gw._wan30_download
REAL_PROBE = gw.yz30_has_video_stream
YZ_ROUTE = {"model": "wan3.0-video-prime", "base_url": "https://youzan666.vip",
            "api_key": "gate-key", "purpose": "wan3-prime"}


def run_yz(script, params=None, route=None, probe_ok=True, model="wan3.0-video-prime"):
    """跑一次真 `_yz30_video_run`：传输层、下载、ffprobe 三处打桩 ⇒ 0 网络、0 花钱。"""
    calls = []

    def fake(method, url, headers=None, body=None, timeout=60.0):
        # Y2b 要回读 body 里的真参数 ⇒ 这里不截断（截到 260 字节会把 media 数组切掉，json.loads 直接炸）
        calls.append({"method": method, "url": url, "headers": dict(headers or {}),
                      "body": (body or b"")})
        return script(method, url, headers or {}, body)

    def fake_dl(url, dest, base_url, api_key):
        with open(dest, "wb") as f:
            f.write(b"0" * 4096)
        return dest, 4096, ""

    gw._yz30_http = fake
    gw._wan30_download = fake_dl
    gw.yz30_has_video_stream = lambda p: ((True, "720x1280 h264 stub") if probe_ok
                                          else (False, "文件里没有视频流（stub）"))
    try:
        p = dict(params or {})
        p.setdefault("output_path", os.path.join(TMP, "yz-out-%d.mp4" % len(calls)))
        r = gw._yz30_video_run(p, dict(route or YZ_ROUTE, model=model))
    finally:
        gw._yz30_http = REAL_YZ
        gw._wan30_download = REAL_DL
        gw.yz30_has_video_stream = REAL_PROBE
    posts = [c for c in calls if c["method"] == "POST"]
    gets = [c for c in calls if c["method"] == "GET"]
    tasks_posts = [c for c in posts if "/v1/tasks" in c["url"] and "refresh" not in c["url"]]
    return r, calls, posts, gets, tasks_posts


def _yz_ok_script(method, url, headers, body):
    if method == "POST" and url.endswith("/v1/tasks"):
        return 200, {}, json.dumps({"ok": True, "task": {"id": "YZ-1", "status": "processing"},
                                    "replayed": False}).encode()
    return 200, {}, json.dumps({"ok": True, "task": {"id": "YZ-1", "status": "succeeded",
                                                    "videoUrl": "https://cdn/yz.mp4",
                                                    "quotedAmount": 3.0}}).encode()


BASE_SUBMIT = {"prompt": "把这条复刻一遍", "duration": 12, "resolution": "1080P",
               "ratio": "9:16", "video_urls": ["https://cdn/ref.mp4"],
               "image_urls": ["https://cdn/p1.png"]}

# Y1 只捞不重投
r, calls, posts, gets, tp = run_yz(lambda m, u, h, b: (200, {}, json.dumps(
    {"ok": True, "task": {"id": "YZ-9", "status": "processing"}}).encode()), {"job_id": "YZ-9"})
check("Y1 带 job_id = 只捞：一个 POST 都不发（含 refresh 也不算提交）",
      [r.get("success"), len(posts), len(gets), "YZ-9" in str(r.get("error"))], [False, 0, 1, True])

# Y2 幂等编号由参数派生：同参数恒定、改内容就变、字符集合法（平台的硬要求）
import re as _re_y2
k1 = gw._yz30_idem_key(BASE_SUBMIT)
k2 = gw._yz30_idem_key(dict(BASE_SUBMIT))
k3 = gw._yz30_idem_key(dict(BASE_SUBMIT, prompt="换了正文"))
check("Y2 幂等 key：同参数两次算出同一个、改 prompt 就变、格式落在 8—100 位 [A-Za-z0-9_-]",
      [k1 == k2, k1 != k3, bool(_re_y2.fullmatch(r"[A-Za-z0-9_-]{8,100}", k1))], [True, True, True])
r, calls, posts, gets, tp = run_yz(_yz_ok_script, dict(BASE_SUBMIT))
_hdr = (tp[0]["headers"] if tp else {})
# 🔴 期望值要按【适配器真正拿到的那份参数】算：idem key 里含 output_path，而 run_yz 会给每次跑
#    分配一个不同的落点名 ⇒ 拿 BASE_SUBMIT 重算必然不等（第一版就是这么假红的）。这里回读请求里
#    真正的参数，比的是"同一次调用内两处算出来是否一致"，不是"我猜的 key 对不对"。
_body = json.loads((tp[0]["body"] or b"{}").decode("utf-8", "replace")) if tp else {}
_same = (_body.get("prompt") == BASE_SUBMIT["prompt"] and _body.get("duration") == BASE_SUBMIT["duration"]
         and _body.get("resolution") == BASE_SUBMIT["resolution"])
check("Y2b 提交时【真的带上了】Idempotency-Key，且 body 里的模型/秒数/分辨率就是路由与参数给的那一份"
      "（不是算了没用、也不是悄悄换了参数）",
      [len(tp), bool(_hdr.get("Idempotency-Key")), _same, _body.get("model")],
      [1, True, True, "wan3.0-video-prime"])

# Y3 模型固定一条（"不许失败换档"落到码上）
r, calls, posts, gets, tp = run_yz(_yz_ok_script, dict(BASE_SUBMIT), model="别的家的模型")
check("Y3 route 解出的模型不是 wan3.0-video-prime ⇒ 0 请求、拒因点名那条名字",
      [len(calls), r.get("kind"), "别的家的模型" in str(r.get("error"))], [0, "submit_param", True])

# Y4/Y5 参数在本地就拒（这家按秒计费、media 有硬上限）
r, calls, posts, gets, tp = run_yz(_yz_ok_script, dict(BASE_SUBMIT, duration=31))
check("Y4 duration=31 超出卡片 2—30 ⇒ 本地拒、0 请求（按秒计费的腿不能把秒数送飞）",
      [len(calls), "2—30" in str(r.get("error"))], [0, True])
r, calls, posts, gets, tp = run_yz(_yz_ok_script, dict(BASE_SUBMIT, image_urls=["https://x/%d.png" % i for i in range(11)]))
check("Y5 参考图 11 张 > 上限 10 ⇒ 本地拒、0 请求",
      [len(calls), "reference_image" in str(r.get("error"))], [0, True])
r, calls, posts, gets, tp = run_yz(_yz_ok_script, {"prompt": "p", "duration": 5, "output_path": "x.mp4"})
check("Y5b 一条素材都不给 ⇒ 本地拒（全能参考腿没素材=让模型凭空编产品）",
      [len(calls), "参考素材" in str(r.get("error"))], [0, True])

# Y6 素材上传回执的 kind 与要引用的不一致 ⇒ 停在建单之前
_local = os.path.join(TMP, "ref.mp4")
with open(_local, "wb") as f:
    f.write(b"0" * 2048)


def _yz_kind_mismatch(method, url, headers, body):
    if "/v1/assets" in url:
        return 200, {}, json.dumps({"ok": True, "asset": {"kind": "reference_image", "url": "https://a/1.png"},
                                    "url": "https://a/1.png"}).encode()
    return 200, {}, json.dumps({"ok": True, "task": {"id": "YZ-1"}}).encode()


r, calls, posts, gets, tp = run_yz(_yz_kind_mismatch, dict(BASE_SUBMIT, video_urls=[_local]))
check("Y6 上传回执 kind ≠ 要引用的 kind ⇒ 当场停，一个 /v1/tasks 都不发（文档：页面会核对实际类型）",
      [len(tp), "不一致" in str(r.get("error"))], [0, True])

# Y7 提交没回话 = 结果未知：只发一次，且不拿幂等当"随便重发"的许可证
def _yz_silent(method, url, headers, body):
    return None, {}, b"socket timeout"


r, calls, posts, gets, tp = run_yz(_yz_silent, dict(BASE_SUBMIT))
check("Y7 提交没回话 ⇒ submit_unknown、只 1 次建单 POST，拒因同时点名『先盘上 wan3_job.json』",
      [r.get("kind"), len(tp), "wan3_job.json" in str(r.get("error"))], ["submit_unknown", 1, True])

# Y8 号当场落盘，而且 provider 必须是这一家（换芯时最容易留错的那一格）
r, calls, posts, gets, tp = run_yz(_yz_ok_script, dict(BASE_SUBMIT))
_jf = os.path.join(os.path.dirname(os.path.abspath(
    (r.get("path") or os.path.join(TMP, "none.mp4")))), "wan3_job.json")
_j = json.loads(io.open(_jf, encoding="utf-8").read()) if os.path.isfile(_jf) else {}
check("Y8 一拿到号就落盘 wan3_job.json，里面 provider=yz30、task_id 是真号（不许写着上一家）",
      [bool(_j), _j.get("provider"), _j.get("task_id")], [True, "yz30", "YZ-1"])

# Y9/Y11 "到手"= ffprobe 读得出视频流（completed 不等于有片，2026-10-21 那条 457 字节的教训）
r, calls, posts, gets, tp = run_yz(_yz_ok_script, dict(BASE_SUBMIT), probe_ok=False)
check("Y9 上游 succeeded、文件也落了盘，但【读不出视频流】⇒ 不算到手、success=False、号还带着可捞",
      [r.get("success"), r.get("kind"), bool(r.get("job_id"))], [False, "stop", True])
r, calls, posts, gets, tp = run_yz(_yz_ok_script, dict(BASE_SUBMIT), probe_ok=True)
check("Y11 同一份桩、只把视频流判据换成【能读出】⇒ 必须成功（证明 Y9 红的是判据，不是别的步骤）",
      [r.get("success"), r.get("billing_unit"), r.get("quoted_amount")], [True, "second", 3.0])

shutil.rmtree(TMP, ignore_errors=True)
print(_safe("\n=== wan3.0 出片腿防双花闸：%d 通过 / %d 失败 ===" % (len(PASS), len(FAIL))))
for f in FAIL:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)
