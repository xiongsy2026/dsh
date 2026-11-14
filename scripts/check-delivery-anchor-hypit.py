"""T4 hypit 锚点自查（临时脚本，跑完删）。🔴 零网络零花费：_run 被换成空操作，DB/台账/意图全在临时目录。"""
import json
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ["HYPIT_JOBS_DB"] = os.path.join(tmp, "jobs.sqlite")
os.environ["DELIVERY_LEDGER"] = os.path.join(tmp, "delivery.json")
os.environ["DSH_WORKSPACE"] = tmp
os.environ["HYPIT_WORKSPACE_INTENT"] = os.path.join(tmp, "intent-ws.json")
os.environ["HYPIT_INTENT_MARK"] = os.path.join(tmp, "intent-mark.json")
os.environ["HYPIT_NO_AUTOSTART"] = "1"

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, WS)
sys.path.insert(0, BACKEND)
import hypit_bridge as H  # noqa: E402
import delivery_ledger as L  # noqa: E402

fails = []


def check(name, got, want):
    a, b = json.dumps(got, ensure_ascii=False, default=str), json.dumps(want, ensure_ascii=False, default=str)
    print(("PASS " if a == b else "FAIL ") + name + ("" if a == b else " => %r 期望 %r" % (got, want)))
    if a != b:
        fails.append(name)


calls = []
H._run = lambda jid, body: calls.append(jid)      # 🔴 不上游、不花钱

body = {"model": "seedance-2-mini", "prompt": "一条测试腿", "duration": 5, "session": "session-T4h",
        "project": "productions/t4h/build.svrun"}
code, res = H.handle_submit(dict(body))
check("1 handle_submit 仍回 200 + job id", [code, "id" in res], [200, True])
jid = res.get("id", "")
rows = json.loads(open(os.environ["DELIVERY_LEDGER"], encoding="utf-8").read())
check("2 提交后台账出现一条义务", len(rows), 1)
e = rows[0] if rows else {}
check("3 归属 skill=hypit、task=工程键（_project_key 归一成 productions/<工程> 目录，不是文件路径）",
      [e.get("skill"), e.get("task")], ["hypit", "productions/t4h"])
check("4 期望项带 job_id（提交时产物名还不存在，不许写路径）",
      [len(e.get("expected") or []), e.get("expected", [{}])[0].get("job_id") == jid], [1, True])

code2, res2 = H.handle_submit(dict(body))
rows = json.loads(open(os.environ["DELIVERY_LEDGER"], encoding="utf-8").read())
check("5 第二条腿【追加】不覆盖（同工程欠两条）",
      [code2, len(rows), len(rows[0]["expected"])], [200, 1, 2])
check("6 两条 job_id 不同", len({i["job_id"] for i in rows[0]["expected"]}), 2)

check("7 queued 状态不算交付", H.delivery_job_done(jid), False)
check("8 未知 job 一律 False（不许猜成功）", H.delivery_job_done("deadbeef00000000"), False)
check("9 台账现在判这条会话还欠着", len(L.pending(session="session-T4h", job_done=H.delivery_job_done)), 1)

con = H._db()
con.execute("UPDATE jobs SET state='succeeded' WHERE id=?", (jid,))
con.commit()
con.close()
check("10 一条 succeeded 后仍欠另一条", H.delivery_job_done(jid), True)
check("11 pending 里 outstanding 少了一条",
      len(L.pending(session="session-T4h", job_done=H.delivery_job_done)[0]["outstanding"]), 1)
con = H._db()
con.execute("UPDATE jobs SET state='succeeded' WHERE id=?", (res2["id"],))
con.commit()
con.close()
check("12 全成功 ⇒ 义务清零", len(L.pending(session="session-T4h", job_done=H.delivery_job_done)), 0)

before = len(H._read if hasattr(H, "_read") else [])
H.handle_submit({k: v for k, v in body.items() if k != "session"})
check("13 没有 session ⇒ 不登记（fail-closed）", len(json.loads(
      open(os.environ["DELIVERY_LEDGER"], encoding="utf-8").read())), 1)

orig = L.append_obligation
L.append_obligation = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("模拟台账炸"))
try:
    code3, res3 = H.handle_submit(dict(body))
    ok = code3 == 200 and "id" in res3
except Exception as ex:
    ok, code3 = False, repr(ex)
L.append_obligation = orig
check("14 台账炸了也不能影响提交", [ok, len(calls) >= 3], [True, True])
check("15 台账里不许出现 job_id 之外的空路径项",
      all((i.get("path") or i.get("job_id")) for r in json.loads(
          open(os.environ["DELIVERY_LEDGER"], encoding="utf-8").read()) for i in r["expected"]), True)

print("\n=== T4 hypit 锚点自查：%d 条，失败 %d ===（临时目录 %s）" % (15, len(fails), tmp))
sys.exit(1 if fails else 0)
