"""
爆款复刻 wan3.0 服务 - 通过 RunningHub 工作流调用 wan3.0 模型，
将爆款视频 + 产品图 + H3 提示词合成为复刻新视频。

流程：
1. 用户上传爆款视频 + 产品图 + 反推得到的 H3 prompt
2. 上传视频/图片到 RunningHub
3. 提交 wan3.0 工作流（图/视频/prompt 注入对应节点）
4. 轮询任务完成，下载结果视频
"""
import asyncio
from pathlib import Path
from typing import Optional

from backend.services.runninghub_client import (
    RunningHubClient,
    poll_task_until_done,
    API_KEY,
    RUNNINGHUB_BASE_URL,
)
from config import settings


def _get_wan3_config() -> dict:
    """读 wan3.0 工作流配置"""
    return {
        "workflow_id": settings.RUNNINGHUB_WAN3_WORKFLOW_ID,
        "is_ai_app": settings.RUNNINGHUB_WAN3_IS_AI_APP,
        "api_key": settings.RUNNINGHUB_WAN3_API_KEY or API_KEY,
        "base_url": settings.RUNNINGHUB_WAN3_BASE_URL or RUNNINGHUB_BASE_URL,
        "image_node": settings.RUNNINGHUB_WAN3_IMAGE_NODE,
        "video_node": settings.RUNNINGHUB_WAN3_VIDEO_NODE,
        "prompt_node": settings.RUNNINGHUB_WAN3_PROMPT_NODE,
        "fps_node": settings.RUNNINGHUB_WAN3_FPS_NODE,
        "width_node": settings.RUNNINGHUB_WAN3_WIDTH_NODE,
        "height_node": settings.RUNNINGHUB_WAN3_HEIGHT_NODE,
        "width": settings.RUNNINGHUB_WAN3_WIDTH,
        "height": settings.RUNNINGHUB_WAN3_HEIGHT,
        "fps": settings.RUNNINGHUB_WAN3_FPS,
        "instance_type": settings.RUNNINGHUB_WAN3_INSTANCE_TYPE,
    }


def validate_config() -> Optional[str]:
    """校验配置完整性，返回缺失字段说明（None=通过）"""
    missing = []
    if not settings.RUNNINGHUB_WAN3_WORKFLOW_ID:
        missing.append("RUNNINGHUB_WAN3_WORKFLOW_ID")
    if not settings.RUNNINGHUB_WAN3_IMAGE_NODE:
        missing.append("RUNNINGHUB_WAN3_IMAGE_NODE")
    if not settings.RUNNINGHUB_WAN3_VIDEO_NODE:
        missing.append("RUNNINGHUB_WAN3_VIDEO_NODE")
    if not settings.RUNNINGHUB_WAN3_PROMPT_NODE:
        missing.append("RUNNINGHUB_WAN3_PROMPT_NODE")
    if missing:
        return (
            f"wan3.0 配置不完整，请在 .env 或 config.py 补充：{', '.join(missing)}。"
            f"参考 wan2.2 节点配置（如 image=57/video=413/prompt=939）"
        )
    return None


async def submit_replica(
    video_path: str,
    image_path: str,
    prompt: str,
    width: Optional[int] = None,
    height: Optional[int] = None,
    fps: Optional[int] = None,
) -> dict:
    """提交 wan3.0 复刻任务到 RunningHub

    Args:
        video_path: 爆款视频本地路径
        image_path: 产品图本地路径
        prompt: H3 prompt 文本
        width/height/fps: 输出参数覆盖（None 时用 config 默认）

    Returns:
        {
            "task_id": str,
            "video_resource": str,    # 上传后的 RH 资源地址
            "image_resource": str,
            "prompt": str,
            "config": dict,
        }
    """
    err = validate_config()
    if err:
        raise RuntimeError(err)

    cfg = _get_wan3_config()
    if not Path(video_path).exists():
        raise FileNotFoundError(f"视频不存在: {video_path}")
    if not Path(image_path).exists():
        raise FileNotFoundError(f"产品图不存在: {image_path}")
    if not prompt or not prompt.strip():
        raise ValueError("H3 prompt 不能为空")

    client = RunningHubClient(api_key=cfg["api_key"])
    client.base_url = cfg["base_url"]

    # 1. 上传视频和图片
    print(f"  [wan3.0上传] 视频: {video_path}", flush=True)
    video_resource = await client.upload_resource(video_path)
    print(f"  [wan3.0上传] ✓ 视频: {video_resource}", flush=True)
    print(f"  [wan3.0上传] 产品图: {image_path}", flush=True)
    image_resource = await client.upload_resource(image_path)
    print(f"  [wan3.0上传] ✓ 产品图: {image_resource}", flush=True)

    # 2. 构造 nodeInfoList
    actual_width = width if width else cfg["width"]
    actual_height = height if height else cfg["height"]
    actual_fps = fps if fps else cfg["fps"]

    node_info_list = [
        {
            "nodeId": cfg["image_node"],
            "fieldName": "image",
            "fieldValue": image_resource,
            "description": "Product image",
        },
        {
            "nodeId": cfg["video_node"],
            "fieldName": "video",
            "fieldValue": video_resource,
            "description": "Viral video for replica",
        },
        {
            "nodeId": cfg["prompt_node"],
            "fieldName": "value",
            "fieldValue": prompt,
            "description": "H3 prompt",
        },
    ]
    if cfg["fps_node"]:
        node_info_list.append({
            "nodeId": cfg["fps_node"],
            "fieldName": "value",
            "fieldValue": str(actual_fps),
            "description": "Output fps",
        })
    if cfg["width_node"]:
        node_info_list.append({
            "nodeId": cfg["width_node"],
            "fieldName": "value",
            "fieldValue": str(actual_width),
            "description": "Output width",
        })
    if cfg["height_node"]:
        node_info_list.append({
            "nodeId": cfg["height_node"],
            "fieldName": "value",
            "fieldValue": str(actual_height),
            "description": "Output height",
        })

    # 3. 提交任务
    print(
        f"  [wan3.0提交] workflow={cfg['workflow_id']}, "
        f"size={actual_width}x{actual_height}, fps={actual_fps}, "
        f"base={cfg['base_url']}",
        flush=True,
    )
    result = await client.submit_task(
        workflow_id=cfg["workflow_id"],
        node_info_list=node_info_list,
        is_ai_app=cfg["is_ai_app"],
        instance_type=cfg["instance_type"],
    )
    print(f"  [wan3.0提交] ✓ taskId={result.get('taskId', '')}", flush=True)

    return {
        "task_id": result["taskId"],
        "video_resource": video_resource,
        "image_resource": image_resource,
        "prompt": prompt,
        "config": {
            "workflow_id": cfg["workflow_id"],
            "is_ai_app": cfg["is_ai_app"],
            "width": actual_width,
            "height": actual_height,
            "fps": actual_fps,
        },
    }


async def poll_until_done(task_id: str, max_wait: int = 1800) -> dict:
    """轮询 wan3.0 任务直到完成

    Returns:
        RunningHub 任务详情，包含 outputs 列表
    """
    return await poll_task_until_done(task_id, max_wait=max_wait)
