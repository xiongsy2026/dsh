"""
H3 导演台 RunningHub 客户端

基于 ComfyUI_MiniMaxH3_Director 插件工作流，通过 RunningHub 标准 API 提交多段分镜视频生成任务。

核心节点：MiniMaxH3Director（节点 ID 默认 12）
关键字段：timeline_data（JSON 字符串，包含 segments/keyframes/shots/global/output）

支持任务类型（task_type）：
- t2v  文生视频
- i2v  图生视频
- fl2v 首尾帧生视频（默认，最常用）
- r2v  参考主体生视频
- v2v  视频转视频
- rv2v 参考素材改视频
"""
import asyncio
import json
import os
import time
import uuid
import httpx
from pathlib import Path
from typing import List, Optional, Dict, Any, Callable
from PIL import Image

from config import settings
from .runninghub_client import RunningHubClient


# 导演台工作流 ID 与节点 ID
DIRECTOR_WORKFLOW_ID = getattr(settings, "RUNNINGHUB_H3_DIRECTOR_WORKFLOW_ID", "2084788947984666625")
# ref2va 工作流（r2v / v2v / rv2v 专用）
DIRECTOR_REF2VA_WORKFLOW_ID = getattr(settings, "RUNNINGHUB_H3_DIRECTOR_REF2VA_WORKFLOW_ID", DIRECTOR_WORKFLOW_ID)
DIRECTOR_NODE_ID = getattr(settings, "RUNNINGHUB_H3_DIRECTOR_NODE_ID", "12")

# 需要 ref2va UNET 的任务类型
REF2VA_TASK_TYPES = {"r2v", "v2v", "rv2v"}

# 兼容旧调用（step5_h3_generate 等引用 H3_WORKFLOW_ID / WORKFLOW_ID）
H3_WORKFLOW_ID = DIRECTOR_WORKFLOW_ID
WORKFLOW_ID = DIRECTOR_WORKFLOW_ID

# 画面比例 → width/height 映射（与 step5_multi_shot._DIRECTOR_RATIO_MAP 一致）
_RATIO_TO_SIZE = {
    "9:16": (576, 1024),
    "16:9": (1024, 576),
    "1:1": (832, 832),
    "3:4": (768, 1024),
    "4:3": (1024, 768),
}

# 默认采样参数（与工作流 JSON 一致）
DEFAULT_FRAME_RATE = 24
DEFAULT_FRAMES_PER_5SEC = 124  # 24fps × 5.17s ≈ 124 帧
DEFAULT_NEG_PROMPT = "bad video"
DEFAULT_STEPS = 20
DEFAULT_SAMPLER = "res_multistep"
DEFAULT_SCHEDULER = "simple"
DEFAULT_SHIFT_VIDEO = 12
DEFAULT_SHIFT_AUDIO = 3
DEFAULT_CFG = 1.0
DEFAULT_REF_MAX_SIZE = 736

# 画面尺寸预设（megapixels → width×height，竖版 3:4）
SIZE_PRESETS = {
    "0.2": (608, 352),   # 16:9
    "0.3": (736, 416),   # 16:9
    "0.4": (864, 480),   # 16:9 横版默认
    "0.4_v": (576, 736), # 3:4 竖版
    "0.5": (960, 544),
    "0.6": (1056, 608),
    "0.8": (1216, 672),
    "1.0": (1376, 768),
    "1.5": (1664, 928),
    "2.0": (1920, 1088),
}


def _gen_id() -> str:
    """生成 13 位 segment id（模仿原工作流 msdtx3zi62ktv 格式）"""
    return "msd" + uuid.uuid4().hex[:10]


def _get_image_size(path: str) -> tuple:
    """读取图片宽高"""
    try:
        with Image.open(path) as im:
            return im.size  # (width, height)
    except Exception:
        return (768, 1024)


async def _upload_image_async(client: RunningHubClient, path: str) -> Dict[str, Any]:
    """上传图片到 RH，返回 {imageFile, width, height}"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"图片不存在: {path}")
    file_name = await client.upload_resource(str(p))
    w, h = _get_image_size(path)
    return {"imageFile": file_name, "width": w, "height": h}


async def _upload_file_async(client: RunningHubClient, path: str) -> Dict[str, Any]:
    """上传任意文件（视频/音频等）到 RH，返回 {videoFile, fileName}"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    file_name = await client.upload_resource(str(p))
    return {"videoFile": file_name, "fileName": p.name}


def _build_segment(
    seg_id: str,
    start: int,
    length: int,
    frame_rate: int,
    duration_sec: int,
    prompt: str,
    negative_prompt: str,
    start_image: Optional[Dict] = None,
    end_image: Optional[Dict] = None,
    task_type: str = "fl2v",
    reference_video: Optional[Dict] = None,
    ref_images: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """构造单个 segment

    Args:
        reference_video: v2v/rv2v 模式下的参考视频片段，格式 {"videoFile": "openapi/xxx.mp4", "fileName": "..."}
        ref_images: rv2v 模式下的参考图片（产品图），格式 [{"imageFile": "openapi/xxx.jpg", "width":..,"height":..}]
    """
    frame_count = length
    has_start = start_image is not None
    has_end = end_image is not None
    seg = {
        "id": seg_id,
        "start": start,
        "length": length,
        "frameCount": frame_count,
        "durationSec": duration_sec,
        "prompt": prompt,
        "negativePrompt": negative_prompt,
        "isStartFrame": has_start,
        "isEndFrame": has_end,
        "genImage": start_image or {"imageFile": ""},
        "endImage": end_image or {"imageFile": ""},
        "taskType": task_type if task_type != "fl2v" else "",
        "refs": ref_images or [],
    }
    # v2v/rv2v 模式：segment 级参考视频字段
    if reference_video is not None:
        seg["referenceVideo"] = reference_video
    return seg


def _build_keyframes(segments: List[Dict], frame_rate: int) -> List[Dict]:
    """从 segments 派生 keyframes（每段拆 _s/_e 两个关键帧）"""
    keyframes = []
    for seg in segments:
        seg_id = seg["id"]
        half = seg["length"] // 2
        # 起始关键帧
        keyframes.append({
            "id": f"{seg_id}_s",
            "imageFile": seg.get("genImage", {}).get("imageFile", ""),
            "width": seg.get("genImage", {}).get("width", 768),
            "height": seg.get("genImage", {}).get("height", 1024),
            "start": seg["start"],
            "length": half,
            "frameCount": half,
            "durationSec": seg["durationSec"],
            "prompt": seg["prompt"],
            "negativePrompt": seg["negativePrompt"],
            "isStartFrame": True,
            "isEndFrame": False,
        })
        # 结束关键帧
        keyframes.append({
            "id": f"{seg_id}_e",
            "imageFile": seg.get("endImage", {}).get("imageFile", ""),
            "width": seg.get("endImage", {}).get("width", 768),
            "height": seg.get("endImage", {}).get("height", 1024),
            "start": seg["start"] + half,
            "length": half,
            "frameCount": half,
            "durationSec": seg["durationSec"],
            "prompt": "",
            "negativePrompt": seg["negativePrompt"],
            "isStartFrame": False,
            "isEndFrame": True,
        })
    return keyframes


def _build_shots(segments: List[Dict]) -> List[Dict]:
    """从 segments 派生 shots"""
    shots = []
    for seg in segments:
        shots.append({
            "id": seg["id"],
            "durationSec": seg["durationSec"],
            "prompt": seg["prompt"],
            "negativePrompt": seg["negativePrompt"],
            "startImage": seg.get("genImage", {"imageFile": ""}),
            "endImage": seg.get("endImage", {"imageFile": ""}),
        })
    return shots


def build_timeline_data(
    task_type: str,
    segments_input: List[Dict[str, Any]],
    frame_rate: int = DEFAULT_FRAME_RATE,
    width: int = 576,
    height: int = 736,
    ref_max_size: int = DEFAULT_REF_MAX_SIZE,
    global_prompt: str = "",
    audio_mode: str = "none",
    continuity_enabled: bool = False,
    run_select_enabled: bool = False,
    fill_strategy: str = "cache",
) -> str:
    """
    构造 timeline_data JSON 字符串

    segments_input 格式：
    [
        {
            "durationSec": 5,
            "prompt": "...",
            "negativePrompt": "bad video",
            "startImage": {"imageFile": "openapi/xxx.png", "width": 768, "height": 1024},  # 已上传
            "endImage": {"imageFile": "openapi/yyy.png", "width": 768, "height": 1024},
        }
    ]

    返回可直接填入 nodeInfoList 的 JSON 字符串
    """
    total_duration = sum(s.get("durationSec", 5) for s in segments_input)
    total_frames = int(total_duration * frame_rate / 5 * DEFAULT_FRAMES_PER_5SEC / (frame_rate * 5 / DEFAULT_FRAMES_PER_5SEC))
    # 简化：每段 5 秒 = 124 帧
    frames_per_seg = []
    cursor = 0
    built_segments = []
    run_selection_ids: List[str] = []
    for si in segments_input:
        dur = si.get("durationSec", 5)
        # 5秒=124帧，按比例
        length = int(dur * DEFAULT_FRAMES_PER_5SEC / 5)
        seg_id = _gen_id()
        seg = _build_segment(
            seg_id=seg_id,
            start=cursor,
            length=length,
            frame_rate=frame_rate,
            duration_sec=dur,
            prompt=si.get("prompt", ""),
            negative_prompt=si.get("negativePrompt", DEFAULT_NEG_PROMPT),
            start_image=si.get("startImage"),
            end_image=si.get("endImage"),
            task_type=si.get("taskType", task_type),  # segment 级 task_type 优先
            reference_video=si.get("referenceVideo"),  # v2v/rv2v 参考视频
            ref_images=si.get("refs"),  # rv2v 参考图片
        )
        # 注入参考音频 / 参考视频（r2v/rv2v）
        if si.get("refAudios"):
            seg["refAudios"] = si["refAudios"]
        if si.get("refVideos"):
            seg["refVideos"] = si["refVideos"]
        # 选择运行
        seg_selected = si.get("selected", True)
        seg["selected"] = seg_selected
        if run_select_enabled and seg_selected:
            run_selection_ids.append(seg_id)
        built_segments.append(seg)
        cursor += length

    total_frames = cursor

    # v2v/rv2v 模式：顶层 video 和 global.referenceVideo 必须填充，否则节点 12 报
    # "No source video in MiniMax H3 Director. Upload a video inside the node timeline UI before running."
    top_video = {
        "fileName": "",
        "videoFile": "",
        "subfolder": "",
        "type": "input",
        "frames": [],
        "frameMap": [],
        "sourceFrameCount": total_frames,
        "deletedSourceRanges": [],
    }
    global_ref_video = {"videoFile": "", "fileName": "", "type": "input", "subfolder": ""}
    if task_type in ("v2v", "rv2v"):
        # 取第一个 segment 的 referenceVideo 填充顶层
        for si in segments_input:
            rv = si.get("referenceVideo")
            if rv:
                top_video["fileName"] = rv.get("fileName", "")
                top_video["videoFile"] = rv.get("videoFile", "")
                top_video["subfolder"] = rv.get("subfolder", "")
                global_ref_video = dict(rv)
                global_ref_video.setdefault("type", "input")
                global_ref_video.setdefault("subfolder", "")
                break

    timeline = {
        "version": 5,
        "editMode": "segment",
        "totalFrames": total_frames,
        "frameRate": frame_rate,
        "video": top_video,
        "videoClips": [],
        "global": {
            "taskType": f"{task_type} — {_task_type_label(task_type)}",
            "prompt": global_prompt,
            "refs": [],
            "referenceVideo": global_ref_video,
            "continuousReference": False,
            "genImage": {"imageFile": ""},
            "sourceWidth": width,
            "sourceHeight": height,
            "refAudios": [],
        },
        "output": {
            "mode": "fixed",
            "aspectRatio": "3:4 (竖版标准)" if height > width else "16:9 (横版标准)",
            "megapixels": 0.4,
            "multiple": 32,
            "longEdge": max(width, height),
            "width": width,
            "height": height,
            "maxExportFrames": 0,
            "exportMode": "all",
            "audioMode": audio_mode,
            "continuityEnabled": continuity_enabled,
            "continuityOverlapFrames": 9,
        },
        "runSelectEnabled": run_select_enabled,
        "runSelection": run_selection_ids,
        "fillStrategy": fill_strategy,
        "segments": built_segments,
        "timelineMode": task_type,
        "width": width,
        "height": height,
        "refMaxSize": ref_max_size,
        "gen": {"defaultFrameCount": DEFAULT_FRAMES_PER_5SEC},
        "keyframes": _build_keyframes(built_segments, frame_rate),
        "durationSec": total_duration,
        "shots": _build_shots(built_segments),
    }
    return json.dumps(timeline, ensure_ascii=False)


def _task_type_label(task_type: str) -> str:
    labels = {
        "t2v": "文生视频(Text to Video)",
        "i2v": "图生视频(Image to Video)",
        "fl2v": "首尾帧生视频(First-Last Frame)",
        "r2v": "参考主体生视频(Reference to Video)",
        "v2v": "视频转视频(Video to Video)",
        "rv2v": "参考素材改视频(Reference Video to Video)",
    }
    return labels.get(task_type, task_type)


async def submit_h3_director(
    task_type: str,
    segments: List[Dict[str, Any]],
    frame_rate: int = DEFAULT_FRAME_RATE,
    width: int = 576,
    height: int = 736,
    ref_max_size: int = DEFAULT_REF_MAX_SIZE,
    global_prompt: str = "",
    audio_mode: str = "none",
    continuity_enabled: bool = False,
    steps: int = DEFAULT_STEPS,
    cfg: float = DEFAULT_CFG,
    seed: int = 0,
    sampler: str = DEFAULT_SAMPLER,
    scheduler: str = DEFAULT_SCHEDULER,
    shift_video: float = DEFAULT_SHIFT_VIDEO,
    shift_audio: float = DEFAULT_SHIFT_AUDIO,
    run_select_enabled: bool = False,
    fill_strategy: str = "cache",
    engine: str = "director",  # 保留参数兼容旧调用，实际只走 director
) -> Dict[str, Any]:
    """
    提交 H3 导演台任务

    segments 输入格式：
    [
        {
            "durationSec": 5,
            "prompt": "镜头描述...",
            "negativePrompt": "bad video",
            "startImagePath": "/local/start.png",  # 本地路径，将自动上传
            "endImagePath": "/local/end.png",      # 本地路径，将自动上传
        }
    ]

    返回：{"taskId": "...", "taskStatus": "QUEUED", "timeline_data": "...", "segment_count": N}
    """
    if not DIRECTOR_WORKFLOW_ID:
        raise RuntimeError("未配置 RUNNINGHUB_H3_DIRECTOR_WORKFLOW_ID")
    if not segments:
        raise ValueError("至少需要 1 个分镜段")

    # 根据 task_type 自动选择工作流：r2v/v2v/rv2v → ref2va 工作流，其余 → fl2va 工作流
    workflow_id = DIRECTOR_REF2VA_WORKFLOW_ID if task_type in REF2VA_TASK_TYPES else DIRECTOR_WORKFLOW_ID
    node_id = DIRECTOR_NODE_ID

    client = RunningHubClient()

    # 1. 并发上传所有分镜图片 + 参考图片
    upload_tasks = []
    upload_slots = []  # 记录 (seg_idx, "start"/"end"/"ref_video"/"ref_img")
    for i, seg in enumerate(segments):
        if seg.get("startImagePath"):
            upload_tasks.append(_upload_image_async(client, seg["startImagePath"]))
            upload_slots.append((i, "start"))
        if seg.get("endImagePath"):
            upload_tasks.append(_upload_image_async(client, seg["endImagePath"]))
            upload_slots.append((i, "end"))
        # v2v/rv2v：上传参考视频
        if seg.get("referenceVideoPath"):
            upload_tasks.append(_upload_file_async(client, seg["referenceVideoPath"]))
            upload_slots.append((i, "ref_video"))
        # rv2v：上传参考图片（产品图）1-9
        for ri, ref_path in enumerate(seg.get("refImagePaths", [])):
            upload_tasks.append(_upload_image_async(client, ref_path))
            upload_slots.append((i, f"ref_img_{ri}"))
        # rv2v：上传参考音频 1-3
        for ai, audio_path in enumerate(seg.get("refAudioPaths", [])):
            upload_tasks.append(_upload_file_async(client, audio_path))
            upload_slots.append((i, f"ref_audio_{ai}"))
        # r2v/rv2v：上传参考视频 1-3
        for vi, vid_path in enumerate(seg.get("refVideoPaths", [])):
            upload_tasks.append(_upload_file_async(client, vid_path))
            upload_slots.append((i, f"ref_video_extra_{vi}"))

    uploaded = await asyncio.gather(*upload_tasks) if upload_tasks else []

    # 2. 把上传结果回填到 segments
    seg_ref_imgs: Dict[int, List[Dict]] = {}
    seg_ref_audios: Dict[int, List[Dict]] = {}
    seg_ref_videos: Dict[int, List[Dict]] = {}
    for (seg_idx, pos), info in zip(upload_slots, uploaded):
        if pos == "start":
            segments[seg_idx]["startImage"] = info
        elif pos == "end":
            segments[seg_idx]["endImage"] = info
        elif pos == "ref_video":
            # 段级源视频（v2v/rv2v 主源视频）
            file_name = os.path.basename(segments[seg_idx]["referenceVideoPath"])
            segments[seg_idx]["referenceVideo"] = {
                "videoFile": info.get("videoFile") or info.get("fileName") or info,
                "fileName": file_name,
                "type": "input",
                "subfolder": "",
            }
        elif pos.startswith("ref_img_"):
            seg_ref_imgs.setdefault(seg_idx, []).append(info)
        elif pos.startswith("ref_audio_"):
            seg_ref_audios.setdefault(seg_idx, []).append(info)
        elif pos.startswith("ref_video_extra_"):
            seg_ref_videos.setdefault(seg_idx, []).append(info)

    for seg_idx, imgs in seg_ref_imgs.items():
        segments[seg_idx]["refs"] = imgs
    for seg_idx, audios in seg_ref_audios.items():
        segments[seg_idx]["refAudios"] = audios
    for seg_idx, vids in seg_ref_videos.items():
        segments[seg_idx]["refVideos"] = vids

    # 3. 构造 timeline_data
    timeline_data = build_timeline_data(
        task_type=task_type,
        segments_input=segments,
        frame_rate=frame_rate,
        width=width,
        height=height,
        ref_max_size=ref_max_size,
        global_prompt=global_prompt,
        audio_mode=audio_mode,
        continuity_enabled=continuity_enabled,
        run_select_enabled=run_select_enabled,
        fill_strategy=fill_strategy,
    )

    # 4. 构造 nodeInfoList（导演台节点 12：timeline_data + director 专属字段）
    node_info_list = [
        # 核心字段：timeline_data
        {"nodeId": node_id, "fieldName": "timeline_data", "fieldValue": timeline_data},
        {"nodeId": node_id, "fieldName": "task_type", "fieldValue": f"{task_type} — {_task_type_label(task_type)}"},
        {"nodeId": node_id, "fieldName": "global_prompt", "fieldValue": global_prompt},
        {"nodeId": node_id, "fieldName": "cfg", "fieldValue": cfg},
        {"nodeId": node_id, "fieldName": "seed", "fieldValue": seed},
        {"nodeId": node_id, "fieldName": "frame_rate", "fieldValue": frame_rate},
        {"nodeId": node_id, "fieldName": "width", "fieldValue": width},
        {"nodeId": node_id, "fieldName": "height", "fieldValue": height},
        {"nodeId": node_id, "fieldName": "ref_max_size", "fieldValue": ref_max_size},
        {"nodeId": node_id, "fieldName": "total_frames", "fieldValue": int(sum(s.get("durationSec",5) for s in segments) * DEFAULT_FRAMES_PER_5SEC / 5)},
        {"nodeId": node_id, "fieldName": "steps", "fieldValue": steps},
        {"nodeId": node_id, "fieldName": "sampler", "fieldValue": sampler},
        {"nodeId": node_id, "fieldName": "scheduler", "fieldValue": scheduler},
        {"nodeId": node_id, "fieldName": "shift_video", "fieldValue": shift_video},
        {"nodeId": node_id, "fieldName": "shift_audio", "fieldValue": shift_audio},
    ]

    # 5. 提交任务（ComfyUI 工作流，走 /run/workflow/ 路径）
    result = await client.submit_task(
        workflow_id=workflow_id,
        node_info_list=node_info_list,
        is_ai_app=False,
    )
    result["timeline_data"] = timeline_data
    result["segment_count"] = len(segments)
    result["task_type"] = task_type
    result["engine"] = "director"
    result["workflow_id"] = workflow_id
    result["unet_type"] = "ref2va" if task_type in REF2VA_TASK_TYPES else "fl2va"
    return result


async def poll_and_download(
    rh_task_id: str,
    output_path: str,
    timeout: int = 3600,
    on_progress=None,
) -> str:
    """
    轮询任务并下载视频

    on_progress: 可选回调 fn(elapsed_sec, status, msg)
    """
    client = RunningHubClient()
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    start = time.time()
    fail_count = 0

    while time.time() - start < timeout:
        await asyncio.sleep(30)
        elapsed = int(time.time() - start)

        try:
            resp = await client.query_task(rh_task_id)
            fail_count = 0
        except Exception as e:
            fail_count += 1
            if on_progress:
                on_progress(elapsed, "ERROR", f"查询异常({fail_count}/3): {e}")
            if fail_count >= 3:
                raise RuntimeError(f"H3 导演台查询连续失败 3 次: {e}")
            await asyncio.sleep(min(30, 8 * fail_count))
            continue

        status = resp.get("taskStatus", "UNKNOWN")
        msg = resp.get("msg", "")
        if on_progress:
            on_progress(elapsed, status, msg)

        if status == "SUCCESS":
            outputs = resp.get("results", [])
            for item in outputs:
                url = item.get("url", "")
                otype = item.get("outputType", "")
                if otype == "mp4" or url:
                    async with httpx.AsyncClient(timeout=300.0) as hc:
                        vresp = await hc.get(url)
                        vresp.raise_for_status()
                        p.write_bytes(vresp.content)
                    return str(p)
            raise RuntimeError(f"H3 导演台任务成功但无视频结果: {outputs}")

        if status == "FAILED":
            raise RuntimeError(f"H3 导演台任务失败: {msg or resp}")

    raise RuntimeError(f"H3 导演台任务超时（{timeout}s）")


async def generate_video(
    mode: str = "reference",
    output_path: str = "",
    reference_images: Optional[List[str]] = None,
    first_frame_path: Optional[str] = None,
    last_frame_path: Optional[str] = None,
    prompt: str = "",
    duration: int = 5,
    aspect_ratio: str = "9:16",
    megapixels: float = 0.4,
    max_wait: int = 3600,
    progress_callback: Optional[Callable] = None,
    reference_videos: Optional[List[str]] = None,
    **_extra,
) -> str:
    """统一生成入口（与 manxue_h3_client.generate_video 接口对齐）

    通过导演台工作流生成单段视频，支持 t2v/i2v/fl2v/r2v 全模式。
    供 app.py 的 H3 Studio「runninghub」渠道及 step5_h3_generate 调用。

    模式自动选择：
      - first_last_frame 模式 → fl2v（首+尾帧）
      - reference 模式 + 多张参考图 → r2v（参考主体）
      - reference 模式 + 1 张图 → i2v（首帧）
      - reference 模式 + 0 张图 → t2v（纯文本）
    """
    def _notify(status, msg):
        if progress_callback:
            try:
                progress_callback(status, 0, msg)
            except Exception:
                pass

    width, height = _RATIO_TO_SIZE.get(aspect_ratio, (576, 1024))

    # 根据 mode + 图片自动判定 task_type 与 segment 字段
    if mode == "first_last_frame" and first_frame_path and last_frame_path:
        task_type = "fl2v"
        seg = {
            "durationSec": duration,
            "prompt": prompt,
            "startImagePath": first_frame_path,
            "endImagePath": last_frame_path,
        }
    elif reference_images and len(reference_images) >= 2:
        task_type = "r2v"
        seg = {
            "durationSec": duration,
            "prompt": prompt,
            "refImagePaths": list(reference_images),
        }
    elif reference_images and len(reference_images) == 1:
        task_type = "i2v"
        seg = {
            "durationSec": duration,
            "prompt": prompt,
            "startImagePath": reference_images[0],
        }
    elif first_frame_path:
        task_type = "i2v"
        seg = {
            "durationSec": duration,
            "prompt": prompt,
            "startImagePath": first_frame_path,
        }
    else:
        task_type = "t2v"
        seg = {
            "durationSec": duration,
            "prompt": prompt,
        }

    # rv2v：同时有参考视频与参考图
    if reference_videos and len(reference_videos) > 0:
        task_type = "rv2v"
        seg["referenceVideoPath"] = reference_videos[0]
        if reference_images:
            seg["refImagePaths"] = list(reference_images[:1])

    _notify("submitting", f"提交导演台任务（{task_type}，{width}x{height}）...")
    result = await submit_h3_director(
        task_type=task_type,
        segments=[seg],
        width=width,
        height=height,
    )
    rh_task_id = result.get("taskId")
    if not rh_task_id:
        raise RuntimeError(f"导演台任务提交失败，无 taskId: {result}")

    _notify("generating", f"导演台生成中（任务 {rh_task_id}）...")
    video_path = await poll_and_download(
        rh_task_id=rh_task_id,
        output_path=output_path,
        timeout=max_wait,
        on_progress=lambda e, s, m: _notify("generating", f"[{e}s] {s} {m}"),
    )
    return video_path
