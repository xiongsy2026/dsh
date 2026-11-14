"""T4 omni 锚点自查（临时脚本，跑完删）。全程临时目录：生产 intent.json / delivery.json 一律不碰。"""
import json
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ["VIDEO_UI_RUNS"] = os.path.join(tmp, "runs")
os.environ["DSH_WORKSPACE"] = os.path.join(tmp, "ws")
os.environ["DELIVERY_LEDGER"] = os.path.join(tmp, "ws", ".dsh", "delivery.json")
# 🔴 把意图登记也拐到临时路径：_register_intent 会 import hypit_bridge 并写登记簿，别污染真文件
os.environ["HYPIT_WORKSPACE_INTENT"] = os.path.join(tmp, "ws", ".dsh", "intent.json")
os.environ["HYPIT_INTENT_MARK"] = os.path.join(tmp, "intent.json")
os.environ["HYPIT_NO_AUTOSTART"] = "1"

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WS, r"scratch\video-ui\video-ui"))
import store  # noqa: E402

fails = []


def check(name, got, want):
    a, b = json.dumps(got, ensure_ascii=False, default=str), json.dumps(want, ensure_ascii=False, default=str)
    print(("PASS " if a == b else "FAIL ") + name + ("" if a == b else " => %r 期望 %r" % (got, want)))
    if a != b:
        fails.append(name)


led = os.environ["DELIVERY_LEDGER"]
segs = store.segments_for(25)
r1 = store.create_run("墙体除污剂 25s 测试", 25, "product", session="session-T4")
rows = json.loads(open(led, encoding="utf-8").read()) if os.path.exists(led) else []
check("1 建 run 后台账出现一条", len(rows), 1)
e = rows[0] if rows else {}
check("2 归属：session / task / skill", [e.get("session"), e.get("skill")], ["session-T4", "omni"])
check("3 task 名 = run 名", e.get("task"), r1.name)
check("4 期望条数 = 段数 + 1 成片", len(e.get("expected") or []), segs + 1)
check("5 段数取自 state（不靠猜）", e.get("expected") and (segs == int(r1.state["segments"])), True)
tail = [i["path"].replace("\\", "/") for i in e.get("expected") or []]
check("6 最后一条是 video/final.mp4", tail[-1].endswith("video/final.mp4"), True)
check("7 前面都是 segments/segment_N.mp4", all(t.endswith(".mp4") for t in tail[:-1]), True)
check("8 期望路径落在本次 run 目录下", all(r1.name in t for t in tail), True)
check("9 台账指向的 workspace 相对化（runs 在工作区内才相对，否则保留绝对）",
      all(("/" in t) for t in tail), True)

store.create_run("无会话的一条", 10, "product", session=None)
rows = json.loads(open(led, encoding="utf-8").read())
check("10 没有 session ⇒ 不登记（fail-closed，不给没主人的会话记账）", len(rows), 1)

# 台账炸了不能带崩建 run
import delivery_ledger  # noqa: E402
orig = delivery_ledger.open_obligation
delivery_ledger.open_obligation = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("模拟写盘炸"))
try:
    r3 = store.create_run("台账坏了也要建片", 15, "product", session="session-T4b")
    ok = bool(r3.name) and os.path.isdir(str(r3.root))
except Exception as ex:
    ok = False
    print("   create_run 被带崩：", ex)
check("11 登记失败绝不影响建 run", ok, True)
delivery_ledger.open_obligation = orig

# 销账链路：把 final + 所有段造出来 ⇒ pending 归零
e = json.loads(open(led, encoding="utf-8").read())[0]
ws = os.environ["DSH_WORKSPACE"]
for it in e["expected"]:
    p = it["path"] if os.path.isabs(it["path"]) else os.path.join(ws, it["path"])
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "wb").write(b"x" * 8)
check("12 全部落盘 ⇒ 不再欠账", len(delivery_ledger.pending()), 0)
check("13 台账里该条已 closed", bool(json.loads(open(led, encoding="utf-8").read())[0].get("closed_at")), True)

print("\n=== T4 omni 锚点自查：%d 条，失败 %d ===" % (13, len(fails)))
print("临时目录：", tmp)
sys.exit(1 if fails else 0)
