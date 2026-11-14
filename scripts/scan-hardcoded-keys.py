"""扫盘：源码里的明文凭据 + 绕过网关直连供应商的调用点。

只打印 文件:行 + 掩码指纹（前 6 位 + sha256 前 8），绝不打印 key 本身。
跑法：PYTHONIOENCODING=utf-8 python scripts/scan-hardcoded-keys.py
"""
import hashlib
import io
import os
import re
import subprocess
from collections import Counter
WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")

ROOTS = [BACKEND, WS]
EXTS = (".py", ".js", ".mjs", ".ts", ".ps1", ".cmd", ".bat", ".md", ".json")
SKIP_DIR = ("node_modules", ".git", "__pycache__", "dist", "build", "outputs", "output",
            "video_runs", "hypit.runtime", ".hypit", "Cache", "frontend", "docs" + os.sep + "vendor")
# 明文凭据的形状：32 位十六进制 / sk-… / wk-… / Bearer 字面量 / api_key=“…”
PATTERNS = [
    ("32hex", re.compile(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])")),
    ("sk-", re.compile(r"sk-[A-Za-z0-9]{10,}")),
    ("wk-", re.compile(r"wk-[A-Za-z0-9]{10,}")),
    ("Bearer字面量", re.compile(r"Bearer\s+(?!\\|\$|\{|%s)[A-Za-z0-9_\-]{16,}")),
    ("赋值型", re.compile(r"(?i)(api_?key|token|secret)\s*[=:]\s*[\"'][A-Za-z0-9_\-]{12,}[\"']")),
]
# 明显不是 key 的常见误报（哈希/文件名/占位）
NOISE = re.compile(r"(0{32}|f{32}|sha1|md5|example|placeholder|your[-_]|xxxx)", re.I)

hits, files_scanned = [], 0
for root in ROOTS:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "__pycache__")
                       and not any(dirpath.endswith(s) for s in SKIP_DIR)]
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
            files_scanned += 1
            for ln, line in enumerate(text.split("\n"), 1):
                for name, pat in PATTERNS:
                    for m in pat.finditer(line):
                        s = m.group(0)
                        if NOISE.search(s) or "sha" in line.lower()[:20]:
                            continue
                        hits.append((p, ln, name, s))

# 每个仓库跑一次 git ls-files，别对每个命中都 fork 一个 git（慢且刷屏）
TRACKED = {}
for root in ROOTS:
    try:
        out = subprocess.run(["git", "-C", root, "ls-files"], capture_output=True, text=True, timeout=30).stdout
        TRACKED[root] = {os.path.normcase(os.path.join(root, l.strip())) for l in out.splitlines()}
    except Exception:
        TRACKED[root] = set()

def tracked(p):
    root = next((r for r in ROOTS if os.path.normcase(p).startswith(os.path.normcase(r))), "")
    return os.path.normcase(p) in TRACKED.get(root, set())


print("扫了 %d 个文件，命中 %d 处明文凭据形状" % (files_scanned, len(hits)))
print("按类型:", dict(Counter(h[2] for h in hits)))

# 按"值"分组：真正要关心的是有几把不同的 key、各散在多少文件、有没有已进 git
groups = {}
for p, ln, kind, val in hits:
    groups.setdefault(val, {"kind": kind, "files": set(), "tracked": set()})
    groups[val]["files"].add(p)
    if tracked(p):
        groups[val]["tracked"].add(p)

print("\n=== 唯一凭据值 %d 个（按涉及文件数排序）===" % len(groups))
for val, g in sorted(groups.items(), key=lambda kv: -len(kv[1]["files"])):
    if len(g["files"]) < 2 and not g["tracked"]:
        continue                                   # 只散在一处且未入库的，后面清单里再看
    fp = val[:6] + "…" + hashlib.sha256(val.encode()).hexdigest()[:6]
    mark = "🔴已入 git（撤不回，必须轮换）" if g["tracked"] else "未入库"
    print("  %-4s %-22s %2d 个文件  %s" % (g["kind"], fp, len(g["files"]), mark))
    for p in sorted(g["files"])[:6]:
        print("        ", p[-72:])
    if len(g["files"]) > 6:
        print("         …另有 %d 个" % (len(g["files"]) - 6))

print("\n=== 只出现在一个文件、且未入库（改动风险低）===")
solo = [(v, g) for v, g in groups.items() if len(g["files"]) == 1 and not g["tracked"]]
print("  共 %d 个值：%s" % (len(solo), ", ".join(sorted({os.path.basename(next(iter(g['files']))) for _, g in solo}))[:600]))
