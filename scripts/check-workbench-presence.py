import json, os, sys, time

os.environ["HYPIT_STUDIO_MARK"] = r"C:\dsh\_scratch_b\mark_test.json"
sys.path.insert(0, r"D:\video-skill")
from backend import hypit_bridge as hb   # noqa: E402

fails = []
total = [0]


def check(name, got, want):
    total[0] += 1
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name, "=>", json.dumps(got, ensure_ascii=False)[:150])
    if not ok:
        fails.append((name, got, want))


# 1) 旧格式（有 sessions 无 runs）→ 一律判无主
with open(hb.STUDIO_MARK, "w", encoding="utf-8") as f:
    json.dump({"sessions": ["session-A", "session-B"], "session": "session-B",
               "run": "hypit-e2e", "port": 5179, "ts": time.time()}, f)
check("旧格式·A 无主", hb.studio_status("session-A")["reason"], "not-owner")
check("旧格式·ops 视图不回 up", hb.studio_status()["up"], False)

# 2) claim 写新结构
hb.studio_claim("session-A", "productions/hypit-e2e/assemble.svrun", 5179)
rec = json.load(open(hb.STUDIO_MARK, encoding="utf-8"))
check("新结构有 runs", sorted(rec["runs"]), ["productions/hypit-e2e"])
check("同 run 换主人不累积", len(rec["runs"]), 1)
hb.studio_claim("session-C", "productions/hypit-e2e/assemble.svrun", 5179)
rec = json.load(open(hb.STUDIO_MARK, encoding="utf-8"))
check("换主人后只有一个主人", [v["session"] for v in rec["runs"].values()], ["session-C"])

# 3) _run_matches：claims 传 .svrun、studio 自报 .svml；裸名走 basename 兜底
check("svrun<->svml 同工程", hb._run_matches("productions/hypit-e2e/assemble.svrun",
                                           "productions\\hypit-e2e\\assemble.svml"), True)
check("裸名<->工程目录", hb._run_matches("hypit-e2e", "productions\\hypit-e2e\\assemble.svml"), True)
check("别的工程必须判死", hb._run_matches("productions/futie-replica/clone.svrun",
                                         "productions\\hypit-e2e\\assemble.svml"), False)

# 4) TTL 过期 → 自动回收（不需要谁去 release）
rec = json.load(open(hb.STUDIO_MARK, encoding="utf-8"))
for v in rec["runs"].values():
    v["ts"] = time.time() - hb.STUDIO_CLAIM_TTL - 1
with open(hb.STUDIO_MARK, "w", encoding="utf-8") as f:
    json.dump({"runs": rec["runs"], "ts": time.time()}, f)
check("TTL 过期→not-owner", hb.studio_status("session-C")["reason"], "not-owner")
check("过期项已落盘清掉", json.load(open(hb.STUDIO_MARK, encoding="utf-8"))["runs"], {})

# 5) release 只摘自己名下的
hb.studio_claim("session-A", "productions/a/x.svrun", 5179)
hb.studio_claim("session-B", "productions/b/y.svrun", 5179)
hb.studio_release("session-A")
check("release 只摘自己的", sorted(json.load(open(hb.STUDIO_MARK, encoding="utf-8"))["runs"]), ["productions/b"])

# 6) run-mismatch：studio 自报别的工程 → 判死（这条就是这次的 bug）
hb.studio_claim("session-B", "productions/b/y.svrun", 5179)
hb._probe_studio = lambda port: {"up": True, "tasks": 0, "running": 0, "artifacts": 0}
hb._studio_served_path = lambda port: "productions\\other\\z.svml"
check("run 不匹配→判死", hb.studio_status("session-B")["reason"], "run-mismatch")

# 7) 主人 + run 匹配 + 端口活 → up:true
hb._studio_served_path = lambda port: "productions\\b\\y.svml"
r = hb.studio_status("session-B")
check("主人+匹配+活→up", (r.get("up"), r.get("run_verified")), (True, True))

# 8) session 探不到（生成工程 500）→ 只认登记，仍要求主人
hb._studio_served_path = lambda port: ""
r = hb.studio_status("session-B")
check("拿不到自报 run 时仍认登记", (r.get("up"), r.get("run_verified")), (True, False))
check("非主人仍然 false", hb.studio_status("session-Z")["reason"], "not-owner")

os.remove(hb.STUDIO_MARK)
print("\n=== %d 条断言，失败 %d ===" % (total[0], len(fails)))
sys.exit(1 if fails else 0)
