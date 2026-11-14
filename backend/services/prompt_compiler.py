"""
一句话生视频 - Prompt Compiler

按 backend/services/prompt-compiler.md 规范，把用户的自然语言意图 + 商品图（可选）
+ 产品补充信息（可选）+ 客户补充意见（可选）编译成 Seedance 2.0 视频生成 prompt。

输出两类内容：
  1. metadata: 完整结构化 JSON（intent/audience/hook/route/shots/antiCollapse/voiceover）
  2. compiledPrompt: 最终拼好的 seedancePrompt 文本字符串（直接传给 step5_video_generate）

视频生成阶段调用 pipeline/step5_video_generate.py:run(custom_prompt=compiled_prompt,
product_image_path=product_image) 复用爆款复刻 RPA。
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import asyncio
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from PIL import Image

from config import settings


# ── 路径常量 ──
SPEC_PATH = Path(__file__).parent / "prompt-compiler.md"


# ── 加载规范文本（启动时一次性读取，缓存） ──
_SPEC_CACHE: Optional[str] = None


def _load_spec() -> str:
    """读取 prompt-compiler.md 规范全文。"""
    global _SPEC_CACHE
    if _SPEC_CACHE is None:
        if SPEC_PATH.exists():
            _SPEC_CACHE = SPEC_PATH.read_text(encoding="utf-8")
        else:
            _SPEC_CACHE = ""
    return _SPEC_CACHE


# ── 系统 Prompt 构造 ──

# 3 个变体的风格定义（强制差异化）
VARIANT_STYLES = [
    {
        "id": "A",
        "name": "痛点切入型",
        "hook_desc": "Hook 必须用痛点/使用场景痛点切入，放大用户使用商品前的困扰、尴尬或不便，让用户产生「这就是我」的共鸣",
        "hook_examples": "「每次泡脚都坚持不了 10 分钟，水温一降就不想泡了」「家里泡脚桶太大，收纳麻烦，最后只能落灰」",
    },
    {
        "id": "B",
        "name": "场景沉浸型",
        "hook_desc": "Hook 必须用使用场景/生活方式切入，营造代入感，让用户想象自己使用商品的美好状态",
        "hook_examples": "「周末晚上，泡上一桶热水，看着电视，这才是生活」「出差回家，泡个脚解乏，整个人都放松了」",
    },
    {
        "id": "C",
        "name": "对比证明型",
        "hook_desc": "Hook 必须用效果对比/证据证明切入，强调商品带来的改变和提升，用前后对比、数据、用户反馈等证明",
        "hook_examples": "「泡了 30 分钟水温才降 2 度」「八层加厚 vs 普通三层，保温效果对比」「用户反馈：连续用了一周，睡眠质量明显改善」",
    },
]


def _build_system_prompt(variant_hint: Optional[Dict[str, str]] = None) -> str:
    """构造 GPT 的 system prompt：包含规范全文 + 输出格式约束。

    Args:
        variant_hint: 变体风格提示。传入时会强制脚本走指定风格（痛点/场景/对比）。
                      None 时为单条模式（无风格约束）。
    """
    spec = _load_spec()

    # 变体风格约束段（仅在 variant_hint 存在时注入）
    variant_section = ""
    if variant_hint:
        variant_section = f"""
# 本脚本的风格约束（必须严格遵守）

你正在生成变体 {variant_hint['id']}（{variant_hint['name']}）。
- Hook 风格：{variant_hint['hook_desc']}
- 参考 Hook 表达（禁止直接照抄，必须结合商品特征重新创作）：{variant_hint['hook_examples']}
- hookType / hookObjective / conversionHook / shot1 / voiceoverScript 必须体现上述风格，禁止偏离。
- 与其他变体必须明显差异化，禁止雷同。
"""

    template = """你是 Seedance 2.0 视频 Prompt 编译器。

下面是你的工作规范（必须严格遵守）：

{spec}
{variant_section}
# 输出格式要求

按上述规范完成所有字段的推理，最终输出严格 JSON（不要任何额外文字、不要 Markdown 代码块）：
{
  "userSupplementPrompt": "客户补充意见原样回显（无则空字符串）",
  "productCategory": "商品类型",
  "targetAudience": "目标人群",
  "visualAttributes": "视觉特征",
  "sellingPoints": "核心卖点",
  "purchaseReasons": "购买理由",
  "usageScenes": "使用/穿搭场景",
  "conversionHook": "转化钩子",
  "primaryIntent": "用户真实目标",
  "sellingAngle": "成交角度",
  "emotionalTone": "情绪方向",
  "visualStrategy": "画面策略",
  "defaultModelNeed": "是否需要默认模特辅助",
  "primaryAudience": "核心人群",
  "secondaryAudiences": "次级人群",
  "buyerMindset": "当下心态",
  "purchaseMotivations": "购买动机",
  "purchaseBarriers": "购买阻力",
  "visualTriggers": "视觉触发",
  "voiceoverAngle": "口播角度",
  "routeName": "路线名称",
  "variationSeed": "变化种子",
  "routeReasoning": "路线推理",
  "visualWorld": "视觉世界",
  "differentiators": "差异点",
  "hookType": "Hook类型",
  "hookObjective": "Hook目标",
  "productReasoning": "产品推理",
  "firstFrame": "第一帧",
  "firstAction": "第一动作",
  "audioBeat": "声音节拍",
  "viewerReason": "留存理由",
  "textPolicy": "文字策略",
  "identityAnchors": "身份锚点",
  "riskPoints": "崩坏风险",
  "negativeConstraints": "负向约束",
  "continuityRules": "连续性规则",
  "templateHint": "模板参考（可选，无则空字符串）",
  "shot1": "镜头1 0-3s 内容（含镜头运动/产品角色/模特动作/证明/成交逻辑）",
  "shot2": "镜头2 3-6s",
  "shot3": "镜头3 6-9s",
  "shot4": "镜头4 9-12s",
  "shot5": "镜头5 12-15s",
  "voiceoverTone": "旁白情绪",
  "voiceoverRhythm": "旁白节奏",
  "voiceoverScript": "旁白文案"
}

# 硬约束

1. 所有字段必须填写（除 templateHint 可空），不得使用占位符。
2. shot1-5 必须按规范写法：camera distance/movement + product role + model behavior + proof + conversion logic，禁止抽象表述。
3. 默认无字幕：所有需要精确可读的文字只放在 voiceoverScript，不进入 shot。
4. identityAnchors 明确商品颜色、图案、版型、材质、logo、纹理等不可改变的细节。
5. negativeConstraints 至少包含"不得改变商品颜色/图案/版型/材质/logo/纹理和关键细节""不生成字幕/价格/按钮/品牌口号"。
6. 全部用简体中文。
"""
    return template.replace("{spec}", spec).replace("{variant_section}", variant_section)


# ── 图片压缩 ──
def _compress_image_b64(img_path: str, max_side: int = 1024, quality: int = 85) -> Optional[tuple[str, str]]:
    """把图片缩放到长边 max_side 并转 JPEG base64，返回 (mime, b64)。

    原图可达 6.8MB，base64 后 ~9MB，多张图会让 GPT 请求体超 20MB，
    触发反向代理 403/413。压缩到长边 1024px、JPEG q85 后通常 <200KB，
    既保证 GPT 视觉识别清晰度，又避免请求体过大。
    失败时返回 None，由调用方走原图回退。
    """
    try:
        with Image.open(img_path) as im:
            im = im.convert("RGB")
            w, h = im.size
            if max(w, h) > max_side:
                scale = max_side / max(w, h)
                im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=quality, optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode()
            return ("image/jpeg", b64)
    except Exception:
        return None


# ── 路径展开：目录 → 内部图片文件列表 ──
_IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _expand_image_path(p: str) -> list:
    """把路径展开为图片文件列表（去重保序由调用方处理）。

    - 文件 → [p]
    - 目录 → 内部按文件名排序的图片文件（.jpg/.jpeg/.png/.webp）
    - 不存在/不可访问 → []

    防御：传目录当图片会触发 IsADirectoryError，错误消息含本地绝对路径，
    泄漏进 LLM 输入后会被写进分镜文本。这里提前展开为文件列表。
    """
    if not p:
        return []
    try:
        if os.path.isfile(p):
            return [p]
        if os.path.isdir(p):
            return [
                str(f) for f in sorted(Path(p).iterdir())
                if f.suffix.lower() in _IMG_EXTS and f.is_file()
            ]
    except OSError:
        pass
    return []


# ── 用户消息构造 ──
def _build_user_content(
    user_intent: str,
    product_image_path: Optional[str],
    product_info: Optional[str],
    user_supplement: Optional[str],
    product_image_paths: Optional[list] = None,
) -> list:
    """构造 GPT user message 的 content（支持多模态：图 + 文）。

    支持多张商品图：product_image_paths 优先，回退到单个 product_image_path。
    路径可以是文件或目录（目录会自动展开为内部图片，按文件名排序）。
    """
    content: list = []

    # 合并所有商品图路径（展开目录 + 去重保序）
    all_paths: list = []
    seen: set = set()
    def _add_paths(paths):
        for p in paths:
            if not p:
                continue
            for img in _expand_image_path(p):
                if img not in seen:
                    seen.add(img)
                    all_paths.append(img)

    if product_image_paths:
        _add_paths(product_image_paths)
    if product_image_path:
        _add_paths([product_image_path])

    # 1. 商品图（如果有，支持多张）—— 压缩后再 base64，避免请求体过大触发 403/413
    if all_paths:
        valid_imgs = 0
        for img_path in all_paths:
            try:
                # 优先用压缩后的 JPEG base64（长边 1024px，~150KB）
                compressed = _compress_image_b64(img_path)
                if compressed:
                    mime, img_b64 = compressed
                else:
                    # 压缩失败回退原图
                    with open(img_path, "rb") as f:
                        img_b64 = base64.b64encode(f.read()).decode()
                    ext = Path(img_path).suffix.lower()
                    mime = {
                        ".png": "image/png",
                        ".jpg": "image/jpeg",
                        ".jpeg": "image/jpeg",
                        ".webp": "image/webp",
                    }.get(ext, "image/jpeg")
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{img_b64}"},
                })
                valid_imgs += 1
            except Exception:
                # 脱敏：只暴露文件名，不把本地绝对路径喂给 LLM，避免路径被写进分镜
                content.append({"type": "text", "text": f"（商品图 {Path(img_path).name} 读取失败，已跳过）"})
        if valid_imgs > 0:
            hint = "（上面是商品图" + (f"，共 {valid_imgs} 张" if valid_imgs > 1 else "") + "，请基于图片识别商品类型/颜色/材质/版型/设计点，写入对应字段）"
            content.append({"type": "text", "text": hint})

    # 2. 一句话意图（必填）
    content.append({
        "type": "text",
        "text": f"【用户一句话意图】\n{user_intent}",
    })

    # 3. 产品补充信息（可选）
    if product_info and product_info.strip():
        content.append({
            "type": "text",
            "text": f"【产品补充信息】\n{product_info.strip()}",
        })

    # 4. 客户补充意见（可选，最高优先级）
    if user_supplement and user_supplement.strip():
        content.append({
            "type": "text",
            "text": f"【客户补充意见 - 最高优先级】\n{user_supplement.strip()}",
        })

    # 5. 编译指令
    content.append({
        "type": "text",
        "text": "请按系统提示的规范推理所有字段，输出严格 JSON。",
    })

    return content


# ── 调用 LLM（支持 GPT / Grok 路由） ──
def _resolve_engine_config(engine: str) -> dict:
    """根据 engine 名称解析出 (api_key, base_url, model, label)。

    engine:
      - "gpt"（默认）: 使用 OPENAI_* 配置（gpt-5.6-sol）
      - "grok": 使用 SR_GROK_* 配置（hub.nocannobb.com 网关，grok-4.6），
                与结构性复刻 _step1b_grok_transfer_structure 同源
    """
    e = (engine or "gpt").lower()
    if e == "grok":
        api_key = settings.SR_GROK_API_KEY
        base_url = settings.SR_GROK_BASE_URL
        model = settings.SR_GROK_MODEL
        label = "Grok"
        if not api_key or api_key.startswith("xai-"):
            raise RuntimeError("SR_GROK_API_KEY 未配置（不能是 xai- 占位符）")
    elif e == "gpt":
        api_key = settings.OPENAI_API_KEY
        base_url = settings.OPENAI_BASE_URL
        model = settings.OPENAI_MODEL
        label = "GPT"
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY 未配置")
    else:
        raise ValueError(f"不支持的 engine: {engine}（可选: gpt / grok）")
    return {"api_key": api_key, "base_url": base_url, "model": model, "label": label}


async def _call_gpt(
    system_prompt: str,
    user_content: list,
    engine: str = "gpt",
    _is_fallback: bool = False,
) -> str:
    """调用 LLM 多模态接口，返回原始文本。支持 engine 路由（gpt/grok）。

    使用 OpenAI 兼容 API（GET _resolve_engine_config 解析对应配置）。
    对 403/429/5xx 按指数退避（5/10/15s）重试；对空 content 做内部重试。

    Grok 兜底：当 engine=="grok" 且重试耗尽时，自动切到 GPT-5.6-sol 再试，
    应对 Cloudflare 524 网关超时（hub.nocannobb.com 默认 100s，多图大 payload 容易触发），
    与结构性复刻 app.py:8816-8879 同源策略。
    """
    cfg = _resolve_engine_config(engine)
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "max_completion_tokens": 8192,
    }

    # Grok 首轮减为 2 次重试，给 GPT 兜底留时间预算；其他情况保持 3 次
    max_attempts = 2 if (engine == "grok" and not _is_fallback) else 3
    last_exc: Optional[Exception] = None
    for attempt in range(max_attempts):
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(
                    f"{cfg['base_url']}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                if resp.status_code in (403, 429) or resp.status_code >= 500:
                    body = resp.text[:300]
                    if "insufficient" in body or "额度" in body or "quota" in body:
                        raise RuntimeError(f"{cfg['label']} API 额度不足: {body}")
                    raise httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}: {body}",
                        request=resp.request, response=resp,
                    )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"] or ""
                finish_reason = data["choices"][0].get("finish_reason")
                # 空 content 内部重试（最多 2 次）
                inner_retry = 0
                while not content.strip() and inner_retry < 2:
                    inner_retry += 1
                    print(f"[prompt_compiler] {cfg['label']} 返回空 content (finish_reason={finish_reason})，内部重试第 {inner_retry}/2 次...")
                    async with httpx.AsyncClient(timeout=300.0) as client2:
                        resp2 = await client2.post(
                            f"{cfg['base_url']}/chat/completions",
                            headers=headers, json=payload,
                        )
                        resp2.raise_for_status()
                        data2 = resp2.json()
                        content = data2["choices"][0]["message"]["content"] or ""
                        finish_reason = data2["choices"][0].get("finish_reason")
                if not content.strip():
                    raise RuntimeError(
                        f"{cfg['label']} 连续返回空 content（finish_reason={finish_reason}），"
                        f"请重试或精简输入"
                    )
                return content
        except RuntimeError:
            # 额度不足等不可重试错误，直接向上抛
            raise
        except httpx.HTTPStatusError as e:
            last_exc = e
            if attempt < max_attempts - 1:
                wait = 5 * (attempt + 1)
                print(f"[prompt_compiler] {cfg['label']} 调用 {e.response.status_code}，{wait}s 后重试（第 {attempt+1}/{max_attempts} 次）")
                await asyncio.sleep(wait)
            continue
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last_exc = e
            if attempt < max_attempts - 1:
                wait = 5 * (attempt + 1)
                print(f"[prompt_compiler] {cfg['label']} 调用网络错误 {e}，{wait}s 后重试（第 {attempt+1}/{max_attempts} 次）")
                await asyncio.sleep(wait)
            continue
    # 重试耗尽
    # Grok → GPT 兜底（参考结构性复刻 app.py:8816-8879 策略）
    if engine == "grok" and not _is_fallback:
        print(f"[prompt_compiler] Grok 重试 {max_attempts} 次均失败（{last_exc}），切到 GPT-5.6-sol 兜底...", flush=True)
        try:
            return await _call_gpt(system_prompt, user_content, engine="gpt", _is_fallback=True)
        except Exception as gpt_err:
            print(f"[prompt_compiler] GPT 兜底也失败：{gpt_err}", flush=True)
            raise RuntimeError(
                f"Grok 调用失败（已重试 {max_attempts} 次）: {last_exc}；"
                f"GPT 兜底也失败: {gpt_err}"
            ) from gpt_err
    raise RuntimeError(f"{cfg['label']} 调用失败（已重试 {max_attempts} 次）: {last_exc}") from last_exc


# ── JSON 解析 ──
def _parse_json(content: str) -> Dict[str, Any]:
    """从 GPT 返回中提取 JSON 对象。"""
    if not content:
        raise ValueError("GPT 返回为空")

    # 优先取 ```json 代码块
    fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", content, re.I)
    block = fenced[0].strip() if fenced else None
    if not block:
        m = re.search(r"\{[\s\S]*\}", content)
        if not m:
            raise ValueError("GPT 返回中未找到 JSON 对象")
        block = m.group(0)

    try:
        obj = json.loads(block)
    except json.JSONDecodeError:
        s, e = block.find("{"), block.rfind("}")
        if s != -1 and e > s:
            obj = json.loads(block[s:e + 1])
        else:
            raise

    if not isinstance(obj, dict):
        raise ValueError(f"期望 JSON 对象，实际 {type(obj).__name__}")
    return obj


# ── 拼接最终 seedancePrompt ──
def _assemble_compiled_prompt(m: Dict[str, Any]) -> str:
    """按 prompt-compiler.md 中的模板顺序拼接最终 seedancePrompt 字符串。

    该字符串会作为 custom_prompt 传给 pipeline.step5_video_generate.run。
    """
    def g(k: str) -> str:
        v = m.get(k, "")
        return str(v).strip() if v else ""

    parts: list = []

    user_supp = g("userSupplementPrompt")
    if user_supp:
        parts.append(user_supp)

    parts.append(
        "[15秒带货成交分镜]\n"
        f"镜头1 0-3s：{g('shot1')}\n"
        f"镜头2 3-6s：{g('shot2')}\n"
        f"镜头3 6-9s：{g('shot3')}\n"
        f"镜头4 9-12s：{g('shot4')}\n"
        f"镜头5 12-15s：{g('shot5')}"
    )

    parts.append(
        "[Seedance 2.0 成片要求]\n"
        "生成 9:16、15 秒、电商带货短视频。商品始终清晰可见，镜头节奏服务成交，"
        "不拍无意义空镜。不得改变上传商品的颜色、图案、版型、材质、logo、纹理和关键细节。"
        "画面可有默认模特或局部人物辅助展示，但人物不能抢走商品主体。"
    )

    # 严禁文字生成 —— 固定硬约束，每次编译都自带，不可被覆盖
    parts.append(
        "[严禁文字生成 - 硬约束，不可商量]\n"
        "严禁生成任何文字，包括但不限于：字幕、标题、标语、品牌口号、价格、数字、"
        "按钮、水印、贴纸文字、路牌文字、包装文字、UI 元素文字。\n"
        "画面中不得出现任何可识别的字符（中文、英文、数字、符号均禁止）。\n"
        "所有需要传达的信息只通过画面构图、模特动作、产品展示和后期旁白体现，\n"
        "绝不依赖画面内文字。此约束优先级最高，覆盖一切冲突指令。"
    )

    # 产品一致性 —— 固定硬约束，严格保持产品外观，禁止臆造产品样式
    parts.append(
        "[产品一致性 - 硬约束，不可商量]\n"
        "严格保持产品外观与上传参考图完全一致，必须 1:1 还原以下要素：颜色、图案、"
        "版型、材质、logo、纹理、结构、尺寸比例、印刷细节、配件位置。\n"
        "禁止臆造、推理、脑补产品样式：不得自行添加参考图中没有的颜色、图案、文字、"
        "配件、结构变形；不得「想象」产品的背面/内部/展开形态（参考图未展示的部分不得呈现）。\n"
        "分镜中描述产品时，只能使用参考图可见的特征，不得用「类似」「可能」「大概」等推测性表述。\n"
        "此约束优先级最高，覆盖一切冲突指令。"
    )

    parts.append(
        "[情绪化旁白 - 后期音频脚本，不让视频模型生成字幕]\n"
        f"旁白情绪：{g('voiceoverTone')}\n"
        f"旁白节奏：{g('voiceoverRhythm')}\n"
        f"旁白文案：{g('voiceoverScript')}"
    )

    return "\n\n".join(parts)


# ── 对外入口 ──
async def compile_prompt(
    user_intent: str,
    product_image_path: Optional[str] = None,
    product_info: Optional[str] = None,
    user_supplement: Optional[str] = None,
    product_image_paths: Optional[list] = None,
    variant_count: int = 3,
    engine: str = "gpt",
    variant_strategy: str = "all",
) -> Dict[str, Any]:
    """编译一句话意图为 Seedance 2.0 视频生成 prompt。

    默认串行推理 3 个不同卖点角度的变体脚本（避免单次请求过大导致 504）：
    - 变体 A：痛点切入型
    - 变体 B：场景沉浸型
    - 变体 C：对比证明型

    Args:
        user_intent: 用户一句话意图（必填）
        product_image_path: 商品图本地路径（可选，兼容单图）
        product_info: 产品补充信息（可选）
        user_supplement: 客户补充意见（可选，最高优先级）
        product_image_paths: 多张商品图本地路径列表（可选，优先于 product_image_path）
        variant_count: 变体数量，默认 3。设为 1 时走单条模式（无风格约束）。
        engine: 推理引擎路由，"gpt"（默认，gpt-5.6-sol）或 "grok"（grok-4.6）。
                与结构性复刻 _step1b_grok_transfer_structure 同源配置。
        variant_strategy: 策略选择，覆盖 variant_count。
            "all"（默认）= 三变体一起生成；
            "auto" = 单条无风格约束；
            "A"/"B"/"C" = 仅生成指定风格的一条。

    Returns:
        variant_count=1:
            { "metadata": {...}, "compiledPrompt": str, "specVersion": str }
        variant_count=3:
            { "variants": [...], "specVersion": str }
    """
    if not user_intent or not user_intent.strip():
        raise ValueError("user_intent 不能为空")

    import hashlib
    spec_version = hashlib.md5(_load_spec().encode("utf-8")).hexdigest()[:8]

    user_content = _build_user_content(
        user_intent=user_intent.strip(),
        product_image_path=product_image_path,
        product_info=product_info,
        user_supplement=user_supplement,
        product_image_paths=product_image_paths,
    )

    def _finalize_metadata(vm: dict, variant_style: Optional[dict] = None) -> dict:
        """回显用户原始输入，附加变体元数据"""
        vm.setdefault("userIntent", user_intent.strip())
        if product_info:
            vm.setdefault("productInfo", product_info.strip())
        if user_supplement:
            vm.setdefault("userSupplementPrompt", user_supplement.strip())
        if variant_style:
            vm["variantId"] = variant_style["id"]
            vm["variantName"] = variant_style["name"]
        return vm

    # ── variant_strategy 覆盖 variant_count ──
    # "all" → 三变体；"auto"/"A"/"B"/"C" → 单条（带或不带风格约束）
    _by_id = {s["id"]: s for s in VARIANT_STYLES}
    if variant_strategy in ("A", "B", "C"):
        variant_count = 1
    elif variant_strategy == "auto":
        variant_count = 1
    elif variant_strategy == "all":
        variant_count = 3

    # ── 单条模式（保留原行为） ──
    if variant_count <= 1:
        # 如果指定了 A/B/C 风格，强制走对应风格
        chosen_style = _by_id.get(variant_strategy) if variant_strategy in ("A", "B", "C") else None
        system_prompt = _build_system_prompt(variant_hint=chosen_style)
        raw = await _call_gpt(system_prompt, user_content, engine=engine)
        metadata = _parse_json(raw)
        _finalize_metadata(metadata, chosen_style)
        compiled = _assemble_compiled_prompt(metadata)
        return {
            "metadata": metadata,
            "compiledPrompt": compiled,
            "specVersion": spec_version,
            "variant_strategy": variant_strategy,
        }

    # ── 多变体模式：串行推理，每次指定一种风格 ──
    styles = VARIANT_STYLES[:variant_count]
    variants = []
    for i, style in enumerate(styles):
        print(f"[prompt_compiler] 推理变体 {style['id']}（{style['name']}），第 {i+1}/{len(styles)} 次...")
        system_prompt = _build_system_prompt(variant_hint=style)
        # 变体级重试：最多 2 次（_call_gpt 内部已有 HTTP 级 3 次重试，这里应对 JSON 解析失败）
        vm = None
        last_err: Optional[Exception] = None
        for attempt in range(2):
            try:
                raw = await _call_gpt(system_prompt, user_content, engine=engine)
                vm = _parse_json(raw)
                break
            except Exception as e:
                last_err = e
                if attempt < 1:
                    print(f"[prompt_compiler] 变体 {style['id']} 第 {attempt+1}/2 次失败: {e}，2s 后重试...")
                    await asyncio.sleep(2)
        if vm is None:
            # 重试耗尽：保留错误占位变体，避免前端缺失该方案
            print(f"[prompt_compiler] 变体 {style['id']} 推理失败（已重试 2 次）: {last_err}，使用占位变体")
            variants.append({
                "variantId": style["id"],
                "variantName": style["name"],
                "variantReasoning": f"⚠ 推理失败：{last_err}",
                "metadata": _finalize_metadata({"error": str(last_err)}, style),
                "compiledPrompt": "",
                "error": str(last_err),
            })
            continue
        _finalize_metadata(vm, style)
        compiled = _assemble_compiled_prompt(vm)
        variants.append({
            "variantId": style["id"],
            "variantName": style["name"],
            "variantReasoning": vm.get("routeReasoning", ""),
            "metadata": vm,
            "compiledPrompt": compiled,
        })

    if not variants:
        raise RuntimeError("所有变体推理均失败，请检查 GPT API 状态")

    return {
        "variants": variants,
        "specVersion": spec_version,
    }


# ── Prompt 自查优化 ──
OPTIMIZE_SYSTEM_PROMPT = """你是 Seedance 2.0 视频 Prompt 自查优化专家。

用户会给你一段已编译好的视频生成 Prompt（包含分镜 shot1-shot5、防崩坏约束、旁白等）。
你的任务是对它做分镜逻辑自洽性自查并输出优化后的完整 Prompt。

注意："严禁文字生成"已是固定硬约束段，不需要你检查或修改，原样保留即可。

# 自查维度

1. 分镜逻辑自洽（核心）：
   - shot1→shot5 的镜头推进是否有逻辑断层（如前一镜近景下一镜突然变远景无衔接）
   - 每一镜的"镜头运动 + 产品角色 + 模特动作 + 证明 + 成交逻辑"是否完整自洽
   - 模特动作与产品展示是否冲突（如手部动作与产品位置矛盾）
   - 时间标注 0-3s/3-6s/6-9s/9-12s/12-15s 与内容时长是否匹配
   - 前后镜的产品身份锚点（颜色/图案/版型/材质/logo）是否一致

2. 成交逻辑闭环：
   - Hook（shot1）→ 兴趣（shot2-3）→ 证明（shot4）→ 成交（shot5）是否闭合
   - 是否有"空镜"（无产品、无模特、无成交信息）的废镜头

# 输出要求

直接输出优化后的完整 Prompt 纯文本（与输入格式一致，不要 JSON、不要 Markdown 代码块、不要任何解释）。
如果原文已经自洽，原样返回（最多微调措辞，不改变结构）。
保持原文的"[xxx]"分区结构和分镜时间标注格式不变。
"""


async def optimize_prompt(compiled_prompt: str, engine: str = "gpt") -> Dict[str, Any]:
    """对已编译的 Prompt 做分镜逻辑自洽性自查并优化。

    调用 LLM 复查 compiled_prompt，重点检查分镜逻辑断层、前后镜矛盾。
    "严禁文字生成"约束已固定写入 prompt，不由本函数检查。

    Args:
        compiled_prompt: 已编译的视频生成 Prompt 文本
        engine: 推理引擎路由，"gpt"（默认）或 "grok"。与 compile_prompt 同源。

    Returns:
        {
            "optimizedPrompt": str,   # 优化后的完整 Prompt
            "originalLength": int,    # 原文字符数
            "optimizedLength": int,   # 优化后字符数
            "changed": bool,          # 是否有改动
        }
    """
    if not compiled_prompt or not compiled_prompt.strip():
        raise ValueError("compiled_prompt 不能为空")

    user_content = [
        {
            "type": "text",
            "text": f"请对下面这段已编译的视频生成 Prompt 做自查优化，直接输出优化后的完整 Prompt：\n\n{compiled_prompt}",
        }
    ]

    optimized = await _call_gpt(OPTIMIZE_SYSTEM_PROMPT, user_content, engine=engine)
    optimized = optimized.strip()

    # 去除可能的外层代码块包裹（GPT 偶尔会加）
    if optimized.startswith("```"):
        m = re.search(r"```(?:\w+)?\s*([\s\S]*?)\s*```", optimized)
        if m:
            optimized = m.group(1).strip()

    return {
        "optimizedPrompt": optimized,
        "originalLength": len(compiled_prompt),
        "optimizedLength": len(optimized),
        "changed": optimized != compiled_prompt.strip(),
    }
