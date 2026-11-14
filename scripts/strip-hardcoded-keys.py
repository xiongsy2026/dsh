"""把探针脚本里的明文 key 换成读 env/settings，改不动的宁可留着报出来也不瞎改。

规则：
  1) 整串字面量  "TOKEN" / 'TOKEN'      → os.environ["VAR"]
  2) 嵌在长串里  "Bearer TOKEN" / URL   → f"...{os.environ['VAR']}"（给该串加 f 前缀）
  3) 每个文件先备份成 <file>.keyfix.bak，改完 py_compile 不过就**自动还原**并记为失败
不碰的文件：config.py / backend/**（生产代码已单独手改）
跑法：PYTHONIOENCODING=utf-8 python scripts/strip-hardcoded-keys.py [--dry]
"""
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, WS)
sys.path.insert(0, BACKEND)
from config import settings  # noqa: E402

DRY = "--dry" in sys.argv
ROOTS = [BACKEND, WS]
SKIP_PATH = ("node_modules", ".git", "__pycache__", "video_runs", ".hypit",
             "Cache", "vendor", "OpenMontage", os.sep + "backend" + os.sep)
if "--all" not in sys.argv:                       # 默认连 outputs 下的历史探针也不碰，加 --all 才扫
    SKIP_PATH = SKIP_PATH + ("outputs",)
TOKEN_RE = re.compile(r"[0-9a-f]{32}|sk-[A-Za-z0-9]{10,}|wk-[A-Za-z0-9]{10,}")
STR_RE = re.compile(r"""([rfb]?)("([^"\\\n]*)"|'([^'\\\n]*)')""")

real = {}
for n in dir(settings):
    if n.startswith("_"):
        continue
    try:
        v = getattr(settings, n)
    except Exception:
        continue
    if isinstance(v, str) and len(v) >= 12 and re.search(r"(KEY|TOKEN|SECRET|PASS)", n) and not v.startswith("http"):
        real[hashlib.sha256(v.encode()).hexdigest()[:12]] = v

VAR_OF = {}
for n in dir(settings):
    try:
        v = getattr(settings, n)
    except Exception:
        continue
    if isinstance(v, str) and v:
        VAR_OF.setdefault(v, n)


def tok_var(tok):
    return VAR_OF.get(tok) if hashlib.sha256(tok.encode()).hexdigest()[:12] in real else None


changed, failed, skipped = [], [], []
for root in ROOTS:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "__pycache__")]
        if any(t in dirpath for t in SKIP_PATH):
            continue
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                text = io.open(p, encoding="utf-8", newline="").read()
            except (OSError, UnicodeError):
                continue
            toks = {t for t in TOKEN_RE.findall(text) if tok_var(t)}
            if not toks:
                continue

            new = text
            for tok in toks:
                var = tok_var(tok)
                # ① 整串字面量
                new = new.replace('"' + tok + '"', 'os.environ["%s"]' % var)
                new = new.replace("'" + tok + "'", "os.environ['%s']" % var)

            def fix_inner(m):
                prefix, whole, dq, sq = m.group(1), m.group(0), m.group(3), m.group(4)
                body = dq if dq is not None else sq
                hits = [t for t in TOKEN_RE.findall(body) if tok_var(t)]
                if not hits:
                    return whole
                out = body
                for t in hits:
                    out = out.replace(t, "{os.environ['%s']}" % tok_var(t))
                q = '"' if dq is not None else "'"
                return ("f" if "f" not in prefix else "") + q + out + q

            new = STR_RE.sub(fix_inner, new)

            if "os.environ[" in new and "import os" not in new.split("\n\n")[0]:
                new = re.sub(r"^(import |from )", "import os\n\\1", new, count=1, flags=re.M)

            if new == text:
                skipped.append(p)
                continue
            if DRY:
                changed.append(p + "  (dry)")
                continue
            bak = p + ".keyfix.bak"
            shutil.copy2(p, bak)
            io.open(p, "w", encoding="utf-8", newline="").write(new)
            r = subprocess.run([sys.executable, "-m", "py_compile", p], capture_output=True, text=True)
            if r.returncode != 0:
                shutil.copy2(bak, p)                    # 还原，不留下坏文件
                os.remove(bak)
                failed.append((p, r.stderr.strip().splitlines()[-1][:110] if r.stderr else "?"))
            else:
                os.remove(bak)
                changed.append(p)

print("模式: %s" % ("DRY-RUN（没写盘）" if DRY else "已改"))
print("成功 %d 个文件 | 语法不过被还原 %d 个 | 无法安全替换 %d 个" % (len(changed), len(failed), len(skipped)))
for p in changed[:50]:
    print("  ✓", p)
for p, e in failed:
    print("  ✗ 还原", p, "|", e)
for p in skipped[:20]:
    print("  ? 待手改", p)
