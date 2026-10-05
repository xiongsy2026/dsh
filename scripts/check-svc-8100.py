#!/usr/bin/env python
"""常驻 8100（模型管理台 = 右栏页签的判定口）自愈闸。

默认【只读】：不杀进程、不改任务，只核三件事——
  A 启动脚本的守卫是"HTTP 探针"而不是"netstat 端口在不在"
    （2026-10-04 实测：端口活着但没人答话，看门狗照样判成功 exit 0，页签全灭却查不到死因）
  B 计划任务确实每 1 分钟兜一次、且不会重复起
  C 现在这个 8100 真的在答判定口的话（presence 三档齐全）
--selfheal 才会杀掉服务、计时看它多久回来（会制造 ~20 秒右栏空窗，先跑 preflight）。
"""
import io
import json
import os
import re
import subprocess
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
CMD = r"D:\video-skill\start_model_admin.cmd"
SELFHEAL = "--selfheal" in sys.argv
out = []


def check(name, got, want):
    ok = json.dumps(got, ensure_ascii=False, default=str) == json.dumps(want, ensure_ascii=False, default=str)
    out.append((ok, name))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else " => %r (期望 %r)" % (got, want)))


def ps(body):
    return subprocess.run(["powershell.exe", "-NoProfile", "-Command", body],
                          capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()


def http(path, timeout=3.0):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8100" + path, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, type(e).__name__


# ---- A 启动脚本 ----
raw = open(CMD, "rb").read()
check("A1 启动脚本纯 ASCII（中文注释会被 GBK 控制台炸）", sum(1 for b in raw if b > 127), 0)
check("A2 CRLF 行尾且无裸 LF", raw.count(b"\r\n") > 0 and raw.count(b"\n") == raw.count(b"\r\n"), True)
txt = raw.decode("ascii")
code_only = "\n".join(l for l in txt.splitlines() if not l.strip().startswith("rem"))
check("A3 守卫不再拿 netstat 当'在服务'", "netstat" in code_only, False)
check("A4 守卫是 HTTP 探针（-f 让 4xx/5xx 也算失败）", bool(re.search(r"curl\.exe -fs .*127\.0\.0\.1:8100/api/model/caps", code_only)), True)
check("A5 探针通过才 exit 0", bool(re.search(r"&& exit /b 0", code_only)), True)
check("A6 起的是 venv 解释器 + backend/model_admin.py", bool(re.search(r'\.venv\\Scripts\\pythonw\.exe" "backend\\model_admin\.py', code_only)), True)
check("A7 崩溃有落盘证据（stderr 进 err.log）", "_svc_8100.err.log" in code_only, True)

# ---- B 计划任务 ----
info = ps("$t=Get-ScheduledTask -TaskName WeeksirModelAdmin;" +
          "$r=@();foreach($x in $t.Triggers){$r+=$x.Repetition.Interval};$s=$t.Settings;" +
          "'{0}|{1}|{2}' -f ($r -join ','), $s.MultipleInstances, $s.ExecutionTimeLimit")
intervals, multi, limit = (info.split("|") + ["", "", ""])[0:3]
check("B1 时间触发重复间隔 = PT1M（原来是 PT5M ⇒ 最长 5 分钟盲区）", "PT1M" in intervals, True)
check("B2 MultipleInstances=IgnoreNew（健康时不会叠第二份）", multi, "IgnoreNew")
check("B3 ExecutionTimeLimit=PT0S（不限时，长任务不会被任务引擎掐）", limit, "PT0S")

# ---- B4/B5 隐藏执行（2026-10-05：那条 1 分钟看门狗每分钟闪一个控制台窗，客户桌面被闪）----
# 规则钉在"动作指向哪"，不钉"窗口有没有出现"（后者没法自动断言）。守卫逻辑仍只在 .cmd 一处，
# vbs 只负责隐藏窗口 + 等子进程（保住 IgnoreNew 的"一次只重启一份"）+ 转发退出码。
VBS = r"D:\video-skill\run_hidden.vbs"
acts = ps("$r=@();foreach($n in 'WeeksirModelAdmin','WeeksirVideoUI'){$a=(Get-ScheduledTask -TaskName $n).Actions[0];" +
          "$r+=$a.Execute+'|'+$a.Arguments}; $r -join ';'")
lines = [l for l in acts.split(";") if l.strip()]
hidden = [l for l in lines if "wscript.exe" in l.lower() and "run_hidden.vbs" in l.lower()]
direct = [l for l in lines if re.search(r"cmd(\.exe)?\|", l, re.I) or l.lower().rstrip("|").endswith(".cmd")]
check("B4 两条看门狗都走隐藏执行、没有一条还直接跑 .cmd（直接跑=每分钟闪一个窗）",
      [len(lines), len(hidden), len(direct)], [2, 2, 0])
check("B4b 隐藏执行仍各自指回原来那条 .cmd（守卫没被搬进 vbs，不留第二个入口）",
      [any("start_model_admin.cmd" in l for l in hidden), any("start_video_ui.cmd" in l for l in hidden)], [True, True])
vraw = open(VBS, "rb").read() if os.path.isfile(VBS) else b""
vcode = "\n".join(l for l in vraw.decode("ascii", "replace").splitlines() if not l.strip().startswith("'"))
check("B5 vbs 纯 ASCII（中文注释在 ANSI 脚本宿主里会 mojibake）", sum(1 for b in vraw if b > 127), 0)
check("B5b vbs 用 style 0 且 wait=True（不等待的话每分钟都会叠一份重启尝试）",
      bool(re.search(r",\s*0,\s*True\s*\)", vcode)), True)
check("B5c vbs 把子进程退出码转出去（LastTaskResult 才有意义，不又是假保险）",
      "WScript.Quit rc" in vcode, True)

# ---- C 现在真的在答话 ----
st, body = http("/api/model/caps")
check("C1 /api/model/caps 回 200", st, 200)
check("C2 且是新表（含音频腿 audio.generate）", "audio.generate" in body, True)
st2, body2 = http("/api/workbench/presence?session=gate-check-probe")
check("C3 presence 判定口在答话", st2, 200)
try:
    pres = json.loads(body2)
except Exception:
    pres = {}
check("C4 三档台都在（omni/hypit/film）", sorted(pres.keys()), ["film", "hypit", "omni"])
h = pres.get("hypit") or {}
check("C5 无意图档字段齐（intent/up/reason）", all(k in h for k in ("intent", "up", "reason")), True)
check("C5b 无意图的 reason 说真话，不甩锅给别人", h.get("reason"), "no-intent")
check("C5c 旧 claim 只当显示（owners 在、但不因它弹台）", isinstance(h.get("owners"), list), True)
film = pres.get("film") or {}
check("C5d 引擎活着≠有意图：film 只能 up 不 intent", (film.get("up"), film.get("intent")), (True, False))
# 有意图的那条会话必须把端口和工程一起回出来 —— 少一个，客户端就会回落兜底端口去看【别人的工程】
sid = ""
try:
    intents = json.load(open(r"C:\dsh\.dsh\intent.json", encoding="utf-8"))
    cand = sorted(((v.get("ts") or 0, k) for k, v in intents.items() if isinstance(v, dict) and v.get("skill") == "hypit"),
                  reverse=True)
    sid = cand[0][1] if cand else ""
except Exception:
    sid = ""
if sid:
    st3, body3 = http("/api/workbench/presence?session=" + sid)
    hh = (json.loads(body3).get("hypit") or {}) if st3 == 200 else {}
    # 钉的是【规则】不是【现场】：up 为真才要求 port/run 齐；studio 没起时 up=false 是合法态。
    # 原来这里写死 (True,True,True)，于是同一道闸会在"查看器恰好没开着"时假红一次（3 次里中过 1 次）。
    rule_ok = (not hh.get("up")) or (bool(hh.get("port")) and bool(hh.get("run")))
    check("C5e 有意图会话: up 为真 ⇒ 必须同时回 port 与 run（客户端不许猜端口）", rule_ok, True)
    print("      C5e 现场: up=%r port=%r run=%r（studio 没起 ⇒ 这条只验规则）"
          % (hh.get("up"), hh.get("port"), hh.get("run")))
else:
    print("SKIP C5e（工作区意图簿里没有 hypit 登记）")
check("C6 无意图的会话不许谎报 up", h.get("up"), False)
listeners = ps("(Get-NetTCPConnection -State Listen -LocalPort 8100 -ErrorAction SilentlyContinue).OwningProcess | " +
               "Sort-Object -Unique")
check("C7 8100 只有一个监听者（两份进程=以后查'跑哪版'必翻车）",
      len([x for x in listeners.splitlines() if x.strip()]), 1)

if SELFHEAL:
    print("\n--selfheal：杀掉服务进程，计时看 1 分钟触发多久把它拉回来（右栏会空窗 ~20 秒）")
    print("  杀之前:", http("/api/model/caps")[0])
    ps("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'model_admin' -and $_.Name -like 'python*' }" +
       " | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    import time
    t0 = time.time()
    up = None
    for _ in range(40):
        time.sleep(3)
        if http("/api/model/caps")[0] == 200:
            up = round(time.time() - t0)
            break
    check("L1 自愈成功（≤90 秒）", up is not None and up <= 90, True)
    print("   恢复耗时 %s 秒" % up)

bad = [n for ok, n in out if not ok]
print("\n=== 8100 自愈闸：%d 条断言，失败 %d ===" % (len(out), len(bad)))
sys.exit(1 if bad else 0)
