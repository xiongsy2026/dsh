"""
小红书视频下载器
支持：
- 分享短链解析（xhslink.cn / xhslink.com → www.xiaohongshu.com/notes/xxx）
- 笔记页直链（www.xiaohongshu.com/explore/xxx、/discovery/item/xxx、/notes/xxx）
- 无水印视频下载（yt-dlp 优先 + httpx web API 兜底）

接口签名与 douyin_downloader.download_douyin_video 一致，便于在批量流水线中替换。
"""
import re
import asyncio
import subprocess
from pathlib import Path
from typing import Optional, Callable, Dict, Any
from urllib.parse import urlparse

import httpx

from config import settings


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.xiaohongshu.com/",
    "Origin": "https://www.xiaohongshu.com",
}


# ─────────────────────────────────────────────────────────
# 链接识别
# ─────────────────────────────────────────────────────────

_XHS_HOSTS = (
    "xhslink.cn",
    "xhslink.com",
    "www.xiaohongshu.com",
    "xiaohongshu.com",
)


def is_xhs_link(text: str) -> bool:
    """判断文本中是否包含小红书链接"""
    if not text:
        return False
    low = text.lower()
    return any(host in low for host in _XHS_HOSTS)


# ─────────────────────────────────────────────────────────
# 解析短链 → note_id + 完整 URL
# ─────────────────────────────────────────────────────────


async def resolve_xhs_url(share_text: str) -> Dict[str, str]:
    """
    解析小红书分享文本/链接，返回笔记信息：
    {
        "note_id": str,        # 笔记 ID（explore 后那串）
        "note_url": str,       # 完整笔记页 URL
        "title": str,          # 笔记标题（可能拿不到，兜底用 note_id）
        "author": str,         # 作者昵称
        "desc": str,           # 描述文案
    }
    """
    share_text = (share_text or "").strip()
    if not share_text:
        raise ValueError("小红书链接为空")

    # 清理零宽字符、全角空格等不可见字符
    cleaned = re.sub(r'[\u200b\u200c\u200d\ufeff\u3000]', '', share_text)

    # 从分享文案中提取 URL
    # 只允许 ASCII URL 字符，避免把后面跟随的中文当作 URL 一部分
    url_match = re.search(
        r'https?://[A-Za-z0-9\-._~:/?#\[\]@!$&\'()*+,;=%]+',
        cleaned,
    )
    if not url_match:
        raise ValueError(
            f"未在输入中找到小红书链接。输入前60字符：{share_text[:60]!r}"
        )
    raw_url = url_match.group(0).rstrip('/`\'"<>')
    print(f"  [xhs] 提取到 URL: {raw_url}", flush=True)

    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=HEADERS,
        timeout=15.0,
    ) as client:
        # 直接从 URL 提取 note_id（用户粘贴完整链接 /notes/xxx 或 /explore/xxx）
        direct = (
            re.search(r'/notes/([a-zA-Z0-9]{24})', raw_url)
            or re.search(r'/explore/([a-zA-Z0-9]{24})', raw_url)
            or re.search(r'/discovery/item/([a-zA-Z0-9]{24})', raw_url)
        )

        note_id = direct.group(1) if direct else None
        final_url = raw_url

        # 短链跳转解析（xhslink.cn / xhslink.com）
        if not note_id:
            try:
                resp = await client.get(raw_url)
                final_url = str(resp.url)
                # 跳转后的 URL 中找 note_id
                m = (
                    re.search(r'/notes/([a-zA-Z0-9]{24})', final_url)
                    or re.search(r'/explore/([a-zA-Z0-9]{24})', final_url)
                    or re.search(r'/discovery/item/([a-zA-Z0-9]{24})', final_url)
                )
                if m:
                    note_id = m.group(1)
                # 回退：从 HTML 正则提取 note_id（变体: "noteId":"xxx" 或 "id":"xxx"）
                if not note_id:
                    html_match = re.search(r'"noteId"\s*:\s*["\']([a-zA-Z0-9]{24})["\']', resp.text)
                    if html_match:
                        note_id = html_match.group(1)
            except Exception:
                pass

        if not note_id:
            raise RuntimeError(
                "无法解析小红书链接：可能是无效链接、笔记已删除，或需 cookie 访问。"
                f" 原始URL: {raw_url}"
            )

        note_url = f"https://www.xiaohongshu.com/explore/{note_id}"

        # 尝试从 HTML 提取标题/作者（无 cookie 时也能拿到一部分）
        title = ""
        author = ""
        desc = ""
        try:
            page_resp = await client.get(note_url)
            html = page_resp.text
            t_match = re.search(r'<title[^>]*>([^<]+)</title>', html)
            if t_match:
                title = t_match.group(1).strip()
            # 描述：meta[name=description]
            d_match = re.search(
                r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']+)["\']',
                html,
            )
            if d_match:
                desc = d_match.group(1).strip()
            # 作者：尝试从 JSON-LD / window.__INITIAL_STATE__ 提取
            a_match = re.search(r'"nickname"\s*:\s*["\']([^"\']+)["\']', html)
            if a_match:
                author = a_match.group(1).strip()
        except Exception:
            pass

        if not title:
            title = f"小红书笔记_{note_id}"

        return {
            "note_id": note_id,
            "note_url": note_url,
            "raw_url": raw_url,  # 提取后的纯 URL（短链或长链），喂给 yt-dlp 用
            "title": title,
            "author": author,
            "desc": desc,
        }


# ─────────────────────────────────────────────────────────
# 方案0：yt-dlp（社区维护，自动跟随反爬升级）
# ─────────────────────────────────────────────────────────


async def _download_with_ytdlp(
    share_url: str,
    note_id: str,
    output_path: Path,
    status_callback: Optional[Callable[[str], Any]] = None,
) -> bool:
    """
    用 yt-dlp 下载小红书视频（社区维护的 xiaohongshu extractor）。
    yt-dlp 是同步库，放到线程池执行。
    """
    try:
        import yt_dlp  # noqa
    except ImportError:
        if status_callback:
            await _notify(status_callback, "yt-dlp 未安装，跳过 yt-dlp 方案")
        return False

    if status_callback:
        await _notify(status_callback, "使用 yt-dlp 下载小红书视频...")

    # 调用方传入的 share_url 已经是纯 URL（resolve_xhs_url 解析后的 note_url）
    url_to_use = share_url

    ydl_opts = {
        "outtmpl": str(output_path.with_suffix(".%(ext)s")),
        "format": "best",
        "quiet": False,
        "no_warnings": False,
        "noplaylist": True,
        "http_headers": {
            "User-Agent": HEADERS["User-Agent"],
            "Referer": "https://www.xiaohongshu.com/",
        },
    }

    # 捕获 yt-dlp 的 stderr 输出，便于诊断失败原因
    import io
    import contextlib
    stderr_buf = io.StringIO()

    def _run_sync():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # 重定向 yt-dlp 的日志到 buf
            with contextlib.redirect_stderr(stderr_buf), contextlib.redirect_stdout(stderr_buf):
                ydl.download([url_to_use])

    try:
        await asyncio.to_thread(_run_sync)
    except Exception as e:
        stderr_text = stderr_buf.getvalue()[-800:] if stderr_buf else ""
        if status_callback:
            await _notify(
                status_callback,
                f"yt-dlp 下载失败: {type(e).__name__}: {e} | stderr: {stderr_text}",
            )
        print(f"  [xhs] yt-dlp 失败：{e}\n  stderr: {stderr_text}", flush=True)
        return False

    # yt-dlp 输出扩展名可能不是 .mp4，找出来重命名
    parent = output_path.parent
    stem = output_path.stem
    candidates = list(parent.glob(f"{stem}.*"))
    if not candidates:
        # 兜底：找最近写入的视频文件
        candidates = sorted(
            parent.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True
        )
    if candidates:
        real = candidates[0]
        if real.suffix.lower() != ".mp4":
            target = real.with_suffix(".mp4")
            real.rename(target)
            real = target
        if real != output_path:
            real.rename(output_path)
        if output_path.exists() and output_path.stat().st_size > 100 * 1024:
            if status_callback:
                await _notify(
                    status_callback,
                    f"yt-dlp 下载完成（{output_path.stat().st_size // 1024}KB）",
                )
            return True

    if status_callback:
        await _notify(status_callback, "yt-dlp 下载结果不达标")
    return False


async def _notify(callback, msg):
    """支持 sync 和 async 回调"""
    if callback is None:
        return
    ret = callback(msg)
    if asyncio.iscoroutine(ret):
        await ret


# ─────────────────────────────────────────────────────────
# 方案1：httpx web API（无 cookie 兜底，成功率较低）
# ─────────────────────────────────────────────────────────


async def _download_via_web_api(
    note_id: str,
    output_path: Path,
    status_callback: Optional[Callable[[str], Any]] = None,
) -> bool:
    """通过 web API 尝试拿 video_url 直接下载（无 cookie 多数会 460/461）"""
    if status_callback:
        await _notify(status_callback, "尝试 httpx web API 方案...")

    api_url = "https://www.xiaohongshu.com/api/sns/web/v1/feed"
    payload = {
        "source_note_id": note_id,
        "image_formats": ["jpg", "webp", "png"],
        "extra": {"need_body_topic": 1},
    }
    headers = {**HEADERS, "Content-Type": "application/json;charset=UTF-8"}

    try:
        async with httpx.AsyncClient(
            follow_redirects=True, headers=headers, timeout=15.0
        ) as client:
            resp = await client.post(api_url, json=payload)
            if resp.status_code != 200:
                if status_callback:
                    await _notify(
                        status_callback,
                        f"web API HTTP {resp.status_code}，跳过该方案",
                    )
                return False
            data = resp.json()
            items = data.get("data", {}).get("items", [])
            if not items:
                return False
            note = items[0].get("note", {})
            video = note.get("video", {})
            play_url = ""
            if video.get("media", {}).get("stream", {}):
                streams = video["media"]["stream"]
                for s in streams.get("h264", [{}]):
                    if s.get("master_url"):
                        play_url = s["master_url"]
                        break
                    if s.get("backup_urls"):
                        play_url = s["backup_urls"][0]
                        break
            if not play_url and video.get("consumer", {}).get("origin_video_key"):
                play_url = (
                    "https://sns-video-bd.xhscdn.com/"
                    + video["consumer"]["origin_video_key"]
                )
            if not play_url:
                return False

            dl_resp = await client.get(
                play_url, headers={**HEADERS, "Referer": "https://www.xiaohongshu.com/"}
            )
            dl_resp.raise_for_status()
            output_path.write_bytes(dl_resp.content)
            if output_path.stat().st_size > 100 * 1024:
                if status_callback:
                    await _notify(
                        status_callback,
                        f"web API 下载完成（{output_path.stat().st_size // 1024}KB）",
                    )
                return True
    except Exception as e:
        if status_callback:
            await _notify(status_callback, f"web API 异常: {e}")
    return False


# ─────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────


async def download_xhs_video(
    share_url: str,
    output_dir: str,
    status_callback: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    """
    完整流程：解析 → 下载视频

    Args:
        share_url: 小红书分享链接（短链 xhslink.cn 或完整链接）
        output_dir: 输出目录
        status_callback: 状态回调（同步或异步均可）

    Returns:
        {
            "video_path": str,    # 下载后的本地视频绝对路径
            "title": str,         # 笔记标题
            "author": str,        # 作者昵称
            "note_id": str,       # 笔记 ID
            "note_url": str,      # 完整笔记页 URL
        }
    """
    if status_callback:
        await _notify(status_callback, "解析小红书链接...")

    info = await resolve_xhs_url(share_url)
    note_id = info["note_id"]

    if status_callback:
        await _notify(status_callback, f"已解析：{info['title'][:30]}... 下载视频...")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_title = re.sub(r'[\\/:*?"<>|\n\r\t]', '_', info["title"])[:40]
    filename = f"xhs_{note_id}_{safe_title}.mp4"
    output_path = output_dir / filename

    # 方案0：yt-dlp（首选）
    # 用 resolve_xhs_url 提取后的纯 URL（短链或长链），避免中文分享文案污染
    if status_callback:
        await _notify(status_callback, "尝试方案0：yt-dlp...")
    ok = await _download_with_ytdlp(info["raw_url"], note_id, output_path, status_callback)
    if ok:
        return {
            "video_path": str(output_path),
            "title": info["title"],
            "author": info["author"],
            "note_id": note_id,
            "note_url": info["note_url"],
        }

    # 方案1：httpx web API 兜底
    ok = await _download_via_web_api(note_id, output_path, status_callback)
    if ok:
        return {
            "video_path": str(output_path),
            "title": info["title"],
            "author": info["author"],
            "note_id": note_id,
            "note_url": info["note_url"],
        }

    raise RuntimeError(
        "小红书视频下载失败：yt-dlp 与 httpx web API 两种方案均失败。"
        " 可能原因：笔记非视频类型、已删除、需要登录态 cookie，或反爬策略升级。"
        " 建议：① 确认链接为视频笔记；② 手动下载后通过视频上传接口走反推流程。"
    )


if __name__ == "__main__":
    import sys

    test_url = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "https://xhslink.cn/o/531YDRqRzDh"
    )
    result = asyncio.run(
        download_xhs_video(test_url, "outputs/xhs_test")
    )
    print(result)
