"""铁律回归：新会话零 UI，明确意图之后才挂台（排他）。

规则（docs/ADR-workbench-presence.md「意图铁律」·2026-10-02 定稿 intent-only）：
  有显式登记 ⇒ 只挂 skill 匹配的那一个台，其余一律不挂
  无显式登记 ⇒ 零 UI。盘上的旧 claim / 带本会话标的 run 只是显示用历史，永不触发页签
  （旧写法"退到佐证"会删掉一条会话的永久页签：67 分钟前跑过的 run 到现在还在弹）
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

# ── ⑤ intent-only：痕迹永不当触发器（取代旧的"退到佐证"）────────────────────────
# 为什么要砍：run 带会话标、claim 只增不减，两者都不过期 ⇒ 一条会话"跑过一次"就永久拥有页签。
# 2026-10-02 实测事故：装了个纯提示词 skill，切回会话却在弹 omni 台，就是这条兜底干的。
hb.clear_intent(S)
check("5 清掉意图 ⇒ 零 UI（盘上那条 omni run 和这个会话的旧 claim 都不算数）", both(S), [False, False])
st = hb.studio_status(S)
check("5b 旧 claim 不弹台，但要作为 stale_claim 摆给人看（可见≠拥有）",
      [st["reason"], bool(st.get("stale_claim"))], ["no-intent", True])
shutil.rmtree(FAKE_RUNS, ignore_errors=True)      # 把 3c 造的 omni run 撤掉
fresh_runs()
hb.studio_release(S)
check("5c 痕迹也清了 ⇒ 依旧零 UI", both(S), [False, False])

# ── ⑤b 纯提示词/知识库类 skill（不挂台的第 14~34 个）：登记了也不许乱挂 ──────────
hb.set_intent(S, "seedance-prompt-library", "GPT-5.8 生视频 Prompt 库")
check("5d 未知/无引擎的 skill ⇒ 两台都不挂，理由说清登记的是谁",
      [both(S), hb.omni_status(S)["reason"]], [[False, False], "intent-is-seedance-prompt-library"])

# ──  运维视图：谁挂着哪条流水线一眼可见 ────────────────────────────────────────
hb.set_intent("session-A", "omni", "t1")
hb.set_intent("session-B", "hypit", "t2")
view = {r["session"]: r["skill"] for r in hb.intent_view()}
check("6 运维视图列得出全部会话意图", [view.get("session-A"), view.get("session-B")], ["omni", "hypit"])
check("6b ops 视角的 omni 判定带 intents 清单", "intents" in hb.omni_status(None), True)

# ── ⑦ 泛化矩阵：规则不针对某条会话，只针对"登记的 skill 名" ────────────────────────
#    有台的 ⇒ 只弹它那一个；没台的（super-clone/product-video/film 和纯提示词类）⇒ 零台，
#    绝不许"没有对应台就随便弹一个"，也绝不许同时弹两个。
MATRIX = [
    ("omni", ["流水线"]), ("hypit", ["Studio"]),
    ("super-clone", []), ("product-video", []), ("film", []),
    ("seedance-prompt-library", []), ("ai-film-router", []), ("short-drama", []),
]
for sk, want in MATRIX:
    hb.set_intent(S, sk, "矩阵")
    got = [("流水线" if hb.omni_status(S)["intent"] else None),
           ("Studio" if hb.studio_status(S)["intent"] else None)]
    check("7 [%s] 只弹 %s（排他 + 没台就不弹）" % (sk, want or "零台"),
          [x for x in got if x], want)
# 换 skill = 覆盖：连续切换不能留下两个台
for sk in ("omni", "hypit", "film", "omni"):
    hb.set_intent(S, sk, "切换")
    live = sum(1 for f in (hb.omni_status, hb.studio_status) if f(S)["intent"])
    check("7x 切到 %s 后台数正好 1 或 0（不漏旧的）" % sk, live if sk != "film" else 0,
          1 if sk != "film" else 0)

shutil.rmtree(FAKE_RUNS, ignore_errors=True)
for p in (hb.INTENT_MARK, hb.STUDIO_MARK):
    if os.path.exists(p):
        os.remove(p)

print("\n=== %d 条断言，失败 %d ===" % (total[0], len(fails)))
for n, g, w in fails:
    print("  FAIL", n, "got", g, "want", w)
sys.exit(1 if fails else 0)
