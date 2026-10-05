#!/usr/bin/env python
"""交付三件套脚本 deliver_pack.py 的闸（V6）。

钉的是"坏输入必须非 0 退出" —— 这条链的 purpose 就是不让 agent 拿一个坏包当交付收工，
所以它的退出码比输出好不好看重要得多。
正例走 --no-net：直链可达性归 check-svc-8100.py 管（闸不依赖外部进程活着，那是假保险）。
跑法：python scripts/check-deliver-pack.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.join(HERE, "deliver_pack.py")
REAL = os.path.join(os.path.dirname(HERE), "productions", "2026-09-26-铁软糖", "outputs", "final-20s.mp4")
out = []


def check(name, got, want):
    ok = json.dumps(got, ensure_ascii=False, default=str) == json.dumps(want, ensure_ascii=False, default=str)
    out.append((ok, name))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else " => %r 期望 %r" % (got, want)))


def run(*args):
    return subprocess.run([sys.executable, PACK] + list(args), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=180)


check("0 脚本在", os.path.isfile(PACK), True)
check("0b 真成片在盘上（没有就别跑这条闸）", os.path.isfile(REAL), True)

r = run(REAL, "--no-net", "--json")
j = json.loads(r.stdout or "{}")
check("1 真片 ⇒ 退出 0 且 ok", [r.returncode, j.get("ok")], [0, True])
check("2 三件套齐（路径/直链/≥1 张帧）",
      [bool(j.get("video")), bool(j.get("link")), len(j.get("frames") or []) >= 1], [True, True, True])
check("3 markdown 里三样都在", [s in j.get("markdown", "") for s in ("**成片**", "**直链**", "![预览1]")],
      [True, True, True])
check("4 直链形状对（/api/film/file?task=…&rel=outputs/…）",
      ["/api/film/file?task=" in j.get("link", ""), "rel=outputs%2F" in j.get("link", "") or "rel=outputs/" in j.get("link", "")],
      [True, True])

tmp = tempfile.mkdtemp()
bad = os.path.join(tmp, "不存在.mp4")
check("5 文件不存在 ⇒ 非 0 退出", run(bad, "--no-net").returncode != 0, True)

tiny = os.path.join(tmp, "outputs")
os.makedirs(tiny, exist_ok=True)
p = os.path.join(tiny, "tiny.mp4")
open(p, "wb").write(b"0" * 500)
check("6 只有 500 字节 ⇒ 不算交付", run(p, "--no-net").returncode != 0, True)

cn = os.path.join(tmp, "outputs", "成品.mp4")
open(cn, "wb").write(b"0" * 20000)
r = run(cn, "--no-net", "--json")
check("7 中文文件名 ⇒ 非 0（8100 静态口要 ASCII 名）",
      [r.returncode != 0, json.loads(r.stdout or "{}").get("checks", {}).get("文件名是 ASCII")], [True, False])

check("8 不在 productions/<task>/outputs 下 ⇒ 非 0",
      run(os.path.join(tmp, "loose.mp4"), "--no-net").returncode != 0, True)
open(os.path.join(tmp, "loose.mp4"), "wb").write(b"0" * 20000)
r = run(os.path.join(tmp, "loose.mp4"), "--no-net", "--json")
check("8b 平铺在临时目录 ⇒ 该条校验为 False",
      json.loads(r.stdout or "{}").get("checks", {}).get("在 productions/<task>/outputs 下"), False)

src = open(PACK, encoding="utf-8").read()
check("9 抽帧只收 >2KB（黑帧/错误页也是文件，别拿它当画面）", "2048" in src, True)
check("10 不写假数据：只读输入 + 写 preview 图，绝不碰账本",
      ["sqlite" not in src, "gateway" not in src.lower()], [True, True])

print("\n=== V6 交付三件套闸：%d 条，失败 %d ===" % (len(out), sum(1 for ok, _ in out if not ok)))
sys.exit(1 if any(not ok for ok, _ in out) else 0)
