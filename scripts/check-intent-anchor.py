"""锚点回归：omni 意图必须由「建 run」这个必然动作自动钉上，不指望 agent 记得跑 intent。

为什么要有这条：2026-10-02 实测线上意图登记簿里 0 条是 agent 自己登记的 ⇒ "该弹的不弹"。
契约见 docs/ADR-workbench-presence.md「第四次修订」。

隔离：VIDEO_UI_RUNS 和 HYPIT_INTENT_MARK 全指到 _scratch_b，不碰线上 run 目录和登记簿。
跑法：PYTHONIOENCODING=utf-8 python scripts/check-intent-anchor.py
"""
import json
import os
import shutil
import sys

SCRATCH = r"C:\dsh\_scratch_b"
RUNS = os.path.join(SCRATCH, "anchor_runs")
INTENT = os.path.join(SCRATCH, "anchor_intent.json")
os.makedirs(SCRATCH, exist_ok=True)
shutil.rmtree(RUNS, ignore_errors=True)
if os.path.exists(INTENT):
    os.remove(INTENT)

os.environ["VIDEO_UI_RUNS"] = RUNS                    # store.py 的 run 根目录
os.environ["HYPIT_INTENT_MARK"] = INTENT              # hypit_bridge 的意图登记文件
sys.path.insert(0, r"C:\dsh\scratch\video-ui\video-ui")   # 常驻 8765 跑的就是这一份
sys.path.insert(0, r"D:\video-skill\backend")

import hypit_bridge as hb                             # noqa: E402
import store                                          # noqa: E402

hb._probe_omni = lambda: True                         # 不撞真 8765
fails, total = [], [0]


def check(name, got, want):
    total[0] += 1
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name, "=>", json.dumps(got, ensure_ascii=False)[:120])
    if not ok:
        fails.append((name, got, want))


def refresh_runs():
    hb._RUNS_CACHE = {"ts": 0.0, "rows": []}


S = "session-ANCHOR-1"

# ── 1 建 run 之前：什么都没登记 ⇒ 零 UI ────────────────────────────────────────────
check("1 建 run 前无意图 ⇒ 零 UI", hb.omni_status(S)["intent"], False)

# ── 2 建 run ⇒ 自动钉上 omni 意图（agent 一个字都没写）───────────────────────────────
run = store.create_run("锚点自测片", 10, "product", session=S)
refresh_runs()
j = hb.omni_status(S)
check("2 建完 run 自动有 omni 意图", j["intent"], True)
check("2b source 只可能是 explicit（run-tag 那档已废）", j["source"], "explicit")
check("2c 登记的就是这条 run", j["run"], run.name)
check("2d 台地址带上会话过滤", j["url"].endswith("session=" + S), True)
check("2e hypit 侧被排他挡掉（同会话只挂一个台）", hb.studio_status(S)["reason"], "intent-is-omni")

# ── 3 不带 session 的调用（老式/手工点 UI）不许凭空给谁钉意图 ─────────────────────────
before = len(hb.intent_view())
store.create_run("无会话锚点", 10)
check("3 不带 session ⇒ 登记簿不多任何一条", len(hb.intent_view()), before)

# ── 4 右栏台是附带品：登记挂了也绝不能让建 run 失败 ─────────────────────────────────
orig = hb.set_intent
hb.set_intent = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("登记簿炸了"))
try:
    r4 = store.create_run("登记失败也要能建片", 10, "product", session="session-ANCHOR-2")
    ok4 = os.path.isdir(r4.root)
except Exception as e:                    # noqa: BLE001
    ok4, r4 = False, e
hb.set_intent = orig
check("4 set_intent 抛异常时建 run 照样成功", ok4, True)

# ── 5 换会话不串：另一条会话不会继承这条的意图 ──────────────────────────────────────
refresh_runs()
check("5 别的会话仍然零 UI", hb.omni_status("session-ANCHOR-OTHER")["intent"], False)

shutil.rmtree(RUNS, ignore_errors=True)
if os.path.exists(INTENT):
    os.remove(INTENT)
print("\n=== %d 条断言，失败 %d ===" % (total[0], len(fails)))
for n, g, w in fails:
    print("  FAIL", n, "got", g, "want", w)
sys.exit(1 if fails else 0)
