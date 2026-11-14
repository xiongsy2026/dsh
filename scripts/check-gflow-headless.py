# -*- coding: utf-8 -*-
"""gflow 生图腿：无头传输与"假 200"兜底的回归闸（2026-10-10 用户选 A：换 sapisidhash 不弹窗）。

钉的是【规则】不是现场：不依赖 gflow 能不能连上 Google（那条腿今天实测连不上，见下），
只钉"代码里这条腿到底怎么起的、失败了怎么说"。跑法：python scripts/check-gflow-headless.py
"""
import io
import json
import os
import re
import sys
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
GW = BACKEND
SRC = io.open(os.path.join(GW, "gateway.py"), encoding="utf-8", errors="replace").read()
CFG = io.open(os.path.join(GW, "config.py"), encoding="utf-8", errors="replace").read()

TOTAL = [0]
fails = []


def check(name, got, want):
    TOTAL[0] += 1
    a, b = json.dumps(got, ensure_ascii=False, default=str), json.dumps(want, ensure_ascii=False, default=str)
    print(("PASS " if a == b else "FAIL ") + name + ("" if a == b else "  => %r (期望 %r)" % (got, want)))
    if a != b:
        fails.append(name)


def body(src, start_marker, end_marker):
    i = src.find(start_marker)
    j = src.find(end_marker, i + len(start_marker)) if i >= 0 else -1
    return src[i:j] if i >= 0 and j > i else ""


GF = body(SRC, "def _gflow_image(", "\ndef ")
check("H0 读到了 _gflow_image 函数体（后面几条不是空跑）", len(GF) > 1200, True)

# ── 传输层：无头是选定的默认，有头是显式回退项 ──────────────────────────────────
m = re.search(r'GFLOW_TRANSPORT:\s*str\s*=\s*"([^"]+)"', CFG)
check("H1 config 里默认传输 = sapisidhash（不是驱动网页那条 ui_automation）",
      m.group(1) if m else None, "sapisidhash")
check("H2 适配器把传输落到【子进程 env】，并打开 experimental 开关（不打开 CLI 不认这个值）",
      ['env["GFLOW_CLI_TRANSPORT"] = transport' in GF,
       'env["GFLOW_CLI_EXPERIMENTAL_TRANSPORTS"] = "1"' in GF], [True, True])
check("H3 非 ui_automation 时强制 HEADLESS=true（这条传输本来就不需要可见窗口）",
      'env["GFLOW_CLI_HEADLESS"] = "true"' in GF, True)
check("H4 🔴 env 只构造一处（第二处各拼一遍 = 迟早只有一处带 transport，又回到有头）",
      len(re.findall(r'env = \{"GFLOW_CLI_HEADLESS"', GF)), 1)
check("H5 回退口在，且不用改代码（.env 写 GFLOW_TRANSPORT=ui_automation 就回有头）",
      "GFLOW_TRANSPORT" in CFG and "ui_automation" in CFG, True)

# ── 失败怎么说：不许把超时报成没登录 ────────────────────────────────────────────
tm = re.search(r'\["auth", "status"\],\s*([\d.]+)', GF)
check("H6 auth status 超时 >= 60s（gflow 自报慢网要 ~45s；30s 会把探测超时误报成『没登录』）",
      float(tm.group(1)) >= 60.0 if tm else False, True)
check("H7 code is None 与 code != 0 分开报：没回话 ≠ 没登录，且都不发起生成",
      ["if code is None:" in GF, "没回话" in GF, "if code != 0:" in GF], [True, True, True])

# ── experimental 的代价：空媒体假 200 必须判失败 ─────────────────────────────────
check("H8 🔴 『退 0 但没找到落盘文件 ⇒ 判失败』这条兜底还在（上游文档明说会回空媒体的假 200）",
      "回 0 但没找到落盘文件" in GF, True)
check("H9 落盘判据不看退出码单独成立：先挖 --json 路径、再兜底扫目录",
      ['("path", "file", "output_path"' in GF, "glob.glob" in GF], [True, True])

# ── 反向对照：把改动退回去，判据必须咬 ──────────────────────────────────────────
mut1 = GF.replace('env["GFLOW_CLI_TRANSPORT"] = transport', "pass  # 变异：不传传输")
check("H10 反向对照：不传 transport ⇒ H2 必须红", 'env["GFLOW_CLI_TRANSPORT"]' in mut1, False)
mut2 = GF.replace("回 0 但没找到落盘文件", "成功")
check("H11 反向对照：把落盘兜底摘掉 ⇒ H8 必须红（只剩退出码=会被假 200 骗）",
      "回 0 但没找到落盘文件" in mut2, False)
mut3 = GF.replace('["auth", "status"], 75.0', '["auth", "status"], 20.0')
check("H12 反向对照：超时改回 20s ⇒ H6 必须红（证明 H6 真在判时间，不是恒绿）",
      float(re.search(r'\["auth", "status"\],\s*([\d.]+)', mut3).group(1)) >= 60.0, False)

# ── H13 泛化：整份 gateway.py 扫"函数里用了、但哪一层都没绑过"的名字 ──────────────
# 为什么钉这条：2026-10-10 连着抓到两个同类故障 —— invoke() 成功返回里写 `done.get(...)`
# （`done` 从没赋值 ⇒ 每次成功都 NameError、被外层 except 吞成"失败"+回滚预扣+退段，
#  账本上和真 502 长得一模一样），以及 _gflow_image 里写 `_j.loads(...)`（模块级别名其实是 `_json`
#  ⇒ NameError 被就近的 except Exception 吃掉 ⇒ gflow 的 --json 解析从来没生效过）。
# 两个都是 `py_compile` 过得去、闸全绿的【隐形】故障，所以这里用 symtable 扫，不靠人眼。
import symtable as _sym

_BUILT = set(dir(__builtins__)) if isinstance(__builtins__, dict) else set(dir(__builtins__))


def undefined_names(src, filename="gateway.py"):
    """返回 [(函数, 名字)]：函数体里 Load 了、但函数内/模块内/builtins 都没有绑定的名字。
    解析不了 ⇒ 当成【一条命中】返回（闸崩≠闸红：宁可红着说"读不懂"，也不许异常把整闸打断）。"""
    try:
        top = _sym.symtable(src, filename, "exec")
    except Exception as e:
        return [("<parse>", "symtable 读不懂: %s" % str(e)[:80])]
    # `__file__` 这类是 CPython 在 exec 模块时【注入】的，symtable 看不见但不是未定义 ⇒ 白名单要有出处：
    # 见 CPython docs 2「exec module / namespace 预置 __name__/__file__/__doc__/__package__/__loader__/__spec__/__builtins__」
    mod = {s.get_name() for s in top.get_symbols() if s.is_local()} | _BUILT | {
        "__file__", "__name__", "__doc__", "__package__", "__loader__", "__spec__", "__builtins__",
        "__path__", "__dict__", "__class__"}
    out = []

    def walk(scope, prefix):
        for ch in scope.get_children():
            fname = prefix + "." + ch.get_name()
            bound = mod | {s.get_name() for s in ch.get_symbols()
                           if s.is_local() or s.is_imported() or s.is_parameter()}
            for s in ch.get_symbols():
                n = s.get_name()
                if s.is_local() or s.is_free() or s.is_imported() or s.is_parameter():
                    continue
                if n in bound:
                    continue
                out.append((fname, n))
            walk(ch, fname)

    walk(top, "<module>")
    return sorted(set(out))


undef = undefined_names(SRC)
check("H13 🔴 泛化：gateway.py 里不许有『用了却没绑定』的名字（done/_j 这两次都是它，py_compile 抓不到）",
      undef, [])
_bad = SRC.replace("def _gflow_image(",
                   "def _landmine_probe():\n    return never_bound_name_xyz + 1\n\n\n"
                   "def _gflow_image(", 1)
check("H14 反向对照：故意塞一个未绑定名字进去 ⇒ 扫描必须点名（证明 H13 不是恒绿的摆设）",
      [n for (_f, n) in undefined_names(_bad) if n == "never_bound_name_xyz"],
      ["never_bound_name_xyz"])
check("H15 反向对照：变异体本身要能被解析（否则 H14 是在测【读不懂】而不是测未绑定名字）",
      [f for (f, _n) in undefined_names(_bad) if f == "<parse>"], [])

print("\n=== gflow 无头腿闸：%d 条断言，失败 %d ===" % (TOTAL[0], len(fails)))
if fails:
    print("失败清单: " + " | ".join(f.split()[0] for f in fails))
sys.exit(1 if fails else 0)
