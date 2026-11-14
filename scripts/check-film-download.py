# -*- coding: utf-8 -*-
"""验收 8100 的交付下载口：attachment / Range / 白名单真拦 / 越界拒。跑真服务，不 mock。"""
import glob
import json
import os
import urllib.error
import urllib.parse
import urllib.request
WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")

BASE = "http://127.0.0.1:8100/api/film/file"


def get(task, rel, dl=False, rng=None):
    q = {"task": task, "rel": rel}
    if dl:
        q["dl"] = "1"
    url = BASE + "?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"Range": rng} if rng else {})
    try:
        r = urllib.request.urlopen(req, timeout=30)
        body = r.read()
        return r.status, dict(r.headers), len(body), url
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), 0, url


big = [f for f in glob.glob(os.path.join(WS, r"productions\**\*.mp4"), recursive=True) if os.path.getsize(f) > 1024 * 1024]
print("big mp4 用:", os.path.basename(big[0]) if big else "（没找到 >1MB 的 mp4）")
res = []

st, hd, n, url = get("sock-face-clone", "out_test_5s/trimmed_source.mp4", dl=True)
res.append(("D1 下载腿 200 + attachment 头", (st, n > 0, "attachment" in (hd.get("Content-Disposition") or "")), (200, True, True)))
res.append(("D2 文件名带 ASCII 兜底", "filename=\"" in (hd.get("Content-Disposition") or ""), True))

st2, hd2, n2, _ = get("sock-face-clone", "out_test_5s/trimmed_source.mp4", dl=False)
res.append(("D3 内联腿【不】带 attachment（别把播放变成下载）", (st2, "attachment" in (hd2.get("Content-Disposition") or "")), (200, False)))

if big:
    p = big[0]
    task = os.path.relpath(p, os.path.join(WS, r"productions")).split(os.sep)[0]
    rel = os.path.relpath(p, os.path.join(os.path.join(WS, r"productions"), task)).replace(os.sep, "/")
    size = os.path.getsize(p)
    st4, hd4, n4, _ = get(task, rel, dl=True, rng="bytes=0-1023")
    res.append(("D4 大文件 Range 分段（下载腿也要 206）", (st4, n4, hd4.get("Content-Range", "").startswith("bytes 0-1023/")), (206, 1024, True)))
    st5, _h5, n5, _ = get(task, rel, dl=False, rng="bytes=%d-" % (size - 10))
    res.append(("D5 尾段 Range", (st5, n5), (206, 10)))
    st6, _h6, _n6, _ = get(task, rel, dl=False, rng="bytes=%d-%d" % (size + 100, size + 200))
    res.append(("D6 越界 Range 必须 416 不许谎报 200", st6, 416))

st7, _h7, _n7, _ = get("sock-face-clone", "../../../../Windows/win.ini", dl=True)
res.append(("S1 rel 带 .. 越界 ⇒ 404", st7, 404))
st8, _h8, _n8, _ = get("../../../Windows", "win.ini", dl=True)
res.append(("S2 task 带 .. 越界（以前只查 rel）⇒ 404", st8, 404))
st9, _h9, _n9, _ = get("sock-face-clone", "LOOP.md", dl=True)
res.append(("S3 未知扩展名 ⇒ 404（以前回落 octet-stream 照样发）", st9, 404))
st10, _h10, _n10, _ = get("sock-face-clone", "LOOP.md", dl=False)
res.append(("S4 未知扩展名内联也拒", st10, 404))
st11, _h11, _n11, _ = get("sock-face-clone", "out_test_5s/nope.mp4", dl=True)
res.append(("S5 不存在的文件 ⇒ 404", st11, 404))

# json：内联放行（看板要读），下载拒
jf = glob.glob(os.path.join(WS, r"productions\**\*.json"), recursive=True)
if jf:
    task = os.path.relpath(jf[0], os.path.join(WS, r"productions")).split(os.sep)[0]
    rel = os.path.relpath(jf[0], os.path.join(os.path.join(WS, r"productions"), task)).replace(os.sep, "/")
    a, _ha, na, _ = get(task, rel, dl=False)
    b, _hb, _nb, _ = get(task, rel, dl=True)
    res.append(("S6 json 内联放行（看板数据）", (a, na > 0), (200, True)))
    res.append(("S7 json 下载拒（状态文件里存过明文 key）", b, 404))

# ---- B 组：二进制出口的分流判据（按类型，不按大小）----
big_png = None
small_png = None
for f in glob.glob(os.path.join(WS, r"productions\**\*.png"), recursive=True):
    try:
        sz = os.path.getsize(f)
    except OSError:
        continue
    if sz > 1024 * 1024 and big_png is None:
        big_png = f
    elif 1024 < sz < 800 * 1024 and small_png is None:
        small_png = f


def parts(p):
    task = os.path.relpath(p, os.path.join(WS, r"productions")).split(os.sep)[0]
    rel = os.path.relpath(p, os.path.join(os.path.join(WS, r"productions"), task)).replace(os.sep, "/")
    return task, rel


for tag, p in (("大", big_png), ("小", small_png)):
    if not p:
        print("      （没找到%s的 png，跳过）" % tag)
        continue
    t, rl = parts(p)
    st3, hd3, n3, _ = get(t, rl, dl=False)
    ct = hd3.get("Content-Type") or ""
    res.append(("B %s png 内联：ctype 精确且【不带 charset】" % tag, (st3, ct, "charset" in ct), (200, "image/png", False)))
    res.append(("B %s png 带 Accept-Ranges（二进制出口）" % tag, hd3.get("Accept-Ranges"), "bytes"))
    st4, hd4, n4, _ = get(t, rl, dl=False, rng="bytes=0-99")
    res.append(("B %s png Range 分段 206/100 字节" % tag, (st4, n4), (206, 100)))

src_ma = open(os.path.join(BACKEND, r"model_admin.py"), encoding="utf-8").read()
res.append(("B5 判据是按【类型】分流而不是按大小/只认 mp4",
            'ctype.startswith(("image/", "video/"))' in src_ma, True))
res.append(("B6 变异：退回旧判据（只认 mp4）必须判红",
            'ext == "mp4" and len(data) > 1024 * 1024' in src_ma, False))

bad = 0
for name, got, want in res:
    ok = got == want
    bad += 0 if ok else 1
    print("%-4s %-52s got=%r want=%r" % ("PASS" if ok else "FAIL", name, got, want))
print("\n=== 8100 交付下载口验收：%d/%d 通过 ===" % (len(res) - bad, len(res)))
raise SystemExit(1 if bad else 0)
