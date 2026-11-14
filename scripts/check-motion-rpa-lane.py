#!/usr/bin/env python
r"""动作迁移（motion-rpa · 8091 nzdh 工作室）这条腿的回归闸。

跑法：python scripts/check-motion-rpa-lane.py

这条腿和别的腿不一样，五件事必须同时成立，所以闸分五组：
 ① 它是【转调本机 8091】、不是直连厂商 ⇒ 探活绝不能花钱（provider 以 local 开头）。
 ② 批准条子的【消费点】在干活那一端（网关对这条 purpose 豁免）⇒ 「豁免」与「8091 真的有闸」必须成对，
    少一边就是绕过批准的后门（F 组是这里的核心）。
 ③ 一次 RPA = 一段成片，【时长不是计费档】（上游按 GPU 秒烧的是它自己账户的钱）。
 ④ 重投 = 双花（还占着全局 RPA 串行锁）⇒ job_id 只捞不重投；查询炸 6 次判死也不许重提交。
 ⑤ 右栏那一格嵌不了 runninghub.cn 原页（X-Frame-Options: SAMEORIGIN）⇒ 只放我们自己的截图，
    且页面里必须写清「为什么不做代理」，防止后人图省事改成转发登录态。

全离线：批准条子、积分账本、交付台账、8091 产物目录都指到临时目录；HTTP 层换成假 Client
（🔴 只换传输层，被测函数 `_motion_rpa_run` 整块真跑 —— 把它本身替掉就是假保险）。
"""
import inspect
import json
import os
import re
import sys
import tempfile

GW_ROOT = BACKEND
GATEWAY_PY = os.path.join(GW_ROOT, "backend", "gateway.py")
NZDH_ROOT = r"D:\nzdh"
NZDH_ROUTER = os.path.join(NZDH_ROOT, "womenswear", "router.py")
NZDH_CONFIG = os.path.join(NZDH_ROOT, "config.py")
NZDH_HTML = os.path.join(NZDH_ROOT, "frontend", "womenswear_studio.html")
NZDH_RPA = os.path.join(NZDH_ROOT, "backend", "services", "runninghub_rpa_client.py")
MODEL_ADMIN = os.path.join(GW_ROOT, "backend", "model_admin.py")

SCRATCH = tempfile.mkdtemp(prefix="motion-rpa-gate-")
os.environ["GATEWAY_SPEND_APPROVALS"] = os.path.join(SCRATCH, "approvals.json")
os.environ["GATEWAY_LEDGER"] = os.path.join(SCRATCH, "ledger.sqlite")
os.environ["DELIVERY_LEDGER"] = os.path.join(SCRATCH, "delivery.json")
os.environ.pop("DSH_SESSION_ID", None)          # 闸不许继承调用方身上的会话标
os.environ["PYTHONPATH"] = GW_ROOT
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, GW_ROOT)
sys.path.insert(0, os.path.join(GW_ROOT, "backend"))   # delivery_ledger 住在 backend/ 里

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


import httpx                                        # noqa: E402
from backend import gateway as gw                    # noqa: E402
import delivery_ledger                              # noqa: E402

SRC_GW = read(GATEWAY_PY)
SRC_ROUTER = read(NZDH_ROUTER)
SRC_CONFIG = read(NZDH_CONFIG)
SRC_ADMIN = read(MODEL_ADMIN)
SRC_HTML = read(NZDH_HTML)
SRC_RPA = read(NZDH_RPA)


def between(src, start_marker, end_marker, name=""):
    """从源码里切一段（判据钉在【那一段】上，别拿整文件当判据：整文件里到处都有的字符串谁都能凑齐）。"""
    i = src.find(start_marker)
    if i < 0:
        return ""
    j = src.find(end_marker, i + len(start_marker))
    return src[i:(j if j > 0 else len(src))]


# ── A 注册表：新腿进池，但不动默认档、探活不花钱 ───────────────────────────────
R = gw.ROUTES.get(("video.generate", "motion-rpa"))
check("A1 路由在册且 provider 以 local 开头（8100 面板据此不做 HTTP 探活=探它不花钱）",
      bool(R) and str(R.get("provider")).startswith("local"), True)
check("A2 适配器已接（is_adapted 真；没接的腿不该进池）",
      gw.is_adapted("video.generate", "motion-rpa"), True)
check("A3 内置单价 = 60 分/段（2026-10-06 用户拍的占位值；改价走 model_prices.json 不动代码）",
      gw.PRICE_ROUTES.get(("video.generate", "motion-rpa")), 60)
check("A4 占位价在源码里就标着「占位/未实测」（没测过的钱不许写得像测过）",
      "占位" in between(SRC_GW, "# ⚠️ 动作迁移 RPA 腿", '("video.generate", "motion-rpa"): 60'), True)
check("A5 离线探活回 local（既不是 live-fail，也不发起生成）",
      gw.test_route("video.generate", "motion-rpa")["status"], "local")
check("A6 默认档没被顶掉：video.generate 仍默认 omni-t2v",
      gw.DEFAULT_PURPOSE["video.generate"], "omni-t2v")

# ── B 计价真值表：一次 RPA = 一段，时长不是档 ──────────────────────────────────
M = lambda p: gw.meter("video.generate", "motion-rpa", p)["credits_estimate"]
check("B1 duration 5/30/60 一律 60（上游烧的是它自己账户的 GPU 秒，我方按段记账）",
      [M({"duration": d}) for d in (5, 30, 60)], [60, 60, 60])
check("B2 count=3 ⇒ 180（段数才是乘数）", M({"count": 3}), 180)
check("B3 例外不外溢：别的 video.generate 腿仍按每 10 秒折档",
      [gw.meter("video.generate", "omni-t2v", {"duration": 20})["credits_estimate"],
       gw.meter("video.generate", "h3", {"duration": 20})["credits_estimate"]], [80, 120])

# ── C 提交前校验（纯函数真值表）：缺件一律本地拦，0 分不发 HTTP ─────────────────
P = gw._motion_rpa_payload
check("C1 只给 job_id ⇒ poll（只捞不重投）", P({"job_id": "abc"})[0]["mode"], "poll")
check("C2 给 task_id ⇒ step5（补跑第五步）", P({"task_id": "abc"})[0]["mode"], "step5")
check("C3 job_id 与 task_id 同时给 ⇒ 以 task_id 为准（step5）",
      P({"job_id": "a", "task_id": "b"})[0]["mode"], "step5")
check("C4 product 模式缺模特图 ⇒ 本地拒（README 说可不填是错的，以 router._start_task 为准）",
      bool(P({"source_mode": "product", "product_url": "u", "video_url": "v"})[1]), True)
check("C4b 拒话点名缺的是哪个字段", "model_image_path" in
      str(P({"source_mode": "product", "product_url": "u", "video_url": "v"})[1]), True)
check("C5 creator 模式缺模仿视频 ⇒ 本地拒",
      bool(P({"source_mode": "creator", "raw_product_path": "r"})[1]), True)
check("C6 引擎只认 plus/lite/standard，乱写一律拒（不静默回落成 plus）",
      [P({"task_id": "t", "engine": "turbo"})[1] is not None,
       P({"task_id": "t", "engine": "lite"})[0]["engine"]], [True, "lite"])
check("C7 run 的 body 只带非空字段，且恒带 source_mode/engine",
      sorted(P({"source_mode": "creator", "raw_product_path": "r",
                "source_video_path": "s"})[0]["body"].keys()),
      sorted(["source_mode", "engine", "raw_product_path", "source_video_path"]))

# ── D 行为：假传输层 + 真函数（把被测函数整块替掉=假保险，不许）────────────────
OUT = os.path.join(SCRATCH, "nzdh_out")
gw.NZDH_OUTPUT_DIR = OUT                 # 「8091 的产物目录」指到临时盘
BASE = "http://127.0.0.1:8099"           # 假 base：真端口一次都不许碰
MP4 = b"x" * 4096
TASK_OK = {"status": "SUCCESS", "stage": "完成（RPA 链路）", "progress": 100,
           "outputs": [{"fileUrl": "/outputs/womenswear_studio/t9/final.mp4"}],
           "extra": {"current_step": "done", "session": "s-1"}, "rh_task_id": ""}


class Resp:
    def __init__(self, status=200, payload=None, content=b""):
        self.status_code = status
        self._p = payload if payload is not None else {}
        self.content = content or json.dumps(self._p).encode("utf-8")

    def json(self):
        return self._p

    @property
    def text(self):
        return json.dumps(self._p, ensure_ascii=False)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("HTTP %s" % self.status_code, request=None, response=None)


class FakeClient:
    """脚本表 script = {「POST <路径后缀>」: [Resp|Exception...]}；每次调用都记下来。"""
    script, calls, posts, raise_on_get = {}, [], [], 0

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _take(self, method, url):
        FakeClient.calls.append((method, url))
        for key, seq in FakeClient.script.items():
            verb, path = key.split(" ", 1)
            if verb == method and url.endswith(path) and seq:
                r = seq.pop(0)
                if isinstance(r, Exception):
                    raise r
                if isinstance(r, dict):          # 脚本表里允许直接写字典，省得每处都写 Resp(...)
                    r = Resp(**r)
                return r
        # 认不出的调用：记进 calls（D1b/F 组就是靠"多出来的那条 POST"抓双花），
        # 回一个 404 让流程自然失败。注意这里绝不会摸真网络——Client 整块已被换掉。
        return Resp(status=404, payload={"fake_unmatched": "%s %s" % (method, url)})

    def post(self, url, json=None, timeout=None):
        FakeClient.posts.append((url, json))
        return self._take("POST", url)

    def get(self, url):
        if FakeClient.raise_on_get:
            raise httpx.ConnectError("模拟查询掐线")
        return self._take("GET", url)


httpx.Client = FakeClient                # 只换传输层


def scripted(script):
    FakeClient.script = script
    FakeClient.calls = []
    FakeClient.posts = []
    FakeClient.raise_on_get = 0


def posts_only():
    return sorted(set(u.rsplit("/", 2)[-2] + "/" + u.rsplit("/", 1)[-1]
                      for m, u in FakeClient.calls if m == "POST"))


# D1 run 模式：只提交一次 /run，绝不再补 step5-rpa（/run 自己会跑到第五步 ⇒ 补一刀=双花）
os.makedirs(os.path.join(OUT, "t9"), exist_ok=True)
with open(os.path.join(OUT, "t9", "final.mp4"), "wb") as f:
    f.write(MP4)
scripted({"POST /api/womenswear-studio/run": [Resp(payload={"success": True, "task_id": "t9"})],
          "GET /api/womenswear-studio/task/t9": [Resp(payload=TASK_OK)],
          "GET /final.mp4": [Resp(content=MP4)]})
outp = os.path.join(SCRATCH, "deliv", "final.mp4")
res = gw._motion_rpa_run({"source_mode": "creator", "raw_product_path": r"D:\a\m.jpg",
                          "source_video_path": r"D:\a\v.mp4", "nzdh_base": BASE, "session": "s-1",
                          "output_path": outp, "poll_interval": 0.01, "poll_timeout": 5})
check("D1 run 模式成功（盯到终态、不是「已提交」）", res["success"], True)
check("D1b run 模式只打 /run 一次，不打 step5-rpa（双花防护）", posts_only(), ["womenswear-studio/run"])
check("D2 run 的 body 带上会话主人（8091 按它校验批准、按它分历史）",
      [b.get("session") for u, b in FakeClient.posts if "/run" in u], ["s-1"])
def _bytes_at(p):
    # 🔴 断言参数是急切求值的：上一版这里直接 open(outp)，文件没落盘时整份闸当场 FileNotFoundError 崩掉
    #    ——闸崩比闸红更糟（traceback 会被读成"没结果"，而不是"这条没过"）。
    try:
        return open(p, "rb").read()
    except OSError:
        return b""
check("D3 产物真落到调用方要的路径、字节与上游一致（落盘才算交付）",
      [os.path.isfile(outp), _bytes_at(outp) == MP4], [True, True])
check("D4 信封带回 8091 的 task_id（后续只捞不重投就靠它）", res.get("job_id"), "t9")

# D5 step5 模式：只打 step5-rpa，不重跑整条流水线
scripted({"POST /api/womenswear-studio/step5-rpa": [Resp(payload={"success": True, "task_id": "t9"})],
          "GET /api/womenswear-studio/task/t9": [Resp(payload=TASK_OK)],
          "GET /final.mp4": [Resp(content=MP4)]})
gw._motion_rpa_run({"task_id": "t9", "nzdh_base": BASE, "session": "s-1",
                    "output_path": os.path.join(SCRATCH, "s5.mp4"),
                    "poll_interval": 0.01, "poll_timeout": 5})
check("D5 step5 模式只打 /step5-rpa（不重跑 1-4 步）",
      posts_only(), ["womenswear-studio/step5-rpa"])

# D6 poll 模式：零 POST
scripted({"GET /api/womenswear-studio/task/t9": [Resp(payload=TASK_OK)],
          "GET /final.mp4": [Resp(content=MP4)]})
resp = gw._motion_rpa_run({"job_id": "t9", "nzdh_base": BASE, "session": "s-1",
                           "output_path": os.path.join(SCRATCH, "p.mp4"),
                           "poll_interval": 0.01, "poll_timeout": 5})
check("D6 job_id 模式一个 POST 都不发（重投=双花，还占着 RPA 全局串行锁）",
      [len(FakeClient.posts), resp["success"]], [0, True])

# D7 423（8091 判定没有批条）：说清是闸在拦、不轮询、不产文件
scripted({"POST /api/womenswear-studio/run": [Resp(status=423, payload={
    "detail": "approval_required：没有覆盖本次请求的批准条子"})]})
r423 = gw._motion_rpa_run({"source_mode": "creator", "raw_product_path": "r", "source_video_path": "s",
                           "nzdh_base": BASE, "session": "s-1",
                           "output_path": os.path.join(SCRATCH, "x.mp4"),
                           "poll_interval": 0.01, "poll_timeout": 5})
check("D7 8091 回 423 ⇒ 报「批准闸在拦」而不是「服务坏了」，且不轮询",
      [r423["success"], "批准" in (r423["error"] or ""), len(FakeClient.calls)], [False, True, 1])

# D8 查询连炸 6 次 ⇒ 判死但留住 job_id、绝不重提交
scripted({"GET /api/womenswear-studio/task/t9": [Resp(payload={
    "status": "RUNNING", "stage": "跑着", "progress": 90, "extra": {}})]})
FakeClient.raise_on_get = 1
rdead = gw._motion_rpa_run({"job_id": "t9", "nzdh_base": BASE, "session": "s-1",
                            "output_path": os.path.join(SCRATCH, "y.mp4"),
                            "poll_interval": 0.01, "poll_timeout": 30})
check("D8 查询连炸 6 次判死：带 job_id + 明说别重提交 + 零 POST",
      [bool(rdead.get("job_id")), "别重提交" in (rdead["error"] or ""), len(FakeClient.posts)],
      [True, True, 0])
FakeClient.raise_on_get = 0

# D9 只下到 4 字节（错误页也是 200）⇒ 判失败，不交假产物
os.makedirs(os.path.join(OUT, "t8"), exist_ok=True)
with open(os.path.join(OUT, "t8", "final.mp4"), "wb") as f:
    f.write(b"tiny")
scripted({"GET /api/womenswear-studio/task/t8": [Resp(payload={
              "status": "SUCCESS", "outputs": [{"fileUrl": "/outputs/womenswear_studio/t8/final.mp4"}],
              "extra": {}})],
          "GET /final.mp4": [Resp(content=b"tiny")]})
rsmall = gw._motion_rpa_run({"job_id": "t8", "nzdh_base": BASE, "session": "s-1",
                             "output_path": os.path.join(SCRATCH, "z.mp4"),
                             "poll_interval": 0.01, "poll_timeout": 5})
check("D9 只下到 4 字节 ⇒ 判失败且不在盘上留个假 mp4",
      [rsmall["success"], os.path.isfile(os.path.join(SCRATCH, "z.mp4"))], [False, False])

# D10 FAILED：失败步骤 + 上游原文一起交回（文案二次不可信）
scripted({"GET /api/womenswear-studio/task/t7": [Resp(status=200, payload={
    "status": "FAILED", "error": "Step5-RPA 失败: 参考图未通过肖像保护",
    "stage": "Step5-RPA 失败", "extra": {"failed_step": "step5"}})]})
rf = gw._motion_rpa_run({"job_id": "t7", "nzdh_base": BASE, "session": "s-1",
                         "poll_interval": 0.01, "poll_timeout": 5})
check("D10 FAILED ⇒ 报「失败于哪一步 + 上游原文」（肖像保护那类拦法要原样交回）",
      [rf["success"], "step5" in (rf["error"] or ""), "肖像保护" in (rf["error"] or "")],
      [False, True, True])

# ── E 交付台账：先记账再花钱 + 记账炸了不能拖垮调用 ────────────────────────────
def dl_rows():
    """台账文件的形状由 delivery_ledger 决定，闸跟着它读（顶层是 list 还是 {obligations:[...]} 都吃）。"""
    try:
        with open(os.environ["DELIVERY_LEDGER"], encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return []
    rows = d if isinstance(d, list) else (d.get("obligations") or [])
    return [r for r in rows if isinstance(r, dict)]


def dl_count():
    return len(dl_rows())


def dl_expected_count():
    return sum(len(r.get("expected") or []) for r in dl_rows())


check("E1 提交后登记了「欠一条成片」：run 与 step5 各记一次（poll 只捞不重复记账 ⇒ 恰好 2 条）",
      dl_count(), 2)
scripted({"POST /api/womenswear-studio/step5-rpa": [Resp(payload={"success": True, "task_id": "t9"})],
          "GET /api/womenswear-studio/task/t9": [Resp(payload=TASK_OK)],
          "GET /final.mp4": [Resp(content=MP4)]})
_b = dl_count()
res_nosid = gw._motion_rpa_run({"task_id": "t9", "nzdh_base": BASE,
                               "output_path": os.path.join(SCRATCH, "nosid.mp4"),
                               "poll_interval": 0.01, "poll_timeout": 5})
check("E2 没会话 id ⇒ 不登记（不给没主人的会话记账），但照常调用",
      [dl_count() == _b, res_nosid["success"]], [True, True])
_real_open = delivery_ledger.open_obligation
delivery_ledger.open_obligation = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("模拟记账炸了"))
scripted({"POST /api/womenswear-studio/step5-rpa": [Resp(payload={"success": True, "task_id": "t9"})],
          "GET /api/womenswear-studio/task/t9": [Resp(payload=TASK_OK)],
          "GET /final.mp4": [Resp(content=MP4)]})
res_boom = gw._motion_rpa_run({"task_id": "t9", "nzdh_base": BASE, "session": "s-1",
                              "output_path": os.path.join(SCRATCH, "boom.mp4"),
                              "poll_interval": 0.01, "poll_timeout": 5})
delivery_ledger.open_obligation = _real_open
check("E3 记账抛异常也不能让这次生成失败（登记炸了≠建片炸了）", res_boom["success"], True)

# ── F 花钱批准的【消费点】在哪一端 ─────────────────────────────────────────────
# 2026-10-10 用户定：8091 是【人自己在工作台上点的】独立产品，那一端不再要 DSH 的批准条子；
# agent 那条轨（gateway call video.generate/motion-rpa → 直调本地 services/motion_rpa）照旧要条子。
# 🔴 这两半必须同时钉：只钉"8091 不问"看不出"取消有没有一路扩到网关"，只钉"网关要批"看不出 8091 漂回去了。
check("F1 条子在【网关这一端】消费：源头豁免表里没有 motion-rpa（不重复扣段，用户也只批一次）",
      gw.approval_consumed_at_source("video.generate", "motion-rpa"), False)
check("F2 invoke() 的豁免写成「要批 且 不在源头消费」，不是整块跳过闸",
      'needs_spend_approval(capability) and not approval_consumed_at_source('
      'capability, r["purpose"])' in SRC_GW, True)
check("F3 豁免表里每一条都必须是【已接的腿】（豁免=占位路由 = 无人值守的钱）",
      [gw.is_adapted(c, p) for (c, p) in gw.SPEND_APPROVAL_AT_SOURCE], [True] * len(gw.SPEND_APPROVAL_AT_SOURCE))
check("F4 🔴 8091 那一端的批准闸整块拆了：判定 helper 在源码里零出现",
      SRC_ROUTER.count("_spend_guard_or_423"), 0)
check("F4c 反向对照：router 确实读到了、三个花钱口也都还在（F4 的 0 不是「没读到文件」）",
      [len(SRC_ROUTER) > 40000] + [p in SRC_ROUTER for p in (
          '"/api/womenswear-studio/run"', '"/api/womenswear-studio/step5-rpa"',
          '"/api/womenswear-studio/retry"')], [True, True, True, True])
check("F4b 🔴 取消只到 8091 为止：video.generate 仍然【要批】，且 invoke() 真的调了判定口",
      [gw.needs_spend_approval("video.generate"),
       'gate = check_spend_approval(capability, r["purpose"]' in SRC_GW], [True, True])
check("F4d 8091 不再留任何批准配置（残留一行 = 下一个人以为它还会问）",
      [k in SRC_CONFIG for k in ("SPEND_GUARD_URL", "SPEND_CAPABILITY", "SPEND_PURPOSE")],
      [False, False, False])
check("F7 8100 上的 spend 端点集合（approve/deny 只消费【已有】 pending，guard 留给跨进程的腿）",
      sorted(set(re.findall(r"/api/spend/[a-z]+", SRC_ADMIN))),
      ["/api/spend/approvals", "/api/spend/approve", "/api/spend/deny",
       "/api/spend/guard", "/api/spend/pending"])
GUARD_BRANCH = between(SRC_ADMIN, '"/api/spend/guard"', "elif u.path ==", "branch")
# 🔴 2026-10-09：环回判据被抽成 _not_loopback/_is_loopback「只这一份」⇒ 分支里不再有
#    client_address 字面量，原来那条"钉现场"的断言当场假红。改成钉规则：走那一份守卫 + 守卫本体真判环回 + 全仓只有一份。
check("F8 guard 端点走【那一份】环回守卫（局域网里的机器不能消耗本机用户签的批准）",
      "_not_loopback(" in GUARD_BRANCH, True)
_nl_body = SRC_ADMIN[SRC_ADMIN.index("def _not_loopback"):SRC_ADMIN.index("def _not_loopback") + 420]
check("F8b 守卫本体只问 _is_loopback 并回 403（不许某条口自己发别的码）",
      ["_is_loopback(" in _nl_body, "403" in _nl_body], [True, True])
check("F8c 环回元组全仓只有一份（第 N 份 = 漏改一处就漏一个口）",
      len(re.findall(r'\("127\.0\.0\.1", "::1", "localhost"\)', SRC_ADMIN)), 1)
check("F9 🔴 「创建批准」的口物理上不存在：8100 全文不调 create_spend_pending，只调两个消费口",
      ["create_spend_pending" in SRC_ADMIN, "consume_by_click" in SRC_ADMIN,
       "deny_pending" in SRC_ADMIN], [False, True, True])
check("F9b 建待批仍然只在 CLI（gateway.py approve）那一侧，且签发只有 consume_by_click 一条路",
      "def create_spend_pending" in SRC_GW, True)

# ── G 端口与右栏契约 ───────────────────────────────────────────────────────────
check("G1 8091 默认绑环回（config.py）",
      (re.search(r'HOST:\s*str\s*=\s*"([^"]+)"', SRC_CONFIG) or [None, None])[1], "127.0.0.1")
_env = read(os.path.join(NZDH_ROOT, ".env"))
check("G2 .env 里那行 HOST 也收回来了（.env 会盖默认值；闸只取这一行，不读文件其它内容）",
      (re.search(r"(?m)^HOST=([^\r\n]*)", _env) or [None, ""])[1].strip(), "127.0.0.1")
check("G3 历史按会话过滤在位：/history 与 /videos 都收 session 参数",
      [SRC_ROUTER.count("session: str = \"\"") >= 2, "extra\", {}).get(\"session\")" in SRC_ROUTER
       or "(t.get(\"extra\", {}) or {}).get(\"session\")" in SRC_ROUTER], [True, True])
check("G4 旧任务没有主人 ⇒ 带 session 的查询里不出现（宁可少列，不把别人的单子塞给你）",
      "if session:\n        tasks = [t for t in tasks" in SRC_ROUTER, True)
check("G5 前端把 ?session= 透传到 run 提交 / history / videos 三处",
      ["payload.session = DSH_SESSION" in SRC_HTML,
       SRC_HTML.count("encodeURIComponent(DSH_SESSION)") >= 2,
       "URLSearchParams(location.search).get(" in SRC_HTML], [True, True, True])

# ── H 「为什么不嵌 RH 原页」这段理由必须留在页面与代码里 ────────────────────────
LIVE = between(SRC_ROUTER, "_LIVE_PAGE = ", "def womenswear_studio_rpa_live_png", "live")
check("H1 实时画面三个端点在位（页面 / meta / png）",
      ['"/api/womenswear-studio/rpa-live"' in SRC_ROUTER, "/api/womenswear-studio/rpa-live/meta" in SRC_ROUTER,
       "/api/womenswear-studio/rpa-live.png" in SRC_ROUTER], [True, True, True])
check("H2 页面写明了物理原因（X-Frame-Options: SAMEORIGIN），不是「还没做」",
      "X-Frame-Options" in LIVE and "SAMEORIGIN" in LIVE, True)
check("H3 页面/代码里写明「不做转发登录态的代理」这条取舍（防止后人改成开放代理）",
      ("登录态" in LIVE or "凭据" in LIVE) and "代理" in LIVE, True)
LIVE_LOOP = between(SRC_RPA, "def _live_shoot_loop", "def _ensure_live_shooter")
# 🔴 钉的是【包住截图那一条】外层兜底：内层那个 `except Exception:`（取 url 用的）不算——
#    上一版就是拿"循环里有没有 except Exception"当判据，把外层换成窄捕获照样绿（假保险）。
#    外层那条带 `as e`（要打印异常名），用它当锚，且必须在截图调用之后。
check("H4 截图调用外层包着 except Exception as e（页面一抖只影响那一格，不许把在跑的 RPA 任务带崩）",
      ["await page.screenshot(" in LIVE_LOOP,
       "except Exception as e:" in LIVE_LOOP,
       -1 < LIVE_LOOP.find("await page.screenshot(") < LIVE_LOOP.find("except Exception as e:")],
      [True, True, True])
check("H5 画面会过期：60 秒没新图就不当「正在跑」（旧帧不许冒充实时）",
      "age < 60" in SRC_RPA, True)
_m = re.search(r"RPA_LIVE_INTERVAL:\s*float\s*=\s*([\d.]+)", SRC_CONFIG)
check("H6 实时画面由配置开关控制（0 = 关），且默认间隔不激进（防挤进动作时序）",
      bool(_m) and float(_m.group(1)) >= 1.0, True)

print(_safe("\n=== 动作迁移腿闸：%d 条断言，失败 %d ===" % (TOTAL, len(fails))))
if fails:
    print(_safe("失败项：" + "、".join(fails)))
    sys.exit(1)
