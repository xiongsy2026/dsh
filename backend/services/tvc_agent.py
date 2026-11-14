"""
一键产品 TVC 流水线

3 步流程：
  Step1: 输入产品名+人群+广告主题+场景要求+画面比例+产品图 → gpt-6.1-sol 生成画布提示词
  Step2: 输入服装图+故事板提示词 → image2 生成 4K 故事板（3x4 十二宫格）
  Step3: 输入故事板图片+视频提示词+产品图 → RPA 走 SD2 生成成片

设计参考：toddler_chef/agent.py 的三步流水线模式
"""
import asyncio
import base64
import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from openai import OpenAI

from config import settings, BASE_DIR
from .image2_client import Image2Client


OUTPUT_DIR = BASE_DIR / "outputs" / "tvc"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 内存任务存储
_tasks: Dict[str, Dict[str, Any]] = {}


# ═══════════════════════════════════════
# 持久化：任务状态保存/加载（支持服务重启后断点续接）
# ═══════════════════════════════════════

def _state_file(task_id: str) -> Path:
    return OUTPUT_DIR / task_id / "_state.json"


def _persist_task(task_id: str) -> None:
    """把内存任务状态保存到磁盘"""
    task = _tasks.get(task_id)
    if not task_id:
        return
    try:
        sf = _state_file(task_id)
        sf.parent.mkdir(parents=True, exist_ok=True)
        # 去掉非可序列化字段
        safe = {k: v for k, v in task.items() if k != "_fut"}
        sf.write_text(json.dumps(safe, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    except Exception as e:
        print(f"[Persist] 保存任务 {task_id} 失败: {e}")


def _load_all_persisted() -> None:
    """服务启动时从磁盘恢复所有任务到内存

    - awaiting_review: 直接恢复，用户可继续审核
    - running: 检查是否有未完成的 phase，若有则恢复 awaiting_review（仅当产物存在）
      或标记 failed（无产物）
    - completed/failed: 原样恢复
    """
    if not OUTPUT_DIR.exists():
        return
    for sub in OUTPUT_DIR.iterdir():
        if not sub.is_dir():
            continue
        sf = sub / "_state.json"
        if not sf.exists():
            continue
        try:
            task = json.loads(sf.read_text(encoding="utf-8"))
            tid = task.get("task_id") or sub.name
            # running 任务：如果产物已存在，按 awaiting_review/failed 处理
            if task.get("status") == "running":
                if task.get("outputs", {}).get("storyboard_path") and not task.get("outputs", {}).get("video_path"):
                    # 故事板已生成但视频未生成 → 回到 awaiting_review
                    task["status"] = "awaiting_review"
                    task["phase"] = "after_storyboard"
                    task["step"] = "故事板已生成，等待审核（从磁盘恢复）"
                    task["review"] = {
                        "phase": "after_storyboard",
                        "storyboard_path": task["outputs"]["storyboard_path"],
                        "canvas_prompt": task["outputs"].get("canvas_prompt"),
                    }
                else:
                    task["status"] = "failed"
                    task["step"] = "失败：服务重启中断"
                    task["error"] = "服务重启导致任务中断"
            _tasks[tid] = task
            print(f"[Restore] 恢复 TVC 任务 {tid}: {task.get('status')}/{task.get('phase')}")
        except Exception as e:
            print(f"[Restore] 加载 {sub.name} 失败: {e}")


# 服务启动时自动加载历史任务
_load_all_persisted()


# ═══════════════════════════════════════
# Step 1: gpt-6.1-sol 生成画布提示词
# ═══════════════════════════════════════

SYSTEM_PROMPT_TEMPLATE = """你是一位资深广告创意总监+AIGC提示词工程师，专门为GPTImage2编写【{duration} TVC广告脚本分镜面板】提示词。

请先识别用户给定的产品图，确认产品品类、品牌调性、核心卖点、目标人群、使用场景与适合的广告美学，然后综合用户填写的所有输入（产品名、卖点、目标人群、场景要求、画面比例），直接输出一段可用于 GPT Image2 的完整提示词，用来生成一张{aspect_ratio}、4K高清、3x4共12格的专业广告脚本分镜面板。

12宫格需按"起、承、转、合、浓、余、印、章"的广告叙事结构展开：建立世界、埋下钩子、产品登场、人物互动情绪高潮、回味留白、产品Hero Shot、品牌落款。请确保12格风格高度统一。每格必须包含时间码、画面、台词/旁白、音效、转场五项内容，服装类需有分镜中模特出镜（不一定要全部画面都有模特），请结合服装推理，**时长硬性约束（必须严格遵守）**：
- 总时长严格 = {duration}（12 格 × {per_shot}/格 = {duration}）
- 每格时长严格 = {per_shot}，禁止某格长某格短
- 时间码严格按 {per_shot} 步长累加：第1格 [00:00.0-00:{per_shot}]，第2格 [00:{per_shot}-2×{per_shot}]，第3格 [2×{per_shot}-3×{per_shot}]... 第12格结束必须正好 = {duration}
- 第 12 格结束时间码必须正好 = {duration}
- 如选 30s 模型：必须用满 30s；如选 15s 模型：必须用满 15s
画面需写清镜位、动作与光线，台词累计3-4句，最后一句需与品牌slogan一致。生成的提示词必须要求产品严格还原参考图，logo、文字、配色100%一致不变形中文清晰无错，排版整齐，分隔线笔直，整体呈现高级TVC广告质感。注意：不要生成图片，只输出最终可复制使用的提示词。

重要：用户填写的卖点、产品名、目标人群、场景要求等信息必须原样采纳，不要删减或改写客户的输入。"""


def _normalize_product_images(params: Dict[str, Any]) -> List[str]:
    """从任务 params 中归一化出产品参考图列表。

    兼容三种历史格式：
      - product_image_paths: List[str]   （新格式，优先）
      - product_image_path:  str          （旧格式，单图）
      - 两者都没有：返回 []
    会去重并过滤掉不存在的路径（保留顺序）。
    """
    paths: List[str] = []
    seen = set()
    raw = params.get("product_image_paths")
    if isinstance(raw, list):
        for p in raw:
            if p and p not in seen:
                paths.append(p)
                seen.add(p)
    if not paths:
        single = params.get("product_image_path")
        if isinstance(single, str) and single and single not in seen:
            paths.append(single)
            seen.add(single)
    return paths


def _build_user_content_step1(
    product_image_paths: List[str],
    product_name: str,
    audience: str,
    ad_theme: str,
    scene_requirement: str,
    aspect_ratio: str,
    selling_points: str,
    duration: str = "15s",
) -> List[Dict[str, Any]]:
    """构造 Step1 的多模态用户消息（多张产品图 + 客户原始输入原样传递）。

    支持传入多张产品参考图（多角度/多细节），gpt-6.1-sol 会综合识别产品。
    客户填写的所有字段（产品名、卖点、目标人群、场景要求、画面比例）原样放在
    user 消息里，不做模板改写、不截断，由 GPT 自行识别产品并综合生成提示词。

    duration: TVC 总时长（如 "15s"/"20s"/"30s"），用于让 LLM 推理对应时长的故事板。
    """
    # 时长→每格时长映射（12 格固定面板）
    _DURATION_TO_PER_SHOT = {
        "5s": "0.42秒",
        "10s": "0.83秒",
        "15s": "1.25秒",
        "20s": "1.67秒",
        "25s": "2.08秒",
        "30s": "2.5秒",
    }
    per_shot = _DURATION_TO_PER_SHOT.get(duration, "1.25秒")
    system_text = SYSTEM_PROMPT_TEMPLATE.format(
        aspect_ratio=aspect_ratio, duration=duration, per_shot=per_shot
    )

    # 客户原始输入原样拼装（不删减、不改写）
    user_lines = [f"产品名：{product_name or '（未填写，请从产品图识别）'}"]
    if selling_points:
        user_lines.append(f"卖点：\n{selling_points}")
    else:
        user_lines.append("卖点：（未填写，请从产品图分析提取）")
    if audience:
        user_lines.append(f"目标人群：{audience}")
    else:
        user_lines.append("目标人群：（未填写，请从产品分析推理）")
    if ad_theme:
        user_lines.append(f"广告主题：{ad_theme}")
    if scene_requirement:
        user_lines.append(f"场景要求：\n{scene_requirement}")
    user_lines.append(f"画面比例：{aspect_ratio}")
    if len(product_image_paths) > 1:
        user_lines.append(
            f"附上 {len(product_image_paths)} 张产品参考图（不同角度/细节），"
            "请综合所有参考图识别产品外观，并在分镜中严格还原。"
        )
    user_lines.append(f"请基于上面的产品图和客户输入，生成 {duration} TVC 分镜面板提示词。")
    user_text = "\n".join(user_lines)

    # 多张产品图按顺序放入 multimodal content
    from .image2_client import _compress_image_bytes
    image_blocks: List[Dict[str, Any]] = []
    for img_path in product_image_paths:
        p = Path(img_path)
        if not p.exists():
            continue
        content = p.read_bytes()
        content, _ = _compress_image_bytes(content, max_side=1024, quality=85, threshold=200_000)
        b64 = base64.b64encode(content).decode()
        image_blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })

    if not image_blocks:
        raise FileNotFoundError("Step1 缺少可用的产品参考图")

    return [
        {"role": "system", "content": system_text},
        {
            "role": "user",
            "content": [*image_blocks, {"type": "text", "text": user_text}],
        },
    ]


def _call_gpt55_sync(messages: List[Dict[str, Any]]) -> str:
    """调用 gpt-6.1-sol 生成提示词，失败时自动切换 MiMo-v2.5 兜底。

    gpt-6.1-sol 已测通多模态请求（49s 完成，2659 字符）。
    若遇 504 网关超时或其他错误，自动切换 MiMo-v2.5 兜底。
    """
    global _LAST_PROVIDER, _LAST_ERROR
    last_err: Optional[Exception] = None
    for attempt in range(1, 3):  # gpt-6.1-sol 最多 2 次
        http_client = httpx.Client(timeout=httpx.Timeout(300.0, connect=30.0))
        client = OpenAI(
            api_key=settings.GPT55_API_KEY,
            base_url=settings.GPT55_BASE_URL,
            http_client=http_client,
        )
        try:
            # 流式调用：网关持续转发 token，避免推理时间长触发 Cloudflare 524/504 超时
            stream = client.chat.completions.create(
                model="gpt-6.1-sol",
                messages=messages,
                max_completion_tokens=8192,
                stream=True,
            )
            parts: List[str] = []
            for chunk in stream:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        parts.append(delta.content)
            content = "".join(parts)
            if not content.strip():
                raise RuntimeError("gpt-6.1-sol 流式返回空内容")
            _LAST_PROVIDER = "gpt-6.1-sol"
            return content
        except Exception as e:
            last_err = e
            msg = str(e)
            retryable = any(k in msg for k in ("504", "502", "Gateway", "timed out",
                                                "ConnectError", "ConnectionError",
                                                "RemoteProtocolError", "ReadTimeout"))
            if not retryable or attempt == 2:
                # gpt-6.1-sol 重试耗尽 → 切换 MiMo-v2.5 兜底
                _LAST_ERROR = f"{type(e).__name__}: {msg[:200]}"
                print(f"[tvc_agent] gpt-6.1-sol 第 {attempt} 次失败（{type(e).__name__}: {msg[:80]}），切换 MiMo-v2.5 兜底")
                try:
                    result = _call_mimo_fallback(messages)
                    _LAST_PROVIDER = "MiMo-v2.5"
                    return result
                except Exception as me:
                    print(f"[tvc_agent] MiMo-v2.5 兜底也失败: {me}")
                    raise last_err
            time.sleep(5)
    raise last_err  # type: ignore[misc]


# 上次成功生成提示词使用的模型来源（gpt-6.1-sol / MiMo-v2.5）
_LAST_PROVIDER: str = "gpt-6.1-sol"
# gpt-6.1-sol 最后一次失败原因（兜底时写入任务日志，便于定位降级原因）
_LAST_ERROR: str = ""


def _call_mimo_fallback(messages: List[Dict[str, Any]]) -> str:
    """MiMo-v2.5 兜底：gpt-6.1-sol 不可用时使用。

    MiMo 支持 image_url 多模态，模型名为 mimo-v2.6-flash。
    """
    headers = {
        "Authorization": f"Bearer {settings.MIMO_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.TEXT_MODEL_MIMO,
        "messages": messages,
        "max_completion_tokens": 8192,
    }
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=300.0) as client:
                resp = client.post(
                    f"{settings.MIMO_BASE_URL}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                if resp.status_code in (403, 429) or resp.status_code >= 500:
                    body = resp.text[:300]
                    if "insufficient" in body or "额度" in body or "quota" in body:
                        raise RuntimeError(f"MiMo API 额度不足：{body}")
                    raise httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}: {body}",
                        request=resp.request, response=resp,
                    )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"] or ""
                if not content.strip():
                    raise RuntimeError("MiMo-v2.5 返回空 content")
                return content
        except RuntimeError:
            raise
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.TransportError) as e:
            last_exc = e
            if attempt < 2:
                wait = 5 * (attempt + 1)
                print(f"[tvc_agent] MiMo-v2.5 兜底第 {attempt+1}/3 次失败 {e}，{wait}s 后重试")
                time.sleep(wait)
            continue
    raise RuntimeError(f"MiMo-v2.5 兜底失败（已重试 3 次）: {last_exc}") from last_exc


async def step1_generate_prompt(
    product_image_paths: List[str],
    product_name: str,
    audience: str,
    ad_theme: str,
    scene_requirement: str = "",
    aspect_ratio: str = "16:9",
    selling_points: str = "",
    duration: str = "15s",
) -> Dict[str, Any]:
    """Step1: 调用 gpt-6.1-sol 生成画布提示词

    支持传入多张产品参考图（不同角度/细节），gpt-6.1-sol 会综合识别产品外观。

    duration: TVC 总时长（"15s"/"20s"/"30s"），LLM 会据此推理每格时长。

    返回: {"canvas_prompt": str, "raw": str}
    """
    if not product_image_paths:
        raise FileNotFoundError("Step1 缺少产品参考图")

    # 校验至少有一张可用，并压缩到 1024px 长边（在 _build_user_content_step1 内完成）
    available = [p for p in product_image_paths if p and Path(p).exists()]
    if not available:
        raise FileNotFoundError(f"产品图均不存在: {product_image_paths}")

    messages = _build_user_content_step1(
        product_image_paths=available,
        product_name=product_name,
        audience=audience,
        ad_theme=ad_theme,
        scene_requirement=scene_requirement,
        aspect_ratio=aspect_ratio,
        selling_points=selling_points,
        duration=duration,
    )

    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, _call_gpt55_sync, messages)

    # gpt-6.1-sol 通常直接返回提示词文本，可能被包在 ```text 代码块里
    canvas_prompt = _extract_prompt_block(raw)

    return {
        "canvas_prompt": canvas_prompt,
        "raw": raw,
    }


def _extract_prompt_block(text: str) -> str:
    """从 GPT 响应中提取最终提示词（去掉代码块包装和多余解释）"""
    # 优先提取 ```text 或 ``` 代码块
    m = re.search(r"```(?:text|markdown)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # 没有代码块，尝试找"提示词:"标签后的内容
    m = re.search(r"(?:最终)?提示词[：:]\s*\n?(.*?)(?:\n\n注意|\Z)", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()


# 内容审核安全化：替换触发 Adobe 安全策略的敏感词为中性表达
# Adobe 拒绝：裸露/沐浴/皮肤/搓澡/背部/身体/浴巾等
_CONTENT_SAFETY_MAP = {
    "洗澡": "清洁",
    "沐浴": "护理",
    "浴巾": "毛巾",
    "浴袍": "家居服",
    "背部": "后身",
    "后背": "后身",
    "皮肤": "肌肤",
    "身体": "身姿",
    "搓澡": "清洁",
    "泡沫": "泡沫",
    "沐浴露": "洗护用品",
    "towel wrapped around body": "wearing casual home clothes",
    "woman's shoulder": "person's shoulder",
    "bath": "spa",
    "bathtub": "basin",
    "naked": "casual",
    "skin texture": "surface texture",
    "dry skin": "surface detail",
    "裸": "休闲",
}


def _sanitize_prompt_for_adobe(prompt: str) -> str:
    """对提示词做内容安全化处理，避免触发 Adobe 内容审核。

    替换沐浴/裸露/皮肤相关敏感词为更中性的表达，
    保留产品功能和叙事意图，但去除可能被判定为成人/敏感内容的元素。
    """
    sanitized = prompt
    for src, dst in _CONTENT_SAFETY_MAP.items():
        sanitized = sanitized.replace(src, dst)
    # 额外添加安全声明，让 image2 知道这是产品广告
    safety_prefix = (
        "重要声明：这是产品商业广告分镜，所有人物必须穿着得体家居服饰，"
        "不出现任何裸露、沐浴、私密场景。人物仅展示使用产品的动作即可。\n\n"
    )
    return safety_prefix + sanitized


# ═════════════════════════════════════════════════════════════════════
# Step 2: image2 生成 4K 故事板（3x4 十二宫格）
# ═════════════════════════════════════════════════════════════════════

# 16:9 的 image2 支持尺寸映射（image2 仅接受固定档位）
_ASPECT_TO_SIZE = {
    "16:9": "1792x1024",  # 接近 16:9 的最大档
    "9:16": "1024x1792",
    "1:1": "1024x1024",
    "3:4": "1024x1344",  # 近似
    "4:3": "1344x1024",
}


async def step2_generate_storyboard(
    canvas_prompt: str,
    clothing_image_path: Optional[str] = None,
    product_image_paths: Optional[List[str]] = None,
    aspect_ratio: str = "16:9",
    output_dir: Path = OUTPUT_DIR,
    feedback: str = "",
) -> Dict[str, Any]:
    """Step2: 调用 image2 生成 4K 故事板

    优先用 image_edit_sync（图生图）传入产品图+服装图作为参考，
    若无任何参考图则降级为 text_to_image_sync（纯文生图）。
    支持多张产品参考图（不同角度/细节），全部传给 image2 作为产品外观真值。

    feedback: 用户审核意见（驳回重做时传入），会附加到 prompt 末尾指导重新生成。

    注意：故事板图片固定使用 16:9 横版比例 + 3列×4行 网格布局，与用户填写的
    最终视频比例解耦。最终视频比例在 Step3 由视频生成模型处理。
    """
    # 故事板固定 16:9 横版（与爆款复刻对齐）
    storyboard_ratio = "16:9"
    size = _ASPECT_TO_SIZE.get(storyboard_ratio, "1792x1024")
    client = Image2Client()

    # 内容审核安全化：避免触发 Adobe 安全策略（沐浴/裸露/皮肤等）
    canvas_prompt = _sanitize_prompt_for_adobe(canvas_prompt)

    # 参考图归集：服装图优先作为主参考，其后跟所有产品参考图
    images: List[str] = []
    if clothing_image_path and Path(clothing_image_path).exists():
        images.append(clothing_image_path)
    if product_image_paths:
        for p in product_image_paths:
            if p and Path(p).exists() and p not in images:
                images.append(p)

    # 用户审核意见注入（重做时生效）
    feedback_block = ""
    if feedback and feedback.strip():
        feedback_block = (
            "\n\n==================== 用户审核修改意见（必须执行）====================\n"
            f"{feedback.strip()}\n"
            "====================================================================\n"
            "请严格按上述意见调整分镜面板，其余部分保持原提示词要求不变。"
        )

    # 动态生成参考图说明（让 image2 知道每张图的用途）
    ref_desc_parts: List[str] = []
    idx = 1
    if clothing_image_path and Path(clothing_image_path).exists():
        ref_desc_parts.append(f"第{idx}张为服装/模特参考")
        idx += 1
    if product_image_paths:
        prod_count = sum(1 for p in product_image_paths if p and Path(p).exists())
        if prod_count:
            if prod_count == 1:
                ref_desc_parts.append(f"第{idx}张为产品参考（多角度细节）")
            else:
                ref_desc_parts.append(
                    f"第{idx}~{idx + prod_count - 1}张为产品参考（多角度/细节，共{prod_count}张）"
                )
    ref_desc = "；".join(ref_desc_parts) + "。" if ref_desc_parts else ""

    if images:
        # 图生图：把参考图和提示词一起送给 image2
        # 故事板固定 16:9 横版 + 3列×4行（与爆款复刻对齐）
        grid_desc = "3列×4行（3 columns × 4 rows）"
        full_prompt = (
            f"请根据以下提示词生成一张 {grid_desc} 共12格的专业 TVC 广告分镜面板，"
            f"画面比例 {storyboard_ratio}，4K 高清，分隔线笔直，排版整齐。\n\n"
            "重要：每个面板必须在画面顶部或底部以清晰小字标注以下元数据（不可省略）：\n"
            "  - 面板编号（如 PANEL 1）\n"
            "  - 时间码（如 00:00-00:01）\n"
            "  - 镜位（如 特写/中景/全景）\n"
            "  - 旁白文案（中文台词）\n"
            "让观众能直接从图片读出每个面板的脚本信息，不要只画画面而丢失这些文字维度。\n\n"
            "参考图使用规则（关键 - 必须严格遵守）：\n"
            "- 参考图仅作为产品外观的真值参考（颜色、材质、logo、形状、细节），\n"
            "  绝不可将参考图原样贴入画面，也不可让产品悬浮/漂浮在画面外。\n"
            "- 必须根据每个分镜的具体场景和人物动作，重新绘制产品的物理状态：\n"
            "  · 人物握持产品时，必须画出正确的握持姿势（手指、手掌、握把位置），\n"
            "    产品与人手有自然的接触关系，不可悬浮。\n"
            "  · 产品在使用中时，必须体现真实的物理交互（如刷子接触皮肤/背部/腿部，\n"
            "    刷毛自然弯曲贴合人体曲线，刷柄呈现符合人体工学的角度）。\n"
            "  · 产品特写时，必须符合该产品的真实尺寸比例与摆放方式，不可变形。\n"
            "- 简言之：产品必须在场景中真实『被使用』，而不是『被展示』在画面外。\n\n"
            f"参考图说明：{ref_desc}\n\n"
            f"提示词：\n{canvas_prompt}"
            f"{feedback_block}"
        )
        result = await client.image_edit_sync(
            prompt=full_prompt,
            images=images,
            size=size,
            n=1,
            response_format="url",
        )
    else:
        # 纯文生图（无参考图）
        grid_desc = "3列×4行（3 columns × 4 rows）"
        meta_requirement = (
            f"\n\n重要：每个面板必须在画面顶部或底部以清晰小字标注以下元数据（不可省略）：\n"
            "  - 面板编号（如 PANEL 1）\n"
            "  - 时间码（如 00:00-00:01）\n"
            "  - 镜位（如 特写/中景/全景）\n"
            "  - 旁白文案（中文台词）\n"
            f"请使用 {grid_desc} 网格布局，画面比例 {storyboard_ratio}，4K 高清。\n"
            "产品物理规则：产品必须在场景中真实『被使用』，不可悬浮或贴图式展示，\n"
            "人物握持时必须画出正确的握持姿势和接触关系。"
        )
        result = await client.text_to_image_sync(
            prompt=canvas_prompt + meta_requirement + feedback_block,
            size=size,
            n=1,
            response_format="url",
        )

    image_url = client._get_result_image(result)
    if not image_url:
        raise RuntimeError("image2 未返回故事板图片")

    # 下载到本地
    storyboard_path = output_dir / "storyboard.png"
    if image_url.startswith("http"):
        async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as hc:
            r = await hc.get(image_url)
            r.raise_for_status()
            storyboard_path.write_bytes(r.content)
    else:
        # b64_json
        b64 = image_url.split("base64,")[-1] if "base64," in image_url else image_url
        storyboard_path.write_bytes(base64.b64decode(b64))

    return {
        "storyboard_path": str(storyboard_path),
        "storyboard_url": image_url,
    }


# ═════════════════════════════════════════════════════════════════════
# Step 3: RPA 走 SD2 生成成片
# ═════════════════════════════════════════════════════════════════════

def _build_video_prompt(storyboard_prompt: str, product_name: str) -> str:
    """构造 Step3 视频生成的提示词。

    ⚠️ 必须把故事板绑到 @图片1：ref_images[0] 就是故事板（见 step3_generate_video）。
    只写"参考故事板"而不绑 @图片1 时，weeksir 模型会把参考图全当产品外观参考，
    故事板等于没传给它（2026-10-09 事故：出片不跟分镜）。写法对齐
    outputs/_gen_final_video_rpa.py 的 @图片1 契约。
    """
    return (
        f"请严格按照@图片1 故事板的12个分镜画面顺序生成一支高级的 {product_name} TVC，"
        "不可自己更改故事板分镜画面，严格保持产品外观/材质/比例一致，"
        "人物保持一致，不要出现字幕。"
    )


async def step3_generate_video(
    storyboard_image_path: str,
    product_image_paths: Optional[List[str]],
    canvas_prompt: str,
    product_name: str,
    aspect_ratio: str,
    output_dir: Path,
    headless: bool = False,
    dry_run: bool = False,
    duration: str = "15s",
    model_value: str = None,
) -> Dict[str, Any]:
    """Step3: 调用 step5 RPA 在 weeksir.com/video 上生成成片

    复用 pipeline/step5_video_generate.py 的底层函数。
    参考图列表：第1张=故事板（@图片1），其后追加所有产品参考图。
    weeksir.com/video 页面最多支持 7 张参考图，超出会被截断并告警。
    dry_run=True 时只填内容不点生成，保留浏览器供人工检查。

    duration: 视频总时长（"15s"/"20s"/"30s"），透传给 RPA 的 _set_parameters。
    model_value: 指定模型（如 MODEL_SEEDANCE_2_5 / MODEL_NO_FACE_LOCK）。
                 None 时默认用 MODEL_NO_FACE_LOCK（Seedance2.0）。
    """
    from pipeline.step5_video_generate import (
        _launch_context, _navigate_and_login, _wait_for_page_ready,
        _fill_prompt, _upload_reference_images, _select_model,
        _set_parameters, _click_generate, _wait_for_video_result,
        MODEL_NO_FACE_LOCK, MODEL_SEEDANCE_2_5,
    )

    # 模型选择：前端传值优先，否则默认 2.0
    used_model = model_value or MODEL_NO_FACE_LOCK

    video_prompt = _build_video_prompt(canvas_prompt, product_name)

    # 参考图列表：第1张=故事板（@图片1），后续=所有产品参考图
    ref_images: List[str] = [storyboard_image_path]
    if product_image_paths:
        for p in product_image_paths:
            if p and Path(p).exists() and p not in ref_images:
                ref_images.append(p)

    # weeksir.com/video 页面最多 7 张参考图，超出截断
    MAX_REFS = 7
    if len(ref_images) > MAX_REFS:
        print(f"[tvc_agent] 参考图 {len(ref_images)} 张超出上限 {MAX_REFS}，截断为前 {MAX_REFS} 张（故事板+前{MAX_REFS-1}张产品图）")
        ref_images = ref_images[:MAX_REFS]

    playwright, context = await _launch_context(headless=headless)
    page = context.pages[0] if context.pages else await context.new_page()

    try:
        await _navigate_and_login(page)
        await _wait_for_page_ready(page)
        await _fill_prompt(page, video_prompt)
        await _upload_reference_images(page, ref_images)
        await _select_model(page, used_model)
        # 映射画面比例：16:9 → "16:9"，9:16 → "9:16"，其他保持原值
        # duration 由前端传入（15s/20s/30s），固定 720p
        await _set_parameters(page, aspect_ratio=aspect_ratio, duration=duration, resolution="720p")

        # 提交前截图
        screenshot = output_dir / "before_generate.png"
        await page.screenshot(path=str(screenshot), full_page=False)

        if dry_run:
            print("[DryRun] 已填好提示词/参考图/参数，保留浏览器供人工检查，不点击生成。")
            return {
                "dry_run": True,
                "video_prompt": video_prompt,
                "ref_images": ref_images,
                "params": {"aspect_ratio": aspect_ratio, "duration": duration, "resolution": "720p", "model": used_model},
                "page_url": page.url,
            }

        await _click_generate(page)
        video_result = await _wait_for_video_result(page, timeout=900)

        # 下载视频
        video_url = video_result.get("video_url", "")
        local_video = None
        if video_url and video_url.startswith("http"):
            local_path = output_dir / "tvc_final.mp4"
            try:
                async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as hc:
                    r = await hc.get(video_url)
                    r.raise_for_status()
                    local_path.write_bytes(r.content)
                local_video = str(local_path)
            except Exception:
                pass

        # 最终截图
        final_shot = output_dir / "after_generate.png"
        await page.screenshot(path=str(final_shot), full_page=False)

        return {
            **video_result,
            "local_video": local_video,
            "video_prompt": video_prompt,
        }
    finally:
        if dry_run:
            # 保留浏览器供人工检查，不关闭 context
            print("[DryRun] 浏览器已保留，请手动检查 weeksir.com/video 页面内容。")
        else:
            await context.close()
            await playwright.stop()


# ═════════════════════════════════════════════════════════════════════
# 全流程入口
# ═════════════════════════════════════════════════════════════════════

async def run_pipeline(
    product_image_paths: List[str],
    product_name: str,
    audience: str,
    ad_theme: str,
    scene_requirement: str = "",
    aspect_ratio: str = "16:9",
    selling_points: str = "",
    clothing_image_path: Optional[str] = None,
    headless: bool = False,
    duration: str = "15s",
    model_value: Optional[str] = None,
) -> str:
    """启动 TVC 全流程（Phase1: Step1+Step2），返回 task_id

    支持多张产品参考图（不同角度/细节），全部传入 Step1/Step2/Step3。
    Phase1 完成后进入 awaiting_review 等待用户审核故事板；
    审核通过后由 approve_task 启动 Phase2 (Step3)。

    duration: TVC 总时长（"15s"/"20s"/"30s"），LLM 据此推理故事板每格时长，RPA 据此设视频时长。
    model_value: 指定模型（seedance-2.0-renlian / seedance-2.5），None 时默认 2.0。
    """
    # 归一化：去重 + 过滤不存在路径
    norm_paths: List[str] = []
    seen = set()
    for p in product_image_paths or []:
        if p and p not in seen:
            norm_paths.append(p)
            seen.add(p)
    if not norm_paths:
        raise ValueError("至少需要 1 张产品参考图")

    task_id = f"tvc_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    _tasks[task_id] = {
        "task_id": task_id,
        "status": "running",
        "phase": "phase_1",
        "step": "初始化",
        "progress": 0,
        "params": {
            "product_name": product_name,
            "audience": audience,
            "ad_theme": ad_theme,
            "scene_requirement": scene_requirement,
            "aspect_ratio": aspect_ratio,
            "selling_points": selling_points,
            "product_image_paths": norm_paths,           # 新格式：多张产品参考图
            "product_image_path": norm_paths[0],          # 旧字段兼容（首张）
            "clothing_image_path": clothing_image_path or "",
            "headless": headless,
            "duration": duration,
            "model_value": model_value or "",
        },
        "outputs": {
            "canvas_prompt": None,
            "storyboard_path": None,
            "video_path": None,
            "video_url": None,
        },
        "review": None,
        "feedback": "",  # 用户驳回意见，重做 Step2 时注入
        "logs": [],
        "error": None,
        "created_at": int(time.time()),
    }
    asyncio.create_task(_run_phase_1(task_id))
    _persist_task(task_id)
    return task_id


async def _run_phase_1(task_id: str):
    """Phase1: Step1 (MiMo-v2.5 提示词) + Step2 (image2 故事板) → awaiting_review"""
    task = _tasks[task_id]
    params = task["params"]
    run_dir = OUTPUT_DIR / task_id
    run_dir.mkdir(parents=True, exist_ok=True)

    def _log(msg: str):
        task["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    try:
        # ── Step1: gpt-6.1-sol 生成画布提示词 ──
        task["step"] = "Step1/3：gpt-6.1-sol 生成画布提示词..."
        task["progress"] = 5
        # 归一化产品参考图列表（兼容旧 state 的单图字段）
        product_image_paths = _normalize_product_images(params)
        if not product_image_paths:
            raise RuntimeError("缺少产品参考图，无法执行 Step1")
        # 回写归一化结果，保证旧任务续跑时也用上新格式
        params["product_image_paths"] = product_image_paths
        params["product_image_path"] = product_image_paths[0]

        _log(f"产品: {params['product_name']} | 人群: {params['audience']} | 主题: {params['ad_theme']} | 比例: {params['aspect_ratio']}")
        _log(f"产品参考图 {len(product_image_paths)} 张，调用 gpt-6.1-sol 分析并生成 12 宫格分镜提示词...")
        _persist_task(task_id)

        step1_result = await step1_generate_prompt(
            product_image_paths=product_image_paths,
            product_name=params["product_name"],
            audience=params["audience"],
            ad_theme=params["ad_theme"],
            scene_requirement=params["scene_requirement"],
            aspect_ratio=params["aspect_ratio"],
            selling_points=params["selling_points"],
            duration=params.get("duration", "15s"),
        )
        canvas_prompt = step1_result["canvas_prompt"]
        if not canvas_prompt:
            raise RuntimeError("gpt-6.1-sol 未返回有效提示词")
        # 标注实际使用的模型（gpt-6.1-sol 不可用时已自动切换 MiMo 兜底）
        provider = _LAST_PROVIDER or "gpt-6.1-sol"
        if provider == "MiMo-v2.5":
            reason = f" | 原因: {_LAST_ERROR}" if _LAST_ERROR else ""
            _log(f"⚠ gpt-6.1-sol 失败，已切换 MiMo-v2.5 兜底生成提示词（{len(canvas_prompt)} 字符）{reason}")
        else:
            _log(f"✓ 画布提示词已生成（gpt-6.1-sol，{len(canvas_prompt)} 字符）")

        # 保存提示词
        (run_dir / "canvas_prompt.txt").write_text(canvas_prompt, encoding="utf-8")
        (run_dir / "step1_raw.txt").write_text(step1_result["raw"], encoding="utf-8")
        task["outputs"]["canvas_prompt"] = canvas_prompt
        task["progress"] = 30
        _persist_task(task_id)

        # ── Step2: image2 生成 4K 故事板 ──
        task["step"] = "Step2/3：image2 生成 4K 故事板..."
        task["progress"] = 35
        feedback = task.get("feedback", "")
        if feedback:
            _log(f"↻ 携带用户审核意见重做故事板: {feedback[:80]}...")
        _log("调用 image2 (gpt-image-2) 生成 16:9 十二宫格故事板...")
        _persist_task(task_id)
        step2_result = await step2_generate_storyboard(
            canvas_prompt=canvas_prompt,
            clothing_image_path=params.get("clothing_image_path"),
            product_image_paths=product_image_paths,
            aspect_ratio=params["aspect_ratio"],
            output_dir=run_dir,
            feedback=feedback,
        )
        storyboard_path = step2_result["storyboard_path"]
        task["outputs"]["storyboard_path"] = storyboard_path
        task["progress"] = 60
        _log(f"✓ 故事板已生成: {storyboard_path}")

        # ── 进入待审核 ──
        task["status"] = "awaiting_review"
        task["phase"] = "after_storyboard"
        task["step"] = "故事板已生成，等待审核"
        task["review"] = {
            "phase": "after_storyboard",
            "storyboard_path": storyboard_path,
            "canvas_prompt": canvas_prompt,
        }
        # 清掉已消费的 feedback
        task["feedback"] = ""
        _log("⏸ 故事板待审核：通过则继续 Step3，驳回则携带意见重做 Step2")
        _persist_task(task_id)

    except Exception as e:
        task["status"] = "failed"
        task["step"] = f"失败: {e}"
        task["error"] = str(e)
        _log(f"✗ 失败: {e}")
        _persist_task(task_id)


async def _run_phase_2(task_id: str):
    """Phase2: Step3 (RPA 走 SD2 生成成片) → completed"""
    task = _tasks[task_id]
    params = task["params"]
    run_dir = OUTPUT_DIR / task_id

    def _log(msg: str):
        task["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    try:
        storyboard_path = task["outputs"].get("storyboard_path")
        canvas_prompt = task["outputs"].get("canvas_prompt")
        if not storyboard_path or not canvas_prompt:
            raise RuntimeError("缺少故事板或提示词，无法执行 Step3")

        task["step"] = "Step3/3：RPA 走 SD2 生成成片..."
        task["progress"] = 65
        # 归一化产品参考图列表（兼容旧 state）
        product_image_paths = _normalize_product_images(params)
        params["product_image_paths"] = product_image_paths
        if product_image_paths:
            params["product_image_path"] = product_image_paths[0]
        _log(
            f"启动 Playwright RPA，在 weeksir.com/video 上生成视频"
            f"（故事板 + {len(product_image_paths)} 张产品参考图）..."
        )
        _persist_task(task_id)

        step3_result = await step3_generate_video(
            storyboard_image_path=storyboard_path,
            product_image_paths=product_image_paths,
            canvas_prompt=canvas_prompt,
            product_name=params["product_name"],
            aspect_ratio=params["aspect_ratio"],
            output_dir=run_dir,
            headless=params.get("headless", False),
            duration=params.get("duration", "15s"),
            model_value=params.get("model_value"),
        )
        task["outputs"]["video_path"] = step3_result.get("local_video")
        task["outputs"]["video_url"] = step3_result.get("video_url")
        task["progress"] = 100
        task["status"] = "completed"
        task["phase"] = "done"
        task["step"] = "全部完成"
        task["review"] = None
        _log(f"✓ 视频已生成: {step3_result.get('local_video') or step3_result.get('video_url')}")
        _persist_task(task_id)

    except Exception as e:
        task["status"] = "failed"
        task["step"] = f"失败: {e}"
        task["error"] = str(e)
        _log(f"✗ 失败: {e}")
        _persist_task(task_id)


def approve_task(task_id: str) -> Dict[str, Any]:
    """审核通过：启动 Phase2 (Step3)"""
    if task_id not in _tasks:
        return {"success": False, "error": "任务不存在"}
    task = _tasks[task_id]
    if task["status"] != "awaiting_review":
        return {"success": False, "error": f"当前状态 {task['status']} 不可审核通过"}

    task.update({
        "status": "running",
        "phase": "phase_2",
        "step": "Step3/3：RPA 走 SD2 生成成片...",
        "review": None,
        "feedback": "",
        "logs": task["logs"] + [f"[{datetime.now().strftime('%H:%M:%S')}] ✓ 审核通过，启动 Step3 视频生成..."],
    })
    asyncio.create_task(_run_phase_2(task_id))
    _persist_task(task_id)
    return {"success": True, "task_id": task_id, "next_phase": "phase_2"}


def reject_task(task_id: str, feedback: str = "") -> Dict[str, Any]:
    """审核驳回：携带用户意见重做 Step2 (image2 故事板)"""
    if task_id not in _tasks:
        return {"success": False, "error": "任务不存在"}
    task = _tasks[task_id]
    if task["status"] != "awaiting_review":
        return {"success": False, "error": f"当前状态 {task['status']} 不可驳回"}

    task.update({
        "status": "running",
        "phase": "redo_phase_1",
        "step": "Step2/3：携带审核意见重做故事板...",
        "feedback": feedback or "",
        "review": None,
        "logs": task["logs"] + [
            f"[{datetime.now().strftime('%H:%M:%S')}] ↻ 审核驳回，重做故事板",
            f"[{datetime.now().strftime('%H:%M:%S')}] 用户意见: {feedback or '（未填写）'}",
        ],
    })
    asyncio.create_task(_run_phase_1(task_id))
    _persist_task(task_id)
    return {"success": True, "task_id": task_id, "redo_phase": "redo_phase_1"}


def retry_task(task_id: str) -> Dict[str, Any]:
    """失败后重试：根据失败阶段自动选择从哪一步续跑，不重头开始。

    策略：
    - phase_2 失败（Step3 失败）→ 重做 phase_2（仅 Step3，复用已有故事板+提示词）
    - phase_1 / redo_phase_1 失败（Step1 或 Step2 失败）→ 重做 phase_1（保留 feedback）
    """
    if task_id not in _tasks:
        return {"success": False, "error": "任务不存在"}
    task = _tasks[task_id]
    if task["status"] != "failed":
        return {"success": False, "error": f"当前状态 {task['status']} 不可重试（仅 failed 可重试）"}

    failed_phase = task.get("phase", "")
    now = datetime.now().strftime("%H:%M:%S")

    if failed_phase == "phase_2":
        # Step3 失败：重做 phase_2，需复用已生成的故事板和提示词
        if not task["outputs"].get("storyboard_path") or not task["outputs"].get("canvas_prompt"):
            return {"success": False, "error": "缺少故事板或提示词，无法重试 Step3"}
        retry_phase = "phase_2"
        step_name = "重试 Step3: 视频生成"
        runner = _run_phase_2
    elif failed_phase in ("phase_1", "redo_phase_1", "resume_phase_1"):
        # Step1/Step2 失败：重做 phase_1（保留 feedback 用于 Step2 重做）
        retry_phase = "phase_1"
        step_name = "重试 Phase1: 提示词+故事板"
        runner = _run_phase_1
    else:
        return {"success": False, "error": f"失败阶段 {failed_phase} 不支持重试"}

    task.update({
        "status": "running",
        "phase": retry_phase,
        "step": step_name + "...",
        "error": None,
        "review": None,
        "logs": task["logs"] + [f"[{now}] ↻ {step_name}（从 {failed_phase} 恢复）..."],
    })
    _persist_task(task_id)
    asyncio.create_task(runner(task_id))
    return {"success": True, "task_id": task_id, "retry_phase": retry_phase, "step_name": step_name}


def get_review(task_id: str) -> Dict[str, Any]:
    """获取待审核内容（故事板图片 + 提示词）"""
    if task_id not in _tasks:
        return {"status": "not_found"}
    task = _tasks[task_id]
    if task["status"] != "awaiting_review":
        return {
            "status": task["status"],
            "phase": task.get("phase"),
            "message": "当前无待审核内容",
            "review": None,
        }

    review = task.get("review") or {}
    storyboard_path = review.get("storyboard_path") or task["outputs"].get("storyboard_path")
    canvas_prompt = review.get("canvas_prompt") or task["outputs"].get("canvas_prompt")

    return {
        "status": "awaiting_review",
        "phase": task.get("phase"),
        "step_name": task.get("step"),
        "review": {
            "phase": review.get("phase", "after_storyboard"),
            "storyboard_path": storyboard_path,
            "canvas_prompt": canvas_prompt,
        },
    }


def get_task_status(task_id: str) -> Dict[str, Any]:
    """查询任务状态"""
    if task_id not in _tasks:
        return {"status": "not_found", "task_id": task_id}
    return _tasks[task_id]


def list_history(limit: int = 20) -> List[Dict[str, Any]]:
    """获取历史任务列表（按创建时间倒序）"""
    items = sorted(_tasks.values(), key=lambda x: x.get("created_at", 0), reverse=True)
    return [
        {
            "task_id": t["task_id"],
            "status": t["status"],
            "step": t["step"],
            "product_name": t.get("params", {}).get("product_name", ""),
            "ad_theme": t.get("params", {}).get("ad_theme", ""),
            "aspect_ratio": t.get("params", {}).get("aspect_ratio", ""),
            "storyboard_path": t.get("outputs", {}).get("storyboard_path"),
            "video_path": t.get("outputs", {}).get("video_path"),
            "video_url": t.get("outputs", {}).get("video_url"),
            "created_at": t.get("created_at"),
        }
        for t in items[:limit]
    ]
