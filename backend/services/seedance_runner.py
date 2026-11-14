"""
Seedance Runner：调用 RH sparkvideo 端点生成视频

支持版本：2.0 / 2.0-fast / 2.0-mini / 2.5（2.5 预留，待 RH 上线）

调用流程：
  1. resolve_seedance_endpoint(version, has_image) → 完整端点名
  2. find_endpoint() → 端点定义（params 等）
  3. 构造 payload（prompt + 可选 image + aspectRatio/duration/resolution）
  4. api_post() 提交任务
  5. poll_task() 轮询完成
  6. download_file() 下载到本地

复用 skills/runninghub/scripts/runninghub.py 的所有函数，不重写。
"""
from __future__ import annotations

import importlib.util
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from config import settings

# ── 加载 RH 脚本模块 ──
_RH_SCRIPT = Path(__file__).parent.parent.parent / "skills" / "runninghub" / "scripts" / "runninghub.py"
_rh_module = None


def _get_rh():
    """懒加载 runninghub.py 模块。"""
    global _rh_module
    if _rh_module is None:
        spec = importlib.util.spec_from_file_location("rh_runner", str(_RH_SCRIPT))
        _rh_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_rh_module)
    return _rh_module


def _resolve_api_key() -> str:
    """解析 RunningHub API Key（参考 app.py._rh_resolve_api_key）。"""
    key = os.environ.get("RUNNINGHUB_API_KEY", "").strip()
    if key:
        return key
    # 默认用 enterprise key
    ent = getattr(settings, "RUNNINGHUB_ENTERPRISE_API_KEY", "").strip()
    if ent:
        return ent
    # 兜底用默认
    return getattr(settings, "RUNNINGHUB_API_KEY", "").strip()


def submit_seedance_task(
    compiled_prompt: str,
    version: str = "2.0",
    aspect_ratio: str = "9:16",
    duration: str = "5",
    resolution: str = "720p",
    image_path: Optional[str] = None,
) -> Dict[str, Any]:
    """提交 Seedance 视频生成任务到 RH。

    Args:
        compiled_prompt: 反推后的 Seedance prompt 文本
        version: Seedance 版本（2.0/2.0-fast/2.0-mini/2.5）
        aspect_ratio: 9:16 / 16:9 / 1:1
        duration: 时长秒数字符串
        resolution: 720p/1080p/4k
        image_path: 产品图本地路径（可选，传入则走 i2v）

    Returns:
        {
            "rh_task_id": str,
            "endpoint": str,
            "is_i2v": bool,
            "version": str,
            "payload": dict,
        }
    """
    from backend.services.oneshot_replica import resolve_seedance_endpoint

    rh = _get_rh()
    api_key = _resolve_api_key()
    has_image = bool(image_path)
    endpoint_name = resolve_seedance_endpoint(version, has_image=has_image)
    endpoint_def = rh.find_endpoint(endpoint_name)
    if not endpoint_def:
        raise RuntimeError(f"Seedance 端点不存在: {endpoint_name}（version={version}）")

    # 构造 payload
    payload: Dict[str, Any] = {}
    prompt_key = "prompt"
    for p in endpoint_def.get("params", []):
        if p.get("key") in ("prompt", "text"):
            prompt_key = p["key"]
            break
    payload[prompt_key] = compiled_prompt

    # 媒体输入（i2v 模式）
    if has_image:
        image_params = [p for p in endpoint_def.get("params", []) if p.get("type") == "IMAGE"]
        if not image_params:
            raise RuntimeError(f"该 Seedance 端点不支持图生视频: {endpoint_name}")
        resolved = rh.resolve_media(api_key, image_path, force_upload=True)
        pk = image_params[0]["key"]
        if image_params[0].get("multiple"):
            payload[pk] = [resolved]
        else:
            payload[pk] = resolved

    # 可选参数映射
    user_values = {
        "aspectRatio": aspect_ratio,
        "duration": duration,
        "resolution": resolution,
    }
    for p in endpoint_def.get("params", []):
        k = p.get("key")
        if k in (prompt_key,) or p.get("type") in ("IMAGE", "VIDEO", "AUDIO"):
            continue
        if k not in user_values:
            continue
        v = user_values[k]
        if not v:
            v = p.get("default")
        if v:
            payload[k] = str(v)

    # 提交
    submit_url = f"{rh.BASE_URL}/{endpoint_def['endpoint']}"
    resp = rh.api_post(api_key, submit_url, payload)
    rh_task_id = resp.get("taskId")
    if not rh_task_id:
        raise RuntimeError(f"未返回 taskId: {json.dumps(resp, ensure_ascii=False)}")

    return {
        "rh_task_id": rh_task_id,
        "endpoint": endpoint_name,
        "is_i2v": has_image,
        "version": version,
        "payload": payload,
        "api_key": api_key,
    }


def poll_seedance_and_download(
    rh_task_id: str,
    api_key: str,
    out_dir: str,
) -> Dict[str, Any]:
    """阻塞轮询 RH 任务直到完成，下载结果视频到本地。

    Args:
        rh_task_id: RH 任务 ID
        api_key: RH API Key
        out_dir: 输出目录（绝对路径）

    Returns:
        {
            "status": "completed"/"failed",
            "video_path": str, "video_url": str,
            "cost": float, "duration_actual": float,
            "error": str,
        }
    """
    rh = _get_rh()
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    url = f"{rh.BASE_URL}{rh.POLL_ENDPOINT}"
    elapsed = 0
    while elapsed < rh.MAX_POLL_SECONDS:
        time.sleep(rh.POLL_INTERVAL)
        elapsed += rh.POLL_INTERVAL
        r = rh.poll_once(api_key, url, rh_task_id)
        if r is None:
            continue
        status = r.get("status", "UNKNOWN")
        if status == "SUCCESS":
            results = r.get("results") or []
            if not results:
                return {"status": "failed", "error": "SUCCESS 但无 results"}
            item = results[0]
            result_url = item.get("url") or item.get("outputUrl")
            if not result_url:
                return {"status": "failed", "error": "results[0] 无 url"}
            ext = item.get("outputType") or "mp4"
            local_path = out_path / f"result.{ext}"
            try:
                full = rh.download_file(result_url, str(local_path))
                rh.fix_mov_to_mp4(full)
                full_path = Path(full)
                # 计算 /outputs/... 形式的 URL
                outputs_dir = Path(settings.OUTPUT_DIR)
                try:
                    rel = full_path.relative_to(outputs_dir)
                    video_url = f"/outputs/{rel.as_posix()}"
                except ValueError:
                    video_url = None
                usage = r.get("usage") or {}
                return {
                    "status": "completed",
                    "video_path": str(full_path),
                    "video_url": video_url,
                    "cost": usage.get("consumeMoney") or usage.get("thirdPartyConsumeMoney"),
                    "duration_actual": usage.get("taskCostTime"),
                }
            except Exception as e:
                return {"status": "failed", "error": f"下载失败: {e}"}
        if status == "FAILED":
            return {"status": "failed", "error": r.get("errorMessage", "Unknown error")}
    return {"status": "failed", "error": f"轮询超时（{rh.MAX_POLL_SECONDS}s）"}


def start_background_poll(
    task_id: str,
    rh_task_id: str,
    api_key: str,
    out_dir: str,
    tasks_store: dict,
    on_done: Optional[callable] = None,
) -> threading.Thread:
    """启动后台线程轮询 RH 任务，完成后更新 tasks_store[task_id]。

    Args:
        task_id: 内部任务 ID
        rh_task_id: RH 任务 ID
        api_key: RH API Key
        out_dir: 输出目录
        tasks_store: 任务字典（app.py 的 oneshot_tasks 等）
        on_done: 完成回调（可选）

    Returns:
        Thread 实例（已启动）
    """
    def _bg():
        try:
            result = poll_seedance_and_download(rh_task_id, api_key, out_dir)
            t = tasks_store.get(task_id)
            if t:
                t.update(result)
                t["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            if on_done:
                on_done(task_id, result)
        except Exception as e:
            t = tasks_store.get(task_id)
            if t:
                t["status"] = "failed"
                t["error"] = f"轮询异常: {e}"
                t["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    th = threading.Thread(target=_bg, daemon=True)
    th.start()
    return th
