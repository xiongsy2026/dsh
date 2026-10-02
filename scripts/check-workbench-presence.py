"""右栏 presence 回归断言（契约：docs/ADR-workbench-presence.md）

规则只有一条：**台跟着技能意图走**。
  intent（这条会话表过态要做哪条流水线，持久）决定页签弹不弹；
  up（引擎现在活着）只决定台里显示什么。新会话两条意图都没有 ⇒ 什么都不弹。

两段：
  A 函数层 —— 临时登记文件 + 临时 video_runs + 探针桩，全离线（不撞 5179/8765、不碰线上）
  B HTTP 层 —— 打常驻 8100 的真判定口，用完把自己塞进去的假主人摘干净

跑法：PYTHONIOENCODING=utf-8 python scripts/check-workbench-presence.py
退出码非 0 = 有断言挂。改归属/presence 逻辑必须先过这里。
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

SCRATCH = r"C:\dsh\_scratch_b"
os.makedirs(SCRATCH, exist_ok=True)
os.environ["HYPIT_STUDIO_MARK"] = os.path.join(SCRATCH, "mark_selftest.json")
# ⚠️ 意图文件也必须隔离：studio_claim 会顺手写意图，早先没隔离时把 5 条假会话写进了线上
#    .gateway_intent.json（就是"绝不往生产状态写假数据"那条老坑）。
os.environ["HYPIT_INTENT_MARK"] = os.path.join(SCRATCH, "intent_selftest.json")
sys.path.insert(0, r"D:\video-skill")
from backend import hypit_bridge as hb   # noqa: E402

RUN = "productions/hypit-e2e/assemble.svrun"
ME = "session-PRESENCE-SELFTEST"
GATEWAY = "http://127.0.0.1:8100/api/workbench/presence"

fails, total = [], [0]


def check(name, got, want):
    total[0] += 1
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name, "=>", json.dumps(got, ensure_ascii=False)[:150])
    if not ok:
        fails.append((name, got, want))


def mark():
    return json.load(open(hb.STUDIO_MARK, encoding="utf-8"))["runs"]


# ── 离线桩：A 段绝不碰真端口 ────────────────────────────────────────────────────
ALIVE = {"studio": True, "omni": True}
SERVED = {"path": "productions\\hypit-e2e\\assemble.svml"}
hb._probe_studio = lambda port: dict(up=ALIVE["studio"], tasks=0, running=0, artifacts=0)
hb._probe_omni = lambda: ALIVE["omni"]
hb._studio_served_path = lambda port: SERVED["path"]

# omni 的意图来自盘上的 run ⇒ 给一个临时 runs 目录
for _p in (os.environ["HYPIT_INTENT_MARK"], os.environ["HYPIT_STUDIO_MARK"]):
    if os.path.exists(_p):
        os.remove(_p)                # 上一轮残留会让这一轮的排他判定莫名其妙
FAKE_RUNS = os.path.join(SCRATCH, "fake_video_runs")
shutil.rmtree(FAKE_RUNS, ignore_errors=True)
hb.VIDEO_RUNS = FAKE_RUNS


def refresh_runs():
    hb._RUNS_CACHE = {"ts": 0.0, "rows": []}


def make_run(name, session, approved=False):
    d = os.path.join(FAKE_RUNS, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "run.json"), "w", encoding="utf-8") as fh:
        json.dump({"session": session, "review": {"script_approved": approved}}, fh)


# ══ A1 hypit 意图 ════════════════════════════════════════════════════════════════
with open(hb.STUDIO_MARK, "w", encoding="utf-8") as f:      # 旧格式：会话级只增不减那一版
    json.dump({"sessions": ["session-A", "session-B"], "session": "session-B",
               "run": "hypit-e2e", "port": 5179, "ts": time.time()}, f)
check("A1 旧格式一律判无意图", hb.studio_status("session-A")["reason"], "no-intent")
check("A1b 不带 session 不回 up", hb.studio_status()["up"], False)

hb.studio_claim("session-A", RUN, 5179)
check("A2 登记按 run 建索引", sorted(mark()), ["productions/hypit-e2e"])
hb.studio_claim("session-C", RUN, 5179)
check("A3 同 run 多人共看（名单累加，不是换主人）",
      mark()["productions/hypit-e2e"]["sessions"], ["session-A", "session-C"])
check("A4 两个主人会话都判 up", [hb.studio_status(s).get("up") for s in ("session-A", "session-C")],
      [True, True])
check("A5 第三会话无意图", hb.studio_status("session-D")["reason"], "no-intent")
hb.studio_release("session-A")
check("A6 release 只摘自己，另一个还在", mark()["productions/hypit-e2e"]["sessions"], ["session-C"])
# ⚠️ 语义变更（铁律三修）：studio-claim 会顺手把该会话钉成 hypit 意图，而 release 只撤 run 归属、
#    不撤意图 ⇒ 摘掉 claim 后是 not-claimed（台还挂、提示怎么起），要 no-intent 必须显式 intent-clear。
check("A7 摘掉 claim 但意图还在 ⇒ not-claimed", hb.studio_status("session-A")["reason"], "not-claimed")
hb.clear_intent("session-A")
check("A7b 显式撤销意图后才真的 no-intent", hb.studio_status("session-A")["reason"], "no-intent")
check("A8 空 session / 空 run 都拒",
      ["error" in hb.studio_claim("", RUN, 5179), "error" in hb.studio_claim("session-X", "", 5179)],
      [True, True])

# ══ A8b 🔴 裸跑 release 不许抹全局（2026-10-02 实际踩过：漏会话 id ⇒ 归属登记一次清光）══
hb.studio_claim("session-D", "productions/other/x.svrun", 5179)     # 第二个 run，用来验"不动别人"
before8 = sorted(mark())
r0 = hb.studio_release()                                            # 等价于 CLI 裸跑 studio-release
check("A8b 无参 release 被拒（回 error，不静默清空）", "error" in r0, True)
check("A8c 拒的时候一份登记都没动", sorted(mark()) == before8, True)
check("A8d 报错里带当前归属，让人知道该摘谁", "session-C" in json.dumps(r0, ensure_ascii=False), True)
hb.studio_release("session-D")
check("A8e 按会话释放照旧生效（名单空了才撤那个 run）", sorted(mark()), ["productions/hypit-e2e"])
r_all = hb.studio_release(all_sessions=True)      # 清空 = 整份文件删掉（生产里那次"文件不见了"就是这个）
check("A8f 只有显式 all_sessions 才允许整份清空",
      [r_all.get("scope"), (not os.path.exists(hb.STUDIO_MARK)) or mark() == {}], ["all", True])
hb.studio_claim("session-C", RUN, 5179)                             # 还给 A9 用

# ══ A9 意图是持久的：超时只影响"活着"那一层，不再撤销页签 ══════════════════════════
rec = mark()
for v in rec.values():
    v["ts"] -= hb.STUDIO_CLAIM_TTL + 1                     # 一天没人看
with open(hb.STUDIO_MARK, "w", encoding="utf-8") as f:
    json.dump({"runs": rec, "ts": 0}, f)
r = hb.studio_status("session-C")
check("A9 心跳超 TTL 仍算有意图（旧版在这撤销 ⇒ 老会话什么都不弹）", r["intent"], True)
check("A9b 台里照常显示引擎在跑", (r["up"], r.get("reason", "")), (True, ""))
ALIVE["studio"] = False
r = hb.studio_status("session-C")
check("A9c studio 死了：intent 保住、up 掉", (r["intent"], r["up"], r["reason"]),
      (True, False, "studio-down"))
check("A9d 死了也要告诉人怎么起", "hypit studio --run" in (r.get("how_to_start") or ""), True)
ALIVE["studio"] = True

# ══ A10 登记封顶：只裁最旧的，不裁"过期的" ════════════════════════════════════════
hb.studio_mark_clear_all()
for i in range(hb.STUDIO_INTENT_KEEP + 5):
    hb.studio_claim("session-many", "productions/r%02d/x.svrun" % i, 5179)
    rec = mark()
    for k, v in rec.items():
        v["ts"] = 1000 + int(k.rsplit("r", 1)[-1][:2])      # 造出明确的新旧序
    with open(hb.STUDIO_MARK, "w", encoding="utf-8") as f:
        json.dump({"runs": rec, "ts": 0}, f)
kept = sorted(mark())
check("A10 登记封顶生效", len(kept) <= hb.STUDIO_INTENT_KEEP, True)
check("A10b 裁掉的是最旧那批（r00 不在了）", any("r00" in k for k in kept), False)
hb.studio_mark_clear_all()

# ══ A11 交叉核对：studio 自报它在服务哪个 run ══════════════════════════════════════
hb.studio_claim("session-B", "productions/b/y.svrun", 5179)
SERVED["path"] = "productions\\other\\z.svml"
check("A12 自报别的 run → 台里提示 run-mismatch", hb.studio_status("session-B")["reason"], "run-mismatch")
check("A12b 但意图仍在（该弹还是弹）", hb.studio_status("session-B")["intent"], True)
SERVED["path"] = "productions\\b\\y.svml"
check("A13 匹配则 up 且 verified",
      [hb.studio_status("session-B").get(k) for k in ("up", "run_verified")], [True, True])
SERVED["path"] = ""                                          # 生成工程 /__studio/session 会回 500
check("A14 拿不到自报 run 时退回只认登记",
      [hb.studio_status("session-B").get(k) for k in ("up", "run_verified")], [True, False])

# ══ A15 omni：意图 = 显式登记；盘上的 run 只提供显示字段 ════════════════════════
# 🔴 2026-10-02 定稿：痕迹不再触发页签。旧写法（"盘上有带本会话标的 run 就算意图"）
#    因为 run 不过期，会让一条几小时前的 run 永久霸占右栏——实测事故：装了个纯提示词
#    skill，切回会话却在弹 omni 台。这等于替用户猜意图，砍掉。
make_run("20261001-aaa", "session-OMNI-1", approved=True)
time.sleep(0.02)
make_run("20261001-bbb", "session-OMNI-1")
make_run("20260930-old", None)                                # 老 run 没打标
refresh_runs()
check("A15 有本会话的 run、但没登记 ⇒ 不挂台（痕迹不当触发器）",
      hb.omni_status("session-OMNI-1")["intent"], False)
hb.set_intent("session-OMNI-1", "omni", "回归用")
o = hb.omni_status("session-OMNI-1")
check("A15a 登记后挂台", o["intent"], True)
check("A15b 台地址带上会话过滤", o["url"].endswith("session=session-OMNI-1"), True)
check("A15c 关口只作为信息（ready），不作为弹的条件", "ready" in o, True)
check("A15d 登记后取最新的 run 显示", o["run"], "20261001-bbb")
check("A15e 没表过态的会话无意图", hb.omni_status("session-NEVER")["reason"], "no-intent")
check("A15f source 只会是 explicit（run-tag 那档已废）", o["source"], "explicit")
check("A15g 老 run 没打标 ⇒ 不在本会话的 run 清单里（可见性靠标，不靠猜）",
      "20260930-old" not in [r["run"] for r in hb._runs_of_session("session-OMNI-1")], True)
ALIVE["omni"] = False
o = hb.omni_status("session-OMNI-1")
check("A15h video-ui 没起：意图在、up 掉", (o["intent"], o["up"]), (True, False))
ALIVE["omni"] = True

# ══ A16 判定口一次给两台 ══════════════════════════════════════════════════════════
hb.studio_claim("session-OMNI-1", RUN, 5179)
both = hb.workbench_presence("session-OMNI-1")
check("A16 一个口同时回答 omni+hypit", sorted(both), ["hypit", "omni"])
# 🔴 铁律三修推翻了旧断言（原来写"同会话跨两条流水线都弹=合法"）：显式登记是【排他】的，
#    claim 顺手钉了 hypit 意图 ⇒ omni 必须不挂。要回 omni 就重新 intent omni。
check("A16b 登记排他：说了 hypit 就不给 omni 台",
      [both["omni"]["intent"], both["hypit"]["intent"]], [False, True])
check("A16c omni 侧理由可诊断", both["omni"]["reason"], "intent-is-hypit")
hb.set_intent("session-OMNI-1", "omni", "切回电商线")
back = hb.workbench_presence("session-OMNI-1")
check("A16d 改登记即换台（排他可逆）", [back["omni"]["intent"], back["hypit"]["intent"]], [True, False])

# ══ B HTTP 层（真 8100 + 真 studio）═══════════════════════════════════════════════
# ⚠️ 这段会真写线上登记文件（8100 读的就是它），用完只摘自己那一条。
REAL_MARK = r"D:\video-skill\.gateway_studio.json"


def cli(*args):
    drop = ("HYPIT_STUDIO_MARK", "HYPIT_INTENT_MARK")
    env = {k: v for k, v in os.environ.items() if k not in drop}   # CLI 要写 8100 读的那两份
    return subprocess.run([sys.executable, r"D:\video-skill\backend\hypit_bridge.py", *args],
                          capture_output=True, text=True, cwd=r"D:\video-skill", env=env)


def http(url):
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"omni": {"reason": "gateway-unreachable:" + type(e).__name__},
                "hypit": {"reason": "gateway-unreachable"}}


def real_runs():
    try:
        with open(REAL_MARK, encoding="utf-8") as f:
            return json.load(f).get("runs") or {}
    except (OSError, ValueError):
        return {}


if http(GATEWAY).get("hypit", {}).get("reason") != "ops-view":
    print("\n!! 8100 没在跑或判定口不是新版，跳过 HTTP 段")
    fails.append(("B0 网关可达", http(GATEWAY).get("hypit", {}).get("reason"), "ops-view"))
else:
    before = real_runs()
    j = http(GATEWAY)
    check("B1 不带 session 只回运维视图", [j["omni"]["reason"], j["hypit"]["reason"]], ["ops-view", "ops-view"])
    check("B2 陌生会话两台都无意图",
          [http(GATEWAY + "?session=session-NOT-MINE")[k]["intent"] for k in ("omni", "hypit")], [False, False])

    cli("studio-claim", ME, RUN, "5179")
    mine = http(GATEWAY + "?session=" + ME)["hypit"]
    check("B3 主人拿到 up", mine.get("up"), True)
    check("B4 交叉核对生效（studio 自报路径对上）", mine.get("run_verified"), True)
    check("B5 端口由判定口给", mine.get("port"), 5179)

    cli("studio-release", ME)
    cli("intent-clear", ME)          # release 只撤 run 归属；意图要显式清，否则自测会话留在生产文件里
    after = real_runs()
    left = [k for k, v in after.items() if ME in ([str(x) for x in (v.get("sessions") or [])] + [str(v.get("session" or ""))])]
    check("B6 自测登记已清干净", left, [])
    check("B7 没动到别人的登记", sorted(set(before) - set(after)), [])

shutil.rmtree(FAKE_RUNS, ignore_errors=True)
if os.path.exists(hb.INTENT_MARK):
    os.remove(hb.INTENT_MARK)
if os.path.exists(hb.STUDIO_MARK):
    os.remove(hb.STUDIO_MARK)

print("\n=== %d 条断言，失败 %d ===" % (total[0], len(fails)))
for name, got, want in fails:
    print("  FAIL", name, "got", got, "want", want)
sys.exit(1 if fails else 0)
