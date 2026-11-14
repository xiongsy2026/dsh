"""视频换人 / 换背景：RunningHub AI App 编排。

换人默认 DreamID-V（2028374986792116225）。
换背景做成可切换的应用目录，方便对比多个 RH 应用。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from config import settings

LogFn = Optional[Callable[[str], None]]


def _log(on_log: LogFn, msg: str) -> None:
    if on_log:
        on_log(msg)
    else:
        print(msg, flush=True)


def _ffprobe_duration(path: str) -> float:
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", path,
            ],
            capture_output=True, text=True, encoding="utf-8",
        )
        return max(1.0, round(float((proc.stdout or "1").strip() or "1")))
    except Exception:
        return 1.0


def person_apps() -> List[Dict[str, Any]]:
    app_id = str(getattr(settings, "RUNNINGHUB_FACESWAP_WORKFLOW_ID", "") or "").strip()
    new_app_id = str(getattr(settings, "RUNNINGHUB_FACESWAP_V2_APP_ID", "") or "1983193873642102786").strip()
    return [
        {
            "key": "dreamid_v",
            "name": "DreamID-V 换人",
            "app_id": app_id,
            "is_ai_app": True,
            "kind": "person",
            "ready": bool(app_id),
            "hint": "原视频 + 人物图。保持表情/头部，替换人脸。",
            "video_node": {"nodeId": "172", "fieldName": "video"},
            "image_node": {"nodeId": "149", "fieldName": "image"},
            "extra": [
                {"nodeId": "135", "fieldName": "value", "from": "fps", "default": "30"},
                {"nodeId": "154", "fieldName": "value", "from": "duration"},
                {"nodeId": "145", "fieldName": "value", "default": "1280"},
                {
                    "nodeId": "192",
                    "fieldName": "text",
                    "from": "prompt",
                    "default": "保持人物面部表情和头部一致不变，增强真实感。手指要清晰可见",
                },
                {"nodeId": "170", "fieldName": "text", "default": "人"},
            ],
        },
        {
            "key": "video_swap_v2",
            "name": "视频换人 V2",
            "app_id": new_app_id,
            "is_ai_app": True,
            "kind": "person",
            "ready": bool(new_app_id),
            "hint": "原视频 + 人物图。节点 352/318/339，duration=int 类型。",
            "video_node": {"nodeId": "352", "fieldName": "video"},
            "image_node": {"nodeId": "318", "fieldName": "image"},
            "extra": [
                {"nodeId": "339", "fieldName": "int", "from": "duration"},
            ],
        },
    ]


def scene_apps() -> List[Dict[str, Any]]:
    """换背景应用目录。未配齐节点的项仍可出现在下拉里，提交前需 probe 或手填节点。"""
    raw = str(getattr(settings, "RUNNINGHUB_BGSWAP_APPS_JSON", "") or "").strip()
    extras: List[Dict[str, Any]] = []
    if raw:
        try:
            extras = json.loads(raw)
            if not isinstance(extras, list):
                extras = []
        except json.JSONDecodeError:
            extras = []
    builtin = [
        {
            "key": "bg_replace",
            "name": "视频换背景",
            "app_id": str(getattr(settings, "RUNNINGHUB_BGSWAP_APP_ID", "") or "2058405935890128898"),
            "is_ai_app": True,
            "kind": "scene",
            "ready": True,
            "hint": "原视频 + 背景图。节点 1344/video、1345/image。",
            "video_node": {"nodeId": "1344", "fieldName": "video"},
            "image_node": {"nodeId": "1345", "fieldName": "image"},
            "extra": [
                {"nodeId": "1339", "fieldName": "value", "default": "20"},
                {"nodeId": "1357", "fieldName": "select", "default": "3"},
                {"nodeId": "1351", "fieldName": "select", "default": "3"},
            ],
        }
    ]
    merged: List[Dict[str, Any]] = []
    seen = set()
    for item in extras + builtin:
        key = str(item.get("key") or item.get("app_id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        item.setdefault("kind", "scene")
        item.setdefault("is_ai_app", True)
        vn = item.get("video_node") or {}
        item["ready"] = bool(item.get("app_id") and vn.get("nodeId"))
        merged.append(item)
    return merged


def list_apps() -> Dict[str, List[Dict[str, Any]]]:
    return {"person": person_apps(), "scene": scene_apps()}


def _find_app(kind: str, key_or_id: str) -> Dict[str, Any]:
    pool = person_apps() if kind == "person" else scene_apps()
    for item in pool:
        if item.get("key") == key_or_id or item.get("app_id") == key_or_id:
            return item
    raise RuntimeError(f"未找到{kind}应用: {key_or_id}")


def _build_nodes(
    spec: Dict[str, Any],
    video_file: str,
    image_file: str,
    duration: int,
    prompt: str,
    fps: str = "30",
) -> List[Dict[str, str]]:
    nodes: List[Dict[str, str]] = []
    vn = spec.get("video_node") or {}
    inn = spec.get("image_node") or {}
    if not vn.get("nodeId") or not inn.get("nodeId"):
        raise RuntimeError(
            f"应用 {spec.get('name') or spec.get('app_id')} 尚未配置视频/图片节点。"
            "请先点「探测应用」或在配置里补 nodeId。"
        )
    nodes.append({"nodeId": str(vn["nodeId"]), "fieldName": vn.get("fieldName") or "video", "fieldValue": video_file})
    nodes.append({"nodeId": str(inn["nodeId"]), "fieldName": inn.get("fieldName") or "image", "fieldValue": image_file})
    ctx = {"duration": str(duration), "fps": fps, "prompt": prompt or ""}
    for extra in spec.get("extra") or []:
        src = extra.get("from")
        value = extra.get("default", "")
        if src:
            value = ctx.get(src) or value
        if value is None or value == "":
            continue
        nodes.append({
            "nodeId": str(extra["nodeId"]),
            "fieldName": extra.get("fieldName") or "value",
            "fieldValue": str(value),
        })
    return nodes


async def probe_ai_app(app_id: str) -> Dict[str, Any]:
    """尝试拉 AI 应用节点说明，方便接入新的换背景应用。"""
    import httpx

    key = (
        str(getattr(settings, "RUNNINGHUB_ENTERPRISE_API_KEY", "") or "").strip()
        or str(getattr(settings, "RUNNINGHUB_API_KEY", "") or "").strip()
    )
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    urls = [
        f"https://www.runninghub.ai/openapi/v2/query/ai-app/{app_id}",
        f"https://www.runninghub.ai/task/openapi/ai-app/demo?webappId={app_id}",
        f"https://www.runninghub.cn/openapi/v2/query/ai-app/{app_id}",
    ]
    async with httpx.AsyncClient(timeout=30.0) as client:
        last: Any = None
        for url in urls:
            try:
                resp = await client.get(url, headers=headers)
                last = {"url": url, "status": resp.status_code, "body": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text[:2000]}
                if resp.status_code == 200:
                    return last
            except Exception as exc:
                last = {"url": url, "error": str(exc)}
        return {"ok": False, "tried": last}


async def run_person_scene(
    video_path: str,
    output_dir: str,
    swap_person: bool = False,
    swap_scene: bool = False,
    person_image_path: Optional[str] = None,
    scene_image_path: Optional[str] = None,
    person_app_key: str = "dreamid_v",
    scene_app_key: str = "bg_v2",
    prompt: str = "",
    on_log: LogFn = None,
) -> str:
    """按勾选顺序：先换人，再换背景。返回最终本地视频路径。"""
    if not swap_person and not swap_scene:
        raise RuntimeError("请至少勾选换人或换背景")
    if swap_person and (not person_image_path or not Path(person_image_path).exists()):
        raise RuntimeError("勾选换人后必须上传人物图")
    if swap_scene and (not scene_image_path or not Path(scene_image_path).exists()):
        raise RuntimeError("勾选换背景后必须上传背景图")
    if not Path(video_path).exists():
        raise RuntimeError(f"视频不存在: {video_path}")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    current = video_path

    if swap_person:
        spec = _find_app("person", person_app_key)
        _log(on_log, f"▶ 换人：{spec.get('name')} ({spec.get('app_id')})")
        current = await _run_generic_app(
            spec=spec,
            video_path=current,
            image_path=person_image_path,
            output_path=str(out / "person_swapped.mp4"),
            prompt=prompt,
            on_log=on_log,
        )
        _log(on_log, f"✓ 换人完成 → {current}")

    if swap_scene:
        spec = _find_app("scene", scene_app_key)
        _log(on_log, f"▶ 换背景：{spec.get('name')} ({spec.get('app_id')})")
        current = await _run_generic_app(
            spec=spec,
            video_path=current,
            image_path=scene_image_path,
            output_path=str(out / "scene_swapped.mp4"),
            prompt=prompt,
            on_log=on_log,
        )
        _log(on_log, f"✓ 换背景完成 → {current}")

    final_path = str(out / "final.mp4")
    if Path(current).resolve() != Path(final_path).resolve():
        Path(final_path).write_bytes(Path(current).read_bytes())
    return final_path


async def _run_generic_app(
    spec: Dict[str, Any],
    video_path: str,
    image_path: str,
    output_path: str,
    prompt: str,
    on_log: LogFn = None,
) -> str:
    from backend.services.runninghub_client import RunningHubClient
    from backend.services.video_translate_pipeline import _run_runninghub_ai_app

    enterprise = str(getattr(settings, "RUNNINGHUB_ENTERPRISE_API_KEY", "") or "").strip()
    client = RunningHubClient(api_key=enterprise or None)
    _log(on_log, "  上传视频/背景图到 RunningHub…")
    video_file = await client.upload_resource(video_path)
    image_file = await client.upload_resource(image_path)
    duration = int(_ffprobe_duration(video_path))
    nodes = _build_nodes(spec, video_file, image_file, duration, prompt)
    _log(on_log, f"  提交 AI App {spec.get('app_id')}，时长 {duration}s")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    return await _run_runninghub_ai_app(
        str(spec["app_id"]),
        nodes,
        output_path,
        api_key=enterprise or None,
    )
