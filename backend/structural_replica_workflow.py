"""
结构性复刻工作流 - 主入口

3 步管线：
  Step1: MiMo 解析原视频+产品图+卖点 → 15s分镜脚本JSON
  Step3: Image2 生成分镜故事板海报（4K 16:9）
  Step4: H3 标准 API 生成 2K 满血视频（30秒轮询，1小时超时）

分阶段审核：
  Phase1 = Step1+Step3 → awaiting_review (after_step3)  审核 storybook_poster.png
  Phase2 = Step4                                        审核 final_video.mp4
"""
import json
import os
import sys
import io
import logging
import base64
import re
import time
import asyncio
import httpx
from pathlib import Path
from datetime import datetime

# Windows 中文编码
if sys.platform == "win32":
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, "encoding", "").lower() not in ("utf-8", "utf8"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    if hasattr(sys.stderr, "buffer") and getattr(sys.stderr, "encoding", "").lower() not in ("utf-8", "utf8"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PHASE_STATE_FILE = "phase_state.json"


# 可重试的错误特征（Cloudflare 524 / 502 / 503 / 504 / 上游超时 / 连接重置）
_RETRYABLE_KEYWORDS = (
    "524", "origin_response_timeout", "timeout occurred",
    "timed out", "timeout", "try again", "upstream",
    "temporary", "temporarily", "service unavailable",
    "connection reset", "connection aborted", "connection refused",
    "read timeout", "connect timeout", "502", "503", "504",
)


def _is_retryable_error(exc: Exception) -> bool:
    """判断异常是否可重试（Cloudflare 524 / 网关超时 / 临时网络故障）"""
    err_str = str(exc).lower()
    return any(kw in err_str for kw in _RETRYABLE_KEYWORDS)


def _call_with_retry(
    client,
    fn,
    *,
    label: str = "LLM",
    max_attempts: int = 3,
    base_delay: float = 120.0,
    **kwargs,
):
    """调用 LLM（同步），对可重试错误（524/502/503/超时）自动指数退避重试。

    Cloudflare 524 错误官方建议：wait and retry, back off for at least 120 seconds。
    首次失败等待 base_delay 秒，第二次失败等待 base_delay*2 秒。
    """
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn(**kwargs)
        except Exception as e:
            last_exc = e
            if not _is_retryable_error(e) or attempt == max_attempts:
                raise
            delay = base_delay * attempt  # 120s, 240s
            print(f"  ⚠ [{label}] 调用失败（可重试），第 {attempt}/{max_attempts} 次，"
                  f"等待 {delay:.0f}s 后重试: {str(e)[:200]}", flush=True)
            time.sleep(delay)
    raise last_exc  # 不会执行到这里


def _call_stream_with_retry(
    client,
    *,
    label: str = "LLM",
    max_attempts: int = 3,
    base_delay: float = 120.0,
    **kwargs,
):
    """流式调用 LLM，对可重试错误自动指数退避重试。

    流式模式下网关持续转发 token，避免因上游推理时间长触发 504 Gateway Time-out。
    返回 (raw_content, finish_reason, usage)。
    """
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            stream = client.chat.completions.create(stream=True, **kwargs)
            raw_content = ""
            finish_reason = None
            usage = None
            for chunk in stream:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        raw_content += delta.content
                    fr = chunk.choices[0].finish_reason
                    if fr:
                        finish_reason = fr
                if hasattr(chunk, "usage") and chunk.usage:
                    usage = chunk.usage
            return raw_content, finish_reason, usage
        except Exception as e:
            last_exc = e
            if not _is_retryable_error(e) or attempt == max_attempts:
                raise
            delay = base_delay * attempt
            print(f"  ⚠ [{label}] 流式调用失败（可重试），第 {attempt}/{max_attempts} 次，"
                  f"等待 {delay:.0f}s 后重试: {str(e)[:200]}", flush=True)
            time.sleep(delay)
    raise last_exc  # 不会执行到这里


def create_run_dir(base_output_dir: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(base_output_dir) / f"sr_{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _save_phase_state(run_dir, state: dict):
    state_file = Path(run_dir) / PHASE_STATE_FILE
    state_file.write_text(json.dumps(state, ensure_ascii=False, default=str, indent=2), encoding="utf-8")


def load_phase_state(run_dir) -> dict:
    state_file = Path(run_dir) / PHASE_STATE_FILE
    if not state_file.exists():
        return {}
    return json.loads(state_file.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────
# Step1: MiMo 解析原视频结构 + Grok 推理迁移 → 15s 分镜脚本 JSON
# ─────────────────────────────────────────────────────────
def run_step1(
    video_path: str,
    product_name: str,
    selling_points: str,
    product_image_paths: list,
    mimo_api_key: str = None,
    output_dir: str = None,
    reasoning_engine: str = "grok",
    reject_reason: str = "",
) -> dict:
    """Step1: 两段式分镜生成（支持断点重试）

    1a. MiMo 解析原视频 → 原视频卖货结构 JSON（保留原时长，标注卖货节点）
    1b. Grok 推理迁移 → 15s 压缩分镜 JSON（原结构 + 新产品 → 15s 分镜）

    断点：
    - `mimo_structure.json` 存在 → 跳过 1a
    - `storyboard.json` 存在 → 跳过 1b

    Returns:
        {
            "storyboard": [...],
            "storyboard_json": str(path),
            "mimo_raw": str(path),       # 原视频结构 JSON 路径
            "grok_raw": str(path),       # Grok 推理原始输出路径
        }
    """
    _engine_label = "GPT" if (reasoning_engine or "grok").lower() == "gpt" else "Grok"
    print("\n" + "=" * 60)
    print(f"Step1: MiMo 解析原视频结构 → {_engine_label} 推理迁移 15s 分镜")
    print("=" * 60)

    if not Path(video_path).exists():
        raise FileNotFoundError(f"原视频不存在: {video_path}")
    imgs = [p for p in (product_image_paths or []) if p and Path(p).exists()]
    if not imgs:
        raise RuntimeError("至少需要1张产品图")

    out_dir = Path(output_dir) if output_dir else Path("outputs/structural_replica/step1")
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── 1a. MiMo 解析原视频结构（断点：mimo_structure.json）──
    mimo_structure_path = out_dir / "mimo_structure.json"
    if mimo_structure_path.exists():
        print(f"  [断点] 跳过 MiMo 解析，复用 {mimo_structure_path.name}")
        orig_structure = json.loads(mimo_structure_path.read_text(encoding="utf-8"))
    else:
        orig_structure = _step1a_mimo_parse_video(
            video_path=video_path,
            out_dir=out_dir,
            mimo_api_key=mimo_api_key,
        )

    # ── 1b. 推理迁移（断点：storyboard.json + 对应引擎 raw）──
    storyboard_path = out_dir / "storyboard.json"
    raw_path_name = "gpt_raw.json" if (reasoning_engine or "grok").lower() == "gpt" else "grok_raw.json"
    grok_raw_path = out_dir / raw_path_name
    if storyboard_path.exists() and grok_raw_path.exists():
        print(f"  [断点] 跳过推理迁移，复用 {storyboard_path.name}")
        storyboard = json.loads(storyboard_path.read_text(encoding="utf-8"))
    else:
        storyboard = _step1b_grok_transfer_structure(
            orig_structure=orig_structure,
            product_name=product_name,
            selling_points=selling_points,
            product_image_paths=imgs,
            out_dir=out_dir,
            reasoning_engine=reasoning_engine,
            reject_reason=reject_reason,
        )

    print(f"  ✓ Shot 数量: {len(storyboard)}")
    print(f"  ✓ 保存: {storyboard_path}")
    return {
        "storyboard": storyboard,
        "storyboard_json": str(storyboard_path),
        "mimo_raw": str(mimo_structure_path),
        "grok_raw": str(grok_raw_path),
    }


def _step1a_mimo_parse_video(video_path: str, out_dir: Path, mimo_api_key: str = None) -> list:
    """1a. MiMo 解析原视频卖货结构（保留原时长，标注卖货节点）"""
    from openai import OpenAI
    from pipeline.step1_mimo_parse import _file_to_base64
    from config import settings

    print("\n  [1a] MiMo 解析原视频结构 ...")
    api_key = mimo_api_key or settings.MIMO_API_KEY
    base_url = settings.MIMO_BASE_URL
    model = settings.TEXT_MODEL_MIMO
    if not api_key:
        raise RuntimeError("MIMO_API_KEY 未配置")

    system_prompt = (
        "你是商业短视频结构分析专家。输入：原视频。\n"
        "任务：解析原视频的卖货结构，输出完整分镜脚本（保留原视频时长，不压缩）。\n\n"
        "每个 Shot 必须包含以下字段：\n"
        "- shot: 序号\n"
        "- 时间段: {开始时间, 结束时间}（格式 00:00.00）\n"
        "- 场景环境: {场景, 背景细节, 光影设定}\n"
        "- 人物与产品: {人物, 产品}\n"
        "- 画面内容: 详细画面描述\n"
        "- 人物动作: 具体动作描述\n"
        "- 产品动作: 产品展示方式\n"
        "- 景别: 特写/近景/中景/全景\n"
        "- 构图: 画面构图方式\n"
        "- 运镜: 推/拉/摇/移/固定\n"
        "- 光线: 光线描述\n"
        "- 画面风格: 风格描述\n"
        "- 环境元素: 环境物品\n"
        "- 道具: 道具列表\n"
        "- 音效: 背景音效\n"
        "- 口播: 该时段口播文案（原文逐字记录）\n"
        "- 声音语气: {语速, 语调, 重音, 停顿, 情绪, 呼吸感}\n"
        "- 卖货节点: Hook/痛点/产品出场/卖点证明/转化钩子/其他\n\n"
        "在分镜数组末尾追加一个结构摘要对象（非 Shot）：\n"
        "{\n"
        '  "type": "structure_summary",\n'
        '  "整体卖货逻辑链": "从 Hook 到转化的完整逻辑描述",\n'
        '  "核心卖点提取": ["卖点1", "卖点2"],\n'
        '  "节奏特征": "剪辑节奏描述",\n'
        '  "目标人群": "推断的目标人群"\n'
        "}\n\n"
        "只输出一个 JSON 数组（分镜对象 + 末尾摘要对象），禁止输出 Markdown/解释。"
    )

    video_b64 = _file_to_base64(video_path, "video/mp4")
    content_list = [{
        "type": "video_url",
        "video_url": {"url": video_b64},
        "fps": 1.0,
        "media_resolution": "default",
    }, {
        "type": "text",
        "text": "请解析这个原视频的卖货结构，输出完整分镜脚本（保留原时长，不压缩），末尾追加结构摘要对象。",
    }]

    print(f"  → 原视频: {Path(video_path).name}")
    print(f"  → 调用 {model} ...")

    # 加超时（600s）+ 自动重试（Cloudflare 524 等可重试错误），避免上游故障直接失败
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=600.0)
    completion = _call_with_retry(
        client, client.chat.completions.create,
        label=f"MiMo({model})",
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content_list},
        ],
        max_completion_tokens=16384,
        timeout=600.0,
    )
    raw_content = completion.choices[0].message.content or ""
    finish_reason = getattr(completion.choices[0], "finish_reason", None)
    print(f"  ✓ MiMo 完成 finish_reason={finish_reason}, 长度 {len(raw_content)}")

    if finish_reason == "length":
        # 截断不一定是错误：如果已经包含完整的 JSON 数组结构，可以尝试解析使用
        # 仅当无法解析为 JSON 时才报错
        try:
            stripped = raw_content.strip()
            if stripped.endswith("]"):
                json.loads(stripped)
                print(f"  ⚠ finish_reason=length 但响应已含完整 JSON，尝试使用")
            else:
                # 尝试补全被截断的 JSON
                last_bracket = stripped.rfind("}")
                if last_bracket > 0:
                    candidate = stripped[:last_bracket + 1] + "\n]"
                    json.loads(candidate)
                    raw_content = candidate
                    print(f"  ⚠ finish_reason=length，已自动补全 JSON 末尾")
                else:
                    raise RuntimeError("MiMo 响应被截断且无法补全为有效 JSON")
        except json.JSONDecodeError:
            raise RuntimeError("MiMo 响应被截断（finish_reason=length），max_completion_tokens=16384 仍不足，请精简提示词")

    # 保存原始输出
    raw_path = out_dir / "mimo_raw.json"
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump({"content": raw_content, "finish_reason": finish_reason}, f, ensure_ascii=False, indent=2)

    # 提取 JSON 数组
    structure = _extract_json_array(raw_content)
    if not structure:
        print("  ⚠ 首次提取失败，重试...")
        retry_text = (
            "你上次的输出格式不正确。请只输出一个 JSON 数组（以 [ 开头、] 结尾），"
            "不要输出任何 Markdown 代码块标记、解释或分析。请重新输出完整的分镜脚本 JSON 数组。"
        )
        completion2 = _call_with_retry(
            client, client.chat.completions.create,
            label=f"MiMo({model})-retry",
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content_list},
                {"role": "assistant", "content": raw_content},
                {"role": "user", "content": retry_text},
            ],
            max_completion_tokens=8192,
            timeout=600.0,
        )
        raw_content = completion2.choices[0].message.content or ""
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump({"content": raw_content, "retry": True}, f, ensure_ascii=False, indent=2)
        structure = _extract_json_array(raw_content)

    if not structure:
        raise RuntimeError("MiMo 解析失败：无法从输出中提取 JSON 数组")

    # 保存结构化结果（供 Grok 使用）
    mimo_structure_path = out_dir / "mimo_structure.json"
    with open(mimo_structure_path, "w", encoding="utf-8") as f:
        json.dump(structure, f, ensure_ascii=False, indent=2)
    print(f"  ✓ 保存原视频结构: {mimo_structure_path.name} ({len(structure)} 项)")
    return structure


def _step1b_grok_transfer_structure(
    orig_structure: list,
    product_name: str,
    selling_points: str,
    product_image_paths: list,
    out_dir: Path,
    reasoning_engine: str = "grok",
    reject_reason: str = "",
) -> list:
    """1b. 推理迁移到 15s 分镜（支持 Grok / GPT 路由选择）

    reasoning_engine:
      - "grok" (默认): 使用 SR_GROK_* 配置（hub.nocannobb.com 网关，grok-4.6）
      - "gpt": 使用 GPT55_* 配置（gpt-6.1-sol）

    reject_reason:
      - 审核驳回时的修改意见，会注入到 system prompt 指导 LLM 调整分镜
    """
    from openai import OpenAI
    from pipeline.step1_mimo_parse import _file_to_base64
    from config import settings

    engine = (reasoning_engine or "grok").lower()
    if engine == "gpt":
        api_key = settings.GPT55_API_KEY
        base_url = settings.GPT55_BASE_URL
        model = "gpt-6.1-sol"
        engine_label = "GPT"
        if not api_key:
            raise RuntimeError("GPT55_API_KEY 未配置")
    else:
        api_key = settings.SR_GROK_API_KEY
        base_url = settings.SR_GROK_BASE_URL
        model = settings.SR_GROK_MODEL
        engine_label = "Grok"
        if not api_key or api_key.startswith("xai-"):
            raise RuntimeError("SR_GROK_API_KEY 未配置")

    print(f"\n  [1b] {engine_label} 推理迁移到 15s 分镜 ...")

    system_prompt = (
        "你是商业短视频结构迁移专家。\n"
        "输入：原视频结构 JSON + 新产品图 + 新产品名 + 卖点描述。\n"
        "任务：把原视频卖货结构迁移到新产品，压缩到 15 秒版本。\n\n"
        "⚠️ 时长硬约束：\n"
        "- 总时长必须为 15 秒，最后一个 Shot 的结束时间不得大于 15.0s\n"
        "- 推荐分 6-8 个 Shot，每个 Shot 1.5-2.5 秒，总和恰好 15.0s\n"
        "- 禁止输出超过 15.0s 的内容\n"
        "- 时间段字段必须从 0.0s 开始，最后一个 Shot 必须以 15.0s 结束\n\n"
        "迁移规则：\n"
        "1. 保留原视频核心卖货结构：Hook（0-3s 痛点/反差）→ 产品出场（3-6s）→ 核心卖点证明（6-11s）→ 转化钩子（11-15s）\n"
        "2. 合并冗余 Shot，保留最关键的卖货节点\n"
        "3. 口播必须能在对应 Shot 时长内说完（按 4 字/秒估算）\n"
        "4. 产品字段必须使用上述新产品名称\n"
        "5. 卖点优先使用用户提供的卖点描述，未提供时基于产品图可见信息提取，禁止虚构\n"
        "6. 人物动作/产品动作需适配新产品特征（基于产品图观察）\n\n"
        "输出字段（每个 Shot）：\n"
        "shot, 时间段{开始时间,结束时间}, 场景, 人物, 产品, 画面内容, 人物动作, "
        "产品动作, 景别, 构图, 运镜, 光线, 画面风格, 环境元素, 道具, 音效, 口播, "
        "声音语气{语速,语调,重音,停顿,情绪,呼吸感}\n\n"
        "只输出一个 JSON 数组，禁止输出 Markdown、解释、分析过程。"
    )

    # 构造多模态输入：产品图 + 原结构文本 + 新产品信息
    # 压缩产品图避免请求体过大触发网关 504（原 9MB+7MB 图 base64 后 ~21MB，网关超时）
    from backend.services.image2_client import _compress_image_bytes
    content_list = []
    for img_path in product_image_paths:
        raw = Path(img_path).read_bytes()
        compressed, mime = _compress_image_bytes(raw, max_side=1024, quality=85, threshold=300_000)
        img_b64 = f"data:{mime};base64,{base64.b64encode(compressed).decode()}"
        content_list.append({"type": "image_url", "image_url": {"url": img_b64}})

    # 分离分镜和摘要
    shots = [s for s in orig_structure if not isinstance(s, dict) or s.get("type") != "structure_summary"]
    summary = [s for s in orig_structure if isinstance(s, dict) and s.get("type") == "structure_summary"]
    summary_text = json.dumps(summary[0], ensure_ascii=False, indent=2) if summary else "（无摘要）"
    shots_text = json.dumps(shots, ensure_ascii=False, indent=2)

    user_text = (
        f"新产品名称：{product_name}\n"
        f"新产品卖点描述：{selling_points}\n\n"
        "=== 原视频卖货结构（分镜脚本）===\n"
        f"{shots_text}\n\n"
        "=== 原视频结构摘要 ===\n"
        f"{summary_text}\n\n"
        "请基于上述原视频卖货结构，结合新产品图与卖点，迁移生成 15s 压缩分镜脚本。\n"
        "保留原视频的核心卖货逻辑链（Hook → 产品出场 → 卖点证明 → 转化钩子），"
        "合并冗余 Shot，适配新产品特征。\n"
    )
    # 注入审核驳回的修改意见（重做 Phase1 时）
    if reject_reason:
        user_text += (
            "\n========================================\n"
            "⚠️ 审核修改意见（必须严格遵循，调整分镜脚本）\n"
            "========================================\n"
            f"{reject_reason}\n\n"
            "→ 上述修改意见来自人工审核，必须在本版分镜脚本中落实。\n"
            "→ 如果意见针对特定 Shot，只调整对应 Shot，其他 Shot 保持稳定。\n"
            "→ 如果意见针对整体节奏/口播/结构，全面调整分镜。\n"
            "→ 调整时仍须遵守 15s 总时长硬约束。\n"
        )
    user_text += "只输出一个 JSON 数组。"
    content_list.append({"type": "text", "text": user_text})

    print(f"  → 产品图: {[Path(p).name for p in product_image_paths]}")
    print(f"  → 原结构: {len(shots)} 个 Shot")
    if reject_reason:
        print(f"  → 修改意见: {reject_reason[:80]}{'...' if len(reject_reason) > 80 else ''}")
    print(f"  → 调用 {model} ...")

    # 流式调用：网关持续转发 token，避免 gpt-5.5 推理时间长触发 504 Gateway Time-out
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=600.0)
    raw_content, finish_reason, usage = _call_stream_with_retry(
        client,
        label=f"Grok({model})",
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content_list},
        ],
        max_completion_tokens=8192,
        timeout=600.0,
    )
    print(f"  ✓ {engine_label} 完成 finish_reason={finish_reason}, 长度 {len(raw_content)}")
    if usage:
        print(f"  → tokens: prompt={getattr(usage, 'prompt_tokens', '?')}, "
              f"completion={getattr(usage, 'completion_tokens', '?')}")

    if finish_reason == "length":
        raise RuntimeError(f"{engine_label} 响应被截断（finish_reason=length），请增大 max_completion_tokens 或精简提示词")

    # 保存原始输出（按引擎区分文件名，避免断点互相覆盖）
    raw_path_name = "gpt_raw.json" if engine == "gpt" else "grok_raw.json"
    grok_raw_path = out_dir / raw_path_name
    with open(grok_raw_path, "w", encoding="utf-8") as f:
        json.dump({"content": raw_content, "finish_reason": finish_reason, "engine": engine}, f, ensure_ascii=False, indent=2)

    # 提取 JSON 数组
    storyboard = _extract_json_array(raw_content)
    if not storyboard:
        print("  ⚠ 首次提取失败，重试...")
        retry_text = (
            "你上次的输出格式不正确。请只输出一个 JSON 数组（以 [ 开头、] 结尾），"
            "不要输出任何 Markdown 代码块标记、解释或分析。请重新输出完整的分镜脚本 JSON 数组。"
        )
        completion2_content, _, _ = _call_stream_with_retry(
            client,
            label=f"Grok({model})-retry",
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content_list},
                {"role": "assistant", "content": raw_content},
                {"role": "user", "content": retry_text},
            ],
            max_completion_tokens=8192,
            timeout=600.0,
        )
        raw_content = completion2_content
        with open(grok_raw_path, "w", encoding="utf-8") as f:
            json.dump({"content": raw_content, "retry": True}, f, ensure_ascii=False, indent=2)
        storyboard = _extract_json_array(raw_content)

    if not storyboard:
        raise RuntimeError("Grok 迁移失败：无法从输出中提取 JSON 数组")

    # 保存最终分镜
    storyboard_path = out_dir / "storyboard.json"
    with open(storyboard_path, "w", encoding="utf-8") as f:
        json.dump(storyboard, f, ensure_ascii=False, indent=2)
    print(f"  ✓ 保存 15s 分镜: {storyboard_path.name} ({len(storyboard)} 个 Shot)")
    return storyboard


def _extract_json_array(content: str):
    """从 MiMo 返回中提取 JSON 数组"""
    if not content:
        return None
    fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", content, re.I)
    block = fenced[0].strip() if fenced else None
    if not block:
        m = re.search(r"\[[\s\S]*\]", content)
        if not m:
            return None
        block = m.group(0)

    # 修复常见问题
    block = re.sub(r"(?<=:)\s*0+(\d+\.\d+)", r"\1", block)
    block = re.sub(r"(?<=:)\s*0+(\d+)(?=[,\]\s}])", r"\1", block)

    try:
        arr = json.loads(block)
    except json.JSONDecodeError:
        start = block.find("[")
        end = block.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                arr = json.loads(block[start : end + 1])
            except json.JSONDecodeError:
                # json_repair 容错解析（修复 MiMo 输出的多余 } 等结构错误）
                try:
                    import json_repair
                    arr = json_repair.loads(block[start : end + 1])
                    print(f"  → json_repair 容错解析成功，{len(arr) if isinstance(arr, list) else 'N/A'} 项")
                except Exception:
                    return None
        else:
            return None

    return arr if isinstance(arr, list) else None


# ─────────────────────────────────────────────────────────
# Step3: Image2 生成 4K 故事板海报
# ─────────────────────────────────────────────────────────
def run_step3(
    storyboard_json: str,
    product_image_paths: list,
    product_name: str,
    selling_points: str,
    output_dir: str = None,
) -> dict:
    """Step3: 调用 Image2 生成 4K 故事板海报

    Returns:
        {"poster_path": str}
    """
    from backend.services.image2_client import image2_client

    print("\n" + "=" * 60)
    print("Step3: Image2 生成 4K 故事板海报")
    print("=" * 60)

    if not Path(storyboard_json).exists():
        raise FileNotFoundError(f"分镜 JSON 不存在: {storyboard_json}")
    imgs = [p for p in (product_image_paths or []) if p and Path(p).exists()]
    if not imgs:
        raise RuntimeError("至少需要1张产品图")

    out_dir = Path(output_dir) if output_dir else Path("outputs/structural_replica/step3")
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(storyboard_json, "r", encoding="utf-8") as f:
        storyboard = json.load(f)
    print(f"  → Shot 数: {len(storyboard)}")

    prompt = _build_poster_prompt(storyboard, product_name, selling_points)
    print(f"  → 提示词长度: {len(prompt)} 字符")

    images_b64 = []
    for p in imgs:
        data = Path(p).read_bytes()
        b64 = base64.b64encode(data).decode()
        images_b64.append(f"data:image/png;base64,{b64}")
        print(f"  → 产品图: {Path(p).name} ({len(data)/1024:.1f} KB)")

    print(f"  → 调用 Image2 ...")

    async def _do_image2():
        result = await image2_client.image_edit_sync(
            prompt=prompt,
            images=images_b64,
            size="1536x1024",
            n=1,
            response_format="b64_json",
            output_format="png",
        )
        result_url = image2_client._get_result_image(result)
        poster_path = out_dir / "storyboard_poster.png"
        await _save_image_result(result_url, poster_path)
        return poster_path

    # 同步函数中调用 async：用新建事件循环避免与父进程冲突
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 子进程通常不会有此问题；若真的在已运行 loop 中调用，需 nest_asyncio
            import nest_asyncio
            nest_asyncio.apply()
            poster_path = loop.run_until_complete(_do_image2())
        else:
            poster_path = loop.run_until_complete(_do_image2())
    except RuntimeError:
        poster_path = asyncio.run(_do_image2())

    size_kb = poster_path.stat().st_size / 1024
    print(f"  ✓ 保存: {poster_path} ({size_kb:.1f} KB)")
    return {"poster_path": str(poster_path)}


def _build_poster_prompt(storyboard: list, product_name: str, selling_points: str) -> str:
    """构造故事板海报提示词（与 _gen_storyboard_poster.py 一致）"""
    storyboard_json_str = json.dumps(storyboard, ensure_ascii=False, indent=2)
    selling_short = selling_points[:80] if selling_points else ""
    prompt = f"""生成一张"电商短视频分镜故事板海报"，采用专业影视分镜 + 带货脚本结构设计。
整体为一张横向信息海报，风格为"商业短视频分镜分析表 + 电商商品展示脚本 + 声音导演表"。

画布比例：16:9 横版，4K 高清画质（3840x2160）
强制三段式栅格布局：
① Header 顶部区域（固定高度 18%）
② Main 中间主体区域（固定高度 64%）
③ Footer 底部区域（固定高度 18%）

核心约束：
1. 三个区域必须严格上下分层，不可重叠
2. 所有元素必须限制在各自区域边界内
3. 必须存在明显视觉分割线
4. 不允许任何文字/图片跨区域

HEADER 内容：
主标题："电商短视频分镜故事板海报"
副标题："商业短视频分镜分析表 + 电商商品展示脚本 + 声音导演表"
参数信息："时长：15秒" | "镜头：{len(storyboard)}" | "类型：带货短视频"
风格标签："商业提案风" "电商广告分镜板" "Pitch Deck Storyboard" "Voice Direction"

MAIN 区域拆分为左右结构：
左侧（70%宽度）：分镜网格 Shot Grid（{len(storyboard)} 个 Shot，2 行 3 列网格）
右侧（30%宽度）：人物 + 产品 + 声音导演表

每个 Shot 卡片三段式：
A. 图像区（55%）：分镜小图 + 左上角 Shot 编号
B. 画面信息区（30%）：场景/景别/画面/人物动作/产品动作/构图/运镜/口播
C. 声音语气精简条（15%）：语速/语调/重音/停顿/情绪/呼吸感

右侧模块：
- 上方 35%：人物展示
- 中间 30%：产品展示（使用上传的 {product_name} 产品多角度参考图）
- 下方 35%：声音导演表（表格形式，{len(storyboard)} 行 × 7 列）

产品卖点：{selling_short}

FOOTER 5 个模块：视觉设计 / 拍摄设备 / 节奏结构 / 音乐建议 / 风格标签

@图片1：产品参考图（{product_name} 多角度白底图）
分镜脚本 JSON：

{storyboard_json_str}

视觉风格：商业提案风 / 电商广告分镜板 / 轻工业设计感 / 信息密度高 / Pitch Deck Storyboard

强制禁止：跨区域 / 文字压图 / 图片压文字 / 区域重叠 / 自由散点排版 / 删减字段 / 省略声音语气
"""
    return prompt


async def _save_image_result(result_url_or_b64: str, output_path: Path) -> None:
    """保存 Image2 返回的图片（兼容 URL / b64_json / data URI）"""
    s = result_url_or_b64 or ""
    if s.startswith("data:"):
        _, b64_part = s.split(",", 1)
        output_path.write_bytes(base64.b64decode(b64_part))
        return
    if not s.startswith("http") and len(s) > 200:
        output_path.write_bytes(base64.b64decode(s))
        return
    if s.startswith("http") and len(s) < 8192:
        import httpx
        async with httpx.AsyncClient(timeout=180) as hc:
            r = await hc.get(s)
            r.raise_for_status()
            output_path.write_bytes(r.content)
        return
    try:
        output_path.write_bytes(base64.b64decode(s))
    except Exception:
        raise RuntimeError(f"无法识别的图片返回格式，len={len(s)}, prefix={s[:40]!r}")


# ─────────────────────────────────────────────────────────
# Step4: H3 标准 API 生成 15s 成品视频（满血 2K）
# ─────────────────────────────────────────────────────────
H3_API_KEY = os.environ.get("RH_API_KEY", "cee93c2be1a34ca88bff5aafa8d15902")
H3_BASE_URL = "https://www.runninghub.cn"


async def _h3_upload_image(client, img_path: str) -> str:
    """上传图片到 RunningHub，返回 download_url"""
    p = Path(img_path)
    print(f"  → 上传图片: {p.name} ({p.stat().st_size / 1024:.1f} KB)")
    with p.open("rb") as f:
        resp = await client.post(
            f"{H3_BASE_URL}/openapi/v2/media/upload/binary",
            headers={"Authorization": f"Bearer {H3_API_KEY}"},
            files={"file": (p.name, f)},
            timeout=120.0,
        )
    resp.raise_for_status()
    data = resp.json()
    value = data.get("data") or {}
    resource = value.get("download_url") or value.get("url") or value.get("fileName")
    if not resource:
        raise RuntimeError(f"图片上传失败: {data}")
    print(f"  ✓ 资源地址: {resource[:80]}...")
    return resource


async def _h3_submit_task(client, prompt: str, image_urls: list, duration: str = "15") -> str:
    """提交 H3 标准任务，返回 taskId"""
    # 🔴 2026-10-09 用户指令"视频严格杜绝走 RunningHub"。这条腿自己拼 HTTP（下面打 hailuo-h3 那个端点）、
    #    不经 submit_task 那个咽喉，而且被 app.py 的动态 import 拉进了 8090 ⇒ 判据要跟着腿走，不跟着目录走。
    #    函数内 import：根级模块的 sys.path 顺序不稳，别把守卫自己变成 ImportError。
    from backend.services.runninghub_client import rh_video_guard
    rh_video_guard()
    print(f"  → 提交 H3 任务: resolution=2K, duration={duration}s, ratio=adaptive, {len(image_urls)}张参考图")
    print(f"  → prompt 长度: {len(prompt)} 字符")
    body = {
        "prompt": prompt,
        "imageUrls": image_urls,
        "videoUrls": [],
        "audioUrls": [],
        "resolution": "2K",
        "duration": duration,
        "ratio": "adaptive",
    }
    resp = await client.post(
        f"{H3_BASE_URL}/openapi/v2/minimax/hailuo-h3/multimodal-to-video",
        headers={"Authorization": f"Bearer {H3_API_KEY}", "Content-Type": "application/json"},
        json=body,
        timeout=60.0,
    )
    resp.raise_for_status()
    data = resp.json()
    task_id = data.get("taskId")
    if not task_id:
        raise RuntimeError(f"H3 任务提交失败: {data}")
    print(f"  ✓ taskId: {task_id}")
    return task_id


async def _h3_poll_and_download(client, task_id: str, output_path: Path, timeout: int = 3600) -> str:
    """轮询 H3 任务直到完成并下载视频，返回本地路径"""
    import time
    print(f"  → 轮询任务 {task_id}（30秒间隔，最长{timeout}s）")
    start = time.time()
    fail_count = 0

    while time.time() - start < timeout:
        await asyncio.sleep(30)
        elapsed = int(time.time() - start)

        try:
            resp = await client.post(
                f"{H3_BASE_URL}/openapi/v2/query",
                headers={"Authorization": f"Bearer {H3_API_KEY}", "Content-Type": "application/json"},
                json={"taskId": task_id},
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            fail_count = 0
        except Exception as e:
            fail_count += 1
            print(f"  [{elapsed}s] 查询异常({fail_count}/3): {type(e).__name__}: {e}")
            if fail_count >= 3:
                raise RuntimeError(f"H3 查询连续失败 3 次: {e}")
            continue

        status = data.get("status", "UNKNOWN")
        err = data.get("errorMessage", "")
        print(f"  [{elapsed}s] 状态: {status} {('(' + err + ')') if err else ''}")

        if status == "SUCCESS":
            results = data.get("results") or []
            for item in results:
                url = item.get("url", "")
                otype = item.get("outputType", "")
                if otype == "mp4" or url:
                    print(f"  → 下载视频到 {output_path.name}")
                    vresp = await client.get(url, timeout=300.0)
                    vresp.raise_for_status()
                    output_path.write_bytes(vresp.content)
                    size_mb = output_path.stat().st_size / 1024 / 1024
                    print(f"  ✓ 下载完成: {size_mb:.2f} MB")
                    return str(output_path)
            raise RuntimeError(f"H3 任务成功但无视频结果: {data}")

        if status == "FAILED":
            raise RuntimeError(f"H3 任务失败: {err or data}")

    raise RuntimeError(f"H3 任务超时（{timeout}s）")


def run_step4(
    storyboard_image_path: str,
    storyboard_json: str,
    product_image_paths: list,
    product_name: str,
    output_dir: str = None,
    duration: str = "15",
    timeout: int = 3600,
    video_model: str = "h3",
) -> dict:
    """Step4: 视频生成（H3 标准 API 或 SEEDANCE2.0 浏览器自动化）

    Args:
        video_model: "h3"（默认，2K 满血）| "seedance"（seedance-2.0-renlian，weeksir.com 浏览器自动化）

    Returns:
        {
            "status": str,
            "video_url": str | None,
            "local_video": str | None,
        }
    """
    model = (video_model or "h3").lower()
    print("\n" + "=" * 60)
    if model == "seedance":
        print("Step4: SEEDANCE2.0 视频生成（weeksir.com 浏览器自动化，seedance-2.0-renlian）")
    else:
        print("Step4: H3 标准 API 生成 2K 满血视频")
    print("=" * 60)

    if not Path(storyboard_json).exists():
        raise FileNotFoundError(f"分镜 JSON 不存在: {storyboard_json}")
    imgs = [p for p in (product_image_paths or []) if p and Path(p).exists()]
    if not imgs:
        raise RuntimeError("至少需要1张产品图")

    out_dir = Path(output_dir) if output_dir else Path("outputs/structural_replica/step4")
    out_dir.mkdir(parents=True, exist_ok=True)

    # 读取分镜 JSON
    with open(storyboard_json, "r", encoding="utf-8") as f:
        storyboard = json.load(f)

    # ── SEEDANCE2.0 分支：weeksir.com 浏览器自动化 ──
    if model == "seedance":
        from pipeline.step5_video_generate import run as run_seedance
        local_video = run_seedance(
            video_path="",  # 结构性复刻不需要原视频
            product_image_path=imgs[0],
            storyboard_image_path=storyboard_image_path,
            product_name=product_name,
            output_dir=str(out_dir),
            headless=False,
            wait_for_result=True,
            timeout=timeout if timeout and timeout > 600 else 1200,
            aspect_ratio="9:16",
            custom_prompt=None,
            duration=duration + "s" if duration and not str(duration).endswith("s") else (duration or "15s"),
            resolution="720p",
            storyboard_json=storyboard,
            product_image_paths=imgs,
        ) or {}
        local_path = local_video.get("local_video") or local_video.get("video_path")
        if not local_path:
            raise RuntimeError(f"SEEDANCE2.0 未返回视频路径: {local_video}")
        size_mb = Path(local_path).stat().st_size / 1024 / 1024
        print(f"  ✓ 状态: success（SEEDANCE2.0）")
        print(f"  ✓ 本地视频: {local_path} ({size_mb:.2f} MB)")
        return {"status": "success", "video_url": local_video.get("video_url"), "local_video": local_path}

    # ── H3 分支：H3 标准 API ──
    h3_prompt = _build_h3_prompt(storyboard, product_name)
    prompt_path = out_dir / "h3_prompt.txt"
    prompt_path.write_text(h3_prompt, encoding="utf-8")
    print(f"  → H3 prompt 长度: {len(h3_prompt)} 字符")

    video_path = out_dir / "final_video.mp4"

    async def _do_h3():
        async with httpx.AsyncClient() as client:
            # 上传产品图（最多9张）
            image_urls = []
            for img in imgs[:9]:
                url = await _h3_upload_image(client, img)
                image_urls.append(url)
            # 提交任务
            task_id = await _h3_submit_task(client, h3_prompt, image_urls, duration)
            # 轮询并下载
            local = await _h3_poll_and_download(client, task_id, video_path, timeout)
            return local

    # 同步函数中调用 async
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
            local_video = loop.run_until_complete(_do_h3())
        else:
            local_video = loop.run_until_complete(_do_h3())
    except RuntimeError:
        local_video = asyncio.run(_do_h3())

    size_mb = Path(local_video).stat().st_size / 1024 / 1024
    print(f"  ✓ 状态: success")
    print(f"  ✓ 本地视频: {local_video} ({size_mb:.2f} MB)")
    return {"status": "success", "video_url": None, "local_video": local_video}


def _build_video_prompt(storyboard: list) -> str:
    """构造视频生成提示词（用户提供的模板 + 分镜 JSON）"""
    slim_shots = []
    for s in storyboard:
        slim = {
            "shot": s.get("shot"),
            "时间": s.get("时间段"),
            "场景": s.get("场景"),
            "人物": s.get("人物"),
            "产品": s.get("产品"),
            "画面": s.get("画面内容"),
            "人物动作": s.get("人物动作"),
            "产品动作": s.get("产品动作"),
            "景别": s.get("景别"),
            "构图": s.get("构图"),
            "运镜": s.get("运镜"),
            "光线": s.get("光线"),
            "风格": s.get("画面风格"),
            "环境": s.get("环境元素"),
            "道具": s.get("道具"),
            "音效": s.get("音效"),
            "口播": s.get("口播"),
            "声音语气": s.get("声音语气"),
        }
        slim_shots.append(slim)
    storyboard_json_str = json.dumps(slim_shots, ensure_ascii=False, indent=2)
    return f"""请严格按照@图片1 故事板的分镜画面生成，不可自己更改故事板分镜画面，进行推理保持产品的一致性保持模特一致性,不要出现字幕！严格按照故事板的口播文字内容，进行口播！

人物高清原生毛孔，皮下血管纹理，自然鱼尾细纹，面部微汗光泽，原生通透皮肤，关闭过度磨皮，100%细节强度，完整保留发丝、皮肤与面料肌理，柔和侧光塑造面部立体骨骼，Canon EF85mm f/1.2镜头，胶片摄影质感，超写实真人肤质，无假面、无塑料皮肤，边缘清晰无锯齿模糊，超清4K极致细节

{storyboard_json_str}"""


def _build_h3_prompt(storyboard: list, product_name: str = "") -> str:
    """把分镜 JSON 编译成 H3 原生格式提示词

    输出格式：
      integrated_multimodal_description: [Shot 1] ... <d>[Chinese] ...</d>
      overall_soundscape: ...
      non_diegetic_music: ...
    """
    shots_desc = []
    soundscape_parts = []
    music_parts = []

    for i, s in enumerate(storyboard):
        sn = s.get("shot", i + 1)
        time_str = s.get("时间段", "")
        # 兼容 dict 格式：{"开始时间": "00:0.00", "结束时间": "00:1.50"}
        if isinstance(time_str, dict):
            time_str = time_str.get("开始时间", "") or ""
        # 解析开始时间（支持 "00:1.50" / "1.50" / "00:01.50" 等格式）
        start_time = ""
        if time_str:
            # 先尝试匹配 mm:ss.xx 格式
            m = re.search(r"(\d+):([\d.]+)", time_str)
            if m:
                start_time = f"At {int(m.group(1)) * 60 + float(m.group(2)):.2f}s，"
            else:
                m = re.match(r"([\d.]+)", time_str)
                if m:
                    start_time = f"At {float(m.group(1)):.2f}s，"

        scene = s.get("场景", "")
        person = s.get("人物", "")
        product = s.get("产品", "") or product_name
        visual = s.get("画面内容", "")
        person_action = s.get("人物动作", "")
        product_action = s.get("产品动作", "")
        shot_size = s.get("景别", "")
        composition = s.get("构图", "")
        camera_move = s.get("运镜", "")
        lighting = s.get("光线", "")
        style = s.get("画面风格", "")
        env = s.get("环境元素", "")
        props = s.get("道具", "")
        sfx = s.get("音效", "")
        voice = s.get("口播", "")
        tone = s.get("声音语气", {}) or {}

        # 构造 Shot 描述段
        parts = [f"[Shot {sn}]"]
        if i > 0 and start_time:
            parts[0] = f"[Shot {sn}] {start_time}"
        elif start_time:
            parts[0] = f"[Shot {sn}] {start_time}"

        # 画面风格 + 场景
        desc_bits = []
        if style:
            desc_bits.append(style)
        if scene:
            desc_bits.append(f"{scene}场景")
        if env:
            desc_bits.append(f"背景为{env}")
        if lighting:
            desc_bits.append(f"{lighting}")
        if desc_bits:
            parts.append("，".join(desc_bits) + "。")

        # 人物 + 动作
        if person and person != "无人物" and "仅" not in person:
            person_bit = f"画面中{person}"
            if person_action and person_action != "无":
                person_bit += f"正在{person_action}"
            parts.append(person_bit + "。")
        elif person_action and person_action != "无":
            parts.append(f"画面中{person_action}。")

        # 产品 + 动作
        if product:
            product_bit = f"产品「{product}」"
            if product_action and product_action != "无":
                # 避免重复"被"字
                act_text = product_action[1:] if product_action.startswith("被") else f"被{product_action}"
                product_bit += act_text
            parts.append(product_bit + "。")

        # 画面内容
        if visual:
            parts.append(visual + "。")

        # 镜头语言
        lens_bits = []
        if shot_size:
            lens_bits.append(shot_size)
        if composition:
            lens_bits.append(composition)
        if camera_move:
            lens_bits.append(f"镜头以{camera_move}")
        if lens_bits:
            parts.append("，".join(lens_bits) + "。")

        # 道具
        if props and props != "无":
            parts.append(f"画面中有{props}作为道具。")

        # 口播
        if voice and voice != "无" and voice != "听不清":
            # 构造语气描述
            speed = tone.get("语速", "")
            emotion = tone.get("情绪", "")
            speaker_desc = "主播"
            if person and person != "无人物" and "仅" not in person:
                speaker_desc = f"画面中的{person}"
            voice_intro = []
            if speed:
                voice_intro.append(f"以{speed}")
            if emotion:
                voice_intro.append(f"{emotion}的语气")
            voice_prefix = f"{speaker_desc}" + (f"{''.join(voice_intro)}" if voice_intro else "") + "说道："
            parts.append(f'{voice_prefix}<d>[Chinese] {voice}</d>')

        shots_desc.append(" ".join(parts))

        # 声音环境
        sfx_bits = []
        if sfx and sfx != "无":
            sfx_bits.append(sfx)
        if env and scene:
            sfx_bits.append(f"{scene}环境底噪")
        if sfx_bits:
            soundscape_parts.append("，".join(sfx_bits))

        # 音乐推测
        if emotion := tone.get("情绪", ""):
            music_parts.append(emotion)

    # 组装最终 prompt
    integrated = " ".join(shots_desc)
    # 第一行加图片对齐指令
    header = "For the target video, at 0.00 seconds into the target video, <Picture 1> is fully referenced.\n\n"
    integrated = header + integrated

    soundscape = "。".join(soundscape_parts) if soundscape_parts else "自然室内环境底噪持续，伴随人物动作产生的轻微摩擦声和脚步声。"
    if not soundscape.endswith("。"):
        soundscape += "。"

    # 音乐根据情绪推测
    if music_parts:
        emotions = "、".join(set(music_parts))
        music = f"轻快的电子鼓点与木吉他以中速节奏铺底，{emotions}时段节奏轻微增强，结尾以简洁上行音符收束。"
    else:
        music = "轻快的木吉他与短促电子鼓点以中速节奏铺底，结尾处节奏轻微增强，以简洁的上行音符收束。"

    return f"""integrated_multimodal_description: {integrated}

overall_soundscape: {soundscape}

non_diegetic_music: {music}"""


# ─────────────────────────────────────────────────────────
# 分阶段审核辅助
# ─────────────────────────────────────────────────────────
def _collect_review_images(run_dir: Path, phase: str) -> dict:
    review = {"phase": phase, "storyboard_json": None, "poster": None, "video": None}

    s1_dir = run_dir / "step1"
    if s1_dir.exists():
        sb = s1_dir / "storyboard.json"
        if sb.exists():
            review["storyboard_json"] = str(sb)

    s3_dir = run_dir / "step3"
    if s3_dir.exists():
        poster = s3_dir / "storyboard_poster.png"
        if poster.exists():
            review["poster"] = str(poster)

    if phase == "after_step4":
        s4_dir = run_dir / "step4"
        if s4_dir.exists():
            # 优先 final_video.mp4，其次任何 mp4
            v = s4_dir / "final_video.mp4"
            if not v.exists():
                v = next(s4_dir.glob("*.mp4"), None)
            if v and v.exists():
                review["video"] = str(v)

    return review


# ─────────────────────────────────────────────────────────
# Phase1 = Step1 + Step3（Step2 用户已上传产品图）
# ─────────────────────────────────────────────────────────
def run_phase_1(
    video_path: str,
    product_name: str,
    selling_points: str,
    product_image_paths: list,
    mimo_api_key: str = None,
    base_output_dir: str = "outputs",
    reasoning_engine: str = "grok",
    video_model: str = "h3",
    reject_reason: str = "",
) -> dict:
    """Phase1: Step1(分镜JSON) + Step3(故事板海报)，完成后暂停等待审核"""
    return _run_phase_1_impl(
        video_path=video_path,
        product_name=product_name,
        selling_points=selling_points,
        product_image_paths=product_image_paths,
        mimo_api_key=mimo_api_key,
        base_output_dir=base_output_dir,
        reasoning_engine=reasoning_engine,
        video_model=video_model,
        reject_reason=reject_reason,
        reuse_run_dir=None,
    )


def _run_phase_1_impl(
    video_path: str,
    product_name: str,
    selling_points: str,
    product_image_paths: list,
    mimo_api_key: str = None,
    base_output_dir: str = "outputs",
    reasoning_engine: str = "grok",
    video_model: str = "h3",
    reject_reason: str = "",
    reuse_run_dir: str = None,
) -> dict:
    """Phase1 内部实现（支持 redo 时复用原 run_dir + 修改意见注入）"""
    if not product_name or not product_name.strip():
        raise ValueError("必须提供产品名称")
    if not selling_points or not selling_points.strip():
        raise ValueError("必须提供产品卖点描述")

    if reuse_run_dir:
        run_dir = Path(reuse_run_dir)
        print(f"[Phase1] 重做运行目录: {run_dir}")
    else:
        run_dir = create_run_dir(base_output_dir)
        print(f"[Phase1] 运行目录: {run_dir}")
    print(f"[Phase1] 推理引擎: {reasoning_engine}")
    print(f"[Phase1] 视频模型: {video_model}")
    if reject_reason:
        print(f"[Phase1] 修改意见: {reject_reason}")

    # Step1
    s1_dir = run_dir / "step1"
    step1_result = run_step1(
        video_path=video_path,
        product_name=product_name,
        selling_points=selling_points,
        product_image_paths=product_image_paths,
        mimo_api_key=mimo_api_key,
        output_dir=str(s1_dir),
        reasoning_engine=reasoning_engine,
        reject_reason=reject_reason,
    )

    # Step3（直接用用户上传的产品图作为 Step2 的输出）
    s3_dir = run_dir / "step3"
    step3_result = run_step3(
        storyboard_json=step1_result["storyboard_json"],
        product_image_paths=product_image_paths,
        product_name=product_name,
        selling_points=selling_points,
        output_dir=str(s3_dir),
    )

    state = {
        "phase": "after_step3",
        "run_dir": str(run_dir),
        "video_path": video_path,
        "product_name": product_name,
        "selling_points": selling_points,
        "product_image_paths": product_image_paths,
        "mimo_api_key": mimo_api_key,
        "reasoning_engine": reasoning_engine,
        "video_model": video_model,
        "storyboard_json": step1_result["storyboard_json"],
        "poster_path": step3_result["poster_path"],
    }
    _save_phase_state(run_dir, state)

    review = _collect_review_images(run_dir, "after_step3")
    print(f"[Phase1] 完成，等待审核故事板海报。")
    return {"phase": "after_step3", "run_dir": str(run_dir), "review": review, "state": state}


# ─────────────────────────────────────────────────────────
# Phase2 = Step4（生成视频）
# ─────────────────────────────────────────────────────────
def run_phase_2(run_dir: str) -> dict:
    """Phase2: Step4 视频生成"""
    state = load_phase_state(run_dir)
    if not state:
        raise RuntimeError(f"未找到 Phase1 状态: {run_dir}")

    video_model = (state.get("video_model") or "h3").lower()
    print(f"[Phase2] 视频生成，运行目录: {run_dir}")
    print(f"[Phase2] 视频模型: {video_model}")

    s4_dir = Path(run_dir) / "step4"
    s4_dir.mkdir(parents=True, exist_ok=True)

    result = run_step4(
        storyboard_image_path=state["poster_path"],
        storyboard_json=state["storyboard_json"],
        product_image_paths=state["product_image_paths"],
        product_name=state["product_name"],
        output_dir=str(s4_dir),
        duration="15",
        timeout=3600,
        video_model=video_model,
    )

    state["phase"] = "after_step4"
    state["video_result"] = {
        "status": result.get("status"),
        "video_url": result.get("video_url"),
        "local_video": result.get("local_video"),
    }
    _save_phase_state(Path(run_dir), state)

    review = _collect_review_images(Path(run_dir), "after_step4")
    print(f"[Phase2] 完成，状态: {result.get('status')}")
    return {"phase": "after_step4", "run_dir": run_dir, "review": review, "state": state}


# ─────────────────────────────────────────────────────────
# 重做 Phase1：重新生成故事板海报
# ─────────────────────────────────────────────────────────
def redo_phase_1(run_dir: str, reject_reason: str = "") -> dict:
    """重做 Phase1：保留原视频/产品图，重新调用 MiMo + Image2

    Args:
        run_dir: 原运行目录
        reject_reason: 审核驳回的修改意见，会传给 LLM 影响重新推理的分镜脚本
    """
    state = load_phase_state(run_dir)
    if not state:
        raise RuntimeError(f"未找到 Phase1 状态: {run_dir}")

    print(f"[Redo Phase1] 重做故事板，运行目录: {run_dir}")
    if reject_reason:
        print(f"[Redo Phase1] 修改意见: {reject_reason}")

    # 清空旧 step1/step3
    for sub in ("step1", "step3"):
        d = Path(run_dir) / sub
        if d.exists():
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    # redo 时复用原 run_dir（而非创建新目录）
    result = _run_phase_1_impl(
        video_path=state["video_path"],
        product_name=state["product_name"],
        selling_points=state["selling_points"],
        product_image_paths=state["product_image_paths"],
        mimo_api_key=state.get("mimo_api_key"),
        base_output_dir=str(Path(run_dir).parent),
        reasoning_engine=state.get("reasoning_engine", "grok"),
        video_model=state.get("video_model", "h3"),
        reject_reason=reject_reason,
        reuse_run_dir=run_dir,
    )
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["1", "2", "redo1"], default="1")
    parser.add_argument("--run-dir", default="")
    parser.add_argument("--video", default="outputs/product_multiangle/video(26).mp4")
    parser.add_argument("--product-name", default="泡脚桶")
    parser.add_argument("--selling-points", default="八层加厚，长效锁温，可以泡到小腿的三阴交和足三里，折叠设计，便携、好收纳")
    parser.add_argument("--product-images", nargs="+", default=[
        "outputs/product_multiangle/01_front.png",
        "outputs/product_multiangle/02_side_45.png",
        "outputs/product_multiangle/04_top.png",
    ])
    args = parser.parse_args()

    if args.phase == "1":
        r = run_phase_1(
            video_path=args.video,
            product_name=args.product_name,
            selling_points=args.selling_points,
            product_image_paths=args.product_images,
            base_output_dir="outputs",
        )
        print(json.dumps({"run_dir": r["run_dir"], "review": r["review"]}, ensure_ascii=False, indent=2))
    elif args.phase == "2":
        if not args.run_dir:
            print("--run-dir 必填")
            sys.exit(1)
        r = run_phase_2(args.run_dir)
        print(json.dumps({"run_dir": r["run_dir"], "review": r["review"]}, ensure_ascii=False, indent=2))
    elif args.phase == "redo1":
        if not args.run_dir:
            print("--run-dir 必填")
            sys.exit(1)
        r = redo_phase_1(args.run_dir)
        print(json.dumps({"run_dir": r["run_dir"], "review": r["review"]}, ensure_ascii=False, indent=2))
