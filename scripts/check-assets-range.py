"""8100 静态口 Range/206 回归：视频能不能拖进度条取决于这里，不取决于播放器。

背景（2026-10-03 实测）：`_send_file` 原本无脑 200 全量、且整个服务跑 HTTP/1.0
⇒ 带 `Range: bytes=0-1023` 仍回 200 + 全量字节，浏览器只能整段下、拖不动。
改成 HTTP/1.1 + 206/416 后必须有人钉住，否则下次动响应头就退回去。

跑法：PYTHONIOENCODING=utf-8 python scripts/check-assets-range.py
全程本机 127.0.0.1 的一次性端口，不碰常驻 8100、不碰账本。
"""
import http.client
import json
import os
import sys
import threading
from http.server import ThreadingHTTPServer

os.environ.setdefault("HYPIT_JOBS_DB", os.path.join(WS, r"_scratch_b\assets_range_selftest.sqlite"))
WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, WS)
sys.path.insert(0, BACKEND)
sys.path.insert(0, WS)
sys.path.insert(0, BACKEND)

import model_admin as ma          # noqa: E402

DATA = bytes(range(256)) * 2 + b"tail"      # 516 字节，够测边界又不用真视频
TOTAL = len(DATA)


class ServeFixed(ma.H):                      # 只测两条写口（_send_file / _send），路由那层不掺进来
    def do_GET(self):
        if self.path == "/empty":
            return self._send_file(b"", "video/mp4")
        if self.path == "/j":                # 切 1.1 最容易伤到的就是 JSON 口：右栏每 5 秒轮询一次
            return self._send(404, json.dumps({"error": "no such path", "msg": "中文也要完好"}, ensure_ascii=False))
        self._send_file(DATA, "video/mp4")

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), ServeFixed)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

fails, total = [], [0]


def get(rng=None, conn=None, path="/a.mp4"):
    own = conn is None
    c = conn or http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        c.request("GET", path, headers=({"Range": rng} if rng else {}))
        r = c.getresponse()
        body = r.read()
        return r, dict(r.getheaders()), body
    finally:
        if own:
            c.close()


def check(name, got, want):
    total[0] += 1
    ok = got == want
    try:
        shown = json.dumps(got, ensure_ascii=False)[:150]
    except TypeError:                       # 断言值里有二进制时别把测试自己搞崩
        shown = repr(got)[:150]
    print(("PASS " if ok else "FAIL ") + name, "=>", shown)
    if not ok:
        fails.append((name, got, want))


# ① 不带 Range：200 全量 + 声明支持 Range
r, h, b = get()
check("1 无 Range → 200 全量", [r.status, len(b)], [200, TOTAL])
check("1b  advertise Accept-Ranges（浏览器据此才发 Range 请求）", h.get("Accept-Ranges"), "bytes")
check("1c 协议升到 HTTP/1.1（206 是 1.1 特性）", r.version, 11)

# ② 中段/前段/尾段
for spec, s, e in [("bytes=0-99", 0, 99), ("bytes=100-199", 100, 199), ("bytes=-50", TOTAL - 50, TOTAL - 1)]:
    r, h, b = get(spec)
    check("2 %s → 206 字节逐字节相等" % spec,
          [r.status, h.get("Content-Range"), b == DATA[s:e + 1], len(b)],
          [206, "bytes %d-%d/%d" % (s, e, TOTAL), True, e - s + 1])

# ③ 开区间与越界钳制
r, h, b = get("bytes=%d-" % (TOTAL - 20))
check("3 bytes=A-（到末尾）", [r.status, len(b), h.get("Content-Range")], [206, 20, "bytes %d-%d/%d" % (TOTAL - 20, TOTAL - 1, TOTAL)])
r, h, b = get("bytes=250-99999")
check("3b 尾越界按实际长度钳制", [r.status, len(b), h.get("Content-Range")], [206, TOTAL - 250, "bytes 250-%d/%d" % (TOTAL - 1, TOTAL)])

# ④ 起点越界必须 416，不许骗人回 200 全量
r, h, b = get("bytes=%d-" % TOTAL)
check("4 起点越界 → 416 + Content-Range */总长", [r.status, h.get("Content-Range"), len(b)], [416, "bytes */%d" % TOTAL, 0])

# ⑤ 语法不通的 Range：宽松忽略（RFC 允许），但不能崩、不能返错切片
r, h, b = get("bytes=abc-xyz")
check("5 坏 Range 忽略并回 200 全量（不 500）", [r.status, len(b)], [200, TOTAL])

# ⑥ 空文件不能因为 end=-1 触发 416
r, h, b = get(None, path="/empty")
check("6 空文件 → 200 零长度", [r.status, len(b)], [200, 0])

# ⑦ 🔴 HTTP/1.1 keep-alive 的代价：只要有一条响应漏 Content-Length，同一条连接就会挂住
conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
seq = []
for spec in ["bytes=0-9", None, "bytes=20-29", "bytes=%d-" % TOTAL]:
    r, h, b = get(spec, conn=conn)
    seq.append((r.status, len(b)))
conn.close()
check("7 同一条连接连发 4 次都对（切 1.1 后没漏 Content-Length）", seq, [(206, 10), (200, TOTAL), (206, 10), (416, 0)])

# ⑧ JSON 写在 1.1 下不能漏 Content-Length，否则右栏 presence 轮询会挂
conn2 = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
r, h, b = get(None, conn=conn2, path="/j")
r2, h2, b2 = get(None, conn=conn2, path="/j")        # 同连接再发一次，漏长度就卡死在这一步
conn2.close()
check("8 JSON 口：404 + 正文可解析 + 中文没坏", [r.status, json.loads(b.decode("utf-8")).get("msg"), h.get("Content-Type")],
      [404, "中文也要完好", "application/json; charset=utf-8"])
check("8b 同一条连接连打两次 JSON 都完整（Content-Length 没漏）", [r2.status, b2 == b], [404, True])

srv.shutdown()

# ══ 9 看板文件直链（/api/film/file）：交付三件套里的"可点直链"就走这条路 ════════════
# 2026-10-06 实测踩到的：扩展名查表拿 ".mp4" 去查键为 "mp4" 的表 ⇒ 永远查不中 ⇒
# 所有直链都回 application/octet-stream ⇒ 浏览器把视频当附件下载、图片也不内联显示。
# 🔴 判据必须落在【路由层】而不是 _send/_send_file（上面已钉死那两个）：查表中不中时它们照样回 200，
#    只看状态码会全绿漏网 —— 这里钉的就是 Content-Type 本身。
import shutil
import tempfile

PROD = tempfile.mkdtemp(prefix="film-file-gate-")
os.makedirs(os.path.join(PROD, "t1", "outputs"), exist_ok=True)
with open(os.path.join(PROD, "t1", "outputs", "clip.mp4"), "wb") as f:
    f.write(b"\x00\x00\x00\x18ftypmp42" + DATA)
with open(os.path.join(PROD, "t1", "outputs", "frame.jpg"), "wb") as f:
    f.write(b"\xff\xd8\xff" + DATA)
os.environ["DSH_PROD_ROOT"] = PROD        # 处理函数每次请求都现读它，不污染真 productions


class ServeRoutes(ma.H):                  # 直接挂真路由类，不在闸里重抄一遍逻辑
    def log_message(self, *a):
        pass


srv2 = ThreadingHTTPServer(("127.0.0.1", 0), ServeRoutes)
port2 = srv2.server_address[1]
threading.Thread(target=srv2.serve_forever, daemon=True).start()


def film(rel, rng=None):
    c = http.client.HTTPConnection("127.0.0.1", port2, timeout=5)
    try:
        c.request("GET", "/api/film/file?task=t1&rel=" + rel,
                  headers=({"Range": rng} if rng else {}))
        r = c.getresponse()
        r.read()
        return r.status, dict(r.getheaders()).get("Content-Type", "")
    finally:
        c.close()


# 🔴 期望值 2026-10-09 更正：二进制【不带 charset】才是对的（`image/png; charset=utf-8` 本身就是错的类型，
#    而且 _send_file 的注释与 hypit_bridge 的 content-type 判定都依赖这一点）。
#    旧期望写的是"带 charset"，那是当年那条 bug 的形状被固化进了闸 ⇒ 码修对了、闸反而红。
check("9 mp4 直链回 video/mp4 且【不带 charset】（带了就等于把二进制当文本，hypit collect 判定会脏）",
      film("outputs/clip.mp4")[1], "video/mp4")
check("9b jpg 直链回 image/jpeg 且【不带 charset】（贴图那一件走的是同一张表）",
      film("outputs/frame.jpg")[1], "image/jpeg")
check("9c 两条都回 200（类型修对的同时别把正文弄坏）",
      [film("outputs/clip.mp4")[0], film("outputs/frame.jpg")[0]], [200, 200])
check("9d 路径穿越拿不到东西（rel 带 .. 编码也不许爬到 productions 外面）",
      [film("../secret")[0], film("..%2F..%2Fsecret")[0] in (400, 404)], [404, True])
check("9e 不存在的文件回 404，不是 500", film("outputs/nope.mp4")[0], 404)
SRC_MA = open(os.path.join(BACKEND, r"model_admin.py"), encoding="utf-8", errors="replace").read()
check("9f 复发防护：取值时剥掉点号（表键不带点，两边必须同一口径）",
      SRC_MA.count('[1].lower().lstrip(".")') >= 1, True)
srv2.shutdown()
shutil.rmtree(PROD, ignore_errors=True)
print("\n=== %d 条断言，失败 %d ===" % (total[0], len(fails)))
for n, g, w in fails:
    print("  FAIL", n, "got", g, "want", w)
sys.exit(1 if fails else 0)
