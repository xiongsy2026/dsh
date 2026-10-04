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

os.environ.setdefault("HYPIT_JOBS_DB", r"C:\dsh\_scratch_b\assets_range_selftest.sqlite")
sys.path.insert(0, r"D:\video-skill")
sys.path.insert(0, r"D:\video-skill\backend")

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
print("\n=== %d 条断言，失败 %d ===" % (total[0], len(fails)))
for n, g, w in fails:
    print("  FAIL", n, "got", g, "want", w)
sys.exit(1 if fails else 0)
