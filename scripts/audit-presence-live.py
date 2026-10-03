"""活会话审计：把 DSH 真实会话逐条打判定口，核对"严格匹配"是否成立（只读，不写登记）。

数据源三份，互相对账：
  ① DSH 会话目录   %D%/harness/sessions/--C-dsh--/session-*            ← 谁是真会话
  ② 意图登记簿     D:/video-skill/.gateway_intent.json                 ← 谁表过态
  ③ 盘上痕迹       video_runs/*/run.json 的 session、.gateway_studio.json 的 owners ← 谁跑过
判定口 8100 的 verdict 必须满足：弹哪个台 == 显式登记；没登记 ⇒ 两台都不弹（痕迹不算数）；任何会话最多一个台。

跑法：PYTHONIOENCODING=utf-8 python scripts/audit-presence-live.py [要审计的工作区目录名]
"""
import glob
import io
import json
import os
import sys
import time
import urllib.request

DSH_SESSIONS = r"C:\Users\Administrator\AppData\Roaming\dsh-desktop\harness\sessions"
GW = "http://127.0.0.1:8100/api/workbench/presence?session="
VIDEO_RUNS = os.environ.get("VIDEO_UI_RUNS", r"C:\dsh\video_runs")
STUDIO_MARK = r"D:\video-skill\.gateway_studio.json"
INTENT_MARK = r"D:\video-skill\.gateway_intent.json"
SCOPE = sys.argv[1] if len(sys.argv) > 1 else "--C-dsh--"


def http_json(url):
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}")
    except Exception:                       # noqa: BLE001
        return None


def load(p, default):
    try:
        return json.load(io.open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return default


# ① 真会话（按最后活动时间排序，最近的排前面）
sids = []
for d in glob.glob(os.path.join(DSH_SESSIONS, SCOPE, "session-*")):
    files = glob.glob(os.path.join(d, "**", "*"), recursive=True)
    last = max((os.path.getmtime(f) for f in files if os.path.isfile(f)), default=0)
    sids.append((last, os.path.basename(d)))
sids.sort(reverse=True)

# ② 意图登记簿
intent = load(INTENT_MARK, {})

# ③ 痕迹：omni 的 run 标 + hypit 的 claim 主人
omni_traces = {}
for rj in glob.glob(os.path.join(VIDEO_RUNS, "*", "run.json")):
    st = load(rj, {})
    s = st.get("session")
    if s:
        row = omni_traces.setdefault(s, {"runs": 0, "approved": 0, "last": 0})
        row["runs"] += 1
        row["approved"] += 1 if (st.get("review") or {}).get("script_approved") else 0
        row["last"] = max(row["last"], os.path.getmtime(rj))
hypit_traces = {}
for run, v in load(STUDIO_MARK, {}).get("runs", {}).items():
    for owner in (v.get("sessions") or ([v.get("session")] if v.get("session") else [])):
        hypit_traces.setdefault(owner, []).append(run)

print("会话数 %d（工作区 %s）｜登记簿 %d 条｜omni 痕迹 %d 个会话｜hypit 痕迹 %d 个会话\n"
      % (len(sids), SCOPE, len(intent), len(omni_traces), len(hypit_traces)))
HDR = "%-26s %-12s %-10s │ %-22s %-22s │ %s"
print(HDR % ("会话", "登记意图", "应弹台", "omni 判定", "hypit 判定", "痕迹(不弹是对的)"))
print("-" * 132)

bad, rows = [], []
for last, sid in sids:
    v = http_json(GW + sid)
    if v is None:
        bad.append((sid, "判定口没回话（8100 挂了？）"))
        continue
    o, hp = v.get("omni") or {}, v.get("hypit") or {}
    sk = (intent.get(sid) or {}).get("skill") or "-"
    # 只认有台的 skill；未知/无引擎的 skill（如 film、prompt 库）应弹台=无，不许 KeyError
    should = {"omni": "流水线", "hypit": "Studio"}.get(sk) or "无"
    pops = [n for n, x in (("流水线", o), ("Studio", hp)) if x.get("intent")]
    fmt = lambda x: ("弹 up=%s" % ("Y" if x.get("up") else "N")) if x.get("intent") else "不弹 %s" % (x.get("reason") or "?")
    trace = "omni run×%d/approved%d · hypit claim×%d" % (
        (omni_traces.get(sid) or {}).get("runs", 0), (omni_traces.get(sid) or {}).get("approved", 0),
        len(hypit_traces.get(sid) or []))
    print(HDR % (sid[:26], sk, should, fmt(o), fmt(hp), trace))
    rows.append((sid, sk, pops, o, hp, trace))

print("\n" + "=" * 60 + "\n核对结论")
print("-" * 60)
n = 0
for sid, sk, pops, o, hp, trace in rows:
    expect = [{"omni": "流水线", "hypit": "Studio"}.get(sk)] if sk in ("omni", "hypit") else []
    if pops != expect:
        n += 1
        print("  ✗ %s：登记=%s 却弹出 %s（应为 %s）" % (sid, sk, pops or "无台", expect or "无台"))
    if len(pops) > 1:
        n += 1
        print("  ✗ %s：同时挂两个台 %s ⇒ 排他失效" % (sid, pops))
    has_trace = "run×0" not in trace or "claim×1" in trace
    if not pops and has_trace and "-" in sk:
        print("  · %s：有痕迹无登记 ⇒ 已按 intent-only 不弹（对）" % sid)
for sid, why in bad:
    n += 1
    print("  ✗ %s：%s" % (sid, why))
print("  %s：全部 %d 条会话的弹台结果与登记严格一致（排他 + 痕迹不触发）"
      % ("✓" if n == 0 else "✗ 共 %d 处不符" % n, len(rows)))
sys.exit(1 if n else 0)
