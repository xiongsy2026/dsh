"""进度台数据面回归：生成腿必须自带「工程 + 会话」身份，账本能按会话聚出段状态（全程 0 分、不联网）。

契约见 docs/ADR-workbench-presence.md「身份进数据面」。跑法：
  PYTHONIOENCODING=utf-8 python scripts/check-hypit-progress.py
"""
import io
import json
import os
import sys

SCRATCH = r"C:\dsh\_scratch_b"
DB = os.path.join(SCRATCH, "progress_selftest.sqlite")
WS = r"C:\dsh\hypit_runs"        # 用真工作区根：_project_key 只做 realpath/relpath，不写盘
os.makedirs(SCRATCH, exist_ok=True)
assert os.path.isdir(os.path.join(WS, "productions", "nose-cream")), "工作区根/工程目录得真实存在"
if os.path.exists(DB):
    os.remove(DB)
os.environ["HYPIT_JOBS_DB"] = DB
os.environ["HYPIT_WORKSPACE"] = WS
os.environ["HYPIT_INTENT_MARK"] = os.path.join(SCRATCH, "progress_intent.json")
os.environ["HYPIT_STUDIO_MARK"] = os.path.join(SCRATCH, "progress_studio.json")
sys.path.insert(0, r"D:\video-skill\backend")
import hypit_bridge as hb                                       # noqa: E402

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

# ── ④ 判定口把进度带上（台才有内容可画，哪怕 studio 没起）───────────────────────────
hb.set_intent(SID, "hypit", "复刻 video_1")
hb._probe_studio = lambda port: {"up": False, "error": "ConnectionRefusedError"}
v = hb.workbench_presence(SID)["hypit"]
check("4 studio 没起也带得上进度", [v["up"], v.get("progress", {}).get("known")], [False, True])
check("4b 进度里能看到段状态", sorted(x["state"] for x in v["progress"]["segments"]),
      ["failed", "succeeded"])
check("4c 意图仍是唯一触发器（无登记的会话不带进度）",
      hb.workbench_presence("session-PROGRESS-OTHER")["hypit"]["intent"], False)

for f in (DB, hb.INTENT_MARK, hb.STUDIO_MARK):
    try:
        os.remove(f)
    except OSError:
        pass
print("\n=== %d 条断言，失败 %d ===" % (total[0], len(fails)))
for n, g, w in fails:
    print("  FAIL", n, "got", g, "want", w)
sys.exit(1 if fails else 0)
