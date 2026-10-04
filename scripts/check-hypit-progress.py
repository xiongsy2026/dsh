"""进度台数据面回归：生成腿必须自带「工程 + 会话」身份，账本能按会话聚出段状态（全程 0 分、不联网）。

契约见 docs/ADR-workbench-presence.md「身份进数据面」。跑法：
  PYTHONIOENCODING=utf-8 python scripts/check-hypit-progress.py
"""
import io
import json
import os
import shutil
import sys

SCRATCH = r"C:\dsh\_scratch_b"
DB = os.path.join(SCRATCH, "progress_selftest.sqlite")
WS = r"C:\dsh\hypit_runs"        # 用真工作区根：_project_key 只做 realpath/relpath，不写盘
os.makedirs(SCRATCH, exist_ok=True)
assert os.path.isdir(os.path.join(WS, "productions", "nose-cream")), "工作区根/工程目录得真实存在"
if os.path.exists(DB):
    os.remove(DB)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Windows 控制台默认 GBK，箭头一印就炸
except Exception:
    pass
os.environ["HYPIT_JOBS_DB"] = DB
os.environ["HYPIT_WORKSPACE"] = WS
os.environ["HYPIT_INTENT_MARK"] = os.path.join(SCRATCH, "progress_intent.json")
os.environ["HYPIT_STUDIO_MARK"] = os.path.join(SCRATCH, "progress_studio.json")
# 账本路径在 gateway 导入时就定死了 ⇒ 必须在 import 之前设好，测试一笔真钱都不碰生产账本
os.environ["GATEWAY_LEDGER"] = os.path.join(SCRATCH, "progress_ledger.sqlite")
sys.path.insert(0, r"D:\video-skill")
sys.path.insert(0, r"D:\video-skill\backend")
import hypit_bridge as hb                                       # noqa: E402
import subprocess                                               # noqa: E402
import time                                                     # noqa: E402

SID = "session-PROGRESS-1"
NEED = ("need:author%3AC%3A%5Cdsh%5Chypit_runs%5Cproductions%5Cnose-cream%5Cnose.svml"
        "%3A%3Acomponent%3A%3Afinal:request-audio-render")      # 2026-10-02 实测 plan --json 的原文

fails, total = [], [0]


def check(name, got, want):
    total[0] += 1
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name, "=>", json.dumps(got, ensure_ascii=False)[:130])
    if not ok:
        fails.append((name, got, want))


# ── ① 身份归一：need id → 工程目录；越界一律丢 ──────────────────────────────────────
check("1 实测 need id 归一出工程目录", hb._project_key({"need": NEED}), "productions/nose-cream")
check("1b 段标签取 need 尾巴", hb._need_label({"need": NEED}), "request-audio-render")
check("1c 绝对路径在工作区内也算（正斜杠也接），_project_key 归一结果",
      hb._project_key({"project": "C:/dsh/hypit_runs/productions/hypit-e2e/assemble.svml"}), "productions/hypit-e2e")
check("1d 工作区外的自报路径被丢", hb._project_key({"project": r"C:\Windows\nope.svml"}), "")
check("1e 目录穿越被丢", hb._project_key({"project": "../../etc/passwd"}), "")
check("1f 不在 productions/ 下的被丢", hb._project_key({"project": "packages/a/b.svml"}), "")

# ── ② 提交盖章：老形状（没有 project/need/session）照旧受理，不炸 ────────────────────
hb._run = lambda *a, **k: None                                  # 不许真去调网关
base = {"model": "seedance-2-mini", "prompt": "自测段", "duration": 5,
        "resolution": "720p", "ratio": "9:16", "account": "selftest"}
code_old, r_old = hb.handle_submit(dict(base))
check("2 不带身份字段的老请求仍受理", [code_old, "id" in r_old], [200, True])
code_new, r_new = hb.handle_submit(dict(base, need=NEED, session=SID))
check("2b 带身份的受理并落库", [code_new, "id" in r_new], [200, True])
con = hb._db()
row = con.execute("select run,label,session from jobs where id=?", (r_new["id"],)).fetchone()
con.close()
check("2c 工程/段名/会话都盖进账本了", list(row), ["productions/nose-cream", "request-audio-render", SID])
con = hb._db()
row0 = con.execute("select run,session from jobs where id=?", (r_old["id"],)).fetchone()
con.close()
check("2d 老请求落空串，不冒充任何工程", list(row0), ["", ""])

# ── ③ 聚合：状态计数、耗时、扣分、上游任务号 ────────────────────────────────────────
hb._touch(r_new["id"], state="succeeded", credits=50)
jid2 = hb.handle_submit(dict(base, need=NEED.replace("request-audio-render", "request-video-generate"),
                             session=SID))[1]["id"]
hb._touch(jid2, state="failed", error=json.dumps(
    {"code": "GATEWAY_FAILED", "message": "轮询超时 1800s（上游任务 d909fa86e1bf43f484c2e20aa106bd34，可据此查）"},
    ensure_ascii=False))
jid3 = hb.handle_submit(dict(base, need=NEED, session="session-PROGRESS-OTHER"))

p = hb.jobs_progress(SID)
check("3 按会话聚合：2 条里 1 成 1 败", [p["known"], p["summary"]["ok"], p["summary"]["failed"]],
      [True, 1, 1])
check("3b 扣分合计只算真成功的", p["summary"]["credits"], 50)
check("3c 工程分组出来", p["projects"], ["productions/nose-cream"])
seg = {x["id"]: x for x in p["segments"]}
check("3d 失败段带上游任务号", seg[jid2]["upstream"], "d909fa86e1bf43f484c2e20aa106bd34")
check("3e 别的会话不串进来", hb.jobs_progress("session-PROGRESS-OTHER")["summary"]["total"], 1)
check("3f 没腿的会话如实说没有", [hb.jobs_progress("session-NOPE")["known"],
                                 hb.jobs_progress("session-NOPE")["reason"]], [False, "no-jobs"])
check("3g 不带会话不查库", hb.jobs_progress("")["reason"], "no-session")

# ── ③b 成功的腿必须带成片地址（右栏"看片"、agent 回传贴图都靠它，2026-10-03 用户点名）──
con = hb._db()
con.execute("update jobs set output=? where id=?",
            ("http://localhost:8100/api/hypit/assets/abc123.mp4", r_new["id"]))
con.commit()
con.close()
hb._PROGRESS_CACHE.update(sid=None, ts=0)            # 绕开 5 秒缓存，别拿旧结果自证
p_out = {x["id"]: x for x in hb.jobs_progress(SID)["segments"]}
check("3h 成功段带得出 8100 直链", p_out[r_new["id"]]["output"], "http://localhost:8100/api/hypit/assets/abc123.mp4")
check("3i 失败段不给空链接（画出来就是骗人）", p_out[jid2]["output"], "")

# ── ④ 判定口把进度带上（台才有内容可画，哪怕 studio 没起）───────────────────────────
hb.set_intent(SID, "hypit", "复刻 video_1")
hb._probe_studio = lambda port: {"up": False, "error": "ConnectionRefusedError"}
v = hb.workbench_presence(SID)["hypit"]
check("4 studio 没起也带得上进度", [v["up"], v.get("progress", {}).get("known")], [False, True])
check("4b 进度里能看到段状态", sorted(x["state"] for x in v["progress"]["segments"]),
      ["failed", "succeeded"])
check("4c 意图仍是唯一触发器（无登记的会话不带进度）",
      hb.workbench_presence("session-PROGRESS-OTHER")["hypit"]["intent"], False)

# ── ④b「选了就给界面」：工程解析与端口分配（不起真进程）────────────────────────
os.makedirs(os.path.join(WS, "productions", "demo-a"), exist_ok=True)
with io.open(os.path.join(WS, "productions", "demo-a", "build.svrun"), "w", encoding="utf-8") as f:
    f.write("<run/>")
with io.open(os.path.join(WS, "productions", "demo-a", "demo.svml"), "w", encoding="utf-8") as f:
    f.write("<doc/>")
check("4b 给工程目录也能解析到 .svrun",
      hb._pick_svrun("productions/demo-a").replace(chr(92), "/").endswith("productions/demo-a/build.svrun"), True)
check("4c 给 .svml 也能跳到同名 .svrun",
      hb._pick_svrun(os.path.join(WS, "productions", "demo-a", "demo.svml")).endswith("build.svrun"), True)
check("4d 越界路径不解析", hb._pick_svrun("C:" + chr(92) + "Windows" + chr(92) + "x.svrun"), "")
check("4e 不存在的工程不解析", hb._pick_svrun("productions/nope-xyz"), "")
check("4f 没工程/没会话时明说，不去起进程",
      [hb.studio_up("session-NO-PROJECT-AT-ALL")["reason"], hb.studio_up("")["reason"]], ["no-project", "no-session"])
busy = hb._free_studio_port()
import socket as _sk
_hole = _sk.socket(_sk.AF_INET6, _sk.SOCK_STREAM)
_hole.bind(("::1", busy))
_hole.listen(1)
check("4g 已被占的端口不会再分配（不抢别人正在服务的查看器）", hb._free_studio_port() != busy, True)
_hole.close()

# 4h 🔴 整段窗口被历史查看器占满时不能再回 0（2026-10-03 实测：5179..5190 全占 ⇒ 新会话点了 skill 起不了查看器）
import socket as _sk2
_saved = hb.STUDIO_PORT
_holes = []
try:
    _base = _sk2.socket(_sk2.AF_INET6, _sk2.SOCK_STREAM)
    _base.bind(("::1", 0))
    hb.STUDIO_PORT = int(_base.getsockname()[1])
    _base.close()
    for _k in range(3):
        _h = _sk2.socket(_sk2.AF_INET6, _sk2.SOCK_STREAM)
        _h.bind(("::1", hb.STUDIO_PORT + _k))
        _h.listen(1)
        _holes.append(_h)
    _got = hb._free_studio_port(span=3)
    _probe = _sk2.socket(_sk2.AF_INET6, _sk2.SOCK_STREAM)
    try:
        _probe.bind(("::1", _got))
        _bindable = True
    except OSError:
        _bindable = False
    finally:
        _probe.close()
    check("4h 窗口占满 ⇒ 回一个窗口外的空闲口（不是 0），且真能 bind",
          [_got not in range(hb.STUDIO_PORT, hb.STUDIO_PORT + 3), _got > 0, _bindable], [True, True, True])
finally:
    for _h in _holes:
        _h.close()
    hb.STUDIO_PORT = _saved
shutil.rmtree(os.path.join(WS, "productions", "demo-a"), ignore_errors=True)

# ── ⑤ 孤儿预扣：只列 + 指名回收（绝不自动退，自动退会伤到别的进程在途的花费）────────
LEDGER = os.environ["GATEWAY_LEDGER"]
if os.path.exists(LEDGER):
    os.remove(LEDGER)
import gateway as gw                                            # noqa: E402  （已带隔离路径）

led = gw.ledger()
h1 = led.preauth("hypit", "video.generate", "seedance-2.0", 50)["hold_id"]
h2 = led.preauth("hypit", "video.generate", "seedance-2.0", 50)["hold_id"]
led.preauth(" somebody-else", "video.generate", "omni-t2v", 40)
pend = hb.holds_pending("hypit")
check("5 只列本泳道的未结算预扣", [len(pend), sum(p["amount"] for p in pend)], [2, 100])
check("5b 全量列出来时包含别人的（所以不能盲退）", len(hb.holds_pending()), 3)
rb = hb.holds_rollback(h1)
check("5c 指名回滚一笔（走 Ledger，退款流水一起写）", [rb.get("ok"), rb.get("refunded")], [True, 50])
check("5d 剩两笔：另一条 hypit + 别人的", [p["hold_id"] for p in hb.holds_pending("hypit")], [h2])
check("5e 重复回收同一笔会被拒（不双退）", hb.holds_rollback(h1).get("ok"), False)
hb.holds_rollback(h2)

# ── ⑥ preflight：重启 8100 前必查的那个数（2026-10-02 我就是没查它，带着 3 条在途腿把服务杀了）
con = hb._db()
con.execute("INSERT INTO jobs(id,model,state,route,created,updated)"
            " VALUES('j-live','seedance-2-mini','running','x/y',?,?)", (time.time(), time.time()))
con.commit(); con.close()
out = subprocess.run([sys.executable, r"D:\video-skill\backend\hypit_bridge.py", "preflight"],
                     capture_output=True, text=True, encoding="utf-8", errors="replace",
                     cwd=r"D:\video-skill", env={**os.environ, "HYPIT_JOBS_DB": DB,
                                                 "GATEWAY_LEDGER": LEDGER}).stdout
check("6 有在途腿 ⇒ preflight 说不许重启", json.loads(out)["safe_to_restart"], False)
con = hb._db(); con.execute("DELETE FROM jobs WHERE id='j-live'")
con.execute("DELETE FROM jobs WHERE state IN ('queued','running')")   # 前面步骤留下的排队行也清掉
con.commit(); con.close()
out2 = json.loads(subprocess.run([sys.executable, r"D:\video-skill\backend\hypit_bridge.py", "preflight"],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace",
                                 cwd=r"D:\video-skill", env={**os.environ, "HYPIT_JOBS_DB": DB,
                                                             "GATEWAY_LEDGER": LEDGER}).stdout)
check("6b 清空后放行，且把遗留预扣数也报出来", [out2["active_jobs"], out2["safe_to_restart"]], [0, True])

# ── ⑦ 回落链：腿没带 session（agent 起 build 前没导出 HYPIT_SESSION_ID）也要能捞到 ──
hb.studio_claim("session-CLAIM-ONLY", r"C:\dsh\hypit_runs\productions\nose-cream\nose.svrun", 5179)
p7 = hb.jobs_progress("session-CLAIM-ONLY")
check("7 无会话标 ⇒ 按 claim 的工程回落，且标明是这么匹配上的",
      [p7["known"], p7["matched_by"], p7["summary"]["total"]], [True, "project", 2])
check("7b 既没会话也没工程 ⇒ 老实说没有", hb.jobs_progress("session-完全没关系")["known"], False)

for f in (DB, LEDGER, hb.INTENT_MARK, hb.STUDIO_MARK):
    try:
        os.remove(f)
    except OSError:
        pass
print("\n=== %d 条断言，失败 %d ===" % (total[0], len(fails)))
for n, g, w in fails:
    print("  FAIL", n, "got", g, "want", w)
sys.exit(1 if fails else 0)
