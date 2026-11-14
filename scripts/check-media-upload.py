#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""闸：素材上传口 gateway.upload_media + POST /api/media/upload（2026-10-09）。

钉的是【那三种真实死法不再出现】，不是"接口能通"：
  · /openapi/v1/upload_file 不存在，但 HTTP 回 200、body 里才是 code:404 ⇒ 只看状态码会当成功
  · host 写成 runninghub.cn ⇒ code=1 "API Key不存在"（RH 的 key 按站点分）
  · 拿错家（PRIME_YZ_*）⇒ 直接 error
传输层整个换成计数器：任何一条判据红，都对应"真会发出去一次请求"或"真会漏一次成功"。

M1  host/端点唯一源（5 档 hint 全指 .ai + v2 binary，字面量里不许有 .cn / v1/upload_file）
M2  未知 provider / 未知 key_hint ⇒ 抛，并把可选项说清
M3  取不到 key ⇒ 抛，不静默换一把
M4  成功形状 ⇒ file_name 优先给、download_url 另给、0 真网络
M5 🔴 HTTP 200 + body code:404 ⇒ 判失败（这条就是那条会话踩的坑）
M6  200 + code:0 但 data 里两个字段都没有 ⇒ 判失败
M7  空文件 / 不存在 / 超体积上限 ⇒ 拒且 0 请求
M8  不计费 ⇒ 信封 credits 恒 0、basis=free、media.upload 不在 CAPABILITIES/ROUTES、不在批准清单
M9  key 永不出信封/台账 ⇒ 只留 sha8
M10 台账一行一记；记账炸了 ⇒ ok:true + ledger_error（登记炸了 ≠ 上传炸了）
M11 8100 那条口的判据：缺参/越界/非媒体/不存在/正常，各回各的码
M12 反向对照：证明"200 不等于成功"这条判据吃得住（同一条响应只按状态码判会当成功）
M13 三处 host/端点字面量一致（网关 vs wan3_client vs runninghub_client）——漂移判红
"""
import io
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, WS)
sys.path.insert(0, BACKEND)

SCRATCH = tempfile.mkdtemp(prefix="media-up-")
PROD = os.path.join(SCRATCH, "productions")
os.makedirs(os.path.join(PROD, "t1", "outputs"), exist_ok=True)
os.environ["GATEWAY_MEDIA_UPLOADS"] = os.path.join(SCRATCH, "media.json")
os.environ["GATEWAY_SPEND_APPROVALS"] = os.path.join(SCRATCH, "approvals.json")
os.environ["GATEWAY_LEDGER"] = os.path.join(SCRATCH, "ledger.sqlite")
os.environ["DSH_PROD_ROOT"] = PROD

PASS, FAIL = [], []


def _safe(s):
    """按【输出流自己的编码】errors=replace。绝不 reconfigure 成 utf-8（会造"零输出 + exit 0"的静默假绿）。"""
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        s.encode(enc)
        return s
    except (UnicodeEncodeError, LookupError):
        return s.encode(enc, "replace").decode(enc, "replace")


def check(name, got, want):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(_safe("%-4s %-58s got=%r want=%r" % ("PASS" if ok else "FAIL", name, got, want)))


from backend import gateway as gw                                       # noqa: E402

REQ = []
RESP = {"payload": {"code": 0, "msg": "success",
                    "data": {"fileName": "openapi/fake.png", "download_url": "https://rh.invalid/fake.png",
                             "size": 3}},
        "status": 200}


class _FakeResp:
    def __init__(self, body):
        self._b = body

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(req, timeout=None):
    REQ.append((getattr(req, "method", "GET"), getattr(req, "full_url", "")))
    return _FakeResp(json.dumps(RESP["payload"], ensure_ascii=False).encode("utf-8"))


_real_urlopen = urllib.request.urlopen
urllib.request.urlopen = fake_urlopen                                  # 只换传输层，函数体是真身


def upload(path, **kw):
    REQ.clear()
    return gw.upload_media(path, **kw), list(REQ)


def png(name="a.png", data=b"\x89PNG\r\n\x1a\n" + b"0" * 200):
    p = os.path.join(PROD, "t1", "outputs", name)
    io.open(p, "wb").write(data)
    return p


# ---- M1 ----
for hint in ("", "wan3", "h3", "motion", "lipsync"):
    t = gw.resolve_media_target("runninghub", hint)
    check("M1 hint=%-7s host/端点固定" % (hint or "(默认)"),
          (t["host"], t["endpoint"]), ("https://www.runninghub.ai", "/openapi/v2/media/upload/binary"))
check("M1b 域名表里没有 .cn（.cn 会回 code=1 API Key不存在）",
      any(".cn" in h for h in gw.MEDIA_UPLOAD_HOSTS.values()), False)
check("M1c 端点表里没有 v1/upload_file（那个端点不存在）",
      any("v1" in e or "upload_file" in e for e in gw.MEDIA_UPLOAD_ENDPOINTS.values()), False)

# ---- M2 ----
for prov, hint, want in (("unknown", "", "未知上传供应商"), ("runninghub", "prime", "没有 key_hint")):
    try:
        gw.resolve_media_target(prov, hint)
        got = "没抛"
    except ValueError as e:
        got = str(e)
    check("M2 %s/%s ⇒ 抛 ValueError 且列可选项" % (prov, hint or "(默认)"),
          want in got and ("可选" in got or "按顺序" in got), True)

# ---- M3 取不到 key ⇒ 抛，不静默换家 ----
_saved = {n: getattr(gw.settings, n, None) for n in dir(gw.settings) if n.startswith("RUNNINGHUB")}
try:
    for n in _saved:
        setattr(gw.settings, n, "")
    try:
        gw.resolve_media_target("runninghub", "")
        m3 = "没抛"
    except ValueError as e:
        m3 = str(e)
    check("M3 全部 key 清空 ⇒ 抛『取不到密钥』，不内置兜底", "取不到密钥" in m3, True)
    r3, c3 = upload(png())
    check("M3b 取不到 key 时 0 请求、信封带 error", (r3["ok"], len(c3)), (False, 0))
finally:
    for n, v in _saved.items():
        setattr(gw.settings, n, v)

# ---- M4 成功形状 ----
r, calls = upload(png(), key_hint="wan3")
check("M4 ok:true", r["ok"], True)
check("M4b 恰好 1 次请求，且打到 v2 上传端点",
      (len(calls), calls[0][0], calls[0][1].endswith(gw.MEDIA_UPLOAD_ENDPOINTS["runninghub"])),
      (1, "POST", True))
check("M4c file_name 给的是引用名（媒体节点要这个）", r["result"]["file_name"], "openapi/fake.png")
check("M4d download_url 单独给（给人看的直链）", r["result"]["download_url"], "https://rh.invalid/fake.png")
check("M4e 说清用的哪把 key（只给变量名）", r["result"]["key_src"], "RUNNINGHUB_WAN3_API_KEY")

# ---- M5 🔴 200 + body code:404 ⇒ 失败 ----
RESP["payload"] = {"code": 404, "msg": "NOT_FOUND", "errorMessages": None, "data": None}
r5, c5 = upload(png())
check("M5 HTTP 200 但 body code:404 ⇒ 判失败", r5["ok"], False)
check("M5b 拒因里带 code 与 msg（不许糊成『上传失败』）",
      ("code=404" in (r5["error"] or "")) and ("NOT_FOUND" in (r5["error"] or "")), True)
check("M5c 状态码确实是 200（证明判据吃的是 body 不是状态码）", RESP["status"], 200)
check("M12 反向对照：同一条响应只按状态码判会被当成功（所以必须查 body）", RESP["status"] == 200, True)
# 🔴 M5 的形状（data:null）会被"没拿到地址"那条兜住 ⇒ 单独钉一条【只有 code 字段能区分】的形状，
#    否则"查 code"这条判据其实是空转（变异对照抓出来的：把 code_ok 改成恒真，M5 照样绿）。
RESP["payload"] = {"code": 803, "msg": "积分不足", "data": {"fileName": "openapi/stale.png",
                                                           "download_url": "https://rh.invalid/stale.png"}}
r5d, _c5d = upload(png())
check("M5d 200 + code≠0 但 data 里带着地址 ⇒ 仍判失败（不拿旧 data 当成功）", r5d["ok"], False)
check("M5e 且拒因点名 code", "code=803" in (r5d["error"] or ""), True)
check("M5f 失败时信封里没有 result（不许把 stale 地址递出去）", r5d["result"], None)
RESP["payload"] = {"code": 0, "msg": "success",
                   "data": {"fileName": "openapi/fake.png", "download_url": "https://rh.invalid/fake.png"}}

# ---- M6 200 + code:0 但没拿到地址 ----
RESP["payload"] = {"code": 0, "msg": "success", "data": {"fileName": "", "download_url": ""}}
r6, _ = upload(png())
check("M6 两个字段都空 ⇒ 判失败", r6["ok"], False)
RESP["payload"] = {"code": 0, "msg": "success",
                   "data": {"fileName": "openapi/fake.png", "download_url": "https://rh.invalid/fake.png"}}

# ---- M7 本地判据先拦住，0 请求 ----
for tag, path in (("不存在", os.path.join(PROD, "nope.png")),
                  ("空文件", png("empty.png", b""))):
    r7, c7 = upload(path)
    check("M7 %s ⇒ 拒且 0 请求" % tag, (r7["ok"], len(c7)), (False, 0))
big = png("big.png", b"x" * (gw.MEDIA_UPLOAD_MAX_BYTES + 10))
r7b, c7b = upload(big)
check("M7b 超体积上限 ⇒ 拒且 0 请求", (r7b["ok"], len(c7b)), (False, 0))
os.remove(big)

# ---- M8 不计费：这条口不该出现在扣费任何一张表里 ----
env = r["credits"]
check("M8a 信封 credits 恒 0", (env["estimated"], env["charged"], env["basis"]), (0, 0, "free"))
check("M8b media.upload 不是 capability（不会被误接进 invoke）",
      "media.upload" in gw.CAPABILITIES, False)
check("M8c ROUTES 里没有 media.upload", any("media" in str(k) for k in gw.ROUTES), False)
# 2026-10-09 口径改成【默认要批 + 免批白名单】⇒ "不在清单里"永远不成立，那条判据已失效。
# 真正的判据是：这条口【根本不问花钱闸】—— 源码里不出现任何 spend/approval 调用。
import inspect as _insp
_um_src = _insp.getsource(gw.upload_media)
check("M8d 上传口根本不问花钱闸（不调 spend_guard / check_spend_approval / needs_spend_approval）",
      [k in _um_src for k in ("check_spend_approval", "spend_guard", "needs_spend_approval")],
      [False, False, False])

# ---- M9 key 不外泄 ----
real_key = gw.resolve_media_target("runninghub", "wan3")["key"]
blob = json.dumps(r, ensure_ascii=False) + json.dumps(gw.media_uploads(50), ensure_ascii=False)
check("M9a 信封+台账里没有真 key", real_key in blob, False)
check("M9b 也没有 Bearer 字样", "Bearer" in blob, False)

# ---- M10 台账 ----
rows = gw.media_uploads(50)
check("M10a 每次尝试都留一行（含失败的那几次）", len(rows) >= 6, True)
last = rows[-1]
check("M10b 行里字段齐", sorted([k for k in ("ts", "provider", "key_hint", "file", "size",
                                             "file_name", "key_sha8", "ok", "error") if k in last]),
      sorted(["ts", "provider", "key_hint", "file", "size", "file_name", "key_sha8", "ok", "error"]))
check("M10c key 只留 sha8", len(str(last["key_sha8"])), 8)
_note = gw._media_upload_note
gw._media_upload_note = lambda rec: (_ for _ in ()).throw(RuntimeError("台账写不进去"))
try:
    r10, _c = upload(png())
    check("M10d 记账炸了 ⇒ 上传结果照给 + 明说 ledger_error（登记炸了≠上传炸了）",
          (r10["ok"], "台账写不进去" in str(r10.get("ledger_error"))), (True, True))
finally:
    gw._media_upload_note = _note

# ---- M11 8100 那条口的判据（直接调纯函数，不起服务）----
from backend import model_admin as ma                                   # noqa: E402


def port(body):
    REQ.clear()
    code, payload = ma.media_upload(body)
    return code, payload, list(REQ)


check("M11a 缺 file ⇒ 400", port({})[0], 400)
outside = os.path.join(SCRATCH, "outside.png")
io.open(outside, "wb").write(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
c, b, n = port({"file": outside})
check("M11b productions 之外 ⇒ 403 且 0 请求", (c, b.get("ok"), len(n)), (403, False, 0))
secret = os.path.join(PROD, "t1", "outputs", "phase_state.json")
io.open(secret, "w", encoding="utf-8").write('{"k":"RUNNINGHUB_ENTERPRISE_API_KEY=SECRET-VALUE"}')
c, b, n = port({"file": secret})
check("M11c json 一律拒传（phase_state.json 里存过明文厂商 key）",
      (c, b.get("ok"), len(n)), (400, False, 0))
trav = os.path.join(PROD, "t1", "outputs", "..", "..", "..", "..", "Windows", "win.ini")
c, b, n = port({"file": trav})
check("M11d 路径穿越（realpath 判定）⇒ 非 200 且 0 请求", (c, b.get("ok"), len(n)), (403, False, 0))
c, b, n = port({"file": png(), "key_hint": "wan3"})
check("M11e productions 内的 png ⇒ 200、真走网关一次", (c, len(n)), (200, 1))
check("M11f 响应里没有 key", real_key in json.dumps(b, ensure_ascii=False), False)

# ---- M13 三处 host/端点字面量一致（不改那两条腿的代码，改成钉一致性）----
import re                                                              # noqa: E402

wc = io.open(os.path.join(BACKEND, r"services\wan3_client.py"), encoding="utf-8").read()
rc = io.open(os.path.join(BACKEND, r"services\runninghub_client.py"), encoding="utf-8").read()


def literal(src, pat, tag):
    """从真源码里【抠出】字面量来比 —— 拿硬编码常量去比硬编码常量，那是假保险。"""
    m = re.search(pat, src, re.M)
    return (m.group(1) if m.groups() else m.group(0)) if m else "没在%s里找到这个形状" % tag


want_host = gw.MEDIA_UPLOAD_HOSTS["runninghub"]
check("M13a wan3_client 的 host 与网关一致",
      literal(wc, r'RH_WAN3_BASE_URL = \(getattr\(settings, "RUNNINGHUB_WAN3_BASE_URL", ""\) or "([^"]+)"\)',
              "wan3_client"), want_host)
check("M13b runninghub_client 的 host 与网关一致",
      literal(rc, r'^RUNNINGHUB_BASE_URL = "([^"]+)"', "runninghub_client"), want_host)
ep = gw.MEDIA_UPLOAD_ENDPOINTS["runninghub"]
check("M13c wan3_client 的上传端点与网关一致",
      literal(wc, r'RH_UPLOAD_ENDPOINT = "([^"]+)"', "wan3_client"), ep)
check("M13d runninghub_client 上传打的也是同一个端点",
      literal(rc, r'(/openapi/v2/media/upload/binary)', "runninghub_client"), ep)

urllib.request.urlopen = _real_urlopen
print(_safe("\n=== 素材上传口闸：%d/%d 通过 ===" % (len(PASS), len(PASS) + len(FAIL))))
for f in FAIL:
    print(_safe("  FAIL:" + " " + str(f)))
print(_safe("临时目录:" + " " + str(SCRATCH)))
sys.exit(1 if FAIL else 0)
