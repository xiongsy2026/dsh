#!/usr/bin/env python
"""后台任务"唤醒预算"补丁的闸：值对不对、补丁钉没钉住、有没有误伤内核代码。

为什么钉这条：`dsh-tool-jobs` 的终态通知只在唤醒预算内才开新一轮，默认 3
（_setup/dsh-harness/packages/jobs/tool-jobs/src/index.ts:48-51）；花完就降级成 inject
（同文件 :293-298）⇒ 多阶段视频链路跑完，没人被叫醒播报，客户看到的就是"它自己停了"。
我们把 shipped preset 的挂载行加了 config ⇒ 64。这个闸保证三件事：值在、补丁注册了、
内核源码的默认值没被动（动过 = 下次升级会漂移，而且那是改内核不是打补丁）。
默认 0 写入（幂等重跑除外，跑完校验字节一致）。
"""
import io
import json
import subprocess
import sys
from pathlib import Path
WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

YML = Path.home() / ("AppData/Local/Programs/DSH Desktop/resources/app/node_modules"
                     "/@deepseek-ai/dsh-agent-presets/presets/standard/agent.cordis.yml")
BACKUP = Path(os.path.join(WS, r".dsh-ui-backup\agent.cordis.yml.pre-wake-budget-20261004"))
PATCH = Path(os.path.join(WS, r"_setup\patch_wake_budget.py"))
REBUILD = Path(os.path.join(WS, r"_setup\rebuild_weeksir_desktop.py"))
SRC = Path(os.path.join(WS, r"_setup\dsh-harness\packages\jobs\tool-jobs\src\index.ts"))
RUNTIME = Path.home() / ("AppData/Local/Programs/DSH Desktop/resources/app/node_modules"
                         "/@deepseek-ai/dsh-tool-jobs/lib/index.js")
out = []


def check(name, got, want):
    ok = json.dumps(got, ensure_ascii=False, default=str) == json.dumps(want, ensure_ascii=False, default=str)
    out.append((ok, name))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else " => %r (期望 %r)" % (got, want)))


y = YML.read_text(encoding="utf-8")
lines = y.splitlines()
i = next((k for k, l in enumerate(lines) if l.strip() == "- id: tool-jobs"), -1)
block = "\n".join(lines[i:i + 8]) if i >= 0 else ""

check("1 找得到 tool-jobs 挂载行", i >= 0, True)
check("2 预算值是 64", "maxConsecutiveWakes: 64" in block, True)
check("3 config 块挂在这个条目下（缩进 2/4，别挂错行）",
      ("  config:" in block, "    maxConsecutiveWakes: 64" in block), (True, True))
check("4 补丁脚本已注册进 rebuild 的 PATCHES（没注册=下次发版被打回默认 3）",
      "patch_wake_budget.py" in REBUILD.read_text(encoding="utf-8"), True)

before = YML.read_bytes()
r = subprocess.run([sys.executable, str(PATCH)], capture_output=True, text=True, timeout=120, encoding="utf-8")
check("5 幂等：重跑退出 0、报 already patched、且没再改任何东西",
      (r.returncode == 0, "already patched" in r.stdout, "patched\n" in r.stdout), (True, True, False))
check("6 幂等：重跑后文件字节一模一样", YML.read_bytes() == before, True)

if BACKUP.is_file():
    import difflib
    a, b = BACKUP.read_text(encoding="utf-8").splitlines(), lines
    # 不能用"这行在不在原文里"判新增 —— `  config:` 这种行在别处本来就有（tool-fs-search 就有一行），
    # 会被算成"没新增"。按 diff 的 +/- 数才是真新增/真删除。
    added = [l for l in difflib.unified_diff(a, b, lineterm="", n=0)
             if l.startswith("+") and not l.startswith("+++")]
    removed = [l for l in difflib.unified_diff(a, b, lineterm="", n=0)
               if l.startswith("-") and not l.startswith("---")]
    check("7 相对备份只加行、不删行、不动别的（4 行：config + 2 行注释 + 值）",
          (len(added), len(removed)), (4, 0))
else:
    print("SKIP 7（没有备份基线）")

s = SRC.read_text(encoding="utf-8") if SRC.is_file() else ""
check("8 内核源码的默认值仍是 3（我们是 overlay，不是改内核）",
      "maxConsecutiveWakes: z.number().min(1).default(3)" in s, True)
check("9 运行时 bundle 里这个 Config 存在（补丁打的键名和它对得上）",
      "maxConsecutiveWakes" in (RUNTIME.read_text(encoding="utf-8") if RUNTIME.is_file() else ""), True)
check("10 生效前提写在这：常驻进程要重启才读新 config（闸不替你重启）",
      "maxConsecutiveWakes" in y, True)

bad = [n for ok, n in out if not ok]
print("\n=== 唤醒预算闸：%d 条断言，失败 %d ===" % (len(out), len(bad)))
print("提醒：改的是加载期读的 config ⇒ 要重启 DSH Desktop 才生效（当前进程仍按 3 跑）。")
sys.exit(1 if bad else 0)
