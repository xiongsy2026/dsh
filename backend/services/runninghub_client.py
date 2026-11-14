"""
RunningHub ComfyUI 工作流 API 客户端
用于动作迁移功能的后端对接
"""
import asyncio
import time
import httpx
from pathlib import Path
from typing import List, Optional
from config import settings


RUNNINGHUB_BASE_URL = "https://www.runninghub.ai"  # 国际站（与 .env 配置的 API key 匹配）
# 🔴 不许内置兜底 key（2026-10-03 定：全仓明文 key 清零）。取不到就是配置缺失，宁停下也别
#    静默拿某把写死的 key 去花钱——那种钱在网关账本上对不到人，也永远不会出现在报价里。
API_KEY = (getattr(settings, "RUNNINGHUB_ENTERPRISE_API_KEY", "")
           or getattr(settings, "RUNNINGHUB_API_KEY", "") or "")

# 🔴 2026-10-09 用户指令："视频严格杜绝走 RunningHub 这条线路"（H3 走的正是 RH；本地 RPA 腿保留）。
#    文案里点名替代路，是为了被拒的那一方（agent/人）能立刻知道往哪走，而不是拿到一句"不许"。
#    ⚠️ 这条文案【不许】写"怎么豁免"（原来那句"必须显式传 spend_kind=edit/image"已删）：
#       拒绝文案会被原样贴进日志和对话，等于把绕过说明书发给每一个被拦下的人。豁免的声明方式看源码。
RH_VIDEO_BANNED = (
    "RunningHub 禁止用于生成视频（2026-10-09 用户指令：视频严格杜绝走这条线路）。"
    "要出片请走：seedance(vinted.cam) / wan3-prime / agnes-v，"
    "或本地 RPA 腿 h3-rpa(8093)、motion-rpa —— 那两条是指定保留的。")

# 豁免清单 = 唯一源（回归闸 check-rh-video-ban.py 读它，不许自己抄第二份）。
# 往这里加一条 = 必须同时在那一端证明"它不生成视频"，所以加之前先想清楚谁来担保。
RH_VIDEO_EXEMPT = ("edit", "image")


def rh_video_guard(spend_kind: str = "video") -> None:
    """RunningHub 视频禁令的【唯一判据】。

    凡是【自己拼 HTTP 打 RunningHub 提交口】的腿（不走 submit_task 的那些），
    必须在 post 之前调一次本函数 —— 判据只有一份，别在调用点复制一遍 `== "video"`。
    默认 "video"：没声明就当生成视频，直接拒。
    """
    if str(spend_kind or "") not in RH_VIDEO_EXEMPT:
        raise RuntimeError(RH_VIDEO_BANNED)


def _resolve_motion_mode(motion_mode: Optional[str]) -> str:
    """解析动作迁移模式：'multi'（多图V9）| 'single'（单图AI App）| 'wan22'（Wan2.2 Animate）| 'hand'（Wan21 SCAIL2）"""
    mode = (motion_mode or "").strip().lower()
    if mode not in ("multi", "single", "wan22", "hand"):
        mode = str(getattr(settings, "RUNNINGHUB_MOTION_MODE_DEFAULT", "multi")).lower()
        if mode not in ("multi", "single", "wan22", "hand"):
            mode = "multi"
    return mode


def _get_motion_config(motion_mode: str) -> dict:
    """根据模式返回动作迁移工作流配置（workflow_id / is_ai_app / 节点列表）"""
    mode = _resolve_motion_mode(motion_mode)
    if mode == "single":
        # 单图模式（AI App，仅主图，速度快但易穿模）
        return {
            "mode": "single",
            "workflow_id": getattr(settings, "RUNNINGHUB_MOTION_SINGLE_WORKFLOW_ID", "2068645846073307137"),
            "is_ai_app": getattr(settings, "RUNNINGHUB_MOTION_SINGLE_IS_AI_APP", True),
            "image_nodes": [n.strip() for n in str(
                getattr(settings, "RUNNINGHUB_MOTION_SINGLE_IMAGE_NODES", "299")).split(",") if n.strip()] or ["299"],
            "video_node": getattr(settings, "RUNNINGHUB_MOTION_SINGLE_VIDEO_NODE", "275"),
            "multiswitch_node": getattr(settings, "RUNNINGHUB_MOTION_SINGLE_MULTISWITCH_NODE", ""),
        }
    if mode == "wan22":
        # Wan2.2 Animate 模式（AI App，独立 key + 国际站域名）
        return {
            "mode": "wan22",
            "workflow_id": getattr(settings, "RUNNINGHUB_MOTION_WAN22_WORKFLOW_ID", "2079191464839847938"),
            "is_ai_app": getattr(settings, "RUNNINGHUB_MOTION_WAN22_IS_AI_APP", True),
            "image_nodes": [getattr(settings, "RUNNINGHUB_MOTION_WAN22_IMAGE_NODE", "57")],
            "video_node": getattr(settings, "RUNNINGHUB_MOTION_WAN22_VIDEO_NODE", "413"),
            "multiswitch_node": "",
            # 🔴 内置 key 已清（2026-10-03）：取不到就让 RunningHubClient 显式报错，不静默花这把的钱
            "api_key": getattr(settings, "RUNNINGHUB_MOTION_WAN22_API_KEY", ""),
            "base_url": getattr(settings, "RUNNINGHUB_MOTION_WAN22_BASE_URL", "https://www.runninghub.ai"),
        }
    if mode == "hand":
        # Wan21 SCAIL2 模式（AI App，rhtv 国际站，独立 key）
        # 复用 MOTION_PRODUCT_* 配置；节点：24=image, 25=video, 42=width, 43=height, 48=text
        return {
            "mode": "hand",
            "workflow_id": getattr(settings, "MOTION_PRODUCT_WORKFLOW_ID", "2067130016248455169"),
            "is_ai_app": getattr(settings, "MOTION_PRODUCT_IS_AI_APP", True),
            "image_nodes": [getattr(settings, "MOTION_PRODUCT_IMAGE_NODE", "24")],
            "video_node": getattr(settings, "MOTION_PRODUCT_VIDEO_NODE", "25"),
            "multiswitch_node": "",
            "api_key": getattr(settings, "MOTION_PRODUCT_API_KEY", ""),
            "base_url": getattr(settings, "MOTION_PRODUCT_BASE_URL", "https://rhtv.runninghub.ai"),
        }
    # 多图模式（V9 ComfyUI，主图+人脸+场景+全身，一致性高）
    return {
        "mode": "multi",
        "workflow_id": getattr(settings, "RUNNINGHUB_MOTION_MULTI_WORKFLOW_ID", "2061660674434101250"),
        "is_ai_app": getattr(settings, "RUNNINGHUB_MOTION_MULTI_IS_AI_APP", False),
        "image_nodes": [n.strip() for n in str(
            getattr(settings, "RUNNINGHUB_MOTION_MULTI_IMAGE_NODES", "299,507,508,509,510,517")).split(",") if n.strip()],
        "video_node": getattr(settings, "RUNNINGHUB_MOTION_MULTI_VIDEO_NODE", "545"),
        "multiswitch_node": getattr(settings, "RUNNINGHUB_MOTION_MULTI_MULTISWITCH_NODE", "515"),
    }


# 兼容旧引用（默认多图模式）
WORKFLOW_ID = _get_motion_config(None)["workflow_id"]


def _get_motion_image_nodes() -> List[str]:
    """兼容旧引用：返回默认模式的图片节点列表"""
    return _get_motion_config(None)["image_nodes"]


class RunningHubClient:
    """RunningHub API 客户端"""

    def __init__(self, api_key: str = API_KEY):
        if not api_key:
            raise RuntimeError("RunningHub 未配置密钥：请在 .env 写 "
                               "RUNNINGHUB_ENTERPRISE_API_KEY 或 RUNNINGHUB_API_KEY（源码里不再有内置 key）")
        self.api_key = api_key
        self.base_url = RUNNINGHUB_BASE_URL
        self.timeout = httpx.Timeout(60.0, connect=10.0)

    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    # ── 上传资源 ──
    async def upload_resource(self, file_path: str) -> Optional[str]:
        """上传本地图片/视频，返回 RunningHub 资源地址。

        注意：AI App / ComfyUI 媒体节点必须传 fileName，不是 download_url。
        """
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        async with httpx.AsyncClient(timeout=300.0) as client:
            with p.open("rb") as file_handle:
                files = {"file": (p.name, file_handle, "application/octet-stream")}
                resp = await client.post(
                    f"{self.base_url}/openapi/v2/media/upload/binary",
                    headers=self._headers(),  # 不放 Content-Type，httpx 会自动加 boundary
                    files=files,
                )
            resp.raise_for_status()
            data = resp.json()
        value = data.get("data") or {}
        # 优先 fileName（媒体节点必需），其次 download_url
        resource = value.get("fileName") or value.get("download_url") or value.get("url")
        if not resource:
            raise RuntimeError(f"上传失败，响应缺少资源地址: {data}")
        return resource

    # ── 提交 ComfyUI 任务（高级） ──
    async def submit_task(
        self,
        workflow_id: str = WORKFLOW_ID,
        node_info_list: Optional[list] = None,
        access_password: Optional[str] = None,
        is_ai_app: bool = False,
        instance_type: str = "plus",
        spend_kind: str = "video",
    ) -> dict:
        """提交 RunningHub v2 任务。

        is_ai_app=False: ComfyUI 工作流，POST /run/workflow/{workflowId}
        is_ai_app=True:  AI 应用，POST /run/ai-app/{appId}
        instance_type: "plus"=48G 显存（默认，避免 WanVideo 爆显存）| "default"=24G
        spend_kind: "video"(默认) / "edit"(去水印·超分等加工) / "image"(生图)。
                    只有 RH_VIDEO_EXEMPT 里那两种放行，其余【一律按生成视频拒】。
        """
        # 🔴 2026-10-09 用户指令"视频严格杜绝走 RunningHub 这条线路"（H3 走的正是 RH；保留本地 RPA 腿）。
        #    判据只有一份，在 rh_video_guard() 里 —— 不走 submit_task 的腿（super_clone_wan3 的 H3）也调同一个。
        #    白名单，不是黑名单：早先写成 `if spend_kind == "video"` 时，传 "banana"/""/None 就绕过了。
        #    默认值 "video" 的含义只是"没声明就当生成视频"，放行只认显式豁免（闸 R4 钉这条）。
        rh_video_guard(spend_kind)
        if not workflow_id:
            raise RuntimeError("未配置工作流 ID")
        endpoint = "ai-app" if is_ai_app else "workflow"
        body = {
            "addMetadata": True,
            "nodeInfoList": node_info_list or [],
            "instanceType": instance_type,
            "usePersonalQueue": False,
        }
        async with httpx.AsyncClient(timeout=300.0) as client:
            last_err: Optional[Exception] = None
            for _attempt in range(3):
                try:
                    resp = await client.post(
                        f"{self.base_url}/openapi/v2/run/{endpoint}/{workflow_id}",
                        headers={**self._headers(), "Content-Type": "application/json"},
                        json=body,
                    )
                    if resp.status_code in (502, 503, 504):
                        last_err = RuntimeError(f"网关 {resp.status_code}，重试中")
                        await __import__("asyncio").sleep(3)
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    if not data.get("taskId"):
                        raise RuntimeError(f"提交任务失败: {data}")
                    return {
                        "taskId": data["taskId"],
                        "taskStatus": data.get("status", "QUEUED"),
                    }
                except httpx.HTTPStatusError as e:
                    last_err = e
                    if e.response.status_code in (502, 503, 504):
                        await __import__("asyncio").sleep(3)
                        continue
                    raise
            raise RuntimeError(f"提交任务失败（3 次重试后）: {last_err}")

    # ── 查询任务状态 ──
    async def query_task(self, task_id: str) -> dict:
        """查询 RunningHub v2 任务状态。"""
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self.base_url}/openapi/v2/query",
                headers={**self._headers(), "Content-Type": "application/json"},
                json={"taskId": task_id},
            )
            resp.raise_for_status()
            data = resp.json()
            # 详尽收集失败原因字段，便于诊断 OOM/CRASH/GPU 错误
            error_msg = data.get("errorMessage", "") or ""
            error_code = data.get("errorCode", "") or ""
            failed_reason = data.get("failedReason") or {}
            # failedReason 可能含 exception_type / exception_message
            if isinstance(failed_reason, dict):
                exc_type = failed_reason.get("exception_type", "")
                exc_msg = failed_reason.get("exception_message", "")
                if exc_type or exc_msg:
                    error_msg = f"{error_msg} | {exc_type}: {exc_msg}".strip(" |")
            elif isinstance(failed_reason, str) and failed_reason:
                error_msg = f"{error_msg} | {failed_reason}".strip(" |")
            if error_code and error_code not in error_msg:
                error_msg = f"[{error_code}] {error_msg}".strip()
            return {
                "taskStatus": data.get("status", "UNKNOWN"),
                "progress": 100 if data.get("status") == "SUCCESS" else 0,
                "msg": error_msg,
                "errorCode": error_code,
                "failedReason": failed_reason,
                "results": data.get("results") or [],
                # 上游自己报的消耗（taskCostTime / consumeMoney / consumeCoins）。之前归一化时丢了它，
                # 于是"按 GPU 秒真扣钱"的几条腿（H3 / 去水印）永远拿不到实测数去校准占位单价。
                "usage": data.get("usage") or {},
            }

    # ── 获取任务输出 ──
    async def get_task_output(self, task_id: str) -> dict:
        """获取任务输出，并转换成前端现有格式。"""
        status_data = await self.query_task(task_id)
        outputs = [
            {
                "fileUrl": item.get("url", ""),
                "fileType": item.get("outputType", ""),
                "nodeId": item.get("nodeId", ""),
            }
            for item in status_data.get("results", [])
            if item.get("url")
        ]
        return {"outputs": outputs}

    # ── 取消任务 ──
    async def cancel_task(self, task_id: str) -> dict:
        """
        取消正在执行的任务
        POST /task/openapi/cancel
        """
        url = f"{self.base_url}/task/openapi/cancel"
        body = {
            "apiKey": self.api_key,
            "taskId": task_id,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=self._headers(), json=body)
            resp.raise_for_status()
            data = resp.json()
            return data


# ── 便捷函数：提交动作迁移任务（支持多参考图） ──
async def _submit_motion_transfer_wan22(
    video_path: str,
    image_path: str,
    config: dict,
    fps: int = 24,
    width: int = 720,
    height: int = 1280,
) -> dict:
    """Wan2.2 Animate 工作流提交（独立 key + 国际站域名 + 节点 57/413/935/933/934/939/940）"""
    # Wan2.2 工作流绑定独立账号 + 国际站
    api_key = config.get("api_key") or API_KEY
    base_url = config.get("base_url") or RUNNINGHUB_BASE_URL
    client = RunningHubClient(api_key=api_key)
    client.base_url = base_url  # 覆盖默认国内站

    # 1. 上传视频和图片（用独立 key + 国际站域名）
    video_file = await client.upload_resource(video_path)
    print(f"  [RH-Wan22上传] 视频: {video_file}", flush=True)
    image_file = await client.upload_resource(image_path)
    print(f"  [RH-Wan22上传] 主图: {image_file}", flush=True)

    # 2. 读取 Wan2.2 节点配置
    image_node = getattr(settings, "RUNNINGHUB_MOTION_WAN22_IMAGE_NODE", "57")
    video_node = getattr(settings, "RUNNINGHUB_MOTION_WAN22_VIDEO_NODE", "413")
    fps_node = getattr(settings, "RUNNINGHUB_MOTION_WAN22_FPS_NODE", "935")
    width_node = getattr(settings, "RUNNINGHUB_MOTION_WAN22_WIDTH_NODE", "933")
    height_node = getattr(settings, "RUNNINGHUB_MOTION_WAN22_HEIGHT_NODE", "934")
    pos_node = getattr(settings, "RUNNINGHUB_MOTION_WAN22_POS_PROMPT_NODE", "939")
    neg_node = getattr(settings, "RUNNINGHUB_MOTION_WAN22_NEG_PROMPT_NODE", "940")

    # 3. 参数解析（传入值优先，否则取 config 默认）
    actual_fps = fps if fps != 30 else int(getattr(settings, "RUNNINGHUB_MOTION_WAN22_FPS", 24))
    actual_width = width if width not in (832, 720) else int(getattr(settings, "RUNNINGHUB_MOTION_WAN22_WIDTH", 720))
    actual_height = height if height != 480 else int(getattr(settings, "RUNNINGHUB_MOTION_WAN22_HEIGHT", 1280))
    pos_prompt = str(getattr(settings, "RUNNINGHUB_MOTION_WAN22_POS_PROMPT", "最佳质量"))
    neg_prompt = str(getattr(settings, "RUNNINGHUB_MOTION_WAN22_NEG_PROMPT", ""))

    # 4. 构造 nodeInfoList
    node_info_list = [
        {"nodeId": image_node, "fieldName": "image", "fieldValue": image_file, "description": "Upload image"},
        {"nodeId": video_node, "fieldName": "video", "fieldValue": video_file, "description": "Upload video"},
        {"nodeId": fps_node, "fieldName": "value", "fieldValue": str(actual_fps), "description": "Output video frame rate"},
        {"nodeId": width_node, "fieldName": "value", "fieldValue": str(actual_width), "description": "Output video width"},
        {"nodeId": height_node, "fieldName": "value", "fieldValue": str(actual_height), "description": "Output video height"},
        {"nodeId": pos_node, "fieldName": "value", "fieldValue": pos_prompt, "description": "Brief positive prompt"},
        {"nodeId": neg_node, "fieldName": "value", "fieldValue": neg_prompt, "description": "Brief negative prompt words"},
    ]

    print(
        f"  [RH-Wan22提交] {config['workflow_id']}, fps={actual_fps}, "
        f"size={actual_width}x{actual_height}, base={base_url}",
        flush=True,
    )

    # 5. 提交（强制 plus 实例，Wan2.2 Animate 必须 48G 显存）
    result = await client.submit_task(
        workflow_id=config["workflow_id"],
        node_info_list=node_info_list,
        is_ai_app=True,
    )
    print(f"  [RH-Wan22提交] ✓ taskId={result.get('taskId', '')}", flush=True)
    result["image_count"] = 1
    result["motion_mode"] = "wan22"
    return result


# ── Hand 模式：Wan21 SCAIL2 工作流提交（rhtv 国际站，独立 key）──
async def _submit_motion_transfer_hand(
    video_path: str,
    image_path: str,
    config: dict,
    width: int = 720,
    height: int = 1280,
    max_frames: int = 81,
) -> dict:
    """Wan21 SCAIL2 工作流提交（rhtv 国际站 AI App，5 节点：24=image, 25=video, 42=width, 43=height, 48=text）

    与 product_motion/pipeline._submit_one_wan22 等价的通用链路版本，
    供 replica_motion.run_phase_2a 在 motion_mode='hand' 时调用。

    width/height：原视频实际尺寸（如方形 720x720）。优先使用传入值，保证输出比例
    与原视频一致；仅当未传时回退到 config 默认值（720x1280 竖屏）。
    max_frames：目标生成帧数。SCAIL2 工作流本身没有 max_frames 节点，由工作流端按
    视频原时长自动决定；但过大的尺寸+长视频组合会爆显存，因此本函数会按 size
    自适应压缩 width/height 上限，避免 OOM。
    """
    api_key = config.get("api_key") or API_KEY
    base_url = config.get("base_url") or RUNNINGHUB_BASE_URL
    client = RunningHubClient(api_key=api_key)
    client.base_url = base_url  # 覆盖为 rhtv 国际站

    # 1. 上传视频和图片
    video_file = await client.upload_resource(video_path)
    print(f"  [RH-Hand上传] 视频: {video_file}", flush=True)
    image_file = await client.upload_resource(image_path)
    print(f"  [RH-Hand上传] 主图: {image_file}", flush=True)

    # 2. 读取 SCAIL2 节点配置（复用 MOTION_PRODUCT_*）
    image_node = getattr(settings, "MOTION_PRODUCT_IMAGE_NODE", "24")
    video_node = getattr(settings, "MOTION_PRODUCT_VIDEO_NODE", "25")
    width_node = getattr(settings, "MOTION_PRODUCT_WIDTH_NODE", "42")
    height_node = getattr(settings, "MOTION_PRODUCT_HEIGHT_NODE", "43")
    text_node = getattr(settings, "MOTION_PRODUCT_TEXT_NODE", "48")

    # 3. 参数解析：优先使用传入的 width/height（= 原视频实际尺寸），
    # 仅在未传或为 0 时回退到 config 默认值（720x1280），避免比例失真
    cfg_w = int(getattr(settings, "MOTION_PRODUCT_WIDTH", 720))
    cfg_h = int(getattr(settings, "MOTION_PRODUCT_HEIGHT", 1280))
    actual_width = int(width) if width and int(width) > 0 else cfg_w
    actual_height = int(height) if height and int(height) > 0 else cfg_h

    # 3.1 显存安全上限：按帧数自适应压缩 size
    # 经验值（plus 48G 实例）：
    #   - max_frames ≤ 120：允许 1080×1920
    #   - max_frames ≤ 200：压缩到 720×1280
    #   - max_frames > 200：压缩到 540×960
    size_cap_px = 1080  # 单边像素上限
    if max_frames > 200:
        size_cap_px = 540
    elif max_frames > 120:
        size_cap_px = 720
    if actual_width > size_cap_px or actual_height > size_cap_px:
        scale = size_cap_px / max(actual_width, actual_height)
        actual_width = max(64, int(actual_width * scale))
        actual_height = max(64, int(actual_height * scale))
        # 保证被 8 整除（扩散模型常用对齐）
        actual_width = (actual_width // 8) * 8
        actual_height = (actual_height // 8) * 8
        print(
            f"  [RH-Hand] max_frames={max_frames} > {size_cap_px}px 上限，"
            f"已压缩到 {actual_width}x{actual_height} 防 OOM",
            flush=True,
        )

    text_prompt = str(getattr(settings, "MOTION_PRODUCT_TEXT_PROMPT", "女孩在跳舞"))

    # 4. 构造 nodeInfoList
    node_info_list = [
        {"nodeId": video_node, "fieldName": "video", "fieldValue": video_file, "description": "Upload video"},
        {"nodeId": image_node, "fieldName": "image", "fieldValue": image_file, "description": "Upload image"},
        {"nodeId": width_node, "fieldName": "value", "fieldValue": str(actual_width), "description": "Width Resolution"},
        {"nodeId": height_node, "fieldName": "value", "fieldValue": str(actual_height), "description": "High resolution"},
        {"nodeId": text_node, "fieldName": "text", "fieldValue": text_prompt, "description": "Optional, can be default"},
    ]

    print(
        f"  [RH-Hand提交] {config['workflow_id']}, size={actual_width}x{actual_height}, "
        f"max_frames={max_frames}, base={base_url}",
        flush=True,
    )

    # 5. 提交
    # Hand 模式（SCAIL-V2 动作迁移-丝滑超精细）默认走 plus 实例（48G 显存），
    # 与 multi/wan22 模式保持一致，避免因显存不足导致画面崩坏或 OOM
    instance_type = getattr(settings, "MOTION_PRODUCT_INSTANCE_TYPE", "plus")
    result = await client.submit_task(
        workflow_id=config["workflow_id"],
        node_info_list=node_info_list,
        is_ai_app=True,
        instance_type=instance_type,
    )
    print(f"  [RH-Hand提交] ✓ taskId={result.get('taskId', '')}", flush=True)
    result["image_count"] = 1
    result["motion_mode"] = "hand"
    return result


# ── 便捷函数：提交动作迁移任务（支持多参考图） ──
async def submit_motion_transfer(
    video_path: str,
    image_paths: List[str],
    width: int = 832,
    height: int = 480,
    steps: Optional[int] = None,
    cfg: Optional[float] = None,
    lora_strength: Optional[float] = None,
    cn_strength: Optional[float] = None,
    fps: int = 30,
    max_frames: int = 81,
    motion_mode: Optional[str] = None,
) -> dict:
    """
    提交动作迁移任务到 RunningHub

    motion_mode（由前端选择，默认取 config.RUNNINGHUB_MOTION_MODE_DEFAULT）:
    - "multi"  → V9 多参考图 ComfyUI 工作流（主图+人脸+场景+全身，一致性高，不穿模）
    - "single" → AI App 单图工作流（仅主图，速度快但易穿模）

    参数:
        image_paths: 参考图本地路径列表
            multi 模式: [主图, 人脸, 场景, 全身]（按 IMAGE_NODES 顺序映射，超出节点数跳过）
            single 模式: [主图]（仅用第 1 张）
    """
    mc = _get_motion_config(motion_mode)
    workflow_id = mc["workflow_id"]
    is_ai_app = mc["is_ai_app"]
    image_nodes = mc["image_nodes"]
    video_node = mc["video_node"]
    multiswitch_node = mc["multiswitch_node"]

    if not workflow_id:
        raise RuntimeError(
            f"动作迁移未配置工作流 ID（mode={mc['mode']}），请检查 config.py"
        )
    if not image_paths:
        raise ValueError("至少需要 1 张参考图")

    # Wan2.2 模式：独立 key + 国际站域名，节点结构不同，走专属分支
    if mc["mode"] == "wan22":
        return await _submit_motion_transfer_wan22(
            video_path=video_path,
            image_path=image_paths[0],
            config=mc,
            fps=fps,
            width=width,
            height=height,
        )

    # Hand 模式（Wan21 SCAIL2）：独立 key + rhtv 国际站，5 节点（image/video/width/height/text），走专属分支
    if mc["mode"] == "hand":
        return await _submit_motion_transfer_hand(
            video_path=video_path,
            image_path=image_paths[0],
            config=mc,
            width=width,
            height=height,
            max_frames=max_frames,
        )

    client = RunningHubClient()

    # 1. 上传视频到 RunningHub
    video_url = await client.upload_resource(video_path)
    print(f"  [RH上传] 视频: {video_url}", flush=True)

    # 2. 共享节点 ID 与默认值
    width_node = getattr(settings, "RUNNINGHUB_MOTION_WIDTH_NODE", "422")
    fps_node = getattr(settings, "RUNNINGHUB_MOTION_FPS_NODE", "264")
    cn_node = getattr(settings, "RUNNINGHUB_MOTION_CN_NODE", "266")
    lora_node = getattr(settings, "RUNNINGHUB_MOTION_LORA_NODE", "297")
    steps_node = getattr(settings, "RUNNINGHUB_MOTION_STEPS_NODE", "451")
    cfg_node = getattr(settings, "RUNNINGHUB_MOTION_CFG_NODE", "450")
    select_node = getattr(settings, "RUNNINGHUB_MOTION_SELECT_NODE", "470")

    default_cn = getattr(settings, "RUNNINGHUB_MOTION_CN_STRENGTH", 0.2)
    default_width = getattr(settings, "RUNNINGHUB_MOTION_WIDTH", 900)
    default_steps = getattr(settings, "RUNNINGHUB_MOTION_STEPS", 9)
    default_cfg = getattr(settings, "RUNNINGHUB_MOTION_CFG", 16.0)
    default_lora = getattr(settings, "RUNNINGHUB_MOTION_LORA_STRENGTH", 1.0)
    default_select = getattr(settings, "RUNNINGHUB_MOTION_SELECT", 2)

    actual_cn = cn_strength if cn_strength is not None else default_cn
    actual_steps = steps if steps is not None else default_steps
    actual_cfg = cfg if cfg is not None else default_cfg
    actual_lora = lora_strength if lora_strength is not None else default_lora
    # width 参数：传入值优先（None 时用配置默认值）
    actual_width = width if width is not None and width != 832 else default_width

    # 3. 上传参考图并按节点列表映射
    image_node_infos = []
    uploaded_count = 0
    if is_ai_app:
        # AI App 单图模式：仅用第 1 张
        main_image_url = await client.upload_resource(image_paths[0])
        print(f"  [RH上传] 主图: {main_image_url}", flush=True)
        image_node_infos.append(
            {"nodeId": image_nodes[0], "fieldName": "image", "fieldValue": main_image_url}
        )
        uploaded_count = 1
    else:
        # V9 多参考图模式：按节点列表顺序映射
        ref_labels = ["主图", "人脸", "场景", "全身", "预留1", "预留2"]
        for i, node_id in enumerate(image_nodes):
            if i >= len(image_paths):
                # 预留位（510/517）不传值，跳过
                label = ref_labels[i] if i < len(ref_labels) else "预留"
                print(f"  [RH上传] 节点 {node_id}（{label}）: 预留位不传值", flush=True)
                continue
            img_url = await client.upload_resource(image_paths[i])
            label = ref_labels[i] if i < len(ref_labels) else f"图{i}"
            print(f"  [RH上传] 节点 {node_id}（{label}）: {img_url}", flush=True)
            image_node_infos.append(
                {"nodeId": node_id, "fieldName": "image", "fieldValue": img_url}
            )
            uploaded_count += 1

    # 4. 构造 nodeInfoList
    node_info_list = list(image_node_infos)
    node_info_list.append({"nodeId": video_node, "fieldName": "video", "fieldValue": video_url})
    node_info_list.append({"nodeId": width_node, "fieldName": "value", "fieldValue": str(actual_width)})
    node_info_list.append({"nodeId": fps_node, "fieldName": "value", "fieldValue": str(fps)})
    node_info_list.append({"nodeId": cn_node, "fieldName": "value", "fieldValue": str(actual_cn)})
    node_info_list.append({"nodeId": lora_node, "fieldName": "value", "fieldValue": str(actual_lora)})
    node_info_list.append({"nodeId": steps_node, "fieldName": "value", "fieldValue": str(actual_steps)})
    node_info_list.append({"nodeId": cfg_node, "fieldName": "value", "fieldValue": str(actual_cfg)})
    node_info_list.append({"nodeId": select_node, "fieldName": "select", "fieldValue": str(default_select)})

    # V9 多参开关（始终开启，字段名"开关"对应 AnyBooleanSwitch 的 widget name）
    if not is_ai_app and multiswitch_node:
        node_info_list.append(
            {"nodeId": multiswitch_node, "fieldName": "开关", "fieldValue": "true"}
        )
        print(f"  [RH多参] 节点 {multiswitch_node} 开关: true", flush=True)

    mode_label = f"[{mc['mode']}] " + ("AI App 单图" if is_ai_app else f"V9 多参考图({uploaded_count}图)")
    print(f"  [RH提交] {mode_label} {workflow_id}, cn={actual_cn}, steps={actual_steps}, cfg={actual_cfg}, width={actual_width}", flush=True)

    # 5. 提交任务
    result = await client.submit_task(
        workflow_id=workflow_id,
        node_info_list=node_info_list,
        is_ai_app=is_ai_app,
    )
    print(f"  [RH提交] ✓ taskId={result.get('taskId', '')}", flush=True)
    result["image_count"] = uploaded_count
    result["motion_mode"] = mc["mode"]
    return result


async def poll_task_until_done(task_id: str, max_wait: int = 1800) -> dict:
    """
    轮询任务直到完成（默认最长等待30分钟）
    返回最终输出结果
    """
    client = RunningHubClient()
    start = time.time()
    while time.time() - start < max_wait:
        status_data = await client.query_task(task_id)
        task_status = status_data.get("taskStatus", "UNKNOWN")

        if task_status == "SUCCESS":
            # 获取输出
            output = await client.get_task_output(task_id)
            return {"status": "SUCCESS", "outputs": output.get("outputs", [])}
        elif task_status == "FAILED":
            err = status_data.get("msg", "任务执行失败")
            err_code = status_data.get("errorCode", "")
            failed_reason = status_data.get("failedReason", {})
            print(
                f"  [RH] 任务 {task_id} FAILED: code={err_code}, msg={err}, "
                f"failedReason={failed_reason}",
                flush=True,
            )
            return {
                "status": "FAILED",
                "error": err,
                "errorCode": err_code,
                "failedReason": failed_reason,
            }

        await asyncio.sleep(5)

    return {"status": "TIMEOUT", "error": "任务超时"}
