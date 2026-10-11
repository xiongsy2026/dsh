#!/usr/bin/env python
"""常驻 8100（模型管理台 = 右栏页签的判定口）自愈闸。

默认【只读】：不杀进程、不改任务，只核三件事——
  A 启动脚本的守卫是"HTTP 探针"而不是"netstat 端口在不在"
    （2026-10-04 实测：端口活着但没人答话，看门狗照样判成功 exit 0，页签全灭却查不到死因）
  B 计划任务确实每 1 分钟兜一次、且不会重复起
  C 现在这个 8100 真的在答判定口的话（presence 台键齐全，名单读 hypit_bridge.PRESENCE_KEYS）
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


# ── 存活守卫的判据（A 组与 W 组共用一份，不在闸里再抄一遍规则）──
import importlib.util as _ilu                                            # noqa: E402
_gspec = _ilu.spec_from_file_location("svc8100guard", r"D:\video-skill\svc_8100_guard.py")
G = _ilu.module_from_spec(_gspec)
_gspec.loader.exec_module(G)

# ---- A 启动脚本 ----
raw = open(CMD, "rb").read()
check("A1 启动脚本纯 ASCII（中文注释会被 GBK 控制台炸）", sum(1 for b in raw if b > 127), 0)
check("A2 CRLF 行尾且无裸 LF", raw.count(b"\r\n") > 0 and raw.count(b"\n") == raw.count(b"\r\n"), True)
txt = raw.decode("ascii")
code_only = "\n".join(l for l in txt.splitlines() if not l.strip().startswith("rem"))
check("A3 守卫不再拿 netstat 当'在服务'", "netstat" in code_only, False)
_GUARD = io.open(r"D:\video-skill\svc_8100_guard.py", encoding="utf-8", errors="replace").read()
_gcode = "\n".join(l for l in _GUARD.splitlines() if not l.strip().startswith("#"))
# 🔴 A4/A5/A6 原来钉的是 .cmd 里那两行 curl 守卫。2026-10-11 判据搬进 svc_8100_guard.py
#    （.cmd 只负责叫守卫），所以这三条改成钉搬家后的同一件事 —— 意图一条没丢：
#    还是"必须用 HTTP 探针、不许拿端口当在服务、探针通过才算数、解释器只一支"。
check("A4 守卫是 HTTP 探针（urllib 打 8100 自己的口，连续 TRIES 次才算死），不是端口在不在",
      [bool(re.search(r"urllib\.request\.urlopen\(HEALTH_URL", _gcode)),
       "127.0.0.1:%d" % 8100 in _gcode or 'BASE = "http://127.0.0.1' in _gcode,
       G.TRIES >= 3, "netstat" in _gcode],
      [True, True, True, True])
check("A5 探针 netstat 只用来问『谁在监听』（判 WEDGED 要它），绝不当『在服务』的证据",
      [bool(re.search(r"LISTENING", _gcode)), "classify" in _gcode,
       G.classify([False, False, False], 555) == "WEDGED"], [True, True, True])
check("A6 解释器只一支：.cmd 用 .venv 起守卫，守卫用【自己的 sys.executable】起 model_admin（不混两代 python）",
      [bool(re.search(r'\.venv\\Scripts\\pythonw\.exe" "svc_8100_guard\.py', code_only)),
       # 🔴 钉的是"用哪个解释器"这件事，不是当年那个局部变量名（2026-10-11 把它内联进 Popen 实参后，
       #    原来那条 `py = sys\.executable` 就扫不到了 —— 规则没变、形状变了，就该改探针而不是改回代码）
       bool(re.search(r"Popen\(\[[^\]]*sys\.executable", _gcode)),
       bool(re.search(r"backend\", \"model_admin\.py", _gcode) or re.search(r'os\.path\.join\(HERE, "backend", "model_admin\.py"\)', _gcode))],
      [True, True, True])
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
# 🔴 名单从权威源读（hypit_bridge.PRESENCE_KEYS），不在这里再抄一份：
#    上一版这里写死 ["film","hypit","omni"]，加了 prompt-reverse/motion-transfer 两档后它一直假红。
sys.path.insert(0, r"D:\video-skill")
sys.path.insert(0, r"D:\video-skill\backend")
try:
    import hypit_bridge as _hb
    _want_keys = sorted(_hb.PRESENCE_KEYS)
except Exception as e:
    _want_keys = None
    print("      （import hypit_bridge 失败：%s）" % type(e).__name__)
check("C4 判定口的台键 = 权威名单（omni/hypit/film/prompt-reverse/motion-transfer）",
      sorted(pres.keys()), _want_keys)
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

# ── W 组：存活守卫 svc_8100_guard.py（2026-10-11 现网"端口 LISTENING 但整个 8100 不答话"）──
check("W1 classify 三态真值表（有一次应答=HEALTHY；没人应答+端口有人=WEDGED；没人监听=DOWN）",
      [G.classify([True], 555), G.classify([False, False, False], 555),
       G.classify([False], None), G.classify([], None)],
      ["HEALTHY", "WEDGED", "DOWN", "DOWN"])
check("W2 只杀【命令行里确实是 model_admin】的占用者（白名单，不是黑名单）",
      [G.should_kill("pythonw.exe backend/model_admin.py"), G.should_kill("python app.py"),
       G.should_kill(""), G.should_kill("PYW~1.EXE BACKEND\\MODEL_ADMIN.PY")], [True, False, False, True])
check("W3 探针判'活'=拿到任何 HTTP 应答（404 也算）——拿 200 当活会每分钟杀健康服务",
      [G.answered.__doc__ is not None, "except urllib.error.HTTPError" in
       io.open(r"D:\video-skill\svc_8100_guard.py", encoding="utf-8", errors="replace").read()],
      [True, True])
if http("/api/model/caps")[0] == 200:
    _keep = G.HEALTH_URL
    try:
        G.HEALTH_URL = "http://127.0.0.1:8100/api/health"        # 这一条真回 404
        w3_live = G.answered()
    finally:
        G.HEALTH_URL = _keep
    G.HEALTH_URL = "http://127.0.0.1:1/api/model/caps"           # 没人听 ⇒ 必须判"没应答"
    w3_dead = G.answered()
    G.HEALTH_URL = _keep
    check("W3b 真跑对照：404 那一条算活着、拒连那一条算死了", [w3_live, w3_dead], [True, False])
else:
    print("SKIP W3b（8100 现在没答话 ⇒ 不做在线对照，闸自己不许变成故障点）")
_gsrc = io.open(r"D:\video-skill\svc_8100_guard.py", encoding="utf-8", errors="replace").read()
_ens = _gsrc[_gsrc.index("def ensure("):_gsrc.index("if __name__")]
check("W4 🔴 ensure 里绝不【调用】schtasks —— .cmd 就是任务动作，ensure 再 /run 是自我递归"
      "（2026-10-11 实测：第二层 guard 把刚起的那份 /end 掉，表现是报 RESTARTED-OK 然后端口空着）",
      ['subprocess.run(["schtasks"' in _ens,
       'subprocess.run(["schtasks"' in _gsrc[_gsrc.index("def task_restart("):_gsrc.index("def kill_wedged")]],
      [False, True])
_cmd = io.open(CMD, encoding="utf-8", errors="replace").read()
check("W5 启动脚本只负责叫守卫（判据不留在 .cmd 里，也不留第二条 starter）",
      ["svc_8100_guard.py" in _cmd, "curl.exe" in _cmd, "model_admin.py" in _cmd], [True, False, False])
_ma = io.open(r"D:\video-skill\backend\model_admin.py", encoding="utf-8", errors="replace").read()
check("W6 handler 有空闲 timeout（HTTP/1.1 keep-alive 不超时 = 连接只增不减，攒到积压打满就拒连）",
      [bool(re.search(r"class H\(BaseHTTPRequestHandler\)[\s\S]{0,600}?\n    timeout = \d+", _ma)),
       G.TIMEOUT > 0 and G.TRIES >= 3], [True, True])
check("W7 bind 失败必须退非 0（不留「活着但一个端口都没有」的僵尸；.venv 壳会再生一个子进程，别按进程数判双实例）",
      [bool(re.search(r"except OSError as e:[\s\S]{0,400}?sys\.exit\(3\)", _ma)), _ma.count("sys.exit(3)")],
      [True, 1])
_st = subprocess.run([sys.executable, r"D:\video-skill\svc_8100_guard.py", "status"],
                     capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
try:
    _sj = json.loads(_st.stdout.strip().splitlines()[-1])
except Exception:
    _sj = {}
check("W8 status 子命令：状态与退出码对得上（0=HEALTHY 1=WEDGED 2=DOWN，看门狗靠它判）",
      [_sj.get("state") == {0: "HEALTHY", 1: "WEDGED", 2: "DOWN"}.get(_st.returncode),
       _sj.get("port")], [True, 8100])

# ── W9（2026-10-11 23:53 现网：8100 从 21:31 之后一直 DOWN，首页四张技能封面全成碎图标）──
# 死因不是"端口被占"也不是"服务起不来"，是【看门狗被自己的日志锁死】：
#   _spawn_direct() 把服务进程的 stdout/stderr 指到守卫自己那份 _svc_8100.err.log（任务那一跳又 2>> 同一个文件），
#   长期活着的服务继承了那份句柄 ⇒ 下一次真要重启时 open(LOG,"a") 撞 PermissionError ⇒ ensure 每分钟崩在裸 open 上。
#   "只有要重启才走的那条路"恰好是唯一会碰这份日志的路 ⇒ 平时一行都不红，一出事就是永久死锁。
_gsrc = io.open(r"D:\video-skill\svc_8100_guard.py", encoding="utf-8", errors="replace").read()
import ast as _ast                                                      # noqa: E402
_gt = _ast.parse(_gsrc)


def _fseg(name):
    n = next((x for x in _gt.body if isinstance(x, _ast.FunctionDef) and x.name == name), None)
    return _ast.get_source_segment(_gsrc, n) or ""


def _fcode(name):
    """函数体【剥掉 docstring】再扫 —— 我自己的注释里就写着当年那行 `open(LOG,"a")`，
    拿整段源码做"不许出现"这类断言会被自己的说明文字喂红（这次 W9 第一次跑就是这么红的）。"""
    n = next((x for x in _gt.body if isinstance(x, _ast.FunctionDef) and x.name == name), None)
    seg = _fseg(name)
    if n and n.body and isinstance(n.body[0], _ast.Expr) \
            and isinstance(n.body[0].value, _ast.Constant) and isinstance(n.body[0].value.value, str):
        ls = seg.splitlines()
        del ls[n.body[0].lineno - n.lineno: n.body[0].end_lineno - n.lineno + 1]
        seg = "\n".join(ls)
    return seg


check("W9 🔴 守卫自己的日志不许被它看着的进程握：_spawn_direct 里没有裸 open(、"
      "_log_sink 的候选名单里不许出现守卫那份 LOG（当年就是这两处叠成死锁）",
      [bool(_fcode("_spawn_direct")), bool(_fcode("_log_sink")),
       not re.search(r"(?<![A-Za-z])open\(", _fcode("_spawn_direct")),
       not re.search(r"(?<![A-Z_])LOG\b", _fcode("_log_sink")),
       "SERVICE_LOG" in _fcode("_log_sink")],
      [True, True, True, True, True])
check("W9a 反向对照：W9 那两条「不许出现」不是靠没读到东西 —— 同一段源码【连着 docstring】扫必须扫得出 LOG",
      [bool(_fseg("_log_sink")), bool(re.search(r"(?<![A-Z_])LOG\b", _fseg("_log_sink")))], [True, True])
_TMPD = os.path.join(os.environ.get("TEMP") or "/tmp", "svc8100-gate")
os.makedirs(_TMPD, exist_ok=True)
_OG = (G.SERVICE_LOG, G.subprocess, G.LOG)
try:
    G.LOG = os.path.join(_TMPD, "guard.log")     # 🔴 桩跑出来的那句"已拉起"绝不许写进生产日志（假成功样本）
    G.SERVICE_LOG = os.path.join(_TMPD, "no-such-dir", "svc.log")      # 两条候选都开不了
    try:
        _h1, _w1 = G._log_sink()
        _raised1 = ""
    except Exception as e:                                             # noqa: BLE001
        _h1, _w1, _raised1 = None, "", type(e).__name__
    check("W9b 日志彻底不可用 ⇒ 退到 DEVNULL 而【不抛】（宁可无声，不可不起；抛出来就是这次那种每分钟崩）",
          [_raised1, _h1 == subprocess.DEVNULL, isinstance(_w1, str)], ["", True, True])

    G.SERVICE_LOG = _TMPD                                              # 第一条撞 PermissionError、第二条可建
    _h2, _w2 = G._log_sink()
    check("W9c 主路径不可开时必须【换名字再试一次】而不是放弃：拿到真句柄、落到带时间戳的旁路",
          [hasattr(_h2, "write"), _w2.startswith("svc8100-gate."), _w2 != "svc8100-gate"],
          [True, True, True])
    try:
        _h2.close()
    except OSError:
        pass

    _seen = {}

    class _Sp:
        DEVNULL = subprocess.DEVNULL

        @staticmethod
        def Popen(argv, **kw):
            _seen["argv"] = list(argv)
            _seen["stdout"] = kw.get("stdout")
            return type("P", (), {"pid": 1})()

    G.subprocess = _Sp
    _r1 = G._spawn_direct()
    check("W9d 真跑 _spawn_direct（Popen 打桩，绝不真起第二个实例）：报 direct-spawn、"
          "起的确实是 backend/model_admin.py、stderr 落的是服务专用那份而不是守卫日志",
          [_r1, os.path.basename(_seen["argv"][-1]), _seen["stdout"] is not subprocess.DEVNULL,
           "svc8100-gate" in str(getattr(_seen["stdout"], "name", ""))],
          ["direct-spawn", "model_admin.py", True, True])

    try:                                   # 桩留下的句柄要先放掉，否则 Windows 删不掉（W9z 会红）
        if hasattr(_seen.get("stdout"), "close"):
            _seen["stdout"].close()
    except OSError:
        pass

    class _SpBoom(_Sp):
        @staticmethod
        def Popen(argv, **kw):
            raise OSError("spawn blocked")

    G.subprocess = _SpBoom
    G.SERVICE_LOG = os.path.join(_TMPD, "no-such-dir", "svc.log")   # 这一档只要 DEVNULL，别再留文件
    try:
        _r2 = G._spawn_direct()
        _raised2 = ""
    except Exception as e:                                             # noqa: BLE001
        _r2, _raised2 = "", type(e).__name__
    check("W9e 连 Popen 都炸时也只【报出来】，不许把守卫带走（守卫一死，这条自愈线就整条没了）",
          [_raised2, _r2], ["", "direct-spawn-failed:OSError"])

    _old_shape = "def _log_sink():\n    return open(LOG, 'a'), 'main'\n"
    _ns = {"open": open, "LOG": _TMPD}
    exec(compile(_old_shape, "old-shape", "exec"), _ns)
    try:
        _ns["_log_sink"]()
        _raised3 = ""
    except OSError as e:
        _raised3 = type(e).__name__
    check("W9f 反向对照·钉的是规则不是当年的修法：把 sink 换回【裸 open 守卫自己那份日志】的旧形状，"
          "同一份不可开的输入必须抛 ⇒ 证明 W9b 退路真的在起作用（而不是恒真断言）",
          [_raised3], ["PermissionError"])
finally:
    G.SERVICE_LOG, G.subprocess, G.LOG = _OG
    # 测试残留清干净：旁路日志（svc8100-gate.<ts>）与沙盒目录都不许留下
    _left = []
    for _p in os.listdir(os.environ.get("TEMP") or "/tmp"):
        if _p.startswith("svc8100-gate."):
            try:
                os.remove(os.path.join(os.environ.get("TEMP") or "/tmp", _p))
            except OSError:
                _left.append(_p)
    for _p in ("guard.log",):
        try:
            os.remove(os.path.join(_TMPD, _p))
        except OSError:
            _left.append(_p)
    try:
        os.rmdir(_TMPD)
    except OSError:
        _left.append("dir:" + os.path.basename(_TMPD))
    check("W9z 收尾：这一组起的桩文件与沙盒目录全部清掉（残留会让下一次 W9c 拿到旧句柄）", _left, [])

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
