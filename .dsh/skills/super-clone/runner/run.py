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
    p.add_argument("--stage", default="submit", choices=["compile", "submit"],
                   help="compile=只走到「物证+回执+合同核验」就返回（不提交、不建预扣、0 分）；"
                        "submit=照常出片。SKILL.md 要求「不符就补 extra-note 重编译（0 分）」，"
                        "这一档就是那句散文的合法出口 —— 以前没有它，遵守它只能绕开入口。")
    p.add_argument("--session", default=None,
                   help="DSH 会话 id（默认读 $env:DSH_SESSION_ID）。用于查右栏那张表有没有出现过；"
                        "读不到就不判（常驻服务里没有会话 id，绝不猜一个填进去）。")
    return p


def main(argv=None) -> int:
    p = build_parser()
    a = p.parse_args(argv)
    sid = (a.session or os.environ.get("DSH_SESSION_ID") or "").strip()
    vals = {d: v for d, v in vars(a).items() if d not in ("from_form", "stage", "session")}
    _gate = None

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
    _ensure_video_skill_on_path()
    # 延迟导入：--help / 参数校验不需要重依赖（playwright 等）
    from backend.services.super_clone_wan3 import run_pipeline  # noqa

    os.makedirs(vals["output_dir"], exist_ok=True)
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
    print(json.dumps(result, ensure_ascii=False, default=str))
    if isinstance(result, dict) and result.get("compiled_only"):
        # ⑤ 只编译档位：物证与回执已经落盘，一条都没提交 ⇒ 明确出声，别让人以为在跑
        print("[档位=只编译] 0 分、没建预扣、没提交。提示词/分镜/回执在：" +
              "、".join(str(s.get("dir")) for s in (result.get("segments") or [])[:6]))
        print("[下一步] 对着 frames/ 逐秒核动作与分镜；不符就补 --extra-note 重跑同一条 --stage compile"
              "（还是 0 分）；认可后去掉 --stage（或 --stage submit）再走报价。")
        return 0
    if isinstance(result, dict):
        if result.get("success") is False or result.get("error"):
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
