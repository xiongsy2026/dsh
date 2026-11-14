"""
详情图生成流水线

输入：产品图 + 产品名（可选场景要求/画幅/数量）
流程：
  Step1: 多模态 LLM 识别产品图 + 产品名 → 生成 N 个详情图场景提示词
  Step2: 并发调用生图引擎（image2 或 ideogram4）生成 N 张场景图
  Step3: 轮询所有任务完成，下载结果图片到本地

生图引擎：
  - image2 (默认)：本地 image2_client.image_edit_sync 同步图生图，支持产品图作参考
  - ideogram4 (已隐藏)：RunningHub AI App 文生图，仅靠 prompt 还原产品

设计参考：tvc_agent.py 的任务持久化 + LLM 多模态调用模式
"""
import asyncio
import base64
import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from openai import OpenAI

from config import settings, BASE_DIR
from .image2_client import Image2Client, _compress_image_bytes
from .runninghub_client import RunningHubClient


OUTPUT_DIR = BASE_DIR / "outputs" / "detail_image"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 内存任务存储
_tasks: Dict[str, Dict[str, Any]] = {}


# ═══════════════════════════════════════
# 持久化：任务状态保存/加载
# ═══════════════════════════════════════

def _state_file(task_id: str) -> Path:
    return OUTPUT_DIR / task_id / "_state.json"


def _persist_task(task_id: str) -> None:
    """把内存任务状态保存到磁盘"""
    task = _tasks.get(task_id)
    if not task_id:
        return
    try:
        sf = _state_file(task_id)
        sf.parent.mkdir(parents=True, exist_ok=True)
        safe = {k: v for k, v in task.items() if k != "_fut"}
        sf.write_text(json.dumps(safe, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    except Exception as e:
        print(f"[DetailImage] 保存任务 {task_id} 失败: {e}")


def _load_all_persisted() -> None:
    """服务启动时从磁盘恢复所有任务到内存"""
    if not OUTPUT_DIR.exists():
        return
    for sub in OUTPUT_DIR.iterdir():
        if not sub.is_dir():
            continue
        sf = sub / "_state.json"
        if not sf.exists():
            continue
        try:
            task = json.loads(sf.read_text(encoding="utf-8"))
            tid = task.get("task_id") or sub.name
            if task.get("status") == "running":
                # 重启中断的任务标记为 failed
                task["status"] = "failed"
                task["step"] = "失败：服务重启中断"
                task["error"] = "服务重启导致任务中断"
            _tasks[tid] = task
            print(f"[DetailImage] 恢复任务 {tid}: {task.get('status')}")
        except Exception as e:
            print(f"[DetailImage] 加载 {sub.name} 失败: {e}")


_load_all_persisted()


# ═══════════════════════════════════════
# Step 1: 多模态 LLM 生成 N 个场景提示词
# ═══════════════════════════════════════

SYSTEM_PROMPT = """你是一位享誉全球的顶级电商创意总监，拥有为奢侈品、高科技、快消品、服饰等全品类产品策划详情页的深厚背景。你擅长拆解品牌调性，并将其转化为极其精准的 AI 图像描述词。

任务：根据用户给定的产品图、产品名与数量 N，直接输出 {count} 屏详情页策划方案，不要废话。

先在后台分析（不输出）：
1. 品牌与类目深度分析：类目识别、调性锁定、目标人群与痛点
2. 视觉风格映射：背景环境、光影逻辑

然后按"首图海报 / 卖点展示 / 颜色展示 / 商品信息 / 尺码比例 / 模特生活场景 / 细节深度展示 / 底部服务说明"等典型详情页结构选择 {count} 屏（若 count<10 则取最关键的几屏；count=10 则全量）。

每一屏必需包含 7 个字段，且每屏比例需保持一致：
1. 屏名（如：首图海报 / 卖点展示一 / 颜色展示 / 商品信息 / 尺码比例信息 / 模特生活化场景 / 细节深度展示 / 底部服务说明）
2. 主标题
3. 副标题
4. 信息布局（中央大图/左图右文/三联画/三宫格微距/静物阵列对称 等）
5. 风格与素材（场景道具、背景、色调、模特动作等）
6. 光影（主光方向/辅光/白平衡/景深控制）
7. AI图像描述词（一段精炼英文描述词，image2 图生图模型会用它+产品参考图直接生成这一屏画面，需详细描述场景、产品位置、氛围、构图，末尾标比例如 4:5 portrait）

输出格式（严格 JSON 数组，不要 markdown 代码块，不要多余解释）：
[
  {{
    "scene": "首图海报",
    "title": "主标题",
    "subtitle": "副标题",
    "layout": "信息布局描述",
    "style": "风格与素材描述",
    "lighting": "光影描述",
    "prompt": "English AI image description, scene + product position + mood + composition, end with '4:5 portrait'"
  }},
  ...
]

要求：
- 数组长度严格 = {count}
- 屏之间差异化，覆盖核心营销逻辑（痛点解决 / 技术硬核 / 材质细节 / 颜色美学 / 参数 / 尺码 / 生活场景 / 细节 / 服务）
- prompt 字段用英文，其他字段用中文
- prompt 中不出现具体品牌名（除非用户产品名就是品牌）
- prompt 末尾必须标注比例（如 4:5 portrait / 1:1 / 3:4）
- 主标题副标题简洁有力，符合电商详情页文案调性"""


def _build_llm_messages(
    product_image_paths: List[str],
    product_name: str,
    count: int,
    scene_style: str = "",
    aspect_ratio: str = "3:4 (Portrait Standard)",
) -> List[Dict[str, Any]]:
    """构造多模态 LLM 消息（产品图 + 产品名 → N 个场景 prompt）"""
    system_text = SYSTEM_PROMPT.format(count=count)

    user_lines = [f"产品名：{product_name or '（未填写，请从产品图识别）'}"]
    if scene_style:
        user_lines.append(f"场景要求：\n{scene_style}")
    else:
        user_lines.append("场景要求：（未指定，请根据产品品类自动推理合适的差异化场景）")
    user_lines.append(f"画面比例：{aspect_ratio}")
    user_lines.append(f"请生成 {count} 张详情图的场景提示词，严格按 JSON 数组格式输出。")
    user_text = "\n".join(user_lines)

    # 多张产品图按顺序放入 multimodal content
    image_blocks: List[Dict[str, Any]] = []
    for img_path in product_image_paths:
        p = Path(img_path)
        if not p.exists():
            continue
        content = p.read_bytes()
        content, _ = _compress_image_bytes(content, max_side=1024, quality=85, threshold=200_000)
        b64 = base64.b64encode(content).decode()
        image_blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })

    if not image_blocks:
        raise FileNotFoundError("缺少可用的产品参考图")

    return [
        {"role": "system", "content": system_text},
        {
            "role": "user",
            "content": [*image_blocks, {"type": "text", "text": user_text}],
        },
    ]


def _call_gpt55_sync(messages: List[Dict[str, Any]]) -> str:
    """调用 gpt-6.1-sol 生成场景提示词，失败时切换 MiMo-v2.5 兜底"""
    last_err: Optional[Exception] = None
    for attempt in range(1, 3):
        http_client = httpx.Client(timeout=httpx.Timeout(300.0, connect=30.0))
        client = OpenAI(
            api_key=settings.GPT55_API_KEY,
            base_url=settings.GPT55_BASE_URL,
            http_client=http_client,
        )
        try:
            # 流式调用：网关持续转发 token，避免推理时间长触发 Cloudflare 524/504 超时
            stream = client.chat.completions.create(
                model="gpt-6.1-sol",
                messages=messages,
                max_completion_tokens=8192,
                stream=True,
            )
            parts: List[str] = []
            for chunk in stream:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        parts.append(delta.content)
            content = "".join(parts)
            if not content.strip():
                raise RuntimeError("gpt-6.1-sol 流式返回空内容")
            return content
        except Exception as e:
            last_err = e
            msg = str(e)
            retryable = any(k in msg for k in ("504", "502", "Gateway", "timed out",
                                                "ConnectError", "ConnectionError",
                                                "RemoteProtocolError", "ReadTimeout"))
            if not retryable or attempt == 2:
                print(f"[DetailImage] gpt-6.1-sol 失败（{type(e).__name__}: {msg[:80]}），切换 MiMo-v2.5")
                return _call_mimo_fallback(messages)
            time.sleep(5)
    raise last_err  # type: ignore[misc]


def _call_mimo_fallback(messages: List[Dict[str, Any]]) -> str:
    """MiMo-v2.5 兜底"""
    headers = {
        "Authorization": f"Bearer {settings.MIMO_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"model": settings.TEXT_MODEL_MIMO, "messages": messages, "max_completion_tokens": 8192}
    with httpx.Client(timeout=300.0) as client:
        resp = client.post(
            f"{settings.MIMO_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"] or ""
        if not content.strip():
            raise RuntimeError("MiMo-v2.5 返回空 content")
        return content


def _parse_scene_prompts(raw: str, expected_count: int) -> List[Dict[str, str]]:
    """从 LLM 响应中解析场景提示词 JSON 数组

    返回 [{"title": str, "prompt": str}, ...]
    """
    # 尝试提取 ```json 代码块
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", raw, re.DOTALL)
    json_text = m.group(1).strip() if m else raw.strip()

    # 尝试直接找 JSON 数组
    arr_match = re.search(r"\[[\s\S]*\]", json_text)
    if arr_match:
        json_text = arr_match.group(0)

    try:
        scenes = json.loads(json_text)
        if isinstance(scenes, list):
            # 补齐/截断到期望数量
            if len(scenes) < expected_count:
                print(f"[DetailImage] 警告：期望 {expected_count} 个场景，实际解析 {len(scenes)} 个")
            return scenes[:max(expected_count, len(scenes))]
    except json.JSONDecodeError as e:
        print(f"[DetailImage] JSON 解析失败: {e}，尝试逐条提取")

    # 兜底：逐行提取 prompt 字段
    prompts = re.findall(r'"prompt"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
    titles = re.findall(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
    scenes = []
    for i, p in enumerate(prompts[:expected_count]):
        scenes.append({"title": titles[i] if i < len(titles) else f"场景{i+1}", "prompt": p})
    if not scenes:
        raise RuntimeError(f"无法从 LLM 响应中解析场景提示词: {raw[:200]}")
    return scenes


async def step1_generate_scene_prompts(
    product_image_paths: List[str],
    product_name: str,
    count: int,
    scene_style: str = "",
    aspect_ratio: str = "3:4 (Portrait Standard)",
) -> List[Dict[str, str]]:
    """Step1: 多模态 LLM 识别产品 → 生成 N 个场景提示词

    返回 [{"title": str, "prompt": str}, ...]
    """
    available = [p for p in product_image_paths if p and Path(p).exists()]
    if not available:
        raise FileNotFoundError(f"产品图均不存在: {product_image_paths}")

    messages = _build_llm_messages(available, product_name, count, scene_style, aspect_ratio)
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, _call_gpt55_sync, messages)
    scenes = _parse_scene_prompts(raw, count)

    # 保存原始响应
    return scenes


# ═══════════════════════════════════════
# Step 2: 并发调用 ideogram4 生成 N 张场景图
# ═══════════════════════════════════════

def _make_ideogram4_client() -> RunningHubClient:
    """创建 ideogram4 专用 RunningHub 客户端（国际站 + 独立 key）"""
    client = RunningHubClient(api_key=settings.RUNNINGHUB_IDEOGRAM4_API_KEY)
    client.base_url = settings.RUNNINGHUB_IDEOGRAM4_BASE_URL
    return client


def _normalize_aspect_ratio(ratio: str) -> str:
    """把用户简写（如 '3:4'）归一化为 AI App 期望的完整格式（如 '3:4 (Portrait Standard)'）"""
    if not ratio:
        return settings.DETAIL_IMAGE_DEFAULT_ASPECT_RATIO
    # 合法的 aspect_ratio 选项（和 config.py 的 fieldData options 一致）
    valid = [
        "1:1 (Square)", "2:3 (Portrait Photo)", "3:2 (Photo)",
        "3:4 (Portrait Standard)", "4:3 (Standard)",
        "9:16 (Portrait Widescreen)", "16:9 (Widescreen)", "21:9 (Ultrawide)",
    ]
    # 已是完整格式
    if ratio in valid:
        return ratio
    # 简写匹配：取括号前的比例部分
    for v in valid:
        if v.split(" ")[0] == ratio:
            return v
    # 无法匹配，用默认值
    return settings.DETAIL_IMAGE_DEFAULT_ASPECT_RATIO


def _build_node_info_list(prompt: str, aspect_ratio: str) -> List[Dict[str, Any]]:
    """构造 ideogram4 AI App 的 nodeInfoList

    节点（来自 API 文档）：
      - 205: prompt (fieldName="value")
      - 37:  aspect_ratio (fieldName="aspect_ratio", COMBO field with fieldData)
      - 37:  megapixels (fieldName="megapixels")
    """
    return [
        {
            "nodeId": settings.RUNNINGHUB_IDEOGRAM4_PROMPT_NODE,
            "fieldName": "value",
            "fieldValue": prompt,
            "description": "Simple prompt",
        },
        {
            "nodeId": settings.RUNNINGHUB_IDEOGRAM4_RATIO_NODE,
            "fieldName": "aspect_ratio",
            "fieldData": settings.RUNNINGHUB_IDEOGRAM4_RATIO_FIELD_DATA,
            "fieldValue": aspect_ratio,
            "description": "Scale",
        },
        {
            "nodeId": settings.RUNNINGHUB_IDEOGRAM4_RATIO_NODE,
            "fieldName": "megapixels",
            "fieldValue": settings.RUNNINGHUB_IDEOGRAM4_MEGAPIXELS,
            "description": "2=200W pixels (1=100W pixels)",
        },
    ]


async def _submit_one_scene(
    client: RunningHubClient,
    prompt: str,
    aspect_ratio: str,
    scene_index: int,
) -> Dict[str, Any]:
    """提交单个场景的 ideogram4 生成任务"""
    node_info_list = _build_node_info_list(prompt, aspect_ratio)
    result = await client.submit_task(
        workflow_id=settings.RUNNINGHUB_IDEOGRAM4_WORKFLOW_ID,
        node_info_list=node_info_list,
        is_ai_app=settings.RUNNINGHUB_IDEOGRAM4_IS_AI_APP,
        instance_type=settings.RUNNINGHUB_IDEOGRAM4_INSTANCE_TYPE,
        spend_kind="image",   # ideogram4 出的是图，不是视频（2026-10-09：只有"生成视频"被禁）
    )
    task_id = result["taskId"]
    print(f"  [ideogram4] 场景{scene_index+1} 已提交 taskId={task_id}", flush=True)
    return {"task_id": task_id, "scene_index": scene_index, "status": "submitted"}


async def _poll_one_scene(
    client: RunningHubClient,
    rh_task_id: str,
    scene_index: int,
    max_wait: int = 900,
) -> Dict[str, Any]:
    """轮询单个 ideogram4 任务直到完成"""
    start = time.time()
    empty_success_retries = 0
    while time.time() - start < max_wait:
        status_data = await client.query_task(rh_task_id)
        task_status = status_data.get("taskStatus", "UNKNOWN")

        if task_status == "SUCCESS":
            outputs = status_data.get("results", []) or []
            image_url = ""
            for item in outputs:
                if item.get("url"):
                    image_url = item["url"]
                    break
            # AI App 偶发 SUCCESS 但 results 为空，用旧版 outputs 接口兜底
            if not image_url:
                print(f"  [ideogram4] 场景{scene_index+1} SUCCESS 但 results 为空，尝试旧版 outputs 接口...", flush=True)
                image_url = await _fetch_output_legacy(rh_task_id)
            if image_url:
                print(f"  [ideogram4] 场景{scene_index+1} SUCCESS url={image_url[:60]}...", flush=True)
                return {"status": "SUCCESS", "image_url": image_url, "scene_index": scene_index}
            # 仍为空：等待后重试（最多 3 次，避免长时间空等）
            empty_success_retries += 1
            if empty_success_retries <= 3 and time.time() - start < max_wait:
                print(f"  [ideogram4] 场景{scene_index+1} SUCCESS 但输出未就绪，10s 后重试 ({empty_success_retries}/3)...", flush=True)
                await asyncio.sleep(10)
                continue
            return {"status": "FAILED", "error": "任务 SUCCESS 但无图片输出（aspect_ratio 参数可能不合法）", "scene_index": scene_index}

        if task_status == "FAILED":
            err = status_data.get("msg", "任务执行失败")
            print(f"  [ideogram4] 场景{scene_index+1} FAILED: {err}", flush=True)
            return {"status": "FAILED", "error": err, "scene_index": scene_index}

        await asyncio.sleep(5)

    return {"status": "TIMEOUT", "error": "任务超时", "scene_index": scene_index}


async def _fetch_output_legacy(task_id: str) -> str:
    """用旧版 /task/openapi/outputs 接口获取图片 URL（AI App 兜底）"""
    api_key = settings.RUNNINGHUB_IDEOGRAM4_API_KEY
    base_url = settings.RUNNINGHUB_IDEOGRAM4_BASE_URL
    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(
                f"{base_url}/task/openapi/outputs",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"apiKey": api_key, "taskId": task_id},
            )
            if r.status_code != 200:
                return ""
            data = r.json()
            items = data.get("data", []) or []
            for item in items:
                url = item.get("fileUrl", "")
                if url:
                    return url
    except Exception as e:
        print(f"  [ideogram4] legacy outputs 查询失败: {e}", flush=True)
    return ""


async def _download_image(url: str, save_path: Path) -> str:
    """下载图片到本地"""
    async with httpx.AsyncClient(timeout=120.0) as c:
        resp = await c.get(url)
        resp.raise_for_status()
        save_path.write_bytes(resp.content)
    return str(save_path)


# ═══════════════════════════════════════
# Step 2 (image2 路径)：本地 Image2Client 同步图生图
# ═══════════════════════════════════════

# 复用全局 image2 客户端
_image2_client: Optional[Image2Client] = None


def _get_image2_client() -> Image2Client:
    global _image2_client
    if _image2_client is None:
        _image2_client = Image2Client()
    return _image2_client


def _aspect_ratio_to_size(ratio: str) -> str:
    """把 aspect_ratio 形如 '3:4 (Portrait Standard)' 映射为 image2 的 size 形如 '768x1024'"""
    head = ratio.split(" ")[0] if ratio else "3:4"
    try:
        w, h = head.split(":")
        w, h = int(w), int(h)
    except Exception:
        w, h = 3, 4
    # 基准 1024 长边
    if w >= h:
        W, H = 1024, int(1024 * h / w)
    else:
        H, W = 1024, int(1024 * w / h)
    # image2 接受的尺寸需是 64 的倍数（gpt-image-2 实际放宽，但保 64 倍数最稳）
    W = max(512, (W // 64) * 64)
    H = max(512, (H // 64) * 64)
    return f"{W}x{H}"


async def _image2_generate_one(
    client: Image2Client,
    prompt: str,
    product_image_path: str,
    size: str,
    scene_index: int,
) -> Dict[str, Any]:
    """用 image2 单张产品图 + prompt 同步图生图，返回图片 url 或 b64"""
    result = await client.image_edit_sync(
        prompt=prompt,
        images=[Path(product_image_path)],
        size=size,
        output_format="png",
    )
    img = client._get_result_image(result)
    print(f"  [image2] 场景{scene_index+1} 已生成", flush=True)
    return {"image_data": img, "scene_index": scene_index, "status": "SUCCESS"}


async def _image2_save_one(
    client: Image2Client,
    image_data: str,
    save_path: Path,
) -> str:
    """保存 image2 返回的 url/b64 到磁盘，返回本地路径"""
    return await client._save_image_to_disk(image_data, save_path)


# ── Step4: 分析生成图片 → 推理卖点 → 详情页文案 ──

_DETAIL_ANALYSIS_PROMPT = """你是一位资深电商视觉设计师+营销文案专家。擅长分析产品场景图，推理产品卖点，撰写有感染力的电商详情页文案。

请分析这张电商详情页场景图，完成以下任务：

1. 画面描述：简要描述图片内容（产品、场景、光影、构图、氛围），2-3句话。
2. 卖点推理：从画面推断该产品的 5 个核心卖点（结合场景和产品特征），每个卖点一句话。
3. 详情页文案：撰写 1 段电商详情页介绍文案（含主标题、副标题、2-3 个卖点段落），有感染力、突出产品价值。

用中文输出，使用 Markdown 格式。"""


async def _analyze_scene_image(
    image_path: str,
    product_name: str,
    scene_title: str,
) -> str:
    """用 gpt-6.1-sol 多模态分析生成的场景图 → 推理卖点 → 生成详情页文案"""
    p = Path(image_path)
    if not p.exists():
        return ""

    img_b64 = base64.b64encode(p.read_bytes()).decode()
    ext = p.suffix.lower().lstrip(".") or "png"
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "webp": "image/webp"}.get(ext, "image/png")
    data_uri = f"data:{mime};base64,{img_b64}"

    messages = [
        {"role": "system", "content": "你是一位资深电商视觉设计师+营销文案专家。"},
        {"role": "user", "content": [
            {
                "type": "text",
                "text": f"产品名称：{product_name}\n场景主题：{scene_title}\n\n{_DETAIL_ANALYSIS_PROMPT}",
            },
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]},
    ]

    loop = asyncio.get_event_loop()
    desc = await loop.run_in_executor(None, _call_gpt55_sync, messages)
    return desc or ""


# ═══════════════════════════════════════
# 主流程
# ═══════════════════════════════════════

async def run_pipeline(
    product_image_paths: List[str],
    product_name: str,
    count: int = 4,
    aspect_ratio: str = "",
    scene_style: str = "",
    engine: str = "image2",
) -> str:
    """启动详情图生成全流程，返回 task_id

    流程：
    1. LLM 识别产品图 + 产品名 → 生成 N 个场景提示词
    2. 并发调用生图引擎（image2 / ideogram4）生成 N 张场景图
    3. 下载结果图片
    """
    # 参数归一化
    norm_paths: List[str] = []
    seen = set()
    for p in product_image_paths or []:
        if p and p not in seen:
            norm_paths.append(p)
            seen.add(p)
    if not norm_paths:
        raise ValueError("至少需要 1 张产品参考图")

    count = max(1, min(count, settings.DETAIL_IMAGE_MAX_COUNT))
    aspect_ratio = _normalize_aspect_ratio(aspect_ratio or settings.DETAIL_IMAGE_DEFAULT_ASPECT_RATIO)
    # image2 用第一张产品图作 i2i 参考
    product_image_for_i2i = norm_paths[0]

    task_id = f"di_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    _tasks[task_id] = {
        "task_id": task_id,
        "status": "running",
        "step": "初始化",
        "progress": 0,
        "engine": engine,
        "params": {
            "product_name": product_name,
            "count": count,
            "aspect_ratio": aspect_ratio,
            "scene_style": scene_style,
            "product_image_paths": norm_paths,
            "product_image_for_i2i": product_image_for_i2i,
            "engine": engine,
        },
        "outputs": {
            "scenes": None,        # LLM 生成的场景提示词
            "images": [],           # 下载后的图片本地路径列表
        },
        "scene_tasks": [],          # 每个场景的 RH 任务状态
        "logs": [],
        "error": None,
        "created_at": int(time.time()),
    }
    asyncio.create_task(_run_pipeline_async(task_id))
    _persist_task(task_id)
    return task_id


async def _run_pipeline_async(task_id: str):
    """异步执行详情图生成全流程"""
    task = _tasks[task_id]
    params = task["params"]
    run_dir = OUTPUT_DIR / task_id
    run_dir.mkdir(parents=True, exist_ok=True)

    def _log(msg: str):
        task["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    try:
        # ── Step1: LLM 生成场景提示词 ──
        task["step"] = f"Step1/4：LLM 分析产品并生成 {params['count']} 个场景提示词..."
        task["progress"] = 10
        _log(f"产品: {params['product_name']} | 数量: {params['count']} | 比例: {params['aspect_ratio']}")
        _log("调用 gpt-6.1-sol 多模态分析产品图...")
        _persist_task(task_id)

        scenes = await step1_generate_scene_prompts(
            product_image_paths=params["product_image_paths"],
            product_name=params["product_name"],
            count=params["count"],
            scene_style=params["scene_style"],
            aspect_ratio=params["aspect_ratio"],
        )
        task["outputs"]["scenes"] = scenes
        _log(f"✓ 已生成 {len(scenes)} 个场景提示词")

        # 保存场景提示词
        (run_dir / "scenes.json").write_text(
            json.dumps(scenes, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _persist_task(task_id)

        engine = params.get("engine", "image2")
        if engine == "ideogram4":
            # ── Step2/3 (ideogram4)：并发提交 + 轮询 + 下载 ──
            task["step"] = f"Step2/4：并发提交 {len(scenes)} 个 ideogram4 生成任务..."
            task["progress"] = 30
            _log(f"并发提交 {len(scenes)} 个 ideogram4 任务...")
            _persist_task(task_id)

            client = _make_ideogram4_client()

            submit_tasks = [
                _submit_one_scene(client, scene["prompt"], params["aspect_ratio"], i)
                for i, scene in enumerate(scenes)
            ]
            submit_results = await asyncio.gather(*submit_tasks, return_exceptions=True)

            scene_tasks = []
            for i, result in enumerate(submit_results):
                if isinstance(result, Exception):
                    _log(f"✗ 场景{i+1} 提交失败: {result}")
                    scene_tasks.append({
                        "scene_index": i,
                        "title": scenes[i].get("title", f"场景{i+1}") if i < len(scenes) else f"场景{i+1}",
                        "prompt": scenes[i].get("prompt", "") if i < len(scenes) else "",
                        "rh_task_id": "",
                        "status": "FAILED",
                        "error": str(result),
                        "image_url": "",
                        "image_path": "",
                    })
                else:
                    scene_tasks.append({
                        "scene_index": i,
                        "title": scenes[i].get("title", f"场景{i+1}") if i < len(scenes) else f"场景{i+1}",
                        "prompt": scenes[i].get("prompt", "") if i < len(scenes) else "",
                        "rh_task_id": result["task_id"],
                        "status": "submitted",
                        "error": "",
                        "image_url": "",
                        "image_path": "",
                    })
                    _log(f"✓ 场景{i+1} 已提交 taskId={result['task_id']}")

            task["scene_tasks"] = scene_tasks
            _persist_task(task_id)

            task["step"] = f"Step3/4：轮询 {len(scene_tasks)} 个任务并下载结果..."
            task["progress"] = 50
            _log("等待 ideogram4 生成完成...")
            _persist_task(task_id)

            poll_tasks = [
                _poll_one_scene(client, st["rh_task_id"], st["scene_index"])
                for st in scene_tasks
                if st["rh_task_id"]
            ]
            poll_results = await asyncio.gather(*poll_tasks, return_exceptions=True)

            images = []
            for result in poll_results:
                if isinstance(result, Exception):
                    _log(f"✗ 轮询异常: {result}")
                    continue
                idx = result["scene_index"]
                st = scene_tasks[idx]
                st["status"] = result["status"]
                if result["status"] == "SUCCESS" and result.get("image_url"):
                    st["image_url"] = result["image_url"]
                    ext = ".png"
                    save_path = run_dir / f"scene_{idx+1}{ext}"
                    try:
                        local_path = await _download_image(result["image_url"], save_path)
                        st["image_path"] = local_path
                        st["status"] = "downloaded"
                        images.append({
                            "scene_index": idx,
                            "scene": scenes[idx].get("scene", "") if idx < len(scenes) else "",
                            "title": st["title"],
                            "subtitle": scenes[idx].get("subtitle", "") if idx < len(scenes) else "",
                            "image_path": local_path,
                            "image_url": f"/outputs/detail_image/{task_id}/scene_{idx+1}{ext}",
                        })
                        _log(f"✓ 场景{idx+1} 已下载")
                    except Exception as e:
                        st["error"] = f"下载失败: {e}"
                        _log(f"✗ 场景{idx+1} 下载失败: {e}")
                elif result["status"] != "SUCCESS":
                    st["error"] = result.get("error", "")
                    _log(f"✗ 场景{idx+1} {result['status']}: {st['error']}")

            task["scene_tasks"] = scene_tasks
            _persist_task(task_id)
        else:
            # ── Step2/3 (image2)：并发 i2i 同步生图 + 直接落盘 ──
            task["step"] = f"Step2/4：并发调用 image2 图生图 {len(scenes)} 张..."
            task["progress"] = 30
            _log(f"并发调用 image2 图生图（产品图作参考）...")
            _persist_task(task_id)

            i2_client = _get_image2_client()
            size = _aspect_ratio_to_size(params["aspect_ratio"])
            _log(f"image2 size={size} (源比例 {params['aspect_ratio']})")
            product_ref = params["product_image_for_i2i"]

            gen_tasks = [
                _image2_generate_one(i2_client, scene["prompt"], product_ref, size, i)
                for i, scene in enumerate(scenes)
            ]
            gen_results = await asyncio.gather(*gen_tasks, return_exceptions=True)

            images = []
            scene_tasks = []
            for i, result in enumerate(gen_results):
                st_entry = {
                    "scene_index": i,
                    "title": scenes[i].get("title", f"场景{i+1}") if i < len(scenes) else f"场景{i+1}",
                    "prompt": scenes[i].get("prompt", "") if i < len(scenes) else "",
                    "rh_task_id": "",
                    "status": "SUCCESS",
                    "error": "",
                    "image_url": "",
                    "image_path": "",
                }
                if isinstance(result, Exception):
                    st_entry["status"] = "FAILED"
                    st_entry["error"] = str(result)
                    _log(f"✗ 场景{i+1} image2 失败: {result}")
                elif isinstance(result, dict) and result.get("image_data"):
                    save_path = run_dir / f"scene_{i+1}.png"
                    try:
                        local_path = await _image2_save_one(
                            i2_client, result["image_data"], save_path
                        )
                        st_entry["image_path"] = local_path
                        st_entry["status"] = "downloaded"
                        images.append({
                            "scene_index": i,
                            "scene": scenes[i].get("scene", "") if i < len(scenes) else "",
                            "title": st_entry["title"],
                            "subtitle": scenes[i].get("subtitle", "") if i < len(scenes) else "",
                            "image_path": local_path,
                            "image_url": f"/outputs/detail_image/{task_id}/scene_{i+1}.png",
                        })
                        _log(f"✓ 场景{i+1} image2 已生成")
                    except Exception as e:
                        st_entry["status"] = "FAILED"
                        st_entry["error"] = f"保存失败: {e}"
                        _log(f"✗ 场景{i+1} 保存失败: {e}")
                else:
                    st_entry["status"] = "FAILED"
                    st_entry["error"] = "image2 返回空"
                    _log(f"✗ 场景{i+1} image2 返回空")
                scene_tasks.append(st_entry)

            task["scene_tasks"] = scene_tasks
            _persist_task(task_id)

        task["outputs"]["images"] = images
        _persist_task(task_id)

        # ── Step4: 分析生成图片 → 推理卖点 → 详情页文案 ──
        if images:
            task["step"] = f"Step4/4：分析 {len(images)} 张图片并生成详情页文案..."
            task["progress"] = 80
            _log(f"并发分析 {len(images)} 张图片，生成详情页文案...")
            _persist_task(task_id)

            analyze_tasks = [
                _analyze_scene_image(img["image_path"], params["product_name"], img["title"])
                for img in images
            ]
            analyze_results = await asyncio.gather(*analyze_tasks, return_exceptions=True)

            for i, result in enumerate(analyze_results):
                if isinstance(result, Exception):
                    _log(f"✗ 图片{i+1} 文案生成失败: {result}")
                    images[i]["description"] = ""
                else:
                    images[i]["description"] = result
                    _log(f"✓ 图片{i+1} 文案已生成")

            task["outputs"]["images"] = images
            _persist_task(task_id)

        task["progress"] = 100

        if images:
            task["status"] = "completed"
            task["step"] = f"完成：成功生成 {len(images)}/{len(scenes)} 张详情图"
            _log(f"✓ 完成：{len(images)}/{len(scenes)} 张详情图生成成功")
        else:
            task["status"] = "failed"
            task["step"] = "失败：所有场景图生成均失败"
            task["error"] = "所有场景图生成均失败"
            _log("✗ 所有场景图生成均失败")

        _persist_task(task_id)

    except Exception as e:
        task["status"] = "failed"
        task["step"] = f"失败：{e}"
        task["error"] = str(e)
        _log(f"✗ 异常: {e}")
        _persist_task(task_id)


# ═══════════════════════════════════════
# 对外查询接口
# ═══════════════════════════════════════

def get_task_status(task_id: str) -> Dict[str, Any]:
    """查询任务状态"""
    if task_id not in _tasks:
        return {"status": "not_found", "task_id": task_id}
    return _tasks[task_id]


def list_history(limit: int = 20) -> List[Dict[str, Any]]:
    """获取历史任务列表（按创建时间倒序）"""
    items = sorted(_tasks.values(), key=lambda x: x.get("created_at", 0), reverse=True)
    return [
        {
            "task_id": t["task_id"],
            "status": t["status"],
            "step": t["step"],
            "product_name": t.get("params", {}).get("product_name", ""),
            "count": t.get("params", {}).get("count", 0),
            "aspect_ratio": t.get("params", {}).get("aspect_ratio", ""),
            "image_count": len(t.get("outputs", {}).get("images", [])),
            "images": t.get("outputs", {}).get("images", []),
            "created_at": t.get("created_at"),
        }
        for t in items[:limit]
    ]
