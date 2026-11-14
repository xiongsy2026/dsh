"""
Step5 (H3 模式): 使用 Minimax H3 工作流生成视频

作为爆款复刻 Step5 的可选实现，替代 weeksir.com Seedance 2.0 路径。
直接调用 RunningHub ComfyUI 工作流，无需浏览器自动化。

参考图综合策略（最多5张，H3 工作流有4个 LoadImage 节点）:
  优先级（综合用于爆款复刻的产品替换）:
    1. replaced_storyboard_image_path  替换好产品的分镜图（最关键）
    2. product_image_path              产品图（爆款复刻的核心替换素材）
    3. storyboard_image_path           故事板海报（整体画面参考）
    4. model_image_path                模特图
    5. scene_image_path / extra_images 场景图 / 额外图
  分发到工作流的4个 LoadImage 节点: 153、155、182、185
"""
import asyncio
import json
import os
from pathlib import Path
from typing import Optional

from backend.services import h3_director_client


DEFAULT_PROMPT = (
    "请严格按照参考图的画面构图、人物姿态、产品位置生成视频，"
    "9:16竖版，保持产品一致性和人物一致性，镜头平滑过渡，"
    "超清4K极致细节，不要出现字幕"
)


def run(
    storyboard_image_path: Optional[str] = None,
    replaced_storyboard_image_path: Optional[str] = None,
    product_image_path: Optional[str] = None,
    model_image_path: Optional[str] = None,
    scene_image_path: Optional[str] = None,
    extra_images: Optional[list] = None,
    product_name: str = "爆款产品",
    output_dir: str = ".",
    duration: float = 15.0,
    aspect_ratio: str = "9:16",
    megapixels: float = 0.7,
    custom_prompt: Optional[str] = None,
    workflow_id: str = "",
    max_wait: int = 1800,
) -> dict:
    """Step5 H3 模式：通过 RunningHub ComfyUI 工作流生成视频

    Args:
        storyboard_image_path: Step4 故事板海报
        replaced_storyboard_image_path: Step3 替换好产品的分镜图（首选主参考图）
        product_image_path: 产品图（爆款复刻的核心替换素材）
        model_image_path: 模特图
        scene_image_path: 场景图
        extra_images: 额外参考图
        product_name: 产品名称
        output_dir: 输出目录
        duration: 视频时长（秒）
        aspect_ratio: 画面比例
        megapixels: 分辨率
        custom_prompt: 自定义提示词
        workflow_id: 自定义工作流ID（留空用配置）
        max_wait: 最大等待秒数

    Returns:
        与 Seedance Step5 相同的返回结构:
        {"status": "success", "video_url": ..., "local_video": ...}
    """
    print("\n" + "=" * 60)
    print("Step5 (H3): Minimax H3 视频生成（RunningHub ComfyUI）")
    print("=" * 60)

    # 综合收集所有可用参考图（按优先级，去重，最多5张）
    candidates = [
        replaced_storyboard_image_path,  # 替换好产品的分镜图（最关键）
        product_image_path,              # 产品图
        storyboard_image_path,           # 故事板海报
        model_image_path,                # 模特图
        scene_image_path,                # 场景图
    ]
    if extra_images:
        candidates.extend(extra_images)

    ref_images = []
    seen = set()
    for img in candidates:
        if img and img not in seen and Path(img).exists():
            ref_images.append(img)
            seen.add(img)
        if len(ref_images) >= 5:
            break

    if not ref_images:
        raise RuntimeError(
            "H3 模式至少需要1张参考图（替换分镜图/产品图/故事板）"
        )

    print(f"  → 综合参考图 ({len(ref_images)} 张):")
    for i, img in enumerate(ref_images):
        print(f"     [{i + 1}] {os.path.basename(img)}")

    # 提示词
    prompt = custom_prompt or DEFAULT_PROMPT
    if product_name and product_name != "爆款产品":
        prompt = f"{prompt}。产品：{product_name}"
    print(f"  → 时长: {duration}s | 比例: {aspect_ratio} | megapixels: {megapixels}")
    print(f"  → 提示词: {prompt[:80]}...")

    # 输出路径
    output_path = os.path.join(output_dir, "h3_video.mp4")
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # 进度回调
    step5_log = os.path.join(output_dir, "step5_h3_log.jsonl")
    log_f = open(step5_log, "w", encoding="utf-8")

    def on_progress(status, progress, msg):
        print(f"  [{status}] {progress}% - {msg}")
        log_f.write(json.dumps({
            "status": status, "progress": progress, "msg": msg, "ts": __import__("time").time()
        }, ensure_ascii=False) + "\n")
        log_f.flush()

    try:
        # 跑异步：把所有参考图综合传给导演台工作流
        result_path = asyncio.run(h3_director_client.generate_video(
            mode="reference",
            reference_images=ref_images,
            prompt=prompt,
            duration=int(duration),
            aspect_ratio=aspect_ratio,
            megapixels=megapixels,
            output_path=output_path,
            max_wait=max_wait,
            progress_callback=on_progress,
        ))

        result = {
            "status": "success",
            "video_url": "",  # H3 不返回 URL，只有本地文件
            "local_video": result_path,
            "model": "minimax-h3",
            "workflow_id": workflow_id or h3_director_client.H3_WORKFLOW_ID,
        }

        # 保存结果
        with open(os.path.join(output_dir, "step5_result.json"), "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        print(f"\n✓ H3 视频生成完成: {result_path}")
        return result

    except Exception as e:
        result = {"status": "failed", "error": str(e), "model": "minimax-h3"}
        with open(os.path.join(output_dir, "step5_result.json"), "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n✗ H3 视频生成失败: {e}")
        return result

    finally:
        log_f.close()


if __name__ == "__main__":
    # 快速测试
    import sys
    if len(sys.argv) < 2:
        print("用法: python step5_h3_generate.py <参考图1> [参考图2]")
        print(f"WORKFLOW_ID: {h3_director_client.WORKFLOW_ID or '(未配置)'}")
        sys.exit(0)

    r = run(
        storyboard_image_path=sys.argv[2] if len(sys.argv) > 2 else None,
        replaced_storyboard_image_path=sys.argv[1],
        output_dir="outputs/h3_test",
        duration=5,
    )
    print(json.dumps(r, ensure_ascii=False, indent=2))
