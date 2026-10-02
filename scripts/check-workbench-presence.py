"""右栏工作台 presence 回归断言（契约：docs/ADR-workbench-presence.md）

两段：
  A 函数层 —— 打临时登记文件，不碰线上（15 条）
  B HTTP 层 —— 打常驻 8100 的真 presence 口（6 条），用完把自己塞进去的假主人摘干净

跑法：PYTHONIOENCODING=utf-8 python scripts/check-workbench-presence.py
退出码非 0 = 有断言挂。改 hypit_bridge 的归属/presence 逻辑必须先过这里。
"""
import json
import os
import subprocess
import time
import sys
import urllib.error
import urllib.request

os.environ["HYPIT_STUDIO_MARK"] = r"C:\dsh\_scratch_b\mark_selftest.json"
os.makedirs(os.path.dirname(os.environ["HYPIT_STUDIO_MARK"]), exist_ok=True)
sys.path.insert(0, r"D:\video-skill")
from backend import hypit_bridge as hb   # noqa: E402

GATEWAY = "http://127.0.0.1:8100/api/hypit/studio"
RUN = "productions/hypit-e2e/assemble.svrun"     # 5179 上那台 studio 真在服务的工程
ME = "session-PRESENCE-SELFTEST"

fails, total = [], [0]


def check(name, got, want):
    total[0] += 1
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name, "=>", json.dumps(got, ensure_ascii=False)[:150])
    if not ok:
        fails.append((name, got, want))


# ══ A 函数层 ═══════════════════════════════════════════════════════════════════════
with open(hb.STUDIO_MARK, "w", encoding="utf-8") as f:      # 旧格式=只增不减那版
    json.dump({"sessions": ["session-A", "session-B"], "session": "session-B",
               "run": "hypit-e2e", "port": 5179, "ts": os.path.getmtime(__file__)}, f)
check("A1 旧格式一律判无主", hb.studio_status("session-A")["reason"], "not-owner")
check("A2 不带 session 不回 up", hb.studio_status()["up"], False)

# A 段全程离线：探针桩提前装好，别去撞真的 5179（B 段才打真服务）
hb._probe_studio = lambda port: {"up": True, "tasks": 0, "running": 0, "artifacts": 0}
hb._studio_served_path = lambda port: "productions\\hypit-e2e\\assemble.svml"


hb.studio_claim("session-A", RUN, 5179)
check("A3 登记按 run 建索引", sorted(json.load(open(hb.STUDIO_MARK, encoding="utf-8"))["runs"]),
      ["productions/hypit-e2e"])
# ⚠️ 语义变更（2026-10-02 续）：同一个 run 再 claim = 【加一个观看者】，不是换主人。
#    旧版"会话级全局只增不减"是 bug 根因，但矫枉过正成"一个 run 只能一个主人"会让
#    多窗口共看同一台 studio 直接看不到台（实测 3 个会话在轮询）⇒ 归属粒度=（run, 会话）对。
hb.studio_claim("session-C", RUN, 5179)
check("A4 同 run 多人共看（名单累加）",
      json.load(open(hb.STUDIO_MARK, encoding="utf-8"))["runs"]["productions/hypit-e2e"]["sessions"],
      ["session-A", "session-C"])
check("A4b 两个主人会话都判 up", [hb.studio_status(s).get("up") for s in ("session-A", "session-C")],
      [True, True])
check("A4c 第三个会话仍 not-owner", hb.studio_status("session-D")["reason"], "not-owner")
hb.studio_release("session-A")
check("A4d release 只摘自己，另一个还在",
      json.load(open(hb.STUDIO_MARK, encoding="utf-8"))["runs"]["productions/hypit-e2e"]["sessions"],
      ["session-C"])
check("A4e 被摘的那个不再看得到", hb.studio_status("session-A")["reason"], "not-owner")
hb.studio_claim("session-C", RUN, 5179)
check("A5 空 session 直接拒", "error" in hb.studio_claim("", RUN, 5179), True)

# 心跳：右栏每 4 秒的探测要能把 ts 顶上去，否则一条 30 分钟的 build 会被 TTL 误杀
def age_runs(seconds):
    rec = json.load(open(hb.STUDIO_MARK, encoding="utf-8"))
    for v in rec["runs"].values():
        v["ts"] -= seconds
    with open(hb.STUDIO_MARK, "w", encoding="utf-8") as f:
        json.dump({"runs": rec["runs"], "ts": 0}, f)


age_runs(600)                                       # 10 分钟没心跳（仍在 30 分钟 TTL 内）
hb.studio_status("session-C")                       # 打一次 presence = 顶一次心跳
rec = json.load(open(hb.STUDIO_MARK, encoding="utf-8"))
check("A5b 探测会把心跳 ts 顶回来（长任务不被 TTL 误杀）",
      all(time.time() - v["ts"] < 60 for v in rec["runs"].values()), True)
age_runs(hb.STUDIO_CLAIM_TTL + 1)                   # 真的超过 TTL
check("A5c 心跳停超 TTL → 判无主", hb.studio_status("session-C")["reason"], "not-owner")
hb.studio_claim("session-C", RUN, 5179)


check("A6 svrun<->svml 同工程算匹配", hb._run_matches(RUN, "productions\\hypit-e2e\\assemble.svml"), True)
check("A7 裸工程名走 basename 兜底", hb._run_matches("hypit-e2e", "productions\\hypit-e2e\\assemble.svml"), True)
check("A8 别的工程判死", hb._run_matches("productions/futie-replica/clone.svrun",
                                        "productions\\hypit-e2e\\assemble.svml"), False)

rec = json.load(open(hb.STUDIO_MARK, encoding="utf-8"))
for v in rec["runs"].values():
    v["ts"] -= hb.STUDIO_CLAIM_TTL + 1
with open(hb.STUDIO_MARK, "w", encoding="utf-8") as f:
    json.dump({"runs": rec["runs"], "ts": 0}, f)
check("A9 TTL 过期→not-owner", hb.studio_status("session-C")["reason"], "not-owner")
check("A10 过期项读时已落盘回收", json.load(open(hb.STUDIO_MARK, encoding="utf-8"))["runs"], {})

hb.studio_claim("session-A", "productions/a/x.svrun", 5179)
hb.studio_claim("session-B", "productions/b/y.svrun", 5179)
hb.studio_release("session-A")
check("A11 release 只摘自己名下的", sorted(json.load(open(hb.STUDIO_MARK, encoding="utf-8"))["runs"]),
      ["productions/b"])

hb.studio_claim("session-B", "productions/b/y.svrun", 5179)
hb._probe_studio = lambda port: {"up": True, "tasks": 0, "running": 0, "artifacts": 0}
hb._studio_served_path = lambda port: "productions\\other\\z.svml"
check("A12 studio 自报别的 run→判死", hb.studio_status("session-B")["reason"], "run-mismatch")
hb._studio_served_path = lambda port: "productions\\b\\y.svml"
check("A13 主人+匹配+活→up", [hb.studio_status("session-B").get(k) for k in ("up", "run_verified")],
      [True, True])
hb._studio_served_path = lambda port: ""       # 生成工程 /__studio/session 回 500
check("A14 拿不到自报 run 时退回只认登记", [hb.studio_status("session-B").get(k) for k in ("up", "run_verified")],
      [True, False])
check("A15 非主人仍然 false", hb.studio_status("session-Z")["reason"], "not-owner")
if os.path.exists(hb.STUDIO_MARK):
    os.remove(hb.STUDIO_MARK)

# ══ B HTTP 层（真 8100 + 真 studio）════════════════════════════════════════════════
# ⚠️ 这段会真写线上登记文件 D:\video-skill\.gateway_studio.json（8100 读的就是它），
#    用完只摘自己那一条；顺带把旧格式的僵尸主人覆盖掉——这是有意的，别当副作用。
REAL_MARK = r"D:\video-skill\.gateway_studio.json"


def cli(*args):
    env = {k: v for k, v in os.environ.items() if k != "HYPIT_STUDIO_MARK"}   # CLI 必须写 8100 读的那份
    return subprocess.run([sys.executable, r"D:\video-skill\backend\hypit_bridge.py", *args],
                          capture_output=True, text=True, cwd=r"D:\video-skill", env=env)


def mark_runs():
    try:
        with open(REAL_MARK, encoding="utf-8") as f:
            return json.load(f).get("runs") or {}
    except (OSError, ValueError):
        return {}


def http(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"up": None, "reason": "gateway-unreachable:" + type(e).__name__}


if http(GATEWAY).get("reason") != "ops-view":
    print("\n!! 8100 没在跑或不是新版 presence 口，跳过 HTTP 段")
    fails.append(("B0 网关可达", http(GATEWAY).get("reason"), "ops-view"))
else:
    before = mark_runs()
    check("B1 不带 session 只回运维视图", http(GATEWAY)["reason"], "ops-view")
    check("B2 陌生会话 not-owner", http(GATEWAY + "?session=session-NOT-MINE")["reason"], "not-owner")

    cli("studio-claim", ME, RUN, "5179")
    mine = http(GATEWAY + "?session=" + ME)
    check("B3 主人拿到 up", mine.get("up"), True)
    check("B4 交叉核对生效（studio 自报路径对上）", mine.get("run_verified"), True)
    check("B5 端口由 presence 口给（换端口不用改插件）", mine.get("port"), 5179)

    cli("studio-release", ME)         # 只摘自己，别人名下的 run 不动
    after = mark_runs()
    check("B6 自测登记已清干净", [k for k, v in after.items() if v.get("session") == ME], [])
    check("B7 没动到别人的登记", sorted(set(before) - set(after)), [])

print("\n=== %d 条断言，失败 %d ===" % (total[0], len(fails)))
for name, got, want in fails:
    print("  FAIL", name, "got", got, "want", want)
sys.exit(1 if fails else 0)
