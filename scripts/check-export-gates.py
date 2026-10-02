"""导出口四道闸的断言（#29）：路径白名单 / 文件名 / origin / 单飞。
跑法：PYTHONIOENCODING=utf-8 python scripts/check-export-gates.py
"""
import os
import sys

sys.path.insert(0, r"D:\video-skill")
from backend import hypit_bridge as hb   # noqa: E402

fails, total = [], [0]


def check(name, got, want):
    total[0] += 1
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name, "got", json_safe(got)[:110], "want", json_safe(want)[:60])
    if not ok:
        fails.append((name, json_safe(got), json_safe(want)))


def json_safe(v):
    import json
    try:
        return json.dumps(v, ensure_ascii=False)
    except Exception:
        return repr(v)


# ② 路径白名单：run 只能是 hypit_runs 下的相对 .svrun
check("run 合法", bool(hb.resolve_run("productions/hypit-e2e/assemble.svrun")[0]), True)
check("run 穿越(..)拒", bool(hb.resolve_run("../etc/passwd.svrun")[0]), False)
check("run 绝对路径拒", bool(hb.resolve_run(r"C:\dsh\hypit_runs\productions\hypit-e2e\assemble.svrun")[0]), False)
check("run 非 .svrun 拒", bool(hb.resolve_run("productions/hypit-e2e/assemble.svml")[0]), False)
check("run 不存在拒", bool(hb.resolve_run("productions/nope/x.svrun")[0]), False)
check("run 空拒", bool(hb.resolve_run("")[0]), False)

# task 只能是 productions 下已存在的一层目录名
check("task 空=只落资产目录", hb.resolve_task(""), ("", ""))
check("task 带分隔符拒", bool(hb.resolve_task("a/b")[1]), True)
check("task 穿越拒", bool(hb.resolve_task("../Windows")[1]), True)
check("task 不存在拒", bool(hb.resolve_task("不存在的任务目录")[1]), True)
real = [d for d in os.listdir(hb.PROD_ROOT) if os.path.isdir(os.path.join(hb.PROD_ROOT, d))]
check("task 真实目录放行", bool(hb.resolve_task(real[0])[0]), True)

# 文件名：只收裸 ASCII .mp4
check("文件名正常保留", hb._safe_name("final-20s.mp4", "export-abc"), "final-20s.mp4")
check("中文文件名回落", hb._safe_name("成片.mp4", "export-abc"), "export-abc.mp4")
check("穿越文件名回落", hb._safe_name("../../evil.mp4", "export-abc"), "export-abc.mp4")
check("反斜杠文件名回落", hb._safe_name(r"..\evil.mp4", "export-abc"), "export-abc.mp4")
check("非 mp4 后缀回落", hb._safe_name("x.exe", "export-abc"), "export-abc.mp4")
check("退化空名回落", hb._safe_name(".mp4", "export-abc"), "export-abc.mp4")
check("短但合法的名字保留", hb._safe_name("a.mp4", "export-abc"), "a.mp4")

# ④ origin：Bearer 或本机 origin，外站一律拒
check("origin 本机渲染层放行", hb.export_authorized(None, "http://127.0.0.1:43129"), True)
check("origin localhost 放行", hb.export_authorized(None, "http://localhost:5173/"), True)
check("origin dsh-app 源放行", hb.export_authorized(None, "dsh-app://anything"), True)
check("origin 外站拒", hb.export_authorized(None, "http://evil.example.com"), False)
check("origin 外站 https 拒", hb.export_authorized(None, "https://evil.example.com:8443"), False)
check("无 origin 无 token 拒", hb.export_authorized(None, None), False)
check("origin 解析不吃 scheme 冒充", hb.export_authorized(None, "http://127.0.0.1.evil.com"), False)

# ③ 免费闸：真跑一遍判定（假 hypit + 临时 sqlite，绝不碰生产库、也不真 build）
hb.DB_PATH = r"C:\dsh\_scratch_b\exp_gates.sqlite"
if os.path.exists(hb.DB_PATH):
    os.remove(hb.DB_PATH)

REAL_HYPIT = hb._hypit


def fake_hypit(argv, timeout=180.0):
    if argv[0] == "plan":
        return 0, '{"format":"hypit.cli-plan@1","ok":true,"targets":["final.video"],' \
                  '"providerRequestCount":4,"requestCount":11}', ""
    return 0, "", ""


runs = "productions/hypit-e2e/assemble.svrun"
run_abs = os.path.join(hb.HYPIT_WS, runs)
con = hb._exp_db()
con.execute("INSERT INTO exp(id,run,state,created,updated) VALUES(?,?,?,?,?)",
            ("selftest01", runs, "queued", 0.0, 0.0))
con.commit()
con.close()
hb._hypit = fake_hypit
hb._export_run("selftest01", run_abs, "", "selftest.mp4")
row = hb._exp_row("selftest01")
check("有 4 段付费生成 → 判死", row["state"], "failed")
check("拒绝理由点名付费段数", "4" in (row["error"] or "") and "付费" in (row["error"] or ""), True)
check("免费闸在取 build 之前就拦（build_id 为空）", row["build_id"], None)

# 反向：providerRequestCount=0 时才继续往下走（下一步是 builds 快照，不是直接失败）
def free_plan(argv, timeout=180.0):
    if argv[0] == "plan":
        return 0, '{"format":"hypit.cli-plan@1","ok":true,"targets":["final.video"],"providerRequestCount":0}', ""
    if argv[0] == "builds":
        return 0, '{"format":"hypit.cli-builds@1","builds":[]}', ""
    if argv[0] == "build":
        return 0, "", ""
    return 0, "", ""

con = hb._exp_db()
con.execute("INSERT INTO exp(id,run,state,created,updated) VALUES(?,?,?,?,?)",
            ("selftest02", runs, "queued", 0.0, 0.0))
con.commit()
con.close()
hb._hypit = free_plan
hb._export_run("selftest02", run_abs, "", "selftest2.mp4")
row2 = hb._exp_row("selftest02")
check("零生成腿才会越过免费闸（走到后面才失败）", "付费" not in (row2["error"] or ""), True)
hb._hypit = REAL_HYPIT
os.remove(hb.DB_PATH)

print("\n=== %d 条断言，失败 %d ===" % (total[0], len(fails)))
sys.exit(1 if fails else 0)
