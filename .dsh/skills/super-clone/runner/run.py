#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""super-clone skill runner：薄封装 D:\\video-skill 的 super_clone_wan3.run_pipeline。

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
import sys

VIDEO_SKILL_HOME = os.environ.get("VIDEO_SKILL_HOME", r"D:\video-skill")

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
       8100 用 `D:\\video-skill\\.venv\\Scripts\\pythonw.exe` 起子进程，那是个**重定向壳** ——
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


# 三档的唯一源 = SKILL.md『声音模式与换音色』(193-199)。这里不再解释语义，只做入口一致性判定。
VOICE_MODES = ("preserve_original", "timbre_swap", "rewrite_voice")


def voice_mode_problems(mode, has_audio) -> str:
    """声音档与"有没有交目标人声"必须自洽。判据抽成纯函数 ⇒ 闸直接跑值表，不靠人在场。
    返回错误串；空串 = 放行。"""
    if mode not in VOICE_MODES:
        return "声音档只认 %s（收到 %r）" % ("/".join(VOICE_MODES), mode)
    if mode in ("timbre_swap", "rewrite_voice") and not has_audio:
        return ("%s 必须给目标人声参考（--voice-audio）：没有音色可换，"
                "我不会拿原片原声冒充换过声音" % mode)
    if mode == "preserve_original" and has_audio:
        return ("preserve_original（原声保留）却给了 --voice-audio = 自相矛盾："
                "贴回原声就不会换音色（SKILL.md 硬规则 13 的穿帮形状）")
    return ""


FORM_GATE_URL = "http://127.0.0.1:8100/api/skills/form?session="


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
                   help="场景替换描述 = 背景的唯一输入口（只走文字）。会并进 customer_request 的「背景要求」段，"
                        "因为编译器只从那里读背景（references/compiler-contract.md 任务4）")
    p.add_argument("--output-dir", default=None, help="缺省用 <任务目录>/outputs")
    p.add_argument("--product-facts", default=None, help="客户明确陈述的非视觉事实")
    p.add_argument("--model-image", default=None, help="该段唯一脸图（一段一张脸）")
    p.add_argument("--extra-model-images", nargs="*", default=None, help="多脸时其余脸图(不整段塞)")
    p.add_argument("--voice-mode", default=None, choices=list(VOICE_MODES),
                   help="声音档：preserve_original 原声保留 / timbre_swap 只换音色 / rewrite_voice 换人声+改口播。"
                        "唯一语义源 = SKILL.md『声音模式与换音色』；这一档决定 --voice-audio 必给还是禁给。")
    p.add_argument("--voice-audio", action="append", default=None,
                   help="目标音色参考音频（可多次给，≤5 段、单段 1-15s、总长 ≤15s）。"
                        "换人（男/女）时必须给：Wan 生成时直接用该音色合成口播，原片原声不贴回。"
                        "不换人（换产品/换背景）时不给，原片原声照常贴回。")
    # 🔴 没有 --scene-image：本 lane 背景只走文字（2026-10-09 定）。客户参考图先由 agent
    #    用看图能力转成文字写进 --customer-request，图不作为生成入参。引擎侧 scene_image_path
    #    仍然存在（别的线路在用），但这条入口不传 ⇒ 传了会直接 argparse 报错，不会静默收下。
    p.add_argument("--trim-start", type=float, default=None)
    p.add_argument("--trim-end", type=float, default=None)
    p.add_argument("--resolution", default=None)
    p.add_argument("--aspect-ratio", default=None)
    p.add_argument("--engine", default="wan3", choices=["wan3"],
                   help="只有一个合法值 wan3。引擎锁的唯一源在服务层 engine_lock_error()，这里只是让人敲命令时就撞墙，而不是跑到一半才拒")
    p.add_argument("--extra-note", default=None,
                   help="extra_compile_note：合同 storyboard-fact-check 那一步的【唯一授权手段】——"
                        "MiMo 的分镜/动作标签与原片不符时，在这里按秒写死真实时间轴压过它（重编译 0 分）")
    p.add_argument("--split-by-shots", action="store_true")
    p.add_argument("--as-duration-chunk", action="store_true", help="按段调用时置 True（难单每段 True）")
    p.add_argument("--from-form", action="store_true",
                   help="🔴 值全部从这条会话【已提交】的右栏表单读（媒体是服务端存的绝对路径），"
                        "命令行显式给的参数只作覆盖。没有它 agent 就得手抄参数 —— 抄就有猜错的那一天。")
    p.add_argument("--stage", default="submit", choices=["compile", "submit", "wait"],
                   help="compile=只走到「物证+回执+合同核验」就返回（不提交、不建预扣、0 分）；"
                        "submit=照常出片。SKILL.md 要求「不符就补 extra-note 重编译（0 分）」，"
                        "这一档就是那句散文的合法出口 —— 以前没有它，遵守它只能绕开入口。\n"
                        "wait=【只捞不投】第 5 步：读盘上已提交未落盘的任务号（wan3_job.json），"
                        "查询 + 下载 + 交付，一个提交请求都不发。")
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
            print("        到手：接下来 present → deliver_pack → 原样贴 markdown。")
    return 2 if bad else 0


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
        _v = voice_mode_problems(vals["voice_mode"], bool(vals.get("voice_audio")))
        if _v:
            raise SystemExit(_v)
    # 背景只走文字：--scene-text 并进 customer_request 的「背景要求」段（编译器只认这一处）。
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
    # ⓪d 自己挂牌（只在「只编译」这一档）：所有会早退的校验都过了、还没动引擎之前。
    #     不挂牌的那半形状现网演过一遍：agent 自己起的编译跑完，挂号被"这台不是我起的"拒掉
    #     ⇒ 产物落盘却没人被叫醒；收工拦截也看不见它 ⇒ 它一收工，客户对着右栏干等。
    if a.stage == "compile":
        claim_compile_pid(_gate, vals.get("output_dir"))
    _ensure_video_skill_on_path()
    # 延迟导入：--help / 参数校验不需要重依赖（playwright 等）
    from backend.services.super_clone_wan3 import run_pipeline  # noqa

    os.makedirs(vals["output_dir"], exist_ok=True)
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
        product_name=vals["product_name"],
        product_facts=vals.get("product_facts"),
        resolution=vals.get("resolution"),
        aspect_ratio=vals.get("aspect_ratio"),
        engine=vals["engine"],
        split_by_shots=vals.get("split_by_shots") or False,
        extra_compile_note=vals.get("extra_note"),
        extra_model_image_paths=list(vals["extra_model_images"]) if vals.get("extra_model_images") else None,
        voice_audio_paths=list(vals["voice_audio"]) if vals.get("voice_audio") else None,
        as_duration_chunk=vals.get("as_duration_chunk") or False,
        stop_after=("compile" if a.stage == "compile" else None),
        ))
    except Exception as e:                                    # noqa: BLE001
        # 编译炸了也要回程报一句 —— 否则交完表页面永远停在"编译中"，人只能来问
        if a.stage == "compile":
            report_compiled(sid, False, "%s: %s" % (type(e).__name__, str(e)[:180]))
        raise
    print(json.dumps(result, ensure_ascii=False, default=str))
    if isinstance(result, dict) and result.get("compiled_only"):
        # ⑤ 只编译档位：物证与回执已经落盘，一条都没提交 ⇒ 明确出声，别让人以为在跑
        print("[档位=只编译] 0 分、没建预扣、没提交。提示词/分镜/回执在：" +
              "、".join(str(s.get("dir")) for s in (result.get("segments") or [])[:6]))
        print("[下一步] 对着 frames/ 逐秒核动作与分镜；不符就补 --extra-note 重跑同一条 --stage compile"
              "（还是 0 分）；认可后去掉 --stage（或 --stage submit）再走报价。")
        if a.stage == "compile":
            report_compiled(sid, True)                        # 交表即点火的回程：叫 agent 来报价
        return 0
    if isinstance(result, dict):
        if result.get("success") is False or result.get("error"):
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
