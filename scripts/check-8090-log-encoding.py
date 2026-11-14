#!/usr/bin/env python
r"""8090（app.py）的日志编码与"盘上真值纠状态"回归闸。

跑法：python scripts/check-8090-log-encoding.py

起因（2026-10-06 实测事故）：结构性复刻 Phase1 已经跑完（`phase_state.json=after_step3`、
故事板海报 2.2MB 在盘上），却被父进程一句 `print(f"[SR-{task_id}] {msg}")` 打死 ——
Windows 控制台是 GBK，子进程回传的日志里一个 `✓`(U+2713) 就抛 UnicodeEncodeError，
外层 except 接住 ⇒ **标成 failed**。钱与时间都花了，待审页看不到、"通过"点不动。

闸钉四条规则（不是钉现场快照）：
 ① 父进程回显子进程日志必须走 `echo()`，一处裸 print 都不许留（同一失效面还有 one-shot/redo_shot/RM 三处）。
 ② `echo()` 在 GBK 流、以及 pythonw（stdout=None）下都不许抛 —— 并且要证明旧写法【真的会抛】（反向对照）。
 ③ "按盘上产物纠状态"只有一份实现，三处调用；只纠 failed，绝不许把 RUNNING 翻成跑完。
 ④ 历史页排序键必须同型（`created_at` 数字 + `task_id` 兜底混比 ⇒ TypeError ⇒ 整页 500）。

全离线：只【读】盘上产物与 task_store，不改任何任务状态、不发起任何生成、不碰线上账本。
"""
import json
import os
import re
import subprocess
import sys
WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")

APP_ROOT = BACKEND
APP_PY = os.path.join(APP_ROOT, "app.py")
SCRATCH_SR_DIR = os.path.join(APP_ROOT, "outputs", "sr_20261006_162002")   # 事故那条的产物（只读）

TOTAL = 0
fails = []


def _safe(s):
    """GBK 控制台上「⇒/🔴」会把 print 打崩 = 闸跑到一半没结果（最坏的假安静）。
    只按【输出流编码】做 errors=replace；绝不 sys.stdout.reconfigure（造"零输出+exit 0"的静默假绿）。"""
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        s.encode(enc)
        return s
    except (UnicodeEncodeError, LookupError):
        return s.encode(enc, "replace").decode(enc, "replace")


def report(line):
    """证词一律走 fd 1 立刻写出：print() 的块缓冲会让闸的前半段"像没跑过"（本次实测丢过 11 行）。"""
    os.write(1, (_safe(line) + "\n").encode("utf-8", "replace"))


def check(name, got, want):
    global TOTAL
    TOTAL += 1
    a = json.dumps(got, ensure_ascii=False, default=str)
    b = json.dumps(want, ensure_ascii=False, default=str)
    # 🔴 每条都立刻刷到 fd：闸的输出就是它的证词，"看不见的第一半"会伪装成"没跑过"（本次实测丢过 11 行）
    line = _safe(("PASS " if a == b else "FAIL ") + name + ("" if a == b else "  => %r (期望 %r)" % (got, want)))
    report(line)
    if a != b:
        fails.append(name)


SRC = open(APP_PY, encoding="utf-8", errors="replace").read()

# ── 装载被测模块（app.py 是 uvicorn 入口，import 只定义不监听）──────────────────
os.environ.setdefault("PYTHONPATH", APP_ROOT)
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)
try:
    import app as appmod
except Exception as e:
    report("FAIL 0 import app.py 就炸：%s: %s" % (type(e).__name__, str(e)[:200]))
    sys.exit(1)
# 🔴 这里在闸自己的进程里【一行都不 print】：本文件 import app 后，早期写到 stdout 的内容会被丢掉
#    （实测 21 条只出得来后 9 条）。所有证词统一走 report()=os.write(fd 1)，一条都不许走缓冲。
check("0 import app.py 不炸（闸读的是真模块，不是抄一份源码）", True, True)

# ── E1 结构性：父进程不许再有"裸 print 回显子进程日志"───────────────────────────
# 🔴 剥掉子进程脚本模板再扫：模板里的 print 跑在【子进程】里，它自带 TextIOWrapper(utf-8) 兜底，
#    把它算进父进程的账就是误伤（这条我自己错过一次）。
ECHO_SITES = [
    r'print(f"[SR-{task_id}] {msg}", flush=True)',
    r'print(f"[{task_id}] {msg}", flush=True)',
    r'print(f"[{task_id}/shot{shot_num:02d}] {msg}", flush=True)',
    r'print(f"[RM-{task_id}] {msg}", flush=True)',
]
for pat in ECHO_SITES:
    check("E1 父进程回显已收进 echo()（残留 %s）" % pat.split('"')[1][:18],
          pat in SRC, False)
check("E1b echo() 的四位回显点都在（SR 两处 + one-shot + redo_shot + RM）",
      len(re.findall(r'echo\(f"\[(?:SR-|RM-)?\{', SRC)) >= 4, True)

# ── E2 行为：GBK 流上旧写法必炸、echo() 不炸；stdout=None 也不炸 ────────────────
# 🔴 这三条必须开【子进程】跑：我第一版在闸进程里直接 `sys.stdout = 假流` 做对照，
#    结果闸自己前一半输出没了（20 条只打出 9 条）——拿"看不见"的绿当过，就是静默假绿。
CHECK_U = "\u2713"      # 就是打死那条任务的那个字符 U+2713
PROBE = r'''
import io, json, os, sys
sys.path.insert(0, WS)
sys.path.insert(0, BACKEND)
import app as A

class Gbk(io.TextIOBase):
    encoding = "gbk"
    def __init__(self): self.chunks = []
    def write(self, s):
        s.encode("gbk")            # 真 cp936 流：编不了就抛
        self.chunks.append(s); return len(s)
    def flush(self): pass

MSG = "MiMo 完成 finish_reason=stop, 长度 6352 %s 分镜" % chr(0x2713)
real = sys.__stdout__
out = {}

sys.stdout = Gbk()
try:
    print("[SR-x] " + MSG, flush=True); old = None
except UnicodeEncodeError: old = "UnicodeEncodeError"
except Exception as e: old = type(e).__name__
finally: sys.stdout = real
out["old_print"] = old

g = Gbk(); sys.stdout = g
try:
    A.echo("[SR-x] " + MSG); new = None
except Exception as e: new = type(e).__name__
finally: sys.stdout = real
w = "".join(g.chunks)
out["echo"] = new
out["echo_len"] = len(w)
out["echo_has_raw_check"] = chr(0x2713) in w

sys.stdout = None
try:
    A.echo("x " + MSG); none_err = None
except Exception as e: none_err = type(e).__name__
finally: sys.stdout = real
out["echo_stdout_none"] = none_err

# echo 的兜底不许哪天退化成窄捕获（变异对照 M2 验的就在这条）
import inspect as _I
_b = _I.getsource(A.echo)
out["echo_has_replace_branch"] = ("except (UnicodeEncodeError, LookupError):" in _b
                                  and 's.encode(enc, "replace")' in _b)

sys.__stdout__.write(json.dumps(out) + "\n")
'''
probe = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
try:
    PR = json.loads((probe.stdout or "").strip().splitlines()[-1])
except Exception:
    PR = {"__probe_broke__": (probe.returncode, (probe.stderr or "")[-160:])}
check("E2 反向对照：旧写法（裸 print）在 GBK 流上【确实炸】（不炸=这条控制是假的）",
      PR.get("old_print"), "UnicodeEncodeError")
check("E2b echo() 同一条消息不抛异常", PR.get("echo"), None)
check("E2b2 echo() 内容照样落出去，只是把编不了的字符换掉（不是整条吞了）",
      [PR.get("echo_len", 0) > 10, PR.get("echo_has_raw_check")], [True, False])
check("E2c pythonw（无控制台、sys.stdout=None）下 echo() 静默丢掉，绝不让日志把业务带崩",
      PR.get("echo_stdout_none"), None)
check("E2d echo() 的兜底是 except (UnicodeEncodeError, LookupError) + replace（不许哪天缩成窄捕获）",
      PR.get("echo_has_replace_branch"), True)

# ── D1/D2 "盘上真值"规则：一份实现、只纠 failed、产物必须真在─────────────────────
check("D1 恢复规则只有一份实现（三处调用都走 _sr_state_from_disk，别抄 phase 名单）",
      [SRC.count("def _sr_state_from_disk"), SRC.count("_sr_state_from_disk(")], [1, 4])
live = {"status": "running", "phase": "phase_1", "run_dir": SCRATCH_SR_DIR, "logs": []}
check("D2 RUNNING 不许被翻案（盘上有海报也不行——可能真在跑）",
      appmod._sr_state_from_disk(live), None)
bad = {"status": "failed", "phase": "phase_1", "run_dir": os.path.join(APP_ROOT, "outputs", "sr_不存在"),
       "logs": ["[Phase1] 运行目录: %s" % os.path.join(APP_ROOT, "outputs", "sr_不存在")]}
check("D3 目录不存在 ⇒ 不翻案（真失败就报真失败）", appmod._sr_state_from_disk(bad), None)
norun = {"status": "failed", "phase": "phase_1", "run_dir": "", "logs": ["没有运行目录的一行"]}
check("D4 日志里找不到运行目录 ⇒ 不翻案", appmod._sr_state_from_disk(norun), None)

if os.path.isdir(SCRATCH_SR_DIR):
    # 事故本尊：status=failed、run_dir 为空（崩在回显时还没写回）、phase 还是入口值 phase_1
    accident = {"status": "failed", "phase": "phase_1", "run_dir": "",
                "logs": ["[Phase1] 运行目录: %s" % SCRATCH_SR_DIR]}
    got = appmod._sr_state_from_disk(accident)
    check("D5 事故那条：run_dir 从自己的日志里找回 + phase 以盘上 phase_state.json 为准 ⇒ 纠回待审",
          [None if not got else got[0], None if not got else got[3]], ["awaiting_review", "after_step3"])
    check("D5b 海报是真的存在（不是靠状态字段猜）",
          None if not got else bool((got[1] or {}).get("poster")), True)
else:
    check("D5 事故产物目录还在（闸的这条现场判据）", os.path.isdir(SCRATCH_SR_DIR), True)

# ── S1 历史页排序键（混类型一比就 TypeError ⇒ 整页 500）────────────────────────
check("S1 排序键不再拿 `created_at or task_id` 混类型比",
      'history.sort(key=lambda h: h.get("created_at", "") or h.get("task_id", "")' in SRC, False)
check("S1b 有专门的同型键函数（数字 + 字符串两段）", "def _hist_key(h):" in SRC, True)
try:
    rows = __import__("asyncio").run(appmod.sr_history())
    hist_err = None
except Exception as e:
    rows, hist_err = None, "%s: %s" % (type(e).__name__, str(e)[:150])
check("S2 真跑一遍 /api/structural-replica/history ⇒ 不再 500", hist_err, None)
rows = rows or {}
hist = rows.get("history") if isinstance(rows, dict) else rows
check("S2b 返回 dict 带 history 列表，而且真读出任务（空列表=空判据，不算过）",
      [isinstance(rows, dict), isinstance(hist, list), len(hist) > 0], [True, True, True])
# 🔴 纠回待审之后，那句"服务重启，任务标记为失败"必须一起清掉：留着它，页面就是"待审 + 一条错误"自相矛盾，
#    人只会以为片子坏了（M5 变异：把 err_text 换回 t["error"] 就该红）。
_i = SRC.find('fix = _sr_state_from_disk({"status": status')
S3 = SRC[_i:_i + 700] if _i > 0 else ""
check("S3 兜底分支纠完状态：响应里 error 置空 + 同步把库里的 error 清掉（不是只改显示）",
      ["err_text = None" in S3, 'task_store.update(task_id, error="")' in S3,
       '"error": err_text,' in SRC], [True, True, True])

check("S2c 每条历史都带展示状态（页面按它渲染，缺字段就是又一处红字）",
      all(isinstance(h, dict) and h.get("status") for h in (hist or [])), True)

# ── F 8090 的启动方式 = 这类崩的总闸（某一句 print 改不完，也不许去改 BOUNDARY 文件）──────
#    判据形状：先量"子进程 stdout 到底是什么编码"，再看崩不崩 ——
#    只看退出码会出假绿（上一版就这样：父进程里 import app 之后环境带上 UTF-8 位，
#    子进程根本没走 gbk，却报"没崩"）。
_L = os.path.join(BACKEND, "start_8090.cmd")
_lc = open(_L, encoding="ascii", errors="replace").read() if os.path.isfile(_L) else ""
check("F1 8090 有启动脚本（没它就只能手敲 python app.py，stdout 编码全凭运气）",
      bool(_lc), True)
_app_src = open(os.path.join(BACKEND, r"app.py"),
                encoding="utf-8", errors="replace").read()
# 守卫探的那个路径【必须是 app.py 真注册过的路由】。为什么钉这条而不是钉 URL 字面量：
# 2026-10-10 我把守卫写成了 /api/health/models —— 那是 8100 的口，8090 上永远不应答，
# 于是"8090 活着"永远判不出来：守卫形同虚设，还会多起一个实例去抢日志文件。
_guard = re.search(r"curl\.exe -fs[^\n]*?http://127\.0\.0\.1:8090(/\S*)?", _lc)
_guard_path = (_guard.group(1) if _guard and _guard.group(1) else "") or None
_routes = set(re.findall(r'@app\.(?:get|post|api_route|websocket)\(\s*"([^"]+)"', _app_src))
check("F2 脚本三件套：HTTP 探活守卫（不是 netstat）+ python.exe -u（不是 pythonw）+ PYTHONIOENCODING 带 :replace",
      ["curl.exe -fs" in _lc, 'python.exe" -u app.py' in _lc,
       'PYTHONIOENCODING=utf-8:replace' in _lc], [True, True, True])
# 🔴 F2c 钉的是【规则】不是当年的修法：10-09 我用 pythonw 躲裸 print 的 cp936 崩，10-10 发现那是遮羞布——
#    pythonw 的 sys.stderr=None，uvicorn 默认日志配置要给它挂 StreamHandler，detached 启动直接崩在
#    "Unable to configure formatter 'default'"。正解=python.exe -u + 双流落盘 + :replace。
#    所以这条反向对照要一直咬：谁把 pythonw 加回来，F2/F2c 一起红。
check("F2c 反向对照：脚本里不许再出现 pythonw 起 app.py（那是 10-10 崩溃循环的形状）",
      bool(re.search(r"pythonw\.exe\"?\s+app\.py", _lc)), False)
check("F2d stdout 与 stderr 两条都要落盘（只接 stderr ⇒ 崩溃证据一半都没有）",
      [bool(re.search(r"1>>\s*\"[^\"]*\.out\.log\"", _lc)), bool(re.search(r"2>>\s*\"[^\"]*\.err\.log\"", _lc))],
      [True, True])
check("F2b 守卫探的路径 8090 自己注册过（探一个不存在的口 = 永远判不出活着）",
      [_guard_path, _guard_path in _routes if _guard_path else False], ["/", True])


def _run_print(extra_env):
    """真跑：子进程自己报 stdout 编码，再打一个 ✓（chr 造，躲开多层转义）。"""
    e = dict(os.environ)
    for k in ("PYTHONIOENCODING", "PYTHONUTF8", "PYTHONLEGACYWINDOWSSTDIO"):
        e.pop(k, None)                                    # 剥干净，测的才是"默认会怎样"
    e.update(extra_env)
    code = "import sys;print('ENC=' + str(sys.stdout.encoding));print('  ' + chr(0x2713) + ' composer', flush=True)"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, env=e,
                       cwd=BACKEND)
    out = (r.stdout or b"").decode("utf-8", "replace")
    enc = out.split("ENC=")[-1].splitlines()[0].strip() if "ENC=" in out else "?"
    return r.returncode, enc, (r.stderr or b"").decode("utf-8", "replace")


rc_bare, enc_bare, err_bare = _run_print({})
check("F3 🔴 默认（本机 cp936）下同一句 print 必崩，且崩法与事故逐字相同（\\u2713 / position 2）",
      [enc_bare.lower().replace("-", ""), rc_bare != 0,
       "can't encode character" in err_bare and "position 2" in err_bare],
      ["gbk", True, True])
rc_fix, enc_fix, err_fix = _run_print({"PYTHONIOENCODING": "utf-8:replace"})
check("F4 脚本给的环境里同一句不崩，而且字符是真发出去了（:replace 只换编不了的，不吞整条）",
      [rc_fix, enc_fix.lower().startswith("utf-8"), "can't encode" in err_fix], [0, True, False])
rc_bad, _e2, _x2 = _run_print({"PYTHONIOENCODING": "gbk"})
check("F5 反向对照：环境换成 gbk ⇒ 又崩（决定生死的就是这一位，不是巧合）", rc_bad != 0, True)
check("F6 pythonw 那条路的形状有出处：app.py 里 print 走的是 echo()，不是裸 print 崩业务",
      "def echo(" in open(os.path.join(BACKEND, r"app.py"),
                         encoding="utf-8", errors="replace").read(), True)
report("\n=== 8090 日志编码/状态纠正闸：%d 条断言，失败 %d ===" % (TOTAL, len(fails)))
if fails:
    report("失败项：" + "、".join(fails))
    sys.exit(1)
