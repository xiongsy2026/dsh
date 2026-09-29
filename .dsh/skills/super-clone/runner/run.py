#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""super-clone skill runner：薄封装 D:\\video-skill 的 super_clone_wan3.run_pipeline。

只做参数整理 + 调用 + 打印结果 JSON，不重写管线、不复制视频引擎（PRD §6.2 共用工具层）。

用法示例（难单一段）：
  python run.py --task-id seg3 \\
    --video D:/ref/orig_trim.mp4 --product-images p1.jpg p2.png \\
    --product-name "富铁软糖" --output-dir D:/out \\
    --model-image face_c.jpg --as-duration-chunk
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

VIDEO_SKILL_HOME = os.environ.get("VIDEO_SKILL_HOME", r"D:\video-skill")


def _ensure_video_skill_on_path() -> None:
    if not os.path.isdir(VIDEO_SKILL_HOME):
        raise SystemExit(
            f"找不到管线目录 {VIDEO_SKILL_HOME}（用环境变量 VIDEO_SKILL_HOME 覆盖）"
        )
    if VIDEO_SKILL_HOME not in sys.path:
        sys.path.insert(0, VIDEO_SKILL_HOME)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="超级复刻 Wan3.0 出片（薄封装 run_pipeline）")
    p.add_argument("--task-id", required=True)
    p.add_argument("--video", required=True, help="原片（或该段裁剪后原片）路径")
    p.add_argument("--product-images", nargs="+", required=True, help="产品图 1-10 张")
    p.add_argument("--product-name", required=True, help="产品名（入口校验：不能为空）")
    p.add_argument("--customer-request", default=None, help="缺省用 --product-name")
    p.add_argument("--output-dir", required=True)
    p.add_argument("--product-facts", default=None, help="客户明确陈述的非视觉事实")
    p.add_argument("--model-image", default=None, help="该段唯一脸图（一段一张脸）")
    p.add_argument("--extra-model-images", nargs="*", default=None, help="多脸时其余脸图(不整段塞)")
    p.add_argument("--scene-image", default=None, help="换背景仅传文字，一般留空")
    p.add_argument("--trim-start", type=float, default=None)
    p.add_argument("--trim-end", type=float, default=None)
    p.add_argument("--resolution", default=None)
    p.add_argument("--aspect-ratio", default=None)
    p.add_argument("--engine", default="wan3")
    p.add_argument("--extra-note", default=None, help="extra_compile_note")
    p.add_argument("--split-by-shots", action="store_true")
    p.add_argument("--as-duration-chunk", action="store_true", help="按段调用时置 True（难单每段 True）")
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    if not (a.product_name or "").strip():
        raise SystemExit("product-name 不能为空（入口校验）")
    _ensure_video_skill_on_path()
    # 延迟导入：--help / 参数校验不需要重依赖（playwright 等）
    from backend.services.super_clone_wan3 import run_pipeline  # noqa

    os.makedirs(a.output_dir, exist_ok=True)
    result = asyncio.run(run_pipeline(
        task_id=a.task_id,
        video_path=a.video,
        product_image_paths=list(a.product_images),
        customer_request=a.customer_request or a.product_name,
        output_dir=a.output_dir,
        trim_start=a.trim_start,
        trim_end=a.trim_end,
        model_image_path=a.model_image,
        scene_image_path=a.scene_image,
        product_name=a.product_name,
        product_facts=a.product_facts,
        resolution=a.resolution,
        aspect_ratio=a.aspect_ratio,
        engine=a.engine,
        split_by_shots=a.split_by_shots,
        extra_compile_note=a.extra_note,
        extra_model_image_paths=list(a.extra_model_images) if a.extra_model_images else None,
        as_duration_chunk=a.as_duration_chunk,
    ))
    print(json.dumps(result, ensure_ascii=False, default=str))
    if isinstance(result, dict):
        if result.get("success") is False or result.get("error"):
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
