#!/usr/bin/env python
"""首屏大标题「专注电商AI落地！」的闸：文案在不在、有没有被钉住、锚点断没断。

为什么钉这条：这句话是 2026-10-04 之前某次【直接改在已安装 bundle 里】的，_setup 下没有对应补丁脚本
⇒ 下次 rebuild 或内核升级会静默打回上游的「探索未至之境」。同一文件当年还被改坏过一次
（HeroFish 区域写坏 ⇒ 整个对话输入框消失），所以这里连"只改了 2 行、行尾没变、语法过"一起验。
默认 0 写入（幂等重跑除外，它写完会校验字节一致）。
"""
import io
import json
import subprocess
import sys
from pathlib import Path
WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BUNDLE = Path.home() / ("AppData/Local/Programs/DSH Desktop/resources/app/node_modules"
                        "/@deepseek-ai/dsh-client-ui-conversation/lib/client.js")
BACKUP = Path(os.path.join(WS, r".dsh-ui-backup\dsh-client-ui-conversation.client.js.pre-hero-headline-20261004"))
PLUGIN = Path(os.path.join(WS, r"plugins\super-clone-ui\lib\client.js"))
REBUILD = Path(os.path.join(WS, r"_setup\rebuild_weeksir_desktop.py"))
PATCH = Path(os.path.join(WS, r"_setup\patch_hero_headline.py"))
NEW_ZH = '"hero.headline": "专注电商AI落地！",'
NEW_EN = '"hero.headline": "Focus on e-commerce AI in production!",'
OLD = ['"hero.headline": "探索未至之境",', '"hero.headline": "专注AI落地！",',
       '"hero.headline": "Into the Unknown",', '"hero.headline": "Focus on AI in Production!",']
out = []


def check(name, got, want):
    ok = json.dumps(got, ensure_ascii=False, default=str) == json.dumps(want, ensure_ascii=False, default=str)
    out.append((ok, name))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else " => %r (期望 %r)" % (got, want)))


raw = BUNDLE.read_bytes()
text = raw.decode("utf8")
check("1 新中文案在 bundle 里", NEW_ZH in text, True)
check("2 新英文案在 bundle 里（中英要成对，切语言不能露出上游文案）", NEW_EN in text, True)
check("3 四条旧文案（含上游原文）全部不在", [o for o in OLD if o in text], [])
check("4 补丁脚本已注册进 rebuild 的 PATCHES（没注册=下次发版被打回）",
      "patch_hero_headline.py" in REBUILD.read_text(encoding="utf8"), True)

before = raw
r = subprocess.run([sys.executable, str(PATCH)], capture_output=True, text=True, timeout=120, encoding="utf8")
check("5 幂等：重跑补丁退出 0、自报收敛、且没再替换任何东西",
      (r.returncode == 0, "zh True" in r.stdout, "替换" in r.stdout), (True, True, False))
check("6 幂等：重跑后文件字节一模一样", BUNDLE.read_bytes() == before, True)

if BACKUP.is_file():
    a = BACKUP.read_bytes().decode("utf8").splitlines()
    b = text.splitlines()
    diff = [i + 1 for i, (x, y) in enumerate(zip(a, b)) if x != y]
    check("7 相对备份只改了那 2 行（当年改坏过这个文件，多一行都要查）", (len(a) == len(b), diff), (True, [13702, 13864]))
    check("8 行尾没被动（CRLF 数量不变）", raw.count(b"\r\n") == BACKUP.read_bytes().count(b"\r\n"), True)
else:
    print("SKIP 7/8（没有备份基线可比）")

c = subprocess.run(["node", "--check", str(BUNDLE)], capture_output=True, text=True, timeout=120)
check("9 bundle 语法过 node --check", c.returncode, 0)

p = PLUGIN.read_text(encoding="utf8")
check("10 插件锚点表含新文案（首屏 tab 靠它定位标题行，漏了就静默不挂）",
      "专注电商AI落地！" in p and "Focus on e-commerce AI in production!" in p, True)
check("11 锚点用 indexOf 匹配整张表（不是 [0]/[1] 硬比，回退时不至于断）",
      "HERO_HEADLINES.indexOf(t) >= 0" in p, True)

bad = [n for ok, n in out if not ok]
print("\n=== 首屏大标题闸：%d 条断言，失败 %d ===" % (len(out), len(bad)))
sys.exit(1 if bad else 0)
