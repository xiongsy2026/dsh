r"""段回传 + 装配工程 的回归（0 花费：不调上游、不提交生成，只跑 hypit check/plan 这种免费校验）。

契约见 .dsh/skills/hypit-pipeline/references/monitor-contract.md §11。
跑法：PYTHONIOENCODING=utf-8 python scripts/check-hypit-assemble.py
"""
import json
import os
import re
import shutil
import subprocess
import sys

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HYPIT_WS = os.path.join(WS, "hypit_runs")
GEN = os.path.join(WS, "scripts", "hypit-assemble.py")
sys.path.insert(0, r"D:\video-skill")
sys.path.insert(0, r"D:\video-skill\backend")

total, fails = [0], []


def check(name, got, want):
    total[0] += 1
    if got != want:
        fails.append((name, got, want))
        print("FAIL", name, "\n     实得", str(got)[:220], "\n     期望", str(want)[:220])
    else:
        print("PASS", name, "=>", str(got)[:100])


import hypit_bridge as hb            # noqa: E402
SRC_BRIDGE = open(os.path.join(r"D:\video-skill\backend", "hypit_bridge.py"), encoding="utf-8").read()

# ──  leg 名：段必须按 leg 命名，不能按完成先后 ─────────────────────────────────────
NEED1 = "need:author%3AC%3A%5Cdsh%5Chypit_runs%5Cproductions%5Creplica-final%5Cclone.svml%3A%3A" \
        "component%3A%3Aseg1-video%3Agenerate"
check("1 need id 里取到 leg 名（%3A%3A 编码要先解码）", hb._leg_name({"need": NEED1}), "seg1")
check("1b 未编码的也能取", hb._leg_name({"need": "need:author:x.svml::component::seg12-video:generate"}), "seg12")
check("1c 音频腿去掉后缀", hb._leg_name({"need": "a::component::voicing-audio:render"}), "voicing")
check("1d 没有 component 段 ⇒ 空（宁缺勿错，不瞎起名）", hb._leg_name({"need": "need:author:x.svml"}), "")
check("1e 非法字符进不来（防穿越）",
      hb._leg_name({"need": "a::component::..\\..\\evil-video:generate"}), "")
check("1f 空 body 不炸", hb._leg_name({}), "")

# ── ② 回传：落进工程 clips/，越界不写，且绝不把已花钱的腿判死 ────────────────────────
TMP = os.path.join(HYPIT_WS, "productions", "_assemble-selftest")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(os.path.join(TMP, "clips"), exist_ok=True)
SRCDIR = os.environ.get("HYPIT_JOBS_DIR") or r"D:\video-skill\outputs\_hypit_jobs"
saved_ws, saved_mig = hb.HYPIT_WORKSPACE, hb._DB_MIGRATED["ok"]
hb.HYPIT_WORKSPACE = HYPIT_WS
real_src = None
if os.path.isdir(SRCDIR):
    for f in sorted(os.listdir(SRCDIR)):
        if f.endswith(".mp4") and os.path.getsize(os.path.join(SRCDIR, f)) > 10000:
            real_src = os.path.join(SRCDIR, f)
            break
try:
    body = {"need": NEED1, "project": os.path.join(TMP, "clone.svml")}
    rel = hb._land_clip(body, real_src) if real_src else ""
    check("2 段回传进 <工程>/clips/seg1.mp4 并回相对路径",
          [rel, os.path.isfile(os.path.join(TMP, "clips", "seg1.mp4"))],
          ["productions/_assemble-selftest/clips/seg1.mp4", True] if real_src else ["skipped", False])

    # 重跑同一段 ⇒ 覆盖同一个文件名，不多出一段
    rel2 = hb._land_clip({"need": "a::component::seg1-video:generate", "project": os.path.join(TMP, "clone.svml")},
                         real_src or "")
    check("2b 重跑同一段覆盖同名文件（不会多出 seg9）",
          len([f for f in os.listdir(os.path.join(TMP, "clips")) if f.endswith(".mp4")]), 1)

    check("2c 越界工程路径不回传（自报路径不可信）",
          hb._land_clip({"need": NEED1, "project": r"C:\Windows\x\clone.svml"}, real_src or ""), "")
    check("2d 没 leg 名不回传（宁可缺，不瞎起名）",
          hb._land_clip({"need": "need:author:x", "project": TMP}, real_src or ""), "")
    check("2e 源文件不在 ⇒ 只回空串，绝不抛（已花积分的腿不能被回传失败判死）",
          hb._land_clip(body, os.path.join(TMP, "nope.mp4")), "")
    check("2f 账本加了 clip 列（迁移走 _db 的 ALTER TABLE）",
          '"clip"' in SRC_BRIDGE.split("for col in")[1].split("\n")[0], True)
    check("2g 进度台把 clip 发出去（右栏那行 → clips/segN.mp4 靠它）",
          '"clip": (clip or "") if state == "succeeded"' in SRC_BRIDGE, True)

    # ── ③ 生成器：只写 assemble.*、幂等、0 生成腿、查看器指向它 ───────────────────
    open(os.path.join(TMP, "clone.svml"), "w", encoding="utf-8").write(
        '<?svml using="@hypit/markup@1"?>\n<svml>\n'
        '  <space:Canvas id="canvas" width="720" height="1280"/>\n</svml>\n')
    shutil.copyfile(os.path.join(TMP, "clips", "seg1.mp4"), os.path.join(TMP, "clips", "seg2.mp4"))

    def gen(*extra):
        r = subprocess.run([sys.executable, GEN, "--project", "productions/_assemble-selftest",
                            "--no-verify"] + list(extra), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
        return r

    g1 = gen()
    j1 = json.loads(g1.stdout.strip().splitlines()[-1])
    check("3 生成器写出 assemble.svml/.svrun，段数与画布对得上",
          [j1["segments"], j1["canvas"], sorted(j1["wrote"])],
          [2, "720x1280", ["assemble.svml", "assemble.svrun"]])
    g2 = gen()
    j2 = json.loads(g2.stdout.strip().splitlines()[-1])
    check("3b 幂等：内容没变就不写盘", j2["wrote"], "无变化（已是最新）")
    svml = open(os.path.join(TMP, "assemble.svml"), encoding="utf-8").read()
    check("3c 装配工程只有素材引用（media:Video ×2、一个生成腿都没有）",
          [svml.count("<media:Video"), svml.count("seedance:"), svml.count("h3:")], [2, 0, 0])
    # 🔴 时间轴数学：末段的 at+for 必须正好等于 timeline end（早先写成 at-1 ⇒ studio 投影报
    #    `Temporal Instant projection falls outside ProgramSpace`，台起来了但 500）
    items = [(int(a), int(f)) for a, f in re.findall(r'at="(\d+)f" for="(\d+)f"', svml)]
    end = int(re.search(r'<time:Timeline[^>]*end="(\d+)f"', svml).group(1))
    check("3c2 每段都在时间轴内、末段 at+for 正好等于 end（差一帧投影就炸）",
          [all(a + f <= end for a, f in items), max(a + f for a, f in items) == end, items[0][0]],
          [True, True, 1])
    check("3d 生成器不碰生成工程本体", "clone.svml" not in json.dumps(j1["wrote"]), True)

    hb._DB_MIGRATED["ok"] = True
    picked = hb._pick_svrun("productions/_assemble-selftest")
    check("3e 查看器会挑中 assemble.svrun（sorted 第一个 ⇒ 不用人指定）",
          os.path.basename(picked), "assemble.svrun")

    exe = shutil.which("hypit.cmd") or shutil.which("hypit") or r"C:\Users\Administrator\AppData\Roaming\npm\hypit.cmd"
    p = subprocess.run([exe, "plan", "productions/_assemble-selftest/assemble.svrun", "--json"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=HYPIT_WS, timeout=300)
    d = json.loads(p.stdout)
    check("3f hypit plan 免费校验：0 个付费生成请求（不是 0 就说明混进了生成腿）",
          [d.get("ok"), d.get("providerRequestCount")], [True, 0])
    c = subprocess.run([exe, "check", "productions/_assemble-selftest/assemble.svrun"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=HYPIT_WS, timeout=300)
    check("3g hypit check 过（时间轴/帧数不是拼出来的空壳）", c.returncode, 0)

    # ── ④ 越界与缺料的拒绝 ────────────────────────────────────────────────────────
    r = subprocess.run([sys.executable, GEN, "--project", r"C:\Windows", "--no-verify"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    check("4 工程不在 hypit 工作区内 ⇒ 直接拒", [r.returncode, "工作区" in (r.stdout + r.stderr)], [2, True])
    empty = os.path.join(HYPIT_WS, "productions", "_assemble-selftest-empty")
    os.makedirs(empty, exist_ok=True)
    r = subprocess.run([sys.executable, GEN, "--project", "productions/_assemble-selftest-empty", "--no-verify"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    check("4b clips/ 空 ⇒ 明说没有段，不生成空工程", [r.returncode, "clips/" in (r.stdout + r.stderr)], [2, True])
finally:
    hb.HYPIT_WORKSPACE, hb._DB_MIGRATED["ok"] = saved_ws, saved_mig
    shutil.rmtree(TMP, ignore_errors=True)
    shutil.rmtree(os.path.join(HYPIT_WS, "productions", "_assemble-selftest-empty"), ignore_errors=True)

print("\n=== 回传与装配闸：%d 条断言，失败 %d ===" % (total[0], len(fails)))
for n, g, w in fails:
    print("  FAIL", n, "got", str(g)[:120], "want", str(w)[:120])
sys.exit(1 if fails else 0)
