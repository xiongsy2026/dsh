"""
1人漫剧 H3 - manxueapi 网关客户端

基于 OpenAI 兼容协议接入 manxueapi.com 网关，调用 MiniMax H3 视频生成模型。

API 规格（实测）:
  - 网关: https://manxueapi.com
  - 提交端点: POST /v1/video/generations
  - 查询端点: GET /v1/videos/{task_id}
  - 鉴权: Bearer {api_key}
  - 模型: 1ren-minimax-h3
  - 异步返回: task_id（status=queued）

参数支持（实测）:
  - prompt (必填): 文本提示词
  - duration (可选): 5 / 10(默认) / 15 秒
  - ratio (可选): 9:16(默认) / 16:9 / 1:1 / 4:3 / 3:4
  - images (可选): List[公网URL]，参考图（实测 2026-08-11 让模型真正消费图片）
  - reference_videos (可选): List[data URI 或 URL]（视频参考，ref2va 模式）

图片传参（2026-08-11 实测探测）:
  - ✅ images + 公网URL → 模型真正消费图片（视频内容与参考图一致）
  - ✅ reference_images + 公网URL → 同样有效
  - ❌ first_frame_image + data URI → 网关接受但模型不消费（视频内容随机）
  - ❌ first_frame_image + 公网URL → 网关接受但模型不消费（视频内容随机）
  - ❌ content 数组 + image_url → 网关接受但模型不消费（视频内容随机）
  - 结论：必须用 images + 公网URL
  - 本地图通过 imgbb 上传转公网URL（见 _upload_image_to_imgbb）

输出规格（实测）:
  - 分辨率: 736×1280 (9:16) / 1280×736 (16:9)，固定 768p
  - 时长: 与 duration 一致（5→5.17s，10→10.13s，15→15.08s）
  - 音频: 原生立体声（H3 自带）
  - 格式: MP4

模式映射:
  - reference_videos + 图/文本 → Ref2VA（视频参考，优先级最高）
  - reference + 0 张图 → T2VA（纯文本）
  - reference + 1 张图 → I2VA（首帧）
  - reference + 2 张图 → FL2VA（首+尾）
  - first_last_frame → FL2VA（首+尾）
"""
import asyncio
import base64
import time
import json
import uuid
import httpx
from pathlib import Path
from typing import Optional, Callable, List

from config import settings


# ── 配置 ──
BASE_URL = getattr(settings, "MANXUE_H3_BASE_URL", "https://manxueapi.com").rstrip("/")
API_KEY = getattr(settings, "MANXUE_H3_API_KEY", "")
MODEL = getattr(settings, "MANXUE_H3_MODEL", "1ren-minimax-h3")

# imgbb 图床 API（manxue 网关 images 字段要求公网URL，不接受 data URI）
IMGBB_API_KEY = getattr(settings, "IMGBB_API_KEY", "")
IMGBB_API_URL = "https://api.imgbb.com/1/upload"

# 支持的 duration（实测仅 5/10/15 被接受）
SUPPORTED_DURATIONS = [5, 10, 15]
DEFAULT_DURATION = 10

# 支持的比例
SUPPORTED_RATIOS = ["9:16", "16:9", "1:1", "4:3", "3:4"]


# ─────────────────────────────────────
# 工具函数
# ─────────────────────────────────────

def _file_to_data_uri(file_path: str) -> str:
    """本地图片/视频转 data URI（保留兼容，仅 video 参考用）"""
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    ext = p.suffix.lower()
    mime_map = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
        ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    }
    mime = mime_map.get(ext, "application/octet-stream")

    with open(file_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


async def _upload_image_to_imgbb(file_path: str) -> str:
    """上传本地图片到 imgbb，失败时回退 catbox.moe，返回公网URL

    manxue 网关 images 字段要求公网URL，不接受 data URI。
    实测 2026-08：images + 公网URL 可让模型真正消费图片；
    first_frame_image + data URI/URL 均无法让模型消费。

    Args:
        file_path: 本地图片路径

    Returns:
        公网URL（https://i.ibb.co/.../xxx.png 或 https://files.catbox.moe/xxx.xxx）

    Raises:
        RuntimeError: 所有图床均失败
    """
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"图片不存在: {file_path}")

    # ---- 主路径：RunningHub CDN（最稳，公网 download_url） ----
    try:
        from backend.services.wan3_client import upload_media
        url = upload_media(str(p))
        if url and url.startswith("http"):
            print(f"[rh-cdn] 上传成功: {p.name} → {url}")
            return url
        print(f"[rh-cdn] 失败：返回 {url}，回退 imgbb")
    except Exception as e:
        print(f"[rh-cdn] 异常 {e}，回退 imgbb")

    # ---- 备用路径 1：imgbb ----
    if IMGBB_API_KEY:
        try:
            # 压缩大图：imgbb 限制 32MB，但太大会超时，压缩到长边 1024px
            with open(file_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")

            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    IMGBB_API_URL,
                    params={"key": IMGBB_API_KEY},
                    data={"image": b64, "name": p.stem},
                )
                if resp.status_code < 400:
                    data = resp.json()
                    if data.get("success"):
                        url = data["data"]["url"]
                        print(f"[imgbb] 上传成功: {p.name} → {url}")
                        return url
            # imgbb 失败，打印并继续走 catbox
            print(f"[imgbb] 失败 HTTP {resp.status_code}，回退 catbox")
        except Exception as e:
            print(f"[imgbb] 异常 {e}，回退 catbox")

    # ---- 备用路径 1：catbox.moe（免 API key，无封禁风险） ----
    CATBOX_API_URL = "https://catbox.moe/user/api.php"
    catbox_error = None
    try:
        # 压缩到长边 1024px JPEG，避免大文件上传超时
        compressed = _compress_image_to_bytes(file_path, max_side=1024, quality=88)
        ext = "jpg"
        filename = f"{p.stem}.{ext}"

        async with httpx.AsyncClient(timeout=120.0) as client:
            files = {
                "reqtype": (None, "fileupload"),
                "fileToUpload": (filename, compressed, f"image/{ext}"),
            }
            resp = await client.post(
                CATBOX_API_URL,
                files=files,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                },
            )
            if resp.status_code < 400 and resp.text.strip().startswith("http"):
                url = resp.text.strip()
                print(f"[catbox] 上传成功: {p.name} → {url}")
                return url
        catbox_error = f"catbox HTTP {resp.status_code} - {resp.text[:200]}"
        print(f"[catbox] 失败 {catbox_error}，回退 0x0.st")
    except Exception as e:
        catbox_error = str(e)
        print(f"[catbox] 异常 {e}，回退 0x0.st")

    # ---- 备用路径 2：0x0.st（极简 curl-based 文件托管） ----
    try:
        if 'compressed' not in dir():
            compressed = _compress_image_to_bytes(file_path, max_side=1024, quality=88)
        ext = "jpg"
        filename = f"{p.stem}.{ext}"

        async with httpx.AsyncClient(timeout=120.0) as client:
            files = {"file": (filename, compressed, f"image/{ext}")}
            resp = await client.post(
                "https://0x0.st",
                files=files,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                },
            )
            if resp.status_code < 400 and resp.text.strip().startswith("http"):
                url = resp.text.strip()
                print(f"[0x0.st] 上传成功: {p.name} → {url}")
                return url
        raise RuntimeError(
            f"0x0.st 上传失败: HTTP {resp.status_code} - {resp.text[:300]}"
        )
    except Exception as e:
        raise RuntimeError(
            f"所有图床上传均失败（imgbb + catbox + 0x0.st）：{e} | catbox last error: {catbox_error}"
        )


def _compress_image_to_bytes(
    file_path: str, max_side: int = 1024, quality: int = 88
) -> bytes:
    """把图片压缩到长边 max_side px 的 JPEG，返回 bytes。

    catbox 接受 PNG/JPG，但压缩后上传更稳。
    """
    from PIL import Image
    import io

    with Image.open(file_path) as im:
        im = im.convert("RGB")
        w, h = im.size
        if max(w, h) > max_side:
            scale = max_side / max(w, h)
            im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()


async def _upload_images_to_imgbb(file_paths: List[str]) -> List[str]:
    """批量上传图片到图床（imgbb 优先，失败回退 catbox），返回公网URL列表"""
    urls = []
    for fp in file_paths:
        url = await _upload_image_to_imgbb(fp)
        urls.append(url)
    return urls


async def _convert_triview_to_product_image(image_path: str) -> str:
    """三视图转产品实物主图（用 Image2 图生图）

    H3 模型不理解"三视图"概念，会把整张三视图当作一个整体画面，
    导致生成的视频产品外形变形（如多出耳朵、外形错乱）。

    本函数用 Image2 把三视图转成单视角产品实物主图，再传给 H3。

    Args:
        image_path: 三视图本地路径

    Returns:
        转换后的产品实物主图本地路径
    """
    # 延迟导入避免循环依赖
    from backend.services.image2_client import image2_client

    p = Path(image_path)
    if not p.exists():
        raise FileNotFoundError(f"三视图不存在: {image_path}")

    # 读取图片转 base64
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")

    prompt = (
        "请根据上传的产品三视图，生成一张产品实物主图。\n"
        "要求：\n"
        "1. 严格保留产品的真实结构、外形轮廓、比例、颜色、材质纹理、表面工艺、五金、装饰件\n"
        "2. 选择最能体现产品特征的单一视角（通常为正视图或3/4视角）\n"
        "3. 输出为纯白背景产品实物图，光线均匀，细节清晰\n"
        "4. 不要文字、不要水印、不要标注线、不要多视角拼接\n"
        "5. 产品居中，占画面主体，边缘留白适当\n"
        "6. 保持产品真实摄影质感，不要卡通化、不要插画化"
    )

    print(f"[img2] 三视图转实物图: {p.name}")
    result = await image2_client.image_edit_sync(
        prompt=prompt,
        images=[f"data:image/png;base64,{b64}"],
        size="1024x1024",
    )
    result_url = image2_client._get_result_image(result)

    # 下载结果到本地
    import uuid
    output_dir = Path(settings.OUTPUT_DIR) / "manxue_product_convert"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"product_{uuid.uuid4().hex[:8]}.png"

    if result_url.startswith("http"):
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(result_url)
            resp.raise_for_status()
            output_path.write_bytes(resp.content)
    elif result_url.startswith("data:"):
        _, b64_part = result_url.split(",", 1)
        output_path.write_bytes(base64.b64decode(b64_part))
    else:
        raise RuntimeError(f"无法识别的 Image2 返回格式: {result_url[:80]}")

    print(f"[img2] 实物图已保存: {output_path}")
    return str(output_path)


async def _smart_upload_image(file_path: str, force_triview: bool = False) -> str:
    """智能上传单张图片到 imgbb

    如果检测到是三视图（文件名提示 或 宽高比>1.2 或 force_triview），
    先用 Image2 转成产品实物主图再上传。

    Args:
        file_path: 图片本地路径
        force_triview: 强制按三视图处理（调用方已知是三视图时使用）

    Returns:
        公网URL
    """
    p = Path(file_path)
    name_hint = p.stem.lower()
    is_triview = force_triview or any(kw in name_hint for kw in ["triview", "三视图", "tri_view", "3view"])

    # 检测宽高比：三视图通常偏宽（宽 > 高 * 1.2）
    # 注：MiniMax 三视图可能是 2400x1792（1.34）等接近正方形的尺寸，
    # 单靠宽高比无法完全判定，主要依赖文件名和 force_triview
    if not is_triview:
        try:
            from PIL import Image
            with Image.open(file_path) as img:
                w, h = img.size
                if w > h * 1.2:
                    is_triview = True
        except Exception:
            pass

    target_path = file_path
    if is_triview:
        print(f"[smart] 检测到三视图，先转实物图: {p.name}")
        try:
            target_path = await _convert_triview_to_product_image(file_path)
        except Exception as e:
            print(f"[smart] 三视图转换失败，使用原图: {e}")

    return await _upload_image_to_imgbb(target_path)


def _detect_multi_view(image_path: str) -> bool:
    """检测图片是否为多视图拼接图（三视图、四视图等）

    判定规则（满足任一即判定为多视图）：
    1. 文件名包含 triview/三视图/tri_view/3view/multiview/多视图/4view 等关键词
    2. 宽高比 > 1.2（多视图通常是水平拼接的宽图）

    Args:
        image_path: 图片本地路径

    Returns:
        True 表示是多视图拼接图
    """
    p = Path(image_path)
    name_hint = p.stem.lower()
    if any(kw in name_hint for kw in [
        "triview", "三视图", "tri_view", "3view",
        "multiview", "多视图", "multi_view", "4view", "四视图",
    ]):
        return True

    try:
        from PIL import Image
        with Image.open(image_path) as img:
            w, h = img.size
            # 宽高比 > 1.2 视为多视图拼接
            if w > h * 1.2:
                return True
    except Exception:
        pass

    return False


def _split_multi_view(image_path: str, parts: int = 3) -> List[str]:
    """把多视图拼接图拆分成多张独立单图

    默认按水平方向三等分（最常见的三视图布局：侧视/俯视/正视）。

    Args:
        image_path: 多视图图片本地路径
        parts: 拆分数量（默认3）

    Returns:
        拆分后的单图本地路径列表
    """
    from PIL import Image

    p = Path(image_path)
    img = Image.open(image_path)
    w, h = img.size
    part_w = w // parts

    output_dir = Path(settings.OUTPUT_DIR) / "manxue_split"
    output_dir.mkdir(parents=True, exist_ok=True)

    out_paths: List[str] = []
    for i in range(parts):
        left = i * part_w
        right = (i + 1) * part_w if i < parts - 1 else w
        crop = img.crop((left, 0, right, h))
        out_path = output_dir / f"{p.stem}_part{i+1}_{uuid.uuid4().hex[:6]}.png"
        crop.save(out_path)
        out_paths.append(str(out_path))
        print(f"[split] 拆分 [{i+1}/{parts}]: {out_path} ({crop.size[0]}x{crop.size[1]})")

    return out_paths


async def preprocess_images(image_paths: List[str]) -> List[str]:
    """预处理图片列表：检测多视图并自动拆分

    对每张图片：
    1. 检测是否为多视图拼接图（三视图等）
    2. 如果是，拆分成多张独立单图
    3. 如果不是，保持原样

    最终返回所有单图路径列表（多视图被拆分后展开）。

    Args:
        image_paths: 原始图片路径列表

    Returns:
        预处理后的单图路径列表
    """
    result: List[str] = []
    for img_path in image_paths:
        if not img_path or not Path(img_path).exists():
            continue
        if _detect_multi_view(img_path):
            print(f"[preprocess] 检测到多视图，自动拆分: {Path(img_path).name}")
            try:
                parts = _split_multi_view(img_path, parts=3)
                result.extend(parts)
            except Exception as e:
                print(f"[preprocess] 拆分失败，使用原图: {e}")
                result.append(img_path)
        else:
            result.append(img_path)
    return result


def _prepare_reference_prompt(prompt: str, image_count: int, duration: int) -> str:
    """统一使用 H3 Ref2VA 全能参考结构，不按图片数量切换首帧或首尾帧。"""
    if image_count <= 0 or not prompt:
        return prompt
    picture_labels = ", ".join(f"<Picture {i}>" for i in range(1, image_count + 1))
    return (
        f"subject_definitions: The product shown across {picture_labels} is the same target product; "
        "use all pictures as full visual references throughout the target video.\n\n"
        "summary: Generate the described product demonstration while preserving the product identity "
        "throughout the entire video.\n\n"
        "retention_analysis: Retain the product appearance, color, material, structure, proportions, texture, "
        "decoration, and all visible details from all reference pictures. Do not add, remove, redesign, "
        "or treat any picture as a first-frame or last-frame constraint.\n\n"
        f"detailed_description: {prompt}\n\n"
        "overall_soundscape: Use the overall_soundscape specified in the detailed description.\n\n"
        "non_diegetic_music: Use the non_diegetic_music specified in the detailed description."
    )


def _normalize_duration(duration: int) -> int:
    """规整 duration 到支持的值"""
    if duration in SUPPORTED_DURATIONS:
        return duration
    # 取最接近的支持值
    closest = min(SUPPORTED_DURATIONS, key=lambda x: abs(x - duration))
    return closest


def _normalize_ratio(ratio: str) -> str:
    """规整 ratio 到支持的值"""
    if ratio in SUPPORTED_RATIOS:
        return ratio
    return "9:16"


# ─────────────────────────────────────
# API 调用
# ─────────────────────────────────────

async def submit_generation(
    prompt: str,
    duration: int = 10,
    ratio: str = "9:16",
    images: Optional[List[str]] = None,
    reference_videos: Optional[List[str]] = None,
) -> str:
    """提交视频生成任务，返回 task_id

    Args:
        prompt: 文本提示词（必填）
        duration: 时长秒数（5/10/15）
        ratio: 画面比例（9:16/16:9/1:1/4:3/3:4）
        images: 参考图公网URL列表（实测 images + 公网URL 让模型真正消费图片）
        reference_videos: 视频参考列表（data URI 或 URL），ref2va 模式
    """
    if not API_KEY:
        raise RuntimeError("未配置 MANXUE_H3_API_KEY")
    if not prompt:
        raise RuntimeError("prompt 必填")

    body = {
        "model": MODEL,
        "prompt": prompt,
        "duration": _normalize_duration(duration),
        "ratio": _normalize_ratio(ratio),
    }
    if images:
        body["images"] = images
    if reference_videos:
        body["reference_videos"] = reference_videos

    # ── 诊断日志 ──
    print(f"[manxue] 提交 body 字段: model={body.get('model')} duration={body.get('duration')} ratio={body.get('ratio')}")
    print(f"[manxue] prompt 长度={len(prompt)} 首80字: {prompt[:80]!r}")
    imgs = body.get("images")
    rv = body.get("reference_videos")
    print(f"[manxue] images: {'有 count=' + str(len(imgs)) + ' 首项=' + imgs[0][:80] + '...' if imgs else '无'}")
    print(f"[manxue] reference_videos: {'有 count=' + str(len(rv)) + ' 首项前缀=' + rv[0][:40] + '...' if rv else '无'}")
    print(f"[manxue] body 总键: {list(body.keys())}")

    url = f"{BASE_URL}/v1/video/generations"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, headers=headers, json=body)
        if resp.status_code >= 400:
            print(f"[manxue] 提交失败 HTTP {resp.status_code} body: {resp.text[:800]}")
            raise RuntimeError(f"提交失败: HTTP {resp.status_code} - {resp.text[:500]}")
        data = resp.json()
        print(f"[manxue] 提交响应: {json.dumps(data, ensure_ascii=False)[:500]}")
        task_id = data.get("task_id") or data.get("id")
        if not task_id:
            raise RuntimeError(f"提交响应缺少 task_id: {data}")
        return task_id


async def query_task(task_id: str) -> dict:
    """查询任务状态"""
    if not API_KEY:
        raise RuntimeError("未配置 MANXUE_H3_API_KEY")

    url = f"{BASE_URL}/v1/videos/{task_id}"
    headers = {"Authorization": f"Bearer {API_KEY}"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(f"查询失败: HTTP {resp.status_code} - {resp.text[:500]}")
        return resp.json()


def _extract_video_url(status_data: dict) -> str:
    """从查询结果提取视频 URL"""
    # 多个字段都可能携带
    if status_data.get("metadata") and isinstance(status_data["metadata"], dict):
        url = status_data["metadata"].get("url")
        if url:
            return url
    for key in ("video_url", "url"):
        v = status_data.get(key)
        if v and isinstance(v, str):
            return v
    video = status_data.get("video")
    if isinstance(video, dict) and video.get("url"):
        return video["url"]
    output = status_data.get("output")
    if isinstance(output, dict) and output.get("url"):
        return output["url"]
    return ""


async def download_file(url: str, output_path: str):
    """下载视频到本地"""
    async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(resp.content)


# ─────────────────────────────────────
# 轮询
# ─────────────────────────────────────

async def _poll_until_done(
    task_id: str,
    output_path: str,
    max_wait: int,
    progress_callback: Optional[Callable] = None,
) -> str:
    """轮询任务直到完成，下载视频到 output_path"""
    def _notify(status, progress=0, msg=""):
        if progress_callback:
            try:
                progress_callback(status, progress, msg)
            except Exception:
                pass

    start = time.time()
    consecutive_err = 0
    last_status = ""
    last_progress = -1

    while time.time() - start < max_wait:
        await asyncio.sleep(10)
        try:
            data = await query_task(task_id)
        except Exception as e:
            consecutive_err += 1
            if consecutive_err >= 10:
                raise RuntimeError(f"查询连续异常({consecutive_err}次): {e}")
            await asyncio.sleep(min(60, 10 * consecutive_err))
            continue

        consecutive_err = 0
        status = data.get("status", "")
        progress = data.get("progress", 0) or 0

        if status != last_status or progress != last_progress:
            _notify("running", progress, f"状态: {status} 进度: {progress}%")
            last_status = status
            last_progress = progress

        if status == "failed":
            err = data.get("error") or json.dumps(data, ensure_ascii=False)
            _notify("failed", 0, f"任务失败: {err}")
            raise RuntimeError(f"manxue H3 任务失败: {err}")

        if status in ("completed", "succeeded"):
            _notify("downloading", 100, "任务完成，下载视频...")
            video_url = _extract_video_url(data)
            if not video_url:
                raise RuntimeError(f"任务成功但无视频输出: {json.dumps(data, ensure_ascii=False)[:500]}")
            await download_file(video_url, output_path)
            _notify("done", 100, f"视频已保存: {output_path}")
            return output_path

    _notify("timeout", 0, f"任务超时({max_wait}秒)")
    raise RuntimeError(f"manxue H3 任务超时({max_wait}秒)")


# ─────────────────────────────────────
# 统一入口（与 h3_director_client.generate_video 接口对齐）
# ─────────────────────────────────────

async def generate_video(
    mode: str,
    output_path: str,
    reference_images: Optional[List[str]] = None,
    first_frame_path: Optional[str] = None,
    last_frame_path: Optional[str] = None,
    prompt: str = "",
    duration: int = 10,
    aspect_ratio: str = "9:16",
    megapixels: float = 0.7,
    max_wait: int = 1800,
    progress_callback: Optional[Callable] = None,
    reference_videos: Optional[List[str]] = None,
    multi_view_mode: str = "split",
) -> str:
    """统一生成入口（与 RunningHub 客户端接口对齐）

    根据 mode + 图片/视频参数自动选择 H3 模式：
      - reference_videos + 图/文本 → Ref2VA（视频参考，优先级最高）
  - reference 模式有图 → Ref2VA（全能参考）
  - reference 模式无图 → T2VA（纯文本）
  - first_last_frame 模式 → FL2VA（首+尾）

    Args:
        mode: "reference" | "first_last_frame"（manxue 渠道下统一处理）
        output_path: 输出视频路径
        reference_images: 全能参考模式的商品图列表，全部作为 Ref2VA 参考图
        first_frame_path: 首帧图片路径
        last_frame_path: 尾帧图片路径
        prompt: 提示词
        duration: 时长（5/10/15，其他值自动规整）
        aspect_ratio: 比例
        megapixels: 忽略（manxue 固定 768p）
        max_wait: 最大等待秒数
        multi_view_mode: 多视图处理模式
          - "split"（默认）: 检测三视图/多视图自动拆分成多张单图，多视角一起传
          - "convert": 多视图用 Image2 转成单张产品实物主图
          - "off": 不处理，原样上传
        progress_callback: 回调(status, progress, msg)
        reference_videos: 视频参考路径列表（ref2va 模式，2026-08 实测网关接受）
    """
    def _notify(status, progress=0, msg=""):
        if progress_callback:
            try:
                progress_callback(status, progress, msg)
            except Exception:
                pass

    if not prompt:
        raise RuntimeError("prompt 必填")

    # 规整参数
    duration_n = _normalize_duration(duration)
    ratio_n = _normalize_ratio(aspect_ratio)

    # 收集所有参考图（first_last_frame 模式和 reference 模式合并处理）
    # manxue 网关 images 字段实测 2026-08：images + 公网URL 让模型真正消费图片
    all_image_paths: List[str] = []
    if mode == "first_last_frame":
        if first_frame_path and Path(first_frame_path).exists():
            all_image_paths.append(first_frame_path)
        if last_frame_path and Path(last_frame_path).exists():
            all_image_paths.append(last_frame_path)
    else:  # reference
        imgs = [p for p in (reference_images or []) if p and Path(p).exists()]
        all_image_paths.extend(imgs)

    # 解析视频参考（保持 data URI，reference_videos 字段接受 data URI）
    ref_vids = [p for p in (reference_videos or []) if p and Path(p).exists()]

    # 模式判定（用于日志展示）
    if ref_vids:
        mode_label = "Ref2VA 视频+图（混合参考）" if all_image_paths else "Ref2VA 视频"
    elif all_image_paths:
        mode_label = "Ref2VA 全能参考"
    else:
        mode_label = "T2VA（纯文本）"

    # 多视图预处理：检测多视图拼接图并自动拆分
    # - multi_view_mode="split"（默认）: 多视图拆成多张单图，多视角一起传
    # - multi_view_mode="convert": 多视图用 Image2 转成单张实物图
    # - multi_view_mode="off": 不处理，原样上传
    mv_mode = (multi_view_mode or "split").lower()
    if mv_mode == "split" and all_image_paths:
        _notify("preprocessing", 8, "检测多视图并拆分...")
        try:
            before_count = len(all_image_paths)
            all_image_paths = await preprocess_images(all_image_paths)
            if len(all_image_paths) > before_count:
                _notify("preprocessing", 12, f"多视图已拆分: {before_count} → {len(all_image_paths)} 张")
        except Exception as e:
            print(f"[manxue] 多视图拆分失败: {e}")

    # 上传图片到 imgbb 拿公网URL
    # - mv_mode="convert": 三视图用 Image2 转实物图
    # - 其他: 直接上传（split 模式下已经是单图）
    image_urls: List[str] = []
    total_upload = len(all_image_paths) + len(ref_vids)
    upload_count = 0

    for img_path in all_image_paths:
        upload_count += 1
        pct = int(10 + (upload_count / max(total_upload, 1)) * 30)
        _notify("uploading", pct, f"处理图片: {Path(img_path).name}")
        try:
            if mv_mode == "convert":
                url = await _smart_upload_image(img_path)
            else:
                url = await _upload_image_to_imgbb(img_path)
            image_urls.append(url)
        except Exception as e:
            print(f"[manxue] 图片上传失败 {img_path}: {e}")
            raise

    # 视频参考保持 data URI（reference_videos 字段接受 data URI）
    ref_vid_data: List[str] = []
    for vid in ref_vids:
        upload_count += 1
        pct = int(10 + (upload_count / max(total_upload, 1)) * 30)
        _notify("uploading", pct, f"编码参考视频: {Path(vid).name}")
        ref_vid_data.append(_file_to_data_uri(vid))

    # 提交
    _notify("submitting", 50, f"提交 manxue H3 任务 [{mode_label}]...")
    prompt = _prepare_reference_prompt(prompt, len(image_urls), duration_n)
    task_id = await submit_generation(
        prompt=prompt,
        duration=duration_n,
        ratio=ratio_n,
        images=image_urls if image_urls else None,
        reference_videos=ref_vid_data if ref_vid_data else None,
    )
    _notify("submitted", 60, f"任务已提交: {task_id} (duration={duration_n}s, ratio={ratio_n})")

    # 轮询
    return await _poll_until_done(task_id, output_path, max_wait, progress_callback)
