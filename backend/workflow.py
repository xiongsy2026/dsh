"""
============================================================================
⚠️ 爆款复刻工作流 - 受保护文件（整文件保护）
参见 BOUNDARY.md。修改本文件必须经过显式确认：
  1. 运行 python scripts/check_replica_boundary.py 记录变更
  2. 运行 pytest tests/test_replica_boundary.py -v 验证行为
  3. 运行 python scripts/update_replica_boundary.py 更新签名基准
未经流程确认的修改将破坏工作流稳定性。
============================================================================

爆款复刻工作流 - 主入口
固定管线：Step1 → Step2(抽帧) → Step2.5(素材预处理) → Step3(产品替换) → Step4(海报) → Step5(视频生成)
不允许跳步或乱跑

====================分镜图存储规范（命名严格统一，禁止混淆）====================
step2/frames/shot_01.jpg              ← Step2 第1帧抽帧（Step5 @原视频分镜图，仅参考光影效果）
step2/storyboard.jpg                  ← Step2 原始抽帧拼图（未替换产品）
step3/shot_XX.png                     ← Step3 单帧替换后的图片
step3/storyboard_replaced.jpg         ← Step3 替换后分镜拼图（Step5 的 @替换分镜图，产品一致性权威）
step4/shot_reference.jpg              ← Step4 中间产物（拼接替换后 shot 给图片编辑API当输入，禁止展示/上传）
step4/final_storyboard.jpg            ← Step4 最终海报（Step5 的 @故事板，指导分镜构图）
====================================================================================
"""
import json
import os
import sys
import io
import base64
import logging
import argparse
from pathlib import Path
from datetime import datetime

# 修复 Windows 中文编码问题
if sys.platform == "win32":
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, "encoding", "").lower() not in ("utf-8", "utf8"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    if hasattr(sys.stderr, "buffer") and getattr(sys.stderr, "encoding", "").lower() not in ("utf-8", "utf8"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    # 抑制 httpx/openai DEBUG 日志
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

# 确保项目根目录在 sys.path 中
ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import (
    step1_mimo_parse,
    step2_extract_frames,
    step2_preprocess_materials,
    step3_replace_product,
    step4_storyboard_poster,
    step5_video_generate,
)
from pipeline import step5_h3_generate
from pipeline import step5_multi_shot
from typing import Optional


# ── 多产品图角度匹配（仅 /replica 路径使用） ──
# 让多模态 LLM 同时看【原帧 + N 张产品图】，识别原帧产品角度/形态，
# 从 N 张产品图中选最匹配的那张；若无一精确匹配，输出推理依据让 image2
# 按指示调整角度。返回 (选中产品图路径, 匹配分析文本)。
# 失败降级：返回 (第1张产品图, "")。
async def _analyze_and_match_product_image(
    original_frame: Path,
    product_image_paths: list,
    shot_desc: Optional[dict] = None,
    product_name: str = "",
) -> tuple:
    """多图角度匹配：选最匹配原帧产品角度/形态的那张产品图。

    Returns:
        (选中产品图 Path, LLM 输出的分析文本)。失败时分析文本为空。
    """
    import asyncio as _asyncio
    from backend.services import gpt_client as _gpt

    # 过滤有效产品图
    valid_paths = [Path(p) for p in product_image_paths if p and Path(p).exists()]
    if not valid_paths:
        return (None, "")
    if len(valid_paths) == 1:
        return (valid_paths[0], "")

    def _to_b64(p: Path, max_width: int = 768) -> str:
        """压缩到 max_width 宽度 JPEG q85，减小 base64 体积"""
        try:
            from PIL import Image as _I
            import io as _io
            im = _I.open(p).convert("RGB")
            if im.width > max_width:
                im = im.resize((max_width, int(im.height * max_width / im.width)), _I.LANCZOS)
            buf = _io.BytesIO()
            im.save(buf, format="JPEG", quality=85)
            return base64.b64encode(buf.getvalue()).decode()
        except Exception:
            return base64.b64encode(p.read_bytes()).decode()

    try:
        ob = _to_b64(original_frame)
        prod_b64s = [_to_b64(p) for p in valid_paths]
    except Exception as e:
        print(f"  [多图匹配] 图片读取失败: {e}")
        return (valid_paths[0], "")

    shot_hint = ""
    if shot_desc:
        scene = shot_desc.get("画面动作", {}) or {}
        shot_hint = (
            f"分镜描述参考：画面内容={scene.get('画面内容', '')}，"
            f"人物动作={scene.get('人物动作', '')}，"
            f"产品动作={scene.get('产品动作', '')}"
        )

    # 构造图片顺序说明（让 LLM 知道每张产品图对应编号）
    img_index_desc = "、".join([f"第{k+2}张图（编号 P{k+1}）" for k in range(len(valid_paths))])

    analysis_prompt = (
        f"第1张图：原视频关键帧（含待替换的目标产品）。\n"
        f"第2~{len(valid_paths)+1}张图：{len(valid_paths)} 张新产品候选图，每张角度/形态可能不同。\n"
        f"  {img_index_desc}\n\n"
        f"{shot_hint}\n"
        f"用户指定目标产品名：「{product_name or '（未指定，请识别画面中最突出的产品）'}」\n\n"
        "请按以下步骤分析（必须严格遵守）：\n"
        "1. 【原帧产品识别】仔细识别第1张原图中目标产品的：\n"
        "   - 呈现的角度（正面/侧面/背面/俯视/仰视/45度斜视等）\n"
        "   - 形态（打开/折叠/包装完整/拆封/摆放/悬挂/手持等）\n"
        "   - 占画面比例、位置、与人物/背景的接触关系\n"
        "   - 是否被遮挡、被遮挡的部分\n\n"
        "2. 【候选图逐一分析】对每张产品候选图（P1, P2, ...），分别识别：\n"
        "   - 角度（正面/侧面/背面/俯视/仰视等）\n"
        "   - 形态（打开/折叠/包装/摆放等）\n"
        "   - 与原图产品的角度/形态匹配度（高/中/低）\n\n"
        "3. 【匹配决策】从候选图中选 1 张与原图产品角度/形态最匹配的：\n"
        "   - 若有多张角度一致：优先选形态/包装一致的\n"
        "   - 若无一精确匹配：选最接近的 1 张，并在推理说明中告诉 image2 如何调整\n"
        "   输出格式（必须严格遵守）：\n"
        "   `SELECT_PRODUCT=P{编号}`\n"
        "   `MATCH_REASON=<匹配理由，简明 1-2 句>`\n"
        "   `ADJUST_HINT=<若无精确匹配，给 image2 的角度/形态调整指令；已匹配则填 无>`\n"
        "   `ORIGINAL_ANALYSIS=<原帧产品角度/形态的完整分析（2-3 句），供 image2 替换时参考>`\n\n"
        "用简明中文输出。禁止臆造，只描述图中实际可见的内容。"
    )

    messages = [
        {
            "role": "system",
            "content": (
                "你是工业级图像分析师，负责在产品替换前做精准的多图角度匹配。"
                "仔细观察原图与多张候选产品图，选最匹配的那张用于替换。"
                "禁止臆造，只描述图中实际可见内容。"
            ),
        },
        {
            "role": "user",
            "content": [{"type": "text", "text": analysis_prompt}]
            + [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{ob}"}}]
            + [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}"}} for b in prod_b64s],
        },
    ]

    try:
        # 多图匹配为加速流程：1 次失败立即降级用第1张，不死等重试
        # （gpt_client 默认 max_attempts=5 会等 5+10+20+30+60=125s 才放弃）
        result = await _asyncio.wait_for(
            _gpt.chat_completion(
                messages,
                temperature=0.2,
                max_tokens=1500,
                max_attempts=2,  # 仅重试 1 次：504 时立即降级
                fallback_models=(),  # 禁用 fallback，避免多模型叠加重试
            ),
            timeout=120.0,  # 单次 LLM 调用最多等 120s
        )
        content = result["choices"][0]["message"]["content"].strip()
        print(f"  [多图匹配] LLM 分析完成，{len(content)} 字:\n{content[:400]}")

        # 解析 SELECT_PRODUCT=P{n}
        import re
        m = re.search(r"SELECT_PRODUCT\s*=\s*P?(\d+)", content, re.IGNORECASE)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(valid_paths):
                print(f"  [多图匹配] 选中 P{idx+1}: {valid_paths[idx].name}")
                return (valid_paths[idx], content)

        # 解析失败：降级用第1张
        print(f"  [多图匹配] 未解析出 SELECT_PRODUCT，降级用第1张")
        return (valid_paths[0], content)
    except _asyncio.TimeoutError:
        print(f"  [多图匹配] 超时(300s)降级，用第1张")
        return (valid_paths[0], "")
    except Exception as e:
        print(f"  [多图匹配] 失败降级: {e}")
        return (valid_paths[0], "")


def create_run_dir(base_output_dir: str) -> Path:
    """创建带时间戳的运行目录"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(base_output_dir) / f"run_{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def run_step1(video_path: str, product_name: str,
              product_image_path: str = None,
              api_key: str = None, output_dir: str = None,
              product_image_paths: list = None,
              allow_cross_segment: bool = False,
              target_duration: float = None,
              split_mode: str = "action_grained") -> dict:
    """Step1: MiMo-2.5 视频结构解析

    Args:
        target_duration: 目标时长（秒）。None=1:1 完整复刻；15/20/25/30=压缩重编
        split_mode: 分镜切分模式。
            "scene_cut" = 镜头转场切分（允许复合动作，固定机位视频易判为 1 shot）
            "action_grained" = 细颗粒度动作切分（产品形态变化必拆，爆款复刻默认）
    """
    print("\n" + "=" * 60)
    print("Step1: MiMo-2.5 视频结构解析" + ("（跨段重组模式）" if allow_cross_segment else ""))
    print("=" * 60)
    result = step1_mimo_parse.run(
        video_path=video_path,
        product_name=product_name,
        product_image_path=product_image_path,
        api_key=api_key,
        output_dir=output_dir,
        product_image_paths=product_image_paths,
        allow_cross_segment=allow_cross_segment,
        target_duration=target_duration,
        split_mode=split_mode,
    )
    print(f"  → 解析完成: {len(result['storyboard'])} 个Shot")
    print(f"  → 时间轴: {[t['time'] for t in result['timeline']]}")
    return result


def run_step2(video_path: str, timeline: list, output_dir: str) -> dict:
    """Step2: FFmpeg 抽帧 + 分镜拼图"""
    print("\n" + "=" * 60)
    print("Step2: FFmpeg 抽帧 + 分镜拼图")
    print("=" * 60)
    result = step2_extract_frames.run(
        video_path=video_path,
        timeline=timeline,
        output_dir=output_dir,
    )
    print(f"  → 抽帧完成: {len(result['frames'])} 帧")
    print(f"  → 拼图: {result['storyboard'].get('path', 'N/A')}")
    return result


def run_step2_5(
    product_image_path: str = None,
    clothing_image_path: str = None,
    model_image_path: str = None,
    scene_image_path: str = None,
    output_dir: str = ".",
) -> dict:
    """
    Step2.5: 素材预处理（提取标准化白底参考图）
    2.1 产品图 → 三视图白底
    2.2 服装图 → 服装平铺图白底
    2.3 模特图 → 人物白底正面照
    2.4 场景图 → 无人物场景图
    """
    print("\n" + "=" * 60)
    print("Step2.5: 素材预处理（标准化白底参考图）")
    print("=" * 60)
    inputs = []
    if product_image_path:
        inputs.append("产品")
    if clothing_image_path:
        inputs.append("服装")
    if model_image_path:
        inputs.append("模特")
    if scene_image_path:
        inputs.append("场景")
    print(f"  → 待处理素材: {' + '.join(inputs) if inputs else '无'}")

    result = step2_preprocess_materials.run(
        product_image_path=product_image_path,
        clothing_image_path=clothing_image_path,
        model_image_path=model_image_path,
        scene_image_path=scene_image_path,
        output_dir=output_dir,
    )
    for key in ["product", "clothing", "model", "scene"]:
        if key in result and result[key].get("status") == "ok":
            print(f"  → {key} 标准化完成: {result[key]['path']}")
    return result


def run_step3(frames_dir: str, storyboard_json: list,
              product_standard_path: str = None,
              model_standard_path: str = None,
              scene_standard_path: str = None,
              output_dir: str = ".",
              clothing_standard_path: str = None,
              product_image_paths: list = None,
              target_duration: float = None) -> dict:
    """
    Step3: 产品替换（使用上传的原始参考图）

    Args:
        target_duration: 目标时长（秒）。作为时长约束第二道防线，
            Step3 校验 storyboard.json 总时长，未压缩则二次压缩每个 shot 时长。
    """
    print("\n" + "=" * 60)
    print("Step3: 产品替换（Image2）")
    print("=" * 60)
    refs = []
    if product_standard_path:
        refs.append(f"产品={product_standard_path}")
    if model_standard_path:
        refs.append(f"模特={model_standard_path}")
    if scene_standard_path:
        refs.append(f"场景={scene_standard_path}")
    if refs:
        print(f"  → 参考图: {' | '.join(refs)}")
    if target_duration:
        print(f"  → 时长约束第二道防线：目标 {target_duration}s")

    # 产品/服装二选一作为产品参考图传给 Step3
    product_ref = product_standard_path or clothing_standard_path

    result = step3_replace_product.run(
        frames_dir=frames_dir,
        storyboard_json=storyboard_json,
        product_image_path=product_ref,
        output_dir=output_dir,
        model_image_path=model_standard_path,
        scene_image_path=scene_standard_path,
        product_image_paths=product_image_paths,
        target_duration=target_duration,
    )
    ok = sum(1 for r in result["replaced"] if r["status"] == "ok")
    fallback = sum(1 for r in result["replaced"] if r["status"] == "fallback")
    if len(result["replaced"]) != len(storyboard_json):
        raise RuntimeError(
            f"Step3 Shot 数量不一致: 输入 {len(storyboard_json)}，输出 {len(result['replaced'])}"
        )
    print(f"  → 替换完成: {ok}/{len(result['replaced'])} 成功，{fallback} 个保留原帧")
    print(f"  → 拼图: {result['storyboard']}")
    if result["replaced"] and ok == 0:
        # 收集各 Shot 的具体错误信息，便于诊断 image2 服务端错误
        err_details = []
        for r in result["replaced"]:
            if r.get("status") == "fallback":
                err_details.append(f"Shot {r['shot']:02d}: {r.get('error', '未知错误')}")
        err_summary = "\n  ".join(err_details) if err_details else "无具体错误信息"
        raise RuntimeError(
            f"Step3 产品替换全部失败，已停止后续流程，避免使用未替换的原帧生成成片\n"
            f"  各 Shot 错误详情:\n  {err_summary}"
        )
    return result


def run_step4(shots_image_paths: list, storyboard_json: list,
              output_dir: str, product_ref: str = None,
              model_ref: str = None, video_type: str = None,
              feedback: str = None, override_video_type: str = None,
              product_refs: list = None,
              replaced_storyboard_path: str = None) -> dict:
    """Step4: 故事板海报生成（根据视频类型路由模板）

    Args:
        feedback: 审核修改意见（非空时作为强制段追加到 prompt，覆盖模板默认）
        override_video_type: 视频类型手动覆盖（"auto"/"voiceover"/"showcase"/"action"）
        product_refs: 多张产品图路径列表（可选，所有产品图全部入参给 Step4，
            与 product_ref 合并去重，主产品图置首位，辅助参考图依次追加）
        replaced_storyboard_path: step3/storyboard_replaced.jpg 路径（替换好产品的分镜拼图，
            作为 @图1 传给 image2，避免 step4 自拼低分辨率导致画面糊死）
    """
    print("\n" + "=" * 60)
    print("Step4: 故事板海报生成")
    print("=" * 60)
    if product_refs:
        print(f"  → 产品参考图: {len(product_refs)} 张（全部入参）")
    if replaced_storyboard_path:
        print(f"  → @图1 (step3 替换拼图): {replaced_storyboard_path}")
    result = step4_storyboard_poster.run(
        shots_image_paths=shots_image_paths,
        storyboard_json=storyboard_json,
        output_dir=output_dir,
        product_image_path=product_ref,
        model_image_path=model_ref,
        video_type=video_type,
        feedback=feedback,
        override_video_type=override_video_type,
        product_image_paths=product_refs,
        replaced_storyboard_path=replaced_storyboard_path,
    )
    print(f"  → 海报: {result['poster']}")
    if result.get("video_type"):
        _vt = result["video_type"]
        print(f"  → 视频类型: {_vt}（{'口播类' if _vt == 'voiceover' else '展示类' if _vt == 'showcase' else '动作类'}）")
    if result.get("params_file"):
        print(f"  → 中间参数: {result['params_file']}")
    return result


def run_step5(video_path: str,
              product_image_path: str = None,
              model_image_path: str = None,
              scene_image_path: str = None,
              storyboard_image_path: str = None,
              replaced_storyboard_image_path: str = None,
              original_frame_image_path: str = None,
              output_dir: str = ".",
              headless: bool = False,
              wait_for_result: bool = True,
              extra_images: list = None,
              storyboard_json: list = None,
              product_image_paths: list = None,
              duration: str = "15s",
              resolution: str = "720p",
              model_value: str = None) -> dict:
    """Step5: 提交到 weeksir.com 爆款复刻生成视频

    Args:
        duration: 视频时长（"15s" / "30s"）
        resolution: 视频分辨率（"720p" / "480p"）
        model_value: weeksir 模型值，None 时使用 step5 默认值（MODEL_NO_FACE_LOCK）
    """
    print("\n" + "=" * 60)
    print("Step5: 视频生成（weeksir.com 爆款复刻）")
    print("=" * 60)
    kwargs = dict(
        video_path=video_path,
        product_image_path=product_image_path,
        model_image_path=model_image_path,
        scene_image_path=scene_image_path,
        storyboard_image_path=storyboard_image_path,
        replaced_storyboard_image_path=replaced_storyboard_image_path,
        original_frame_image_path=original_frame_image_path,
        output_dir=output_dir,
        headless=headless,
        wait_for_result=wait_for_result,
        extra_images=extra_images,
        storyboard_json=storyboard_json,
        product_image_paths=product_image_paths,
        duration=duration,
        resolution=resolution,
    )
    if model_value is not None:
        kwargs["model_value"] = model_value
    result = step5_video_generate.run(**kwargs)
    print(f"  → 状态: {result['status']}")
    if result.get("video_url"):
        print(f"  → 视频: {result['video_url']}")
    return result


def _run_step5_h3(
    storyboard_image_path: str = None,
    replaced_storyboard_image_path: str = None,
    product_image_path: str = None,
    model_image_path: str = None,
    scene_image_path: str = None,
    extra_images: list = None,
    product_name: str = "爆款产品",
    output_dir: str = ".",
    duration: float = 15.0,
    workflow_id: str = "",  # 兼容旧参数名（实际为工作流ID）
):
    """Step5 (H3 模式): 通过 RunningHub ComfyUI 工作流生成视频"""
    print("\n" + "=" * 60)
    print("Step5 (H3): Minimax H3 视频生成（RunningHub ComfyUI）")
    print("=" * 60)
    result = step5_h3_generate.run(
        storyboard_image_path=storyboard_image_path,
        replaced_storyboard_image_path=replaced_storyboard_image_path,
        product_image_path=product_image_path,
        model_image_path=model_image_path,
        scene_image_path=scene_image_path,
        extra_images=extra_images,
        product_name=product_name,
        output_dir=output_dir,
        duration=duration,
        workflow_id=workflow_id,
    )
    print(f"  → 状态: {result['status']}")
    if result.get("local_video"):
        print(f"  → 本地视频: {result['local_video']}")
    return result


def run_step5_multi_shot(
    mode: str = "replica",
    video_path: str = None,
    timeline: list = None,
    storyboard_json: list = None,
    product_image_paths: list = None,
    product_name: str = "",
    output_dir: str = ".",
    aspect_ratio: str = "9:16",
    key_frame_paths: list = None,
    segment_duration: int = 5,
    progress_callback=None,
) -> dict:
    """Step5 多镜头生成（复刻模式 / 原创模式）

    复刻模式：切边界帧 → 替换产品 → H3 双帧生成 → 拼接
    原创模式：使用已有 key_frame_paths → H3 双帧生成 → 拼接

    Args:
        mode: "replica" 或 "original"
        video_path: 原视频路径（复刻模式必填）
        timeline: 时间轴（复刻模式必填）
        storyboard_json: 分镜 JSON
        product_image_paths: 产品参考图列表
        product_name: 产品名称
        output_dir: 输出目录
        aspect_ratio: 画面比例
        key_frame_paths: 关键帧路径列表（原创模式必填）
        segment_duration: 每片段时长（原创模式，5/10/15）
        progress_callback: 进度回调
    """
    if mode == "replica":
        return step5_multi_shot.run_replica(
            video_path=video_path,
            timeline=timeline,
            storyboard_json=storyboard_json,
            product_image_paths=product_image_paths,
            output_dir=output_dir,
            product_name=product_name,
            aspect_ratio=aspect_ratio,
            progress_callback=progress_callback,
        )
    elif mode == "original":
        return step5_multi_shot.run_original(
            key_frame_paths=key_frame_paths,
            storyboard_json=storyboard_json,
            product_name=product_name,
            output_dir=output_dir,
            aspect_ratio=aspect_ratio,
            segment_duration=segment_duration,
            progress_callback=progress_callback,
        )
    else:
        raise ValueError(f"未知模式: {mode}，支持 'replica' 或 'original'")


def run_full_pipeline(
    video_path: str,
    product_name: str,
    product_image_path: str = None,
    clothing_image_path: str = None,
    model_image_path: str = None,
    scene_image_path: str = None,
    mimo_api_key: str = None,
    base_output_dir: str = "outputs",
    skip_steps: set = None,
    product_image_paths: list = None,
    video_model: str = "seedance",
    h3_workflow_id: str = "",
    h3_duration: float = 15.0,
) -> dict:
    """
    运行完整爆款复刻管线

    Args:
        video_path: 视频文件路径
        product_name: 用户指定的精确新产品名称
        product_image_path: 产品图路径（可选，原图直传Step3）
        product_image_paths: 多张产品图路径列表（可选，优先使用，传给MiMo分镜解析）
        clothing_image_path: 服装图路径（可选，原图直传Step3）
        model_image_path: 模特参考图路径（可选，原图直传Step3）
        scene_image_path: 场景参考图路径（可选，原图直传Step3）
        mimo_api_key: MiMo API Key
        base_output_dir: 输出根目录
        skip_steps: 跳过的步骤集合，如 {3, 4}
        video_model: Step5视频生成模型 "seedance"(默认 weeksir.com) | "h3"(RunningHub ComfyUI)
        h3_workflow_id: H3模式的工作流ID（留空用config配置）
        h3_duration: H3模式的视频时长（秒）

    Returns:
        完整管线结果
    """
    skip_steps = skip_steps or set()
    if not product_name or not product_name.strip():
        raise ValueError("必须提供精确的新产品名称")
    run_dir = create_run_dir(base_output_dir)
    print(f"运行目录: {run_dir}")

    # 多张产品图：合并单张和多张参数
    if product_image_paths is None:
        product_image_paths = []
    if product_image_path and product_image_path not in product_image_paths:
        product_image_paths.insert(0, product_image_path)
    if product_image_paths:
        print(f"产品参考图: {len(product_image_paths)} 张")

    has_materials = any([product_image_path, clothing_image_path, model_image_path, scene_image_path]) or bool(product_image_paths)

    # ── Step1: MiMo 视频解析 ──
    s1_dir = run_dir / "step1"
    step1_result = run_step1(
        video_path, product_name, product_image_path or clothing_image_path,
        mimo_api_key, str(s1_dir),
        product_image_paths=product_image_paths or None,
    )
    storyboard = step1_result["storyboard"]
    timeline = step1_result["timeline"]

    # ── Step2: 抽帧 + 拼图 ──
    if 2 in skip_steps:
        print("\n⏭ Step2: 已跳过")
        step2_result = None
    else:
        s2_dir = run_dir / "step2"
        step2_result = run_step2(video_path, timeline, str(s2_dir))

    # ── Step2.5: 素材预处理（产品图原图直传，服装/模特/场景做白底标准化） ──
    _has_other_materials = any([clothing_image_path, model_image_path, scene_image_path])
    if 2.5 in skip_steps or not _has_other_materials:
        print("\n⏭ Step2.5: 已跳过（无服装/模特/场景图，或手动跳过）")
        step25_result = None
        _cloth_std = None
        _model_std = None
        _scene_std = None
    else:
        s25_dir = run_dir / "step2_5"
        # 产品图不传给 Step2.5（原图直传），只标准化服装/模特/场景
        step25_result = run_step2_5(
            product_image_path=None,
            clothing_image_path=clothing_image_path,
            model_image_path=model_image_path,
            scene_image_path=scene_image_path,
            output_dir=str(s25_dir),
        )
        # 取标准化路径（失败时回退到原图）
        def _pick_std(key, orig):
            if not orig:
                return None
            r = step25_result.get(key) if step25_result else None
            if r and r.get("path") and Path(r["path"]).exists():
                return r["path"]
            return orig
        _cloth_std = _pick_std("clothing", clothing_image_path)
        _model_std = _pick_std("model", model_image_path)
        _scene_std = _pick_std("scene", scene_image_path)

    # ── Step3: 产品替换 ──
    # 产品图原图直传（不预处理），服装/模特/场景用 Step2.5 标准化结果
    _prod_ref = product_image_path or (product_image_paths[0] if product_image_paths else None)
    _cloth_ref = _cloth_std
    _model_ref = _model_std
    _scene_ref = _scene_std

    has_ref = any([_prod_ref, _cloth_ref, _model_ref, _scene_ref])

    if 3 in skip_steps or not has_ref:
        print("\n⏭ Step3: 已跳过（无参考图或手动跳过）")
        step3_result = None
    elif not step2_result:
        print("\n✗ Step3: 依赖 Step2 结果，无法执行")
        step3_result = None
    else:
        s3_dir = run_dir / "step3"
        frames_dir = str(Path(step2_result["output_dir"]) / "frames")
        step3_result = run_step3(
            frames_dir=frames_dir,
            storyboard_json=storyboard,
            product_standard_path=_prod_ref,
            clothing_standard_path=_cloth_ref,
            model_standard_path=_model_ref,
            scene_standard_path=_scene_ref,
            output_dir=str(s3_dir),
            product_image_paths=product_image_paths,
        )

    # ── Step4: 故事板海报 ──
    if 4 in skip_steps:
        print("\n⏭ Step4: 已跳过")
        step4_result = None
    else:
        if step3_result:
            shots_dir = Path(step3_result["output_dir"])
        elif step2_result:
            shots_dir = Path(step2_result["output_dir"]) / "frames"
        else:
            shots_dir = None

        if not shots_dir:
            print("\n✗ Step4: 无可用的 shot 图片，无法执行")
            step4_result = None
        else:
            shot_paths = sorted(shots_dir.glob("shot_*.png")) + sorted(shots_dir.glob("shot_*.jpg"))
            if not shot_paths:
                print("\n✗ Step4: 未找到 shot 图片")
                step4_result = None
            else:
                s4_dir = run_dir / "step4"
                # step3/storyboard_replaced.jpg 作为 step4 的 @图1（替换好产品的分镜拼图）
                # step3/storyboard_replaced.json 作为 step4 的分镜 JSON（LLM 重写后与新产品图一致）
                _replaced_storyboard_for_step4 = None
                _storyboard_for_step4 = storyboard  # 默认用原 JSON
                if step3_result and step3_result.get("storyboard"):
                    _replaced_path = Path(step3_result["storyboard"])
                    if _replaced_path.exists():
                        _replaced_storyboard_for_step4 = str(_replaced_path)
                    # 用 step3 LLM 重写后的 JSON（与替换后画面一致）
                    if step3_result.get("storyboard_json_replaced"):
                        _storyboard_for_step4 = step3_result["storyboard_json_replaced"]
                step4_result = run_step4(
                    [str(p) for p in shot_paths], _storyboard_for_step4, str(s4_dir),
                    product_ref=_prod_ref, model_ref=_model_ref,
                    product_refs=product_image_paths or None,
                    replaced_storyboard_path=_replaced_storyboard_for_step4,
                )

    # ── Step5: 视频生成 ──
    if 5 in skip_steps:
        print("\n⏭ Step5: 已跳过")
        step5_result = None
    else:
        s5_dir = run_dir / "step5"
        # Step5 参考图传参（严格遵守存储规范，禁止传中间产物）
        # @故事板 ← step4/final_storyboard.jpg（Step4 重绘海报，指导分镜构图）
        _storyboard_ref = None
        if step4_result and step4_result.get("poster"):
            _storyboard_path = Path(step4_result["poster"])
            if _storyboard_path.exists():
                _storyboard_ref = str(_storyboard_path)
                print(f"  → @故事板 (Step4 海报): {_storyboard_ref}")

        # @替换分镜图 ← step3/storyboard_replaced.jpg（Step3 替换拼图，产品一致性权威）
        _replaced_storyboard_ref = None
        if step3_result and step3_result.get("storyboard"):
            _replaced_storyboard_path = Path(step3_result["storyboard"])
            if _replaced_storyboard_path.exists():
                _replaced_storyboard_ref = str(_replaced_storyboard_path)
                print(f"  → @替换分镜图 (Step3 替换拼图): {_replaced_storyboard_ref}")

        # @原视频分镜图 ← 不再上传（按用户要求：不要把原视频的关键帧作为 RPA 的输入图片）
        # 原因：原视频关键帧可能含字幕/水印/旧产品，传给视频生成模型会污染输出
        _original_frame_ref = None

        # 多张产品图：通过 product_image_paths 整体传入，由 Step5 按顺序上传到 @图片2~N 位置
        # （主产品图 _prod_ref 也包括在 product_image_paths 中，避免重复）
        _all_product_paths = []
        if product_image_paths:
            for p in product_image_paths:
                if p and Path(p).exists():
                    _all_product_paths.append(p)
            if _all_product_paths:
                print(f"  → 产品参考图（@图片2~N）: {len(_all_product_paths)} 张")

        step5_result = run_step5(
            video_path=video_path,
            product_image_path=_prod_ref or _cloth_ref,
            model_image_path=_model_ref,
            scene_image_path=_scene_ref,
            storyboard_image_path=_storyboard_ref,
            replaced_storyboard_image_path=_replaced_storyboard_ref,
            original_frame_image_path=_original_frame_ref,
            output_dir=str(s5_dir),
            headless=False,
            wait_for_result=True,
            storyboard_json=storyboard,
            product_image_paths=_all_product_paths or None,
        ) if video_model != "h3" else _run_step5_h3(
            storyboard_image_path=_storyboard_ref,
            replaced_storyboard_image_path=_replaced_storyboard_ref,
            product_image_path=_prod_ref or _cloth_ref,
            model_image_path=_model_ref,
            scene_image_path=_scene_ref,
            extra_images=_extra_imgs or None,
            product_name=product_name,
            output_dir=str(s5_dir),
            duration=h3_duration,
            workflow_id=h3_workflow_id,
        )

    # ── 汇总 ──
    summary = {
        "run_dir": str(run_dir),
        "video": video_path,
        "product_image": product_image_path,
        "clothing_image": clothing_image_path,
        "model_image": model_image_path,
        "scene_image": scene_image_path,
        "shot_count": len(storyboard),
        "step1": {"storyboard": str(s1_dir / "storyboard.json"), "timeline": str(s1_dir / "timeline.json")},
        "step2": {"storyboard": step2_result["storyboard"].get("path") if step2_result else None},
        "step2_5": {k: v.get("path") if isinstance(v, dict) and v.get("status") in ("ok", "fallback") else None
                     for k, v in (step25_result or {}).items() if k != "output_dir"},
        "step3": {"storyboard": step3_result["storyboard"] if step3_result else None,
                  "replaced": step3_result.get("replaced") if step3_result else None},
        "step4": {"poster": step4_result["poster"] if step4_result else None},
        "step5": {"status": step5_result["status"] if step5_result else None,
                  "video_url": step5_result.get("video_url") if step5_result else None,
                  "local_video": step5_result.get("local_video") if step5_result else None},
    }
    with open(run_dir / "pipeline_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print("管线执行完毕")
    print("=" * 60)
    print(f"运行目录: {run_dir}")
    print(f"Shot数: {len(storyboard)}")
    if step5_result and step5_result.get("local_video"):
        print(f"最终视频（本地）: {step5_result['local_video']}")
    elif step5_result and step5_result.get("video_url"):
        print(f"最终视频（URL）: {step5_result['video_url']}")
    elif step4_result:
        print(f"最终海报: {step4_result['poster']}")
    elif step3_result:
        print(f"替换拼图: {step3_result['storyboard']}")
    elif step2_result:
        print(f"分镜拼图: {step2_result['storyboard'].get('path')}")

    return summary


def main():
    parser = argparse.ArgumentParser(description="爆款复刻工作流")
    parser.add_argument("--video", required=True, help="视频文件路径")
    parser.add_argument("--product-name", required=True, help="精确的新产品名称")
    parser.add_argument("--product", default=None, help="产品图路径（可选）")
    parser.add_argument("--clothing", default=None, help="服装图路径（可选）")
    parser.add_argument("--model", default=None, help="模特参考图路径（可选）")
    parser.add_argument("--scene", default=None, help="场景参考图路径（可选）")
    parser.add_argument("--api-key", default=None, help="MiMo API Key")
    parser.add_argument("--output-dir", default="outputs", help="输出根目录")
    parser.add_argument("--skip", nargs="*", type=float, default=[], help="跳过的步骤，如 --skip 2.5 3 4")
    parser.add_argument("--video-model", default="seedance", choices=["seedance", "h3"], help="Step5视频生成模型: seedance(默认 weeksir.com) | h3(RunningHub ComfyUI)")
    parser.add_argument("--h3-workflow-id", default="", help="H3模式工作流ID（留空用config配置）")
    parser.add_argument("--h3-duration", type=float, default=9.0, help="H3模式视频时长（秒）")
    args = parser.parse_args()

    run_full_pipeline(
        video_path=args.video,
        product_name=args.product_name,
        product_image_path=args.product,
        clothing_image_path=args.clothing,
        model_image_path=args.model,
        scene_image_path=args.scene,
        mimo_api_key=args.api_key,
        base_output_dir=args.output_dir,
        skip_steps=set(args.skip),
        video_model=args.video_model,
        h3_workflow_id=args.h3_workflow_id,
        h3_duration=args.h3_duration,
    )


if __name__ == "__main__":
    main()


# ============================================================================
# 分阶段执行模式（支持人工审核）
# ============================================================================
# 流程：
#   Phase 1: Step1 + Step2 + Step3  → 暂停 → 审核分镜替换图
#   Phase 2: Step4                  → 暂停 → 审核故事板海报
#   Phase 3: Step5                  → 完成 → 生成视频
# 中间状态保存在 {run_dir}/_phase_state.json，便于跨进程恢复


PHASE_STATE_FILE = "_phase_state.json"


def _save_phase_state(run_dir, state: dict):
    """保存分阶段执行中间状态"""
    state_file = Path(run_dir) / PHASE_STATE_FILE
    state_file.write_text(json.dumps(state, ensure_ascii=False, default=str, indent=2), encoding="utf-8")


def load_phase_state(run_dir) -> dict:
    """加载中间状态。返回空 dict 表示无状态"""
    state_file = Path(run_dir) / PHASE_STATE_FILE
    if not state_file.exists():
        return {}
    return json.loads(state_file.read_text(encoding="utf-8"))


def _collect_review_images(run_dir: Path, phase: str) -> dict:
    """收集当前阶段可供审核的图片"""
    review = {"phase": phase, "storyboards": [], "shots": [], "poster": None, "original_shots": [], "reference_images": []}

    # 所有阶段都收集用户上传的参考图（产品图/服装图/模特图/场景图）
    try:
        _sf = run_dir / "_phase_state.json"
        if _sf.exists():
            import json as _json
            _sd = _json.loads(_sf.read_text(encoding="utf-8"))
            _ref = []
            # 产品图（多张）
            for p in (_sd.get("product_image_paths") or []):
                if p and Path(p).exists() and str(p) not in [r.get("path") for r in _ref]:
                    _ref.append({"label": f"产品图 {Path(p).name}", "path": str(p), "kind": "product"})
            # 单张产品图（向后兼容）
            _p1 = _sd.get("product_image_path")
            if _p1 and Path(_p1).exists() and str(_p1) not in [r.get("path") for r in _ref]:
                _ref.append({"label": f"产品图 {Path(_p1).name}", "path": str(_p1), "kind": "product"})
            # 服装图
            _c = _sd.get("clothing_image_path")
            if _c and Path(_c).exists():
                _ref.append({"label": f"服装图 {Path(_c).name}", "path": str(_c), "kind": "clothing"})
            # 模特图
            _m = _sd.get("model_image_path")
            if _m and Path(_m).exists():
                _ref.append({"label": f"模特图 {Path(_m).name}", "path": str(_m), "kind": "model"})
            # 场景图
            _sc = _sd.get("scene_image_path")
            if _sc and Path(_sc).exists():
                _ref.append({"label": f"场景图 {Path(_sc).name}", "path": str(_sc), "kind": "scene"})
            review["reference_images"] = _ref
    except Exception:
        pass

    if phase in ("after_step2", "after_step3", "after_step4"):
        # Step2 输出：原始抽帧分镜拼图（作为对比/审核基线）
        s2_dir = run_dir / "step2"
        if s2_dir.exists():
            sb2 = s2_dir / "storyboard.jpg"
            if not sb2.exists():
                sb2 = next(s2_dir.glob("storyboard*.png"), None) or next(s2_dir.glob("storyboard*.jpg"), None)
            if sb2 and sb2.exists():
                review["storyboards"].append({
                    "label": "Step2 原始抽帧分镜拼图",
                    "path": str(sb2),
                })
            # after_step2 阶段也把抽帧单帧纳入 shots，便于前端逐张审核
            if phase == "after_step2":
                for p in sorted(s2_dir.glob("frames/shot_*.jpg")) + sorted(s2_dir.glob("frames/shot_*.png")):
                    review["shots"].append({"label": p.stem, "path": str(p)})

    if phase in ("after_step3", "after_step4"):
        # Step3 输出：替换后的分镜拼图 + 单帧
        s3_dir = run_dir / "step3"
        if s3_dir.exists():
            # 严格查找 storyboard_replaced.jpg（Step3 唯一输出文件名）
            sb = s3_dir / "storyboard_replaced.jpg"
            if not sb.exists():
                # 兜底：兼容旧数据可能存在的其他命名（不主动生成 storyboard.jpg）
                sb = next(s3_dir.glob("storyboard*.jpg"), None) or next(s3_dir.glob("storyboard*.png"), None)
            if sb and sb.exists():
                review["storyboards"].append({
                    "label": "Step3 产品替换后分镜拼图",
                    "path": str(sb),
                })
            for p in sorted(s3_dir.glob("shot_*.png")) + sorted(s3_dir.glob("shot_*.jpg")):
                review["shots"].append({"label": p.stem, "path": str(p)})

        # 收集 Step2 原始抽帧（用于 Step3 审核界面对比"原帧 vs 替换后"）
        s2_frames_dir = run_dir / "step2" / "frames"
        if s2_frames_dir.exists():
            for p in sorted(s2_frames_dir.glob("shot_*.jpg")) + sorted(s2_frames_dir.glob("shot_*.png")):
                review["original_shots"].append({"label": p.stem, "path": str(p)})

    if phase == "after_step4":
        # Step4 输出：故事板海报（优先 final_storyboard.jpg，不用中间产物 shot_reference.jpg）
        s4_dir = run_dir / "step4"
        if s4_dir.exists():
            poster = s4_dir / "final_storyboard.jpg"
            if not poster.exists():
                # 兜底：找其他 jpg/png，但排除 shot_reference.jpg（中间产物）
                poster = next(
                    (p for p in s4_dir.glob("*.jpg") if p.name != "shot_reference.jpg"),
                    None,
                ) or next(
                    (p for p in s4_dir.glob("*.png") if p.name != "shot_reference.png"),
                    None,
                )
            if poster and poster.exists():
                review["poster"] = {"label": "Step4 故事板海报", "path": str(poster)}

            # 中间参数文件（feedback/video_type/模板/image_roles/完整 prompt）
            params_file = s4_dir / "step4_params.json"
            if params_file.exists():
                review["step4_params"] = {"label": "Step4 中间参数", "path": str(params_file)}

    return review


def run_phase_1(
    video_path: str,
    product_name: str,
    product_image_path: str = None,
    clothing_image_path: str = None,
    model_image_path: str = None,
    scene_image_path: str = None,
    mimo_api_key: str = None,
    base_output_dir: str = "outputs",
    product_image_paths: list = None,
    video_model: str = "seedance",
    h3_duration: float = 15.0,
    duration: str = "15s",
    task_id: str = "",
) -> dict:
    """Phase 1: Step1 + Step2 + Step3，完成后暂停等待审核分镜图

    Args:
        duration: 视频时长（"15s"/"20s"/"25s"/"30s"），≥20s 时需 video_model="seedance_25"

    Returns:
        {
            "phase": "after_step3",
            "run_dir": str,
            "review": {...},        # 待审图片
            "state": {...},         # 中间状态（已持久化）
        }
    """
    if not product_name or not product_name.strip():
        raise ValueError("必须提供精确的新产品名称")

    run_dir = create_run_dir(base_output_dir)
    print(f"[Phase1] 运行目录: {run_dir}")

    # 多张产品图合并
    if product_image_paths is None:
        product_image_paths = []
    if product_image_path and product_image_path not in product_image_paths:
        product_image_paths.insert(0, product_image_path)

    # 先保存基础状态。后续每个 Step 成功后立即覆盖，确保失败时可断点恢复。
    state = {
        "phase": "before_step1",
        "run_dir": str(run_dir),
        "task_id": task_id,
        "video_path": video_path,
        "product_name": product_name,
        "product_image_path": product_image_path,
        "clothing_image_path": clothing_image_path,
        "model_image_path": model_image_path,
        "scene_image_path": scene_image_path,
        "product_image_paths": product_image_paths,
        "mimo_api_key": mimo_api_key,
        "video_model": video_model,
        "h3_duration": h3_duration,
        "duration": duration,
    }
    _save_phase_state(run_dir, state)

    # ── Step1 ──
    # 解析目标时长（duration="15s" → 15.0；"1:1" 或空 → None 完整复刻）
    _target_dur = None
    if duration and duration.endswith("s"):
        try:
            _target_dur = float(duration[:-1])
        except ValueError:
            pass
    s1_dir = run_dir / "step1"
    step1_result = run_step1(
        video_path, product_name, product_image_path or clothing_image_path,
        mimo_api_key, str(s1_dir),
        product_image_paths=product_image_paths or None,
        target_duration=_target_dur,
    )
    storyboard = step1_result["storyboard"]
    timeline = step1_result["timeline"]
    state.update({
        "phase": "after_step1",
        "storyboard": storyboard,
        "timeline": timeline,
        "step1_result": {"storyboard_json": str(s1_dir / "storyboard.json")},
    })
    _save_phase_state(run_dir, state)

    # ── Step2 ──
    s2_dir = run_dir / "step2"
    step2_result = run_step2(video_path, timeline, str(s2_dir))
    state.update({
        "phase": "after_step2",
        "step2_result": {
            "storyboard": step2_result["storyboard"].get("path") if step2_result else None,
            "output_dir": step2_result.get("output_dir") if step2_result else None,
            "frames": step2_result.get("frames") if step2_result else None,
        },
    })
    _save_phase_state(run_dir, state)

    # ── Step3 ──
    _prod_ref = product_image_path or (product_image_paths[0] if product_image_paths else None)
    _cloth_ref = clothing_image_path
    _model_ref = model_image_path
    _scene_ref = scene_image_path
    has_ref = any([_prod_ref, _cloth_ref, _model_ref, _scene_ref])

    step3_result = None
    if has_ref and step2_result:
        s3_dir = run_dir / "step3"
        frames_dir = str(Path(step2_result["output_dir"]) / "frames")
        step3_result = run_step3(
            frames_dir=frames_dir,
            storyboard_json=storyboard,
            product_standard_path=_prod_ref,
            clothing_standard_path=_cloth_ref,
            model_standard_path=_model_ref,
            scene_standard_path=_scene_ref,
            output_dir=str(s3_dir),
            product_image_paths=product_image_paths,
            target_duration=_target_dur,
        )
        # 时长二次压缩后回写 storyboard（state + step1/storyboard.json）
        _new_sb = step3_result.get("storyboard_json")
        if _new_sb and _target_dur:
            storyboard = _new_sb
            state["storyboard"] = storyboard
            _sb_path = state.get("step1_result", {}).get("storyboard_json")
            if _sb_path and Path(_sb_path).exists():
                import json as _json
                Path(_sb_path).write_text(_json.dumps(storyboard, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"  → 已回写压缩后 storyboard.json: {_sb_path}")

    # Step3 完成检查点
    state.update({
        "phase": "after_step3",
        "step3_result": {
            "storyboard": step3_result["storyboard"] if step3_result else None,
            "output_dir": step3_result.get("output_dir") if step3_result else None,
        },
    })
    _save_phase_state(run_dir, state)

    review = _collect_review_images(run_dir, "after_step3")
    print(f"[Phase1] 完成，等待审核分镜图。待审图片: 拼图{len(review['storyboards'])}张, 单帧{len(review['shots'])}张")
    return {"phase": "after_step3", "run_dir": str(run_dir), "review": review, "state": state}


def re_reason_step1_to_step3(run_dir: str) -> dict:
    """重新推理：基于已有状态的输入参数，用跨段重组模式重跑 Step1+2+3

    从 _phase_state.json 读取 video_path / product_name / 参考图等，
    清空旧 step1/step2/step3 目录后重新生成。
    """
    import shutil

    run_path = Path(run_dir)
    state = load_phase_state(run_path)
    if not state:
        raise ValueError(f"未找到运行目录 {run_dir} 的中间状态")

    video_path = state.get("video_path")
    product_name = state.get("product_name")
    mimo_api_key = state.get("mimo_api_key")
    product_image_path = state.get("product_image_path")
    clothing_image_path = state.get("clothing_image_path")
    model_image_path = state.get("model_image_path")
    scene_image_path = state.get("scene_image_path")
    product_image_paths = state.get("product_image_paths") or []
    task_id = state.get("task_id", "")

    if not video_path or not product_name:
        raise ValueError("中间状态缺少 video_path 或 product_name")

    print(f"[ReReason] 重新推理（跨段重组）: {run_path.name}")

    # 清空旧 step1/step2/step3 目录
    for step_name in ("step1", "step2", "step3"):
        old_dir = run_path / step_name
        if old_dir.exists():
            shutil.rmtree(old_dir)
            print(f"  → 清空 {step_name}/")

    # 重置 state 到 before_step1
    state["phase"] = "before_step1"
    state["allow_cross_segment"] = True
    _save_phase_state(run_path, state)

    # ── Step1（跨段重组模式）──
    # 解析目标时长（与 run_phase_1 一致：duration="15s" → 15.0；"1:1" 或空 → None 完整复刻）
    _target_dur = None
    _duration = state.get("duration")
    if _duration and _duration.endswith("s"):
        try:
            _target_dur = float(_duration[:-1])
        except ValueError:
            pass
    s1_dir = run_path / "step1"
    step1_result = run_step1(
        video_path, product_name, product_image_path or clothing_image_path,
        mimo_api_key, str(s1_dir),
        product_image_paths=product_image_paths or None,
        allow_cross_segment=True,
        target_duration=_target_dur,
    )
    storyboard = step1_result["storyboard"]
    timeline = step1_result["timeline"]
    state.update({
        "phase": "after_step1",
        "storyboard": storyboard,
        "timeline": timeline,
        "step1_result": {"storyboard_json": str(s1_dir / "storyboard.json")},
    })
    _save_phase_state(run_path, state)

    # ── Step2 ──
    s2_dir = run_path / "step2"
    step2_result = run_step2(video_path, timeline, str(s2_dir))
    state.update({
        "phase": "after_step2",
        "step2_result": {
            "storyboard": step2_result["storyboard"].get("path") if step2_result else None,
            "output_dir": step2_result.get("output_dir") if step2_result else None,
            "frames": step2_result.get("frames") if step2_result else None,
        },
    })
    _save_phase_state(run_path, state)

    # ── Step3 ──
    _prod_ref = product_image_path or (product_image_paths[0] if product_image_paths else None)
    _cloth_ref = clothing_image_path
    _model_ref = model_image_path
    _scene_ref = scene_image_path
    has_ref = any([_prod_ref, _cloth_ref, _model_ref, _scene_ref])

    step3_result = None
    if has_ref and step2_result:
        s3_dir = run_path / "step3"
        frames_dir = str(Path(step2_result["output_dir"]) / "frames")
        step3_result = run_step3(
            frames_dir=frames_dir,
            storyboard_json=storyboard,
            product_standard_path=_prod_ref,
            clothing_standard_path=_cloth_ref,
            model_standard_path=_model_ref,
            scene_standard_path=_scene_ref,
            output_dir=str(s3_dir),
            product_image_paths=product_image_paths,
            target_duration=_target_dur,
        )
        # 时长二次压缩后回写 storyboard（state + step1/storyboard.json）
        _new_sb = step3_result.get("storyboard_json")
        if _new_sb and _target_dur:
            storyboard = _new_sb
            state["storyboard"] = storyboard
            _sb_path = state.get("step1_result", {}).get("storyboard_json")
            if _sb_path and Path(_sb_path).exists():
                import json as _json
                Path(_sb_path).write_text(_json.dumps(storyboard, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"  → 已回写压缩后 storyboard.json: {_sb_path}")

    state.update({
        "phase": "after_step3",
        "step3_result": {
            "storyboard": step3_result["storyboard"] if step3_result else None,
            "output_dir": step3_result.get("output_dir") if step3_result else None,
        },
    })
    _save_phase_state(run_path, state)

    review = _collect_review_images(run_path, "after_step3")
    print(f"[ReReason] 完成。待审图片: 拼图{len(review['storyboards'])}张, 单帧{len(review['shots'])}张")
    return {"phase": "after_step3", "run_dir": str(run_path), "review": review, "state": state}


def run_phase_2(run_dir: str, feedback: str = None, override_video_type: str = None) -> dict:
    """Phase 2: Step4 故事板海报，完成后暂停等待审核

    从 run_dir 的 _phase_state.json 恢复状态

    Args:
        feedback: 审核修改意见（非空时作为强制段追加到 prompt）
        override_video_type: 视频类型手动覆盖（"auto"/"voiceover"/"showcase"/"action"）
    """
    state = load_phase_state(run_dir)
    if not state:
        raise ValueError(f"未找到运行目录 {run_dir} 的中间状态")
    if state.get("phase") != "after_step3":
        raise ValueError(f"当前阶段 {state.get('phase')} 不可执行 Phase 2（需为 after_step3）")

    print(f"[Phase2] 运行目录: {run_dir}")
    storyboard = state["storyboard"]

    # 确定 shot 图片目录
    step3_result = state.get("step3_result") or {}
    step2_result = state.get("step2_result") or {}
    if step3_result.get("output_dir"):
        shots_dir = Path(step3_result["output_dir"])
    elif step2_result.get("output_dir"):
        shots_dir = Path(step2_result["output_dir"]) / "frames"
    else:
        raise ValueError("无可用的 shot 图片目录")

    shot_paths = sorted(shots_dir.glob("shot_*.png")) + sorted(shots_dir.glob("shot_*.jpg"))
    if not shot_paths:
        raise ValueError(f"未在 {shots_dir} 找到 shot 图片")

    s4_dir = Path(run_dir) / "step4"
    # step3/storyboard_replaced.jpg 作为 step4 的 @图1（替换好产品的分镜拼图）
    # step3/storyboard_replaced.json 作为 step4 的分镜 JSON（LLM 重写后与新产品图一致）
    _replaced_storyboard_for_step4 = None
    _storyboard_for_step4 = storyboard  # 默认用原 JSON
    _step3_res = state.get("step3_result") or {}
    if _step3_res.get("storyboard"):
        _replaced_path = Path(_step3_res["storyboard"])
        if _replaced_path.exists():
            _replaced_storyboard_for_step4 = str(_replaced_path)
        # 用 step3 LLM 重写后的 JSON（与替换后画面一致）
        if _step3_res.get("storyboard_json_replaced"):
            _storyboard_for_step4 = _step3_res["storyboard_json_replaced"]
    step4_result = run_step4(
        [str(p) for p in shot_paths], _storyboard_for_step4, str(s4_dir),
        product_ref=state.get("product_image_path"),
        model_ref=state.get("model_image_path"),
        feedback=feedback,
        override_video_type=override_video_type,
        product_refs=state.get("product_image_paths") or None,
        replaced_storyboard_path=_replaced_storyboard_for_step4,
    )

    # 更新状态
    state["phase"] = "after_step4"
    state["step4_result"] = {"poster": step4_result["poster"]}
    if step4_result.get("params_file"):
        state["step4_result"]["params_file"] = step4_result["params_file"]
    if step4_result.get("video_type"):
        state["video_type"] = step4_result["video_type"]
    _save_phase_state(run_dir, state)

    review = _collect_review_images(Path(run_dir), "after_step4")
    print(f"[Phase2] 完成，等待审核故事板海报。海报: {review.get('poster')}")
    return {"phase": "after_step4", "run_dir": run_dir, "review": review, "state": state}


def run_phase_3(run_dir: str) -> dict:
    """Phase 3: Step5 视频生成，从状态恢复执行

    Returns:
        最终管线 summary
    """
    state = load_phase_state(run_dir)
    if not state:
        raise ValueError(f"未找到运行目录 {run_dir} 的中间状态")
    if state.get("phase") != "after_step4":
        raise ValueError(f"当前阶段 {state.get('phase')} 不可执行 Phase 3（需为 after_step4）")

    print(f"[Phase3] 运行目录: {run_dir}")
    s5_dir = Path(run_dir) / "step5"

    # 参考图
    _storyboard_ref = None
    step4_result = state.get("step4_result") or {}
    if step4_result.get("poster"):
        if Path(step4_result["poster"]).exists():
            _storyboard_ref = step4_result["poster"]

    _replaced_storyboard_ref = None
    step3_result = state.get("step3_result") or {}
    if step3_result.get("storyboard"):
        if Path(step3_result["storyboard"]).exists():
            _replaced_storyboard_ref = step3_result["storyboard"]

    # @原视频分镜图 ← 不再上传（按用户要求：不要把原视频的关键帧作为 RPA 的输入图片）
    # 原因：原视频关键帧可能含字幕/水印/旧产品，传给视频生成模型会污染输出
    _original_frame_ref = None

    _prod_ref = state.get("product_image_path")
    _cloth_ref = state.get("clothing_image_path")
    _model_ref = state.get("model_image_path")
    _scene_ref = state.get("scene_image_path")

    # 多张产品图：整体传入 Step5 的 @图片2~N 位置（主产品图也包含在内）
    _all_product_paths = []
    product_image_paths = state.get("product_image_paths") or []
    if product_image_paths:
        for p in product_image_paths:
            if p and Path(p).exists():
                _all_product_paths.append(p)

    # H3 模式仍使用 extra_images（H3 接口未改造）
    _extra_imgs = []
    if product_image_paths:
        for p in product_image_paths[1:]:
            if p and p != _prod_ref and Path(p).exists():
                _extra_imgs.append(p)

    video_model = state.get("video_model", "seedance")
    h3_duration = state.get("h3_duration", 15.0)

    if video_model == "h3":
        step5_result = _run_step5_h3(
            storyboard_image_path=_storyboard_ref,
            replaced_storyboard_image_path=_replaced_storyboard_ref,
            product_image_path=_prod_ref or _cloth_ref,
            model_image_path=_model_ref,
            scene_image_path=_scene_ref,
            extra_images=_extra_imgs or None,
            product_name=state.get("product_name", "爆款产品"),
            output_dir=str(s5_dir),
            duration=h3_duration,
        )
    else:
        # seedance 系列：根据 video_model 选择 weeksir 模型值/分辨率
        # duration 由前端选择（15s/20s/25s/30s），保存到 state
        # resolution 自动决定：seedance_25 → 480p（长视频保证速度），seedance → 720p
        # "seedance" → 720p/MODEL_NO_FACE_LOCK（默认 15s）
        # "seedance_25" → 480p/MODEL_SEEDANCE_2_5（支持 20s/25s/30s）
        if video_model == "seedance_25":
            from pipeline.step5_video_generate import MODEL_SEEDANCE_2_5
            _s5_model_value = MODEL_SEEDANCE_2_5
            _s5_duration = state.get("duration", "30s")
            _s5_resolution = "480p"
        else:
            _s5_model_value = None  # 用 step5 默认值 MODEL_NO_FACE_LOCK
            _s5_duration = state.get("duration", "15s")
            _s5_resolution = "720p"

        step5_result = run_step5(
            video_path=state["video_path"],
            product_image_path=_prod_ref or _cloth_ref,
            model_image_path=_model_ref,
            scene_image_path=_scene_ref,
            storyboard_image_path=_storyboard_ref,
            replaced_storyboard_image_path=_replaced_storyboard_ref,
            original_frame_image_path=_original_frame_ref,
            output_dir=str(s5_dir),
            headless=False,
            wait_for_result=True,
            storyboard_json=state.get("storyboard"),
            product_image_paths=_all_product_paths or None,
            duration=_s5_duration,
            resolution=_s5_resolution,
            model_value=_s5_model_value,
        )

    # 更新状态为完成
    state["phase"] = "completed"
    state["step5_result"] = {
        "status": step5_result.get("status"),
        "video_url": step5_result.get("video_url"),
        "local_video": step5_result.get("local_video"),
    }
    _save_phase_state(run_dir, state)

    summary = {
        "run_dir": run_dir,
        "shot_count": len(state.get("storyboard") or []),
        "step1": state.get("step1_result"),
        "step2": state.get("step2_result"),
        "step3": state.get("step3_result"),
        "step4": state.get("step4_result"),
        "step5": state["step5_result"],
    }
    with open(Path(run_dir) / "pipeline_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    print(f"[Phase3] 完成。视频: {step5_result.get('local_video') or step5_result.get('video_url')}")
    return {"phase": "completed", "run_dir": run_dir, "summary": summary}


def run_phase_3_multi(
    run_dir: str,
    mode: str = "replica",
    segment_duration: int = 5,
) -> dict:
    """Phase 3 多镜头：切边界帧 → 替换产品 → H3 双帧生成 → 拼接

    Args:
        run_dir: 运行目录
        mode: "replica"（复刻）或 "original"（原创）
        segment_duration: 原创模式每片段时长（5/10/15）

    Returns:
        最终管线 summary
    """
    state = load_phase_state(run_dir)
    if not state:
        raise ValueError(f"未找到运行目录 {run_dir} 的中间状态")
    if state.get("phase") != "after_step4":
        raise ValueError(f"当前阶段 {state.get('phase')} 不可执行 Phase 3 多镜头（需为 after_step4）")

    print(f"[Phase3-Multi] 运行目录: {run_dir} | 模式: {mode}")
    s5_dir = Path(run_dir) / "step5_multi"

    storyboard_json = state.get("storyboard") or []
    product_name = state.get("product_name", "爆款产品")
    product_image_paths = state.get("product_image_paths") or []
    if not product_image_paths and state.get("product_image_path"):
        product_image_paths = [state["product_image_path"]]

    # 视频比例：从 Step4 结果推断，默认 9:16
    aspect_ratio = "9:16"
    step4_result = state.get("step4_result") or {}
    if step4_result.get("video_type") == "voiceover":
        aspect_ratio = "16:9"

    if mode == "replica":
        # 复刻模式：需要原视频 + timeline
        video_path = state.get("video_path")
        timeline = state.get("timeline") or []
        if not video_path:
            raise ValueError("复刻模式需要 video_path")
        if not timeline:
            # 尝试从 step1/timeline.json 加载
            tl_path = Path(run_dir) / "step1" / "timeline.json"
            if tl_path.exists():
                timeline = json.loads(tl_path.read_text(encoding="utf-8"))
            else:
                raise ValueError("复刻模式需要 timeline")

        step5_result = run_step5_multi_shot(
            mode="replica",
            video_path=video_path,
            timeline=timeline,
            storyboard_json=storyboard_json,
            product_image_paths=product_image_paths,
            product_name=product_name,
            output_dir=str(s5_dir),
            aspect_ratio=aspect_ratio,
        )
    else:
        # 原创模式：使用 Step3 的产品替换图作为关键帧
        step3_dir = Path(run_dir) / "step3"
        key_frame_paths = sorted([
            str(p) for p in step3_dir.glob("shot_*.png")
        ] + [
            str(p) for p in step3_dir.glob("shot_*.jpg")
        ])
        if not key_frame_paths:
            raise ValueError("原创模式需要 Step3 的产品替换图，但 step3 目录为空")

        step5_result = run_step5_multi_shot(
            mode="original",
            key_frame_paths=key_frame_paths,
            storyboard_json=storyboard_json,
            product_name=product_name,
            output_dir=str(s5_dir),
            aspect_ratio=aspect_ratio,
            segment_duration=segment_duration,
        )

    # 更新状态为完成
    state["phase"] = "completed"
    state["step5_result"] = {
        "status": step5_result.get("status"),
        "mode": step5_result.get("mode"),
        "video": step5_result.get("video"),
        "n_segments": step5_result.get("n_segments"),
    }
    _save_phase_state(run_dir, state)

    summary = {
        "run_dir": run_dir,
        "shot_count": len(storyboard_json),
        "step1": state.get("step1_result"),
        "step2": state.get("step2_result"),
        "step3": state.get("step3_result"),
        "step4": state.get("step4_result"),
        "step5": state["step5_result"],
    }
    with open(Path(run_dir) / "pipeline_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    print(f"[Phase3-Multi] 完成。视频: {step5_result.get('video')}")
    return {"phase": "completed", "run_dir": run_dir, "summary": summary}


def run_phase_3_multi_director(
    run_dir: str,
    mode: str = "replica",
    segment_duration: int = 4,
) -> dict:
    """Phase 3 多镜头（Director 满血版）：切边界帧 → 替换产品 → Director 一次生成

    与 run_phase_3_multi 的差异：
      - segment_duration 支持 1-15 任意整数秒
      - 一次 Director 任务生成完整视频（无 ffmpeg 拼接，无转场）

    Args:
        run_dir: 运行目录
        mode: "replica"（复刻）或 "original"（原创）
        segment_duration: 每片段时长（1-15 任意整数秒，Director 支持）
    """
    state = load_phase_state(run_dir)
    if not state:
        raise ValueError(f"未找到运行目录 {run_dir} 的中间状态")
    if state.get("phase") != "after_step4":
        raise ValueError(f"当前阶段 {state.get('phase')} 不可执行 Phase 3 多镜头 Director 版（需为 after_step4）")

    print(f"[Phase3-Multi-Director] 运行目录: {run_dir} | 模式: {mode} | 每段: {segment_duration}s")
    s5_dir = Path(run_dir) / "step5_director"

    storyboard_json = state.get("storyboard") or []
    product_name = state.get("product_name", "爆款产品")
    product_image_paths = state.get("product_image_paths") or []
    if not product_image_paths and state.get("product_image_path"):
        product_image_paths = [state["product_image_path"]]

    aspect_ratio = "9:16"
    step4_result = state.get("step4_result") or {}
    if step4_result.get("video_type") == "voiceover":
        aspect_ratio = "16:9"

    if mode == "replica":
        video_path = state.get("video_path")
        timeline = state.get("timeline") or []
        if not video_path:
            raise ValueError("复刻模式需要 video_path")
        if not timeline:
            tl_path = Path(run_dir) / "step1" / "timeline.json"
            if tl_path.exists():
                timeline = json.loads(tl_path.read_text(encoding="utf-8"))
            else:
                raise ValueError("复刻模式需要 timeline")

        step5_result = step5_multi_shot.run_replica_director(
            video_path=video_path,
            timeline=timeline,
            storyboard_json=storyboard_json,
            product_image_paths=product_image_paths,
            product_name=product_name,
            output_dir=str(s5_dir),
            aspect_ratio=aspect_ratio,
            segment_duration=segment_duration,
        )
    else:
        step3_dir = Path(run_dir) / "step3"
        key_frame_paths = sorted([
            str(p) for p in step3_dir.glob("shot_*.png")
        ] + [
            str(p) for p in step3_dir.glob("shot_*.jpg")
        ])
        if not key_frame_paths:
            raise ValueError("原创模式需要 Step3 的产品替换图，但 step3 目录为空")

        step5_result = step5_multi_shot.run_original_director(
            key_frame_paths=key_frame_paths,
            storyboard_json=storyboard_json,
            product_name=product_name,
            output_dir=str(s5_dir),
            aspect_ratio=aspect_ratio,
            segment_duration=segment_duration,
        )

    state["phase"] = "completed"
    state["step5_result"] = {
        "status": step5_result.get("status"),
        "mode": step5_result.get("mode"),
        "video": step5_result.get("video"),
        "n_segments": step5_result.get("n_segments"),
        "engine": "director",
    }
    _save_phase_state(run_dir, state)

    summary = {
        "run_dir": run_dir,
        "shot_count": len(storyboard_json),
        "step1": state.get("step1_result"),
        "step2": state.get("step2_result"),
        "step3": state.get("step3_result"),
        "step4": state.get("step4_result"),
        "step5": state["step5_result"],
    }
    with open(Path(run_dir) / "pipeline_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    print(f"[Phase3-Multi-Director] 完成。视频: {step5_result.get('video')}")
    return {"phase": "completed", "run_dir": run_dir, "summary": summary}


def resume_phase_1(run_dir: str) -> dict:
    """从 Phase 1 最近成功检查点继续执行 Step1/Step2/Step3。"""
    state = load_phase_state(run_dir)
    if not state:
        raise ValueError(f"未找到运行目录 {run_dir} 的中间状态")

    run_path = Path(run_dir)
    phase = state.get("phase", "before_step1")
    print(f"[Resume Phase1] 从检查点 {phase} 恢复: {run_dir}")

    # Step1 未完成：只重做 Step1，并保存 after_step1 检查点。
    if phase == "before_step1":
        # 解析目标时长（与 run_phase_1 一致：duration="15s" → 15.0；"1:1" 或空 → None 完整复刻）
        _target_dur = None
        _duration = state.get("duration")
        if _duration and _duration.endswith("s"):
            try:
                _target_dur = float(_duration[:-1])
            except ValueError:
                pass
        s1_dir = run_path / "step1"
        step1_result = run_step1(
            state["video_path"],
            state["product_name"],
            state.get("product_image_path") or state.get("clothing_image_path"),
            state.get("mimo_api_key"),
            str(s1_dir),
            product_image_paths=state.get("product_image_paths") or None,
            target_duration=_target_dur,
        )
        state.update({
            "phase": "after_step1",
            "storyboard": step1_result["storyboard"],
            "timeline": step1_result["timeline"],
            "step1_result": {"storyboard_json": str(s1_dir / "storyboard.json")},
        })
        _save_phase_state(run_path, state)
        phase = "after_step1"

    # Step2 未完成：保留 Step1，重做 Step2。
    if phase == "after_step1":
        s2_dir = run_path / "step2"
        step2_result = run_step2(state["video_path"], state["timeline"], str(s2_dir))
        state.update({
            "phase": "after_step2",
            "step2_result": {
                "storyboard": step2_result["storyboard"].get("path"),
                "output_dir": step2_result.get("output_dir"),
                "frames": step2_result.get("frames"),
            },
        })
        _save_phase_state(run_path, state)
        phase = "after_step2"

    # Step3 未完成：保留 Step1/2，重做 Step3。
    if phase == "after_step2":
        step2_result = state.get("step2_result") or {}
        frames_dir = str(Path(step2_result["output_dir"]) / "frames") if step2_result.get("output_dir") else None
        if not frames_dir:
            raise ValueError("无法恢复 Step2 frames 目录")

        # 修复：旧状态文件 step2_result 可能缺失 frames 字段，从 frames 目录重建
        # 后续 Phase 3 (Step5) 需要 frames[0] 作为原视频分镜shot1
        if not step2_result.get("frames"):
            _frames_dir_path = Path(frames_dir)
            if _frames_dir_path.exists():
                _frame_files = sorted(_frames_dir_path.glob("shot_*.jpg"))
                if _frame_files:
                    step2_result["frames"] = [{"shot": i + 1, "path": str(p)} for i, p in enumerate(_frame_files)]
                    state["step2_result"] = step2_result
                    print(f"[Resume Phase1] 已从目录重建 step2_result.frames（{len(_frame_files)} 帧）")

        has_ref = any([
            state.get("product_image_path"),
            state.get("clothing_image_path"),
            state.get("model_image_path"),
            state.get("scene_image_path"),
        ])
        step3_result = None
        if has_ref:
            s3_dir = run_path / "step3"
            # Step3 时长约束第二道防线（解析目标时长）
            _target_dur = None
            _duration = state.get("duration")
            if _duration and _duration.endswith("s"):
                try:
                    _target_dur = float(_duration[:-1])
                except ValueError:
                    pass
            step3_result = run_step3(
                frames_dir=frames_dir,
                storyboard_json=state["storyboard"],
                product_standard_path=state.get("product_image_path"),
                clothing_standard_path=state.get("clothing_image_path"),
                model_standard_path=state.get("model_image_path"),
                scene_standard_path=state.get("scene_image_path"),
                output_dir=str(s3_dir),
                product_image_paths=state.get("product_image_paths"),
                target_duration=_target_dur,
            )
            # 时长二次压缩后回写 storyboard（state + step1/storyboard.json）
            _new_sb = step3_result.get("storyboard_json")
            if _new_sb and _target_dur:
                state["storyboard"] = _new_sb
                _sb_path = state.get("step1_result", {}).get("storyboard_json")
                if _sb_path and Path(_sb_path).exists():
                    import json as _json
                    Path(_sb_path).write_text(_json.dumps(_new_sb, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(f"  → 已回写压缩后 storyboard.json: {_sb_path}")
        state.update({
            "phase": "after_step3",
            "step3_result": {
                "storyboard": step3_result["storyboard"] if step3_result else None,
                "output_dir": step3_result.get("output_dir") if step3_result else None,
            },
        })
        _save_phase_state(run_path, state)

    if state.get("phase") != "after_step3":
        raise ValueError(f"无法识别的 Phase 1 检查点: {state.get('phase')}")

    review = _collect_review_images(run_path, "after_step3")
    print(f"[Resume Phase1] 完成，等待审核分镜图。待审图片: 拼图{len(review['storyboards'])}张, 单帧{len(review['shots'])}张")
    return {"phase": "after_step3", "run_dir": str(run_path), "review": review, "state": state}


def redo_phase_1(run_dir: str) -> dict:
    """驳回后重做 Phase 1（保留 run_dir，清空 step2/step3 重新执行）

    仅重做 Step3（产品替换），保留 Step1/Step2 结果
    """
    state = load_phase_state(run_dir)
    if not state:
        raise ValueError(f"未找到运行目录 {run_dir} 的中间状态")

    print(f"[Redo Phase1] 重做 Step3: {run_dir}")
    storyboard = state["storyboard"]

    # 清空旧 step3
    s3_dir = Path(run_dir) / "step3"
    if s3_dir.exists():
        import shutil
        shutil.rmtree(s3_dir)

    step2_result = state.get("step2_result") or {}
    frames_dir = str(Path(step2_result["output_dir"]) / "frames") if step2_result.get("output_dir") else None
    if not frames_dir:
        raise ValueError("无法恢复 Step2 frames 目录")

    # 修复：旧状态文件 step2_result 可能缺失 frames 字段，从 frames 目录重建
    # 后续 Phase 3 (Step5) 需要 frames[0] 作为原视频分镜shot1
    if not step2_result.get("frames"):
        _frames_dir_path = Path(frames_dir)
        if _frames_dir_path.exists():
            _frame_files = sorted(_frames_dir_path.glob("shot_*.jpg"))
            if _frame_files:
                step2_result["frames"] = [{"shot": i + 1, "path": str(p)} for i, p in enumerate(_frame_files)]
                state["step2_result"] = step2_result
                print(f"[Redo Phase1] 已从目录重建 step2_result.frames（{len(_frame_files)} 帧）")

    # Step3 时长约束第二道防线（解析目标时长）
    _target_dur = None
    _duration = state.get("duration")
    if _duration and _duration.endswith("s"):
        try:
            _target_dur = float(_duration[:-1])
        except ValueError:
            pass
    step3_result = run_step3(
        frames_dir=frames_dir,
        storyboard_json=storyboard,
        product_standard_path=state.get("product_image_path"),
        clothing_standard_path=state.get("clothing_image_path"),
        model_standard_path=state.get("model_image_path"),
        scene_standard_path=state.get("scene_image_path"),
        output_dir=str(s3_dir),
        product_image_paths=state.get("product_image_paths"),
        target_duration=_target_dur,
    )
    # 时长二次压缩后回写 storyboard（state + step1/storyboard.json）
    _new_sb = step3_result.get("storyboard_json")
    if _new_sb and _target_dur:
        storyboard = _new_sb
        state["storyboard"] = storyboard
        _sb_path = state.get("step1_result", {}).get("storyboard_json")
        if _sb_path and Path(_sb_path).exists():
            import json as _json
            Path(_sb_path).write_text(_json.dumps(storyboard, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  → 已回写压缩后 storyboard.json: {_sb_path}")

    state["phase"] = "after_step3"
    state["step3_result"] = {"storyboard": step3_result["storyboard"], "output_dir": step3_result.get("output_dir")}
    _save_phase_state(run_dir, state)

    review = _collect_review_images(Path(run_dir), "after_step3")
    return {"phase": "after_step3", "run_dir": run_dir, "review": review, "state": state}


def redo_single_shot(run_dir: str, shot_num: int, feedback: str = "", override_image_path: str = "") -> dict:
    """精准重做单个 Shot 的产品替换图（不影响其他 Shot）。

    Args:
        run_dir: 运行目录
        shot_num: Shot 编号（1-based，如 3 表示 Shot 03）
        feedback: 用户修改意见，追加到 Image2 prompt
        override_image_path: 可选，用户上传的替换参考图绝对路径。
            传入时只用此图作为产品参考图，不传时用原产品图。

    Returns:
        {"phase": "after_step3", "run_dir": ..., "review": ..., "redone_shot": shot_num}

    设计对齐（2026-08-24）：
    与 step3 主流程一致的"动态图位 + N 张产品图全传"策略，
    修复历史 bug：默认模式下 images 里只有原帧+模特+场景，产品图根本没塞进 images，
    image2 收到 prompt 说"第2张是产品图"但实际第2张是模特图 → 不替换。
    """
    from backend.services.image2_client import Image2Client
    from pipeline.step3_replace_product import (
        REPLACE_PROMPT, _resize_to_max, _img_to_b64, _download_image,
    )
    from PIL import Image

    state = load_phase_state(run_dir)
    if not state:
        raise ValueError(f"未找到运行目录 {run_dir} 的中间状态")

    storyboard = state["storyboard"]
    target = next((s for s in storyboard if s.get("shot") == shot_num), None)
    if not target:
        raise ValueError(f"Shot {shot_num} 不存在，分镜编号范围: {[s.get('shot') for s in storyboard]}")

    s3_dir = Path(run_dir) / "step3"
    step2_result = state.get("step2_result") or {}
    frames_dir = str(Path(step2_result["output_dir"]) / "frames") if step2_result.get("output_dir") else None
    if not frames_dir:
        raise ValueError("无法恢复 Step2 frames 目录")

    frame_path = Path(frames_dir) / f"shot_{shot_num:02d}.jpg"
    if not frame_path.exists():
        raise FileNotFoundError(f"原始帧不存在: {frame_path}")

    out_path = s3_dir / f"shot_{shot_num:02d}.png"

    print(f"\n[Redo Shot] 精准重做 Shot {shot_num:02d}（意见: {feedback or '无'}）")

    # ── 准备产品图列表（N 张全传给 image2） ──
    # override_image_path 优先：用户上传新图时只用此图作为产品图
    # 否则：合并 state.product_image_path + state.product_image_paths 去重
    if override_image_path and Path(override_image_path).exists():
        _all_product_paths = [Path(override_image_path)]
        print(f"[Redo Shot] 使用用户上传图作为唯一产品图: {override_image_path}")
    else:
        _all_product_paths = []
        _primary = state.get("product_image_path")
        if _primary and Path(_primary).exists():
            _all_product_paths.append(Path(_primary))
        for p in (state.get("product_image_paths") or []):
            if p and Path(p).exists() and Path(p).resolve() not in [x.resolve() for x in _all_product_paths]:
                _all_product_paths.append(Path(p))
        if not _all_product_paths:
            raise ValueError("无可用产品图（state.product_image_path 和 product_image_paths 都为空）")
        print(f"[Redo Shot] 产品图 {len(_all_product_paths)} 张全传 image2")

    # ── 构造 images（原帧 + N 张产品图 + 可选模特 + 可选场景） ──
    original_b64 = _resize_to_max(_img_to_b64(frame_path), max_px=1024)
    images = [f"data:image/jpeg;base64,{original_b64}"]
    product_b64s = []
    for prod_path in _all_product_paths:
        _b64 = _resize_to_max(_img_to_b64(prod_path), max_px=1024)
        product_b64s.append(_b64)
        images.append(f"data:image/jpeg;base64,{_b64}")
    model_b64 = None
    scene_b64 = None
    # override 模式：跳过模特/场景图，避免引入与用户意图不符的参考
    if not override_image_path:
        _model = state.get("model_image_path")
        if _model and Path(_model).exists():
            model_b64 = _resize_to_max(_img_to_b64(Path(_model)), max_px=768)
            images.append(f"data:image/jpeg;base64,{model_b64}")
        _scene = state.get("scene_image_path")
        if _scene and Path(_scene).exists():
            scene_b64 = _resize_to_max(_img_to_b64(Path(_scene)), max_px=768)
            images.append(f"data:image/jpeg;base64,{scene_b64}")

    # ── 动态生成"实际图位"段（与 step3 主流程一致） ──
    # 避免老 bug：写死的"第2张产品图、第3张模特图"在 N>1 时与实际 images 顺序冲突
    # 2026-09-18：模特/场景必须标成「强制替换源」。旧文案把原帧背景写成
    # 「1:1 锁定，不可变」、把场景图写成「仅参考」，导致审核点「修改」后
    # LLM/image2 继续保留原人物和原背景。
    _n_prod = len(product_b64s)
    _has_model = bool(model_b64)
    _has_scene = bool(scene_b64)
    _image_roles = [f"- 第1张图：原视频关键帧（构图/景别/人物占比/动作基准；产品/人物外观/背景按下方强制替换源替换）"]
    if _n_prod == 1:
        _image_roles.append(f"- 第2张图：⚠️强制替换源⚠️ 新产品图，必须将原帧中的目标产品强制替换为这张图中的产品")
    elif _n_prod > 1:
        _image_roles.append(
            f"- 第2~{1+_n_prod}张图：⚠️强制替换源⚠️ 同一新产品的 {_n_prod} 张多角度参考图，"
            f"必须强制替换原帧中的目标产品；image2 根据本 shot 视角从这 {_n_prod} 张中选角度最匹配的 1 张作为替换源"
        )
    _next_idx = 2 + _n_prod
    if _has_model:
        _image_roles.append(
            f"- 第{_next_idx}张图：⚠️强制替换源⚠️ 模特参考图，必须将原帧人物外观替换为这张图中的人物"
            f"（脸/发型/发色/五官/体型），动作和位置不变，严禁保留原人物长相"
        )
        _next_idx += 1
    if _has_scene:
        _image_roles.append(
            f"- 第{_next_idx}张图：⚠️强制替换源⚠️ 场景参考图，必须将原帧背景替换为这张图中的环境，"
            f"严禁保留原背景；人物与产品位置/构图不变"
        )

    scene_env = target.get("场景环境", {})
    lens = target.get("镜头语言", {})
    action = target.get("画面动作", {})
    desc = (
        f"场景:{scene_env.get('场景', target.get('场景',''))} "
        f"背景:{scene_env.get('背景细节','')} "
        f"光影:{scene_env.get('光影设定','')} "
        f"景别:{lens.get('景别', target.get('景别',''))} "
        f"画面:{action.get('画面内容', target.get('画面内容',''))} "
        f"产品动作:{action.get('产品动作', target.get('产品动作',''))} "
        f"构图:{lens.get('构图', target.get('构图',''))} "
        f"运镜:{lens.get('运镜', target.get('运镜',''))}"
    )
    # 净化描述（与 step3 一致）
    try:
        from pipeline.step3_replace_product import _sanitize_desc
        desc = _sanitize_desc(desc)
    except ImportError:
        pass

    prompt = REPLACE_PROMPT + f"""

====================
📋 实际图位（运行时动态生成，必须严格遵循）
====================
{chr(10).join(_image_roles)}

====================
🎯 本任务执行约束
====================
当前只生成 Shot {shot_num:02d} 的一张独立分镜图，不生成拼图，不合并其他 Shot。
当前分镜描述：{desc}

🚨 强制替换指令（最高优先级，不可违反）：
- 必须将原帧（第1张图）中的目标产品强制替换为上述"强制替换源"图中的新产品
- 严禁保留原帧中的旧产品外观/品牌/颜色/包装
- 严禁直接复制原帧而不做替换
- 新产品的真实轮廓、长宽厚比例、包装尺寸、颜色、材质、表面纹理、凹凸纹路、切面纹路、印刷图案、Logo、文字位置、边缘和数量必须逐项复制"强制替换源"图
- 存在的纹路绝对不可抹平、简化、重绘或臆造
- 产品在画面中的视觉尺寸必须继承第1张原始分镜中原产品的占画比例、位置、透视、朝向及遮挡关系，仅允许为适配新品真实长宽比做最小调整
- 输出画布只包含这一张 Shot 图，不在图内添加 Shot 编号、字幕、水印或说明文字"""
    if _has_model:
        prompt += (
            "\n- 必须将原帧人物替换为模特参考图中的人物外观，严禁输出原视频人物的脸/发型"
        )
    if _has_scene:
        prompt += (
            "\n- 必须将原帧背景替换为场景参考图中的环境，严禁输出原视频背景"
        )
    prompt += f"""

【用户审核修改意见 - 必须严格执行】
{feedback or '（无额外意见，按原描述生成）'}
"""

    async def _redo():
        # ── LLM 视觉分析原帧（产品图已全塞 images，无需 LLM 选图） ──
        # 复用 pipeline.replica_motion._analyze_keyframe_for_replacement（原帧分析）
        # 用第 1 张产品图作为辅助参考，分析重点仍是原帧
        final_prompt = prompt
        try:
            from pipeline.replica_motion import _analyze_keyframe_for_replacement
            _prod_local = _all_product_paths[0] if _all_product_paths else None
            if _prod_local and _prod_local.exists():
                print(f"[Redo Shot] LLM 视觉分析原帧 shot_{shot_num:02d}...")
                _fb_parts = []
                if feedback:
                    _fb_parts.append(feedback)
                if model_b64:
                    _fb_parts.append(
                        "用户已上传新模特图：必须替换原帧人物外观为新模特，不要保持原人物脸/发型/发色。"
                    )
                if scene_b64:
                    _fb_parts.append(
                        "用户已上传新场景图：必须替换原帧背景为新场景，不要保持原背景环境。"
                    )
                llm_analysis = await _analyze_keyframe_for_replacement(
                    frame_path, _prod_local, target,
                    force_inject_product=False,
                    product_name=state.get("product_name", ""),
                    user_feedback="\n".join(_fb_parts),
                )
                if llm_analysis:
                    print(f"[Redo Shot] LLM 分析完成，{len(llm_analysis)} 字", flush=True)
                    try:
                        from pipeline.step3_replace_product import _sanitize_desc as _san
                        llm_analysis = _san(llm_analysis)
                    except Exception:
                        pass
                    _override = ""
                    if model_b64 or scene_b64:
                        _override = (
                            "\n====================\n"
                            "🚨 人物/场景强制替换（用户已上传参考图，优先级高于「保持原人物/原背景」）\n"
                            "====================\n"
                        )
                        if model_b64:
                            _override += (
                                "- 必须把原帧人物外观换成模特参考图中的人物，严禁保留原视频人物\n"
                            )
                        if scene_b64:
                            _override += (
                                "- 必须把原帧背景换成场景参考图中的环境，严禁保留原视频背景\n"
                            )
                        _override += "- 构图、景别、人物位置/动作、产品位置仍 1:1 锁定\n\n"
                    final_prompt = (
                        "====================\n"
                        "本分镜识别结果（LLM 基于原图实际识别产品位置/动作；人物外观与背景以用户参考图为准）\n"
                        "====================\n"
                        f"{llm_analysis}\n"
                        + _override
                        + prompt
                    )
                else:
                    print(f"[Redo Shot] LLM 分析返回空，降级用 storyboard prompt", flush=True)
            else:
                print(f"[Redo Shot] 未找到产品图，跳过 LLM 分析", flush=True)
        except Exception as e:
            print(f"[Redo Shot] LLM 分析失败降级: {e}", flush=True)

        # ── 插桩：dump final_prompt + images 元数据（与 step3 主流程一致） ──
        try:
            _dump_path = s3_dir / f"shot_{shot_num:02d}_prompt.txt"
            _img_meta = []
            for _i, _img in enumerate(images):
                _sz_kb = len(_img) // 1024 if _img else 0
                _img_type = "data_url" if _img and _img.startswith("data:") else "other"
                _img_meta.append(f"  images[{_i}]: {_img_type}, ~{_sz_kb}KB")
            _dump_text = (
                f"=== Redo Shot {shot_num:02d} final_prompt ===\n\n"
                f"{final_prompt}\n\n"
                f"=== Images 元数据（共 {len(images)} 张）===\n"
                + "\n".join(_img_meta) + "\n\n"
                f"=== 调用参数 ===\n"
                f"size: 1024x1792\n"
                f"n: 1\n"
                f"feedback: {feedback or '(无)'}\n"
                f"override_image_path: {override_image_path or '(无)'}\n"
            )
            _dump_path.write_text(_dump_text, encoding="utf-8")
        except Exception as _e:
            print(f"[Redo Shot] dump prompt 失败（不影响主流程）: {_e}", flush=True)

        from pipeline.step3_replace_product import _image2_edit_with_content_retry
        client = Image2Client()
        result = await _image2_edit_with_content_retry(
            client, final_prompt, images, "1024x1792", shot_num,
            dump_dir=s3_dir,
        )
        data_list = result.get("data", [])
        if not data_list:
            raise RuntimeError("Image2 返回 data 为空")
        url = data_list[0].get("url", "")
        b64 = data_list[0].get("b64_json", "")
        if url:
            _download_image(url, out_path)
        elif b64:
            out_path.write_bytes(base64.b64decode(b64))
        else:
            raise RuntimeError("Image2 未返回图片 URL 或 base64")
        with Image.open(out_path) as generated:
            generated.verify()

    # 失败必须抛异常让前端感知（不再静默复制原帧，否则前端按钮显示"重做成功"但实际未替换）
    # 3 次重试后仍失败 → 抛 RuntimeError，由 app.py:replica_redo_shot 捕获并切到 redo_failed 状态
    import asyncio as _aio
    try:
        _aio.run(_redo())
        print(f"[Redo Shot] Shot {shot_num:02d} 重做完成: {out_path}")
    except Exception as e:
        # 失败时清理可能存在的残缺输出文件，避免后续 Step 误用
        try:
            if out_path.exists():
                out_path.unlink()
        except Exception:
            pass
        print(f"[Redo Shot] Shot {shot_num:02d} 重做失败: {e}")
        raise RuntimeError(
            f"Shot {shot_num:02d} 重做失败（image2 已重试 3 次）: {e}"
        ) from e

    # 重新拼图（覆盖 storyboard_replaced.jpg）
    from pipeline.step3_replace_product import _merge_storyboard
    storyboard_path = s3_dir / "storyboard_replaced.jpg"
    if not storyboard_path.exists():
        # 兼容旧命名
        sb_old = s3_dir / "storyboard.jpg"
        storyboard_path = sb_old if sb_old.exists() else s3_dir / "storyboard_replaced.jpg"
    _merge_storyboard(s3_dir, storyboard, storyboard_path)

    # 更新 state（step3_result 保持，只是换了某张图）
    state["phase"] = "after_step3"
    _save_phase_state(run_dir, state)

    review = _collect_review_images(Path(run_dir), "after_step3")
    return {"phase": "after_step3", "run_dir": run_dir, "review": review, "redone_shot": shot_num}


def redo_phase_2(run_dir: str, feedback: str = None, override_video_type: str = None) -> dict:
    """驳回后重做 Phase 2（清空 step4 重新生成故事板海报）

    Args:
        feedback: 审核修改意见（非空时作为强制段追加到 prompt）
        override_video_type: 视频类型手动覆盖（"auto"/"voiceover"/"showcase"/"action"）
    """
    state = load_phase_state(run_dir)
    if not state:
        raise ValueError(f"未找到运行目录 {run_dir} 的中间状态")

    print(f"[Redo Phase2] 重做 Step4: {run_dir}")
    storyboard = state["storyboard"]

    s4_dir = Path(run_dir) / "step4"
    if s4_dir.exists():
        import shutil
        shutil.rmtree(s4_dir)

    step3_result = state.get("step3_result") or {}
    step2_result = state.get("step2_result") or {}
    if step3_result.get("output_dir"):
        shots_dir = Path(step3_result["output_dir"])
    elif step2_result.get("output_dir"):
        shots_dir = Path(step2_result["output_dir"]) / "frames"
    else:
        raise ValueError("无可用的 shot 图片目录")

    shot_paths = sorted(shots_dir.glob("shot_*.png")) + sorted(shots_dir.glob("shot_*.jpg"))
    if not shot_paths:
        raise ValueError(f"未在 {shots_dir} 找到 shot 图片")

    # step3/storyboard_replaced.jpg 作为 step4 的 @图1（替换好产品的分镜拼图）
    # step3/storyboard_replaced.json 作为 step4 的分镜 JSON（LLM 重写后与新产品图一致）
    _replaced_storyboard_for_step4 = None
    _storyboard_for_step4 = storyboard  # 默认用原 JSON
    _step3_res = state.get("step3_result") or {}
    if _step3_res.get("storyboard"):
        _replaced_path = Path(_step3_res["storyboard"])
        if _replaced_path.exists():
            _replaced_storyboard_for_step4 = str(_replaced_path)
        # 用 step3 LLM 重写后的 JSON（与替换后画面一致）
        if _step3_res.get("storyboard_json_replaced"):
            _storyboard_for_step4 = _step3_res["storyboard_json_replaced"]
    step4_result = run_step4(
        [str(p) for p in shot_paths], _storyboard_for_step4, str(s4_dir),
        product_ref=state.get("product_image_path"),
        model_ref=state.get("model_image_path"),
        feedback=feedback,
        override_video_type=override_video_type,
        product_refs=state.get("product_image_paths") or None,
        replaced_storyboard_path=_replaced_storyboard_for_step4,
    )

    state["phase"] = "after_step4"
    state["step4_result"] = {"poster": step4_result["poster"]}
    if step4_result.get("params_file"):
        state["step4_result"]["params_file"] = step4_result["params_file"]
    if step4_result.get("video_type"):
        state["video_type"] = step4_result["video_type"]
    _save_phase_state(run_dir, state)

    review = _collect_review_images(Path(run_dir), "after_step4")
    return {"phase": "after_step4", "run_dir": run_dir, "review": review, "state": state}


# ═══════════════════════════════════════════════════════════════════════
# 原创管线（首尾帧无限拼接 - 独立管线，不走 phased 流程）
# 产品图+文字需求 → GPT5.5 脚本 → Image2 关键帧 → H3 双帧拼接
# ═══════════════════════════════════════════════════════════════════════

def run_original_pipeline(
    product_image_paths: list,
    product_name: str,
    selling_points: str = "",
    audience: str = "",
    scene_requirement: str = "",
    aspect_ratio: str = "9:16",
    segment_duration: int = 5,
    total_duration: int = 30,
    base_output_dir: str = "outputs",
    progress_callback=None,
) -> dict:
    """原创管线独立入口：一气呵成，无审核点

    流程：
      Step1: GPT5.5 推理脚本 → storyboard.json
      Step3: Image2 生成关键帧 → N 张关键帧
      Step5: H3 双帧并发生成 → ffmpeg 拼接 → 最终视频

    Args:
        product_image_paths: 产品图路径列表（1-N 张，必传）
        product_name: 产品名称（必填）
        selling_points: 卖点/创意描述（可选）
        audience: 目标人群（可选）
        scene_requirement: 场景要求（可选）
        aspect_ratio: 画面比例（默认 9:16）
        segment_duration: 每段时长（5/10/15，默认 5）
        total_duration: 总时长目标（默认 30）
        base_output_dir: 输出根目录
        progress_callback: 进度回调 callback(status, idx, msg)

    Returns:
        {
            "status": "success",
            "run_dir": str,
            "video": str,
            "storyboard": [...],
            "keyframes": [...],
            "segments": [...],
        }
    """
    from pipeline import step1_original_script
    from pipeline import step3_original_keyframes

    print("\n" + "#" * 60)
    print("# 原创管线（首尾帧无限拼接）")
    print("#" * 60)

    # 创建 run_dir
    run_dir = create_run_dir(base_output_dir)
    print(f"运行目录: {run_dir}")

    # 初始化独立 state（不复用 phased 的 after_step4）
    state = {
        "phase": "original_before_script",
        "mode": "original",
        "product_image_paths": product_image_paths,
        "product_name": product_name,
        "selling_points": selling_points,
        "audience": audience,
        "scene_requirement": scene_requirement,
        "aspect_ratio": aspect_ratio,
        "segment_duration": segment_duration,
        "total_duration": total_duration,
        "started_at": datetime.now().isoformat(),
    }
    _save_phase_state(str(run_dir), state)

    try:
        # ── Step1: GPT5.5 推理脚本 ──
        if progress_callback:
            progress_callback("script_generating", 0, "Step1: GPT5.5 推理分镜脚本...")
        print(f"\n[Step1] GPT5.5 推理脚本...")
        step1_dir = run_dir / "step1_original"
        step1_result = step1_original_script.generate_original_script(
            product_image_paths=product_image_paths,
            product_name=product_name,
            output_dir=str(step1_dir),
            selling_points=selling_points,
            audience=audience,
            scene_requirement=scene_requirement,
            aspect_ratio=aspect_ratio,
            segment_duration=segment_duration,
            total_duration=total_duration,
            progress_callback=progress_callback,
        )
        storyboard = step1_result["storyboard"]
        timeline = step1_result["timeline"]
        state["storyboard"] = storyboard
        state["timeline"] = timeline
        state["phase"] = "original_after_script"
        _save_phase_state(str(run_dir), state)
        print(f"  ✓ 脚本生成完成: {len(storyboard)} 个 shot")

        # ── Step3: Image2 生成关键帧 ──
        if progress_callback:
            progress_callback("keyframes_generating", 0, f"Step3: Image2 生成 {len(storyboard)} 张关键帧...")
        print(f"\n[Step3] Image2 生成关键帧...")
        step3_dir = run_dir / "step3_original"
        step3_result = step3_original_keyframes.generate_original_keyframes(
            storyboard_json=storyboard,
            product_image_paths=product_image_paths,
            output_dir=str(step3_dir),
            aspect_ratio=aspect_ratio,
            progress_callback=progress_callback,
        )
        keyframe_paths = step3_result["keyframes"]
        state["keyframes"] = keyframe_paths
        state["phase"] = "original_after_keyframes"
        _save_phase_state(str(run_dir), state)
        print(f"  ✓ 关键帧生成完成: {len(keyframe_paths)} 张")

        # ── Step5: H3 双帧并发生成 + ffmpeg 拼接 ──
        if progress_callback:
            progress_callback("video_generating", 0, f"Step5: H3 并发生成 {len(keyframe_paths)-1} 个片段...")
        print(f"\n[Step5] H3 双帧并发生成 + 拼接...")
        step5_dir = run_dir / "step5_multi"
        step5_result = step5_multi_shot.run_original(
            key_frame_paths=keyframe_paths,
            storyboard_json=storyboard,
            product_name=product_name,
            output_dir=str(step5_dir),
            aspect_ratio=aspect_ratio,
            segment_duration=segment_duration,
            progress_callback=progress_callback,
        )
        final_video = step5_result["video"]
        state["final_video"] = final_video
        state["segments"] = step5_result.get("segments", [])
        state["phase"] = "original_completed"
        state["completed_at"] = datetime.now().isoformat()
        _save_phase_state(str(run_dir), state)
        print(f"  ✓ 视频生成完成: {final_video}")

        if progress_callback:
            progress_callback("done", 0, f"原创管线完成: {final_video}")

        return {
            "status": "success",
            "run_dir": str(run_dir),
            "video": final_video,
            "storyboard": storyboard,
            "timeline": timeline,
            "keyframes": keyframe_paths,
            "segments": step5_result.get("segments", []),
            "n_segments": step5_result.get("n_segments", 0),
        }

    except Exception as e:
        # 失败时保存错误信息到 state 供排查
        state["phase"] = "original_failed"
        state["error"] = str(e)
        state["failed_at"] = datetime.now().isoformat()
        _save_phase_state(str(run_dir), state)
        print(f"\n✗ 原创管线失败: {e}")
        raise


def run_original_pipeline_director(
    product_image_paths: list,
    product_name: str,
    selling_points: str = "",
    audience: str = "",
    scene_requirement: str = "",
    aspect_ratio: str = "9:16",
    segment_duration: int = 4,
    total_duration: int = 30,
    base_output_dir: str = "outputs",
    progress_callback=None,
) -> dict:
    """原创管线独立入口（Director 满血版）：一气呵成，无审核点

    与 run_original_pipeline 的差异：
      - segment_duration 支持 1-15 任意整数秒（不再限制 5/10/15）
      - Step5 改用 H3 Director 一次任务生成完整视频（无 ffmpeg 拼接，无转场）

    流程：
      Step1: GPT5.5 推理脚本 → storyboard.json
      Step3: Image2 生成关键帧 → N 张关键帧
      Step5: H3 Director 一次任务 → 完整视频
    """
    from pipeline import step1_original_script
    from pipeline import step3_original_keyframes

    print("\n" + "#" * 60)
    print("# 原创管线（首尾帧无限拼接 · Director 满血版）")
    print("#" * 60)

    run_dir = create_run_dir(base_output_dir)
    print(f"运行目录: {run_dir}")

    state = {
        "phase": "original_director_before_script",
        "mode": "original_director",
        "product_image_paths": product_image_paths,
        "product_name": product_name,
        "selling_points": selling_points,
        "audience": audience,
        "scene_requirement": scene_requirement,
        "aspect_ratio": aspect_ratio,
        "segment_duration": segment_duration,
        "total_duration": total_duration,
        "started_at": datetime.now().isoformat(),
    }
    _save_phase_state(str(run_dir), state)

    try:
        # ── Step1: GPT5.5 推理脚本 ──
        if progress_callback:
            progress_callback("script_generating", 0, "Step1: GPT5.5 推理分镜脚本...")
        print(f"\n[Step1] GPT5.5 推理脚本...")
        step1_dir = run_dir / "step1_original"
        step1_result = step1_original_script.generate_original_script(
            product_image_paths=product_image_paths,
            product_name=product_name,
            output_dir=str(step1_dir),
            selling_points=selling_points,
            audience=audience,
            scene_requirement=scene_requirement,
            aspect_ratio=aspect_ratio,
            segment_duration=segment_duration,
            total_duration=total_duration,
            progress_callback=progress_callback,
        )
        storyboard = step1_result["storyboard"]
        timeline = step1_result["timeline"]
        state["storyboard"] = storyboard
        state["timeline"] = timeline
        state["phase"] = "original_director_after_script"
        _save_phase_state(str(run_dir), state)
        print(f"  ✓ 脚本生成完成: {len(storyboard)} 个 shot")

        # ── Step3: Image2 生成关键帧 ──
        if progress_callback:
            progress_callback("keyframes_generating", 0, f"Step3: Image2 生成 {len(storyboard)} 张关键帧...")
        print(f"\n[Step3] Image2 生成关键帧...")
        step3_dir = run_dir / "step3_original"
        step3_result = step3_original_keyframes.generate_original_keyframes(
            storyboard_json=storyboard,
            product_image_paths=product_image_paths,
            output_dir=str(step3_dir),
            aspect_ratio=aspect_ratio,
            progress_callback=progress_callback,
        )
        keyframe_paths = step3_result["keyframes"]
        # 混合模式：获取首帧/尾帧/gen_modes
        start_frames = step3_result.get("start_frames", keyframe_paths)
        end_frames = step3_result.get("end_frames", [None] * len(keyframe_paths))
        gen_modes = step3_result.get("gen_modes", ["fl2v"] * len(keyframe_paths))
        state["keyframes"] = keyframe_paths
        state["start_frames"] = start_frames
        state["end_frames"] = end_frames
        state["gen_modes"] = gen_modes
        state["phase"] = "original_director_after_keyframes"
        _save_phase_state(str(run_dir), state)
        fl2v_n = sum(1 for gm in gen_modes if gm == "fl2v")
        i2v_n = len(gen_modes) - fl2v_n
        print(f"  ✓ 关键帧生成完成: 首帧 {len(start_frames)} 张, 尾帧 {sum(1 for e in end_frames if e)} 张（混合模式: fl2v={fl2v_n}, i2v={i2v_n}）")

        # ── Step5: H3 Director 一次任务生成完整视频（混合模式） ──
        if progress_callback:
            progress_callback("video_generating", 0, f"Step5: Director 一次生成 {len(start_frames)} 段视频（混合模式）...")
        print(f"\n[Step5] H3 Director 一次生成（混合模式，无拼接）...")
        step5_dir = run_dir / "step5_director"
        step5_result = step5_multi_shot.run_original_director(
            key_frame_paths=start_frames,
            storyboard_json=storyboard,
            product_name=product_name,
            output_dir=str(step5_dir),
            aspect_ratio=aspect_ratio,
            segment_duration=segment_duration,
            progress_callback=progress_callback,
            end_frame_paths=end_frames,
            gen_modes=gen_modes,
        )
        final_video = step5_result["video"]
        state["final_video"] = final_video
        state["segments"] = step5_result.get("segments", [])
        state["phase"] = "original_director_completed"
        state["completed_at"] = datetime.now().isoformat()
        _save_phase_state(str(run_dir), state)
        print(f"  ✓ 视频生成完成: {final_video}")

        if progress_callback:
            progress_callback("done", 0, f"原创管线（Director）完成: {final_video}")

        return {
            "status": "success",
            "run_dir": str(run_dir),
            "video": final_video,
            "storyboard": storyboard,
            "timeline": timeline,
            "keyframes": keyframe_paths,
            "segments": step5_result.get("segments", []),
            "n_segments": step5_result.get("n_segments", 0),
            "engine": "director",
        }

    except Exception as e:
        state["phase"] = "original_director_failed"
        state["error"] = str(e)
        state["failed_at"] = datetime.now().isoformat()
        _save_phase_state(str(run_dir), state)
        print(f"\n✗ 原创管线（Director）失败: {e}")
        raise
