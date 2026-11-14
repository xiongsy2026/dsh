"""爆款裂变 Wan3.0 - 整段商品替换裂变工作流

模式：单模式「爆款裂变」，适用于 ≤15s 爆款视频的素材矩阵替换。

与超级复刻的区别：
  - 超级复刻：1个原视频 + 1套素材 → 1个成品（1:1 复刻）
  - 爆款裂变：1个原视频 + N套素材 → N个成品（矩阵裂变）

技术路线：
  - reference_video = 完整原视频（≤15s）
  - duration = -1（保持原视频时长）
  - 变化维度：产品/模特/场景/口播替换
  - 动作骨架：完全继承原视频（保证动作自然真实）

工作流（共享解析，分头生成）：
  Step 1: MiMo 解析原视频 → storyboard JSON（只1次，省调用）
  Step 2: 对每套素材独立执行：
    2a. media_manifest 构建（每套素材独立编号）
    2b. GPT 编译器 → Wan3.0 prompt
    2c. GPT Lint 校验
    2d. Wan3.0 执行（reference_video=原视频, duration=-1）
  Step 3: 收集 N 个成品视频

严格遵循 wan3-prompt-builder skill v3.1.0 规范：
  - 参考视频单段 1-15 秒（≤15s 整段送入，无需切分）
  - 输入视频时长 + 输出时长 ≤ 30 秒（1:1 复刻下天然满足）
  - 提示词含 Shot 分镜 + 图N 参考绑定 + 后置约束
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from backend.services.super_clone_wan3 import (
    _build_media_manifest,
    _ffprobe_duration,
    step1_5_compile_prompt,
    step1_mimo_director,
    step2_wan3_execute,
    validate_and_fix_prompt,
    validate_input,
    validate_prompt_time_against_storyboard,
    validate_trim,
    normalize_storyboard_time,
    trim_video,
)


# ── 素材组数据结构 ──
class MaterialGroup:
    """单套素材组（产品图 + 可选模特图 + 可选场景图 + 产品信息）

    每套素材组生成1个变体视频。
    """

    def __init__(
        self,
        group_id: str,
        product_image_paths: List[str],
        product_name: str,
        model_image_path: Optional[str] = None,
        scene_image_path: Optional[str] = None,
        customer_request: str = "",
        product_facts: Optional[str] = None,
    ):
        self.group_id = group_id
        self.product_image_paths = [p for p in (product_image_paths or []) if p]
        self.product_name = product_name or ""
        self.model_image_path = model_image_path or None
        self.scene_image_path = scene_image_path or None
        self.customer_request = customer_request or ""
        self.product_facts = product_facts or None

    def validate(self) -> Optional[str]:
        """校验本套素材组完整性，返回错误信息或 None。"""
        if not self.product_image_paths:
            return f"素材组 {self.group_id} 至少需要 1 张产品图"
        if not self.product_name.strip():
            return f"素材组 {self.group_id} 缺少产品名"
        for p in self.product_image_paths:
            if not Path(p).exists():
                return f"素材组 {self.group_id} 产品图不存在: {p}"
        if self.model_image_path and not Path(self.model_image_path).exists():
            return f"素材组 {self.group_id} 模特图不存在: {self.model_image_path}"
        if self.scene_image_path and not Path(self.scene_image_path).exists():
            return f"素材组 {self.group_id} 场景图不存在: {self.scene_image_path}"
        # 素材总数 1-10 张
        total = (
            len(self.product_image_paths)
            + int(self.model_image_path is not None)
            + int(self.scene_image_path is not None)
        )
        if not 1 <= total <= 10:
            return (
                f"素材组 {self.group_id} 素材总数 {total} 张非法，"
                f"必须 1-10 张（产品图 {len(self.product_image_paths)}"
                f" + 模特图 {1 if self.model_image_path else 0}"
                f" + 场景图 {1 if self.scene_image_path else 0}）"
            )
        return None

    def to_media_manifest(self) -> List[Dict[str, str]]:
        """构建本素材组的 media_manifest（图N 编号归属）。"""
        return _build_media_manifest(
            self.product_image_paths,
            self.model_image_path,
            self.scene_image_path,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "group_id": self.group_id,
            "product_image_paths": self.product_image_paths,
            "product_name": self.product_name,
            "model_image_path": self.model_image_path,
            "scene_image_path": self.scene_image_path,
            "customer_request": self.customer_request,
            "product_facts": self.product_facts,
        }


# ── 单个变体生成（复用超级复刻的 Step 1.5 / Step 2 / Step 3）──
async def _generate_one_variant(
    variant_id: str,
    video_path: str,
    target_duration: float,
    storyboard: List[Dict[str, Any]],
    material: MaterialGroup,
    output_dir: str,
    on_log: Optional[Callable[[str], None]] = None,
    on_progress: Optional[Callable[[str, str, int, str], None]] = None,
    resolution: Optional[str] = None,
    aspect_ratio: Optional[str] = None,
    audio: Optional[bool] = None,
) -> Dict[str, Any]:
    """对单套素材执行：GPT编译 → Lint → Wan3.0 → 成品视频

    Args:
        variant_id: 变体 ID（如 "v0", "v1"）
        video_path: 原视频路径（reference_video）
        target_duration: Step 0 锁死的目标时长
        storyboard: MiMo 解析的 storyboard（共享，不重新解析）
        material: 本变体的素材组
        output_dir: 本变体的输出目录

    Returns:
        {
            "variant_id": str,
            "status": "success" | "failed",
            "error": Optional[str],
            "prompt": Optional[str],
            "result_url": Optional[str],
            "local_path": Optional[str],
            "output_duration": Optional[float],
            "wan3_task_id": Optional[str],
            "duration_status": Optional[str],
        }
    """
    def log(msg: str) -> None:
        prefix = f"[variant {variant_id}]"
        if on_log:
            on_log(f"{prefix} {msg}")
        print(f"  {prefix} {msg}", flush=True)

    result: Dict[str, Any] = {
        "variant_id": variant_id,
        "status": "failed",
        "error": None,
        "prompt": None,
        "result_url": None,
        "local_path": None,
        "output_duration": None,
        "wan3_task_id": None,
        "duration_status": None,
        "group_id": material.group_id,
    }

    try:
        # ── 构建本变体的 media_manifest ──
        media_manifest = material.to_media_manifest()
        log(f"media_manifest: {media_manifest}")

        # ── Step 2a: GPT 编译器 → Wan3.0 prompt ──
        log("Step 2a: GPT 编译器 → Wan3.0 prompt")
        compiled = await step1_5_compile_prompt(
            storyboard=storyboard,
            target_duration=target_duration,
            media_manifest=media_manifest,
            customer_request=material.customer_request,
            product_image_paths=material.product_image_paths,
            model_image_path=material.model_image_path,
            scene_image_path=material.scene_image_path,
            product_name=material.product_name,
            product_facts=material.product_facts,
            on_log=log,
        )
        prompt = compiled["prompt"]
        try:
            validate_prompt_time_against_storyboard(prompt, storyboard)
        except RuntimeError as exc:
            raise RuntimeError(f"Step 2a Prompt 时间校验失败: {exc}") from exc
        log(f"✓ Step 2a 完成: prompt {len(prompt)} 字")

        # ── Step 2b: GPT Lint ──
        log("Step 2b: GPT Lint → 规则校验 + LLM 二次审查")
        check = await validate_and_fix_prompt(
            prompt=prompt,
            target_duration=target_duration,
            media_manifest=media_manifest,
            product_name=material.product_name,
            storyboard=storyboard,
            on_log=log,
        )
        prompt = check["prompt"]
        if check["fixes"]:
            log(f"✓ Step 2b 完成: 共修复 {len(check['fixes'])} 项")
        else:
            log("✓ Step 2b 完成: 自检通过")

        # ── Step 2c: Wan3.0 执行 ──
        log("Step 2c: Wan3.0（执行器）→ 生成成品视频")
        variant_output_dir = str(Path(output_dir) / variant_id)
        step2 = await step2_wan3_execute(
            prompt=prompt,
            video_path=video_path,
            product_image_paths=material.product_image_paths,
            target_duration=target_duration,
            output_dir=variant_output_dir,
            media_manifest=media_manifest,
            model_image_path=material.model_image_path,
            scene_image_path=material.scene_image_path,
            primary_image_id=1,
            on_log=log,
            on_progress=lambda ph, el, msg: (
                on_progress(variant_id, ph, el, msg) if on_progress else None
            ),
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            audio=audio,
        )

        result["prompt"] = prompt
        result["result_url"] = step2.get("final_result_url")
        result["local_path"] = step2.get("final_path")
        result["output_duration"] = step2.get("output_duration")
        result["wan3_task_id"] = step2.get("task_id")
        result["duration_status"] = step2.get("duration_status")
        result["status"] = "success" if step2.get("final_path") else "failed"
        if not step2.get("final_path"):
            result["error"] = f"Wan3.0 未生成成品（status={step2.get('duration_status')}）"
        else:
            log(f"✓ 变体完成 → {step2.get('final_path')}")

    except Exception as e:
        result["error"] = str(e)
        log(f"✗ 变体失败: {e}")

    return result


# ── 顶层编排 ──
async def run_pipeline(
    task_id: str,
    video_path: str,
    material_groups: List[MaterialGroup],
    output_dir: str,
    on_log: Optional[Callable[[str, None]]] = None,
    on_phase: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    trim_start: Optional[float] = None,
    trim_end: Optional[float] = None,
    max_concurrent: int = 3,
    resolution: Optional[str] = None,
    aspect_ratio: Optional[str] = None,
    audio: Optional[bool] = None,
) -> Dict[str, Any]:
    """爆款裂变 Wan3.0 顶层编排。

    工作流：
      Step 1: MiMo 解析原视频 → storyboard JSON（只1次）
      Step 2: 对每套素材组并行执行：
        2a. GPT 编译器 → Wan3.0 prompt
        2b. GPT Lint 校验
        2c. Wan3.0 执行（reference_video=原视频, duration=-1）
      Step 3: 收集 N 个成品视频

    Args:
        task_id: 任务 ID
        video_path: 原爆款视频路径（≤15s；若提供 trim 区间则裁剪后再走流程）
        material_groups: N 套素材组（每套生成1个变体）
        output_dir: 输出根目录
        on_log: 实时日志回调
        on_phase: 阶段变更回调 fn(phase, extra)
        trim_start: 裁剪起点（秒，0-based）
        trim_end: 裁剪终点（秒，0-based）
        max_concurrent: 最大并发变体数（默认3，避免 Wan3.0 API 限流）

    Returns:
        {
            "storyboard": list,                # MiMo 分镜（共享）
            "target_duration": float,          # Step 0 锁死的全局时长
            "trimmed_path": str,               # 裁剪后视频路径
            "trim_range": dict,                # {"start": float, "end": float} 或 None
            "variants": list,                  # N 个变体的结果列表
            "success_count": int,              # 成功变体数
            "failed_count": int,               # 失败变体数
        }
    """
    def log(msg: str) -> None:
        if on_log:
            on_log(msg)
        print(f"[hot-explosion {task_id}] {msg}", flush=True)

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # ── 入口校验 ──
    err = validate_input(video_path)
    if err:
        raise RuntimeError(err)
    if not material_groups:
        raise RuntimeError("material_groups 至少需要 1 套素材组")
    for i, mg in enumerate(material_groups):
        mg_err = mg.validate()
        if mg_err:
            raise RuntimeError(f"素材组 {i} 校验失败: {mg_err}")

    # ── Step 0: 素材预处理（裁剪 + target_duration 全局硬参数）──
    trimmed_path = video_path
    trim_range: Optional[Dict[str, float]] = None
    orig_dur = _ffprobe_duration(video_path) or 0.0
    trim_err = validate_trim(trim_start, trim_end, orig_dur)
    if trim_err:
        raise RuntimeError(f"裁剪校验失败: {trim_err}")
    if trim_start is not None and trim_end is not None:
        trimmed_path = str(Path(output_dir) / "trimmed_source.mp4")
        log(f"=== Step 0: 精准裁剪原视频 [{trim_start:.2f}s → {trim_end:.2f}s] ===")
        trim_video(video_path, float(trim_start), float(trim_end), trimmed_path)
        trim_range = {"start": float(trim_start), "end": float(trim_end)}
        log(f"✓ 裁剪完成 → {trimmed_path}")
    else:
        # 无裁剪时校验原视频 ≤15s
        from backend.services.super_clone_wan3 import _cfg
        max_dur = _cfg()["max_input_duration"]
        if orig_dur > max_dur + 0.5:
            raise RuntimeError(
                f"未启用裁剪时原视频必须 ≤{int(max_dur)}s，当前 {orig_dur:.1f}s 超限。"
                f"请在前端剪辑尺上选定 5-15s 区间后再提交。"
            )

    target_duration = _ffprobe_duration(trimmed_path) or 0.0
    if target_duration <= 0:
        if trim_range is not None:
            target_duration = trim_range["end"] - trim_range["start"]
        else:
            raise RuntimeError("Step 0 无法确定 target_duration：ffprobe 失败且无裁剪区间")
    target_duration = round(target_duration, 2)

    # target_duration 硬校验 5-15s
    from backend.services.super_clone_wan3 import _cfg
    min_dur = _cfg()["min_trim_duration"]
    max_dur = _cfg()["max_input_duration"]
    if target_duration < min_dur:
        raise RuntimeError(
            f"Step 0 时长下限校验失败：target_duration={target_duration:.3f}s < {min_dur:.0f}s"
        )
    if target_duration > max_dur:
        raise RuntimeError(
            f"Step 0 API 上限校验失败：target_duration={target_duration:.3f}s > {max_dur:.0f}s"
            f"（Wan3.0 API 要求 reference_video ≤ {max_dur:.0f}s）"
        )
    log(f"=== Step 0: target_duration = {target_duration:.2f}s（共 {len(material_groups)} 套素材） ===")

    # ── Step 1: MiMo 解析原视频 → storyboard JSON（只1次）──
    if on_phase:
        on_phase("step1_mimo", {})
    log("=== Step 1: MiMo（纯视频解析器）→ storyboard JSON（共享解析） ===")
    max_mimo_retries = 3
    storyboard = None
    for attempt in range(1, max_mimo_retries + 1):
        step1 = await step1_mimo_director(
            video_path=trimmed_path,
            output_dir=output_dir,
            target_duration=target_duration,
            on_log=log,
        )
        storyboard = step1["storyboard"]
        try:
            storyboard = normalize_storyboard_time(storyboard, target_duration)
            break
        except RuntimeError as exc:
            if attempt < max_mimo_retries:
                log(f"⚠ 第 {attempt}/{max_mimo_retries} 次解析校验失败：{exc}，重试中…")
            else:
                raise RuntimeError(
                    f"Step 1 MiMo 解析 {max_mimo_retries} 次均未通过 Checkpoint A：{exc}"
                ) from exc
    log(f"✓ Step 1 完成：解析出 {len(storyboard)} 个分镜，共享给 {len(material_groups)} 个变体")

    # ── Step 2: 并行生成 N 个变体 ──
    if on_phase:
        on_phase("step2_variants", {"total": len(material_groups)})
    log(f"=== Step 2: 并行生成 {len(material_groups)} 个变体（最大并发 {max_concurrent}） ===")

    semaphore = asyncio.Semaphore(max_concurrent)

    async def _run_variant(idx: int, material: MaterialGroup) -> Dict[str, Any]:
        variant_id = f"v{idx}"
        async with semaphore:
            log(f"▶ 启动变体 {variant_id}（素材组: {material.group_id}）")
            variant_dir = str(Path(output_dir) / "variants" / variant_id)
            return await _generate_one_variant(
                variant_id=variant_id,
                video_path=trimmed_path,
                target_duration=target_duration,
                storyboard=storyboard,
                material=material,
                output_dir=variant_dir,
                on_log=log,
                on_progress=lambda vid, ph, el, msg: log(f"[{vid}/{ph}] {msg}"),
                resolution=resolution,
                aspect_ratio=aspect_ratio,
                audio=audio,
            )

    tasks = [_run_variant(i, mg) for i, mg in enumerate(material_groups)]
    variants = await asyncio.gather(*tasks, return_exceptions=False)

    # ── Step 3: 汇总结果 ──
    success_count = sum(1 for v in variants if v["status"] == "success")
    failed_count = len(variants) - success_count

    if on_phase:
        on_phase("completed", {
            "total": len(variants),
            "success": success_count,
            "failed": failed_count,
        })
    log(
        f"=== 爆款裂变完成：共 {len(variants)} 个变体，"
        f"成功 {success_count}，失败 {failed_count} ==="
    )

    return {
        "storyboard": storyboard,
        "target_duration": target_duration,
        "trimmed_path": trimmed_path,
        "trim_range": trim_range,
        "variants": variants,
        "success_count": success_count,
        "failed_count": failed_count,
    }
