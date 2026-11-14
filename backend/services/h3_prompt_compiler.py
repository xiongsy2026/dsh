# -*- coding: utf-8 -*-
"""
H3 Prompt Compiler

把用户的自然语言描述 + 参考图片（可选）编译成 MiniMax H3 适配的专业提示词。

基于 MiniMax-H3 官方 skill：h3-prompt-writing。
规范文本：backend/services/h3_references/base-en.txt + ref-en.txt

支持 5 种模式（按参考图片自动判定）：
  - T2VA: 无图片，纯文本生成
  - I2VA: 仅首帧图
  - L2VA: 仅尾帧图
  - FL2VA: 首尾帧两张图
  - Ref2VA: 全参考模式（多图参考）—— 6 字段结构

输出：H3 规范提示词文本（integrated_multimodal_description + overall_soundscape + non_diegetic_music 等）
"""
from __future__ import annotations

import base64
import io
import os
from pathlib import Path
from typing import List, Optional, Tuple

import httpx
from PIL import Image

from config import settings


# ── 路径常量 ──
REF_DIR = Path(__file__).parent / "h3_references"
BASE_SPEC_PATH = REF_DIR / "base-en.txt"
REF_SPEC_PATH = REF_DIR / "ref-en.txt"


# ── 规范文本缓存 ──
_BASE_SPEC_CACHE: Optional[str] = None
_REF_SPEC_CACHE: Optional[str] = None


def _load_base_spec() -> str:
    """读取基础模式规范（T2VA/I2VA/FL2VA/L2VA）。"""
    global _BASE_SPEC_CACHE
    if _BASE_SPEC_CACHE is None:
        if BASE_SPEC_PATH.exists():
            _BASE_SPEC_CACHE = BASE_SPEC_PATH.read_text(encoding="utf-8")
        else:
            _BASE_SPEC_CACHE = ""
    return _BASE_SPEC_CACHE


def _load_ref_spec() -> str:
    """读取全参考模式规范（Ref2VA）。"""
    global _REF_SPEC_CACHE
    if _REF_SPEC_CACHE is None:
        if REF_SPEC_PATH.exists():
            _REF_SPEC_CACHE = REF_SPEC_PATH.read_text(encoding="utf-8")
        else:
            _REF_SPEC_CACHE = ""
    return _REF_SPEC_CACHE


# ── 模式判定 ──

def detect_mode(image_paths: Optional[List[str]] = None) -> str:
    """根据参考图片数量自动判定 H3 模式。

    判定规则：
    - 0 张图 → T2VA（纯文本）
    - 1 张图 → I2VA（首帧）  ← 单图默认为首帧
    - 2 张图 → FL2VA（首尾帧）
    - 3+ 张图 → Ref2VA（全参考模式）

    Returns:
        模式字符串："T2VA" / "I2VA" / "FL2VA" / "L2VA" / "Ref2VA"
    """
    if not image_paths:
        return "T2VA"
    n = len(image_paths)
    if n == 1:
        return "I2VA"
    if n == 2:
        return "FL2VA"
    return "Ref2VA"


# ── 图片压缩（复用 prompt_compiler 的策略） ──

def _compress_image_b64(img_path: str, max_size: int = 1024) -> Optional[Tuple[str, str]]:
    """压缩图片到长边 max_size，返回 (mime, base64)。失败返回 None。"""
    try:
        img = Image.open(img_path)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > max_size:
            ratio = max_size / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return ("image/jpeg", b64)
    except Exception as e:
        print(f"[h3_prompt_compiler] 图片压缩失败 {img_path}: {e}")
        return None


# ── 系统 Prompt 构造 ──

def _build_system_prompt(mode: str, channel: str = "runninghub", output_language: str = "auto") -> str:
    """构造 system prompt：规范全文 + 当前模式说明 + 输出格式约束。

    channel="manxue" 时：
      - 不输出图片对齐指令首行
      - 不使用 <Picture N> 引用符号（改用自然语言描述图中的内容）
      - 保留 [Shot N] / <t> / <d> / 时间戳等 H3 通用标记
    output_language:
      - "auto": 跟随用户描述语言
      - "zh": 强制中文 Prompt（字段名仍为英文）
      - "en": 强制英文 Prompt
    """
    base_spec = _load_base_spec()
    ref_spec = _load_ref_spec()

    # 模式说明
    mode_desc = {
        "T2VA": "纯文本生成视频（T2VA）。无参考图片，从用户描述构建完整音视频时间线。",
        "I2VA": "首帧引导（I2VA）。用户提供 1 张首帧图，视频从首帧开始向前发展。",
        "FL2VA": "首尾帧（FL2VA）。用户提供首帧和尾帧，视频在两者间连续插值。",
        "L2VA": "尾帧引导（L2VA）。用户提供 1 张尾帧图，视频收敛到尾帧。",
        "Ref2VA": "全参考模式（Ref2VA）。用户提供多张参考图，使用 6 字段结构。",
    }.get(mode, "未知模式")

    # 输出结构说明
    if mode == "Ref2VA":
        output_struct = """输出 6 个字段（Ref2VA 全参考模式）：
1. subject_definitions: 定义所有参考内容及其引用标签（<Picture N> / <Subject N> / <Video N> / <Audio N>）
2. summary: 一句话概述视频主题
3. retention_analysis: 描述参考内容如何被保留、迁移或复用
4. detailed_description: 按播放顺序描述画面、动作、镜头、声音、对白
5. overall_soundscape: 环境声、物理动作声、非语言人声
6. non_diegetic_music: 画外背景音乐"""
        spec_section = f"{base_spec}\n\n# 全参考模式补充规范\n{ref_spec}"
    else:
        output_struct = """输出 3 个核心字段（基础模式）：
1. integrated_multimodal_description: 沿时间线描述画面、动作、镜头、说话人、对白、戏内声音
2. overall_soundscape: 环境声、物理动作声、非语言人声
3. non_diegetic_music: 画外背景音乐"""
        spec_section = base_spec

    # 渠道差异：manxue 网关不解析 <Picture N> 引用符号
    is_manxue = channel == "manxue"
    force_zh = (output_language or "auto").lower() == "zh"
    force_en = (output_language or "auto").lower() == "en"
    if force_zh:
        lang_rule = (
            "语言：必须输出中文 Prompt。"
            "画面、人物、商品、动作、场景、风格、时间线、音效描述全部使用中文，不得把整段写成英文。"
            "上方英文规范仅作结构参考，不要模仿其语言。"
            "运镜类型可用英文术语（Pan/Tilt/Track/Zoom/Static/Dolly），但整句必须是中文。"
            "口播原文保持原语言不要翻译；中文口播用 <d>[Chinese] 原文</d>。"
        )
        dialogue_example = "<d>[Chinese] 你好。</d>"
        alignment_example = "目标视频在 0.00 秒完整引用 <Picture 1>（来自 [Shot 1]）。"
        field_lang_rule = "字段名保持英文（integrated_multimodal_description 等），字段内容必须是中文"
    elif force_en:
        lang_rule = "语言：必须输出英文 Prompt。口播原文保持原语言，中文口播放在 <d>[Chinese] 原文</d> 中。"
        dialogue_example = "<d>[English] Hello there.</d>"
        alignment_example = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."
        field_lang_rule = "字段名保持英文（integrated_multimodal_description 等），字段内容使用英文"
    else:
        lang_rule = "语言：用户描述用什么语言，prompt 就用什么语言（中文描述→中文 prompt，英文描述→英文 prompt）"
        dialogue_example = "<d>[Chinese] 你好。</d>"
        alignment_example = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."
        field_lang_rule = "字段名保持英文（integrated_multimodal_description 等），字段内容跟随用户语言"

    # 关键规则
    if is_manxue:
        # manxue：图通过 first_frame_image / last_frame_image 字段单独传，prompt 中用自然语言描述
        rules = f"""# 关键规则
1. 镜头切分用 [Shot 1]、[Shot 2] ... 标记，按时间顺序
2. 运镜必须含三个维度：类型（Pan/Tilt/Track/Zoom/Static/Dolly 等）+ 幅度 + 速度
3. 对话用 <d>[Language] speaker_text</d> 格式，例如 {dialogue_example}
4. 屏幕文字用 <t>text</t> 标记
5. 时间用秒，格式 0.00s / 3.50s / 10.00s
6. **不要使用 <Picture N> 引用符号**（参考图通过 API 字段单独传递，prompt 中用自然语言描述图中物体的外观、颜色、结构）
7. **不要输出图片对齐指令首行**（不要写 "{alignment_example}"）
8. {lang_rule}
9. 不要输出任何解释性文字，只输出最终 prompt 文本本身
10. {field_lang_rule}"""
        alignment_hint = "（直接从 integrated_multimodal_description 开始，不要图片对齐指令首行）"
    else:
        # runninghub：导演台工作流支持 <Picture N> 引用
        rules = f"""# 关键规则
1. 镜头切分用 [Shot 1]、[Shot 2] ... 标记，按时间顺序
2. 运镜必须含三个维度：类型（Pan/Tilt/Track/Zoom/Static/Dolly 等）+ 幅度 + 速度
3. 对话用 <d>[Language] speaker_text</d> 格式，例如 {dialogue_example}
4. 屏幕文字用 <t>text</t> 标记
5. 时间用秒，格式 0.00s / 3.50s / 10.00s
6. 参考图引用：{mode} 模式下用 <Picture 1>、<Picture 2> ... 标签
7. {lang_rule}
8. 不要输出任何解释性文字，只输出最终 prompt 文本本身
9. {field_lang_rule}"""
        alignment_hint = (
            f"（首行必须是图片对齐指令，例如：{alignment_example} 然后空一行）"
            if mode != "T2VA"
            else "（T2VA 无图片对齐指令，直接从 integrated_multimodal_description 开始）"
        )

    lang_override = ""
    if force_zh:
        lang_override = """
# 语言覆盖（优先级高于上方英文规范）
- 必须输出中文 Prompt，不得把画面/动作/场景/音效写成英文段落
- 字段名保持英文，字段内容必须是中文
- H3 标记保持原格式：[Shot N]、<Picture N>、<Subject N>、<t>、<d>
- 运镜类型可用英文术语，但整句用中文
- 口播原文不要翻译
"""

    return f"""你是 MiniMax H3 视频 Prompt 编译器。

# 任务
把用户的自然语言描述 + 参考图片（如果有）编译成 H3 适配的专业提示词。

# 当前模式
{mode_desc}

# H3 提示词规范（必须严格遵守）
{spec_section}

# 输出结构要求
{output_struct}

{rules}
{lang_override}
# 输出格式
直接输出最终 prompt 文本，格式如下（不要加 markdown 代码块标记）：

{("subject_definitions: ..." if mode == "Ref2VA" else "integrated_multimodal_description: [Shot 1] ...")}
{("summary: ..." if mode == "Ref2VA" else "")}
{("retention_analysis: ..." if mode == "Ref2VA" else "")}
{("detailed_description: ..." if mode == "Ref2VA" else "")}
overall_soundscape: ...
non_diegetic_music: ...

{alignment_hint}
"""


# ── 用户消息构造 ──

def _build_user_content(
    user_description: str,
    image_paths: Optional[List[str]] = None,
    mode: str = "T2VA",
    channel: str = "runninghub",
    voiceover_segments: Optional[List[dict]] = None,
    duration: Optional[float] = None,
    output_language: str = "auto",
) -> list:
    """构造 user message 的 content（支持多模态：图 + 文）。

    无论哪个渠道，图片都会传给 LLM 用于理解内容。
    channel="manxue" 时，hint 文本不使用 <Picture N> 引用符号，
    让 LLM 改用自然语言描述图中物体的外观。
    """
    content: list = []
    is_manxue = channel == "manxue"

    # 1. 参考图片（LLM 需要看到图才能描述其内容）
    if image_paths:
        valid = 0
        for img_path in image_paths:
            if not img_path or not os.path.exists(img_path):
                continue
            compressed = _compress_image_b64(img_path)
            if compressed:
                mime, img_b64 = compressed
            else:
                with open(img_path, "rb") as f:
                    img_b64 = base64.b64encode(f.read()).decode()
                ext = Path(img_path).suffix.lower()
                mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(ext, "image/jpeg")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{img_b64}"},
            })
            valid += 1
        if valid > 0:
            if is_manxue:
                # manxue：不使用 <Picture N> 引用，让 LLM 用自然语言描述图中产品
                if mode == "FL2VA":
                    hint = f"（上面共 {valid} 张图：第 1 张是首帧，第 2 张是尾帧。请在 prompt 中用自然语言详细描述图中产品的外观、颜色、结构、材质，不要使用 <Picture N> 引用符号）"
                elif mode == "I2VA":
                    hint = f"（上面是首帧图。请在 prompt 中用自然语言详细描述图中产品的外观、颜色、结构、材质，不要使用 <Picture N> 引用符号。视频将从该首帧开始向前发展）"
                elif mode == "L2VA":
                    hint = f"（上面是尾帧图。请在 prompt 中用自然语言详细描述图中产品的外观、颜色、结构、材质，不要使用 <Picture N> 引用符号。视频将收敛到该尾帧）"
                else:
                    hint = f"（上面共 {valid} 张参考图。请在 prompt 中用自然语言详细描述图中产品的外观、颜色、结构、材质，不要使用 <Picture N> 引用符号）"
            else:
                # runninghub：使用 <Picture N> 引用符号
                if mode == "FL2VA":
                    hint = f"（上面共 {valid} 张图：第 1 张是首帧 <Picture 1>，第 2 张是尾帧 <Picture 2>）"
                elif mode == "I2VA":
                    hint = f"（上面是首帧图 <Picture 1>，视频从该帧开始向前发展）"
                elif mode == "L2VA":
                    hint = f"（上面是尾帧图 <Picture 1>，视频收敛到该帧）"
                else:
                    labels = "、".join([f"<Picture {i+1}>" for i in range(valid)])
                    hint = f"（上面共 {valid} 张参考图，依次为 {labels}）"
            content.append({"type": "text", "text": hint})

    # 2. 用户自然语言描述（必填）
    content.append({
        "type": "text",
        "text": f"【用户自然语言描述】\n{user_description}",
    })

    # 2.5 口播文案（可选，来自 ASR 反推）
    if voiceover_segments:
        voiceover_text = " ".join(s["text"] for s in voiceover_segments)
        timeline = "\n".join(
            f"  [{s['start']:.1f}-{s['end']:.1f}s] {s['text']}" for s in voiceover_segments
        )
        if (output_language or "auto").lower() == "zh":
            vo_style = (
                "- 旁白用「画外音（S1）以画外旁白说：<d>[Chinese] ...</d>，画面人物嘴唇不动」\n"
                "- 人物对口型用「<Subject 1> 对镜头说：<d>[Chinese] ...</d>」\n"
            )
        else:
            vo_style = (
                "- 旁白用 \"The voiceover (S1) says in an off-screen voiceover: <d>[Chinese] ...</d>\"\n"
                "- 人物对口型用 \"<Subject 1> says to camera: <d>[Chinese] ...</d>\"\n"
            )
        content.append({
            "type": "text",
            "text": (
                f"【原片口播文案（ASR 识别，带时间戳）】\n{timeline}\n\n"
                f"完整文案：{voiceover_text}\n\n"
                "⚠️ 编译规则：\n"
                "- 若原片为口播型（人物对镜头说话或画外旁白），请在 detailed_description 中"
                "对应时间轴插入 <d>[Chinese] 口播文案</d> 对白标签\n"
                f"{vo_style}"
                "- 文案按 ASR 时间戳分段对应到各 Shot，不要合并成一大段\n"
                "- 若原片为纯展示型无口播，忽略此段文案"
            ),
        })

    # 3. 编译指令
    duration_constraint = ""
    if duration and duration > 0:
        duration_constraint = (
            f"\n⚠️ 时间轴约束（必须严格遵守）：\n"
            f"- 视频总时长为 {duration:.1f} 秒\n"
            f"- 所有分镜的时间戳必须从 0.00s 开始，最终 Shot 的结束时间必须等于 {duration:.1f}s\n"
            f"- 分镜数量和各 Shot 时长由你根据场景内容自主分配，但总时长必须精确等于 {duration:.1f} 秒\n"
            f"- 不要在 {duration:.1f}s 之前结束，也不要超过 {duration:.1f}s\n"
            f"- ASR 口播段时间戳仅用于对齐口播内容，不代表视频总时长；口播结束后到 {duration:.1f}s 之间的时间也要分配分镜"
        )
    lang_tail = ""
    if (output_language or "auto").lower() == "zh":
        lang_tail = "必须输出中文 Prompt：字段名保持英文，字段内容全部用中文，不要写成英文段落。"
    content.append({
        "type": "text",
        "text": (
            f"请基于以上{('图片和' if image_paths else '')}描述，按 {mode} 模式规范输出最终 H3 prompt。"
            f"{lang_tail}{duration_constraint}"
        ),
    })

    return content


# ── 调用 GPT（OpenAI 兼容接口，复用 prompt_compiler 的重试逻辑） ──

async def _call_gpt(system_prompt: str, user_content: list, *, stream: bool = False) -> str:
    """调用 GPT (gpt-5.6-sol) 多模态接口，返回原始文本。

    使用 OpenAI 兼容 API（OPENAI_BASE_URL + OPENAI_API_KEY + OPENAI_MODEL）。
    对 403/429/5xx 按指数退避重试；对空 content 做内部重试。
    stream=True 时启用流式接收，避免上游网关对长 prompt 的 idle 超时（504）。
    """
    headers = {
        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.OPENAI_MODEL,  # gpt-5.6-sol
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "max_completion_tokens": 16384,
    }
    if stream:
        payload["stream"] = True

    async def _stream_collect(client: httpx.AsyncClient) -> tuple[str, str]:
        """流式收集 content 与 finish_reason。"""
        chunks: list[str] = []
        finish_reason = ""
        async with client.stream(
            "POST",
            f"{settings.OPENAI_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=httpx.Timeout(connect=30.0, read=None, write=30.0, pool=30.0),
        ) as resp:
            if resp.status_code in (403, 429) or resp.status_code >= 500:
                body = (await resp.aread()).decode("utf-8", errors="replace")[:300]
                if "insufficient" in body or "额度" in body or "quota" in body:
                    raise RuntimeError(f"GPT API 额度不足: {body}")
                raise httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}: {body}",
                    request=resp.request, response=resp,
                )
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    obj = __import__("json").loads(data_str)
                except Exception:
                    continue
                try:
                    delta = obj["choices"][0]["delta"]
                    if delta.get("content"):
                        chunks.append(delta["content"])
                    if obj["choices"][0].get("finish_reason"):
                        finish_reason = obj["choices"][0]["finish_reason"]
                except (KeyError, IndexError):
                    continue
        return "".join(chunks), finish_reason

    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            if stream:
                async with httpx.AsyncClient() as client:
                    content, finish_reason = await _stream_collect(client)
                if not content.strip():
                    raise RuntimeError(
                        f"GPT 流式返回空 content（finish_reason={finish_reason}），请重试或精简输入"
                    )
                return content
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(
                    f"{settings.OPENAI_BASE_URL}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                if resp.status_code in (403, 429) or resp.status_code >= 500:
                    body = resp.text[:300]
                    if "insufficient" in body or "额度" in body or "quota" in body:
                        raise RuntimeError(f"GPT API 额度不足: {body}")
                    raise httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}: {body}",
                        request=resp.request, response=resp,
                    )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"] or ""
                finish_reason = data["choices"][0].get("finish_reason")
                # 空 content 内部重试
                inner = 0
                while not content.strip() and inner < 2:
                    inner += 1
                    async with httpx.AsyncClient(timeout=300.0) as client2:
                        resp2 = await client2.post(
                            f"{settings.OPENAI_BASE_URL}/chat/completions",
                            headers=headers, json=payload,
                        )
                        resp2.raise_for_status()
                        data2 = resp2.json()
                        content = data2["choices"][0]["message"]["content"] or ""
                        finish_reason = data2["choices"][0].get("finish_reason")
                if not content.strip():
                    raise RuntimeError(
                        f"GPT 连续返回空 content（finish_reason={finish_reason}），请重试或精简输入"
                    )
                return content
        except RuntimeError:
            raise
        except httpx.HTTPStatusError as e:
            last_exc = e
            if attempt < 2:
                wait = [5, 10, 15][attempt]
                print(f"[h3_prompt_compiler] HTTP {e.response.status_code}，{wait}s 后重试（{attempt+1}/3）...")
                await __import__("asyncio").sleep(wait)
            continue
        except Exception as e:
            last_exc = e
            if attempt < 2:
                await __import__("asyncio").sleep(5)
            continue

    raise RuntimeError(f"GPT 调用失败（重试 3 次）: {last_exc}")


# ── 主入口 ──

async def compile_h3_prompt(
    user_description: str,
    image_paths: Optional[List[str]] = None,
    channel: str = "runninghub",
    voiceover_segments: Optional[List[dict]] = None,
    duration: Optional[float] = None,
    output_language: str = "auto",
) -> dict:
    """编译 H3 提示词。

    Args:
        user_description: 用户自然语言描述（必填）
        image_paths: 参考图片绝对路径列表（可选）
        channel: 渠道
          - "runninghub": RunningHub 导演台工作流（支持 <Picture N> 引用符号）
          - "manxue": manxue 网关（OpenAI 兼容协议，prompt 为纯文本，不解析引用符号；
                      图通过 first_frame_image/last_frame_image 字段单独传递）
        voiceover_segments: ASR 反推的口播文案段（可选，[{"start","end","text"}]）
        output_language: "auto" 跟随用户描述；"zh" 强制中文；"en" 强制英文

    Returns:
        {
            "mode": "T2VA"/"I2VA"/"FL2VA"/"L2VA"/"Ref2VA",
            "prompt": "最终 H3 提示词文本",
            "image_count": int,
            "channel": str,
        }
    """
    if not user_description or not user_description.strip():
        raise ValueError("用户自然语言描述不能为空")

    # 过滤掉不存在的图片路径
    valid_paths: List[str] = []
    if image_paths:
        for p in image_paths:
            if p and os.path.exists(p):
                valid_paths.append(p)

    mode = detect_mode(valid_paths)
    system_prompt = _build_system_prompt(mode, channel=channel, output_language=output_language)
    user_content = _build_user_content(
        user_description.strip(), valid_paths, mode,
        channel=channel, voiceover_segments=voiceover_segments,
        duration=duration, output_language=output_language,
    )

    prompt = await _call_gpt(system_prompt, user_content)

    return {
        "mode": mode,
        "prompt": prompt.strip(),
        "image_count": len(valid_paths),
        "channel": channel,
    }


# ════════════════════════════════════════════════════════════════════
# 9 宫格带货工作流（三阶段：脚本 → 图提示词 → Ref2VA 视频提示词）
# ════════════════════════════════════════════════════════════════════
# 设计要点：
#   - 模式：Ref2VA（9 张 <Picture N> 锚点喂给 H3）
#   - 单段时长：15 秒
#   - 段内 Shot 数：LLM 自主决定（1-9 个，时长不等）
#   - 跨段衔接：段(i+1) sc1 = 段(i) sc9 物理同一张图
#   - 9 图生成：以原始产品图为参考 i2i 生成
#   - 段数 N：用户在前端自选（1-6 段）
#   - 输出格式：JSON 结构化
#   - 话术：LLM 自动生成画外音
#   - 画幅：9:16 竵屏
# ────────────────────────────────────────────────────────────────────


# ── 阶段 1：9 宫格脚本 ──

def _build_storyboard_system_prompt(segment_count: int) -> str:
    """构造阶段 1 系统提示词：9 宫格分镜脚本 + 画外音话术 + 跨段衔接。"""
    return f"""你是工业级带货视频分镜导演 + H3 Ref2VA 9 宫格分镜编剧。

# 任务
基于用户输入的产品图、产品名、核心卖点，推理出 {segment_count} 段 × 每段 9 宫格的连续分镜脚本，并自动生成带货话术。每段时长 15 秒，整片总时长 {segment_count * 15} 秒。

# 9 宫格设计法则（本模板灵魂）
9 个格子是**同一物理世界的连续时间切片**，不是 9 个独立场景：
1. **世界连续**：9 格共用同一场景、同一时间窗、同一光照基调；除非剧情明确转场，严禁换场景/跳时间/变光照。
2. **资产状态延续**：产品外观、摆放位置、灯光、桌面/地面状态跨格为真，随剧情连续变化，禁止无交代跳变。
3. **位置时间轴推进**：产品/道具在格与格之间的位置按时间连续移动，禁止瞬移；若文案要求空间位移，格间要能看出推进轨迹。
4. **相邻格递进（硬切三选二铁律）**：相邻两格必须满足以下 ≥2 条（且绝不越 180° 轴线，产品相对左右位置跨格一致）：
   - 景别跨 ≥1-2 档（如 中景↔特写、近景↔全景），严禁连续两格同景别；
   - 机位角度变化 ≥30°；
   - cut on action：前一格末段动作在后一格开格继续做。
5. **入格/出格衔接**：每段首格 (sc1) 承接上一段末格 (sc9) 的动势/站位/光线；末格 (sc9) 为下一段铺垫离场动机或新动作起点。
6. **9 格全有效**：每格都是有效画面，严禁黑屏/废帧/纯黑过渡格。
7. **产品锁定（最高优先级）**：产品外观由用户输入的产品图决定，9 格中产品形态、品牌、颜色、款式必须与产品图一致；只有产品被实际使用时才写变化部分（被拿起、被使用、被拆解等）。

# 跨段衔接（本工作流核心）
- 段(i+1) 的 sc1 = 段(i) 的 sc9（**物理同一张图**），视觉状态完全一致。
- LLM 必须在段(i+1) storyboard 的 sc1 字段中显式标注 `"inherits_from": "segment_{{i}}_sc9"`。
- 段(i+1) 的 sc1 描述必须与段(i) 的 sc9 描述逐字一致（人物位置/产品姿态/光线/场景）。
- 180° 原则：相邻段产品相对位置不翻转。
- 动势衔接：段(i) sc9 的动作方向 = 段(i+1) sc1 的延续（如「手抬起」→「手抬起中」）。

# 单段时长与 Shot 切分
- 单段固定 15 秒，9 宫格按时间均匀分布（每格约 1.875 秒）。
- **段内 Shot 数由你自主决定**（1-9 个，时长不等），在 storyboard 中给出 `shot_structure` 字段。
- 示例：单段 1 Shot（长镜头连续推进） / 3 Shot（前 5s / 中 5s / 后 5s）/ 9 Shot（每格一个独立镜头硬切）均可，按卖点和动作合理选择。
- 每个 Shot 的起止时间戳严格递增，落在 [0.00s, 15.00s] 区间内。

# 卖点视觉化（带货核心）
- 卖点必须翻译为**100% 纯视觉**的物理动作，禁止抽象词（"强大"、"高效"、"智能"等）。
- 例：「强力吸尘」→「粉尘颗粒从车座表面被吸入吸嘴，尘桶内涡流可见」。
- 例：「无线便携」→「吸尘器从车座旁被提起，无电源线连接，镜头随产品移动」。
- 例：「一秒清尘」→「吸头扫过 1 秒，原污渍区域恢复洁净」。

# 话术生成（画外音）
- 自动生成 {segment_count} 段带货话术，每段 1-2 句，与该段画面强相关。
- 话术风格：简洁、抓人、有行动号召。
- 用中文写出完整话术文本，标记 `voiceover_script` 字段。
- 话术与画面动作时间戳对应（用户后续在 videoPrompt 阶段会以画外音格式插入 detailed_description）。
- 示例：「无线强力，一秒清尘。」「车载吸尘，洁净如新。」

# 输出格式（严格 JSON）
```json
{{
  "segment_count": {segment_count},
  "total_duration_sec": {segment_count * 15},
  "aspect_ratio": "9:16",
  "segments": [
    {{
      "segment_id": 1,
      "duration_sec": 15,
      "shot_structure": [
        {{"shot_id": 1, "start_sec": "0.00", "end_sec": "5.00", "sc_range": [1, 2, 3]}},
        {{"shot_id": 2, "start_sec": "5.00", "end_sec": "10.00", "sc_range": [4, 5, 6]}},
        {{"shot_id": 3, "start_sec": "10.00", "end_sec": "15.00", "sc_range": [7, 8, 9]}}
      ],
      "storyboard": [
        {{
          "sc": 1,
          "timestamp_sec": "0.00",
          "shot_id": 1,
          "scene_description": "中景，产品放在车座上，自然光从车窗外打入。",
          "action": "产品静止，镜头从远处缓慢推进。",
          "product_state": "完整产品外观，吸头朝向画面右侧。",
          "camera": "Push In, small amplitude, slow speed"
        }},
        {{
          "sc": 2,
          "timestamp_sec": "1.875",
          "shot_id": 1,
          "scene_description": "...",
          "action": "...",
          "product_state": "...",
          "camera": "..."
        }},
        ... (sc 3-8 同样格式)
        {{
          "sc": 9,
          "timestamp_sec": "15.00",
          "shot_id": 3,
          "scene_description": "...",
          "action": "...",
          "product_state": "...",
          "camera": "Static Shot"
        }}
      ],
      "voiceover_script": "无线强力，一秒清尘。",
      "voiceover_timing_sec": "0.5-3.0"
    }},
    {{
      "segment_id": 2,
      "duration_sec": 15,
      "shot_structure": [...],
      "storyboard": [
        {{
          "sc": 1,
          "timestamp_sec": "0.00",
          "shot_id": 1,
          "inherits_from": "segment_1_sc9",
          "scene_description": "（与段1 sc9 逐字一致）...",
          "action": "...",
          "product_state": "...",
          "camera": "..."
        }},
        ... (sc 2-9)
      ],
      "voiceover_script": "...",
      "voiceover_timing_sec": "..."
    }}
  ]
}}
```

# 严禁
- 禁止输出 JSON 以外的任何文字（无前言、无解释、无 markdown 代码块标记）。
- 禁止抽象情绪词（"感到震撼"、"很强大"）。
- 禁止无交代跳变（产品瞬移、光线突变）。
- 禁止越 180° 轴线（相邻段产品左右位置翻转）。
- 禁止黑屏/废帧。
- 禁止编造产品图未呈现的外观特征（颜色、品牌、型号等只能忠于产品图）。

# 输出
直接输出 JSON 对象本身，无任何包裹。
"""


def _build_storyboard_user_content(
    product_image_path: str,
    product_name: str,
    selling_points: list[str],
    segment_count: int,
    extra_context: str = "",
) -> list:
    """构造阶段 1 用户消息：产品图 + 产品名 + 卖点 + 段数。"""
    content: list = []

    # 1. 产品图（LLM 需要看图才能锁定产品外观）
    if product_image_path and os.path.exists(product_image_path):
        compressed = _compress_image_b64(product_image_path)
        if compressed:
            mime, img_b64 = compressed
        else:
            with open(product_image_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()
            ext = Path(product_image_path).suffix.lower()
            mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(ext, "image/jpeg")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{img_b64}"},
        })
        content.append({
            "type": "text",
            "text": "（上面是产品图。9 宫格所有格中的产品外观必须与此图一致：颜色、款式、品牌、型号、材质等忠于原图。产品被使用时可写姿态变化，但外观不变。）"
        })

    # 2. 文本输入
    text = f"""【产品名】
{product_name}

【核心卖点】
"""
    for i, sp in enumerate(selling_points, 1):
        text += f"{i}. {sp}\n"

    text += f"""
【段数】
{segment_count} 段（每段 15 秒，总时长 {segment_count * 15} 秒）

【画幅】
9:16 竵屏

【任务】
请按系统提示词规范，输出 {segment_count} 段 9 宫格分镜 JSON。
段(i+1) sc1 必须与段(i) sc9 物理同一张图，在 storyboard 中标注 `inherits_from`。
每段自动生成带货话术（画外音）。"""
    if extra_context:
        text += f"\n\n【补充上下文】\n{extra_context}"

    content.append({"type": "text", "text": text})
    return content


async def compile_h3_storyboard(
    product_image_path: str,
    product_name: str,
    selling_points: list[str],
    segment_count: int = 2,
    extra_context: str = "",
) -> dict:
    """阶段 1：9 宫格分镜脚本 + 话术。

    Args:
        product_image_path: 产品图绝对路径（必填，用于锁定产品外观）
        product_name: 产品名（必填）
        selling_points: 核心卖点列表（1-10 条）
        segment_count: 段数 N（1-6 段，每段 15 秒）
        extra_context: 补充上下文（可选，如目标平台、风格偏好等）

    Returns:
        {
            "storyboard_json": dict,   # 9 宫格脚本 JSON
            "raw_response": str,       # LLM 原始返回
        }
    """
    if not product_image_path or not os.path.exists(product_image_path):
        raise ValueError("产品图路径无效")
    if not product_name.strip():
        raise ValueError("产品名不能为空")
    if not selling_points:
        raise ValueError("至少提供 1 条卖点")
    if segment_count < 1 or segment_count > 6:
        raise ValueError("段数必须在 1-6 之间")

    system_prompt = _build_storyboard_system_prompt(segment_count)
    user_content = _build_storyboard_user_content(
        product_image_path, product_name.strip(), selling_points, segment_count, extra_context
    )
    raw = await _call_gpt(system_prompt, user_content, stream=True)

    # 提取 JSON（容错：LLM 可能包裹 ```json ... ```）
    import json
    import re
    cleaned = raw.strip()
    # 去除可能的 markdown 包裹
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    try:
        storyboard_json = json.loads(cleaned)
    except json.JSONDecodeError as e:
        # 尝试定位 JSON 起止
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            try:
                storyboard_json = json.loads(match.group(0))
            except json.JSONDecodeError:
                raise RuntimeError(f"LLM 返回非合法 JSON: {e}\n原始返回前 500 字: {raw[:500]}")
        else:
            raise RuntimeError(f"LLM 返回非合法 JSON: {e}\n原始返回前 500 字: {raw[:500]}")

    return {
        "storyboard_json": storyboard_json,
        "raw_response": raw,
    }


# ── 阶段 2：9:16 竵屏图片提示词 ──

def _build_image_prompt_system_prompt() -> str:
    """构造阶段 2 系统提示词：9:16 竵屏图片生成提示词。"""
    return """你是 9:16 竵屏电商图片生成提示词工程师。

# 任务
把 9 宫格分镜脚本中每一格的 scene_description + action + product_state + camera 转译为图片生成模型（SD/Flux/i2i）可用的中文提示词。

# 输入
用户会提供：
1. 9 宫格分镜 JSON（来自阶段 1 的输出）
2. 产品名（用于提示词中锁定产品）

# 输出格式（严格 JSON）
```json
{
  "aspect_ratio": "9:16",
  "segments": [
    {
      "segment_id": 1,
      "first_frame_prompts": [
        {
          "sc": 1,
          "timestamp_sec": "0.00",
          "prompt": "9:16 竵屏，中景，工业风简洁背景，自然光从画面左侧打入。产品 XXX 放在木质车座上，吸头朝右，整体完整无包装。镜头视角略低于产品，前景虚化。",
          "negative_prompt": "lowres, bad anatomy, bad hands, text, watermark, logo, blur, deformed, extra limbs, duplicate, cropped"
        },
        {
          "sc": 2,
          "timestamp_sec": "1.875",
          "prompt": "...",
          "negative_prompt": "..."
        },
        ... (sc 3-9 同样格式)
      ]
    },
    {
      "segment_id": 2,
      "first_frame_prompts": [
        {
          "sc": 1,
          "timestamp_sec": "0.00",
          "prompt": "（与段1 sc9 完全一致的画面描述）...",
          "negative_prompt": "...",
          "inherits_from": "segment_1_sc9"
        },
        ...
      ]
    }
  ]
}
```

# 提示词写法
1. **开头固定**：每条 prompt 以 `9:16 竵屏，` 开头，锁定画幅。
2. **景别在前**：中景 / 近景 / 全景 / 特写 / 大特写 等。
3. **场景描述**：简洁物理描写，无抽象词。
4. **产品状态**：忠于产品图外观（颜色/款式/品牌/型号/材质不变），只写姿态变化。
5. **光线/构图**：明确光源方向、镜头视角、前景/背景虚化。
6. **运镜不可写**：图片是静止的，camera 字段信息转化成「视角/前景虚化/景别」等静帧能呈现的元素，不能写 Push In / Pan 等。
7. **每条 prompt 长度**：50-150 中文字符。
8. **negative_prompt 固定模板**：`lowres, bad anatomy, bad hands, text, watermark, logo, blur, deformed, extra limbs, duplicate, cropped`（可按产品特性微调）。

# 跨段共享
- 段(i+1) sc1 的 prompt 必须与段(i) sc9 的 prompt **逐字一致**（物理同一张图）。
- 在段(i+1) sc1 中标注 `"inherits_from": "segment_{{i}}_sc9"`，prompt 字段复用段(i) sc9 的内容。

# 严禁
- 禁止输出 JSON 以外文字。
- 禁止抽象词（"震撼"、"高端"、"智能"）。
- 禁止运镜词（Push In / Pan / Zoom 等）。
- 禁止产品外观与产品图不符。

# 输出
直接输出 JSON 对象本身，无任何包裹。"""


def _build_image_prompt_user_content(storyboard_json: dict, product_name: str) -> list:
    """构造阶段 2 用户消息：阶段1脚本 + 产品名。"""
    import json as _json
    content: list = [{
        "type": "text",
        "text": f"""【9 宫格分镜脚本（阶段 1 输出）】
{_json.dumps(storyboard_json, ensure_ascii=False, indent=2)}

【产品名】
{product_name}

【任务】
把上面 9 宫格脚本中每格的 scene_description / action / product_state / camera 转译为 9:16 竵屏图片生成提示词。
段(i+1) sc1 的 prompt 必须与段(i) sc9 逐字一致。
按系统提示词规范的 JSON 格式输出。"""
    }]
    return content


async def compile_h3_image_prompts(
    storyboard_json: dict,
    product_name: str,
) -> dict:
    """阶段 2：把 9 宫格脚本转译为 9:16 竵屏图片提示词。

    Args:
        storyboard_json: 阶段 1 输出的 9 宫格脚本 JSON
        product_name: 产品名

    Returns:
        {
            "image_prompts_json": dict,
            "raw_response": str,
        }
    """
    if not storyboard_json:
        raise ValueError("9 宫格脚本不能为空")
    if not product_name.strip():
        raise ValueError("产品名不能为空")

    system_prompt = _build_image_prompt_system_prompt()
    user_content = _build_image_prompt_user_content(storyboard_json, product_name.strip())
    raw = await _call_gpt(system_prompt, user_content, stream=True)

    import json
    import re
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    try:
        image_prompts_json = json.loads(cleaned)
    except json.JSONDecodeError as e:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            try:
                image_prompts_json = json.loads(match.group(0))
            except json.JSONDecodeError:
                raise RuntimeError(f"LLM 返回非合法 JSON: {e}")
        else:
            raise RuntimeError(f"LLM 返回非合法 JSON: {e}")

    return {
        "image_prompts_json": image_prompts_json,
        "raw_response": raw,
    }


# ── 阶段 3：Ref2VA 6 字段视频提示词 ──

def _build_video_prompt_system_prompt() -> str:
    """构造阶段 3 系统提示词：Ref2VA 6 字段视频提示词。"""
    base_spec = _load_base_spec()
    ref_spec = _load_ref_spec()
    return f"""你是 MiniMax H3 Ref2VA 全参考模式视频提示词编译器。

# 任务
基于 9 宫格分镜脚本 + 9 张关键帧图（已生成）+ 画外音话术，输出 N 段 Ref2VA 6 字段视频提示词 JSON。每段提交给 H3 导演台（r2v 模式，ref2va UNET）独立生成 15 秒视频，段间通过「尾帧=下段首帧」物理共享实现视觉连续。

# H3 规范（必须严格遵守）
{base_spec}

# 全参考模式补充规范（Ref2VA 6 字段）
{ref_spec}

# 跨段 Picture 共享规则
- 段(i+1) 的 <Picture 1> 物理上就是段(i) 的 <Picture 9>（同一张图文件）。
- 在段(i+1) 的 subject_definitions 中显式声明：
  `<Picture 1> is the first frame of [Shot 1] in this segment, physically identical to the last keyframe of the previous segment, ensuring visual continuity across the cut.`
- 段(i+1) 的 detailed_description 开头必须承接段(i) sc9 的动势/站位/光线。

# 9 张 Picture 的映射规则
每段 9 张图全部作为 <Picture 1> ~ <Picture 9> 关键帧锚点，按 timestamp 映射到 15 秒内的对应时刻。
- <Picture 1> 是 [Shot 1] 的首帧（0.00s）
- <Picture 9> 是最后一个 [Shot N] 的末帧（15.00s）
- 中间 <Picture 2-8> 作为各 Shot 的关键帧锚点，在 detailed_description 中自然引用：`the shot's keyframe corresponds to <Picture N>`

# Shot 切分
- 段内 Shot 数由阶段 1 脚本的 shot_structure 决定（1-9 个）。
- [Shot 1] 无时间戳；后续 [Shot N] 用 `At 00:SS.mmm, the camera cuts to...` 标记硬切时间。
- 每个 Shot 的时长严格遵循阶段 1 脚本的 shot_structure.start_sec / end_sec。
- 相邻 Shot 用 `the camera cuts to` 显式硬切，并显式写清换镜头感（景别/机位角度/动作连续性三选二）。

# 画外音话术插入
- 段内 voiceover_script 必须以画外音格式写入 detailed_description：
  `The male/female voiceover with an enthusiastic, confident tone (S1) says in an off-screen voiceover: <d>[Chinese] ...话术原文...</d> while no on-screen character's lips move.`
- 话术时间戳对齐 voiceover_timing_sec。
- 话术原文逐字保留，禁止改写/缩写/意译。

# 运镜
- 每个 Shot 必须包含运镜三维度：motion type（Pan/Tilt/Track/Zoom/Static/Dolly/Arc 等）+ amplitude（small/large）+ speed（slow/fast）。
- 写成自然英文动作句，不堆叠标签。

# 产品锁定
- subject_definitions 中 <Subject 1> 必须定义产品（用户提供的「产品名」即 Subject 名称），引用所有 9 张 <Picture N> 作为外观锚点：
  `<Subject 1> is the {{product_name}} (the product shown in the user-provided product image) shown in <Picture 1> through <Picture 9>, with consistent appearance: colors, brand, model, and material locked from the product image.`
  （product_name 由用户消息提供，你必须在输出时填入实际产品名）
- detailed_description 中产品姿态变化可写，但外观特征（颜色/品牌/型号/材质）禁止改写。

# 输出格式（严格 JSON）
```json
{{
  "segments": [
    {{
      "segment_id": 1,
      "duration_sec": 15,
      "video_prompt": {{
        "subject_definitions": "<Subject 1> is the ... shown in <Picture 1> through <Picture 9> ...\\n<Picture 1> is the first frame of [Shot 1], showing ...\\n<Picture 2> is a keyframe of [Shot 1] at 1.875s, showing ...\\n... <Picture 9> is the last frame of [Shot N] at 15.00s, showing ...",
        "summary": "[reference generation + keyframe completion] The target video shows <Subject 1> ... over 15 seconds with 9 keyframe anchors.",
        "retention_analysis": "<Subject 1> (appears in [Shot 1]...[Shot N]): fully_preserved - product appearance locked.\\n<Picture 1> ([Shot 1] first frame): fully_preserved - ...\\n... <Picture 9> ([Shot N] last frame): fully_preserved - ...",
        "detailed_description": "The target video is in a cinematic, commercial-product style with natural lighting.\\n[Shot 1] ... <Subject 1> ... The male voiceover (S1) says in an off-screen voiceover: <d>[Chinese] 无线强力，一秒清尘。</d> while no on-screen character's lips move.\\n[Shot 2] At 00:05.000, the camera cuts to ...\\n... [Shot N] At 00:XX.mmm, the camera cuts to ... ending on <Picture 9>.",
        "overall_soundscape": "...",
        "non_diegetic_music": "..."
      }}
    }},
    {{
      "segment_id": 2,
      "duration_sec": 15,
      "video_prompt": {{
        "subject_definitions": "<Subject 1> is the ... shown in <Picture 1> through <Picture 9> ...\\n<Picture 1> is the first frame of [Shot 1] in this segment, physically identical to the last keyframe of the previous segment, ensuring visual continuity across the cut. ...",
        "summary": "[reference generation + keyframe completion] ...",
        "retention_analysis": "...",
        "detailed_description": "The target video is in a cinematic, commercial-product style consistent with the previous segment.\\n[Shot 1] ...承接上一段末帧动势... <Subject 1> ... The male voiceover (S1) says in an off-screen voiceover: <d>[Chinese] ...</d> ...\\n...",
        "overall_soundscape": "...",
        "non_diegetic_music": "..."
      }}
    }}
  ]
}}
```

# 严禁
- 禁止输出 JSON 以外文字。
- 禁止改写话术原文（必须逐字）。
- 禁止产品外观与产品图不符。
- 禁止跨段复用 <Picture N> 标签（每段独立编号 1-9）。
- 禁止 negative prompt 字段（H3 无负面提示词字段）。
- 禁止 abstract 抽象情绪词（"震撼"、"高端"、"智能"）。

# 输出
直接输出 JSON 对象本身，无任何包裹。
"""


def _build_video_prompt_user_content(
    storyboard_json: dict,
    image_prompts_json: dict,
    product_name: str,
    product_image_path: str,
) -> list:
    """构造阶段 3 用户消息：产品图 + 脚本 + 图片提示词。"""
    import json as _json
    content: list = []

    # 1. 产品图（让 LLM 锁定产品外观）
    if product_image_path and os.path.exists(product_image_path):
        compressed = _compress_image_b64(product_image_path)
        if compressed:
            mime, img_b64 = compressed
        else:
            with open(product_image_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()
            ext = Path(product_image_path).suffix.lower()
            mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(ext, "image/jpeg")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{img_b64}"},
        })
        content.append({
            "type": "text",
            "text": "（上面是产品图。<Subject 1> 的外观必须与此图一致：颜色、品牌、型号、材质等忠于原图。）"
        })

    # 2. 阶段 1 脚本 + 阶段 2 图片提示词
    content.append({
        "type": "text",
        "text": f"""【9 宫格分镜脚本（阶段 1 输出）】
{_json.dumps(storyboard_json, ensure_ascii=False, indent=2)}

【9:16 竵屏图片提示词（阶段 2 输出，9 张关键帧已生成）】
{_json.dumps(image_prompts_json, ensure_ascii=False, indent=2)}

【产品名】
{product_name}

【任务】
按系统提示词规范的 Ref2VA 6 字段结构，输出 N 段 video_prompt JSON。
- 段(i+1) <Picture 1> 物理上 = 段(i) <Picture 9>，在 subject_definitions 中显式声明。
- 话术以画外音格式写入 detailed_description。
- Shot 切分严格遵循阶段 1 脚本的 shot_structure。
- 每段 9 张 <Picture N> 全部作为关键帧锚点。"""
    })
    return content


async def compile_h3_video_prompts(
    storyboard_json: dict,
    image_prompts_json: dict,
    product_name: str,
    product_image_path: str,
) -> dict:
    """阶段 3：输出 N 段 Ref2VA 6 字段视频提示词 JSON。

    Args:
        storyboard_json: 阶段 1 输出的 9 宫格脚本
        image_prompts_json: 阶段 2 输出的图片提示词
        product_name: 产品名
        product_image_path: 产品图绝对路径

    Returns:
        {
            "video_prompts_json": dict,
            "raw_response": str,
        }
    """
    if not storyboard_json:
        raise ValueError("9 宫格脚本不能为空")
    if not image_prompts_json:
        raise ValueError("图片提示词不能为空")
    if not product_name.strip():
        raise ValueError("产品名不能为空")
    if not product_image_path or not os.path.exists(product_image_path):
        raise ValueError("产品图路径无效")

    system_prompt = _build_video_prompt_system_prompt()
    user_content = _build_video_prompt_user_content(
        storyboard_json, image_prompts_json, product_name.strip(), product_image_path
    )
    raw = await _call_gpt(system_prompt, user_content, stream=True)

    import json
    import re
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    try:
        video_prompts_json = json.loads(cleaned)
    except json.JSONDecodeError as e:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            try:
                video_prompts_json = json.loads(match.group(0))
            except json.JSONDecodeError:
                raise RuntimeError(f"LLM 返回非合法 JSON: {e}\n原始返回前 500 字: {raw[:500]}")
        else:
            raise RuntimeError(f"LLM 返回非合法 JSON: {e}\n原始返回前 500 字: {raw[:500]}")

    return {
        "video_prompts_json": video_prompts_json,
        "raw_response": raw,
    }
