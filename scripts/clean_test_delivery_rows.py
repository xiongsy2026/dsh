import io
import json
import re
import shutil
WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LED = os.path.join(WS, r".dsh\delivery.json")
BAK = os.path.join(WS, r".dsh\delivery.json.bak-before-test-cleanup")
# 只认这两类合成会话 id；真实会话是 session-<uuid>，不会被匹配到
TEST = re.compile(r"^session-(ANCHOR|PROGRESS)")

rows = json.load(io.open(LED, encoding="utf-8"))
keep = [r for r in rows if not TEST.match(r.get("session") or "")]
drop = [r for r in rows if TEST.match(r.get("session") or "")]
debt = sum(1 for r in drop for e in r.get("expected", []) if not e.get("delivered_at"))

shutil.copy2(LED, BAK)
io.open(LED, "w", encoding="utf-8").write(json.dumps(keep, ensure_ascii=False, indent=1) + "\n")
print("备份 =", BAK)
print("清掉 %d 条测试登记（含 %d 件假欠账），保留 %d 条真实记录" % (len(drop), debt, len(keep)))
print("被清的会话：", sorted({r["session"] for r in drop}))
