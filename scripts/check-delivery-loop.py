#!/usr/bin/env python
r"""交付闭环闸（PRD-delivery-loop §6 的 V1/V2/V4 部分 + 台账语义真值表）。

跑法：python scripts/check-delivery-loop.py —— 全程临时目录，绝不动生产台账 <工作区>\.dsh\delivery.json。
V7（不双花）要等 T4 的提交锚点落地后补进来；纯函数 decide() 整块抠出来跑真值表，不依赖任何进程活着。
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, r"D:\video-skill\backend")
import delivery_ledger as L  # noqa: E402

fails = []


def check(name, got, want):
    a, b = json.dumps(got, ensure_ascii=False, default=str), json.dumps(want, ensure_ascii=False, default=str)
    print(("PASS " if a == b else "FAIL ") + name + ("" if a == b else " => %r 期望 %r" % (got, want)))
    if a != b:
        fails.append(name)


# ---- decide 真值表（size_of 注入，完全不碰磁盘）----
def ent(paths, created=1000.0, ttl=100.0):
    return {"session": "s", "task": "t", "skill": "omni", "created_at": created, "ttl_sec": ttl,
            "closed_at": None, "expired_at": None,
            "expected": [{"seq": i + 1, "path": p, "kind": "video", "delivered_at": None}
                         for i, p in enumerate(paths)]}

sizes = {"a.mp4": 10, "b.mp4": 0}          # b 存在但是 0 字节 ⇒ 不算交付
sz = lambda p: sizes.get(p)                # noqa: E731

st, e = decide_all = L.decide(ent(["x.mp4"]), 1010.0, lambda p: None)
check("1 全未落盘 ⇒ pending", st, "pending")
check("2 未落盘的没有 delivered_at", e["expected"][0]["delivered_at"], None)
check("3 超 TTL 仍未清账 ⇒ expired", L.decide(ent(["x.mp4"]), 1200.0, lambda p: None)[0], "expired")
check("4 超 TTL 但已全落盘 ⇒ closed 优先", L.decide(ent(["a.mp4"]), 1200.0, lambda p: 10)[0], "closed")
st, e = L.decide(ent(["a.mp4", "b.mp4"]), 1010.0, sz)
check("5 部分落盘 ⇒ pending", st, "pending")
check("6 落盘那条打了时间戳、0 字节那条没打",
      [bool(e["expected"][0]["delivered_at"]), bool(e["expected"][1]["delivered_at"])], [True, False])
st, e = L.decide(ent(["a.mp4"]), 1010.0, lambda p: 10)
first = e["closed_at"]
st2, e2 = L.decide(e, 9999.0, lambda p: 10)
check("7 closed 幂等（closed_at 不被后面刷新改掉）", [st2, e2["closed_at"] == first], ["closed", True])
check("8 空 expected ⇒ closed（没东西欠；正常路径进不来，open_obligation 会先拒）",
      L.decide(ent([]), 1010.0, lambda p: 10)[0], "closed")

# ---- 读写器（临时目录，绝不动生产台账）----
tmp = tempfile.mkdtemp()
led = os.path.join(tmp, "delivery.json")
ws = os.path.join(tmp, "ws")
os.makedirs(os.path.join(ws, "productions", "t1", "outputs"), exist_ok=True)
os.makedirs(os.path.join(ws, "productions", "t2", "outputs"), exist_ok=True)

L.open_obligation("session-A", "t1", "omni",
                  [os.path.join(ws, "productions/t1/outputs/seg1.mp4").replace("\\", "/"),
                   ws + "/productions/t1/outputs/seg2.mp4"],
                  workspace=ws, path=led, budget_credits=100, now=1000.0)
rows = L._read(led)
check("9 登记一条：两条期望、seq 连续", [len(rows), [i["seq"] for i in rows[0]["expected"]]], [1, [1, 2]])
check("10 绝对工作区路径被归一成相对+正斜杠",
      [i["path"] for i in rows[0]["expected"]],
      ["productions/t1/outputs/seg1.mp4", "productions/t1/outputs/seg2.mp4"])

L.open_obligation("session-A", "t1", "omni", ["productions/t1/outputs/only.mp4"], workspace=ws, path=led, now=1001.0)
rows = L._read(led)
check("11 同 (session,task) 覆盖不叠加", [len(rows), len(rows[0]["expected"])], [1, 1])
L.open_obligation("session-A", "t2", "hypit", ["productions/t2/outputs/x.mp4"], workspace=ws, path=led, now=1002.0)
check("12 不同 task 并存", len(L._read(led)), 2)
try:
    L.open_obligation("s", "t", "omni", ["", None], workspace=ws, path=led, now=1.0)
    check("13 全空路径要报错", "no-raise", "ValueError")
except ValueError:
    check("13 全空路径要报错", "raised", "raised")

check("14 未落盘 ⇒ pending 2 条", len(L.pending(path=led, workspace=ws, now=1003.0)), 2)
check("15 按会话过滤", len(L.pending(session="session-B", path=led, workspace=ws, now=1003.0)), 0)

with open(os.path.join(ws, "productions/t2/outputs/x.mp4"), "wb") as f:
    f.write(b"x" * 5)
p = L.pending(path=led, workspace=ws, now=1004.0)
check("16 文件真落盘 ⇒ 自动销账（少一条）", [len(p), [x["task"] for x in p]], [1, ["t1"]])
check("17 销账那条已 closed（不在 pending 里）", any(x["task"] == "t2" for x in p), False)

n = L.sweep(path=led, workspace=ws, now=1005.0)
check("18 sweep 清掉 closed 的那条", [n, len(L._read(led))], [1, 1])
L.open_obligation("session-A", "t3", "omni", ["productions/t1/outputs/z.mp4"], workspace=ws, path=led,
                  now=1006.0, ttl_sec=1.0)
check("19 TTL=1s 到期 ⇒ 不再算欠账", len(L.pending(path=led, workspace=ws, now=2000.0)), 1)
check("20 到期条目被标 expired（不是被删）",
      [r.get("expired_at") is not None for r in L._read(led) if r["task"] == "t3"], [True])
check("21 台账文件是合法 JSON 且没留 .tmp",
      [os.path.exists(led), [f for f in os.listdir(tmp) if f.startswith("delivery.json.tmp")]], [True, []])

# 三个提交锚点各自成篇（要各自隔离 env：临时 DB / 临时台账 / 把 _run 换成空操作），这里做统一入口
import subprocess
SIBS = ["check-delivery-anchor-omni.py", "check-delivery-anchor-hypit.py",
        "check-delivery-anchor-dewatermark.py", "check-delivery-read-api.py",
        "check-deliver-pack.py"]
anchor_total = 0
for s in SIBS:
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), s)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    # 只认含「条，失败」的那行汇总（临时目录路径里也有数字，别拿末行硬取）
    sum_lines = [x for x in (r.stdout or "").splitlines() if "条，失败" in x]
    tail = sum_lines[-1:] or (r.stdout or "").strip().splitlines()[-1:] or ["无输出"]
    ok = r.returncode == 0
    head = tail[0].split("条，失败")[0].strip()          # "…自查：15 " 这种
    k = len(head)
    while k > 0 and head[k - 1].isdigit():
        k -= 1
    anchor_total += (int(head[k:]) if (ok and k < len(head)) else 0)
    print(("PASS " if ok else "FAIL ") + "锚点闸 " + s + " -> " + (tail[0][:70]))
    if not ok:
        fails.append(s)
        print((r.stderr or "")[-400:])

# V7 不双花：查询失败/超时都要把 job_id 带回来（据此续查），否则"重提交"就没有拦头
gw = open(os.path.join("D:", "video-skill", "backend", "gateway.py"), encoding="utf-8").read()
check("V7 去水印：失败路径都回 job_id 且明写不重提交",
      [gw.count(chr(34) + "job_id" + chr(34) + ": tid") >= 3, ("别重提交" in gw or "不重提交" in gw)], [True, True])

print(chr(10) + "=== 交付闭环闸：台账 21 + 锚点与读口 " + str(anchor_total) + " + V7 1 = " + str(21 + anchor_total + 1) + " 条，失败 " + str(len(fails)) + " ===")
sys.exit(1 if fails else 0)
