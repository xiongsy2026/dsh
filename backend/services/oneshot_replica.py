"""
One-Shot Replica：爆款视频 → MiMo 解析 → LLM 反推 → Seedance Prompt

流程：
  Step1: MiMo 解析爆款视频（画面+音频），输出 storyboard JSON
  Step2: LLM（grok 优先 / gpt 备用）看 storyboard + 用户选的时长/比例
         → 反推 Seedance metadata + compiledPrompt

输出：
  {
    "storyboard": [...],            # MiMo 解析结果
    "timeline": [...],              # MiMo 时间轴
    "metadata": {...},             # LLM 推理的结构化字段
    "compiled_prompt": str,         # 最终 seedancePrompt 文本
    "mimo_raw": str,                # MiMo 原始输出
    "engine": "grok"/"gpt",
    "duration": "5s", "aspect_ratio": "9:16",
  }
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import settings


# ── 端点解析（从 settings.ONESHOT_SEEDANCE_ENDPOINTS 解析出版本 → 端点映射）──
def _parse_seedance_endpoints() -> Dict[str, str]:
    """解析 '2.0=rhart-video/sparkvideo-2.0,2.5=rhart-video/sparkvideo-2.5' 形式。"""
    out: Dict[str, str] = {}
    raw = getattr(settings, "ONESHOT_SEEDANCE_ENDPOINTS", "") or ""
    for pair in raw.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            k, v = k.strip(), v.strip()
            if k and v:
                out[k] = v
    # 兜底
    if not out:
        out = {
            "2.0": "rhart-video/sparkvideo-2.0",
            "2.0-fast": "rhart-video/sparkvideo-2.0-fast",
            "2.0-mini": "rhart-video/sparkvideo-2.0-mini",
            "2.5": "rhart-video/sparkvideo-2.5",
        }
    return out


def resolve_seedance_endpoint(version: str, has_image: bool) -> str:
    """根据版本与是否传产品图返回完整端点名。

    Args:
        version: 版本代号，如 "2.0" / "2.5"
        has_image: 是否传入产品图（True 走 i2v，False 走 t2v）

    Returns:
        完整端点名，如 "rhart-video/sparkvideo-2.0/image-to-video"
    """
    table = _parse_seedance_endpoints()
    base = table.get(version) or table.get(
        getattr(settings, "ONESHOT_SEEDANCE_DEFAULT_VERSION", "2.0")
    ) or "rhart-video/sparkvideo-2.0"
    suffix = "image-to-video" if has_image else "text-to-video"
    return f"{base}/{suffix}"


# ── 加载规范文本（与 prompt_compiler 共享缓存）──
def _load_spec() -> str:
    """读取 prompt-compiler.md 规范全文。"""
    try:
        from backend.services.prompt_compiler import _load_spec as _spec
        return _spec()
    except Exception:
        spec_path = Path(__file__).parent / "prompt-compiler.md"
        return spec_path.read_text(encoding="utf-8") if spec_path.exists() else ""


# ── Step1: MiMo 解析视频 ──
async def step1_mimo_parse(
    video_path: str,
    output_dir: str,
    product_name: str = "（待识别产品）",
    product_image_paths: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """调 pipeline.step1_mimo_parse.run 解析爆款视频。

    MiMo 同时识别画面（镜头/动作/产品）和音频（口播/声音语气/音效），
    输出 storyboard JSON + timeline 时间轴。

    Args:
        video_path: 爆款视频本地路径
        output_dir: 工作目录
        product_name: 用户指定的产品名（爆款复刻场景可填"待识别"）
        product_image_paths: 产品图路径列表（可选，仅用于辅助识别）

    Returns:
        {"storyboard": [...], "timeline": [...], "raw_content": str,
         "output_dir": str, "video_duration": float}
    """
    from pipeline import step1_mimo_parse

    # 1:1 模式（按原视频完整时长解析，让 LLM 自行压缩到目标时长）
    fps = float(getattr(settings, "ONESHOT_MIMO_FPS", 4.0))
    media_res = getattr(settings, "ONESHOT_MIMO_MEDIA_RESOLUTION", "max")
    split_mode = getattr(settings, "ONESHOT_MIMO_SPLIT_MODE", "scene_cut")

    # 在线程中跑同步函数
    result = await asyncio.to_thread(
        step1_mimo_parse.run,
        video_path=video_path,
        product_name=product_name,
        product_image_path=None,
        product_image_paths=product_image_paths,
        output_dir=output_dir,
        fps=fps,
        media_resolution=media_res,
        split_mode=split_mode,
        target_duration=None,  # 1:1 模式
    )

    # 估算视频时长
    video_duration = 0.0
    try:
        if result.get("timeline"):
            for t in result["timeline"]:
                end = t.get("end", 0) if isinstance(t, dict) else 0
                if isinstance(end, (int, float)) and end > video_duration:
                    video_duration = float(end)
    except Exception:
        pass

    return {
        "storyboard": result.get("storyboard", []),
        "timeline": result.get("timeline", []),
        "raw_content": result.get("raw_content", ""),
        "output_dir": result.get("output_dir", output_dir),
        "video_duration": video_duration,
    }


# ── Step2: LLM 反推 Seedance Prompt ──
_REVERSE_SYSTEM_TEMPLATE = """你是 Seedance {version} 视频 Prompt 反推引擎。

你将收到一段爆款视频的 MiMo 结构化解析结果（storyboard JSON），它客观记录了原视频的：
- 画面（构图、人物动作、产品动作、镜头语言）
- 音频（口播逐字、声音语气、音效）
- 时间轴（每个 Shot 的起止时间）

你的任务：基于该解析结果，反推一个可生成等价爆款效果的新视频 Seedance Prompt。
不要 1:1 复刻原视频，而是学习原视频的：
- 分镜节奏（Hook-痛点-展示-证明-CTA）
- 镜头语言（景别切换、运镜、转场）
- 旁白情绪（语速、语调、呼吸感）
- 产品动作（包装、特写、手持、展示）

然后按下方规范生成结构化字段 + compiledPrompt。

# 输出参数约束
- 目标时长：{duration} 秒
- 比例：{aspect_ratio}
- 分辨率：{resolution}
- 分镜数量：严格按目标时长分配（约 {shots_count} 个 Shot，每 Shot ≈ {duration}/{shots_count:.1f}s）

# 工作规范（必须严格遵守）

{spec}

# 反推原则

1. **学习结构而非具体内容**：从原视频分镜学到"开头特写→痛点放大→产品展示→效果证明→CTA"
   这种结构节奏，应用到新脚本中。但具体的口播词、产品名、卖点要重新组织。

2. **保持原视频的镜头节奏**：如果原视频是"快速切镜 + 急促口播"，新脚本也要快节奏；
   如果原视频是"长镜头 + 平缓旁白"，新脚本也要慢节奏。

3. **保留原视频的视觉风格**：场景、光影、构图风格尽量贴近原视频，但产品/人物/字幕要按新脚本生成。

4. **不得 1:1 复制原视频字幕或口播**：原视频的口播是参考学习素材，不是要照搬的文案。
   要根据原视频的卖点逻辑和情绪曲线，重新撰写新的口播文案。

5. **产品信息来源**：{product_hint}

# 输出格式（严格 JSON，不要 Markdown 代码块，不要任何额外文字）

{{
  "userSupplementPrompt": "",
  "productCategory": "从原视频识别的商品类型",
  "targetAudience": "目标人群（参考原视频人群画像）",
  "visualAttributes": "视觉特征",
  "sellingPoints": "核心卖点（学习原视频的卖点表达）",
  "purchaseReasons": "购买理由",
  "usageScenes": "使用场景",
  "conversionHook": "转化钩子",
  "primaryIntent": "用户真实目标",
  "sellingAngle": "成交角度",
  "emotionalTone": "情绪方向（参考原视频声音语气）",
  "visualStrategy": "画面策略（参考原视频镜头语言）",
  "defaultModelNeed": "需要默认模特（参考原视频人物出镜情况）",
  "primaryAudience": "核心人群",
  "secondaryAudiences": "次级人群",
  "buyerMindset": "当下心态",
  "purchaseMotivations": "购买动机",
  "purchaseBarriers": "购买阻力",
  "visualTriggers": "视觉触发",
  "voiceoverAngle": "口播角度",
  "routeName": "路线名称",
  "variationSeed": "反推-{orig_duration}s→{duration}s",
  "routeReasoning": "路线推理（说明本脚本如何学习原视频结构）",
  "visualWorld": "视觉世界",
  "differentiators": "差异点",
  "hookType": "Hook 类型（参考原视频开头分镜）",
  "hookObjective": "Hook 目标",
  "productReasoning": "产品推理",
  "firstFrame": "第一帧（参考原视频开头画面）",
  "firstAction": "第一动作",
  "audioBeat": "声音节拍（参考原视频声音语气）",
  "viewerReason": "留存理由",
  "textPolicy": "文字策略",
  "identityAnchors": "身份锚点",
  "riskPoints": "崩坏风险",
  "negativeConstraints": "负向约束",
  "continuityRules": "连续性规则",
  {shot_fields},
  "voiceoverTone": "旁白情绪（参考原视频声音语气.情绪）",
  "voiceoverRhythm": "旁白节奏（参考原视频声音语气.语速/停顿）",
  "voiceoverScript": "旁白文案（学习原视频口播风格，重新撰写）"
}}

其中 `shot_fields` 按目标时长动态生成：
- 5s → 3 个 shot（shot1=0-2s, shot2=2-4s, shot3=4-5s）
- 8s → 5 个 shot（shot1=0-2s, shot2=2-4s, shot3=4-6s, shot4=6-7s, shot5=7-8s）
- 10s → 5 个 shot（shot1=0-2s, shot2=2-4s, shot3=4-6s, shot4=6-8s, shot5=8-10s）
- 15s → 5 个 shot（shot1=0-3s, shot2=3-6s, shot3=6-9s, shot4=9-12s, shot5=12-15s）

每个 shot 描述新视频该时段的镜头画面、人物动作、产品动作。
"""


def _build_shots_fields(duration_sec: int) -> str:
    """根据目标时长生成 shot 字段名列表。"""
    templates = {
        5: [("shot1", "0-2s"), ("shot2", "2-4s"), ("shot3", "4-5s")],
        8: [("shot1", "0-2s"), ("shot2", "2-4s"), ("shot3", "4-6s"),
            ("shot4", "6-7s"), ("shot5", "7-8s")],
        10: [("shot1", "0-2s"), ("shot2", "2-4s"), ("shot3", "4-6s"),
             ("shot4", "6-8s"), ("shot5", "8-10s")],
        15: [("shot1", "0-3s"), ("shot2", "3-6s"), ("shot3", "6-9s"),
             ("shot4", "9-12s"), ("shot5", "12-15s")],
    }
    items = templates.get(duration_sec, templates[5])
    return ", ".join(f'"{n}": "镜头{n[-1]} ({t})：画面内容描述"' for n, t in items)


def _build_reverse_system_prompt(
    version: str,
    duration: str,
    aspect_ratio: str,
    resolution: str,
    orig_duration: float,
    has_product_image: bool,
) -> str:
    """构造 LLM 的 system prompt。"""
    duration_sec = int(float(duration.rstrip("s")) if duration else 5)
    shots_count = {5: 3, 8: 5, 10: 5, 15: 5}.get(duration_sec, 3)
    shot_fields = _build_shots_fields(duration_sec)
    spec = _load_spec()
    product_hint = (
        "用户已上传产品图，按产品图实际可见特征描述产品（颜色/图案/材质/包装）"
        if has_product_image
        else "未上传产品图，从原视频中识别产品类型，新脚本中产品描述要通用化（不绑定特定品牌）"
    )
    return _REVERSE_SYSTEM_TEMPLATE.format(
        version=version,
        duration=duration_sec,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        shots_count=shots_count,
        spec=spec,
        shot_fields=shot_fields,
        orig_duration=f"{orig_duration:.1f}",
        product_hint=product_hint,
    )


# ── 反推主入口 ──
async def reverse_engineer(
    video_path: str,
    output_dir: str,
    duration: str = "5",
    aspect_ratio: str = "9:16",
    resolution: str = "720p",
    version: str = "2.0",
    engine: str = "grok",
    product_image_paths: Optional[List[str]] = None,
    extra_hint: str = "",
) -> Dict[str, Any]:
    """爆款视频 → MiMo 解析 → LLM 反推 → Seedance Prompt。

    Args:
        video_path: 爆款视频本地路径
        output_dir: 工作目录（存中间结果）
        duration: 目标时长（秒字符串，如 "5"/"8"/"15"）
        aspect_ratio: 比例（9:16/16:9/1:1）
        resolution: 分辨率（720p/1080p/4k）
        version: Seedance 版本（2.0/2.0-fast/2.0-mini/2.5）
        engine: LLM 推理引擎（grok 优先，gpt 备用）
        product_image_paths: 产品图路径列表（可选，提升产品描述精度）
        extra_hint: 用户附加提示

    Returns:
        {
            "storyboard": [...], "timeline": [...],
            "metadata": {...}, "compiled_prompt": str,
            "mimo_raw": str, "engine": "grok"/"gpt",
            "duration": "5", "aspect_ratio": "9:16",
            "video_duration": float,
        }
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Step1: MiMo 解析 ──
    print(f"[OneShotReplica] Step1: MiMo 解析视频 {video_path}")
    mimo_result = await step1_mimo_parse(
        video_path=video_path,
        output_dir=str(out_dir / "step1_mimo"),
        product_name="（待识别产品）",
        product_image_paths=product_image_paths,
    )
    storyboard = mimo_result.get("storyboard", [])
    if not storyboard:
        raise RuntimeError("MiMo 解析失败：storyboard 为空")
    video_duration = mimo_result.get("video_duration", 0.0)
    print(f"  → MiMo 解析完成: {len(storyboard)} 个 Shot, 视频时长 {video_duration:.1f}s")

    # 持久化 storyboard
    (out_dir / "storyboard.json").write_text(
        json.dumps(storyboard, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ── Step2: LLM 反推 ──
    has_product_image = bool(product_image_paths)
    # engine 回退：grok 失败自动降级到 gpt
    engines_to_try = [engine]
    if engine == "grok" and "gpt" not in engines_to_try:
        engines_to_try.append("gpt")
    elif engine == "gpt" and "grok" not in engines_to_try:
        engines_to_try.append("grok")

    metadata: Optional[Dict[str, Any]] = None
    used_engine: Optional[str] = None
    last_err: Optional[Exception] = None

    for eng in engines_to_try:
        print(f"[OneShotReplica] Step2: LLM 反推（engine={eng}）")
        try:
            metadata = await _call_llm_reverse(
                storyboard=storyboard,
                duration=duration,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                version=version,
                orig_duration=video_duration,
                has_product_image=has_product_image,
                engine=eng,
                extra_hint=extra_hint,
                product_image_paths=product_image_paths or [],
            )
            used_engine = eng
            print(f"  → 反推成功（engine={eng}）")
            break
        except Exception as e:
            print(f"  → engine={eng} 失败: {e}")
            last_err = e
            continue

    if metadata is None:
        raise RuntimeError(
            f"所有 LLM engine 反推均失败（tried={engines_to_try}, last_err={last_err}）"
        )

    # ── 组装 compiled_prompt ──
    compiled_prompt = _assemble_replica_prompt(
        metadata=metadata,
        duration=duration,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
    )

    # 持久化 metadata + compiled
    (out_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "compiled_prompt.txt").write_text(compiled_prompt, encoding="utf-8")

    return {
        "storyboard": storyboard,
        "timeline": mimo_result.get("timeline", []),
        "mimo_raw": mimo_result.get("raw_content", ""),
        "metadata": metadata,
        "compiled_prompt": compiled_prompt,
        "engine": used_engine or engine,
        "duration": duration,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "version": version,
        "video_duration": video_duration,
        "output_dir": str(out_dir),
    }


# ── LLM 调用（带降级回退）──
async def _call_llm_reverse(
    storyboard: List[Dict[str, Any]],
    duration: str,
    aspect_ratio: str,
    resolution: str,
    version: str,
    orig_duration: float,
    has_product_image: bool,
    engine: str,
    extra_hint: str,
    product_image_paths: List[str],
) -> Dict[str, Any]:
    """调 LLM 反推，返回 metadata dict。

    复用 prompt_compiler._resolve_engine_config 和 _call_gpt。
    """
    from backend.services.prompt_compiler import (
        _resolve_engine_config, _call_gpt, _parse_json,
    )

    system_prompt = _build_reverse_system_prompt(
        version=version,
        duration=duration,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        orig_duration=orig_duration,
        has_product_image=has_product_image,
    )

    # user content: storyboard JSON + 可选产品图
    user_content: list = [{
        "type": "text",
        "text": (
            f"# 原视频 MiMo 解析结果（storyboard JSON，共 {len(storyboard)} 个 Shot）\n\n"
            f"```json\n{json.dumps(storyboard, ensure_ascii=False, indent=2)}\n```\n\n"
            + (f"# 用户附加提示\n{extra_hint}\n\n" if extra_hint else "")
            + "请按 system prompt 的输出格式，反推一个 Seedance Prompt，严格输出 JSON。"
        ),
    }]

    # 产品图（可选）
    from backend.services.prompt_compiler import _compress_image_b64
    for img_path in product_image_paths[:3]:  # 最多 3 张
        try:
            img_data = _compress_image_b64(img_path, max_side=1024, quality=85)
            if img_data:
                mime, b64 = img_data
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                })
        except Exception as e:
            print(f"  ⚠ 产品图 {img_path} 加载失败: {e}")

    content = await _call_gpt(system_prompt, user_content, engine=engine)
    metadata = _parse_json(content)
    if not metadata or not isinstance(metadata, dict):
        raise RuntimeError(f"LLM 返回非 JSON：{content[:200]}")
    return metadata


# ── 拼装最终 compiled_prompt（按目标时长/比例动态调整）──
def _assemble_replica_prompt(
    metadata: Dict[str, Any],
    duration: str,
    aspect_ratio: str,
    resolution: str,
) -> str:
    """组装最终 seedancePrompt 文本（动态适配时长/比例）。"""
    def g(k: str) -> str:
        v = metadata.get(k, "")
        return str(v).strip() if v else ""

    parts: list = []

    user_supp = g("userSupplementPrompt")
    if user_supp:
        parts.append(user_supp)

    # 动态生成镜头段
    duration_sec = int(float(duration.rstrip("s")) if duration else 5)
    shot_templates = {
        5: [("镜头1", "0-2s", "shot1"), ("镜头2", "2-4s", "shot2"), ("镜头3", "4-5s", "shot3")],
        8: [("镜头1", "0-2s", "shot1"), ("镜头2", "2-4s", "shot2"),
            ("镜头3", "4-6s", "shot3"), ("镜头4", "6-7s", "shot4"), ("镜头5", "7-8s", "shot5")],
        10: [("镜头1", "0-2s", "shot1"), ("镜头2", "2-4s", "shot2"),
             ("镜头3", "4-6s", "shot3"), ("镜头4", "6-8s", "shot4"), ("镜头5", "8-10s", "shot5")],
        15: [("镜头1", "0-3s", "shot1"), ("镜头2", "3-6s", "shot2"),
             ("镜头3", "6-9s", "shot3"), ("镜头4", "9-12s", "shot4"), ("镜头5", "12-15s", "shot5")],
    }
    shot_list = shot_templates.get(duration_sec, shot_templates[5])
    shots_text = "\n".join(f"{name} {ts}：{g(key)}" for name, ts, key in shot_list)

    parts.append(f"[{duration_sec}秒带货成交分镜]\n{shots_text}")

    parts.append(
        f"[Seedance 成片要求]\n"
        f"生成 {aspect_ratio}、{duration_sec} 秒、{resolution} 电商带货短视频。"
        "商品始终清晰可见，镜头节奏服务成交，不拍无意义空镜。"
        "不得改变上传商品的颜色、图案、版型、材质、logo、纹理和关键细节。"
        "画面可有默认模特或局部人物辅助展示，但人物不能抢走商品主体。"
    )

    # 严禁文字生成
    parts.append(
        "[严禁文字生成 - 硬约束，不可商量]\n"
        "严禁生成任何文字，包括但不限于：字幕、标题、标语、品牌口号、价格、数字、"
        "按钮、水印、贴纸文字、路牌文字、包装文字、UI 元素文字。\n"
        "画面中不得出现任何可识别的字符（中文、英文、数字、符号均禁止）。\n"
        "所有需要传达的信息只通过画面构图、模特动作、产品展示和后期旁白体现，"
        "绝不依赖画面内文字。此约束优先级最高，覆盖一切冲突指令。"
    )

    # 产品一致性
    parts.append(
        "[产品一致性 - 硬约束，不可商量]\n"
        "严格保持产品外观与上传参考图完全一致，必须 1:1 还原以下要素：颜色、图案、"
        "版型、材质、logo、纹理、结构、尺寸比例、印刷细节、配件位置。\n"
        "禁止臆造、推理、脑补产品样式：不得自行添加参考图中没有的颜色、图案、文字、"
        "配件、结构变形；不得「想象」产品的背面/内部/展开形态（参考图未展示的部分不得呈现）。\n"
        "分镜中描述产品时，只能使用参考图可见的特征，不得用「类似」「可能」「大概」等推测性表述。\n"
        "此约束优先级最高，覆盖一切冲突指令。"
    )

    # 旁白
    parts.append(
        "[情绪化旁白 - 后期音频脚本，不让视频模型生成字幕]\n"
        f"旁白情绪：{g('voiceoverTone')}\n"
        f"旁白节奏：{g('voiceoverRhythm')}\n"
        f"旁白文案：{g('voiceoverScript')}"
    )

    return "\n\n".join(parts)
