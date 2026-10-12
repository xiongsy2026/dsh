#!/usr/bin/env python
"""交付三件套脚本 deliver_pack.py 的闸（V6）。

钉的是"坏输入必须非 0 退出" —— 这条链的 purpose 就是不让 agent 拿一个坏包当交付收工，
所以它的退出码比输出好不好看重要得多。
正例走 --no-net：直链可达性归 check-svc-8100.py 管（闸不依赖外部进程活着，那是假保险）。
跑法：python scripts/check-deliver-pack.py
"""
import json
import time
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.join(HERE, "deliver_pack.py")
REAL = os.path.join(os.path.dirname(HERE), "productions", "2026-09-26-铁软糖", "outputs", "final-20s.mp4")
out = []


def _safe(s):
    """按【输出流自己的编码】兜底；GBK 控制台上一个 U+21D2 就能把整条闸崩在半路
       —— 闸崩不等于闸红，崩了等于没跑。"""
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        s.encode(enc)
        return s
    except (UnicodeEncodeError, LookupError):
        return s.encode(enc, "replace").decode(enc, "replace")


def check(name, got, want):
    ok = json.dumps(got, ensure_ascii=False, default=str) == json.dumps(want, ensure_ascii=False, default=str)
    out.append((ok, name))
    print(_safe(("PASS " if ok else "FAIL ") + name + ("" if ok else " => %r 期望 %r" % (got, want))))


def run(*args):
    # 旧用例（1/6/7/8/13）只测"链对不对、名对不对、布局对不对"，它们的 fixture 当然没 present 过
    # ⇒ 显式跳过 present 这条规则，别让它把无关用例全带红。present 本身由 14a-14e 严格测。
    env = dict(os.environ, DELIVER_PACK_NO_PRESENT="1")
    return subprocess.run([sys.executable, PACK] + list(args), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=180, env=env)


check("0 脚本在", os.path.isfile(PACK), True)
check("0b 真成片在盘上（没有就别跑这条闸）", os.path.isfile(REAL), True)

r = run(REAL, "--no-net", "--json")
j = json.loads(r.stdout or "{}")
check("1 真片 ⇒ 退出 0 且 ok", [r.returncode, j.get("ok")], [0, True])
check("2 三件套齐（路径/直链/≥1 张帧）",
      [bool(j.get("video")), bool(j.get("link")), len(j.get("frames") or []) >= 1], [True, True, True])
check("3 markdown 里三样都在", [s in j.get("markdown", "") for s in ("**成片**", "**直链**", "![预览1]")],
      [True, True, True])
check("4 直链形状对（/api/film/file?task=…&rel=outputs/…）",
      ["/api/film/file?task=" in j.get("link", ""), "rel=outputs%2F" in j.get("link", "") or "rel=outputs/" in j.get("link", "")],
      [True, True])

tmp = tempfile.mkdtemp()
bad = os.path.join(tmp, "不存在.mp4")
check("5 文件不存在 ⇒ 非 0 退出", run(bad, "--no-net").returncode != 0, True)

tiny = os.path.join(tmp, "outputs")
os.makedirs(tiny, exist_ok=True)
p = os.path.join(tiny, "tiny.mp4")
open(p, "wb").write(b"0" * 500)
check("6 只有 500 字节 ⇒ 不算交付", run(p, "--no-net").returncode != 0, True)

cn = os.path.join(tmp, "outputs", "成品.mp4")
open(cn, "wb").write(b"0" * 20000)
r = run(cn, "--no-net", "--json")
check("7 中文文件名 ⇒ 非 0（8100 静态口要 ASCII 名）",
      [r.returncode != 0, json.loads(r.stdout or "{}").get("checks", {}).get("文件名是 ASCII")], [True, False])

check("8 不在 productions/<task>/outputs 下 ⇒ 非 0",
      run(os.path.join(tmp, "loose.mp4"), "--no-net").returncode != 0, True)
open(os.path.join(tmp, "loose.mp4"), "wb").write(b"0" * 20000)
r = run(os.path.join(tmp, "loose.mp4"), "--no-net", "--json")
check("8b 平铺在临时目录 ⇒ 该条校验为 False",
      json.loads(r.stdout or "{}").get("checks", {}).get("在 productions/<task>/…/outputs 下"), False)

src = open(PACK, encoding="utf-8").read()

# 14 present 硬校验（2026-10-09 用户指令"必须强制预览，客户体验感好"）：
#     没有那张「打开」带菜单（用默认应用打开 / 在文件资源管理器中显示 / 下载图片·下载视频）的卡，客户看到的就是裸链接 ⇒ 不算交付完。
#     判据是内核的附件复制件（attachments/v1/files/<原名>），且必须比内容哈希 —— 名字会撞。
import hashlib as _hl
import shutil as _sh
def _sha(x):
    h = _hl.sha256()
    with open(x, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()
pv = os.path.join(tmp, "productions", "task14", "outputs", "one.mp4")
os.makedirs(os.path.dirname(pv), exist_ok=True)
open(pv, "wb").write(b"M" * 20000)
store = os.path.join(tmp, "store14")
os.makedirs(store, exist_ok=True)
env14 = dict(os.environ, DSH_ATTACHMENTS_DIR=store)
def run14(extra_env=None):
    e = dict(env14)
    e.update(extra_env or {})
    r = subprocess.run([sys.executable, "-X", "utf8", PACK, pv, "--no-net", "--json"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=e)
    try:
        return r.returncode, json.loads(r.stdout or "{}")
    except Exception:
        return r.returncode, {}
rc14, j14 = run14()
check("14a 附件存储里没有这个文件 ⇒ 该条校验 False",
      j14.get("checks", {}).get("已 present(预览卡可用)"), False)
check("14b 状态=missing 且 markdown 点名要 present",
      [j14.get("presented", {}).get("status"), "present" in (j14.get("markdown") or "")],
      ["missing", True])
_sh.copy(pv, os.path.join(store, "one.mp4"))
rc14b, j14b = run14()
check("14c 同名同内容（真 present 过）⇒ 该条校验为 True（假 mp4 抽不出帧 ⇒ 不看总退出码）",
      [j14b.get("checks", {}).get("已 present(预览卡可用)"), j14b.get("presented", {}).get("status")],
      [True, "presented"])
open(os.path.join(store, "one.mp4"), "wb").write(b"OTHER" * 4000)
rc14c, j14c = run14()
check("14d 🔴 同名不同内容（聊天附件撞名）⇒ 仍判不合格",
      [rc14c != 0, j14c.get("presented", {}).get("status")], [True, "name-collided"])
rc14e, j14e = run14({"DELIVER_PACK_NO_PRESENT": "1"})
check("14e 非 DSH 环境的显式开关：该条放行且不再吓唬人",
      [j14e.get("checks", {}).get("已 present(预览卡可用)"), j14e.get("presented", {}).get("status"),
       "没 present" in (j14e.get("markdown") or "")], [True, "unchecked", False])

# 13 段目录布局（productions/<task>/<seg>/outputs/x.mp4）—— 2026-10-09 真事故：
#    rel 写死 "outputs/" 会丢掉中间层 ⇒ 直链 404、抽帧链 404，用户看到的就是"预览和控件都不见了"。
seg_root = os.path.join(tmp, "productions", "task13", "prime-3seg", "outputs")
os.makedirs(seg_root, exist_ok=True)
p13 = os.path.join(seg_root, "full15.mp4")
open(p13, "wb").write(b"0" * 20000)
j13 = json.loads(run(p13, "--no-net", "--json").stdout or "{}")
check("13a 段目录的 rel_dir 带上中间层", j13.get("rel_dir"), "prime-3seg/outputs")
check("13b task 仍是 productions 下那一层（不是段目录）", j13.get("task"), "task13")
check("13c 直链含中间层（旧写法拼成 rel=outputs/ ⇒ 404）",
      "rel=prime-3seg/outputs/full15.mp4" in j13.get("link", ""), True)
check("13d 这种布局的目录校验应为 True",
      j13.get("checks", {}).get("在 productions/<task>/…/outputs 下"), True)
check("13e 源码里不再出现写死的 outputs/ 拼接", '"outputs/" + os.path.basename' in src, False)
check("9 抽帧只收 >2KB（黑帧/错误页也是文件，别拿它当画面）", "2048" in src, True)
check("10 不写假数据：只读输入 + 写 preview 图，绝不碰账本",
      ["sqlite" not in src, "gateway" not in src.lower()], [True, True])
# 2026-10-09 真事故：print 走控制台代码页（GBK），调用方按 UTF-8 解 ⇒ 中文键名/markdown 全乱码，
# #3/#7/#8b 三条一起红。钉"必须按 UTF-8 字节写"，防止有人把 emit() 改回 print()。
check("11 输出走 UTF-8 字节（emit），不许再用裸 print",
      ["def emit(" in src, "print(json.dumps" in src, "print(rep[" in src], [True, False, False])
# 人类可读那条路（不带 --json）也必须能读：agent 默认就是这么调的
r2 = run(REAL, "--no-net")
try:
    md2 = json.dumps(r2.stdout, ensure_ascii=False)
except Exception:
    md2 = ""
check("12 非 --json 输出里中文没花（成片/直链/校验）",
      all(k in (r2.stdout or "") for k in ("成片", "直链", "校验")), True)


# ── 15-17 不在 productions/<任务>/ 下的成片：必须【快速返回 + 明说原因】，且回执扫描必须有界 ──
_OUTSIDE = os.path.join(HERE, "_dp_outside")
os.makedirs(_OUTSIDE, exist_ok=True)
_FAKE = os.path.join(_OUTSIDE, "video-x.mp4")
if not os.path.isfile(_FAKE):
    with open(_FAKE, "wb") as _f:                       # >10KB 的占位（内容不重要，判据看的是路径）
        _f.write(b"\x00" * 20000)
_t0 = time.time()
try:
    r15 = subprocess.run([sys.executable, PACK, _FAKE, "--json", "--no-net"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=30, env=dict(os.environ, DELIVER_PACK_NO_PRESENT="1"))
    _el = time.time() - _t0
    j15 = json.loads((r15.stdout or "").strip().partition("{")[0] or "{") if False else \
        json.loads("{" + (r15.stdout or "").split("{", 1)[1])
except subprocess.TimeoutExpired:
    _el, j15 = 999.0, {}
check("15 🔴 根目录/工程外的成片 ⇒ 30 秒内返回（曾经拿盘根递归扫整块盘=像卡死）", _el < 30, True)
check("16 这种交付判 FAIL，并明说【下载按钮不会出现】+ 该挪去哪",
      [j15.get("ok"), any("下载按钮" in x for x in (j15.get("notes") or [])),
       j15.get("checks", {}).get("成片在 productions/<任务>/ 下")], [False, True, False])
# 反向对照：同一个文件放进 productions/<任务>/outputs 后，那条路径判据必须翻成 True（证明它真在判路径）
_TASK = os.path.join(HERE, "_dp_prod", "productions", "t-probe", "outputs")
os.makedirs(_TASK, exist_ok=True)
_IN = os.path.join(_TASK, "video-x.mp4")
_sh.copyfile(_FAKE, _IN)
r17 = subprocess.run([sys.executable, PACK, _IN, "--json", "--no-net"],
                     capture_output=True, text=True, encoding="utf-8", errors="replace",
                     timeout=30, env=dict(os.environ, DELIVER_PACK_NO_PRESENT="1"))
j17 = json.loads("{" + (r17.stdout or "").split("{", 1)[1])
check("17 反向对照：放进 productions/<任务>/outputs 后路径判据翻真（不是恒 FAIL 的假严格）",
      j17.get("checks", {}).get("在 productions/<task>/…/outputs 下"), True)
# 18 有界性：latest_receipt 拿盘根当 root 必须直接不扫（这条是 15 的根因，单独钉）
sys.path.insert(0, r"D:\video-skill")
from backend import skill_contract as _sc                      # noqa: E402
_t1 = time.time()
_root_scan = _sc.latest_receipt(os.path.splitdrive(os.path.abspath(_FAKE))[0] + os.sep)
check("18 latest_receipt 拒绝拿文件系统根当 root（有界 + 不扫盘，返回空且 <2 秒）",
      [_root_scan, (time.time() - _t1) < 2], [{}, True])
_sh.rmtree(_OUTSIDE, ignore_errors=True)
_sh.rmtree(os.path.join(HERE, "_dp_prod"), ignore_errors=True)

# ---- 19-22 提示词与模型身份必须跟着交付走（2026-10-10 用户："回执也没有…提示词也没有展示"）----
# 自造沙盒，不依赖盘上某条现网任务（那是"钉现场"，别人一删就随机红）。
_P2 = os.path.join(HERE, "_dp_prod2", "productions", "probe2", "outputs")
_sh.rmtree(os.path.join(HERE, "_dp_prod2"), ignore_errors=True)
_s1, _s2 = os.path.join(_P2, "seg1"), os.path.join(_P2, "seg2")
for _d in (_s1, _s2):
    os.makedirs(_d, exist_ok=True)
_v1 = os.path.join(_s1, "final.mp4")
_sh.copyfile(REAL, _v1)
_sh.copyfile(REAL, os.path.join(_s2, "final.mp4"))
_PRO = os.path.join(_P2, "..", "inputs", "p.png")
os.makedirs(os.path.dirname(_PRO), exist_ok=True)
open(_PRO, "wb").write(b"png-probe")


def _fake_receipt(dst, prompt_text, model_cell):
    open(os.path.join(dst, "prompt.txt"), "w", encoding="utf-8").write(prompt_text)
    json.dump({"source": "step1_5_gpt_compiler", "model": model_cell.split("@")[0],
               "provider": model_cell.split("@")[-1], "ledger": "off",
               "storyboard_sha8": "x", "prompt_chars": len(prompt_text),
               "target_duration": 10.0},
              open(os.path.join(dst, "prompt_meta.json"), "w", encoding="utf-8"))
    open(os.path.join(dst, "storyboard.json"), "w", encoding="utf-8").write('[{"shot":1}]')
    _sc.write_receipt("super-clone", dst,
                      steps={"compile-prompt": os.path.join(dst, "prompt.txt"),
                             "storyboard": os.path.join(dst, "storyboard.json"),
                             "compile-bound": os.path.join(dst, "prompt_meta.json"),
                             "negative-lock": os.path.join(dst, "prompt.txt")},
                      inputs=[_PRO],
                      # 🔴 2026-10-12 起"没模型名 ⇒ 那一格打不上钩"，夹具每一格都得给（本地步给 NO_MODEL）
                      models={"compile-prompt": model_cell,
                              "storyboard": "mimo-v2.5@token-plan-cn.xiaomimimo.com · 账本外·直连厂商",
                              "compile-bound": model_cell, "negative-lock": model_cell})


_fake_receipt(_s1, "SEG1 的提示词 不得出现字幕 水印", "gpt-5.6-sol@hub.nocannobb.com")
_fake_receipt(_s2, "SEG2 的提示词 不得出现字幕 水印", "grok-4.6@api.x.ai")
# 🔴 让 seg2 的【文件时间】更新：交付 seg1 却读到 seg2 的回执 = 张冠李戴，这是 2026-10-10 实测踩到的形状
_now = time.time() + 30
os.utime(os.path.join(_s2, ".skill_receipt.json"), (_now, _now))
_r19 = run(_v1, "--no-net", "--json")
_j19 = json.loads(_r19.stdout or "{}")
_md19 = _j19.get("markdown", "")
check("19 交付 markdown 带【这一段喂给引擎的提示词】那一块（含字数/sha8/全文直链）",
      ["这一段喂给引擎的提示词" in _md19, bool((_j19.get("prompt") or {}).get("link"))], [True, True])
check("20 🔴 提示词必须取自【这条视频自己那一格】：交 seg1 不许贴 seg2 的回执（seg2 那份更新，正是诱饵）",
      ["SEG2" not in _md19, "seg1/prompt.txt" in _md19.replace("%2F", "/"), "SEG1" in _md19],
      [True, True, True])
check("21 执行表有「模型/路由」列且印的是这份产物记下的模型（不是空白糊过去）",
      ["模型/路由" in _md19, "gpt-5.6-sol@hub.nocannobb.com" in _md19], [True, True])
check("22 直链 rel 指向 seg1（成片与提示词同格，交付时点得开）",
      "outputs/seg1/final.mp4" in _j19.get("link", "").replace("%2F", "/"), True)
_sh.rmtree(os.path.join(HERE, "_dp_prod2"), ignore_errors=True)

# ---- 23-30 图片那一形（2026-10-10 用户定"强制要求：图片在回复里不点就能看见画面"）----
# 自造沙盒 + 自造图，理由同 19-22：拿现网某张帧图当 fixture = 钉现场，别人一删就随机红。
import re as _re                                        # noqa: E402
_P3 = os.path.join(HERE, "_dp_prod3", "productions", "probe3", "frames")
_sh.rmtree(os.path.join(HERE, "_dp_prod3"), ignore_errors=True)
os.makedirs(_P3, exist_ok=True)
_IMG = os.path.join(_P3, "seg1_k4.jpg")
open(_IMG, "wb").write(b"\xff\xd8\xff" + b"0" * 11000)   # 够大的 JPEG（闸不跑 ffmpeg、不解码）
_SRC = open(PACK, encoding="utf-8").read()


def _run_img(script, no_present=True, extra=("--no-net",)):
    env = dict(os.environ)
    if no_present:
        env["DELIVER_PACK_NO_PRESENT"] = "1"             # present 单独由 25 严格测，别把无关用例带红
    r = subprocess.run([sys.executable, script, _IMG, "--json"] + list(extra),
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=120, env=env)
    return r, json.loads(r.stdout or "{}")


r23, j23 = _run_img(PACK)
check("23 图片 ⇒ 退出 0、kind=image、不抽帧（画面就是它自己，一帧）",
      [r23.returncode, j23.get("kind"), len(j23.get("frames") or [])], [0, "image", 1])
check("24 markdown 出的是内联图，alt 用文件名（加载失败时看得见是哪张）",
      "![seg1_k4](http" in j23.get("markdown", ""), True)
r25, j25 = _run_img(PACK, no_present=False)
check("25 🔴 图片【不要求】present：摘掉 NO_PRESENT 兜底也照样退出 0（不是靠 env 蒙过去的）",
      [r25.returncode, (j25.get("presented") or {}).get("status")], [0, "not-required"])
check("26 帧图落在 frames/ 也合法（视频那条 outputs/ 判据不许顺手盖到图片上）",
      j23.get("checks", {}).get("图片在 productions/<task>/ 内（不要求 outputs/）"), True)
check("27 --no-net 是【跳过】不是【验过】：跳了哪几条必须写在 json 里（否则一片 True 分不清）",
      j23.get("net_skipped"), ["直链可达(200/206)", "直链回的是图片(image/*)"])
sys.path.insert(0, HERE)
import deliver_pack as _dp                               # noqa: E402
_adm = open(r"D:\video-skill\backend\model_admin.py", encoding="utf-8", errors="replace").read()
_m = _re.search(r"^FILM_INLINE_TYPES\s*=\s*\{(.*?)\}", _adm, _re.M | _re.S)
_inline = set(x.lower() for x in _re.findall(r'"([a-z0-9]+)"\s*:', _m.group(1))) if _m else set()
check("28 覆盖对照：交付脚本认的类型 ⊆ 服务端 FILM_INLINE_TYPES（服务端不发=直链404，贴图一起废）",
      bool(_inline) and (_dp.IMAGE_EXT | _dp.VIDEO_EXT) <= _inline, True)
_TXT = os.path.join(_P3, "note.txt")
open(_TXT, "w").write("x" * 20000)
_r29 = subprocess.run([sys.executable, PACK, _TXT, "--json", "--no-net"],
                      capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
check("29 不认的类型（.txt）⇒ 非 0，且 error 明写不认（不许悄悄退到视频那一形）",
      [_r29.returncode != 0, "不认的交付物类型" in (json.loads(_r29.stdout or "{}").get("error") or "")],
      [True, True])
# 变异对照选"给图片加上 present 门槛"这一刀（AGENTS.md 明令不许的那道闸）：
# 同一张图、同一个 env，只改这一行 ⇒ 结论必须从"可以交付"翻成"不算交付"，否则 25 那条绿灯是虚的。
_mut = _SRC.replace('        pstat, pmsg = "not-required", "图片走内联图，present 不是收工条件"',
                    "        pstat, pmsg = presented_copy(video)", 1)
check("30a 变异锚点命中（没替换成功 = 这条控制是空跑）", _mut != _SRC, True)
_MP = os.path.join(HERE, "_dp_pack_mutated.py")
open(_MP, "w", encoding="utf-8").write(_mut)
_r30, _unused30 = _run_img(_MP, no_present=False)   # 只吃退出码
os.remove(_MP)
check("30b 变异对照 给图片加上 present 门槛 ⇒ 同图同 env 立刻不通过（25 那条绿灯是判据挣来的）",
      [_r30.returncode != 0, (j25.get("presented") or {}).get("status") == "not-required"], [True, True])
_sh.rmtree(os.path.join(HERE, "_dp_prod3"), ignore_errors=True)


print("\n=== V6 交付三件套闸：%d 条，失败 %d ===" % (len(out), sum(1 for ok, _ in out if not ok)))
sys.exit(1 if any(not ok for ok, _ in out) else 0)
