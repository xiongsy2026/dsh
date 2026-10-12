# -*- coding: utf-8 -*-
"""wan3-prime（满血 wan3.0）重试环的回归闸。

2026-10-10 现网：这条 lane 平台侧成功率 ~23%（24 败 / 7 成），而我们过去只有"结果未知【不要重发】"
一句话 ⇒ 两种坏法同时发生：
  ① 20:15/20:16 平台其实【建了任务】（有 prime_ 号），但 HTTP 502 走了"未知"分支、没去挖回包里的号
     ⇒ 号丢了捞不回来，只能重投；
  ② 重投由上层再 call 一次 ⇒ 每 call 吃一段批准额度 ⇒ 用户被逼着点第四次批准。
修法：重试环搬进网关适配器内部（一次批准、一段额度、最多 4 次尝试），每一次决定前【先读平台对账口】。
🔴 红线：只有"对账证明没建上"或"建了但计费 0"才允许重投；对账口没回话 = 停手，不许当成"没建上"。

只换【传输层】：`_yz_prime_http` 换成脚本化假响应，被测的是真 `_yz_prime_run` / `_yz_prime_once` /
`prime_retry_verdict`。不打网络、不花钱、不落仓库（输出写临时目录）。
"""
import io
import json
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, r"D:\video-skill")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend import gateway as gw   # noqa: E402

TOTAL = [0]
fails = []


def _safe(s):
    return s.encode("gbk", "replace").decode("gbk") if isinstance(s, str) else s


def check(name, got, want):
    TOTAL[0] += 1
    a, b = json.dumps(got, ensure_ascii=False, default=str), json.dumps(want, ensure_ascii=False, default=str)
    print(_safe(("PASS " if a == b else "FAIL ") + name + ("" if a == b else "  => %r (期望 %r)" % (got, want))))
    if a != b:
        fails.append(name)


MP4 = b"\x00\x00\x00\x18ftypmp42" + b"M" * 4096          # >1KB，过"不像视频"那道判据
BASE = "https://prime.test"


def bal(remaining=1, consumed=7, reserved=0, recent=None, failed=24, succeeded=7):
    return json.dumps({"balance": {"remainingCalls": remaining, "usedCalls": consumed},
                       "tasks": {"reservedCalls": reserved,
                                 "byStatus": {"failed": failed, "succeeded": succeeded}},
                       "recent": recent or []}).encode("utf-8")


def task(tid, status="succeeded", charged=0, url=""):
    return json.dumps({"id": tid, "status": status, "video_url": url,
                       "billing": {"charged_calls": charged}}).encode("utf-8")


def post(st, body):
    return (st, {}, (body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")))


LAST = {}          # 每次跑完把"号有没有当场落盘"带出来（清盘之后就查不到了）


def run_scenario(posts, balances, tasks, params_extra=None, download=MP4):
    """跑一次真 _yz_prime_run，返回 (结果, POST 次数, GET 任务次数)。"""
    state = {"posts": list(posts), "balances": list(balances), "calls": []}

    def fake_http(method, url, headers=None, body=None, timeout=None):
        state["calls"].append((method, url))
        if url.endswith("/v1/balance"):
            return (200, {}, state["balances"].pop(0) if state["balances"] else balances[-1])
        if url.endswith("/v1/videos/generations"):
            # 脚本用完还发 ⇒ 回一个 4xx（不抛异常：抛了会被被测代码的 `except Exception` 吃掉，
            # 于是"多投一次"反而变成绿灯）。超发由每条断言里的 np 精确次数咬住。
            if not state["posts"]:
                return (429, {}, b"OVER-POST-beyond-script")
            return state["posts"].pop(0)
        m = re.search(r"/v1/videos/([^/?]+)$", url)
        if m:
            tid = m.group(1)
            if tid in tasks:
                return (200, {}, tasks[tid])
            return (404, {}, b'{"error":"TASK_NOT_FOUND"}')
        return (200, {}, download)

    real = gw._yz_prime_http
    gw._yz_prime_http = fake_http
    out_dir = tempfile.mkdtemp(prefix="prime-gate-")
    LAST["job"] = None
    try:
        p = {"prompt": "开衫+百褶裙，锁运镜", "duration": 15, "resolution": "1080P", "ratio": "9:16",
             "video_urls": ["https://cdn.test/ref.mp4"], "image_urls": ["https://cdn.test/a.png"],
             "output_path": os.path.join(out_dir, "seg01-prime.mp4"),
             "base_url": BASE, "api_key": "k-test", "prime_backoff": [0, 0, 0]}
        p.update(params_extra or {})
        r = gw._yz_prime_run(p, {"model": "wan3.0-video-prime", "provider": "yz-prime"})
        jp = os.path.join(out_dir, "wan3_job.json")          # 清盘【之前】把号读出来（这是被验的那件事）
        if os.path.isfile(jp):
            try:
                LAST["job"] = json.load(open(jp, encoding="utf-8"))
            except (OSError, ValueError):
                LAST["job"] = "坏文件"
    finally:
        gw._yz_prime_http = real
        shutil.rmtree(out_dir, ignore_errors=True)
    n_post = sum(1 for m, u in state["calls"] if u.endswith("/v1/videos/generations"))
    n_get = sum(1 for m, u in state["calls"] if re.search(r"/v1/videos/[^/]+$", u) and not u.endswith("generations"))
    return r, n_post, n_get


OK_URL = "https://dashscope.test/out.mp4"

# ── R1-R2 判据值表（纯函数，整块抠出来跑；不打 HTTP）──────────────────────────────
check("R1 值表：未知+回包带号 ⇒ 轮询它（不重投）", gw.prime_retry_verdict("submit_unknown", "prime_A", set(),
      {"recent": {}, "reserved": 0, "consumed": 7}, 3)[0], "poll")
check("R2 值表：未知+平台已建新任务 ⇒ 去捞新任务（今天 20:15/20:16 那两次就该走这条）",
      gw.prime_retry_verdict("submit_unknown", "", set(), {"recent": {"prime_NEW": {}}, "reserved": 0}, 3)[0],
      "poll:prime_NEW")
check("R3 值表：未知+有预占 ⇒ 停（重投=双花）",
      gw.prime_retry_verdict("submit_unknown", "", set(), {"recent": {}, "reserved": 1}, 3)[0], "stop")
check("R4 🔴 值表：对账口没回话(rec=None) ⇒ 停（【拿不到】绝不等于【没建上】）",
      gw.prime_retry_verdict("submit_unknown", "", set(), None, 3)[0], "stop")
check("R5 值表：失败但计费 0 ⇒ 允许重投（这是成功率的来源）",
      gw.prime_retry_verdict("task_failed", "prime_A", set(),
                             {"recent": {"prime_A": {"charged": 0}}}, 3)[0], "retry")
check("R6 值表：失败但已计费 ⇒ 停（别再花一次）",
      gw.prime_retry_verdict("task_failed", "prime_A", set(),
                             {"recent": {"prime_A": {"charged": 1}}}, 3)[0], "stop")
check("R7 值表：已出片没落盘 / 轮询超时 ⇒ 只捞不重投",
      [gw.prime_retry_verdict(k, "prime_A", set(), {"recent": {}}, 3)[0]
       for k in ("deliver_failed", "poll_timeout")], ["stop", "stop"])
check("R8 值表：参数/合规被拒 ⇒ 停（重投一样错）",
      gw.prime_retry_verdict("submit_param", "", set(), {"recent": {}}, 3)[0], "stop")

# ── R9-R15 真跑重试环（假传输）──────────────────────────────────────────────────
r, np, ng = run_scenario([post(502, {}), post(200, {"task_id": "prime_B"})],
                         [bal(recent=[]), bal(recent=[]), bal(recent=[{"id": "prime_B"}])],
                         {"prime_B": task("prime_B", "succeeded", 1, OK_URL)})
check("R9 真跑：502 且平台没建 ⇒ 内部重投一次并成功（POST 2 次、不额外吃批准）",
      [r.get("success"), np, r.get("prime_attempts")], [True, 2, 2])

r, np, ng = run_scenario([post(502, {})],
                         [bal(recent=[]), bal(recent=[{"id": "prime_NEW", "status": "running",
                                                       "billing": {"charged_calls": 0}}])],
                         {"prime_NEW": task("prime_NEW", "succeeded", 1, OK_URL)})
check("R10 🔴 真跑：502 但平台已建任务 ⇒ 去捞它，【一次都不重投】（修掉今天白丢的那两次）",
      [r.get("success"), np, r.get("job_id")], [True, 1, "prime_NEW"])

r, np, ng = run_scenario([post(200, {"task_id": "prime_A"}), post(200, {"task_id": "prime_B"})],
                         [bal(recent=[]), bal(recent=[{"id": "prime_A", "status": "failed",
                                                       "billing": {"charged_calls": 0}}]), bal(recent=[])],
                         {"prime_A": task("prime_A", "failed", 0),
                          "prime_B": task("prime_B", "succeeded", 1, OK_URL)})
check("R11 真跑：任务判失败且计费 0 ⇒ 同参数重投，第二次成（POST 2 次）",
      [r.get("success"), np, r.get("prime_attempts")], [True, 2, 2])

r, np, ng = run_scenario([post(200, {"task_id": "prime_A"})],
                         [bal(recent=[]), bal(recent=[{"id": "prime_A", "status": "failed",
                                                       "billing": {"charged_calls": 1}}])],
                         {"prime_A": task("prime_A", "failed", 1)})
check("R12 真跑：任务失败但已计费 ⇒ 停手且【不重投】（POST 必须 1 次）",
      [r.get("success"), np], [False, 1])

r, np, ng = run_scenario([post(502, {}), post(502, {}), post(502, {}), post(502, {}), post(502, {})],
                         [bal(recent=[])], {})
check("R13 真跑：对账口一直说『没建上』⇒ 最多投 4 次就停（有上限，不死循环烧上游）",
      [r.get("success"), np, len(r.get("prime_tries") or [])], [False, 4, 4])

r, np, ng = run_scenario([post(200, {"task_id": "x"})], [bal(remaining=0, consumed=8)], {})
check("R14 🔴 真跑：平台次数已用尽 ⇒ 提交前就停（POST 0 次、0 消耗），并把对账读数报出来",
      [r.get("success"), np, "已用尽" in str(r.get("error"))], [False, 0, True])

r, np, ng = run_scenario([], [bal(recent=[])], {"prime_Z": task("prime_Z", "succeeded", 0, OK_URL)},
                         params_extra={"job_id": "prime_Z"})
check("R15 真跑：调用方自带 job_id ⇒ 只捞（POST 必须 0 次），重试环不许插手",
      [r.get("success"), np, r.get("job_id")], [True, 0, "prime_Z"])
# 🔴 反向对照（自己差点造出来的双花）：判据字段写错成 task_id 时，payload 不切 poll 模式 ⇒ 会重新 POST。
#    这条钉住"捞"的入口字段名，下次谁改成 task_id 就红。
_SRC2 = io.open(os.path.join(r"D:\video-skill", "backend", "gateway.py"), encoding="utf-8",
                errors="replace").read()
_ring = _SRC2[_SRC2.find("def _yz_prime_run"):]
check("R22 🔴 重试环里『只捞』必须用 job_id 传（写成 task_id = 静默重新提交 = 双花）",
      ['if params.get("job_id"):' in _ring, 'p2["job_id"] =' in _ring,
       'p2["task_id"] =' in _ring], [True, True, False])

r, np, ng = run_scenario([post(200, {"task_id": "x"})], [bal(recent=[])], {},
                         params_extra={"model": "别的模型"})
check("R16 真跑：调用方自带 model ⇒ 大声拒（绝不被换成别的模型偷偷跑）",
      [r.get("success"), np, "只出" in str(r.get("error"))], [False, 0, True])

# ── R17 反向对照（成对跑）：对账口读不出时，判据到底管不管得住 POST 次数 ──────────────
_real_verdict = gw.prime_retry_verdict
_P = [post(502, {}), post(429, {}), post(429, {}), post(429, {}), post(429, {})]
_B = [b"balance-down"] * 4              # 全程读不出 JSON ⇒ rec=None（=对账口没回话）

r, np_real, ng = run_scenario(list(_P), list(_B), {})
check("R17a 真跑：对账口没回话 ⇒ 只投 1 次就停（『读不到』不等于『没建上』）",
      [r.get("success"), np_real], [False, 1])


def _loose(kind, task_id, seen_ids, rec, attempts_left):
    # 变异只动【一条】规则：把"对账没回话"当"没建上"。别的 kind 照旧走真判据
    # ⇒ 这样 np 的变化只归因到这一条，不会因为我把手改宽而多投（那测的就不是这条了）。
    if kind == "submit_unknown" and not isinstance(rec, dict) and attempts_left > 0:
        return "retry", "变异：把『对账没回话』当『没建上』"
    return _real_verdict(kind, task_id, seen_ids, rec, attempts_left)


gw.prime_retry_verdict = _loose
try:
    r2, np_mut, ng2 = run_scenario(list(_P), list(_B), {})
    check("R17b 反向对照：只把判据改松 ⇒ 同场景 POST 从 1 变 2（证明 R17a/R4 真的有牙）",
          [np_mut > np_real, np_mut], [True, 2])
finally:
    gw.prime_retry_verdict = _real_verdict

# ── R18-R19 同名不许悄悄覆盖（真文件系统）──────────────────────────────────────
_d = tempfile.mkdtemp(prefix="prime-dl-")
try:
    dest = os.path.join(_d, "seg01-prime.mp4")
    real = gw._yz_prime_http

    def dl_http(method, url, headers=None, body=None, timeout=None):
        return (200, {}, MP4)
    gw._yz_prime_http = dl_http
    try:
        io.open(dest, "wb").write(b"OLD-CONTENT-DELIVERED" + b"o" * 4096)
        p1, n1, e1 = gw._yz_prime_download("https://cdn.test/a.mp4", dest, "https://cdn.test", "k")
        prev = [f for f in os.listdir(_d) if ".prev-" in f]
        check("R18 🔴 真跑：同名不同内容 ⇒ 旧的那条被留成 .prev-<sha8>（今天 v3 就是这么盖掉已交付成片的）",
              [os.path.isfile(dest), len(prev), io.open(dest, "rb").read() == MP4], [True, 1, True])
        p2, n2, e2 = gw._yz_prime_download("https://cdn.test/a.mp4", dest, "https://cdn.test", "k")
        check("R19 反向对照：同内容重下 ⇒ 不产生第二份 .prev（不是每次都留垃圾）",
              len([f for f in os.listdir(_d) if ".prev-" in f]), 1)
    finally:
        gw._yz_prime_http = real
finally:
    shutil.rmtree(_d, ignore_errors=True)

# ── R20 覆盖对照：网关那条 lane 的适配器确实接的是新入口（不是还指着旧函数）──────
SRC = io.open(os.path.join(r"D:\video-skill", "backend", "gateway.py"), encoding="utf-8",
              errors="replace").read()
check("R20 覆盖对照：价目里 wan3-prime 挂的适配器 = _yz_prime_run（改错地方=白改）",
      '"client": "gateway._yz_prime_run"' in SRC, True)
check("R21 环内重投不许绕开对账：_yz_prime_run 里每次失败后都调了 _yz_prime_reconcile",
      SRC.count("_yz_prime_reconcile(base, api_key)") >= 2, True)

# ── R23-R25 任务号必须【当场落盘】：没这份留痕，进程一死就只能重投（=双花）──────────────
# 2026-10-12 现网：agent 的前台命令被 DSH 工具 10 分钟上限强杀，号只在内存 ⇒ 丢号 ⇒ 只能重投。
r, np, ng = run_scenario([post(200, {"task_id": "prime_JOB1"})], [bal()],
                         {"prime_JOB1": task("prime_JOB1", "succeeded", 1, OK_URL)})
_j = LAST.get("job") or {}
check("R23 真跑：提交成功那一刻就把号落在成片旁边（wan3_job.json：kind + task_id + output_path + 路由）",
      [r.get("success"), _j.get("kind"), _j.get("task_id"), bool(_j.get("output_path")),
       _j.get("route")],
      [True, "wan3_job", "prime_JOB1", True, "video.generate/wan3-prime"])
r2, np2, ng2 = run_scenario([post(404, {"error": "PROMPT_ILLEGAL"})], [bal()], {})
check("R24 反向：提交被明确拒（没号）⇒ 不落号文件（造一个假号 = 后面那条只读捞会去查不存在的任务）",
      [r2.get("success"), LAST.get("job")], [False, None])
_pre = SRC[:SRC.index("deadline = time.time()")]
_NOTE = "_yz_prime_note_job(params, tid, model)"        # 🔴 用【调用形状】找，别用 `_yz_prime_note_job(` ——
                                                        #    那会先命中函数定义本身（定义在调用之前），顺序就反了
check("R25 顺序：写号那一行排在【拿到号之后、进轮询循环之前】—— 排在循环后就等于没写"
      "（循环里被强杀是常态，那正是这一格要防的事）",
      [_NOTE in _pre, _pre.index('vals["task_id"] = tid') < _pre.index(_NOTE)], [True, True])
_cut = SRC.replace(_NOTE, "", 1)
check("R25b 变异对照：把那一行删掉 ⇒ R25 必须红（不红说明它是恒绿的摆设）",
      _NOTE in _cut[:_cut.index("deadline = time.time()")], False)

print(_safe("\n=== wan3-prime 重试环闸：%d 条断言，失败 %d ===" % (TOTAL[0], len(fails))))
if fails:
    print(_safe("失败清单: " + " | ".join(f.split()[0] for f in fails)))
sys.exit(1 if fails else 0)
