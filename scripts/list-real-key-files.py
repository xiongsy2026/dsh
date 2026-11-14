"""把扫到的"凭据形状"和 .env 里的真值做比对，只报**确实是真 key** 的那些。

比对全程只用 sha256 指纹，绝不打印 key，也不打印 .env 内容。
跑法：PYTHONIOENCODING=utf-8 python scripts/list-real-key-files.py
"""
import hashlib
import io
import os
import re
import subprocess
import sys

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, WS)
sys.path.insert(0, BACKEND)
from config import settings  # noqa: E402

ROOTS = [BACKEND, WS]
EXTS = (".py", ".js", ".mjs", ".ts", ".ps1", ".cmd", ".bat", ".md", ".json")
SKIP = ("node_modules", ".git", "__pycache__", "dist", "build", "video_runs", ".hypit",
        "Cache", "vendor", "OpenMontage")

# 1) 收集 .env/settings 里所有"像凭据"的值 → 指纹 → 变量名
real = {}
for name in dir(settings):
    if name.startswith("_"):
        continue
    v = getattr(settings, name)
    if not isinstance(v, str) or len(v) < 12:
        continue
    if re.fullmatch(r"https?://\S+", v):
        continue
    if not re.search(r"(KEY|TOKEN|SECRET|PASS)", name):
        continue
    real.setdefault(hashlib.sha256(v.encode()).hexdigest()[:12], name)

print(".env/settings 里可比对的凭据值：%d 个（只列变量名，不列值）" % len(real))

# 2) 扫文件，按指纹匹配
rows = []
for root in ROOTS:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "__pycache__")]
        if any(t in dirpath for t in SKIP):
            continue
        for fn in filenames:
            if not fn.endswith(EXTS):
                continue
            p = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(p) > 3_000_000:
                    continue
                text = io.open(p, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            for ln, line in enumerate(text.split("\n"), 1):
                for tok in re.findall(r"[0-9a-f]{32}|sk-[A-Za-z0-9]{10,}|wk-[A-Za-z0-9]{10,}", line):
                    var = real.get(hashlib.sha256(tok.encode()).hexdigest()[:12])
                    if var:
                        rows.append((var, p, ln))

TRACKED = {}
for root in ROOTS:
    try:
        out = subprocess.run(["git", "-C", root, "ls-files"], capture_output=True, text=True, timeout=60).stdout
        TRACKED[root] = {os.path.normcase(os.path.join(root, l.strip())) for l in out.splitlines()}
    except Exception:
        TRACKED[root] = set()

by_var = {}
for var, p, ln in rows:
    by_var.setdefault(var, []).append(p)

print("\n=== 确实是真凭据的：%d 处 / %d 个变量 ===" % (len(rows), len(by_var)))
for var, files in sorted(by_var.items(), key=lambda kv: -len(kv[1])):
    uniq = sorted(set(files))
    in_git = [f for f in uniq if os.path.normcase(f) in TRACKED.get(
        next((r for r in ROOTS if os.path.normcase(f).startswith(os.path.normcase(r))), ""), set())]
    print("\n%s  —— 散在 %d 个文件（其中已入 git %d 个%s）" % (
        var, len(uniq), len(in_git), " ⇒ 撤不回，必须轮换" if in_git else ""))
    for f in uniq[:40]:
        print("   ", f)
    if len(uniq) > 40:
        print("    …另有 %d 个" % (len(uniq) - 40))
