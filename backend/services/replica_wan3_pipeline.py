"""爆款复刻 wan3.0 工作流 Pipeline

4 步串行 + 内部并发：
1. MiMo 按完整分镜切分原视频 → N 段 mp4 + storyboard JSON
2. LLM (GPT/Grok) 反推每段 wan3.0 prompt → N 个独立 JSON
3. N 段并发提交 wan3.0 任务（上传→提交→轮询→下载）
4. ffmpeg concat 合并 N 段成 1 个成品视频

严格遵循 wan3-prompt-builder skill v3.1.0 规范。
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from config import settings

# ── 配置（懒读，便于测试覆盖）──
def _cfg() -> Dict[str, Any]:
    return {
        "mimo_fps": float(getattr(settings, "REPLICA_WAN3_MIMO_FPS", 4.0)),
        "mimo_resolution": getattr(settings, "REPLICA_WAN3_MIMO_MEDIA_RESOLUTION", "max"),
        "mimo_split_mode": getattr(settings, "REPLICA_WAN3_MIMO_SPLIT_MODE", "scene_cut"),
        "max_segments": int(getattr(settings, "REPLICA_WAN3_MAX_SEGMENTS", 10)),
        "llm_engine": getattr(settings, "REPLICA_WAN3_LLM_ENGINE", "gpt"),
        "concurrency": int(getattr(settings, "REPLICA_WAN3_CONCURRENCY", 3)),
        "resolution": getattr(settings, "REPLICA_WAN3_RESOLUTION", "480P"),
        "aspect_ratio": getattr(settings, "REPLICA_WAN3_ASPECT_RATIO", "adaptive"),
        "audio": bool(getattr(settings, "REPLICA_WAN3_AUDIO", True)),
    }


# ── Step 1: MiMo 切分 ──
async def step1_split(
    video_path: str,
    output_dir: str,
    on_log: Optional[Callable[[str], None]] = None,
    product_image_paths: Optional[List[str]] = None,
    model_image_path: Optional[str] = None,
    scene_image_path: Optional[str] = None,
    customer_request: Optional[str] = None,
) -> Dict[str, Any]:
    """用 MiMo scene_cut 一站式解析+反推（切分 + 输出 wan3_plans）。

    Returns:
        {
            "storyboard": [...],
            "timeline": [...],
            "wan3_plans": [...],
            "segment_paths": [".../segment_01.mp4", ...],
            "segment_durations": [9.0, 11.0, ...],
            "video_duration": float,
        }
    """
    def log(msg: str) -> None:
        if on_log:
            on_log(msg)
        print(f"  [Step1] {msg}", flush=True)

    log("启动 MiMo 一站式解析（scene_cut + wan3.0 prompt 反推）")

    cfg = _cfg()
    # 同步 settings.MIMO_API_KEY 到环境变量（pipeline.step1_mimo_parse 用 os.environ.get）
    os.environ["MIMO_API_KEY"] = settings.MIMO_API_KEY
    from pipeline.step1_mimo_parse import run as mimo_run

    mimo_out = str(Path(output_dir) / "step1_mimo")
    Path(mimo_out).mkdir(parents=True, exist_ok=True)

    # 调 MiMo 一站式解析（看视频 + 素材图 + 客户需求文本，输出 storyboard + wan3_plans）
    # - split_mode=scene_cut 按镜头转场切分
    # - target_duration=None 1:1 完整复刻
    # - 把产品图/模特图/场景图传给 MiMo，让它视觉识别后写入 wan3_plans 的 prompt
    # - 客户需求文本由 MiMo 综合判断意图，融入每段 wan3.0 prompt
    result = await asyncio.to_thread(
        mimo_run,
        video_path=video_path,
        product_name="（待识别产品）",
        output_dir=mimo_out,
        fps=cfg["mimo_fps"],
        media_resolution=cfg["mimo_resolution"],
        split_mode=cfg["mimo_split_mode"],
        target_duration=None,
        product_image_paths=product_image_paths,
        model_image_path=model_image_path,
        scene_image_path=scene_image_path,
        customer_request=customer_request,
    )

    storyboard = result.get("storyboard") or []
    timeline = result.get("timeline") or []
    wan3_plans = result.get("wan3_plans") or []
    if not storyboard:
        raise RuntimeError("MiMo 未输出 storyboard")
    if wan3_plans:
        log(f"✓ MiMo 一站式输出 {len(wan3_plans)} 段 wan3.0 prompt")

    # 不在 storyboard 上截断（会丢失视频末尾），改为合并后限制段数

    # 从 storyboard 提取每段真实 [start, end] 区间
    # MiMo 字段名为「开始时间」/「结束时间」（非「开始」/「结束」）
    raw_shots: List[Dict[str, Any]] = []
    for idx, shot in enumerate(storyboard, start=1):
        interval = shot.get("时间区间") or shot.get("time_range") or {}
        start = _to_seconds(
            interval.get("开始时间") or interval.get("开始")
            or interval.get("start") or 0
        )
        end = _to_seconds(
            interval.get("结束时间") or interval.get("结束")
            or interval.get("end") or 0
        )
        if end <= start:
            raise RuntimeError(
                f"Shot {idx} 时间区间无效: start={start} end={end} "
                f"(interval={interval})"
            )
        raw_shots.append({
            "idx": idx,
            "start": start,
            "end": end,
            "duration": round(end - start, 2),
        })

    # 优先使用 MiMo 输出的 segment_table（合并段表）
    # 否则降级到代码贪心合并
    seg_table = result.get("segment_table") or []
    final_segments: Optional[List[Dict[str, Any]]] = None
    if seg_table:
        log(f"MiMo 输出 {len(raw_shots)} 个细分分镜 + {len(seg_table)} 个合并段，使用 MiMo 合并段表切分")
        final_segments = []
        for st in seg_table:
            seg_start = _to_seconds(st.get("start_time") or st.get("start") or 0)
            seg_end = _to_seconds(st.get("end_time") or st.get("end") or 0)
            seg_dur = _to_seconds(st.get("duration") or (seg_end - seg_start))
            if seg_end <= seg_start:
                log(f"⚠ MiMo 合并段 {st.get('segment_index', '?')} 时间无效，跳过")
                continue
            final_segments.append({
                "start": seg_start,
                "end": seg_end,
                "duration": round(seg_dur, 2),
                "shots": st.get("contains_shots", []),
            })
        if not final_segments:
            log("⚠ MiMo segment_table 全部无效，降级到代码合并")
            final_segments = None

    if final_segments is None:
        log(f"MiMo 输出 {len(raw_shots)} 个细分分镜，开始代码合并到目标 ~10s/段")
        target = 10.0
        max_seg = 15.0  # API 单段上限
        merged: List[Dict[str, Any]] = []
        cur_start = raw_shots[0]["start"]
        cur_end = raw_shots[0]["end"]
        cur_shot_ids = [raw_shots[0]["idx"]]

        for s in raw_shots[1:]:
            cur_dur = cur_end - cur_start
            new_dur_if_add = s["end"] - cur_start
            if cur_dur >= target - 1.0:
                merged.append({
                    "start": cur_start, "end": cur_end,
                    "shots": cur_shot_ids, "duration": round(cur_dur, 2),
                })
                cur_start = s["start"]
                cur_end = s["end"]
                cur_shot_ids = [s["idx"]]
            elif new_dur_if_add > max_seg:
                merged.append({
                    "start": cur_start, "end": cur_end,
                    "shots": cur_shot_ids, "duration": round(cur_dur, 2),
                })
                cur_start = s["start"]
                cur_end = s["end"]
                cur_shot_ids = [s["idx"]]
            else:
                cur_end = s["end"]
                cur_shot_ids.append(s["idx"])

        merged.append({
            "start": cur_start, "end": cur_end,
            "shots": cur_shot_ids, "duration": round(cur_end - cur_start, 2),
        })

        # 软约束：单段 > 15s 强切
        final_segments = []
        for m in merged:
            if m["duration"] > max_seg:
                cs = m["start"]
                while cs < m["end"]:
                    ce = min(cs + max_seg, m["end"])
                    final_segments.append({
                        "start": cs, "end": ce,
                        "duration": round(ce - cs, 2),
                        "shots": m["shots"],
                    })
                    cs = ce
            else:
                final_segments.append(m)

    # 限制最大段数（截取前 N 段）
    if len(final_segments) > cfg["max_segments"]:
        log(f"合并后段数 {len(final_segments)} 超过上限 {cfg['max_segments']}，"
            f"截取前 {cfg['max_segments']} 段")
        final_segments = final_segments[: cfg["max_segments"]]

    # 用 ffmpeg 切 mp4
    segment_paths: List[str] = []
    segment_durations: List[float] = []
    seg_dir = Path(output_dir) / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)

    for idx, seg in enumerate(final_segments, start=1):
        start = seg["start"]
        end = seg["end"]
        duration = seg["duration"]
        seg_path = seg_dir / f"segment_{idx:02d}.mp4"
        shots_str = ",".join(str(s) for s in seg["shots"]) if seg.get("shots") else ""
        log(f"切分段 {idx}: {start:.2f}s → {end:.2f}s ({duration:.1f}s, "
            f"含分镜 {shots_str}) → {seg_path.name}")
        await asyncio.to_thread(
            _ffmpeg_cut,
            video_path,
            start,
            end,
            str(seg_path),
        )
        segment_paths.append(str(seg_path))
        segment_durations.append(duration)

    # 真实视频总时长（用 ffprobe 探测）
    video_duration = await asyncio.to_thread(_ffprobe_duration, video_path)
    if not video_duration:
        video_duration = sum(segment_durations)

    log(f"切分完成：共 {len(segment_paths)} 段，总时长 {video_duration:.2f}s "
        f"(平均 {video_duration/max(1,len(segment_paths)):.1f}s/段)")
    return {
        "storyboard": storyboard,
        "timeline": timeline,
        "wan3_plans": wan3_plans,
        "segment_paths": segment_paths,
        "segment_durations": segment_durations,
        "video_duration": video_duration,
    }


# ── Step 2: LLM 反推 ──
async def step2_reverse(
    storyboard: List[Dict[str, Any]],
    segment_durations: List[float],
    product_image_paths: List[str],
    model_image_path: str,
    scene_image_path: str,
    customer_request: str,
    on_log: Optional[Callable[[str], None]] = None,
) -> List[Dict[str, Any]]:
    """调 LLM 反推每段 wan3.0 prompt，输出 N 个独立 JSON。

    LLM 自行视觉识别客户上传图（产品图/模特图/场景图），结合 customer_request
    推断复刻意图：哪些素材需要替换、哪些保持原视频特征，由 LLM 自行决定。

    严格按 wan3-prompt-builder skill 的 JSON 格式输出，含：
    - shot_index, reference_video_path, duration, prompt, api_json

    Returns:
        List of segment JSON
    """
    def log(msg: str) -> None:
        if on_log:
            on_log(msg)
        print(f"  [Step2] {msg}", flush=True)

    cfg = _cfg()
    from backend.services.prompt_compiler import (
        _call_gpt, _compress_image_b64, _parse_json,
    )

    system_prompt = _build_llm_system_prompt(
        num_segments=len(storyboard),
        segment_durations=segment_durations,
        num_product_images=len(product_image_paths),
        has_model_image=bool(model_image_path),
        has_scene_image=bool(scene_image_path),
        customer_request=customer_request,
    )

    # user content: storyboard 精简版（只保留 LLM 反推必需字段，避免请求体过大）
    # 原始 MiMo 输出字段太多（含面部微表情/声音语气/呼吸感等），LLM 反推不需要
    slim_storyboard = []
    for shot in storyboard:
        interval = shot.get("时间区间") or shot.get("time_range") or {}
        slim_storyboard.append({
            "shot": shot.get("shot"),
            "时间区间": {
                "开始时间": interval.get("开始时间") or interval.get("开始"),
                "结束时间": interval.get("结束时间") or interval.get("结束"),
            },
            "场景环境": shot.get("场景环境", {}),
            "人物与产品": shot.get("人物与产品", {}),
            "画面动作": shot.get("画面动作", {}),
            "镜头语言": shot.get("镜头语言", {}),
            "音效": shot.get("音效"),
            "口播": shot.get("口播"),
        })

    user_content: List[Dict[str, Any]] = [{
        "type": "text",
        "text": (
            f"# 原视频 MiMo 解析结果（精简 storyboard，共 {len(slim_storyboard)} 个分镜）\n\n"
            f"```json\n{json.dumps(slim_storyboard, ensure_ascii=False, indent=2)}\n```\n\n"
            f"# 每段参考视频时长（秒）\n{json.dumps(segment_durations, ensure_ascii=False)}\n\n"
            f"请按 system prompt 的 JSON 格式，为每段输出一个独立的 wan3.0 调用 JSON，"
            f"严格输出 JSON 数组。"
        ),
    }]

    # 客户素材图（产品图 + 模特图 + 场景图，让 LLM 视觉识别）
    # 压缩到长边 768px（视觉识别足够，避免请求体过大导致网关 504）
    all_images = list(product_image_paths)
    if model_image_path:
        all_images.append(model_image_path)
    if scene_image_path:
        all_images.append(scene_image_path)

    for img_path in all_images[:10]:  # API 上限 10 张
        try:
            img_data = _compress_image_b64(img_path, max_side=768, quality=80)
            if img_data:
                mime, b64 = img_data
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                })
        except Exception as e:
            log(f"⚠ 素材图 {img_path} 加载失败: {e}")

    log(f"调用 LLM ({cfg['llm_engine']}) 反推 {len(storyboard)} 段 wan3.0 prompt")
    content = await _call_gpt(system_prompt, user_content, engine=cfg["llm_engine"])

    parsed = _parse_json(content)
    if not parsed:
        raise RuntimeError(f"LLM 返回非 JSON：{content[:300]}")

    # 兼容两种输出形态：单个数组 / 包含 segments 字段的对象
    if isinstance(parsed, list):
        segments = parsed
    elif isinstance(parsed, dict):
        segments = parsed.get("segments") or [parsed]
    else:
        raise RuntimeError(f"LLM 返回格式不支持: {type(parsed)}")

    if len(segments) != len(storyboard):
        log(f"⚠ LLM 输出段数 {len(segments)} 与切分段数 {len(storyboard)} 不一致")
        # 不强制截断，以 LLM 输出为准（让下游判断）
    log(f"LLM 反推完成：共 {len(segments)} 段 JSON")
    return segments


def _build_llm_system_prompt(
    num_segments: int,
    segment_durations: List[float],
    num_product_images: int,
    has_model_image: bool,
    has_scene_image: bool,
    customer_request: str,
) -> str:
    """构建 LLM 系统提示词，严格遵循 wan3-prompt-builder skill v3.1.0。

    LLM 自行视觉识别客户上传图，结合 customer_request 文本意图，
    推断哪些素材需要替换到新视频中，无需前端多选框。
    """
    # 图片序号映射规则（按上传顺序：产品图 → 模特图 → 场景图）
    image_map_lines = []
    img_idx = 1
    for i in range(1, num_product_images + 1):
        image_map_lines.append(f"Image {img_idx} = 产品图 {i}（视觉识别其颜色/材质/形态/包装）")
        img_idx += 1
    if has_model_image:
        model_idx = img_idx
        image_map_lines.append(f"Image {model_idx} = 模特图（视觉识别人物性别/年龄/服装/姿态/发型）")
        img_idx += 1
    if has_scene_image:
        scene_idx = img_idx
        image_map_lines.append(f"Image {scene_idx} = 场景/背景图（视觉识别环境/色调/光影/陈设）")
        img_idx += 1
    image_map = "\n".join(image_map_lines) if image_map_lines else "（客户未上传任何素材图，仅按原视频复刻）"

    # 自动替换策略说明（LLM 看图 + 看需求 自行判断）
    auto_replace_block = """# 客户上传素材意图判断（LLM 自行视觉识别，无需多选框）

**判断规则**：
1. 看每张 Image 的视觉内容（产品形态/模特特征/场景环境）
2. 对照原视频 MiMo 解析中的「人物与产品」「场景环境」字段
3. 结合客户需求说明文本
4. 推断复刻意图：哪些元素需要替换、哪些保持原视频特征

**替换策略**：
- 上传产品图 → 在新 prompt 中用对应 `Image N` 替换原产品描述（颜色/形态/包装按图为准）
- 上传模特图 → 在新 prompt 中用对应 `Image N` 替换原人物描述
- 上传场景图 → 在新 prompt 中用对应 `Image N` 替换原背景描述
- 客户需求说明文本中提到的元素，要落实到 prompt 中
- 未上传的元素，保持原视频特征（仅文字描述，不引用图片）

**意图判断输出**：在每段 JSON 的 `intent_inference` 字段简述本段替换意图（一句话）。"""

    # 各段时长列表
    durations_list = "\n".join(
        f"  Shot {i+1}: {d:.1f}s" for i, d in enumerate(segment_durations)
    )

    return f"""你是 Wan 3.0 专业导演 + 任务提交官，严格遵循阿里 Wan-3.0 视频生成模型语法。

# 工作目标
为爆款复刻工作流反推 {num_segments} 段独立的 wan3.0 prompt，每段对应原视频 1 个 MiMo 切分片段。
输出 JSON 数组，数组中每个元素对应一段。

# 每段参考视频时长（输入时长）
{durations_list}

每段生成的视频时长必须**严格等于**参考视频时长（精准秒数），不能多也不能少。

# ⚠️ Wan3.0 保留字符清单（禁止误用）

| 用途 | 保留形式 | 说明 |
|---|---|---|
| 分镜 | `Shot N [起始-结束s]` | 如 `Shot 1 [0-9s]` |
| 图片参考 | `Image 1` / `Image 2` | 空格分隔，无 @，序号对应上传顺序 |
| 视频参考 | `Video 1` / `Video 2` | 空格分隔，无 @ |
| 角色参考 | `character1` / `character2` | 小写无空格 |
| 人物台词 | `"台词内容" [角色标签, 语气]:` | 双引号包裹 + 角色语气前缀 |
| 全程静音 | `No dialogue throughout the entire video` | 完整短语 |
| 全程禁乐 | `No background music` | 完整短语 |
| 一镜到底 | `One continuous take` | 完整短语 |
| 硬切转场 | `Hard cut transition` | 完整短语，仅用于跨 Shot 转场 |
| 关闭扩写 | API JSON 中 `duration` 用整数秒，不写 prompt_extend（API 默认关闭） |

**禁止**：
- ❌ 用 `@Image1`（错误：带 @）
- ❌ 用 `Image1`（错误：缺空格）
- ❌ 用 `No dialogue`（错误：截断了保留词）

# 客户素材图序号映射（按上传顺序）
{image_map}

{auto_replace_block}

# 客户需求说明（自然语言，要落实到 prompt）
{customer_request or "（客户未提供额外需求，按原视频 1:1 复刻）"}

# 跨段无缝衔接硬约束
1. **首尾帧衔接**：Shot N 的最后一帧画面描述 = Shot N+1 的第一帧画面描述
2. **场景连贯**：跨 Shot 光影/色调/人物姿态连贯
3. **转场标记**：如需硬切，在 Shot N+1 开头加 `Hard cut transition`
4. **时长对齐**：每段 duration 严格等于参考段时长

# 每段画面后置约束（必须追加在 prompt 末尾，属于自由描述区）
电影级质感，高细节，真实物理运动，减少AI畸变，人物肢体正常，无扭曲穿模，画面稳定，运镜顺滑

# 输出 JSON 数组格式（严格输出，不要多余文字）

```json
[
  {{
    "shot_index": 1,
    "duration": 9,
    "intent_inference": "本段使用 Image 1 替换原山楂饼为山楂糕；模特保持原视频白裙女性；场景保持室内暖光",
    "prompt": "Shot 1 [0-9s]: 中景，年轻女性 character1 手持 Image 1 山楂糕，柔和窗光从左倾洒，镜头缓慢推近，环境白噪音，\\"好吃不腻\\" [character1, 轻快]: 电影级质感，高细节，真实物理运动，减少AI畸变，人物肢体正常，无扭曲穿模，画面稳定，运镜顺滑",
    "api_json": {{
      "prompt": "（与上面 prompt 字段相同）",
      "videoUrls": ["由代码填入"],
      "imageUrls": ["由代码填入"],
      "audioUrls": [],
      "resolution": "480P",
      "aspectRatio": "adaptive",
      "duration": "9",
      "audio": "true",
      "enableThinking": "false",
      "seed": null
    }}
  }},
  ...
]
```

每段 prompt 中：
1. 必须包含运镜、动作、光影、声音的清晰描述，禁止一句话
2. 引用图片时按序号映射用 `Image N`（仅引用客户上传的素材图）
3. 每段 prompt 末尾必加上面那段画面后置约束
4. duration 字段 = 该段参考视频时长整数秒
5. intent_inference 必须明确说明本段用了哪些 Image N 替换哪些原视频元素

严格输出 JSON 数组，不要包裹任何其他文字或解释。"""


# ── Step 3: N 段并发提交 wan3.0 ──
async def step3_generate_concurrent(
    segment_plans: List[Dict[str, Any]],
    segment_video_paths: List[str],
    all_image_urls: List[str],
    output_dir: str,
    on_log: Optional[Callable[[str], None]] = None,
    on_progress: Optional[Callable[[int, str, str], None]] = None,
) -> List[Dict[str, Any]]:
    """N 段并发提交 wan3.0 任务，每段独立链路：上传→提交→轮询→下载。

    Args:
        segment_plans: LLM 反推的 N 段 JSON
        segment_video_paths: 每段参考视频本地路径（与 plans 顺序对齐）
        all_image_urls: 所有客户素材图已上传的 URL 列表
        output_dir: 输出根目录
        on_log: 日志回调
        on_progress: 进度回调 fn(seg_idx, phase, message)

    Returns:
        List of per-segment result dict
    """
    def log(msg: str) -> None:
        if on_log:
            on_log(msg)
        print(f"  [Step3] {msg}", flush=True)

    cfg = _cfg()
    from backend.services import wan3_client

    n = len(segment_plans)
    if n != len(segment_video_paths):
        log(f"⚠ 段数不匹配：plans={n}, videos={len(segment_video_paths)}，取较小值")
        n = min(n, len(segment_video_paths))
        segment_plans = segment_plans[:n]
        segment_video_paths = segment_video_paths[:n]

    log(f"启动 {n} 段并发生成（并发度 {cfg['concurrency']}）")

    semaphore = asyncio.Semaphore(cfg["concurrency"])
    results: List[Optional[Dict[str, Any]]] = [None] * n

    async def _run_one(idx: int) -> None:
        plan = segment_plans[idx]
        ref_path = segment_video_paths[idx]
        # duration 合法值约束：auto 或 2-30 整数
        raw_dur = plan.get("duration") or 5
        try:
            dur_int = int(float(raw_dur))
        except (TypeError, ValueError):
            dur_int = 5
        if dur_int < 2:
            dur_int = 2
        elif dur_int > 30:
            dur_int = 30
        duration = str(dur_int)
        prompt = plan.get("prompt") or plan.get("api_json", {}).get("prompt", "")
        if not prompt:
            log(f"段 {idx+1} 缺少 prompt，跳过")
            results[idx] = {"segment_index": idx + 1, "error": "missing prompt"}
            return

        def progress(phase: str, elapsed: int, msg: str) -> None:
            if on_progress:
                on_progress(idx + 1, phase, msg)

        async with semaphore:
            log(f"段 {idx+1} 开始（duration={duration}s）")
            try:
                res = await asyncio.to_thread(
                    wan3_client.run_one_segment,
                    prompt=prompt,
                    reference_video_path=ref_path,
                    image_urls=all_image_urls,
                    duration=duration,
                    output_dir=output_dir,
                    segment_index=idx + 1,
                    resolution=cfg["resolution"],
                    aspect_ratio=cfg["aspect_ratio"],
                    audio=cfg["audio"],
                    on_progress=progress,
                )
                results[idx] = res
                log(f"段 {idx+1} 完成 → {res['local_path']}")
            except Exception as e:
                log(f"段 {idx+1} 失败: {e}")
                results[idx] = {"segment_index": idx + 1, "error": str(e)}

    tasks = [asyncio.create_task(_run_one(i)) for i in range(n)]
    await asyncio.gather(*tasks)

    # 统计成功/失败
    ok = sum(1 for r in results if r and "local_path" in r)
    log(f"并发完成：{ok}/{n} 段成功")
    return [r or {"segment_index": i + 1, "error": "unknown"} for i, r in enumerate(results)]


# ── Step 4: ffmpeg concat 合并 ──
async def step4_merge(
    segment_results: List[Dict[str, Any]],
    output_dir: str,
    on_log: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """用 ffmpeg concat 协议合并 N 段成 1 个成品视频。

    Returns:
        {"merged_path": str, "duration": float}
    """
    def log(msg: str) -> None:
        if on_log:
            on_log(msg)
        print(f"  [Step4] {msg}", flush=True)

    # 取出成功的段（按 segment_index 排序）
    ok_segments = sorted(
        [r for r in segment_results if r and "local_path" in r],
        key=lambda r: r.get("segment_index", 0),
    )
    if not ok_segments:
        raise RuntimeError("无成功段可合并")

    if len(ok_segments) == 1:
        log("仅 1 段成功，直接作为成品")
        return {
            "merged_path": ok_segments[0]["local_path"],
            "duration": float(ok_segments[0].get("duration", 0)),
        }

    merged_path = Path(output_dir) / "merged_final.mp4"
    log(f"合并 {len(ok_segments)} 段 → {merged_path.name}")

    # 优先用 xfade 跨段交叉淡入淡出（0.3s），避免硬切跳变
    # 仅当所有段编码参数一致时生效；失败回退到 concat copy
    use_xfade = len(ok_segments) >= 2
    if use_xfade:
        try:
            await _ffmpeg_merge_with_xfade(ok_segments, merged_path, xfade_duration=0.3, log=log)
            duration = await asyncio.to_thread(_ffprobe_duration, str(merged_path))
            log(f"合并完成（xfade 过渡）→ {merged_path.name}（{duration:.2f}s）")
            return {"merged_path": str(merged_path), "duration": duration or 0.0}
        except Exception as e:
            log(f"⚠ xfade 合并失败 ({e})，回退到 concat copy")

    # 回退：concat demuxer 无损拼接（段间硬切）
    list_file = Path(output_dir) / "concat_list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for seg in ok_segments:
            p = seg["local_path"].replace("\\", "/")
            f.write(f"file '{p}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(merged_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        log("concat copy 失败，回退到重编码合并")
        cmd_reencode = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-c:v", "libx264",
            "-c:a", "aac",
            "-preset", "fast",
            "-r", "30",                # 强制输出 30fps，避免段间帧率不一致卡帧
            "-vsync", "cfr",           # 恒定帧率模式
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(merged_path),
        ]
        proc2 = await asyncio.create_subprocess_exec(
            *cmd_reencode,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr2 = await proc2.communicate()
        if proc2.returncode != 0:
            raise RuntimeError(
                f"ffmpeg 合并失败: {stderr2.decode('utf-8', errors='ignore')[:500]}"
            )

    duration = await asyncio.to_thread(_ffprobe_duration, str(merged_path))
    log(f"合并完成 → {merged_path}（{duration:.2f}s）")
    return {"merged_path": str(merged_path), "duration": duration or 0.0}


async def _ffmpeg_merge_with_xfade(
    segments: List[Dict[str, Any]],
    out_path: Path,
    xfade_duration: float = 0.3,
    log: Optional[Callable[[str], None]] = None,
) -> None:
    """用 ffmpeg xfade 跨段交叉淡入淡出合并。

    xfade 在段 N 末尾与段 N+1 开头做 0.3s 重叠淡入，平滑画面跳变。
    仅处理视频流；音频用 concat 简单拼接（避免 xfade 音频复杂度）。
    """
    # 1. 探测每段时长
    durations = []
    for seg in segments:
        d = await asyncio.to_thread(_ffprobe_duration, seg["local_path"])
        if not d:
            raise RuntimeError(f"无法探测段 {seg.get('segment_index')} 时长")
        durations.append(d)

    # 2. 构建 ffmpeg 复杂滤镜
    # 输入：所有段作为 -i
    inputs = []
    for seg in segments:
        inputs.extend(["-i", seg["local_path"]])

    # 每段先经过 fps=30 标准化（避免段间帧率不一致导致 xfade 卡帧），再链式 xfade
    # [0:v]fps=30[n0];[1:v]fps=30[n1];[n0][n1]xfade=...[v1];[v1][n2]xfade=...[vout]
    fps_parts = []
    for i in range(len(segments)):
        fps_parts.append(f"[{i}:v]fps=30[n{i}]")
    filter_parts = []
    prev_label = "n0"
    cum_offset = 0.0
    for i in range(1, len(segments)):
        cum_offset += durations[i - 1] - xfade_duration
        out_label = f"v{i}" if i < len(segments) - 1 else "vout"
        filter_parts.append(
            f"[{prev_label}][n{i}]xfade=transition=fade:duration={xfade_duration}:offset={cum_offset:.3f}[{out_label}]"
        )
        prev_label = out_label
    filter_complex = ";".join(fps_parts + filter_parts)

    # 音频用 concat 简单拼接
    concat_filter = "".join(f"[{i}:a]" for i in range(len(segments))) + f"concat=n={len(segments)}:v=0:a=1[aout]"
    filter_complex = filter_complex + ";" + concat_filter if filter_parts else concat_filter

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]" if len(segments) > 1 else "[n0]",
        "-map", "[aout]",
        "-c:v", "libx264",
        "-preset", "fast",
        "-r", "30",                # 强制输出 30fps，避免段间帧率不一致卡帧
        "-vsync", "cfr",           # 恒定帧率模式，防止 VFR 段导致时间戳错位
        "-pix_fmt", "yuv420p",     # 标准化像素格式，避免兼容性问题
        "-movflags", "+faststart", # 流式播放优化
        "-c:a", "aac",
        "-b:a", "128k",
        str(out_path),
    ]
    if log:
        log(f"xfade 合并：{len(segments)} 段，过渡 {xfade_duration}s")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"xfade 合并失败: {stderr.decode('utf-8', errors='ignore')[:500]}")


# ── 完整 Pipeline 入口 ──
async def run_pipeline(
    task_id: str,
    video_path: str,
    product_image_paths: List[str],
    model_image_path: str,
    scene_image_path: str,
    customer_request: str,
    output_dir: str,
    on_log: Optional[Callable[[str], None]] = None,
    on_phase: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """完整执行 3 步工作流（MiMo 一站式解析+反推 → Wan3.0 并发生成 → ffmpeg 合并）。

    MiMo 一次性看视频 + 所有素材图 + 客户需求文本，输出 storyboard + wan3_plans，
    省掉外部 LLM 反推步骤（避免 GPT/Grok 504 超时）。

    Args:
        task_id: 任务 ID
        video_path: 爆款视频本地路径
        product_image_paths: 多张产品图路径
        model_image_path: 模特图路径
        scene_image_path: 场景图路径
        customer_request: 客户需求说明文本（由 MiMo 综合判断意图）
        output_dir: 输出根目录
        on_log: 实时日志回调
        on_phase: 阶段变更回调 fn(phase, extra)

    Returns:
        {
            "merged_path": str,
            "storyboard": list,
            "segment_plans": list,
            "segment_results": list,
            "duration": float,
        }
    """
    def log(msg: str) -> None:
        if on_log:
            on_log(msg)
        print(f"[wan3-pipe {task_id}] {msg}", flush=True)

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # ── Step 1: MiMo 一站式解析+反推（切分 + 输出 wan3_plans）──
    if on_phase:
        on_phase("cutting", {})
    log("=== Step 1/3: MiMo 一站式解析 + wan3.0 prompt 反推 ===")
    split_result = await step1_split(
        video_path=video_path,
        output_dir=output_dir,
        on_log=log,
        product_image_paths=product_image_paths,
        model_image_path=model_image_path,
        scene_image_path=scene_image_path,
        customer_request=customer_request,
    )
    segment_plans = split_result.get("wan3_plans") or []
    if not segment_plans:
        raise RuntimeError("MiMo 未输出 wan3_plans 块，无法继续执行 wan3.0 调用")
    log(f"✓ MiMo 一站式输出 {len(segment_plans)} 段 wan3.0 prompt")

    # ── Step 2: 客户素材图预上传（共享 URL）+ N 段并发 ──
    if on_phase:
        on_phase("generating", {"segments": len(segment_plans)})
    log("=== Step 2/3: 上传素材 + 并发提交 wan3.0 ===")
    from backend.services import wan3_client

    all_image_paths = list(product_image_paths)
    if model_image_path:
        all_image_paths.append(model_image_path)
    if scene_image_path:
        all_image_paths.append(scene_image_path)

    # 并发上传所有素材图
    log(f"并发上传 {len(all_image_paths)} 张素材图到 RunningHub")
    image_urls: List[str] = []
    upload_tasks = [
        asyncio.to_thread(wan3_client.upload_media, p)
        for p in all_image_paths
    ]
    upload_results = await asyncio.gather(*upload_tasks, return_exceptions=True)
    for i, r in enumerate(upload_results):
        if isinstance(r, Exception):
            log(f"⚠ 素材图 {i+1} 上传失败: {r}")
            image_urls.append("")  # 占位，让 LLM 引用失效
        else:
            image_urls.append(r)

    # N 段并发提交
    segment_results = await step3_generate_concurrent(
        segment_plans=segment_plans,
        segment_video_paths=split_result["segment_paths"],
        all_image_urls=image_urls,
        output_dir=str(Path(output_dir) / "outputs"),
        on_log=log,
        on_progress=lambda idx, phase, msg: log(f"段 {idx} {phase}: {msg}"),
    )

    # ── Step 3: 合并 ──
    if on_phase:
        on_phase("merging", {})
    log("=== Step 3/3: ffmpeg concat 合并 ===")
    merged = await step4_merge(
        segment_results=segment_results,
        output_dir=output_dir,
        on_log=log,
    )

    if on_phase:
        on_phase("completed", merged)
    log(f"=== 工作流完成 → {merged['merged_path']} ===")
    return {
        "merged_path": merged["merged_path"],
        "storyboard": split_result["storyboard"],
        "timeline": split_result["timeline"],
        "segment_plans": segment_plans,
        "segment_results": segment_results,
        "duration": merged["duration"],
        "video_duration": split_result["video_duration"],
    }


# ── ffmpeg 工具函数 ──
def _to_seconds(value) -> float:
    """把 '00:02.50' 或数字或 '1500ms' 转为秒数。"""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return 0.0
    # mm:ss.xx 或 hh:mm:ss.xx
    if ":" in s:
        parts = s.split(":")
        try:
            if len(parts) == 2:
                return int(parts[0]) * 60 + float(parts[1])
            elif len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        except ValueError:
            return 0.0
    # ms 后缀
    if s.endswith("ms"):
        try:
            return float(s[:-2]) / 1000.0
        except ValueError:
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _ffmpeg_cut(src: str, start: float, end: float, output: str) -> None:
    """用 ffmpeg 切分视频片段（精准起止时间，重编码保兼容）。"""
    duration = end - start
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start:.3f}",
        "-i", src,
        "-t", f"{duration:.3f}",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-preset", "fast",
        "-movflags", "+faststart",
        output,
    ]
    result = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg 切分失败: {result.stderr.decode('utf-8', errors='ignore')[:300]}"
        )


def _ffprobe_duration(video_path: str) -> Optional[float]:
    """用 ffprobe 探测视频时长（秒）。"""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            capture_output=False,
        )
        return float(result.stdout.decode("utf-8", errors="ignore").strip())
    except Exception:
        return None
