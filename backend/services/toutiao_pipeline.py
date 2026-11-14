"""
今日头条二创 Pipeline
流程:
1. 解析抖音链接 → 下载无水印视频
2. ASR 提取视频文案
3. LLM 改写成头条故事体文章（标题+正文+总结）
4. 配图（原视频抽帧 + AI 生图混合）
5. 浏览器自动化发布（图文文章 + 微头条）

入口: run_toutiao_recreate(...)
"""
import os
import json
import asyncio
import logging
from pathlib import Path
from typing import Optional, Callable, Awaitable

from config import settings

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════
# 状态更新工具
# ═══════════════════════════════════════

async def _noop(msg: str): pass


# ═══════════════════════════════════════
# Step 1: 抖音下载
# ═══════════════════════════════════════

async def step1_download_douyin(
    share_url: str,
    output_dir: str,
    status_callback: Callable[[str], Awaitable[None]] = _noop,
) -> dict:
    """下载抖音视频 —— 走下载这条腿的唯一入口 `link_fetch`（两次重试 + 断头片重下都在里面）。

    返回形状保持这一步原来的契约（`video_path` / `title` / `author`），下游 Step2/3/4 一个字不用改。
    🔴 `analyze=True` 要显式给：漏斗默认 False（批量存片只要文件），这一步沿用的是下载器原来
       的默认 True（moviepy 量时长、短于 5 秒就降级重下）。
    """
    from backend.services.link_fetch import link_fetch_local

    await status_callback("【1/5】解析抖音链接并下载视频...")
    got = await link_fetch_local(share_url, cb=status_callback, out_dir=output_dir, analyze=True)
    await status_callback(f"视频下载完成：{got['title']}")
    return {"video_path": got["file_path"], "title": got["title"], "author": got.get("author") or "",
            "duration": got["duration"], "method": got.get("method") or "",
            "quality": got.get("quality") or ""}


# ═══════════════════════════════════════
# Step 2: ASR 提取文案
# ═══════════════════════════════════════

async def step2_extract_transcript(
    video_path: str,
    status_callback: Callable[[str], Awaitable[None]] = _noop,
) -> str:
    """ASR 提取视频文案"""
    from backend.services.video_translate_pipeline import run_asr

    await status_callback("【2/5】ASR 提取视频文案...")
    asr_result = await run_asr(video_path, source_lang="zh", engine="whisper")
    segments = asr_result["segments"]
    transcript = " ".join(s["text"].strip() for s in segments if s.get("text"))
    await status_callback(f"文案提取完成，长度 {len(transcript)} 字")
    return transcript


# ═══════════════════════════════════════
# Step 3: LLM 改写成头条故事体
# ═══════════════════════════════════════

STORY_REWRITE_SYSTEM = """你是一位资深的今日头条内容创作者，擅长把短视频文案改写成吸引人的头条故事体文章。

要求：
1. 标题：15-30字，吸引眼球但不标题党，可适当使用数字、疑问句
2. 正文：800-1500字，分3-5个小节，每节有小标题
3. 风格：故事化叙事，有画面感，加入个人观点和思考，结尾有总结或反思
4. 配图关键词：提取3-5个适合配图的关键词或短语（用于AI生图）
5. 微头条：50-150字的精简版本，引发讨论

输出 JSON 格式：
{
  "title": "文章标题",
  "content_html": "<p>段落1</p><p>段落2</p>...",
  "image_keywords": ["关键词1", "关键词2", "关键词3"],
  "micro_content": "微头条文案"
}
"""


async def step3_rewrite_to_story(
    transcript: str,
    video_title: str = "",
    author: str = "",
    status_callback: Callable[[str], Awaitable[None]] = _noop,
) -> dict:
    """LLM 改写为头条故事"""
    from backend.services.gpt_client import gpt55_client

    await status_callback("【3/5】LLM 改写成头条故事...")

    user_msg = f"""请把以下抖音视频文案改写成今日头条故事体文章。

视频标题：{video_title}
视频作者：{author}

原始文案：
{transcript}

请按系统提示的要求输出 JSON。"""

    messages = [
        {"role": "system", "content": STORY_REWRITE_SYSTEM},
        {"role": "user", "content": user_msg},
    ]

    resp = await gpt55_client.chat_completion(
        messages=messages,
        model=settings.OPENAI_MODEL,
        temperature=0.7,
        max_tokens=4000,
    )

    content = resp["choices"][0]["message"]["content"]
    # 解析 JSON（兼容 markdown 代码块包裹）
    import re
    json_match = re.search(r'```json\s*(.+?)\s*```', content, re.DOTALL)
    if json_match:
        content = json_match.group(1)
    else:
        # 尝试直接找 { ... }
        m = re.search(r'\{[\s\S]+\}', content)
        if m:
            content = m.group(0)

    try:
        story = json.loads(content)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"LLM 输出 JSON 解析失败: {e}\n原始内容: {content[:500]}")

    await status_callback(f"改写完成：标题《{story.get('title', '')[:20]}...》")
    return story


# ═══════════════════════════════════════
# Step 4: 配图（混合方案）
# ═══════════════════════════════════════

async def step4_prepare_images(
    video_path: str,
    image_keywords: list[str],
    output_dir: str,
    target_count: int = 5,
    status_callback: Callable[[str], Awaitable[None]] = _noop,
) -> list[str]:
    """混合配图：抽帧 + AI生图"""
    from backend.services.image_search import select_images_for_article

    await status_callback("【4/5】准备配图（抽帧 + AI生图混合）...")
    paths = await select_images_for_article(
        video_path=video_path,
        story_keywords=image_keywords,
        output_dir=output_dir,
        target_count=target_count,
        status_callback=status_callback,
    )
    await status_callback(f"配图准备完成：{len(paths)} 张")
    return paths


# ═══════════════════════════════════════
# Step 5: 自动发布
# ═══════════════════════════════════════

async def step5_publish(
    story: dict,
    image_paths: list[str],
    publish_types: list[str],
    status_callback: Callable[[str], Awaitable[None]] = _noop,
) -> dict:
    """发布到今日头条"""
    from backend.services.toutiao_publisher import publish_article, publish_micro, ensure_login

    result = {"article_url": None, "micro_url": None}

    await status_callback("【5/5】发布到今日头条...")

    # 先确保登录
    await ensure_login(status_callback=status_callback)

    if "article" in publish_types:
        await status_callback("发布图文文章...")
        article_url = await publish_article(
            title=story["title"],
            content_html=story["content_html"],
            image_paths=image_paths,
            status_callback=status_callback,
        )
        result["article_url"] = article_url

    if "micro" in publish_types:
        await status_callback("发布微头条...")
        # 微头条用第一张图
        micro_images = image_paths[:1] if image_paths else []
        micro_url = await publish_micro(
            text=story.get("micro_content", story["title"]),
            image_paths=micro_images,
            status_callback=status_callback,
        )
        result["micro_url"] = micro_url

    await status_callback("发布完成！")
    return result


# ═══════════════════════════════════════
# 主流程
# ═══════════════════════════════════════

async def run_toutiao_recreate(
    share_url: str,
    output_dir: str,
    publish_types: Optional[list[str]] = None,
    target_image_count: int = 5,
    status_callback: Optional[Callable[[str], Awaitable[None]]] = None,
) -> dict:
    """
    完整流程：抖音 → 头条二创发布

    参数:
        share_url: 抖音分享链接（短链或完整URL）
        output_dir: 中间产物目录
        publish_types: 发布类型列表 ["article", "micro"]
        target_image_count: 目标配图数量
        status_callback: 异步状态回调

    返回:
        {
            "video_path": str,
            "transcript": str,
            "story": {...},
            "image_paths": [...],
            "publish_result": {"article_url": ..., "micro_url": ...},
        }
    """
    if publish_types is None:
        publish_types = ["article", "micro"]
    if status_callback is None:
        status_callback = _noop

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: 下载
    dl_info = await step1_download_douyin(share_url, str(output_dir), status_callback)

    # Step 2: ASR
    transcript = await step2_extract_transcript(dl_info["video_path"], status_callback)

    # Step 3: LLM 改写
    story = await step3_rewrite_to_story(
        transcript=transcript,
        video_title=dl_info["title"],
        author=dl_info["author"],
        status_callback=status_callback,
    )

    # Step 4: 配图
    image_paths = await step4_prepare_images(
        video_path=dl_info["video_path"],
        image_keywords=story.get("image_keywords", []),
        output_dir=str(output_dir / "images"),
        target_count=target_image_count,
        status_callback=status_callback,
    )

    # Step 5: 发布
    publish_result = await step5_publish(
        story=story,
        image_paths=image_paths,
        publish_types=publish_types,
        status_callback=status_callback,
    )

    return {
        "video_path": dl_info["video_path"],
        "transcript": transcript,
        "story": story,
        "image_paths": image_paths,
        "publish_result": publish_result,
    }
