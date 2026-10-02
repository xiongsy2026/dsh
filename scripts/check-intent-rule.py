"""铁律回归：新会话零 UI，明确意图之后才挂台（排他）。

规则（docs/ADR-workbench-presence.md「意图铁律」）：
  有显式登记 ⇒ 只挂 skill 匹配的那一个台，其余一律不挂
  无显式登记 ⇒ 退到佐证（hypit=run 归属；omni=盘上有属于本会话的 run）
  两者都没有 ⇒ 新会话，零 UI
跑法：PYTHONIOENCODING=utf-8 python scripts/check-intent-rule.py
"""
import json
import os
import shutil
import sys

SCRATCH = r"C:\dsh\_scratch_b"
os.makedirs(SCRATCH, exist_ok=True)
os.environ["HYPIT_INTENT_MARK"] = os.path.join(SCRATCH, "intent_selftest.json")
os.environ["HYPIT_STUDIO_MARK"] = os.path.join(SCRATCH, "mark_selftest.json")
FAKE_RUNS = os.path.join(SCRATCH, "intent_runs")
sys.path.insert(0, r"D:\video-skill")
from backend import hypit_bridge as hb   # noqa: E402

# 全离线：不撞 5179/8765，不碰线上登记
hb._probe_studio = lambda p: {"up": True, "tasks": 0, "running": 0, "artifacts": 0}
hb._studio_served_path = lambda p: ""
hb._probe_omni = lambda: True
hb.VIDEO_RUNS = FAKE_RUNS

fails, total = [], [0]


def check(name, got, want):
    total[0] += 1
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name, "=>", json.dumps(got, ensure_ascii=False)[:120])
    if not ok:
        fails.append((name, got, want))


def both(sid):
    return [hb.omni_status(sid)["intent"], hb.studio_status(sid)["intent"]]


def fresh_runs():
    hb._RUNS_CACHE = {"ts": 0.0, "rows": []}


def make_run(name, session):
    d = os.path.join(FAKE_RUNS, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "run.json"), "w", encoding="utf-8") as f:
        json.dump({"session": session, "review": {"script_approved": True}}, f)


for p in (hb.INTENT_MARK, hb.STUDIO_MARK):
    if os.path.exists(p):
        os.remove(p)
shutil.rmtree(FAKE_RUNS, ignore_errors=True)
fresh_runs()

S = "session-FRESH-DEMO"

# ──  新会话：一条 UI 都不许出现 ────────────────────────────────────────────────
check("1 新会话零 UI（omni/hypit 都不挂）", both(S), [False, False])
check("1b 理由说得很直白", [hb.omni_status(S)["reason"], hb.studio_status(S)["reason"]],
      ["no-intent", "no-intent"])

# ──  明确登记 ⇒ 只挂那一个台 ───────────────────────────────────────────────────
hb.set_intent(S, "omni", "2026-10-02-短剧带货钩子")
check("2 登记 omni ⇒ 只挂 omni 台", both(S), [True, False])
check("2b hypit 侧给排他理由（不是含糊的 unknown）", hb.studio_status(S)["reason"], "intent-is-omni")
check("2c 登记了但还没建 run ⇒ 台照样挂（早期视图，不该逼人已产出才可见）",
      hb.omni_status(S)["run"], "")

# ── ③ 排他：换意图就换台，旧痕迹不能同时挂两个台 ────────────────────────────────
hb.set_intent(S, "hypit", "nose-cream")
check("3 改登记 hypit ⇒ 只剩 hypit 台", both(S), [False, True])
check("3b omni 侧理由", hb.omni_status(S)["reason"], "intent-is-hypit")
make_run("20261002-omni-run", S)          # 就算盘上留着 omni 的 run 也不该翻出来
fresh_runs()
check("3c 有 omni 的 run 也不翻案（显式登记优先）", both(S), [False, True])

# ── ④ 引擎动作自动钉意图（agent 忘了 set_intent 也不会漏）────────────────────────
hb.clear_intent(S)
hb.studio_claim(S, "productions/nose-cream/nose.svrun", 5179)
check("4 claim 即钉 hypit 意图", [hb.intent_of(S), both(S)], ["hypit", [False, True]])

# ── ⑤ 退到佐证：意图记录丢了也不至于功能消失 ────────────────────────────────────
hb.clear_intent(S)
check("5 清掉意图后两台都靠佐证撑着（omni=3c 造的那条 run、hypit=claim）", both(S), [True, True])
shutil.rmtree(FAKE_RUNS, ignore_errors=True)      # 把 3c 造的 omni run 撤掉
fresh_runs()
hb.studio_release(S)
check("5b 佐证也没了 ⇒ 回到零 UI（不是残留）", both(S), [False, False])

# ──  运维视图：谁挂着哪条流水线一眼可见 ────────────────────────────────────────
hb.set_intent("session-A", "omni", "t1")
hb.set_intent("session-B", "hypit", "t2")
view = {r["session"]: r["skill"] for r in hb.intent_view()}
check("6 运维视图列得出全部会话意图", [view.get("session-A"), view.get("session-B")], ["omni", "hypit"])
check("6b ops 视角的 omni 判定带 intents 清单", "intents" in hb.omni_status(None), True)

shutil.rmtree(FAKE_RUNS, ignore_errors=True)
for p in (hb.INTENT_MARK, hb.STUDIO_MARK):
    if os.path.exists(p):
        os.remove(p)

print("\n=== %d 条断言，失败 %d ===" % (total[0], len(fails)))
for n, g, w in fails:
    print("  FAIL", n, "got", g, "want", w)
sys.exit(1 if fails else 0)
