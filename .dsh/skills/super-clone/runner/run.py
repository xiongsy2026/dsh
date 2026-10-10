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
        return ""            # agent 已回填 ⇒ 表已经在客户眼前了（他随时可改）
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="超级复刻 Wan3.0 出片（薄封装 run_pipeline）")
    p.add_argument("--task-id", required=True)
    p.add_argument("--video", required=True, help="原片（或该段裁剪后原片）路径")
    p.add_argument("--product-images", nargs="+", required=True, help="产品图 1-10 张")
    p.add_argument("--product-name", required=True, help="产品名（入口校验：不能为空）")
    p.add_argument("--customer-request", default=None, help="缺省用 --product-name")
    p.add_argument("--scene-text", default=None,
                   help="场景替换描述 = 背景的唯一输入口（只走文字）。会并进 customer_request 的「背景要求」段，"
                        "因为编译器只从那里读背景（references/compiler-contract.md 任务4）")
    p.add_argument("--output-dir", required=True)
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
    p.add_argument("--engine", default="wan3")
    p.add_argument("--extra-note", default=None, help="extra_compile_note")
    p.add_argument("--split-by-shots", action="store_true")
    p.add_argument("--as-duration-chunk", action="store_true", help="按段调用时置 True（难单每段 True）")
    p.add_argument("--session", default=None,
                   help="DSH 会话 id（默认读 $env:DSH_SESSION_ID）。用于查右栏那张表有没有出现过；"
                        "读不到就不判（常驻服务里没有会话 id，绝不猜一个填进去）。")
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    if not (a.product_name or "").strip():
        raise SystemExit("product-name 不能为空（入口校验）")
    if a.voice_mode:                      # 没声明就不管（老写法照跑）；声明了就必须自洽
        _v = voice_mode_problems(a.voice_mode, bool(a.voice_audio))
        if _v:
            raise SystemExit(_v)
    # 背景只走文字：--scene-text 并进 customer_request 的「背景要求」段（编译器只认这一处）。
    _req = fold_scene_text(a.customer_request, a.scene_text)
    # 🔴 表单前置（在导入引擎之前，失败就 0 分）：见 form_gate_verdict 的现网事故形状。
    sid = (a.session or os.environ.get("DSH_SESSION_ID") or "").strip()
    if not sid:
        print("[表单前置] 读不到会话 id ⇒ 这一道不判（不猜一个会话填进去）")
    else:
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

    os.makedirs(a.output_dir, exist_ok=True)
    result = asyncio.run(run_pipeline(
        task_id=a.task_id,
        video_path=a.video,
        product_image_paths=list(a.product_images),
        customer_request=_req or a.product_name,
        output_dir=a.output_dir,
        trim_start=a.trim_start,
        trim_end=a.trim_end,
        model_image_path=a.model_image,
        product_name=a.product_name,
        product_facts=a.product_facts,
        resolution=a.resolution,
        aspect_ratio=a.aspect_ratio,
        engine=a.engine,
        split_by_shots=a.split_by_shots,
        extra_compile_note=a.extra_note,
        extra_model_image_paths=list(a.extra_model_images) if a.extra_model_images else None,
        voice_audio_paths=list(a.voice_audio) if a.voice_audio else None,
        as_duration_chunk=a.as_duration_chunk,
    ))
    print(json.dumps(result, ensure_ascii=False, default=str))
    if isinstance(result, dict):
        if result.get("success") is False or result.get("error"):
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
