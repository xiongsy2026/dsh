"""T4 去水印锚点自查（临时脚本，跑完删）。零网络零扣费：不调 invoke，只测登记器 + 钉源码顺序。"""
import json
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ["DELIVERY_LEDGER"] = os.path.join(tmp, "delivery.json")
os.environ["DSH_WORKSPACE"] = tmp
sys.path.insert(0, r"D:\video-skill\backend")
import gateway as G  # noqa: E402
import delivery_ledger as L  # noqa: E402

fails = []


def check(name, got, want):
    a, b = json.dumps(got, ensure_ascii=False, default=str), json.dumps(want, ensure_ascii=False, default=str)
    print(("PASS " if a == b else "FAIL ") + name + ("" if a == b else " => %r 期望 %r" % (got, want)))
    if a != b:
        fails.append(name)


def rows():
    return json.loads(open(os.environ["DELIVERY_LEDGER"], encoding="utf-8").read()) \
        if os.path.exists(os.environ["DELIVERY_LEDGER"]) else []


out = os.path.join(tmp, "productions", "dw", "outputs", "clean.mp4")
os.makedirs(os.path.dirname(out), exist_ok=True)

os.environ.pop("DSH_SESSION_ID", None)
G._register_delivery_dewatermark({"session": "session-DW1", "task": "dw-task"}, {"output_path": out})
r = rows()
check("1 显式 session ⇒ 登记一条", [len(r), r[0]["session"] if r else None], [1, "session-DW1"])
check("2 skill=dewatermark、task 取自参数", [r[0]["skill"], r[0]["task"]], ["dewatermark", "dw-task"])
check("3 期望就是 output_path（同步落盘型，路径已知）", len(r[0]["expected"]), 1)

G._register_delivery_dewatermark({}, {"output_path": out})
check("4 没有 session（env 也没有）⇒ 不登记", len(rows()), 1)

os.environ["DSH_SESSION_ID"] = "session-DW-ENV"
G._register_delivery_dewatermark({}, {"output_path": out})
check("5 只靠 DSH 注入的 $env:DSH_SESSION_ID 也能登记（不留可选步骤）", len(rows()), 2)
check("6 task 缺省用文件名当占位（clean）", rows()[1]["task"], "clean")

G._register_delivery_dewatermark({"session": "s"}, {"output_path": ""})
check("7 没有 output_path ⇒ 不登记（不许记一条永远销不掉的账）", len(rows()), 2)

orig = L.open_obligation
L.open_obligation = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("模拟台账炸"))
try:
    G._register_delivery_dewatermark({"session": "session-X"}, {"output_path": out})
    ok = True
except Exception:
    ok = False
L.open_obligation = orig
check("8 台账炸了不影响这次调用", ok, True)

src = open(r"D:\video-skill\backend\gateway.py", encoding="utf-8").read()
# 只认【缩进的调用点】—— 顶格的 `def _register_delivery_dewatermark(...)` 是同串前缀，量顺序会指到定义行
i1 = src.find(chr(10) + "        _register_delivery_dewatermark(params, vals)")
i2 = src.find("return _dewatermark_run(")
check("9 先记账再花钱（登记在 _dewatermark_run 之前）", [i1 > 0, i2 > 0, i1 < i2], [True, True, True])
near = src.rfind("vals, err = _dewatermark_payload(params)", 0, i1)   # 取登记之前【最近】那次（文件里还有别的同名调用点）
check("10 本地参数校验仍在登记之前（错参数不该先留下欠账）", [near > 0, near < i1], [True, True])

# 落盘即销账：把文件造出来再问一次
open(out, "wb").write(b"y" * 2048)
check("11 文件真落盘 ⇒ 不再欠账", len(L.pending()), 0)
check("12 该条已 closed", bool(rows()[0].get("closed_at")), True)

print("\n=== T4 去水印锚点自查：%d 条，失败 %d ===" % (12, len(fails)))
sys.exit(1 if fails else 0)
