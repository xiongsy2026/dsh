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
os.environ["HYPIT_WORKSPACE_INTENT"] = os.path.join(SCRATCH, "ws_intent_selftest.json")
os.environ["HYPIT_NO_AUTOSTART"] = "1"          # 自测绝不真起 studio 进程
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
shutil.rmtree(FAKE_RUNS, ignore_errors=True)
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

# ── ⑧ 写盘撞锁不许静默丢登记（Windows 上 os.replace 偶发 WinError 5，实测会一次绿一次炸）──
hb.clear_intent(S)
real_replace, calls = hb.os.replace, []


def flaky(src, dst):
    calls.append(1)
    if len(calls) < 3:
        raise PermissionError(13, "被索引器占用")
    return real_replace(src, dst)


hb.os.replace = flaky
r = hb.set_intent(S, "omni", "撞锁重试")
hb.os.replace = real_replace
check("8 前两次撞锁第三次成功 ⇒ 登记照旧写入（返回里不该有 error）",
      ["error" in r, len(calls), hb.intent_of(S)], [False, 3, "omni"])


def always_locked(src, dst):
    raise PermissionError(13, "一直占用")


hb.os.replace = always_locked
r2 = hb.set_intent(S, "hypit", "一直撞锁")
hb.os.replace = real_replace
check("8b 一直撞锁 ⇒ 明确回 error，不假装成功", ["error" in r2, hb.intent_of(S)], [True, "omni"])

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

# ── ⑧ 工作区侧登记（2026-10-03）：DSH 沙箱写不了 D 盘，意图必须能落在工作区内 ──────
WS_MARK = hb.WORKSPACE_INTENT                   # 已在头部指到 scratch，绝不碰真的 .dsh/intent.json


def ws_write(sid, skill, task="", ts=None):
    d = {}
    if os.path.exists(WS_MARK):
        try:
            d = json.load(open(WS_MARK, encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
    d[sid] = {"skill": skill, "task": task, "ts": ts if ts is not None else __import__("time").time()}
    json.dump(d, open(WS_MARK, "w", encoding="utf-8"), ensure_ascii=False)


ws_write("session-WS-1", "hypit", "productions/p1/build.svrun")
check("8 只写工作区文件也算显式登记（沙箱里跑得动的那条路）", hb.intent_of("session-WS-1"), "hypit")
st = hb.studio_status("session-WS-1")
check("8b 工作区登记能决定页签（intent=True，不是 no-intent）", [st.get("intent"), st.get("reason") != "no-intent"], [True, True])

hb.set_intent("session-WS-1", "omni", "另一条流水线")      # 主登记写一条更新的
check("8c 同一会话两边都有 ⇒ 取 ts 更新的那个（主登记刚写，赢）", hb.intent_of("session-WS-1"), "omni")
ws_write("session-WS-1", "hypit", "productions/p1/build.svrun", ts=__import__("time").time() + 500)
check("8d 工作区侧更新更晚 ⇒ 反过来赢（agent 刚点的那一下必须立刻生效）", hb.intent_of("session-WS-1"), "hypit")

ws_write("session-WS-2", "omni", "任务B")
check("8e 两条会话并发登记互不覆盖", [hb.intent_of("session-WS-1"), hb.intent_of("session-WS-2")], ["hypit", "omni"])

with open(WS_MARK, "w", encoding="utf-8") as f:
    f.write("{ 这不是合法 json")
hb._intent_cache.update(sig=None, data={})
check("8f 工作区登记文件坏了不炸、主登记照读", hb.intent_of("session-WS-1"), "omni")
os.remove(WS_MARK)
hb._intent_cache.update(sig=None, data={})

# 工程"目录"也要算数（2026-10-03 实测坑：只认 .svrun/.svml 后缀 ⇒ 文档写"可以传目录"是假的）
PROJ_DIR = os.path.join(SCRATCH, "proj_dir_selftest")
os.makedirs(PROJ_DIR, exist_ok=True)
ws_write("session-WS-DIR", "hypit", PROJ_DIR)
hb._intent_cache.update(sig=None, data={})
check("8g 意图给的是【工程目录】也算有工程（agent 老实给目录不该被判 no-project）",
      bool(hb._session_project("session-WS-DIR")), True)
ws_write("session-WS-TEXT", "hypit", "参考视频精准复刻")
hb._intent_cache.update(sig=None, data={})
check("8h 只给中文任务名仍然不算工程（该 no-project 就 no-project，不去猜哪个工程）",
      hb._session_project("session-WS-TEXT"), "")

# ── ⑨ viewer_tick：起查看器绑在轮询上，不绑在"agent 敲命令"上 ────────────────────
calls = []
real_up, real_tick = hb.studio_up, hb.VIEWER_TICK_COOLDOWN
_saved_autostart = os.environ.pop("HYPIT_NO_AUTOSTART", None)   # 这一段就是要验"会自起"，头部开关先摘掉


def slow_up(sid, run=""):                       # 真起查看器要等最长 15 秒应答，用它模拟
    import time as _t
    _t.sleep(0.8)
    calls.append(sid)
    return {"started": True, "port": 5199}


def wait_calls(n, secs=3.0):                   # 后台起进程 ⇒ 断言前等一下，别拿竞态当失败
    import time as _t
    end = _t.time() + secs
    while _t.time() < end and len(calls) < n:
        _t.sleep(0.05)
    return len(calls)


hb.studio_up = slow_up
hb._viewer_tick.clear()
hb._viewer_busy.clear()
hb._session_project = lambda sid: "productions/p1/build.svrun"
hb.set_intent("session-TICK-1", "hypit", "productions/p1/build.svrun")
t0 = __import__("time").time()
ret = hb.viewer_tick("session-TICK-1")
spent = __import__("time").time() - t0
check("9 判定口不背启动的债：立刻返回（<0.2s），起进程丢后台", spent < 0.2, True)
check("9a 返回体说清是「正在起」，不谎报 started", [ret.get("started"), ret.get("reason")], [False, "spawning"])
for _ in range(40):
    if calls:
        break
    __import__("time").sleep(0.05)
check("9b 后台确实把查看器起起来了", calls, ["session-TICK-1"])
hb.viewer_tick("session-TICK-1")
check("9c 冷却内不重复起（右栏每 5 秒轮询，不能把进程起爆）", len(calls), 1)
hb._viewer_tick.clear()
hb.viewer_tick("session-TICK-1")
check("9d 冷却到期后同一会话可以再试一次", wait_calls(2), 2)
hb.viewer_tick("session-OMNI-ONLY")
check("9e 没登记 hypit 的会话绝不起", len(calls), 2)
hb.VIEWER_TICK_COOLDOWN = 0
hb._viewer_tick.clear()
hb._session_project = lambda sid: ""
hb.viewer_tick("session-TICK-1")
wait_calls(3, 0.3)
check("9f 名下没工程就不起（去了也是 no-project，白起一个空查看器）", len(calls), 2)
hb._session_project = lambda sid: "productions/p1/build.svrun"
os.environ["HYPIT_NO_AUTOSTART"] = "1"
hb._viewer_tick.clear()
hb.viewer_tick("session-TICK-1")
wait_calls(3, 0.3)
check("9g 回归/测试环境可用开关彻底关掉自起", len(calls), 2)
del os.environ["HYPIT_NO_AUTOSTART"]

hb._session_project = lambda sid: ""          # 这条会话名下确实没有工程
st = hb.studio_status("session-TICK-1")
check("9h 有意图、无工程、无腿 ⇒ 明说 no-project（不再和『查看器没起』混成一句）",
      [st.get("state"), st.get("reason")], ["no-project", "no-project"])
hb._session_project = lambda sid: "productions/p1/build.svrun"
st = hb.studio_status("session-TICK-1")
check("9i 有意图、有工程、但查看器没起 ⇒ 仍是 not-claimed（两种情况必须分得开）",
      [st.get("state"), st.get("reason")], ["not-claimed", "not-claimed"])
hb.studio_up, hb.VIEWER_TICK_COOLDOWN = real_up, real_tick
if _saved_autostart:
    os.environ["HYPIT_NO_AUTOSTART"] = _saved_autostart     # 后面的套件继续靠它挡住真起进程

shutil.rmtree(FAKE_RUNS, ignore_errors=True)
shutil.rmtree(PROJ_DIR, ignore_errors=True)
for p in (hb.INTENT_MARK, hb.STUDIO_MARK, WS_MARK):
    if os.path.exists(p):
        os.remove(p)

shutil.rmtree(PROJ_DIR, ignore_errors=True)
# ── ⑩ 意图有效期：过期就不弹台，也不再被 viewer_tick 自动起查看器 ─────────────────
import time as _tm
_real_ttl = hb.INTENT_TTL_SEC
ws_write("session-TTL-OLD", "hypit", "productions/old/build.svrun", ts=_tm.time() - 25 * 3600)
ws_write("session-TTL-NEW", "hypit", "productions/new/build.svrun", ts=_tm.time() - 60)
hb._intent_cache.update(sig=None, data={})
check("10 超过 24 小时的登记不再算意图（零 UI）", hb.intent_of("session-TTL-OLD"), "")
check("10b 过期会话的判定口回 no-intent（不是弹一个空台）",
      [hb.studio_status("session-TTL-OLD").get(k) for k in ("intent", "reason")], [False, "no-intent"])
check("10c 一分钟前的登记照常算", hb.intent_of("session-TTL-NEW"), "hypit")
hb.INTENT_TTL_SEC = 30.0
hb._intent_cache.update(sig=None, data={})
check("10d TTL 可缩短（HYPIT_INTENT_TTL 覆盖，用于收紧策略）", hb.intent_of("session-TTL-NEW"), "")
hb.INTENT_TTL_SEC = _real_ttl
hb._intent_cache.update(sig=None, data={})
try:
    main_now = json.load(open(hb.INTENT_MARK, encoding="utf-8"))
except (OSError, ValueError):
    main_now = {}          # 前面的小节收尾时删过登记文件，这里从空开始写
main_now["session-TTL-STALE"] = {"skill": "omni", "task": "陈旧", "ts": _tm.time() - 999999}
json.dump(main_now, open(hb.INTENT_MARK, "w", encoding="utf-8"), ensure_ascii=False)
hb._intent_cache.update(sig=None, data={})
hb.set_intent("session-TTL-W", "omni", "写盘顺带清扫")
after = json.load(open(hb.INTENT_MARK, encoding="utf-8"))
check("10e 写盘时把过期条目清掉（登记簿不会只涨不落）", "session-TTL-STALE" in after, False)

_saved = os.environ.pop("HYPIT_NO_AUTOSTART", None)
calls2 = []
hb.studio_up = lambda sid, run="": calls2.append(sid)
hb._viewer_tick.clear()
hb._viewer_busy.clear()
hb._session_project = lambda sid: "productions/old/build.svrun"
hb.viewer_tick("session-TTL-OLD")
_tm.sleep(0.3)
check("10f 过期意图绝不自动起查看器（这才是 TTL 的真正动机）", calls2, [])
if _saved:
    os.environ["HYPIT_NO_AUTOSTART"] = _saved
hb._session_project = lambda sid: "productions/p1/build.svrun"

print("\n=== %d 条断言，失败 %d ===" % (total[0], len(fails)))
for n, g, w in fails:
    print("  FAIL", n, "got", g, "want", w)
sys.exit(1 if fails else 0)
