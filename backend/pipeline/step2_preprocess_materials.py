"""
Step2: 素材预处理（自动提取产品/服装/模特/场景的标准化参考图）
输入: 用户上传的原始素材图（产品/服装/模特/场景，均可选）
输出: 标准化白底参考图，供 Step3 产品替换使用

处理链路：
2.1 产品图 → 三视图白底
2.2 服装图 → 服装平铺图白底
2.3 模特图 → 人物白底正面照
2.4 场景图 → 无人物场景图
"""
import asyncio
import base64
import io
from pathlib import Path
from typing import Optional

import httpx
from PIL import Image
from backend.services.image2_client import Image2Client


def _compress_image(image_path: str, max_size: int = 1024, quality: int = 85) -> bytes:
    """压缩图片：缩放到 max_size 以内，转为 JPEG"""
    img = Image.open(image_path)
    # RGBA → RGB（JPEG 不支持透明通道）
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGB")
    # 缩放
    w, h = img.size
    if max(w, h) > max_size:
        ratio = max_size / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()

# ── 固定提示词（允许通过 prompts.json 覆盖） ──

PROMPT_PRODUCT = """请根据我上传的产品图片，提取产品本体，生成标准化产品参考图。严格保留产品真实结构、外形轮廓、比例、颜色、材质纹理、表面工艺、五金、装饰件、连接结构、边缘细节、开口结构、正面特征、侧面厚度和背面细节，不要改款，不要重新设计，不要增加原图中不存在的元素。

删除人物、手部、服装、桌面、背景、杂物、包装、阴影干扰和所有无关环境元素，只保留产品本身。

输出为纯白背景产品图。若产品是立体物品，如包袋、鞋子、盒子、瓶子、杯子、小家电、饰品等，请生成正视图、侧视图、背视图三视图，并排展示。若产品本身适合平铺展示，如毛巾、围巾、布料、地垫、床品、纸张、贴纸等，请生成正面平铺俯拍图，保持产品完整展开，边缘清晰，图案不变形。

画面要求：白底干净、光线均匀、细节清晰、无多余道具、无文字、无水印、无标签，分镜图不要出现字幕

产品一致性与尺寸铁律：上传图是唯一产品真值。原图可见的包装长宽比、包装厚度、食品形状、切面、表面凹凸纹路、条纹、颗粒、颜色层次、Logo、印刷图案和文字位置必须原样保留，禁止磨平纹路、简化细节、重新设计包装或生成无纹理替代品。产品主体必须完整展示且等比缩放，不拉伸、不压扁、不裁边；单视图主体占对应画格的70%-85%，四周保留均匀白边。"""

PROMPT_CLOTHING = """请根据我上传的服装图片，提取服装本体，生成标准化白底服装平铺图。严格保留服装的真实版型、颜色、面料质感、纹理、图案、门襟、领口、袖口、下摆、纽扣、拉链、口袋、缝线、褶皱、拼接结构、印花位置和所有设计细节，不要改款，不要改变版型，不要替换面料，不要让原衣服纹理残留或混乱。

删除人物身体、手部、头发、背景、衣架、桌面、杂物、阴影干扰、文字、水印和无关元素，只保留服装本体。

输出为纯白背景服装平铺图。服装需要自然摊平，正面朝上，衣身完整展开，左右对称，袖子、裤腿、裙摆等结构清楚可见。若服装有背面重要设计，可同时生成正面平铺图和背面平铺图，并排展示；若只有单面需求，则只输出正面平铺图。

画面要求：白底干净、边缘清晰、面料纹理真实、图案完整、不变形、不缺失、不重绘设计、无多余道具、无文字、无水印，适合作为后续换装、视频复刻和服装结构锁定参考图。"""

PROMPT_MODEL = """请根据我上传的人物参考图，提取人物形象，生成标准化人物参考图。
核心要求： 严格保留人物的真实面部特征、五官比例、脸型、发型、发色、肤色、身材比例、肩颈比例、四肢比例、体态气质、妆容风格、穿搭风格和整体视觉特征。不要换脸，不要改变年龄感，不要改变发型，不要重新设计人物，不要把人物美化成网红脸或模板脸。
人物提取要求： 删除原图中的复杂背景、杂物、路面、树木、建筑、文字、水印和无关元素，只保留人物主体。人物需要完整清晰，头部、身体、四肢尽量完整，不要遮脸，不要裁掉关键身体部位，不要出现额外人物或多余肢体。
输出形式： 输出为干净的白底或浅灰底人物参考图。优先生成全身正面视图，人物自然站立，面向镜头，姿态端正，表情自然。若需要更完整的人物参考，可同时展示正面、侧面、背面三视图，但必须保持同一人物、同一发型、同一服装、同一鞋子和同一身材比例。
真实质感要求（重点去AI化）： 人物必须呈现真实摄影质感，而不是AI塑料人效果。皮肤需要具备高精度真实皮肤质感，清晰可见自然毛孔、轻微软组织起伏、细小纹理、微弱肤色变化、自然红润层次、局部微小阴影变化和轻微真实瑕疵。允许存在非常细微的皮肤不完美，例如轻微软纹、淡淡泪沟、轻微肤色不均、自然唇纹、轻微鼻翼纹理和真实的面部明暗变化。严禁皮肤像塑料、蜡像、陶瓷或磨皮过度；严禁过度祛瑕、过度柔焦、过度美白、过度平滑。
面部与五官要求： 保留真实五官结构和立体感，眼睛、鼻子、嘴唇、耳朵和下颌线要自然真实，不夸张放大，不做二次设计。眼部保留自然眼睑纹理、卧蚕层次、睫毛疏密变化和真实高光，不要玻璃假眼感。嘴唇保留自然唇纹和湿润感，不要橡胶感。牙齿如果可见，应自然整洁但不要假白发光。
头发真实感要求： 头发必须保留真实发量、发丝走向、层次和蓬松度，要有细碎发丝、发尾轻微毛躁、自然高光和发束变化。不要把头发处理成一整块塑料片，不要过度顺滑，不要假发质感。
服装与材质要求： 保留原服装真实款式、面料、褶皱、垂坠、缝线、纹理和层次关系，不要改变版型，不要把衣服做得像CG材质。服装应有真实布料质感和自然褶皱，边缘清晰，结构明确。
肢体与姿态要求： 人物站姿自然，重心合理，肩膀、手臂、手掌、手指、腰部、腿部和脚踝比例真实，避免僵硬摆拍感，避免畸形手指、过长手臂、扭曲关节和不自然体态。双手若自然下垂或轻微摆放，动作应符合真实人体逻辑。
光线与成像要求： 整体为真实摄影棚抠图后的标准参考图效果，光线均匀柔和，保留自然明暗过渡，不要强HDR，不要过度锐化，不要虚假边缘光，不要明显CG渲染感。人物边缘清晰干净，但不要生硬剪贴感。
最终画面要求： 白底或浅灰底，人物完整，光线均匀，肤色自然，边缘干净，服装结构清楚，无多余道具、无文字、无水印。整体效果必须像真实拍摄的人物标准参考图，而不是AI重绘的人像海报。"""

PROMPT_SCENE = """请根据我上传的场景图片，提取场景环境，生成干净的场景参考图。严格保留原场景的空间结构、背景布局、墙面、地面、家具位置、道具陈列、灯光方向、色彩氛围、材质质感、透视关系和镜头视角，不要改变场景风格，不要重新设计空间，不要增加原图中不存在的大型物件。

删除人物、产品主体、手部、临时遮挡物、杂乱干扰物、文字、水印和无关视觉元素，只保留可用于拍摄的干净场景背景。被人物或产品遮挡的区域，只允许根据周围环境自然补全，不要编造明显不属于原场景的新元素。

输出为无人物、无产品的空场景图，保持原图构图和镜头机位。画面需要干净、自然、空间完整，适合作为后续视频复刻中的背景场景参考。

画面要求：光线一致、透视正确、材质真实、背景清晰、无多余文字、无水印、无明显修补痕迹。"""


# 本步骤以 PRD 完整提示词为准，禁止被精简配置覆盖。


def _save_result(url_or_b64: str, save_path: Path):
    """保存 API 返回的图片（URL 或 base64）"""
    if url_or_b64.startswith("http"):
        resp = httpx.get(url_or_b64, timeout=60.0)
        save_path.write_bytes(resp.content)
    else:
        save_path.write_bytes(base64.b64decode(url_or_b64))


async def _process_single(
    client: Image2Client,
    image_path: str,
    prompt: str,
    output_path: Path,
    size: str = "1024x1024",
) -> dict:
    """处理单个素材：原图 + 提示词 → 标准化参考图（自动压缩）"""
    # 保留包装印刷、食品切面与表面纹路；1536px 在控制请求体的同时避免 1024px 过度丢失细节。
    compressed = _compress_image(image_path, max_size=1536, quality=92)
    img_b64 = base64.b64encode(compressed).decode()
    images = [f"data:image/jpeg;base64,{img_b64}"]

    result = await client.image_edit_sync(
        prompt=prompt,
        images=images,
        size=size,
        n=1,
    )
    data_list = result.get("data", [])
    if not data_list:
        return {"status": "no_data"}

    url = data_list[0].get("url", "")
    b64 = data_list[0].get("b64_json", "")
    _save_result(url or b64, output_path)
    return {"status": "ok", "path": str(output_path)}


async def _run_async(
    product_image_path: Optional[str] = None,
    clothing_image_path: Optional[str] = None,
    model_image_path: Optional[str] = None,
    scene_image_path: Optional[str] = None,
    output_dir: str = ".",
    size: str = "1024x1024",
) -> dict:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    client = Image2Client()

    results = {}

    # 2.1 产品图 → 三视图白底
    if product_image_path and Path(product_image_path).exists():
        print("  [2.1] 处理产品图...")
        p = out / "product_standard.png"
        try:
            r = await _process_single(client, product_image_path, PROMPT_PRODUCT, p, size)
            results["product"] = r
            print(f"  [2.1] 产品图完成: {r.get('path', r.get('status'))}")
        except Exception as e:
            # 降级：用原图压缩版作为标准化图
            _compress_image(product_image_path).save(p, "PNG") if False else None
            import shutil
            shutil.copy2(product_image_path, p)
            results["product"] = {"status": "fallback", "path": str(p), "error": str(e)}
            print(f"  [2.1] 产品图失败({e})，降级使用原图")

    # 2.2 服装图 → 服装平铺图白底
    if clothing_image_path and Path(clothing_image_path).exists():
        print("  [2.2] 处理服装图...")
        p = out / "clothing_standard.png"
        try:
            r = await _process_single(client, clothing_image_path, PROMPT_CLOTHING, p, size)
            results["clothing"] = r
            print(f"  [2.2] 服装图完成: {r.get('path', r.get('status'))}")
        except Exception as e:
            import shutil
            shutil.copy2(clothing_image_path, p)
            results["clothing"] = {"status": "fallback", "path": str(p), "error": str(e)}
            print(f"  [2.2] 服装图失败({e})，降级使用原图")

    # 2.3 模特图 → 人物白底正面照
    if model_image_path and Path(model_image_path).exists():
        print("  [2.3] 处理模特图...")
        p = out / "model_standard.png"
        try:
            r = await _process_single(client, model_image_path, PROMPT_MODEL, p, size)
            results["model"] = r
            print(f"  [2.3] 模特图完成: {r.get('path', r.get('status'))}")
        except Exception as e:
            import shutil
            shutil.copy2(model_image_path, p)
            results["model"] = {"status": "fallback", "path": str(p), "error": str(e)}
            print(f"  [2.3] 模特图失败({e})，降级使用原图")

    # 2.4 场景图 → 无人物场景图
    if scene_image_path and Path(scene_image_path).exists():
        print("  [2.4] 处理场景图...")
        p = out / "scene_standard.png"
        try:
            r = await _process_single(client, scene_image_path, PROMPT_SCENE, p, size)
            results["scene"] = r
            print(f"  [2.4] 场景图完成: {r.get('path', r.get('status'))}")
        except Exception as e:
            import shutil
            shutil.copy2(scene_image_path, p)
            results["scene"] = {"status": "fallback", "path": str(p), "error": str(e)}
            print(f"  [2.4] 场景图失败({e})，降级使用原图")

    results["output_dir"] = str(out)
    return results


def run(
    product_image_path: Optional[str] = None,
    clothing_image_path: Optional[str] = None,
    model_image_path: Optional[str] = None,
    scene_image_path: Optional[str] = None,
    output_dir: str = ".",
    size: str = "1024x1024",
) -> dict:
    """
    Step2: 素材预处理

    输入用户上传的原始素材图，输出标准化白底参考图：
    2.1 产品图 → 三视图白底
    2.2 服装图 → 服装平铺图白底
    2.3 模特图 → 人物白底正面照
    2.4 场景图 → 无人物场景图

    Args:
        product_image_path: 产品图路径（可选）
        clothing_image_path: 服装图路径（可选）
        model_image_path: 模特图路径（可选）
        scene_image_path: 场景图路径（可选）
        output_dir: 输出目录
        size: 生成图片尺寸

    Returns:
        {"product": {...}, "clothing": {...}, "model": {...}, "scene": {...}, "output_dir": str}
    """
    return asyncio.run(_run_async(
        product_image_path, clothing_image_path,
        model_image_path, scene_image_path,
        output_dir, size,
    ))
