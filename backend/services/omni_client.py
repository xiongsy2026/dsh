"""zexapi Omni 视频生成 API 客户端

封装 zexapi omni_flash-10s 模型的视频生成接口。
用于「钩子延展」场景：钩子段 video_url + 延展 prompt → 10 秒延展视频。

API 规范（基于实测验证）：
- Endpoint: POST https://zexapi.com/v1/videos
- 查询: GET https://zexapi.com/v1/videos/{task_id}
- 模型: omni_flash-10s（10 秒输出，固定时长）
- 输入字段:
    - model: 模型名
    - prompt: 文本提示词
    - video_url / videoUrls: 参考视频 URL（用于视频续接/修改）
    - image_url / imageUrls: 参考图片 URL
- 输出字段:
    - id / task_id
    - status: queued / in_progress / completed / failed
    - progress: 0-100
    - video_url / url / results[0].url
    - error / failed_reason
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

# ── API 端点 ──
BASE_URL = settings.OMNI_BASE_URL.rstrip("/")
VIDEOS_ENDPOINT = f"{BASE_URL}/v1/videos"


def _headers(api_key: Optional[str] = None) -> Dict[str, str]:
    key = (api_key or settings.OMNI_API_KEY).strip()
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def submit_omni_task(
    prompt: str,
    video_url: Optional[str] = None,
    image_urls: Optional[List[str]] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """提交 Omni 视频生成任务（同步，返回 task_id 和原始响应）。

    参数：
        prompt: 文本提示词
        video_url: 参考视频 URL（可选，用于视频续接/修改）
        image_urls: 参考图片 URL 列表（可选）
        model: 模型名，默认 omni_flash-10s
        api_key: 可选 API key，默认用 settings.OMNI_API_KEY

    返回：
        {"task_id": str, "raw": <完整响应>}
    异常：
        OmniError: 提交失败（HTTP 非 200 或缺 task_id）
    """
    model_name = (model or settings.OMNI_MODEL_T2V or "omni_flash-10s").strip()
    payload: Dict[str, Any] = {
        "model": model_name,
        "prompt": prompt,
    }
    if video_url:
        # 同时传 snake 和 camel 两种字段，兼容 API 不同版本
        payload["video_url"] = video_url
        payload["videoUrls"] = [video_url]
    if image_urls:
        payload["image_url"] = image_urls[0] if len(image_urls) == 1 else None
        payload["imageUrls"] = image_urls
        # 清理 None
        payload = {k: v for k, v in payload.items() if v is not None}

    headers = _headers(api_key)
    logger.info("[Omni] submit payload: model=%s, video_url=%s, image_urls=%d",
                model_name, bool(video_url), len(image_urls or []))

    try:
        with httpx.Client(timeout=30) as client:
            r = client.post(VIDEOS_ENDPOINT, headers=headers, json=payload)
        if r.status_code != 200:
            raise OmniError(
                f"提交失败 HTTP {r.status_code}: {r.text[:300]}",
                status_code=r.status_code,
            )
        d = r.json()
        task_id = d.get("id") or d.get("task_id")
        if not task_id:
            raise OmniError(f"响应缺 task_id: {d}")
        return {"task_id": task_id, "raw": d}
    except httpx.RequestError as e:
        raise OmniError(f"网络错误: {type(e).__name__}: {e}") from e


def fetch_omni_task(
    task_id: str,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """查询 Omni 任务状态。

    返回标准化字段：
        {
            "task_id": str,
            "status": "queued" / "in_progress" / "completed" / "failed",
            "progress": int (0-100),
            "video_url": str | None,
            "error": str | None,
            "raw": <原始响应>,
        }
    """
    headers = _headers(api_key)
    try:
        with httpx.Client(timeout=30) as client:
            r = client.get(f"{VIDEOS_ENDPOINT}/{task_id}", headers=headers)
        if r.status_code != 200:
            return {
                "task_id": task_id,
                "status": "unknown",
                "progress": 0,
                "video_url": None,
                "error": f"HTTP {r.status_code}: {r.text[:200]}",
                "raw": None,
            }
        d = r.json()
    except httpx.RequestError as e:
        return {
            "task_id": task_id,
            "status": "unknown",
            "progress": 0,
            "video_url": None,
            "error": f"网络错误: {type(e).__name__}: {e}",
            "raw": None,
        }

    # 标准化 status
    raw_status = (d.get("status") or "unknown").lower()
    if raw_status in ("queued", "pending"):
        status = "queued"
    elif raw_status in ("in_progress", "processing", "running"):
        status = "in_progress"
    elif raw_status in ("completed", "success", "succeeded"):
        status = "completed"
    elif raw_status in ("failed", "error"):
        status = "failed"
    else:
        status = raw_status

    # 提取 video_url（多字段兼容）
    video_url = (
        d.get("video_url")
        or d.get("url")
        or (d.get("results") or [{}])[0].get("url")
    )

    return {
        "task_id": task_id,
        "status": status,
        "progress": int(d.get("progress") or 0),
        "video_url": video_url,
        "error": d.get("error") or d.get("failed_reason") or "",
        "raw": d,
    }


def wait_for_completion(
    task_id: str,
    timeout: int = 600,
    poll_interval: int = 8,
    on_progress: Optional[callable] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """轮询任务直到完成或超时。

    返回最终 fetch_omni_task 结果（含 video_url）。
    超时则返回最后一次查询结果（status="unknown"）。
    """
    start = time.time()
    last_status = None
    while time.time() - start < timeout:
        info = fetch_omni_task(task_id, api_key=api_key)
        if info["status"] != last_status:
            if on_progress:
                on_progress(info["status"], info["progress"],
                             f"{info['status']} {info['progress']}% ({int(time.time()-start)}s)")
            last_status = info["status"]

        if info["status"] == "completed":
            return info
        if info["status"] == "failed":
            return info

        time.sleep(poll_interval)

    if on_progress:
        on_progress("timeout", 0, f"轮询超时 {timeout}s")
    return {
        "task_id": task_id,
        "status": "timeout",
        "progress": 0,
        "video_url": None,
        "error": f"轮询超时 {timeout}s",
        "raw": None,
    }


def download_video(
    video_url: str,
    save_path: str,
    api_key: Optional[str] = None,
) -> str:
    """下载视频到本地。返回 save_path。"""
    try:
        with httpx.Client(timeout=180, follow_redirects=True) as client:
            r = client.get(video_url)
        if r.status_code != 200:
            raise OmniError(f"下载失败 HTTP {r.status_code}")
        with open(save_path, "wb") as f:
            f.write(r.content)
        return save_path
    except httpx.RequestError as e:
        raise OmniError(f"下载网络错误: {e}") from e


class OmniError(Exception):
    """Omni API 错误"""
    def __init__(self, msg: str, status_code: Optional[int] = None):
        super().__init__(msg)
        self.status_code = status_code
