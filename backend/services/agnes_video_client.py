"""Agnes Video 2.5 / 2.5 Flash 客户端。

Flash 不支持参考视频（videos 字段会 400）。
正式 2.5 才支持 mode=reference + videos。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx

from config import settings

LogFn = Optional[Callable[[str], None]]

FLASH_MODEL = "agnes-video-2.5-flash"
FULL_MODEL = "agnes-video-2.5"
FULL_SIZES = ("720P", "1080P", "1K", "2K")
FULL_SECONDS = {str(i) for i in range(4, 13)}


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.AGNES_API_KEY}",
        "Content-Type": "application/json",
    }


def _base() -> str:
    return settings.AGNES_BASE_URL.rstrip("/")


def _clean_urls(items: Optional[List[str]], limit: int) -> List[str]:
    return [u.strip() for u in (items or []) if u and str(u).strip()][:limit]


async def list_models(timeout: float = 8.0) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(f"{_base()}/models", headers=_headers())
        resp.raise_for_status()
        return resp.json()


async def _post_videos(payload: Dict[str, Any], label: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{_base()}/videos", headers=_headers(), json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"{label} 创建失败 HTTP {resp.status_code}: {resp.text[:500]}")
        return resp.json()


async def create_flash_task(
    prompt: str,
    *,
    seconds: str = "5",
    aspect_ratio: str = "9:16",
    images: Optional[List[str]] = None,
    audios: Optional[List[str]] = None,
    seed: Optional[int] = None,
) -> Dict[str, Any]:
    """创建 Flash 任务。不传 videos。"""
    images = _clean_urls(images, 5)
    audios = _clean_urls(audios, 3)
    mode = "reference" if (images or audios) else "text"
    payload: Dict[str, Any] = {
        "model": settings.AGNES_VIDEO_FLASH_MODEL,
        "prompt": prompt,
        "seconds": str(seconds),
        "mode": mode,
        "size": "720P",
        "aspect_ratio": aspect_ratio or "9:16",
        "n": 1,
    }
    if images:
        payload["images"] = images
    if audios:
        payload["audios"] = audios
    if seed is not None:
        payload["seed"] = int(seed)
    return await _post_videos(payload, "Agnes Flash")


async def create_full_task(
    prompt: str,
    *,
    seconds: str = "5",
    aspect_ratio: str = "9:16",
    size: str = "720P",
    images: Optional[List[str]] = None,
    audios: Optional[List[str]] = None,
    videos: Optional[List[Dict[str, Any]]] = None,
    first_frame: Optional[str] = None,
    last_frame: Optional[str] = None,
    seed: Optional[int] = None,
) -> Dict[str, Any]:
    """创建 Agnes Video 2.5 正式版任务。可传 1 条参考视频。"""
    images = _clean_urls(images, 8)
    audios = _clean_urls(audios, 3)
    first_frame = (first_frame or "").strip() or None
    last_frame = (last_frame or "").strip() or None
    video_objs: List[Dict[str, Any]] = []
    for item in videos or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        obj: Dict[str, Any] = {"url": url}
        if item.get("start_seconds") is not None and str(item.get("start_seconds")) != "":
            obj["start_seconds"] = float(item["start_seconds"])
        if item.get("require_audio") is not None:
            obj["require_audio"] = bool(item["require_audio"])
        video_objs.append(obj)
        if len(video_objs) >= 1:
            break

    if first_frame or last_frame:
        if images or audios or video_objs:
            raise RuntimeError("首尾帧模式不能同时传参考图/音频/视频")
        mode = "keyframe"
    elif images or audios or video_objs:
        mode = "reference"
    else:
        mode = "text"

    size = (size or "720P").upper()
    if size not in FULL_SIZES:
        raise RuntimeError(f"size 必须是 {' / '.join(FULL_SIZES)}")
    sec = str(seconds or "5")
    if sec not in FULL_SECONDS:
        raise RuntimeError("seconds 必须是 4–12")

    payload: Dict[str, Any] = {
        "model": settings.AGNES_VIDEO_MODEL,
        "prompt": prompt,
        "seconds": sec,
        "mode": mode,
        "size": size,
        "aspect_ratio": aspect_ratio or "16:9",
        "n": 1,
    }
    if mode == "keyframe":
        if first_frame:
            payload["first_frame"] = first_frame
        if last_frame:
            payload["last_frame"] = last_frame
    if mode == "reference":
        if images:
            payload["images"] = images
        if audios:
            payload["audios"] = audios
        if video_objs:
            payload["videos"] = video_objs
    if seed is not None:
        payload["seed"] = int(seed)
    return await _post_videos(payload, "Agnes 2.5")


async def get_task(video_id: str, model_name: Optional[str] = None) -> Dict[str, Any]:
    # 🔴 查询腿必须跟创建腿同 base、同 key。曾经硬编码成 apihub.agnes-ai.com/agnesapi
    #    ⇒ 提交 200、查询 401 Invalid token（假象像"key 坏了"，其实是打了另一个鉴权域）。
    model_name = model_name or settings.AGNES_VIDEO_FLASH_MODEL
    url = f"{_base()}/videos/{video_id}"
    params = {"model_name": model_name}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(url, headers=_headers(), params=params)
        if resp.status_code >= 400:
            raise RuntimeError(f"Agnes 查询失败 HTTP {resp.status_code}: {resp.text[:400]}")
        return resp.json()


async def wait_task(
    video_id: str,
    *,
    model_name: Optional[str] = None,
    label: str = "Agnes",
    on_log: LogFn = None,
    poll_s: float = 2.0,
    timeout_s: float = 600.0,
) -> Dict[str, Any]:
    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    elapsed = 0.0
    while elapsed <= timeout_s:
        data = await get_task(video_id, model_name=model_name)
        status = str(data.get("status") or "").lower()
        progress = data.get("progress")
        log(f"{label} {status} {progress}%")
        if status == "completed":
            return data
        if status == "failed":
            err = data.get("error") or data
            raise RuntimeError(f"{label} 失败: {err}")
        await asyncio.sleep(poll_s)
        elapsed += poll_s
    raise TimeoutError(f"{label} 超时 {timeout_s:.0f}s video_id={video_id}")


async def wait_flash_task(
    video_id: str,
    *,
    on_log: LogFn = None,
    poll_s: float = 2.0,
    timeout_s: float = 600.0,
) -> Dict[str, Any]:
    return await wait_task(
        video_id,
        model_name=settings.AGNES_VIDEO_FLASH_MODEL,
        label="Agnes Flash",
        on_log=on_log,
        poll_s=poll_s,
        timeout_s=timeout_s,
    )


async def wait_full_task(
    video_id: str,
    *,
    on_log: LogFn = None,
    poll_s: float = 2.0,
    timeout_s: float = 600.0,
) -> Dict[str, Any]:
    return await wait_task(
        video_id,
        model_name=settings.AGNES_VIDEO_MODEL,
        label="Agnes 2.5",
        on_log=on_log,
        poll_s=poll_s,
        timeout_s=timeout_s,
    )


async def download_file(url: str, dest: str) -> str:
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        Path(dest).write_bytes(resp.content)
    return dest
