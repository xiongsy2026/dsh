#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""super-clone skill runner：薄封装 backend 的 super_clone_wan3.run_pipeline。

只做参数整理 + 调用 + 打印结果 JSON，不重写管线、不复制视频引擎（PRD §6.2 共用工具层）。

用法示例（难单一段）：
  python run.py --task-id seg3 \\
    --video D:/ref/orig_trim.mp4 --product-images p1.jpg p2.png \\
    --product-name "富铁软糖" --output-dir D:/out \\
    --model-image face_c.jpg --as-duration-chunk
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys

VIDEO_SKILL_HOME = os.environ.get("VIDEO_SKILL_HOME", os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

# 只改【错误策略】不改编码：正解是按输出流能力丢字（GBK 控制台上 print 中文是正常需求）。
# 以前踩过的坑是 sys.stdout.reconfigure(encoding="utf-8") —— 那会造出"零输出 + exit 0"的静默假绿。
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass


def _ensure_video_skill_on_path() -> None:
    if not os.path.isdir(VIDEO_SKILL_HOME):
        raise SystemExit(
            f"找不到管线目录 {VIDEO_SKILL_HOME}（用环境变量 VIDEO_SKILL_HOME 覆盖）"
        )
    if VIDEO_SKILL_HOME not in sys.path:
        sys.path.insert(0, VIDEO_SKILL_HOME)


def _hide_console_window() -> None:
    """把入口自己的控制台窗口摘掉（人要盯着输出排障时设 SUPER_CLONE_SHOW_CONSOLE=1 就留着）。

    🔴 为什么入口自己做而不是让调用方加参数：8100 点火那条是 DETACHED 起的（本来没窗口），但 agent
       或人从【没有控制台的父进程】里敲 `python run.py`，Windows 会给这个控制台子进程新建一个黑框
       ⇒ 客户桌面上凭空弹一个窗口（2026-10-12 现网就是这个），而且他顺手点 X 就把跑到一半的编译杀掉，
       MiMo 那几分钟白等。这里【只摘窗口、不改输出去向】—— 点火那条仍然把 stdout 写进 compile.log。
    """
    if os.name != "nt" or (os.environ.get("SUPER_CLONE_SHOW_CONSOLE") or "").strip():
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)          # SW_HIDE
    except Exception:                                          # noqa: BLE001
        pass                                                   # 摘不掉不影响跑


def _pid_alive(pid: int) -> bool:
    """只问「还在不在」，绝不动它。用 ctypes 的查询语义，不拿信号当探针。"""
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)     # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == 259                 # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    except Exception:                                # noqa: BLE001
        return False


def pid_files(gate: dict | None, output_dir: str) -> list:
    """牌子 `.compile.pid` 的路径【只这一处算法】（读与写共用，别各拼一遍）。

    两个候选：意图里的 task_dir，和 output_dir 的上一层（=同一个任务目录）——
    点火那条走前者，手跑那条两个都能命中，所以都留着。
    """
    cands = []
    tdir = str((gate or {}).get("task_dir") or "").strip()
    if tdir:
        cands.append(os.path.join(tdir, ".compile.pid"))
    od = str(output_dir or "").strip().rstrip("\\/")
    if od:
        cands.append(os.path.join(os.path.dirname(od), ".compile.pid"))
    return list(dict.fromkeys(cands))


def claim_compile_pid(gate: dict | None, output_dir: str) -> str:
    """入口把"我这台在跑"写进牌子（不靠 8100 点火那条替它写）。

    🔴 为什么必须自己写（2026-10-12 现网 video33-cardigan-swap 16:23）：牌子以前**只有服务端点火才写**
       ⇒ agent 被叫醒后自己跑的那台编译在盘上等于"不存在"：`obligation_view` 看不见它（busy=false，
       收工拦截拦不住，它一收工客户就没人管了），跑完回来的 `POST /api/skills/form/compiled`
       也被"这台编译不是我起的（没有 .compile.pid）"拒掉 ⇒ **编译成功却永远叫不醒下一个人**。
       现在不管谁起，判据都只有一份：牌子在 = 这台在跑；摘牌 = 报完那一下。
    写在 collisions 检查之后：撞到别人活着的那台会直接退出，不会把别人的牌子盖成自己的。
    """
    for path in pid_files(gate, output_dir):
        if not os.path.isdir(os.path.dirname(path)):
            continue
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
        except OSError:
            return ""                     # 写不上牌子不影响编译本身（别把它做成硬失败），只是少一份留痕
        return path
    return ""


def _ancestor_pids() -> set:
    """我这条进程的【祖先链】（父、祖父…）。拿不到就返回只含直接父亲的小集合 —— 宁少不多。"""
    out = set()
    pid = os.getpid()
    try:
        out.add(os.getppid())
    except OSError:
        pass
    if os.name != "nt":
        return out
    try:                                     # 只读快照：查不到就停在父亲那一层，绝不抛（撞车检查不许变成故障点）
        import ctypes as _c
        import ctypes.wintypes as _wt

        class _PE(_c.Structure):
            _fields_ = [("dwSize", _c.c_ulong), ("cntUsage", _c.c_ulong), ("th32ProcessID", _c.c_ulong),
                        ("th32DefaultHeapID", _c.c_void_p), ("th32ModuleID", _c.c_ulong),
                        ("cntThreads", _c.c_ulong), ("th32ParentProcessID", _c.c_ulong),
                        ("pcPriClassBase", _c.c_long), ("dwFlags", _c.c_ulong),
                        ("szExeFile", _c.c_char * 260)]

        k32 = _c.windll.kernel32
        snap = k32.CreateToolhelp32Snapshot(0x2, 0)              # TH32CS_SNAPPROCESS
        if snap in (None, _wt.HANDLE(-1).value, 0xFFFFFFFF):
            return out
        entry = _PE()
        entry.dwSize = _c.sizeof(_PE)
        parent_of = {}
        if k32.Process32First(snap, _c.byref(entry)):
            while True:
                parent_of[entry.th32ProcessID] = entry.th32ParentProcessID
                if not k32.Process32Next(snap, _c.byref(entry)):
                    break
        k32.CloseHandle(snap)
        for _ in range(24):                                     # 走 24 层封顶，防环
            pid = parent_of.get(pid)
            if not pid:
                break
            out.add(int(pid))
    except Exception:  # noqa: BLE001
        pass
    return out


def compile_collision(gate: dict | None, output_dir: str) -> tuple:
    """纯判据：这条任务【已经有一台编译在跑】吗 ⇒ 返回 (那台的 pid, 牌子路径)；没有返回 (0, "")。

    牌子 = 任务目录的 `.compile.pid`（谁起谁写，跑完由服务端摘；见 claim_compile_pid）。
    🔴 这条为什么必须有：交表即点火之后，那条会话的 agent 收到唤醒又自己敲了一次 runner
       （2026-10-12 现网 11:51:50 点火 + 11:52:28 自起 = 同一任务两台并发）⇒
       同名产物（prompt.txt / storyboard.json / 回执）互相覆盖，MiMo 与编译器还要各跑一遍。

    🔴 祖先那一支不算撞车（2026-10-12 现网 18:26，一整天"点火零产物"的真死因）：
       8100 用 `.venv\Scripts\pythonw.exe` 起子进程，那是个**重定向壳** ——
       实测 `Popen.pid=65192` 而真正执行 run.py 的是它的**子进程 55020**。牌子写的是壳的号，
       于是 run.py 一读牌子 = 我自己爸、`_pid_alive` 为真 ⇒ **把自己拒了**，
       compile.log 里那句【已经在跑 pid=58268】是它自己打印的，outputs/ 因此是空的。
    """
    mine = _ancestor_pids() | {os.getpid()}
    for path in pid_files(gate, output_dir):
        try:
            with open(path, encoding="utf-8") as f:
                raw = (f.read() or "").strip()
        except OSError:
            continue
        try:
            pid = int(raw.splitlines()[0])
        except (ValueError, IndexError):
            continue
        if pid in mine:
            continue                           # 那是我自己 / 把我起来的壳，不是竞争者
        if _pid_alive(pid):
            return pid, path
    return 0, ""


def fold_scene_text(customer_request, scene_text) -> str:
    """背景描述的唯一落点：并进 customer_request 的「背景要求」段。
    编译器只从 customer_request 里读背景（compiler-contract.md 任务4），所以这里不另开入参。
    🔴 --scene-text 给了就【赢】：先剔掉 customer_request 里已有的「背景要求」行，
       否则同一条背景会有两个入口、互相打脸（同一件事不许留两处）。
       只认【行首】那行算这个字段 —— 故意不做子串匹配：客户正文里出现"背景要求"四个字就删，那是吃别人的话。"""
    scene = (scene_text or "").strip()
    lines = [l for l in (customer_request or "").splitlines() if l.strip()]
    if scene:
        lines = [l for l in lines if not l.strip().startswith("背景要求")]
        lines.append("背景要求：" + scene)
    return "\n".join(lines).strip()


# 档位语义的唯一源 = SKILL.md『声音模式与换音色』。这里不解释语义，只做入口一致性判定：
# 表上能选 ≠ 这条流程真做得到 —— 做不到的那档必须【出声拒】，不许悄悄降级交回原声。
VOICE_MODES = ("preserve_original", "timbre_swap", "rewrite_text", "rewrite_voice")
SWAP_MODES = ("rewrite_text",)          # 走 run_narration_swap 后处理腿的档
MODE_NOT_WIRED = ("rewrite_voice",)     # 选项在表上，但今天没有实现路径 ⇒ 入口拒


def voice_mode_problems(mode, has_audio, has_script=False) -> str:
    """声音档与"交没交目标人声 / 新口播文案"必须自洽。判据抽成纯函数 ⇒ 闸直接跑值表，不靠人在场。
    返回错误串；空串 = 放行。"""
    if mode not in VOICE_MODES:
        return "声音档只认 %s（收到 %r）" % ("/".join(VOICE_MODES), mode)
    if mode in MODE_NOT_WIRED:
        return ("%s 今天没有实现路径（新音色参考取哪一段、要不要口型同步，两条都还没定）"
                "⇒ 改口播不换人请选 rewrite_text，只换音色请选 timbre_swap。"
                "🔴 不许拿 timbre_swap 顶替：那是把没改的口播当改过交付。" % mode)
    if mode in ("timbre_swap",) and not has_audio:
        return ("%s 必须给目标人声参考（--voice-audio）：没有音色可换，"
                "我不会拿原片原声冒充换过声音" % mode)
    # 🔴 2026-10-14 用户改口径：rewrite_text（原音色改口播）= 【必须给新口播音频】并送进 wan3 的
    #    reference_audio。旧规则恰好相反（"给了音频=自相矛盾"），因为当时假定"新词靠后处理换轨、
    #    音色取自成片原声"。那条假定今天被实测打掉：wan3 把【参考视频的原声】整条搬进成片
    #    （成片音轨与源音轨相关系数 0.884，FunASR 听到的是原片词），所以不给新音频 = 口播根本没改。
    #    ⇒ 不传音频这一档就拒，绝不拿原片原声当"已改口播"交回。
    if mode in SWAP_MODES and not has_audio:
        return ("%s（改口播）必须给【新口播音频】（--voice-audio / 表单「新口播音频」那一格）："
                "这条 lane 的声音来自参考视频，不给新音频就只会播出原片那条声。"
                "🔴 上游对 reference_audio 的用法（照它念、还是只取音色）未实测 ⇒ 出片后必须 ASR 复验。" % mode)
    if mode in SWAP_MODES and not has_script:
        return ("%s 必须给新口播文案（表单「新口播文案」那一格，一行一句）："
                "文案与音频要能逐句对上，没有文案就没法核对音频说的是哪句。" % mode)
    if mode == "preserve_original" and has_audio:
        return ("preserve_original（原声保留）却给了 --voice-audio = 自相矛盾："
                "贴回原声就不会换音色（SKILL.md 硬规则 13 的穿帮形状）")
    return ""


FORM_GATE_URL = "http://127.0.0.1:8100/api/skills/form?session="


def direct_narration_done(video_path: str) -> str:
    """这条片子是不是【已经直出念新词】（wan3 一次出片就说了改后的口播）⇒ 返回物证路径，没有回空串。
    物证住 output_dir（任务 outputs 根），片子住 outputs/step2_wan3/… ⇒ 往上找几层，不猜文件名。"""
    d = os.path.dirname(os.path.abspath(str(video_path or "")))
    for _ in range(6):
        cand = os.path.join(d, "voice_script_binding.json")
        if os.path.isfile(cand):
            try:
                with open(cand, encoding="utf-8") as fh:
                    rec = json.load(fh) or {}
                if float(rec.get("coverage") or 0) >= 0.999 and not (rec.get("problems") or []):
                    return cand
            except Exception:                                  # noqa: BLE001
                return ""
            return ""
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return ""


def apply_narration_swap(video_path, vals, log=print) -> str:
    """voice_mode=rewrite_text 的后处理腿（改口播 · 音色不变）。其它档返回空串、什么都不做。

    🔴 每改一句 = 一次 RunningHub 声音克隆，且这条腿【不在网关账本上】⇒ 调用点只能落在
       人已批准、片子已经到手之后。失败一律 SystemExit：宁可没有这一条，也不许把没改的
       原片当"口播已改"交回给人（那是静默冒充，比没做更坏）。
    """
    if str(vals.get("voice_mode") or "") not in SWAP_MODES:
        return ""
    _nd = direct_narration_done(video_path)
    if _nd:
        log("[改口播] 这一条是【直出】的：新词已经在 prompt 台词位里（物证 %s）⇒ 不再走换轨，"
            "换了就是一条片子里两条声，还重复花一次克隆的钱" % _nd)
        return ""
    script = str(vals.get("voice_script") or "").strip()
    if not script:
        raise SystemExit("[改口播] voice_mode=%s 但没有新口播文案 ⇒ 不做（没得改，不是改好了）"
                         % vals.get("voice_mode"))
    src = str(video_path or "")
    if not os.path.isfile(src):
        raise SystemExit("[改口播] 成片不在盘上（%s）⇒ 先拿到片子再换轨" % src)
    _ensure_video_skill_on_path()
    from backend.services import voiceover_replace as vo      # noqa: E402
    out_dir = os.path.join(os.path.dirname(os.path.abspath(src)), "narration")
    log("[改口播] 对成片自己做 ASR + 人声分离（本地、0 分上游），逐句对照…")
    rec = asyncio.run(vo.run_narration_swap(src, out_dir, script, on_log=log))
    got = str(rec.get("video_out") or "")
    if not (got and os.path.isfile(got)):
        raise SystemExit("[改口播] 后处理没产成物（%s）⇒ 不许把原片当改好的交付" % got)
    log("[改口播] 换轨成片 → %s ｜ 重做 %s 句、保留 %d 句 ｜ 克隆实扣合计 %s"
        % (got, rec.get("clone_calls"),
           len(rec.get("rows") or []) - int(rec.get("clone_calls") or 0),
           rec.get("usage_total")))
    _tick_narration(os.path.dirname(os.path.abspath(src)),
                    os.path.join(out_dir, "narration_swap.json"))
    return got


LIPSYNC_CAP = ("lipsync", "default")


def apply_lip_sync(video_path, vals, log=print) -> str:
    """口型对齐腿（RunningHub LatentSync，经网关 lipsync/default）。

    🔴 为什么必须是一条【后处理腿】而不是 wan3 的参数（2026-10-13 用户裁定"wan3.0 没法直出了，
       得加口型对齐工作流"）：wan3-prime 的入参名单里没有任何口型字段（`_PRIME_YZ_MEDIA` 只有
       reference_image/video/audio/file），而改口播那条腿自己就写着"不做口型同步"⇒ 换了词嘴形
       就对不上。对齐的目标音频 = **换轨后成片自己的音轨**（本地抽，0 分），不再另外造一条 TTS。
    🔴 花钱边界：这一条走网关 ⇒ 20 分/次（登记值·这条腿在账本里 0 次调用 = 未实测），
       且 `lipsync` 属于【默认要批】⇒ 没条子就 0 分未扣、任务未发起，本函数原样把拒因念出来。
       绝不"顺手"提交：宁可交一条嘴形没对齐的片子并如实说明，也不替人点批准。
    返回对齐后的路径；没做/做错一律回空串（调用方据此继续交付，不许把没做的当做了）。
    """
    if str(vals.get("voice_mode") or "") not in SWAP_MODES:
        return ""
    src = str(video_path or "")
    if not os.path.isfile(src):
        log("[口型] 没有可对齐的成片（%s）⇒ 跳过" % src)
        return ""
    _ensure_video_skill_on_path()
    from backend import gateway as gw                        # noqa: E402
    work = os.path.join(os.path.dirname(os.path.abspath(src)), "lipsync_work")
    os.makedirs(work, exist_ok=True)
    audio = os.path.join(work, "target_audio.wav")
    if not _extract_audio_track(src, audio):
        log("[口型] 抽不出音轨 ⇒ 不做（没有音频就没有对齐的根据，不是「整条送过去碰运气」）")
        return ""
    segs, how = _lip_segments(src)
    if how == "no-face":
        log("[口型] storyboard 里【没有一个镜头有正脸】⇒ 这一刀不做（0 分、不发请求）："
            "画面上根本没有嘴，花 20 分重画只会把产品特写画坏")
        return ""
    params = {"video_path": src, "audio_path": audio, "output_dir": work}
    if segs:
        params["segments"] = segs
    win = " ".join("%.1f-%.1f" % (s["start"], s["end"]) for s in segs[:6]) or "—"
    log("[口型] %s → LatentSync（%s·%d 段 %s）… 这一步花 20 分/次，要先有人批"
        % (os.path.basename(src), "selective" if segs else "整条", len(segs), win))
    env = gw.invoke(LIPSYNC_CAP[0], LIPSYNC_CAP[1], "local", params)
    cr = env.get("credits") or {}
    rec = {"video_in": src, "audio_in": audio, "segments": segs, "segments_from": how,
           "capability": "%s/%s" % LIPSYNC_CAP, "unit_price": gw.unit_price(*LIPSYNC_CAP),
           "charged": cr.get("charged"), "estimated": cr.get("estimated"),
           "ok": bool(env.get("ok")), "error": str(env.get("error") or ""),
           "video_out": str((env.get("result") or {}).get("path") or ""),
           "mode": str((env.get("result") or {}).get("mode") or "")}
    try:
        with open(os.path.join(work, "lip_sync.json"), "w", encoding="utf-8") as fh:
            json.dump(rec, fh, ensure_ascii=False, indent=2)
    except Exception as e:                                    # noqa: BLE001
        log("[口型] 物证没写成(%s)⇒ 这一格打不上钩" % type(e).__name__)
    got = rec["video_out"]
    if not env.get("ok") or not (got and os.path.isfile(got)):
        log("[口型] 没做成：%s ⇒ 交付用的还是没对齐的那条（%s）"
            % (str(rec["error"])[:180] or "上游没落出文件", src))
        return ""
    cr2 = _tick_lip_sync(os.path.dirname(os.path.abspath(src)), os.path.join(work, "lip_sync.json"))
    log("[口型] 对齐成片 → %s ｜ 实扣 %s 分%s" % (got, rec["charged"], cr2))
    return got


def _extract_audio_track(video: str, out_wav: str, log=print) -> bool:
    """本地抽音轨（0 分、不发任何请求）。抽不出 = 不做，不许拿原视频当"已对齐"。
    🔴 死因必须说出来（2026-10-14 现网）：这函数原先 `except: return False` 把 ffmpeg 的 stderr
       和异常一起吃掉 ⇒ 日志只剩一句"抽不出音轨"，而盘上那条片子手动抽是好的，没法定位。
    """
    try:
        p = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video,
                            "-vn", "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", out_wav],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
    except Exception as e:                                     # noqa: BLE001
        log("[口型] 抽音轨抛异常(%s: %s) ⇒ 不做" % (type(e).__name__, str(e)[:160]))
        return False
    if p.returncode != 0:
        log("[口型] ffmpeg 回 %s：%s" % (p.returncode, (p.stderr or "")[:220]))
        return False
    if not os.path.isfile(out_wav):
        log("[口型] ffmpeg 说成功但没落文件：%s" % out_wav)
        return False
    sz = os.path.getsize(out_wav)
    if sz <= 1024:
        log("[口型] 抽出来只有 %d 字节，不像音轨 ⇒ 不做（这条片子可能根本没声）" % sz)
        return False
    return True


def _sec(v):
    """storyboard 里的时间既写 `00:03.00` 也写 `15.0`，两种都要能读（读不出回 None，不猜）。"""
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").strip()
    if not s:
        return None
    if ":" in s:
        parts = s.split(":")
        try:
            return sum(float(p) * (60 ** (len(parts) - 1 - i)) for i, p in enumerate(parts))
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def _near_sidecar(start_dir: str, name: str, up: int = 4) -> str:
    """从片子所在目录往上找同名旁文件（段目录 / narration / step2_wan3 三种落点都在这条链上）。"""
    d = os.path.abspath(start_dir or "")
    for _ in range(up + 1):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return ""


def _face_windows(video_path: str):
    """storyboard 里「正脸入镜」的镜头窗口 = 口型穿帮【唯一看得见】的地方。
    判据 = 面部微表情那一组字段里没写「未入镜 / 看不清」。
    🔴 三种结果要分开：`None` = 没有 storyboard 这份事实源（不能据此断定没人露脸）；
       `[]` = 读到了、而且逐镜都写着未入镜 ⇒ 这一刀【不该做】。"""
    sb = _near_sidecar(os.path.dirname(os.path.abspath(video_path or "")), "storyboard.json")
    if not sb:
        return None
    try:
        with open(sb, encoding="utf-8") as fh:
            rows = json.load(fh) or []
    except Exception:                                          # noqa: BLE001
        return None
    out = []
    for s in rows if isinstance(rows, list) else []:
        if not isinstance(s, dict):
            continue
        t = s.get("时间区间") or {}
        a, b = _sec(t.get("开始时间")), _sec(t.get("结束时间"))
        if a is None or b is None or b <= a:
            continue
        face = "".join(str(v) for v in (s.get("面部微表情") or {}).values())
        if not face.strip() or any(k in face for k in ("未入镜", "看不清")):
            continue
        out.append({"start": a, "end": b})
    return out


def _speech_windows(video_path: str) -> list:
    """改口播那份对账单里的「什么时候在说话」。TTS 换轨那条腿逐句落 rows；
    客户整稿音频那条腿（narration_audio.json）是整条替换、没有逐句时间 ⇒ 读不到就交空。"""
    p = _near_sidecar(os.path.dirname(os.path.abspath(video_path or "")), "narration_swap.json")
    if not p:
        return []
    try:
        with open(p, encoding="utf-8") as fh:
            rows = (json.load(fh) or {}).get("rows") or []
        return [{"start": float(r.get("start", 0)), "end": float(r.get("end", 0))}
                for r in rows if float(r.get("end") or 0) > float(r.get("start") or 0)]
    except Exception:                                          # noqa: BLE001
        return []


def _lip_segments(video_path: str) -> tuple:
    """口型这一刀要重画哪几段，以及这个结论是【怎么定出来的】。

    🔴 为什么要交这份窗口（2026-10-14 现网）：那条 15s 成片里只有镜 1–2（0–4.8s）有正脸说话，
       其余 10.2s 是手、产品特写和背面 —— 拿整条去跑 LatentSync = 花 20 分重画根本没有嘴的画面。
       上一版这里只读 `narration/narration_swap.json`（只有 TTS 换轨那条腿才写得出来），
       客户给整稿音频的形状读不到 ⇒ 静默退成整条模式（`lip_sync.json` 里 segments 空就是这么来的）。
    两份判据：storyboard 的「面部微表情」（有没有嘴）× 换轨台账（什么时候在说话）。
    都有 ⇒ 交集；只有嘴的窗口 ⇒ 用它（face）；两份都没读到 ⇒ unknown，由调用方退成整条。
    🔴 「读到了 storyboard 但一镜正脸都没有」单独回 no-face ⇒ 调用方【不做这一刀】，不是退成整条。
    """
    faces = _face_windows(video_path)
    speech = _speech_windows(video_path)
    if faces is None:                                          # 没有 storyboard 这份事实源
        return (speech, "speech") if speech else ([], "unknown")
    if not faces:                                              # 读到了：一镜正脸都没有
        return [], "no-face"
    if not speech:
        return faces, "face"
    inter = []
    for f in faces:
        for w in speech:
            a, b = max(f["start"], w["start"]), min(f["end"], w["end"])
            if b - a >= 0.25:
                inter.append({"start": round(a, 3), "end": round(b, 3)})
    if not inter:
        return faces, "face"
    return sorted(inter, key=lambda x: x["start"]), "face+speech"


def _tick_lip_sync(task_dir: str, evidence: str) -> str:
    """给合同的 lip-sync 那一格打钩；打不上只出声（required=0，不影响片子）。"""
    try:
        from backend import skill_contract as skc             # noqa: E402
        r = skc.tick("super-clone", task_dir, "lip-sync", evidence=evidence,
                     model="RunningHub LatentSync 工作流（网关 lipsync/default）",
                     impl="gateway:invoke:lipsync")
        return "" if r.get("ok") else ("｜回执没打上钩：%s" % str(r.get("error") or "")[:100])
    except Exception as e:                                     # noqa: BLE001
        return "｜回执这一步没走通(%s)" % type(e).__name__


def narration_audio_verdict(script: str, heard: str) -> dict:
    """纯判据：客户稿与实际听到的，两个方向各算一次覆盖率。空表 = 口播真改了。
    🔴 为什么两个方向都要：只查"稿子被说到"会漏掉【原片旧词还在】那种（今天就是这么翻车的：
       prompt 台词位逐字是新词，成片播的却是参考视频的原声）。"""
    from difflib import SequenceMatcher
    import re as _re

    # 🔴 只留汉字与字母数字：ASR 会在句尾补句号、逗号数量也和客户稿不一致，
    #    早先只去空白 ⇒ 一条完全正确的配音被判"残留 11%"（判据自己算错会把合格对象判红）。
    _KEEP = _re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]")

    def norm(x):
        return _KEEP.sub("", str(x or ""))

    a, b = norm(script), norm(heard)
    if not a:
        return {"said_coverage": 1.0, "stale": 0.0, "problems": [], "heard_chars": len(b)}

    def cov(x, y):
        if not x:
            return 1.0
        m = SequenceMatcher(None, x, y, autojunk=False)
        return round(sum(blk.size for blk in m.get_matching_blocks()) / float(len(x)), 4)

    said, stale = cov(a, b), cov(b, a)
    probs = []
    if said < 0.9:
        probs.append("新稿只有 %.0f%% 被听到" % (said * 100))
    if stale < 0.9:
        probs.append("听到的内容里 %.0f%% 不属于新稿（多半是原片旧词还在播）" % ((1 - stale) * 100))
    return {"said_coverage": said, "stale": stale, "problems": probs, "heard_chars": len(b)}


def verify_narration_audio(video_path, vals, log=print) -> bool:
    """出片之后【必须听一遍】：本地 FunASR 复验成片音轨说的是不是客户新稿（0 分上游生成，
    只有文本校对那条腿按 token 实扣，免批不免账）。复验不过就在交付里明写"口播没改"。
    🔴 参数发出去了 ≠ 生效 —— 这条就是用来堵这个的。
    """
    src = str(video_path or "")
    script = str(vals.get("voice_script") or "").strip()
    if not script or not os.path.isfile(src):
        return False
    _ensure_video_skill_on_path()
    import asyncio
    from pathlib import Path
    from backend.services.prompt_reverser import _extract_voiceover
    from backend.services.super_clone_wan3 import _ffprobe_duration
    work = os.path.join(os.path.dirname(os.path.abspath(src)), "narration_verify")
    os.makedirs(work, exist_ok=True)
    try:
        segs = asyncio.run(_extract_voiceover(src, Path(work), _ffprobe_duration(src) or 0.0)) or []
    except Exception as e:                                    # noqa: BLE001
        log("[口播复验] 跑不起来(%s: %s)⇒ 这一条【没验过】，交付时不许说口播已改"
            % (type(e).__name__, str(e)[:120]))
        return False
    heard = "".join(str(s.get("text") or "") for s in segs)
    v = narration_audio_verdict(script, heard)
    v.update({"film": src, "asr_segments": len(segs), "heard": heard,
              "executor": "runner:verify_narration_audio",
              "note": "本地 FunASR 复验；verdict 只比文本，不判音色"})
    try:
        with open(os.path.join(work, "narration_audio_verify.json"), "w", encoding="utf-8") as fh:
            json.dump(v, fh, ensure_ascii=False, indent=2)
    except OSError as e:
        log("[口播复验] 物证没写成(%s)" % type(e).__name__)
    if v["problems"]:
        log("[口播复验] 🔴 不合格：%s ｜ 听到的是「%s」⇒ 交付必须明写【口播没改】"
            % ("；".join(v["problems"]), heard[:46]))
        return False
    log("[口播复验] ✓ 听到的就是新稿（覆盖 %.2f、残留 %.2f）" % (v["said_coverage"], v["stale"]))
    return True


def _tick_narration(task_dir: str, evidence: str) -> None:
    """给合同的 narration-swap 那一格打钩。打不上只出声，不影响这条片子（它是 required=0）。"""
    try:
        _ensure_video_skill_on_path()
        from backend import skill_contract as skc             # noqa: E402
        r = skc.tick("super-clone", task_dir, "narration-swap", evidence=evidence,
                     model="IndexTTS · RunningHub AI App 1965614643077070850（不在网关账本上）",
                     impl="backend:invoke:voiceover_replace.run_narration_swap")
        if not r.get("ok"):
            print("[改口播] 回执这一格没打上钩：%s" % str(r.get("error") or "")[:120])
    except Exception as e:                                    # noqa: BLE001
        print("[改口播] 回执这一步没走通(%s: %s) ⇒ 不影响片子" % (type(e).__name__, str(e)[:80]))


def form_coverage_gap(gate) -> str:
    """表已经出现了、但【素材根里躺着文件、必填素材格却没值】⇒ 说明料在手却把归类推回客户。
    返回错误串；空串 = 放行。

    🔴 缺哪几格、根里有哪些闲料，是 8100 一份算好的（`prefill_gap` / `loose_materials`），
       这里只把结论翻成人话 —— 脚本/入口各自再解析一遍字段表就是第二份判据，
       漂了的形状是"脚本说填好了、入口却拦着"（或反过来）。

    现网形状（2026-10-10 ef1ba285）：agent 只回填了 `voice_mode` 一格，原片就在任务目录里，
    于是右栏那张表上"原片"永远空着 —— 客户看到的是一张要他自己补素材的表，正是这条铁律要消灭的。
    """
    if not isinstance(gate, dict):
        return ""
    hit = [str(k) for k in (gate.get("prefill_gap") or [])]
    loose = [str(p) for p in (gate.get("loose_materials") or [])]
    if not hit:
        return ""
    return (
        "右栏那张表还缺必填素材格 %s，而这条任务的目录里已经躺着类型对得上的文件却没被引用：\n  %s\n"
        "⇒ 不许进引擎。把这些料挂进对应格子（回填口只认 <task_dir>\\inputs，在 src\\ 里的先复制过去）：\n"
        "  python scripts/intent.py prefill --set '{\"files\":[{\"field\":\"<格名>\","
        "\"src\":\"<inputs 里的路径>\"}]}'\n"
        "只有客户【手上确实没有】的素材才该留空让他在页面上自己传。"
        % ("/".join(hit), "\n  ".join(loose[:6]) or "（见上：格子是空的）")
    )


def form_gate_verdict(gate) -> str:
    """入口的表单前置：这条会话登记了本线 ⇒ 右栏那张表必须【已经出现过】（回填过或已提交）。
    返回错误串；空串 = 放行。判据抽成纯函数 ⇒ 闸直接跑值表，不靠人在场。

    🔴 为什么把门槛钉在入口，而不是在文档里提醒 agent"记得跑 prefill"（2026-10-10 现网实测）：
       三条 super-clone 会话全部回 await-prefill ⇒ 表单一张都没弹过 —— 因为回填是一条
       【可以跳过的额外命令】，而可选步骤就等于不存在的步骤。绑到必然发生的动作（走入口出片）上，
       "客户看到表"才第一次成为硬事实。
    """
    if not isinstance(gate, dict) or not gate.get("show"):
        return ""            # 没登记 / 那条线没配字段表 ⇒ 不归这道管（登记另有铁律）
    if gate.get("submitted"):
        return ""            # 客户点过【提交这张表】= 已授权开工
    if gate.get("defaults") or gate.get("default_files"):
        # 表已经出现过，但"出现过"不等于"填好了" —— 覆盖率不合格照样拦在这。
        return form_coverage_gap(gate)
    return (
        "这条会话的右栏表单还没出现过，不许进引擎。先做两件事：\n"
        "  1) 分析客户给的原片与素材（看片/看图，零成本那条腿）；\n"
        "  2) python scripts/intent.py prefill --set '{\"values\":{...},\"files\":[...]}'\n"
        "     ——素材会被复制进 <task_dir>/inputs 并回填到右栏，表当场弹出。\n"
        "客户在表上点【提交给 agent 分析】就等于授权开工（不要再让他回一句「开始」）。"
    )


def read_form_gate(sid: str) -> dict:
    import urllib.parse
    import urllib.request
    with urllib.request.urlopen(FORM_GATE_URL + urllib.parse.quote(sid), timeout=8) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


# 🔴 这四样是"没有它就跑不动"的格。以前它们是 argparse 的 required=True ⇒ 只能靠人敲命令行给，
#    于是 agent 变成"表单 → 命令行"的人肉转录层：读得出绝对路径还得再抄一遍（2026-10-11 现网
#    21:04 抄对了、20:55 把 key 名抄成另一条线的叫法）。改成【表单能覆盖】⇒ 抄这个动作整个消失。
REQUIRED_DESTS = ("task_id", "video", "product_images", "product_name", "output_dir")


def multi_dests(parser) -> set:
    """哪些参数收多个值 —— 从 parser 自己读，不在这里抄第二份字段表。"""
    import argparse as _ap
    out = set()
    for act in parser._actions:
        if isinstance(act, _ap._AppendAction) or act.nargs in ("+", "*"):
            out.add(act.dest)
    return out


def apply_form_values(known: dict, gate: dict, multi: set) -> tuple:
    """纯函数（闸直接跑值表）：把这条会话【已提交的表单】并进命令行值。

    三条规则，每条都对应一种"本来要靠猜"的动作：
      1) 命令行显式给了 ⇒ 赢（人当场改的，比表新；来源标 `命令行`）；
      2) 表里有 ⇒ 用表的（媒体是服务端存好的绝对路径/数组，一个字都不经过 agent 的手）；
      3) 两边都没有 ⇒ 原样留着，交给 missing_required 点名 —— 🔴 不许在这里编一个默认值，
         编出来的东西会一路走到报价，而人以为那是客户选的。
    返回 (最终值 dict, 来源行 list, 表里给了但这条入口不吃的 key list)。
    """
    vals = dict(known)
    params = dict((gate or {}).get("params") or {})
    provenance = []
    unmapped = []
    for key, raw in sorted(params.items()):
        if key not in vals:
            unmapped.append("%s=%s" % (key, str(raw)[:40]))
            continue                       # 这条入口没有这个参数（voice_script 那类是给 agent 读的）
        if vals[key] not in (None, "", []):
            provenance.append("%-16s 命令行" % key)
            continue
        v = raw
        if key in multi and isinstance(v, str):
            v = [v]
        if key not in multi and isinstance(v, list):
            v = v[0] if v else None
        if v in (None, "", []):
            continue                       # 表上这格是空的 ⇒ 不算"表单给过"
        vals[key] = v
        provenance.append("%-16s 表单" % key)
    # task-id / output-dir 不必让客户填：它们不是选择，是这条任务的家（登记时就建好了）
    tdir = str((gate or {}).get("task_dir") or "").strip()
    if tdir:
        import os as _os
        if not vals.get("output_dir"):
            vals["output_dir"] = _os.path.join(tdir, "outputs")
            provenance.append("%-16s 任务目录" % "output_dir")
        if not vals.get("task_id"):
            vals["task_id"] = _os.path.basename(_os.path.normpath(tdir))
            provenance.append("%-16s 任务目录" % "task_id")
    return vals, provenance, unmapped


def missing_required(vals: dict) -> list:
    """还缺哪几格（表单和命令行都没给）。返回 dest 名单。"""
    return [d for d in REQUIRED_DESTS if vals.get(d) in (None, "", [])]


def missing_message(missing: list, sid: str) -> str:
    return (
        "这几格既没在命令行给、这条会话的表里也没有：%s\n"
        "⇒ 不要自己编一个值填进去。两条正路：\n"
        "  A) 让客户在右栏那张表上选完点【提交这张表】，再跑同一条命令（推荐：值有出处）；\n"
        "     会话 %s\n"
        "  B) 明确知道值的时候直接在命令行给（会盖过表上的值，来源会印出来）。\n"
        % ("、".join(missing), sid or "（读不到会话 id ⇒ 表在哪个会话都不知道）")
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="超级复刻 Wan3.0 出片（薄封装 run_pipeline）")
    # 🔴 这些以前是 required=True：那等于强制"由人敲命令行"，把 agent 钉成转录层。
    #    现在由 --from-form 从表单取，缺哪些格由 missing_required 在合并【之后】点名（照样不会静默跑）。
    p.add_argument("--task-id", default=None,
                   help="缺省用这条会话任务目录的目录名（登记时就建好了）")
    p.add_argument("--video", default=None, help="原片（或该段裁剪后原片）路径；缺省读表单【原片】那一格")
    p.add_argument("--product-images", nargs="+", default=None,
                   help="产品图 1-10 张；缺省读表单【产品图】那一格（服务端存的是绝对路径数组）")
    p.add_argument("--product-name", default=None, help="产品名（入口校验：不能为空）")
    p.add_argument("--customer-request", default=None, help="缺省用 --product-name")
    p.add_argument("--scene-text", default=None,
                   help="场景替换描述：有 --scene-image 时只补图里没有的细节（冲突以图为准），"
                        "没图时是背景唯一输入口。会并进 customer_request 的「背景要求」段，"
                        "因为编译器只从那里读背景（references/compiler-contract.md 任务4）")
    p.add_argument("--output-dir", default=None, help="缺省用 <任务目录>/outputs")
    p.add_argument("--product-facts", default=None, help="客户明确陈述的非视觉事实")
    p.add_argument("--model-image", default=None, help="该段唯一脸图（一段一张脸）")
    p.add_argument("--extra-model-images", nargs="*", default=None, help="多脸时其余脸图(不整段塞)")
    p.add_argument("--voice-mode", default=None, choices=list(VOICE_MODES),
                   help="声音档：%s。改口播不换人 = rewrite_text（走 run_narration_swap 后处理腿）。"
                        "唯一语义源 = SKILL.md『声音模式与换音色』；这一档决定 --voice-audio 必给还是禁给。"
                        % " / ".join(VOICE_MODES))
    p.add_argument("--voice-script", default=None,
                   help="新口播文案（一行一句，句数必须与成片口播对上）。表单「新口播文案」那一格直灌这里。"
                        "🔴 2026-10-14 起这条路是【直出】：编译前灌进各镜「口播」位（apply_customer_narration），"
                        "wan3 一次出片就念新词，物证 voice_script_binding.json（覆盖率不满即拒、0 分）。"
                        "限制：一段一条稿（整条自动切段时带着整稿会被点名拒，逐段跑或改走后处理换轨）。"
                        "⚠️ 直出【不等于】口型已对齐：嘴形由模型自己画，要按新词重画是另一步 lipsync/default（20 分/次·未实测·要批）。")
    p.add_argument("--voice-audio", action="append", default=None,
                   help="目标音色参考音频（可多次给，≤5 段、单段 1-15s、总长 ≤15s）。"
                        "换人（男/女）时必须给：Wan 生成时直接用该音色合成口播，原片原声不贴回。"
                        "不换人（换产品/换背景）时不给，原片原声照常贴回。")
    # 🔴 2026-10-31 用户定「背景就一个背景，本地要能传进wan3.0」⇒ 恢复 --scene-image 透传。
    #    引擎侧 scene_image_path 一直在（_build_media_manifest 把场景图编成 图N，上传后与产品/模特图
    #    一起进 wan3.0 的 image_urls；编译器把该图当背景事实源，各 Shot 只是机位不同、背景全程不变）。
    #    scene_text/--scene-text 仍有效：有图时文字只补图里没有的细节，冲突以图为准。
    p.add_argument("--scene-image", default=None,
                   help="背景参考图（本地路径，可选）。给了就进引擎：场景 图N → 随素材上传 → wan3.0 参考图；"
                        "背景定调以该图为事实源，scene_text 只补图中没有的细节。")
    p.add_argument("--trim-start", type=float, default=None)
    p.add_argument("--trim-end", type=float, default=None)
    p.add_argument("--narration-audio", default=None,
                   help="只给 --stage narration 用：客户【已经念好新词】的那条音频。"
                        "整条替换成片口播（本地、0 分、不调克隆/上游），音色=这条音频的人。"
                        "🔴 与 narration-swap（逐句克隆）互斥，两条不能同时跑。")
    p.add_argument("--film", default=None,
                   help="只给 --stage lipsync 用：指定要对哪一条【成片】对口型；"
                        "缺省取这条任务里最新的一条成片（wan3_job.json 的 output_path）。"
                        "🔴 这不是原片（那是 --video），也别拿它去绕提交。")
    p.add_argument("--resolution", default=None)
    p.add_argument("--aspect-ratio", default=None)
    p.add_argument("--engine", default="wan3", choices=["wan3"],
                   help="只有一个合法值 wan3。引擎锁的唯一源在服务层 engine_lock_error()，这里只是让人敲命令时就撞墙，而不是跑到一半才拒")
    p.add_argument("--extra-note", default=None,
                   help="extra_compile_note：合同 storyboard-fact-check 那一步的【唯一授权手段】——"
                        "MiMo 的分镜/动作标签与原片不符时，在这里按秒写死真实时间轴压过它（重编译 0 分）")
    p.add_argument("--resume", action="store_true",
                   help="复用盘上已落盘的分镜（Step1 产物过 Checkpoint A 复验才复用，0 token、不再花视频解析）。"
                        "2026-10-23 加：以前入口只有【整条重跑】一种走法，于是"
                        "\"改一句措辞重编译\"也要把 Step1 分镜腿重跑一遍 —— 那条腿实测会"
                        " finish_reason=length 截断（现网死过一次），白烧一次还带崩整条。")
    p.add_argument("--split-by-shots", action="store_true")
    p.add_argument("--as-duration-chunk", action="store_true", help="按段调用时置 True（难单每段 True）")
    p.add_argument("--from-form", action="store_true",
                   help="🔴 值全部从这条会话【已提交】的右栏表单读（媒体是服务端存的绝对路径），"
                        "命令行显式给的参数只作覆盖。没有它 agent 就得手抄参数 —— 抄就有猜错的那一天。")
    p.add_argument("--stage", default="submit", choices=["compile", "submit", "wait", "narration", "lipsync"],
                   help="compile=只走到「物证+回执+合同核验」就返回（不提交、不建预扣、0 分）；"
                        "submit=照常出片。SKILL.md 要求「不符就补 extra-note 重编译（0 分）」，"
                        "这一档就是那句散文的合法出口 —— 以前没有它，遵守它只能绕开入口。\n"
                        "wait=【只捞不投】第 5 步：读盘上已提交未落盘的任务号（wan3_job.json），"
                        "查询 + 下载 + 交付，一个提交请求都不发。")
    p.add_argument("--reuse-compile", action="store_true",
                   help="只给 --stage submit 用：命中编译缓存（compile_fp 相同 + 两份物证 sha8 对得上 + "
                        "回执必需步全勾）⇒ 跳过 MiMo/FunASR/Grok，直接读盘上那份 prompt.txt 提交。"
                        "🔴 不命中就【当场拒】（0 分、一个模型都不调），不许「半复用」。")
    # 🔴 服务端【批准即点火】走这一档，不走上面那条硬档（2026-10-23 现网：人批完点链接，服务端起的
    #    那台从 Step0 重跑分镜+编译，人问"提交就提交，怎么又重写编译了"）。
    #    为什么不能复用硬档：条子已经批的是【提交】这件事；缓存恰好不命中（例如批完之后 extra_note
    #    又改过、或物证被重写过）时，硬档会【当场拒】⇒ 一张已生效的批准花不出去、也没人重报价 = 卡死。
    #    软档的语义："能复用就省掉分镜+编译（0 token 0 分），不能复用就照常重编再投"。
    p.add_argument("--reuse-compile-if-hit", action="store_true", dest="reuse_compile_soft",
                   help="只给 --stage submit 用：命中编译缓存就复用、不命中就全量重编译（不拒）。"
                        "服务端批准即点火用的就是这一档。")
    p.add_argument("--session", default=None,
                   help="DSH 会话 id（默认读 $env:DSH_SESSION_ID）。用于查右栏那张表有没有出现过；"
                        "读不到就不判（常驻服务里没有会话 id，绝不猜一个填进去）。")
    return p


COMPILED_URL = "http://127.0.0.1:8100/api/skills/form/compiled"


def report_compiled(sid: str, ok: bool, error: str = "") -> None:
    """跑完编译回来挂号（交表即点火的回程那一半）。
    🔴 失败【不许挡住交付】：叫不醒人是"少一次自动播报"，把这一步做成硬失败反而会让已经跑完的
       0 分编译白跑（而且它自己还要退出非 0，看起来像编译失败）。所以只打一行，不改退出码。"""
    import urllib.error
    import urllib.request
    try:
        req = urllib.request.Request(
            COMPILED_URL,
            data=json.dumps({"session": sid, "pid": os.getpid(), "ok": bool(ok),
                             "error": str(error or "")[:200]}).encode("utf-8"),
            headers={"Content-Type": "text/plain;charset=UTF-8"})
        with urllib.request.urlopen(req, timeout=8) as r:
            raw = r.read()
        # 🔴 服务端说了什么必须出声：HTTP 200 + {"ok":false,"error":…} 这一形状过去被整个丢掉，
        #    于是"编译跑完了但没人被叫醒"在盘上零条红（2026-10-12 现网就是拿不到这一行才查了一个小时）。
        try:
            ans = json.loads(raw.decode("utf-8", errors="replace") or "{}")
        except ValueError:
            ans = {}
        if ans.get("ok") is False:
            print("[编译回执] 8100 不收这一格（%s）⇒ 编译结果照样落盘，但没人被自动叫醒，"
                  "请人回一句让 agent 接着报价" % str(ans.get("error") or "没给原因")[:140])
    except (urllib.error.URLError, OSError, ValueError) as e:
        print("[编译回执] 8100 没收下（%s: %s）⇒ 编译结果照样落盘，但没人被自动叫醒，"
              "请自己回一句让 agent 接着报价" % (type(e).__name__, str(e)[:100]))


def find_film(a, sid: str) -> str:
    """这条任务里【要交付的那条片子】：显式 --film > 盘上最新成片（wan3_job.json 的 output_path 优先）。
    找不到的话宁可拒，也不许拿原片或上一次的片子当成品（那是静默冒充）。"""
    import glob
    explicit = str(getattr(a, "film", "") or "").strip()
    if explicit:
        if not os.path.isfile(explicit):
            raise SystemExit("[找片] --film 指向的文件不在盘上：%s" % explicit)
        return explicit
    tdir = str((read_form_gate(sid) or {}).get("task_dir") or "").strip()
    cands = []
    if tdir:
        for pat in (os.path.join(tdir, "outputs", "**", "wan3_job.json"),
                    os.path.join(tdir, "outputs", "**", "*prime*.mp4")):
            for p in glob.glob(pat, recursive=True):
                dest = p
                if p.endswith("wan3_job.json"):
                    try:
                        with open(p, encoding="utf-8") as f:
                            dest = str((json.load(f) or {}).get("output_path") or "").strip()
                    except (OSError, ValueError):
                        continue
                if dest and os.path.isfile(dest):
                    cands.append(dest)
        cands = sorted(set(cands), key=os.path.getmtime)
    if not cands:
        raise SystemExit("[找片] 这条任务里找不到成片（既没有 wan3_job.json 的 output_path，"
                         "也没有 *prime*.mp4）⇒ 先跑 --stage wait 把片子捞回来，别拿原片凑数")
    return cands[-1]


def stage_narration(a, sid: str) -> int:
    """`--stage narration --narration-audio <音频>`：客户自己给了念好的那条声音 ⇒ 整条替换（0 分本地）。

    🔴 为什么要有这一档：换口播的三条路里，克隆（narration-swap）与口型（lipsync）此刻都堵在
       RunningHub `605 余额不足`，而 wan3 的音轨是从参考视频搬来的、不念 prompt 里的台词
       ⇒ 没有这一档，"客户给了音频"这件事就没有合法出口，只能被手搓 ffmpeg（合同外的手段）。
    """
    src = find_film(a, sid)
    aud = str(getattr(a, "narration_audio", "") or "").strip()
    if not aud or not os.path.isfile(aud):
        raise SystemExit("[换口播] --narration-audio 要指向盘上真实存在的那条音频（给的是 %r）" % aud)
    _ensure_video_skill_on_path()
    import asyncio
    from backend.services import voiceover_replace as vo
    out_dir = os.path.join(os.path.dirname(os.path.abspath(src)), "narration")
    rec = asyncio.run(vo.run_narration_audio(src, aud, out_dir, on_log=lambda m: print("[换口播] %s" % m)))
    got = str(rec.get("video_out") or "")
    if not (got and os.path.isfile(got)):
        raise SystemExit("[换口播] 没落成片（%s）⇒ 不许把没换的那条当已换交付" % got)
    _tick_narration_file(os.path.dirname(os.path.abspath(src)),
                         os.path.join(out_dir, "narration_audio.json"))
    vals = dict(((read_form_gate(sid) or {}).get("params")) or {}) if sid else {}
    # 🔴 复验比的是【哪一份稿】必须说清，且命令行覆盖优先 —— 与 `--from-form` 那条合并规则同一套。
    #    2026-10-14 现网 18:52：这条会话的表是第 1 段（0-15s）交的，第 2 段（16-30s）换完轨之后
    #    复验仍去读表里那份旧稿 ⇒ 算出"新稿只 23% 被听到"，而实际听到的就是这次传进去的 6 句新稿
    #    （自己复核 = 双向 1.000）。判据拿错稿子 = 把改成功的片子报成"口播没改"，和漏报一样是事故。
    _cli_script = str(getattr(a, "voice_script", "") or "").strip()
    if _cli_script:
        vals["voice_script"] = _cli_script
        print("[口播复验] 比对的稿子来源 = 命令行 --voice-script（表单里那份已提交过、不用它）")
    else:
        vals["voice_script"] = vals.get("voice_script") or ""
        print("[口播复验] 比对的稿子来源 = 表单载荷（这次没给 --voice-script；跨段复验请显式传稿）")
    verify_narration_audio(got, vals)
    print("[换口播] 交付请用这一条 → %s（音色=你给的音频；口型未重画，要补得等 RunningHub 充值）" % got)
    return 0


def _tick_narration_file(task_dir: str, evidence: str) -> None:
    """给合同的 narration-file 那一格打钩（打不上只出声，这条本身是 required=0）。"""
    try:
        from backend import skill_contract as skc              # noqa: E402
        r = skc.tick("super-clone", task_dir, "narration-file", evidence=evidence,
                     model=skc.NO_MODEL, impl="backend:invoke:voiceover_replace.run_narration_audio")
        if not r.get("ok"):
            print("[换口播] 回执这一格没打上钩：%s" % str(r.get("error") or "")[:140])
    except Exception as e:                                     # noqa: BLE001
        print("[换口播] 回执这一步没走通(%s: %s)" % (type(e).__name__, str(e)[:90]))


def stage_lipsync(a, sid: str) -> int:
    """`--stage lipsync`：第 6 步「对口型」的合法出口 —— 只对【盘上已有的成片】跑一次网关口型腿。

    🔴 为什么必须有这一档（2026-10-14 现网）：口型原来只在 wait/submit 两条路的尾巴上顺带跑，
       那一跑抽音轨失败（我的函数把 ffmpeg 的死因吞了）⇒ 片子在手、20 分的条子也批了，
       却没有任何一条合法路能【只重跑这一刀】。不重新编译、不重新提交：每 call 一次 wan3
       就是一段批准额度 + 上游一次新计费，拿它来"再试一次口型"就是双花。
    """
    film = find_film(a, sid)
    vals = {}
    try:
        vals = dict(((read_form_gate(sid) or {}).get("params")) or {}) if sid else {}
    except Exception as e:                             # noqa: BLE001
        print("[口型] 读不到判定口(%s)⇒ 拿不到 voice_mode ⇒ 这一刀不做（不猜档位）" % type(e).__name__)
    print("[口型] 目标成片 → %s" % film)
    got = apply_lip_sync(film, vals, log=print)
    if not got:
        print("[口型] 没做成 ⇒ 交付用的还是没对齐的那条，交付时要如实说")
        return 1
    print("[口型] 对齐成片 → %s" % got)
    print("[下一步] present 这一条 → 跑 scripts/deliver_pack.py 原样贴 markdown")
    return 0


def find_pending_jobs(task_dir: str) -> list:
    """盘上有哪些【已提交、成片还没落盘】的 wan3 任务（只读）。

    号是网关在提交成功那一刻当场落的（`wan3_job.json`，成片旁边）—— 没有这份留痕，
    进程一死号就丢，剩下的唯一动作就是重投（=双花）。
    """
    import glob
    out = []
    for p in glob.glob(os.path.join(task_dir, "outputs", "**", "wan3_job.json"), recursive=True):
        try:
            with open(p, encoding="utf-8") as f:
                j = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(j, dict) or j.get("kind") != "wan3_job" or not str(j.get("task_id") or "").strip():
            continue
        dest = str(j.get("output_path") or "").strip()
        if dest and os.path.isfile(dest):
            continue                                   # 已落盘 = 到终态了，不重复捞
        out.append({"job": str(j["task_id"]).strip(), "dest": dest, "file": p})
    return out


def wait_only(a, sid: str) -> int:
    """`--stage wait`：第 5 步「拿到视频」的合法出口 —— 只按盘上的号查询 + 下载，绝不重投。

    🔴 为什么这一档必须存在（2026-10-12 现网 session-5282eabf）：agent 的前台命令被 DSH 工具
       10 分钟上限强杀（dsh-pwsh-local 的 maxTimeoutMs=6e5，自己传更大也夹回来），而一条
       wan3-prime 生成实测 10–17 分钟 ⇒ "提交 + 等终态"写在同一条命令里必然被打断。
       打断之后它只剩两个坏选择：再 call 一次（双花）或收工（客户永远等不到片）。
       有了这一档：提交归提交、等待归等待，任何一条只读命令都能把片子捞回来。
    """
    tdir = ""
    if sid:
        try:
            tdir = str((read_form_gate(sid) or {}).get("task_dir") or "").strip()
        except Exception as e:                                  # noqa: BLE001
            print("[等待] 读不到判定口(%s: %s)⇒ 改用 --output-dir 定位任务目录" % (type(e).__name__, str(e)[:80]))
    if not tdir and (a.output_dir or "").strip():
        tdir = os.path.dirname(os.path.abspath(a.output_dir))   # output_dir = <任务>/outputs
    if not tdir or not os.path.isdir(tdir):
        raise SystemExit("[等待] 定位不到任务目录（要 --session <会话id> 或 --output-dir <任务>/outputs）"
                         "⇒ 不捞也不投（没有目录就没有号，猜一个目录去捞 = 替别人重投）。")
    jobs = find_pending_jobs(tdir)
    if not jobs:
        raise SystemExit(
            "[等待] 这条任务盘上没有【已提交、成片未落盘】的任务号（%s/outputs/**/wan3_job.json 不存在或都已落盘）"
            "⇒ 什么都不做。\n"
            "  wait 不是重投的口子：没号 = 这次根本没提交上过（或成片已经在盘上）。"
            "要出片请跑 --stage submit；别拿 wait 赌一次。" % tdir)
    _ensure_video_skill_on_path()
    from backend import gateway as gw                            # noqa: BLE001
    bad = 0
    for j in jobs:
        print("[等待] 只捞任务号 %s → %s（一个提交请求都不发）" % (j["job"], j["dest"]))
        env = gw.invoke("video.generate", "wan3-prime", "local",
                        {"job_id": j["job"], "output_path": j["dest"]}) or {}
        res = env.get("result") or {}
        cr = env.get("credits") or {}
        path = str(res.get("path") or j["dest"])
        got = os.path.isfile(path)
        print("[等待] 上游回：%s | ok=%s | 成片落盘=%s | 实扣 %s 分（hold=%s）"
              % (str(res.get("kind") or env.get("error") or "")[:120], bool(env.get("ok")), got,
                 cr.get("charged"), str(cr.get("hold_id") or "")[:12]))
        if not (env.get("ok") and got):
            bad += 1
            print("        还没到手 ⇒ 继续用同一条 --stage wait 捞（号还在 %s），**不要重投**。" % j["file"])
        else:
            _p = {}
            try:
                _p = dict(((read_form_gate(sid) or {}).get("params")) or {}) if sid else {}
            except Exception as e:                            # noqa: BLE001
                print("[等待] 读不到表单里的声音档(%s)⇒ 这一条不做换轨，交付时如实说口播没改" % type(e).__name__)
            try:
                _sw = apply_narration_swap(path, _p)
            except SystemExit as e:
                bad += 1
                print("[改口播] 这一条没换成：%s" % str(e)[:200])
                continue
            _tgt = _sw or path                              # 直出那条不需要换轨 ⇒ 片子本身就是新的
            _lip = apply_lip_sync(_tgt, _p)                  # 口型这一刀与"有没有换轨"无关，只看 voice_mode
            _tgt = _lip or _tgt
            verify_narration_audio(_tgt, _p)                # 🔴 交出去之前必须听一遍：说的是不是新稿
            report_frame_text(_tgt, tdir, params=_p)        # 同一姿势：交出去之前必须【看一遍有没有字】
            if _sw or _lip:
                print("        到手 + 口播已改%s ⇒ 交付用 %s"
                      % ("、口型已对齐" if _lip else "（口型【没】对齐，原因见上一行）", _tgt))
            else:
                print("        到手：接下来 present → deliver_pack → 原样贴 markdown。")
    return 2 if bad else 0


def report_frame_text(film: str, tdir: str, product_images=None, params=None) -> dict:
    """成片抽帧 + 本机 OCR ⇒ 「全画面干净」这件事落到物证 `frame_text_verify.json`（**0 分、0 上游调用**）。

    🔴 为什么挂在这里而不是让 agent 记得跑：合同 `negative-lock` 那一格只查 prompt 文本里有没有
       「不得出现字幕」这句话，**不查像素**（2026-10-14 现网：那句在位、钩照打、成片 8/8 帧仍带原片字幕）。
       音轨有 `verify_narration_audio()` 复验、画面文字一个复验口都没有 ⇒ 负向词被当成了质量证据。
       这一步是「拿到片」这条路上必然发生的动作，绑在它后面才有牙齿；写在散文里等于不存在的步骤。
    ⚠️ 白名单来自**产品图自己的 OCR**（合同明令包装真实文字必须保留），不是拍脑袋的词表；
       读不到 OCR 引擎时如实写「这一项没做」，绝不留「空白 = 过了」。
    """
    try:
        _ensure_video_skill_on_path()
        from backend.services import super_clone_wan3 as scw      # noqa: BLE001
    except Exception as e:                                        # noqa: BLE001
        print("[画面文字] 引擎没导进来(%s)⇒ 这一项没做，交付时如实说明" % type(e).__name__)
        return {}
    if not product_images:
        d = os.path.join(tdir, "inputs", "product_images")
        product_images = [os.path.join(d, f) for f in sorted(os.listdir(d))] if os.path.isdir(d) else []
    out = os.path.join(tdir, "outputs", "frame_text_verify.json")
    try:
        rep = scw.verify_frame_text(film, out, product_image_paths=product_images)
    except Exception as e:                                        # noqa: BLE001
        print("[画面文字] 复验没做成：%s: %s ⇒ 这一项【没做】，不许当「过了」"
              % (type(e).__name__, str(e)[:140]))
        return {}
    if rep.get("clean") is True:
        print("[画面文字] ✅ %s 帧（引擎 %s，间隔 %ss）0 处非包装文字 ⇒ 全画面干净"
              % (rep.get("frames_checked"), rep.get("engine"), rep.get("interval_seconds")))
    elif rep.get("clean") is False:
        print("[画面文字] 🔴 %s" % rep.get("note"))
        for s in (rep.get("suspects") or [])[:6]:
            print("            t=%ss 读到「%s」conf=%s 位置=%s"
                  % (s.get("t"), str(s.get("text"))[:28], s.get("conf"), s.get("center")))
        print("            物证：%s ⇒ 交付必须带上这一条（换无字幕源或走 dewatermark 加工腿）" % out)
    else:
        print("[画面文字] ⚠ %s" % rep.get("note"))
    return rep


def submitted_but_unpaid(task_dir: str) -> list:
    """这条任务里【已经提交过、成片还没落盘】的段 ⇒ 返回 [(号, 落点)]。

    🔴 为什么提交前要问这个（2026-10-12 现网 session-5282eabf 13:25）：agent 拿
       `_wan3_request.json`（那是**物证转储**，不是输入口）去敲一个不存在的
       `gateway.py submit-existing` 想重投 —— 上游一次没建上时它猜"再投一次"，
       而真建上时那就是双花。判据不看它用什么姿势提交，只看盘上有没有号：
       有号没片 ⇒ 只许 `--stage wait`。
    """
    import glob
    out = []
    for p in glob.glob(os.path.join(task_dir, "outputs", "**", "wan3_job.json"), recursive=True):
        try:
            with open(p, encoding="utf-8") as f:
                j = json.load(f)
        except (OSError, ValueError):
            continue
        tid = str(j.get("task_id") or "").strip()
        dest = str(j.get("output_path") or "").strip()
        if tid and dest and not os.path.isfile(dest):
            out.append((tid, dest))
    return out


def main(argv=None) -> int:
    _hide_console_window()          # 别在客户桌面上留一个能误杀编译的黑框
    p = build_parser()
    a = p.parse_args(argv)
    sid = (a.session or os.environ.get("DSH_SESSION_ID") or "").strip()
    vals = {d: v for d, v in vars(a).items() if d not in ("from_form", "stage", "session")}
    _gate = None

    # ⓪ 「只捞不投」走在所有校验之前：这一步不需要原片/产品图那五格（号在盘上），
    #    拿 missing_required 拦它等于逼人再提交一次 —— 那正是这一档要避免的双花。
    if a.stage == "wait":
        return wait_only(a, sid)
    if a.stage == "narration":
        return stage_narration(a, sid)
    if a.stage == "lipsync":
        return stage_lipsync(a, sid)

    # ① 入口自己读表：值来自"客户在右栏提交的那一份"，命令行显式给的只作覆盖（来源逐格印出来）。
    if a.from_form:
        if not sid:
            raise SystemExit("--from-form 要会话 id（$env:DSH_SESSION_ID 或 --session <会话id>）——"
                             "没有会话就找不到那张表，绝不猜一个填进去。")
        try:
            _gate = read_form_gate(sid)
        except Exception as e:  # noqa: BLE001
            raise SystemExit("--from-form 读不到判定口(8100)：%s: %s ⇒ 不跑。"
                             "闸门坏了不许当成「闸没拦」（先确认 8100 在跑）。"
                             % (type(e).__name__, str(e)[:120]))
        if not _gate.get("submitted"):
            raise SystemExit(
                "这条会话的表【还没由客户提交】（reason=%s）⇒ --from-form 没有可信的值来源。\n"
                "表本来就弹在右栏：让他填完点【提交这张表】，交完你会被叫醒接着跑 —— "
                "不要替他填、更不要因为等不到就自己编一组值。"
                % (_gate.get("reason") or "not-submitted"))
        vals, _prov, _un = apply_form_values(vals, _gate, multi_dests(p))
        print("[参数来源] %s" % (" | ".join(_prov) if _prov else "全部来自命令行（表单的值一个都没用上）"))
        if _un:
            print("[表单里还有] %s ⇒ 这几格不是引擎入参（是给 agent 读的口径），本入口不吃"
                  % "、".join(_un))

    # ⓪b 同一条任务不许起第二台（在导入引擎、动任何盘之前；拒 = 0 分、0 调用）。
    _cpid, _cfile = compile_collision(_gate, vals.get("output_dir"))
    if _cpid:
        raise SystemExit(
            "[已经在跑] 这条任务的编译已有一台在跑（pid=%s，牌子=%s）⇒ 不起第二台：\n"
            "  两台会写同一批产物（prompt.txt / storyboard.json / 回执互相覆盖），MiMo 和编译器还要各跑一遍。\n"
            "  看进度：type 「%s」（8100 点火那条把输出写在这里），跑完会自动叫醒你。\n"
            "  要重跑：等那台结束（它跑完会摘牌），或在右栏改参数重新提交那张表。"
            % (_cpid, _cfile, os.path.join(os.path.dirname(_cfile), "compile.log")))

    # ⓪c 已有任务号、成片未落 ⇒ 这一档只许 wait，不许再提交（双花的唯一屏障：看盘，不看它怎么拼参数）
    if a.stage == "submit":
        _tdir = str((_gate or {}).get("task_dir") or "").strip() or (
            os.path.dirname(os.path.abspath(vals.get("output_dir") or "")) if vals.get("output_dir") else "")
        _owed = submitted_but_unpaid(_tdir) if _tdir else []
        if _owed:
            raise SystemExit(
                "[已提交过] 这条任务盘上有 %d 个任务号、成片还没落盘：%s\n"
                "  ⇒ 本入口不再提交（再提交=双花，平台按次计费）。只许跑：\n"
                "     python .dsh/skills/super-clone/runner/run.py --stage wait\n"
                "  🔴 也不许拿 outputs/…/_wan3_request.json 当输入口重投 —— 那份是【物证转储】，不是提交入口。"
                % (len(_owed), "；".join("%s→%s" % (t, os.path.basename(d)) for t, d in _owed[:3])))

    _miss = missing_required(vals)
    if _miss:
        raise SystemExit(missing_message(_miss, sid))
    if not (vals.get("product_name") or "").strip():
        raise SystemExit("product-name 不能为空（入口校验）")
    if vals.get("voice_mode"):                # 没声明就不管（老写法照跑）；声明了就必须自洽
        _v = voice_mode_problems(vals["voice_mode"], bool(vals.get("voice_audio")),
                                 bool(str(vals.get("voice_script") or "").strip()))
        if _v:
            raise SystemExit(_v)
    # 背景文字通道：--scene-text 并进 customer_request 的「背景要求」段（编译器从这一处读背景细节；
    # 有 --scene-image 时图为事实源，这段文字只补图里没有的细节）。
    _req = fold_scene_text(vals.get("customer_request"), vals.get("scene_text"))
    # 🔴 表单前置（在导入引擎之前，失败就 0 分）：见 form_gate_verdict 的现网事故形状。
    if not sid:
        print("[表单前置] 读不到会话 id ⇒ 这一道不判（不猜一个会话填进去）")
    else:
        if _gate is None:
            try:
                _gate = read_form_gate(sid)
            except Exception as e:  # noqa: BLE001
                raise SystemExit(
                    "[表单前置] 判定口(8100)没回话：%s: %s ⇒ 不跑。"
                    "闸门坏了不许当成「闸没拦」（先确认 8100 在跑，再重试同一条命令）。"
                    % (type(e).__name__, str(e)[:120]))
        _fg = form_gate_verdict(_gate)
        if _fg:
            raise SystemExit(_fg)
    # 🔴 花钱那一刀的身份：网关读会话只有一处（`gateway._session_of`：先看 params["session"]，
    #    否则读 DSH_SESSION_ID），而这条流水线从入口到 `run_one_segment_prime` 一路【没把 --session 往下传】
    #    ⇒ 从 DSH 自带 agent 之外（Qoder / 运维 shell）跑起来的这一台身份是空串 ⇒ 人批过的按会话绑的条子
    #    对不上号，回 approval_required（2026-10-14 现网 16:39：条子 ok_3de3fb96 段 0/1 没被消费、0 分未扣，
    #    18 分钟编译白等）。这里补的是【同一条会话】—— 上面表单前置刚用同一个 sid 过了判定口，不是猜的 id。
    if sid and not os.environ.get("DSH_SESSION_ID"):
        os.environ["DSH_SESSION_ID"] = sid
        print("[身份] 本次以会话 %s 提交（这条入口不往 params 里塞 session，只能走网关那唯一读法）" % sid)
    # ⓪d 自己挂牌（只在「只编译」这一档）：所有会早退的校验都过了、还没动引擎之前。
    #     不挂牌的那半形状现网演过一遍：agent 自己起的编译跑完，挂号被"这台不是我起的"拒掉
    #     ⇒ 产物落盘却没人被叫醒；收工拦截也看不见它 ⇒ 它一收工，客户对着右栏干等。
    if a.stage == "compile":
        claim_compile_pid(_gate, vals.get("output_dir"))
    _ensure_video_skill_on_path()
    # 延迟导入：--help / 参数校验不需要重依赖（playwright 等）
    from backend.services.super_clone_wan3 import run_pipeline  # noqa

    os.makedirs(vals["output_dir"], exist_ok=True)
    # 🔴 extra_note 的【物证回读】：这一格是编译输入之一（也算进 compile_fp），但表单里没有这一格、
    #    只有 CLI --extra-note 能传。2026-10-23 现网形状：人审帧后 agent 带 extra-note 编译过一份，
    #    随后【批准即点火】那条命令没有这一格 ⇒ 算出的 compile_fp 必然不符 ⇒ 软复用永远不命中
    #    ⇒ 每次点链接都从 Step1 重跑分镜+编译，还会把人已批的那份 prompt.txt 原地重写掉。
    #    物证就在段目录里（extra_note.txt 由编译自己落盘，合同 storyboard-fact-check 那一格），
    #    读它 = 用回【那份已批产物真正吃过的原文】，不是猜一个新值。
    if not str(vals.get("extra_note") or "").strip():
        for _cand in (os.path.join(vals["output_dir"], "step2_wan3", "extra_note.txt"),
                      os.path.join(vals["output_dir"], "extra_note.txt")):
            if os.path.isfile(_cand):
                with open(_cand, encoding="utf-8") as _ef:
                    vals["extra_note"] = _ef.read().strip()
                print("[入口] extra_note 表单/命令行都没给 ⇒ 回读编译物证 %s（%d 字）；"
                      "不回读会算出另一个 compile_fp，把已批的那份 prompt 重编掉"
                      % (_cand, len(vals["extra_note"])))
            break
    try:
        result = asyncio.run(run_pipeline(
        task_id=vals["task_id"],
        video_path=vals["video"],
        product_image_paths=list(vals["product_images"]),
        customer_request=_req or vals["product_name"],
        output_dir=vals["output_dir"],
        trim_start=vals.get("trim_start"),
        trim_end=vals.get("trim_end"),
        model_image_path=vals.get("model_image"),
        scene_image_path=vals.get("scene_image") or None,
        product_name=vals["product_name"],
        product_facts=vals.get("product_facts"),
        resolution=vals.get("resolution"),
        aspect_ratio=vals.get("aspect_ratio"),
        engine=vals["engine"],
        split_by_shots=vals.get("split_by_shots") or False,
        extra_compile_note=vals.get("extra_note"),
        extra_model_image_paths=list(vals["extra_model_images"]) if vals.get("extra_model_images") else None,
        voice_audio_paths=list(vals["voice_audio"]) if vals.get("voice_audio") else None,
        voice_script=str(vals.get("voice_script") or "").strip() or None,
        as_duration_chunk=vals.get("as_duration_chunk") or False,
        stop_after=("compile" if a.stage == "compile" else None),
        # 🔴 `--stage compile` 那一档【永远不复用】：它的语义就是"把编译走一遍"，
        #    让它命中缓存等于把"重编译 0 分"那个合法出口变成假出口（改完 extra-note 重编会读到旧 prompt）。
        reuse_compile=((bool(getattr(a, "reuse_compile", False)) or bool(getattr(a, "reuse_compile_soft", False)))
                       and a.stage != "compile"),
        # 硬档（--reuse-compile）不命中就当场拒；软档（--reuse-compile-if-hit，服务端点火用的）不命中就重编。
        reuse_compile_strict=bool(getattr(a, "reuse_compile", False)),
        resume=bool(getattr(a, "resume", False)),
        ))
    except Exception as e:                                    # noqa: BLE001
        # 编译炸了也要回程报一句 —— 否则交完表页面永远停在"编译中"，人只能来问
        if a.stage == "compile":
            report_compiled(sid, False, "%s: %s" % (type(e).__name__, str(e)[:180]))
        raise
    print(json.dumps(result, ensure_ascii=False, default=str))
    if isinstance(result, dict) and result.get("compiled_only"):
        # ⑤ 只编译档位：物证与回执已经落盘，一条都没提交 ⇒ 明确出声，别让人以为在跑
        #    ⚠️ "0 分"只管【出片腿】：Step1 后面那条抽帧补充腿是 video.analyze（免批但不免账，
        #    8 帧一次实测 178 分），它花没花、花了多少在上一行日志里写得很死，这里不许含糊成"0 分"。
        print("[档位=只编译] 没建预扣、没提交 ⇒ 出片腿 0 分；看图腿(video.analyze)另按台账计"
              "（见上面「抽帧补充…」那行的实扣）。提示词/分镜/回执在：" +
              "、".join(str(s.get("dir")) for s in (result.get("segments") or [])[:6]))
        print("[下一步] 对着 frames/ 逐秒核动作与分镜；不符就补 --extra-note 重跑同一条 --stage compile"
              "（出片腿还是 0 分；抽帧腿命中缓存就 0 新支出，日志会写「累计实扣」而不是「本次实扣」）；"
              "认可后去掉 --stage（或 --stage submit）再走报价。")
        if a.stage == "compile":
            report_compiled(sid, True)                        # 交表即点火的回程：叫 agent 来报价
        return 0
    if isinstance(result, dict):
        if result.get("success") is False or result.get("error"):
            return 2
    _sw = apply_narration_swap((result or {}).get("final_path")
                               or (result or {}).get("local_path"), vals) \
        if isinstance(result, dict) else ""
    _film = str((result or {}).get("final_path") or (result or {}).get("local_path") or "")
    if _sw:
        result["narration_swapped_path"] = _sw
    _lip = apply_lip_sync(_sw or _film, vals)               # 口型这一刀只看 voice_mode，与换没换轨无关
    if _lip:
        result["lip_synced_path"] = _lip
    _final = _lip or _sw or _film
    verify_narration_audio(_final, vals)   # 交出去之前必须听一遍：说的是不是新稿
    if _final and os.path.isfile(_final):
        report_frame_text(_final, os.path.dirname(os.path.normpath(str(vals["output_dir"]))),
                          product_images=vals.get("product_images"), params=vals)
    if _sw or _lip:
        print("[改口播] 交付三件套请用这一条 → %s（%s）"
              % (_final,
                 "口型已对齐" if _lip else "口型【没】对齐：原因见上一行，交付时要如实说"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
