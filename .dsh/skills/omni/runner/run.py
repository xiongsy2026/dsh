#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""omni skill runner：经统一模型网关 backend.gateway 出片（不直连 run_omni）。

skill 只报「能力 + purpose」= video.generate / omni-t2v，由 gateway 解析 provider/model/密钥
（全部来自 backend/.env），实现「一处管理、skill 自动接模型」。

用法：
  python run.py --prompt "In a single continuous 10s shot, ..." \
    --duration 10 --aspect-ratio 9:16 --output-dir D:/out
可选：--model 覆盖默认出片模型；--video-url / --image-urls 传参考。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

VIDEO_SKILL_HOME = os.environ.get("VIDEO_SKILL_HOME", os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))


def _ensure_on_path() -> None:
    if not os.path.isdir(VIDEO_SKILL_HOME):
        raise SystemExit(f"找不到管线目录 {VIDEO_SKILL_HOME}（用 VIDEO_SKILL_HOME 覆盖）")
    if VIDEO_SKILL_HOME not in sys.path:
        sys.path.insert(0, VIDEO_SKILL_HOME)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Omni 出片（经 gateway 统一模型入口）")
    p.add_argument("--prompt", required=True, help="英文 prompt（中文仅产品名/印花/口播引号）")
    p.add_argument("--duration", type=int, default=10, choices=[5, 8, 10], help="omni 单段上限 10s")
    p.add_argument("--aspect-ratio", default="16:9", help="16:9 / 9:16 / 1:1")
    p.add_argument("--output-dir", required=True)
    p.add_argument("--model", default=None, help="覆盖网关默认模型（一般留空，由网关统一管）")
    p.add_argument("--video-url", default=None, help="可选参考视频 URL")
    p.add_argument("--image-urls", nargs="*", default=None, help="可选参考图 URL 列表")
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    if not (a.prompt or "").strip():
        raise SystemExit("prompt 不能为空")
    _ensure_on_path()
    from backend import gateway  # 延迟导入：--help 不需要

    result = gateway.call(
        "video.generate",
        "omni-t2v",
        prompt=a.prompt,
        duration=a.duration,
        aspect_ratio=a.aspect_ratio,
        output_dir=a.output_dir,
        model=a.model,
        video_url=a.video_url,
        image_urls=list(a.image_urls) if a.image_urls else None,
    )
    print(json.dumps(result, ensure_ascii=False, default=str))
    if isinstance(result, dict) and (result.get("success") is False or result.get("error")):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
