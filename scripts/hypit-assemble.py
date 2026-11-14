r"""装配工程生成器：把已成功的生成段落成【素材引用】的 assemble.svml，让导演台能看见片段。

为什么要它（2026-10-03 实测）：hypit 的编辑期投影【不执行异步腿】
（@hypit/hypit/packages/runtime-host-node/src/index.ts:308 "refuses asynchronous capabilities"），
所以只要工程里还挂着 seedance/h3 的生成腿，studio 的 `/__studio/session` 就回 500
（红字 `Studio projection is unresolved: @hypit/seedance@1#seedance-2-mini`），
源文件/产物/预览三块全空 —— 段跑完十条也不会在导演台上出现任何东西。
把段落成本地素材、另开一个 0 生成腿的装配工程，时间轴才会随段增加。

跑法（幂等，可反复执行）：
    python scripts/hypit-assemble.py --project productions/replica-final
    python scripts/hypit-assemble.py --project <工程目录> --backfill   # 先从网关账本回填缺的段
它只写 <工程>/assemble.svml 与 assemble.svrun，绝不改 clone.svml（生成工程本体）。
"""
import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.parse
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")

WS = os.environ.get("DSH_WORKSPACE") or WS
HYPIT_WS = os.environ.get("HYPIT_WORKSPACE") or os.path.join(WS, "hypit_runs")
LEDGER = os.environ.get("HYPIT_JOBS_DB") or os.path.join(BACKEND, r".gateway_jobs.sqlite")
FPS = 30          # 与手写那份 assemble 一致（space:Canvas + program:Clock frame-rate）


def die(msg, code=2):
    print(msg)
    sys.exit(code)


def proj_dir(arg: str) -> str:
    p = arg.strip().strip('"')
    cand = p if os.path.isabs(p) else os.path.join(HYPIT_WS, p.replace("\\", "/"))
    real = os.path.realpath(cand)
    if not real.startswith(os.path.realpath(HYPIT_WS) + os.sep):
        die(f"工程目录必须在工作区 {HYPIT_WS} 里面：{arg}")
    if not os.path.isdir(real):
        die(f"没有这个工程目录：{real}")
    return real


def leg_sort_key(name: str):
    m = re.match(r"^(.*?)(\d+)$", name)
    return (m.group(1), int(m.group(2)), name) if m else (name, 0, name)


def ffprobe_dur(path: str) -> float:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", path], capture_output=True, text=True, timeout=60)
        return float(out.stdout.strip().splitlines()[0])
    except Exception:
        return 0.0


def canvas_size(d: str) -> tuple:
    """从生成工程的 .svml 里读画布尺寸（装配工程必须和它一致，否则时间轴对不上画面）。"""
    for f in sorted(os.listdir(d)):
        if not f.endswith(".svml") or f == "assemble.svml":
            continue
        txt = open(os.path.join(d, f), encoding="utf-8", errors="replace").read()
        m = re.search(r'<space:Canvas[^>]*width="(\d+)"[^>]*height="(\d+)"', txt)
        if m:
            return int(m.group(1)), int(m.group(2))
    return 576, 1024


def backfill(d: str, name: str) -> list:
    """从网关账本把该工程已成功的段复制进 clips/<leg>.mp4（回传代码上线前跑的腿靠这个补）。"""
    if not os.path.isfile(LEDGER):
        return [f"！账本不在 {LEDGER}，跳过回填"]
    con = sqlite3.connect("file:" + LEDGER.replace("\\", "/") + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    notes = []
    clips = os.path.join(d, "clips")
    try:
        rows = con.execute("select id,label,request,output from jobs where run like ? and state='succeeded'"
                           " order by created", (f"%{name}%",)).fetchall()
        for r in rows:
            out = str(r["output"] or "")
            fn = out.rsplit("/", 1)[-1]
            src = os.path.join(os.path.join(BACKEND, r"outputs\_hypit_jobs"), fn) if fn else ""
            if not fn or not os.path.isfile(src):
                continue
            try:
                need = urllib.parse.unquote(str(json.loads(r["request"] or "{}").get("need") or ""))
                comp = need.split("::")[-1].split(":")[0].strip()
                leg = re.sub(r"-(video|clip|image|audio)$", "", comp)
            except (ValueError, IndexError):
                leg = ""
            if not leg or not re.match(r"^[\w.\-]+$", leg):
                continue
            dst = os.path.join(clips, f"{leg}.mp4")
            if not os.path.isfile(dst):
                os.makedirs(clips, exist_ok=True)
                shutil.copyfile(src, dst)
                notes.append(f"回填 {leg}.mp4 ← 账本 {r['id'][:8]}")
    finally:
        con.close()
    return notes


LOOK_SVS = ('<?svml using="@hypit/svs@1"?>\n<sheet version="1">\n'
            '  film.main { background: #000000; }\n'
            '  media.full {\n    fit: cover;\n    stack-order: 10;\n  }\n</sheet>\n')


def ensure_look(d: str) -> bool:
    """`media-track:Item.appearance` 是【必填】（实测报 `media-track:Item.appearance must be a reference.`），
    所以没有 look.svs 就补一份标准最小表 —— 各项目这份文件逐字节相同（replica-final 与
    video_1789805487 都是 152 字节），不是发明样式。返回是否本来就存在。"""
    p = os.path.join(d, "look.svs")
    had = os.path.isfile(p)
    if not had:
        with open(p, "w", encoding="utf-8") as f:
            f.write(LOOK_SVS)
    return had


def build(d: str) -> dict:
    clips = os.path.join(d, "clips")
    files = sorted([f for f in (os.listdir(clips) if os.path.isdir(clips) else [])
                    if f.lower().endswith(".mp4")], key=lambda f: leg_sort_key(os.path.splitext(f)[0]))
    if not files:
        die("clips/ 里一个段都没有 —— 先跑构建（或加 --backfill 从账本回填）")
    w, h = canvas_size(d)
    had_look = ensure_look(d)
    # appearance 是必填，而 look.svs 现在保证存在 ⇒ 一律引用它（做成两态会让第二次运行重写一遍，破坏幂等）
    has_look = True
    items, media, norm, at = [], [], [], 1
    for i, f in enumerate(files, 1):
        dur = ffprobe_dur(os.path.join(clips, f))
        frames = max(1, int(round(dur * FPS)))
        media.append(f'  <media:Video id="s{i}" src="./clips/{f}"/>')
        norm.append(f'  <pipeline:Normalize id="n{i}" source={{s{i}}} clock={{clock}} '
                    f'video="primary-moving" audio="none" span-authority="video"/>')
        ap = ' appearance={look.media.full}' if has_look else ""
        items.append(f'    <media-track:Item id="t{i}" media={{n{i}.media}} frame={{full}}{ap} '
                     f'at="{at}f" for="{frames}f"/>')
        at += frames
        norm.append(f"  <!-- 段{i} {f} 实测 {dur:.2f} 秒 = {frames} 帧 @ {FPS}fps -->")
    # 时间轴 end = 最后一段的 at+for（手写那份：311f + 155f = 466f）。
    # 早先写成 at-1 ⇒ 末段正好压在边界上，studio 投影报 `Temporal Instant projection falls outside ProgramSpace`
    total = at
    look_imp = '  <import as="look" source="./look.svs"/>\n' if has_look else ""
    film_ap = ' appearance={look.film.main}' if has_look else ""
    svml = f'''<?svml using="@hypit/markup@1"?>
<svml>
  <import as="media" from="@hypit/media@1"/>
  <import as="pipeline" from="@hypit/media-pipeline@1"/>
  <import as="time" from="@hypit/timeline-author@1"/>
  <import as="media-track" from="@hypit/media-track@1"/>
  <import as="space" from="@hypit/spatial@1"/>
  <import as="program" from="@hypit/program-space@1"/>
  <import as="film" from="@hypit/film@1"/>
  <import as="render" from="@hypit/render-hyperframes@1"/>
{look_imp}
  <!-- ⚠️ 本文件由 scripts/hypit-assemble.py 生成，别手改（下次生成会覆盖）。
       全部是【素材引用】⇒ 0 个付费生成请求；存在的意义：生成工程的编辑期投影拒绝异步腿（studio 500），
       只有这个工程能在导演台里看到片段与时间轴。当前 {len(files)} 段。 -->
  <space:Canvas id="canvas" width="{w}" height="{h}"/>
  <program:Clock id="clock" frame-rate="{FPS}"/>
  <space:Frame id="full" within={{canvas}} left="0%" top="0%" right="100%" bottom="100%"/>

{chr(10).join(media)}

{chr(10).join(norm)}

  <time:Timeline id="tl" clock={{clock}} end="{total}f"/>

  <media-track:Track id="pic" timeline={{tl.timeline}} canvas={{canvas}}>
{chr(10).join(items)}
  </media-track:Track>

  <film:Film id="main" canvas={{canvas}} timeline={{tl.timeline}}{film_ap}>
    <film:Track source={{pic.visual}}/>
  </film:Film>

  <render:Video id="final" composition={{main.composition}} timeline={{tl.timeline}}/>
</svml>
'''
    svrun = ('<?svml using="@hypit/run-markup@1"?>\n<svrun version="1">\n'
             '  <author source="./assemble.svml"/>\n  <target output="final.video"/>\n</svrun>\n')
    changed = []
    for fn, body in (("assemble.svml", svml), ("assemble.svrun", svrun)):
        p = os.path.join(d, fn)
        old = open(p, encoding="utf-8").read() if os.path.isfile(p) else None
        if old != body:
            open(p, "w", encoding="utf-8").write(body)
            changed.append(fn)
    return {"project": os.path.basename(d), "segments": len(files), "frames": total,
            "seconds": round(total / FPS, 2), "canvas": f"{w}x{h}",
            "look": "已有" if had_look else "补了一份标准最小表（appearance 必填）",
            "wrote": changed or "无变化（已是最新）"}


def hypit_bin() -> str:
    """npm 在 Windows 装的是 hypit.cmd，subprocess 不带 shell 时找不到裸名 `hypit`。"""
    return (shutil.which("hypit.cmd") or shutil.which("hypit")
            or r"C:\Users\Administrator\AppData\Roaming\npm\hypit.cmd")


def hypit_verify(d: str) -> dict:
    """免费校验：hypit check 过 + plan 的 providerRequestCount 必须是 0（不是 0 就说明混进了生成腿）。"""
    run = os.path.join(d, "assemble.svrun")
    out, exe = {}, None
    try:
        exe = hypit_bin()
        c = subprocess.run([exe, "check", run], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          cwd=HYPIT_WS, timeout=300)
        out["check"] = "OK" if c.returncode == 0 else (c.stderr or c.stdout)[:160]
        p = subprocess.run([exe, "plan", run, "--json"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          cwd=HYPIT_WS, timeout=300)
        d2 = json.loads(p.stdout)
        out["plan_ok"] = d2.get("ok")
        out["providerRequestCount"] = d2.get("providerRequestCount")
    except FileNotFoundError:
        out["check"] = f"hypit 找不到（试过 {exe}）"
    except Exception as e:
        out["plan_ok"] = f"读不到 plan：{type(e).__name__}: {str(e)[:120]}"
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="生成 0 花费的装配工程，让导演台看得见片段")
    ap.add_argument("--project", required=True, help="工程目录（相对 hypit_runs 或绝对路径）")
    ap.add_argument("--backfill", action="store_true", help="先从网关账本把已成功的段复制进 clips/")
    ap.add_argument("--no-verify", action="store_true", help="跳过 hypit check/plan（离线快跑）")
    a = ap.parse_args()
    d = proj_dir(a.project)
    notes = backfill(d, os.path.basename(d)) if a.backfill else []
    for n in notes:
        print(n)
    res = build(d)
    print(json.dumps(res, ensure_ascii=False))
    if not a.no_verify:
        print(json.dumps(hypit_verify(d), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
