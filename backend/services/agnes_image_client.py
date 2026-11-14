"""Agnes Image 2.5 Flash 客户端。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from config import settings

IMAGE_SIZES = ("1K", "2K", "3K", "4K")
IMAGE_RATIOS = ("1:1", "3:4", "4:3", "16:9", "9:16", "2:3", "3:2", "21:9")


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.AGNES_API_KEY}",
        "Content-Type": "application/json",
    }


def _base() -> str:
    return settings.AGNES_BASE_URL.rstrip("/")


async def create_image(
    prompt: str,
    *,
    size: str = "1K",
    ratio: str = "1:1",
    images: Optional[List[str]] = None,
) -> Dict[str, Any]:
    size = (size or "1K").upper()
    if size not in IMAGE_SIZES:
        raise RuntimeError(f"size 必须是 {' / '.join(IMAGE_SIZES)}")
    ratio = ratio or "1:1"
    if ratio not in IMAGE_RATIOS:
        raise RuntimeError(f"ratio 必须是 {' / '.join(IMAGE_RATIOS)}")
    refs = [u.strip() for u in (images or []) if u and str(u).strip()]
    payload: Dict[str, Any] = {
        "model": settings.AGNES_IMAGE_MODEL,
        "prompt": prompt,
        "size": size,
        "ratio": ratio,
        "extra_body": {"response_format": "url"},
    }
    if refs:
        payload["image"] = refs
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            f"{_base()}/images/generations",
            headers=_headers(),
            json=payload,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Agnes Image 创建失败 HTTP {resp.status_code}: {resp.text[:500]}")
        return resp.json()


async def download_file(url: str, dest: str) -> str:
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        Path(dest).write_bytes(resp.content)
    return dest
