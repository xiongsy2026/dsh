"""T5 8100 交付读口自查（临时脚本，跑完删）。不起服务、不重启 8100：直接调视图函数 + 钉源码。"""
import json
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
led = os.path.join(tmp, "delivery.json")
os.environ["DELIVERY_LEDGER"] = led
os.environ["DSH_WORKSPACE"] = tmp
os.environ["HYPIT_JOBS_DB"] = os.path.join(tmp, "jobs.sqlite")
sys.path.insert(0, r"D:\video-skill\backend")
import model_admin as M  # noqa: E402
import delivery_ledger as L  # noqa: E402

fails = []


def check(name, got, want):
    a, b = json.dumps(got, ensure_ascii=False, default=str), json.dumps(want, ensure_ascii=False, default=str)
    print(("PASS " if a == b else "FAIL ") + name + ("" if a == b else " => %r 期望 %r" % (got, want)))
    if a != b:
        fails.append(name)


check("1 台账不存在 ⇒ known:true / count:0（真的没欠，不是查不到）",
      (lambda v: [v["known"], v["count"]])(M.delivery_pending_view("session-Z")), [True, 0])

L.open_obligation("session-A", "t1", "omni", ["productions/t1/segments/segment_1.mp4",
                                              "productions/t1/video/final.mp4"],
                  path=led, workspace=tmp, budget_credits=100)
v = M.delivery_pending_view("session-A")
check("2 有欠账 ⇒ count:1、outstanding=2（视图里这个字段是条数，路径另在 paths）",
      [v["count"], v["items"][0]["outstanding"], len(v["items"][0]["paths"])], [1, 2, 2])
check("3 视图里带 skill/task/预算", [v["items"][0]["skill"], v["items"][0]["budget_credits"]], ["omni", 100])
check("4 按会话过滤：别的会话看不到", M.delivery_pending_view("session-B")["count"], 0)
check("5 不带 session ⇒ 看全部", M.delivery_pending_view(None)["count"], 1)
check("6 outstanding_credits 汇总（欠多少分要能一眼看见）", v["outstanding_credits"], 100)

L.open_obligation("session-A", "t2", "hypit", [{"job_id": "abcd1234ef56", "kind": "video"}],
                  path=led, workspace=tmp)
v = M.delivery_pending_view("session-A")
jobpaths = [i["paths"][0] for i in v["items"] if i["skill"] == "hypit"]
check("7 job_id 型期望在视图里显示成 job:<id>（不假装有路径）", jobpaths, ["job:abcd1234ef56"])
check("8 两条任务并存计数对", v["count"], 2)

done = {"abcd1234ef56": True}
M.hypit_bridge.delivery_job_done = lambda jid: bool(done.get(jid))
check("9 腿 succeeded ⇒ hypit 那条清零（读口按 jobs 终态判）",
      [i for i in M.delivery_pending_view("session-A")["items"] if i["skill"] == "hypit"], [])

# 三态：台账坏了必须报 known:false，不许报"不欠"
bad = os.path.join(tmp, "bad.json")
open(bad, "wb").write(b"{ not json")
os.environ["DELIVERY_LEDGER"] = bad
import importlib  # noqa: E402
check("10 台账损坏 ⇒ known:false（读不到 ≠ 没欠账）",
      (lambda x: [x["known"], "error" in x])(M.delivery_pending_view("session-A")), [False, True])
os.environ["DELIVERY_LEDGER"] = led

src = open(os.path.join(r"D:\video-skill\backend", "model_admin.py"), encoding="utf-8").read()
seg = src[src.find('"/api/delivery/pending"'):]
seg = seg[:seg.find("elif")]
check("11 路由只读：这个分支里不许出现 POST/写盘/扣费调用",
      [k in seg for k in ("self._send(200", "do_POST")], [True, False])
check("12 带 CORS（右栏 iframe 要能读）", "delivery_pending_view" in seg, True)

print("\n=== T5 交付读口自查：%d 条，失败 %d ===" % (12, len(fails)))
sys.exit(1 if fails else 0)
