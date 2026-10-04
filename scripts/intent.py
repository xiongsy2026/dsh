r"""工作区侧的意图登记 —— 点完 skill 后 agent 要跑的第一条命令（沙箱里也跑得动）。

为什么要有这个文件：登记动作过去只有一条路 `python D:\video-skill\backend\hypit_bridge.py intent ...`，
而那条命令要写 `D:\video-skill\.gateway_intent.json` —— **在工作区外**，DSH 自带的文件沙箱会直接拒
（实测报 `sandbox escalation … is not strictly wider`）⇒ 结果就是"用户明明点了 /hypit-pipeline，
右栏却什么都不弹"。本脚本只写自己工作区里的 `.dsh/intent.json`，8100 的判定口两个源合并读
（同会话取时间更新的那个），所以 agent 不需要任何提权。

用法（PowerShell 方言，会话 id 是 DSH 注入的托管变量）：
    python scripts/intent.py hypit "C:\\dsh\\hypit_runs\\productions\\<工程>\\build.svrun"
读得到 DSH_SESSION_ID 就不用带 --session；拿不到就显式给：
    python scripts/intent.py hypit <工程路径> --session session-xxxxxxxx

跑完会把判定口的答复打出来（show/state/port），当场就知道界面会不会出。
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 🔴 Windows 控制台默认 GBK：不重设编码的话，下面那句中文 JSON 一打印就 UnicodeEncodeError，
#    整条登记命令在真实 agent 里直接失败（2026-10-03 实测踩到）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
MARK = os.path.join(WORKSPACE, ".dsh", "intent.json")
PRESENCE = "http://127.0.0.1:8100/api/workbench/presence?session="
USAGE = "用法: python scripts/intent.py <omni|hypit|super-clone|product-video|film> [任务名或工程路径] [--session <会话id>]"


def die(msg, code=2):
    print(msg)
    sys.exit(code)


def main(argv):
    args = [a for a in argv]
    sid = os.environ.get("DSH_SESSION_ID") or ""
    if "--session" in args:
        i = args.index("--session")
        if i + 1 >= len(args):
            die("--session 后面要跟会话 id\n" + USAGE)
        sid = args[i + 1]
        del args[i:i + 2]
    if not args or args[0] in ("-h", "--help"):
        die(USAGE)
    skill = args[0].strip()
    task = args[1].strip() if len(args) > 1 else ""
    if not sid:
        die("拿不到会话 id：DSH 会给子进程注入 $env:DSH_SESSION_ID（注意是 PowerShell 写法，"
            "不是 $DSH_SESSION_ID）。确认能读到再重试，或显式加 --session <会话id>。\n" + USAGE)

    data = {}
    if os.path.exists(MARK):
        try:
            with open(MARK, encoding="utf-8") as f:
                loaded = json.load(f)
            data = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            data = {}
    data[sid] = {"skill": skill, "task": task, "ts": time.time()}
    os.makedirs(os.path.dirname(MARK), exist_ok=True)
    tmp = MARK + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, MARK)

    out = {"written": MARK, "session": sid, "skill": skill, "task": task}
    if skill == "hypit" and not task.lower().endswith((".svrun", ".svml")) and not os.path.isdir(task):
        # 契约要求"登记两次"，但文档是可选步骤 ⇒ 在这里当场提醒，别让右栏卡在 no-project 才发现
        out["hint"] = ("这次登记的任务名不是工程路径 ⇒ 右栏会显示 no-project（还没有可打开的工程）。"
                       "把 .svml/.svrun 写出来之后再跑一次："
                       "python scripts/intent.py hypit \"C:\\dsh\\hypit_runs\\productions\\<工程目录>\"")
    try:
        with urllib.request.urlopen(PRESENCE + urllib.parse.quote(sid), timeout=6) as r:
            p = json.loads(r.read().decode("utf-8"))
        # 回显【自己那一档】。以前只认 omni/hypit，film 登记完打印出来全是 null，
        # 看起来像"没生效"，其实台已经挂上了（2026-10-04 实测误导过一次排查）。
        bench = p.get(skill)
        if not isinstance(bench, dict):
            out["presence"] = ("判定口没有 %r 这一档；现有: %s" % (skill, "/".join(sorted(p)) or "(空)"))
        else:
            out["presence"] = {k: bench.get(k) for k in ("intent", "up", "state", "reason", "port", "url", "task")
                               if bench.get(k) is not None}
    except Exception as e:
        out["presence"] = "判定口没回话（8100 没起？）: %s: %s" % (type(e).__name__, str(e)[:120])
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
