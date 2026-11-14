"""
Step5 多镜头视频生成（复刻模式 / 原创模式）

复刻模式（replica）：
  从原视频切 N+1 张边界帧（共享帧策略），产品替换后用 H3 双帧模式生成 N 个片段，拼接成完整视频。
  相邻片段共享帧：shot_i 的尾帧 = shot_{i+1} 的首帧，拼接处天然对齐。

原创模式（original）：
  使用 Step3 的 N 张产品替换图作为关键帧，前 N-1 个片段用双帧模式（相邻共享），
  最后一个片段用单帧模式（I2VA），拼接成完整视频。不依赖原视频，纯生成式。

H3 调用：manxue H3 双帧模式（FL2VA）/ 单帧模式（I2VA）
拼接：ffmpeg concat demuxer，按 shot 顺序拼接
"""
import asyncio
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional, List, Callable

from backend.services import manxue_h3_client
from backend.services.image2_client import Image2Client


# ── 默认提示词 ──
SEGMENT_PROMPT_REPLICA = (
    "请严格按照首帧和尾帧的画面内容生成过渡视频，"
    "保持产品外观、颜色、纹理、Logo 完全一致，"
    "人物姿态自然过渡，镜头平滑运动，"
    "画面连贯无跳变，超清4K极致细节"
)

SEGMENT_PROMPT_ORIGINAL = (
    "请基于首帧画面生成视频，"
    "保持产品外观、颜色、纹理、Logo 完全一致，"
    "人物自然动作，镜头平滑运动，"
    "画面生动有感染力，超清4K极致细节"
)


# ─────────────────────────────────────
# 工具函数
# ─────────────────────────────────────

def _get_video_duration(video_path: str) -> float:
    """获取视频时长（秒）"""
    try:
        import cv2
        cap = cv2.VideoCapture(str(video_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        cap.release()
        if fps > 0 and frame_count > 0:
            return frame_count / fps
    except Exception:
        pass
    # fallback: ffprobe
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            result = subprocess.run(
                [ffprobe, "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
                capture_output=True, text=True, timeout=30
            )
            return float(result.stdout.strip())
        except Exception:
            pass
    return 15.0  # 默认假设


def _extract_frame(video_path: str, timestamp: float, output_path: Path):
    """用 ffmpeg 在指定时间点截取一帧"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        cmd = [ffmpeg, "-y", "-ss", str(timestamp), "-i", str(video_path),
               "-vframes", "1", "-q:v", "2", str(output_path)]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode == 0 and output_path.exists():
            return
    # fallback: opencv
    try:
        import cv2
        cap = cv2.VideoCapture(str(video_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(timestamp * fps))
        ok, frame = cap.read()
        cap.release()
        if ok:
            cv2.imwrite(str(output_path), frame)
            return
    except Exception:
        pass
    raise RuntimeError(f"截取帧失败: {video_path} @ {timestamp}s -> {output_path}")


def _map_duration(shot_duration: float) -> int:
    """将实际 shot 时长映射到 H3 支持的 5/10/15s"""
    if shot_duration <= 7.5:
        return 5
    elif shot_duration <= 12.5:
        return 10
    else:
        return 15


# ─────────────────────────────────────
# 产品替换（复用 Step3 的 Image2 逻辑）
# ─────────────────────────────────────

async def _replace_product_in_frame(
    client: Image2Client,
    frame_path: Path,
    product_image_paths: List[str],
    output_path: Path,
    desc: str = "",
    size: str = "1024x1792",
    sem: asyncio.Semaphore = None,
) -> str:
    """对单张帧做产品替换，返回输出路径"""
    # 复用 step3 的辅助函数
    from pipeline.step3_replace_product import (
        _img_to_b64, _resize_to_max, _sanitize_desc, _download_image, REPLACE_PROMPT
    )

    async def _do():
        product_paths = [p for p in product_image_paths[:1] if p and Path(p).exists()]
        if not product_paths:
            raise RuntimeError("至少需要1张产品参考图")

        product_b64 = _resize_to_max(_img_to_b64(Path(product_paths[0])), max_px=1536)
        original_b64 = _resize_to_max(_img_to_b64(frame_path), max_px=1024)

        desc_clean = _sanitize_desc(desc) if desc else "产品展示分镜"

        images = [f"data:image/jpeg;base64,{original_b64}"]
        images.append(f"data:image/jpeg;base64,{product_b64}")

        prompt = REPLACE_PROMPT + f"""

当前只生成一张独立分镜图。
当前分镜描述：{desc_clean}
产品像素级一致性补充约束：第2张产品参考图是唯一产品外观真值。必须逐项复制其真实轮廓、长宽厚比例、包装尺寸、颜色、材质、表面纹理、凹凸纹路、切面纹路、印刷图案、Logo、文字位置、边缘和数量。产品在画面中的视觉尺寸必须继承第1张原始分镜中原产品的占画比例、位置、透视、朝向及遮挡关系。输出画布只包含这一张图，不添加编号、字幕、水印或说明文字。"""

        result = await client.image_edit_sync(prompt=prompt, images=images, size=size, n=1)
        data_list = result.get("data", [])
        if not data_list:
            raise RuntimeError("Image2 返回 data 为空")
        url = data_list[0].get("url", "")
        b64 = data_list[0].get("b64_json", "")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if url:
            _download_image(url, output_path)
        elif b64:
            output_path.write_bytes(__import__("base64").b64decode(b64))
        else:
            raise RuntimeError("Image2 未返回图片")
        return str(output_path)

    if sem:
        async with sem:
            return await _do()
    return await _do()


async def _replace_all_frames(
    frame_paths: List[Path],
    product_image_paths: List[str],
    storyboard_json: list,
    output_dir: Path,
    concurrency: int = 4,
    progress_callback: Optional[Callable] = None,
) -> List[str]:
    """并行替换所有帧的产品"""
    client = Image2Client()
    sem = asyncio.Semaphore(concurrency)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 为每帧构建描述
    descs = []
    for i in range(len(frame_paths)):
        if i < len(storyboard_json):
            shot = storyboard_json[i]
            scene_env = shot.get("场景环境", {})
            lens = shot.get("镜头语言", {})
            action = shot.get("画面动作", {})
            desc = (
                f"场景:{scene_env.get('场景', '')} "
                f"背景:{scene_env.get('背景细节', '')} "
                f"画面:{action.get('画面内容', '')} "
                f"产品动作:{action.get('产品动作', '')} "
                f"景别:{lens.get('景别', '')} "
                f"运镜:{lens.get('运镜', '')}"
            )
        else:
            # 最后一帧（边界帧的尾部）用前一个 shot 的描述
            shot = storyboard_json[-1] if storyboard_json else {}
            action = shot.get("画面动作", {})
            desc = f"画面:{action.get('画面内容', '产品展示')}"
        descs.append(desc)

    async def _replace_one(idx: int) -> str:
        frame_path = frame_paths[idx]
        out_path = output_dir / f"frame_{idx:02d}.png"
        last_err = None
        for attempt in range(1, 4):
            try:
                if progress_callback:
                    progress_callback("replacing", idx, f"替换帧 {idx:02d}/{len(frame_paths)}（第{attempt}次）")
                result = await _replace_product_in_frame(
                    client, frame_path, product_image_paths, out_path, descs[idx], sem=sem
                )
                print(f"  ✓ 帧 {idx:02d} 替换完成")
                return result
            except Exception as e:
                last_err = e
                print(f"  ⚠ 帧 {idx:02d} 第{attempt}次替换失败: {e}")
                if attempt < 3:
                    await asyncio.sleep(2 * attempt)
        raise RuntimeError(f"帧 {idx:02d} 产品替换 3 次重试均失败: {last_err}")

    results = await asyncio.gather(*[_replace_one(i) for i in range(len(frame_paths))])
    return list(results)


# ─────────────────────────────────────
# H3 片段生成（并行 + 自动重试）
# ─────────────────────────────────────

async def _generate_segment(
    segment_idx: int,
    first_frame: str,
    last_frame: Optional[str],
    duration: int,
    ratio: str,
    prompt: str,
    output_path: Path,
    max_wait: int = 600,
    sem: asyncio.Semaphore = None,
    progress_callback: Optional[Callable] = None,
) -> str:
    """生成单个视频片段"""
    async def _do():
        last_err = None
        for attempt in range(1, 4):
            try:
                if progress_callback:
                    mode_label = "双帧" if last_frame else "单帧"
                    progress_callback("generating", segment_idx,
                                      f"片段 {segment_idx:02d} 生成中（{mode_label}，第{attempt}次，{duration}s）")
                result_path = await manxue_h3_client.generate_video(
                    mode="first_last_frame",
                    output_path=str(output_path),
                    first_frame_path=first_frame,
                    last_frame_path=last_frame,
                    prompt=prompt,
                    duration=duration,
                    aspect_ratio=ratio,
                    max_wait=max_wait,
                )
                print(f"  ✓ 片段 {segment_idx:02d} 生成完成: {output_path.name}")
                return result_path
            except Exception as e:
                last_err = e
                print(f"  ⚠ 片段 {segment_idx:02d} 第{attempt}次生成失败: {e}")
                if attempt < 3:
                    await asyncio.sleep(5 * attempt)
        raise RuntimeError(f"片段 {segment_idx:02d} 生成 3 次重试均失败: {last_err}")

    if sem:
        async with sem:
            return await _do()
    return await _do()


async def _generate_all_segments(
    replaced_frames: List[str],
    durations: List[int],
    ratio: str,
    prompt_template: str,
    output_dir: Path,
    product_name: str = "",
    concurrency: int = 3,
    progress_callback: Optional[Callable] = None,
) -> List[str]:
    """并行生成所有片段

    复刻模式：N 个片段，segment[i] = H3(first=frame[i], last=frame[i+1])，全部双帧
    原创模式：N 个片段，前 N-1 双帧，最后 1 单帧
    """
    segments_dir = output_dir / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)

    n_frames = len(replaced_frames)
    # 片段数 = 帧数 - 1（共享帧策略）
    n_segments = n_frames - 1

    # 构建每个片段的 prompt
    def _build_prompt(idx: int) -> str:
        p = prompt_template
        if product_name:
            p = f"{p}。产品：{product_name}"
        return p

    async def _gen_one(idx: int) -> str:
        first = replaced_frames[idx]
        last = replaced_frames[idx + 1] if idx + 1 < n_frames else None
        out = segments_dir / f"segment_{idx + 1:02d}.mp4"
        return await _generate_segment(
            segment_idx=idx + 1,
            first_frame=first,
            last_frame=last,
            duration=durations[idx] if idx < len(durations) else 5,
            ratio=ratio,
            prompt=_build_prompt(idx),
            output_path=out,
            sem=sem,
            progress_callback=progress_callback,
        )

    print(f"  → 并行生成 {n_segments} 个片段（并发上限 {concurrency}）...")
    results = await asyncio.gather(*[_gen_one(i) for i in range(n_segments)])
    return list(results)


# ─────────────────────────────────────
# ffmpeg 音轨处理
# ─────────────────────────────────────

def _remove_audio(generated_video: str, output_dir: Path) -> str:
    """删除视频音轨，输出无声视频（合并阶段统一配 BGM）

    Director 每段独立生成会产生各自 BGM，拼接后不统一。
    本函数删除音轨，让 final_video 保持纯画面，后续由用户/合并阶段统一配 BGM。
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("  ⚠ 未找到 ffmpeg，跳过音轨删除")
        return generated_video

    output_dir = Path(output_dir)
    output_path = output_dir / "final_video_no_audio.mp4"

    # -an 删除音轨，-c:v copy 视频流直接复制
    cmd = [
        ffmpeg, "-y",
        "-i", str(generated_video),
        "-c:v", "copy",
        "-an",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, timeout=120)
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="ignore")[-300:]
        print(f"  ⚠ 音轨删除失败，保留原生成视频: {err}")
        return generated_video

    # 用无声视频覆盖 final_video.mp4
    shutil.move(str(output_path), str(generated_video))
    print(f"  ✓ 已删除音轨（final_video 无 BGM，合并阶段统一配乐）")
    return generated_video


# ─────────────────────────────────────
# ffmpeg 拼接
# ─────────────────────────────────────

def _concat_segments(segment_paths: List[str], output_path: str) -> str:
    """用 ffmpeg concat demuxer 按顺序拼接视频片段"""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 写入 concat 列表文件
    list_file = output_path.parent / "concat_list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for p in segment_paths:
            # 路径转义：单引号包裹，替换内部单引号
            abs_path = str(Path(p).resolve())
            f.write(f"file '{abs_path}'\n")

    print(f"  → ffmpeg concat 拼接 {len(segment_paths)} 个片段...")

    # 尝试 concat demuxer（无损，要求编码一致）
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg")

    # 方法1：concat demuxer + 重编码（最兼容）
    cmd = [
        ffmpeg, "-y",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "128k",
        "-strict", "experimental",
        "-movflags", "+faststart",
        str(output_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    if result.returncode != 0 or not output_path.exists():
        print(f"  ⚠ concat demuxer 失败，尝试 concat filter...")
        # 方法2：concat filter（更可靠，但需要列出所有输入）
        if len(segment_paths) > 20:
            # 输入太多可能超出命令行长度限制
            raise RuntimeError(f"ffmpeg concat 失败且片段数过多({len(segment_paths)})，"
                               f"demuxer 错误: {result.stderr[-500:]}")

        inputs = []
        for p in segment_paths:
            inputs.extend(["-i", str(p)])

        # 构建 filter_complex
        filter_parts = []
        for i in range(len(segment_paths)):
            filter_parts.append(f"[{i}:v][{i}:a]")
        filter_complex = (
            f"{''.join(filter_parts)}concat=n={len(segment_paths)}:v=1:a=1[outv][outa]"
        )

        cmd2 = [
            ffmpeg, "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(output_path)
        ]
        result2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=600)
        if result2.returncode != 0 or not output_path.exists():
            raise RuntimeError(
                f"ffmpeg concat 拼接失败:\n"
                f"demuxer: {result.stderr[-300:]}\n"
                f"filter: {result2.stderr[-300:]}"
            )

    # 清理临时文件
    try:
        list_file.unlink()
    except Exception:
        pass

    print(f"  ✓ 拼接完成: {output_path}")
    return str(output_path)


# ─────────────────────────────────────
# 复刻模式
# ─────────────────────────────────────

def run_replica(
    video_path: str,
    timeline: list,
    storyboard_json: list,
    product_image_paths: List[str],
    output_dir: str,
    product_name: str = "",
    aspect_ratio: str = "9:16",
    progress_callback: Optional[Callable] = None,
) -> dict:
    """复刻模式：从原视频切边界帧 → 替换产品 → H3 双帧生成 → 拼接

    Args:
        video_path: 原视频路径
        timeline: [{"shot": N, "time": T}, ...] 每个 shot 的起始时间
        storyboard_json: 分镜 JSON 列表
        product_image_paths: 产品参考图路径列表
        output_dir: 输出目录
        product_name: 产品名称
        aspect_ratio: 画面比例
        progress_callback: 进度回调(status, idx, msg)

    Returns:
        {"status": "success", "video": ..., "segments": [...], "frames": [...]}
    """
    print("\n" + "=" * 60)
    print("Step5 多镜头生成（复刻模式）")
    print("=" * 60)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    def _notify(status, idx=0, msg=""):
        if progress_callback:
            try:
                progress_callback(status, idx, msg)
            except Exception:
                pass

    # ── 1. 切 N+1 张边界帧 ──
    _notify("extracting", 0, "切取边界帧...")
    print(f"\n[1/4] 切取 {len(timeline) + 1} 张边界帧（共享帧策略）...")

    boundary_frames_dir = out / "boundary_frames"
    boundary_frames_dir.mkdir(parents=True, exist_ok=True)

    video_duration = _get_video_duration(video_path)
    print(f"  → 视频总时长: {video_duration:.1f}s")

    frame_paths = []
    for i, entry in enumerate(timeline):
        t = entry["time"]
        frame_path = boundary_frames_dir / f"frame_{i:02d}.jpg"
        _extract_frame(video_path, t, frame_path)
        frame_paths.append(frame_path)
        print(f"  ✓ frame_{i:02d} @ {t:.1f}s")

    # 最后一帧：最后一个 shot 的结束时间（视频末尾前 0.5s）
    last_t = timeline[-1]["time"] if timeline else 0
    end_t = min(video_duration - 0.3, last_t + 5.0)  # 最多取最后 shot 后 5s
    if end_t <= last_t:
        end_t = video_duration - 0.3
    last_frame_path = boundary_frames_dir / f"frame_{len(timeline):02d}.jpg"
    _extract_frame(video_path, end_t, last_frame_path)
    frame_paths.append(last_frame_path)
    print(f"  ✓ frame_{len(timeline):02d} @ {end_t:.1f}s (末帧)")

    # ── 2. 产品替换 N+1 帧 ──
    _notify("replacing", 0, f"替换 {len(frame_paths)} 张边界帧的产品...")
    print(f"\n[2/4] 产品替换 {len(frame_paths)} 张边界帧...")

    replaced_dir = out / "replaced_frames"
    replaced_frames = asyncio.run(_replace_all_frames(
        frame_paths=frame_paths,
        product_image_paths=product_image_paths,
        storyboard_json=storyboard_json,
        output_dir=replaced_dir,
        progress_callback=progress_callback,
    ))
    print(f"  ✓ 全部 {len(replaced_frames)} 帧替换完成")

    # ── 3. 计算每个片段的时长 ──
    durations = []
    for i in range(len(timeline)):
        start_t = timeline[i]["time"]
        end_t = timeline[i + 1]["time"] if i + 1 < len(timeline) else end_t
        shot_dur = end_t - start_t
        h3_dur = _map_duration(shot_dur)
        durations.append(h3_dur)
        print(f"  → 片段 {i + 1}: shot {i + 1} 时长 {shot_dur:.1f}s → H3 {h3_dur}s")

    # ── 4. H3 并行生成 N 个片段 ──
    _notify("generating", 0, f"并行生成 {len(timeline)} 个片段...")
    print(f"\n[3/4] H3 并行生成 {len(timeline)} 个片段...")

    segment_paths = asyncio.run(_generate_all_segments(
        replaced_frames=replaced_frames,
        durations=durations,
        ratio=aspect_ratio,
        prompt_template=SEGMENT_PROMPT_REPLICA,
        output_dir=out,
        product_name=product_name,
        concurrency=3,
        progress_callback=progress_callback,
    ))
    print(f"  ✓ 全部 {len(segment_paths)} 个片段生成完成")

    # ── 5. ffmpeg 拼接 ──
    _notify("concatenating", 0, "拼接所有片段...")
    print(f"\n[4/4] ffmpeg 按顺序拼接 {len(segment_paths)} 个片段...")

    final_video = out / "final_video.mp4"
    final_path = _concat_segments(segment_paths, str(final_video))

    _notify("done", 0, f"视频生成完成: {final_path}")

    result = {
        "status": "success",
        "mode": "replica",
        "video": final_path,
        "segments": segment_paths,
        "frames": replaced_frames,
        "n_segments": len(segment_paths),
        "output_dir": str(out),
    }

    with open(out / "step5_multi_shot_result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n✓ 复刻模式完成: {final_path}")
    print(f"  片段数: {len(segment_paths)}")
    print(f"  输出目录: {out}")
    return result


# ─────────────────────────────────────
# 原创模式
# ─────────────────────────────────────

def run_original(
    key_frame_paths: List[str],
    storyboard_json: list,
    product_name: str,
    output_dir: str,
    aspect_ratio: str = "9:16",
    segment_duration: int = 5,
    progress_callback: Optional[Callable] = None,
) -> dict:
    """原创模式：使用已有关键帧 → H3 双帧生成 → 拼接

    Args:
        key_frame_paths: 关键帧路径列表（通常是 Step3 的产品替换图）
        storyboard_json: 分镜 JSON 列表（用于生成 prompt）
        product_name: 产品名称
        output_dir: 输出目录
        aspect_ratio: 画面比例
        segment_duration: 每个片段的时长（5/10/15）
        progress_callback: 进度回调

    Returns:
        {"status": "success", "video": ..., "segments": [...], "frames": [...]}
    """
    print("\n" + "=" * 60)
    print("Step5 多镜头生成（原创模式）")
    print("=" * 60)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    def _notify(status, idx=0, msg=""):
        if progress_callback:
            try:
                progress_callback(status, idx, msg)
            except Exception:
                pass

    n_frames = len(key_frame_paths)
    n_segments = n_frames - 1  # 共享帧策略：N 帧生成 N-1 个片段

    if n_frames < 2:
        raise RuntimeError(f"原创模式至少需要 2 张关键帧，当前只有 {n_frames} 张")

    print(f"  → 关键帧数: {n_frames}")
    print(f"  → 将生成 {n_segments} 个片段（共享帧策略）")
    print(f"  → 每片段时长: {segment_duration}s")

    # 验证关键帧存在
    for i, p in enumerate(key_frame_paths):
        if not Path(p).exists():
            raise FileNotFoundError(f"关键帧不存在: {p}")

    # ── 1. H3 并行生成 N-1 个片段 ──
    _notify("generating", 0, f"并行生成 {n_segments} 个片段...")
    print(f"\n[1/2] H3 并行生成 {n_segments} 个片段...")

    durations = [segment_duration] * n_segments
    segment_paths = asyncio.run(_generate_all_segments(
        replaced_frames=key_frame_paths,
        durations=durations,
        ratio=aspect_ratio,
        prompt_template=SEGMENT_PROMPT_ORIGINAL,
        output_dir=out,
        product_name=product_name,
        concurrency=3,
        progress_callback=progress_callback,
    ))
    print(f"  ✓ 全部 {len(segment_paths)} 个片段生成完成")

    # ── 2. ffmpeg 拼接 ──
    _notify("concatenating", 0, "拼接所有片段...")
    print(f"\n[2/2] ffmpeg 按顺序拼接 {len(segment_paths)} 个片段...")

    final_video = out / "final_video.mp4"
    final_path = _concat_segments(segment_paths, str(final_video))

    _notify("done", 0, f"视频生成完成: {final_path}")

    result = {
        "status": "success",
        "mode": "original",
        "video": final_path,
        "segments": segment_paths,
        "frames": key_frame_paths,
        "n_segments": len(segment_paths),
        "output_dir": str(out),
    }

    with open(out / "step5_multi_shot_result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n✓ 原创模式完成: {final_path}")
    print(f"  片段数: {len(segment_paths)}")
    print(f"  输出目录: {out}")
    return result


# ─────────────────────────────────────
# 统一入口
# ─────────────────────────────────────

def run(
    mode: str = "replica",
    **kwargs,
) -> dict:
    """统一入口

    Args:
        mode: "replica"（复刻模式）或 "original"（原创模式）
        **kwargs: 对应模式的参数

    Returns:
        {"status": "success", "video": ..., "segments": [...], ...}
    """
    if mode == "replica":
        return run_replica(**kwargs)
    elif mode == "original":
        return run_original(**kwargs)
    else:
        raise ValueError(f"未知模式: {mode}，支持 'replica' 或 'original'")


# ═══════════════════════════════════════════════════════════════════
# H3 Director 满血版（一次任务生成多段视频，支持任意秒数）
# ═══════════════════════════════════════════════════════════════════
#
# 与 FL2VA 方案差异：
#   - FL2VA: 每段单独提交任务，并发快但只能 5/10/15 秒
#   - Director: 一次任务生成完整多段视频，支持 1-15 秒任意整数秒
#
# 共享帧策略（与 FL2VA 一致）：
#   shot_i 尾帧 = shot_{i+1} 首帧 → Director 内部处理相邻片段对齐
#   N+1 张关键帧 → N 个片段

from backend.services import h3_director_client


# Director 比例映射（width/height）
_DIRECTOR_RATIO_MAP = {
    "9:16": (576, 1024),
    "16:9": (1024, 576),
    "1:1": (832, 832),
    "3:4": (768, 1024),
    "4:3": (1024, 768),
}


def _ratio_to_director_size(ratio: str) -> tuple:
    """画面比例映射到 Director 的 width/height"""
    return _DIRECTOR_RATIO_MAP.get(ratio, (576, 1024))


def _build_director_segments_replica(
    replaced_frames: List[str],
    storyboard_json: list,
    durations: List[int],
    prompt_template: str,
    product_name: str = "",
    gen_modes: Optional[List[str]] = None,
) -> List[dict]:
    """构建 Director segments 输入（复刻模式，支持 gen_mode 混合）

    混合模式策略：
    - fl2v 模式 shot：使用 startImagePath + endImagePath（frame[i] + frame[i+1]）
    - i2v 模式 shot：仅使用 startImagePath（frame[i]，不传 endImagePath）

    Args:
        replaced_frames: N+1 张替换后的边界帧（共享帧策略）
        gen_modes: 每段 gen_mode 列表（从 storyboard 的 gen_mode 字段读取）；若 None 则全 fl2v
    """
    segments = []
    n_frames = len(replaced_frames)
    n_segments = n_frames - 1

    # 从 storyboard 读取 gen_mode，若未提供则全 fl2v
    if gen_modes is None:
        gen_modes = []
        for i in range(n_segments):
            shot = storyboard_json[i] if i < len(storyboard_json) else {}
            gm = shot.get("gen_mode", "fl2v") if shot else "fl2v"
            gen_modes.append(gm)

    for i in range(n_segments):
        shot = storyboard_json[i] if i < len(storyboard_json) else {}
        scene_env = shot.get("场景环境", {}) if shot else {}
        action = shot.get("画面动作", {}) if shot else {}
        lens = shot.get("镜头语言", {}) if shot else {}

        prompt = prompt_template
        if product_name:
            prompt = f"{prompt}。产品：{product_name}"
        if shot:
            prompt = (
                f"{prompt}。场景:{scene_env.get('场景', '')}，"
                f"画面:{action.get('画面内容', '')}，"
                f"产品动作:{action.get('产品动作', '')}，"
                f"景别:{lens.get('景别', '')}，运镜:{lens.get('运镜', '')}"
            )

        gm = gen_modes[i] if i < len(gen_modes) else "fl2v"
        seg = {
            "durationSec": durations[i] if i < len(durations) else 5,
            "prompt": prompt,
            "negativePrompt": "bad video, blurry, low quality, distorted product",
            "startImagePath": replaced_frames[i],
        }

        if gm == "fl2v":
            # fl2v 模式：尾帧 = 下一张边界帧
            seg["endImagePath"] = replaced_frames[i + 1]
        # i2v 模式：不传 endImagePath，Director 自动识别为 i2v

        segments.append(seg)

    # 统计混合分布
    fl2v_segs = sum(1 for s in segments if "endImagePath" in s)
    i2v_segs = len(segments) - fl2v_segs
    print(f"  → Director segments 混合分布: fl2v={fl2v_segs} 段, i2v={i2v_segs} 段")

    return segments


def _build_director_segments_original(
    key_frame_paths: List[str],
    storyboard_json: list,
    durations: List[int],
    prompt_template: str,
    product_name: str = "",
    end_frame_paths: Optional[List[Optional[str]]] = None,
    gen_modes: Optional[List[str]] = None,
) -> List[dict]:
    """构建 Director segments 输入（原创模式，支持 gen_mode 混合）

    混合模式策略：
    - fl2v 模式 shot：使用 startImagePath + endImagePath（2 张关键帧）
    - i2v 模式 shot：仅使用 startImagePath（1 张关键帧，不传 endImagePath）

    Args:
        key_frame_paths: 首帧路径列表（N 个 shot）
        end_frame_paths: 尾帧路径列表（fl2v 段有路径，i2v 段为 None）；若 None 则退化为全 fl2v 模式
        gen_modes: 每段 gen_mode 列表（"fl2v" 或 "i2v"）；若 None 则全 fl2v
    """
    segments = []
    n_frames = len(key_frame_paths)

    # 兼容旧调用：end_frame_paths 或 gen_modes 为 None 时退化为全 fl2v
    if end_frame_paths is None:
        end_frame_paths = [None] * n_frames
        # 旧逻辑：N-1 段，segment[i] 用 frame[i] 和 frame[i+1]
        # 现在改为：每个 shot 1 段，fl2v 用相邻帧共享
    if gen_modes is None:
        gen_modes = ["fl2v"] * n_frames

    for i in range(n_frames):
        shot = storyboard_json[i] if i < len(storyboard_json) else {}
        scene_env = shot.get("场景环境", {}) if shot else {}
        action = shot.get("画面动作", {}) if shot else {}
        lens = shot.get("镜头语言", {}) if shot else {}

        prompt = prompt_template
        if product_name:
            prompt = f"{prompt}。产品：{product_name}"
        if shot:
            prompt = (
                f"{prompt}。场景:{scene_env.get('场景', '')}，"
                f"画面:{action.get('画面内容', '')}，"
                f"产品动作:{action.get('产品动作', '')}，"
                f"景别:{lens.get('景别', '')}，运镜:{lens.get('运镜', '')}"
            )

        gm = gen_modes[i] if i < len(gen_modes) else "fl2v"
        seg = {
            "durationSec": durations[i] if i < len(durations) else 5,
            "prompt": prompt,
            "negativePrompt": "bad video, blurry, low quality, distorted product",
            "startImagePath": key_frame_paths[i],
        }

        if gm == "fl2v":
            # fl2v 模式：需要尾帧
            end_path = end_frame_paths[i] if i < len(end_frame_paths) else None
            if end_path:
                seg["endImagePath"] = end_path
            elif i + 1 < n_frames:
                # 兼容旧逻辑：尾帧 = 下一帧首帧
                seg["endImagePath"] = key_frame_paths[i + 1]
            # 若既无 end_frame_paths 又无下一帧（最后一段），则降级为 i2v
            if "endImagePath" not in seg:
                print(f"  ⚠ shot {i+1} 标记为 fl2v 但无尾帧，降级为 i2v")
        # i2v 模式：不传 endImagePath，Director 自动识别为 i2v

        segments.append(seg)

    # 统计混合分布
    fl2v_segs = sum(1 for s in segments if "endImagePath" in s)
    i2v_segs = len(segments) - fl2v_segs
    print(f"  → Director segments 混合分布: fl2v={fl2v_segs} 段, i2v={i2v_segs} 段")

    return segments


async def _run_director_async(
    segments: List[dict],
    output_path: str,
    aspect_ratio: str = "9:16",
    progress_callback: Optional[Callable] = None,
    engine: str = "runninghub_director",  # runninghub_director | minimax_full(manxue)
) -> str:
    """提交任务并轮询下载

    引擎：
      - runninghub_director → 导演台工作流（MiniMaxH3Director，支持 t2v/i2v/fl2v/r2v/v2v/rv2v）
      - minimax_full        → manxue 网关（每段单调用 + ffmpeg 拼接）
    """
    def _notify(status, msg):
        if progress_callback:
            try:
                progress_callback(status, 0, msg)
            except Exception:
                pass

    # manxue 网关走单独分支
    if engine == "minimax_full":
        return await _run_minimax_async(
            segments=segments,
            output_path=output_path,
            aspect_ratio=aspect_ratio,
            progress_callback=progress_callback,
        )

    width, height = _ratio_to_director_size(aspect_ratio)
    _notify("submitting", f"提交 Director 任务（{len(segments)} 段，{width}x{height}, engine={engine}）...")
    print(f"  → 提交 Director 任务（{len(segments)} 段，{width}x{height}, engine={engine}）...")

    result = await h3_director_client.submit_h3_director(
        task_type="fl2v",
        segments=segments,
        width=width,
        height=height,
    )
    task_id = result.get("taskId")
    if not task_id:
        raise RuntimeError(f"Director 任务提交失败，无 taskId: {result}")
    print(f"  ✓ 任务已提交: {task_id}")

    _notify("generating", f"Director 生成中（任务 {task_id}）...")
    video_path = await h3_director_client.poll_and_download(
        rh_task_id=task_id,
        output_path=output_path,
        timeout=3600,
        on_progress=lambda e, s, m: _notify("generating", f"[{e}s] {s} {m}"),
    )
    print(f"  ✓ Director 视频下载完成: {video_path}")
    return video_path


async def _run_minimax_async(
    segments: List[dict],
    output_path: str,
    aspect_ratio: str = "9:16",
    progress_callback: Optional[Callable] = None,
    concurrency: int = 2,
) -> str:
    """通过 manxueapi 网关（满血 H3）生成每个片段，再 ffmpeg 拼接

    manxueapi 单次任务支持 5/10/15s 视频，因此：
      1. 并发提交 N 个片段任务
      2. 轮询每个任务并下载
      3. 用 ffmpeg concat 按顺序拼接

    每段根据 segments[i] 字段自动选择模式：
      - referenceVideoPath        → Ref2VA 视频（可叠加 refImagePaths 参考图）
      - startImagePath+endImagePath → FL2V 首尾帧
      - 仅 startImagePath          → I2V 首帧
      - 都没有                     → T2V 纯文本
    """
    # 使用 manxueapi 网关客户端（key 是 manxueapi 的，非 MiniMax 官方）
    from backend.services import manxue_h3_client

    def _notify(status, msg):
        if progress_callback:
            try:
                progress_callback(status, 0, msg)
            except Exception:
                pass

    out_dir = Path(output_path).parent
    segments_dir = out_dir / "minimax_segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)
    n = len(segments)

    _notify("submitting", f"MiniMax 满血 API（manxueapi）：{n} 段，并发上限 {concurrency}...")
    print(f"  → MiniMax 满血 API（manxueapi）：{n} 段，并发上限 {concurrency}")

    async def _gen_one(idx: int) -> str:
        seg = segments[idx]
        duration = seg.get("durationSec", 5)
        # manxueapi 时长限制 5/10/15（客户端会自动规整）
        try:
            duration = int(duration)
        except (TypeError, ValueError):
            duration = 5

        seg_path = segments_dir / f"segment_{idx + 1:02d}.mp4"

        # 模式自动选择
        ref_vid = seg.get("referenceVideoPath")
        if ref_vid:
            gen_mode = "reference"
            reference_videos = [ref_vid]
            reference_images = list(seg["refImagePaths"]) if seg.get("refImagePaths") else None
            first_frame_path = None
            last_frame_path = None
        else:
            gen_mode = "first_last_frame" if seg.get("endImagePath") else "reference"
            reference_videos = None
            reference_images = None
            first_frame_path = seg.get("startImagePath")
            last_frame_path = seg.get("endImagePath")

        async with sem:
            last_err = None
            for attempt in range(1, 4):
                try:
                    _notify("generating", f"片段 {idx + 1}/{n} 生成中（第{attempt}次，{duration}s）")
                    result_path = await manxue_h3_client.generate_video(
                        mode=gen_mode,
                        output_path=str(seg_path),
                        reference_images=reference_images,
                        first_frame_path=first_frame_path,
                        last_frame_path=last_frame_path,
                        prompt=seg.get("prompt", ""),
                        duration=duration,
                        aspect_ratio=aspect_ratio,
                        max_wait=1800,
                        progress_callback=lambda s, p, m: _notify(
                            "generating", f"片段 {idx + 1}/{n}: [{s}] {m}"
                        ),
                        reference_videos=reference_videos,
                    )
                    print(f"  ✓ 片段 {idx + 1:02d} 生成完成: {Path(result_path).name}")
                    return result_path
                except Exception as e:
                    last_err = e
                    print(f"  ⚠ 片段 {idx + 1:02d} 第{attempt}次生成失败: {e}")
                    if attempt < 3:
                        await asyncio.sleep(5 * attempt)
            raise RuntimeError(f"片段 {idx + 1:02d} 3 次重试均失败: {last_err}")

    seg_paths = await asyncio.gather(*[_gen_one(i) for i in range(n)])
    seg_paths = [p for p in seg_paths if p]

    _notify("concatenating", f"ffmpeg 拼接 {len(seg_paths)} 个片段...")
    print(f"  → ffmpeg 拼接 {len(seg_paths)} 个片段...")
    final_path = _concat_segments(list(seg_paths), output_path)
    print(f"  ✓ MiniMax 拼接完成: {final_path}")
    return final_path


def run_replica_director(
    video_path: str,
    timeline: list,
    storyboard_json: list,
    product_image_paths: List[str],
    output_dir: str,
    product_name: str = "",
    aspect_ratio: str = "9:16",
    segment_duration: int = 5,
    progress_callback: Optional[Callable] = None,
    gen_modes: Optional[List[str]] = None,
    engine: str = "runninghub_director",  # runninghub_director | minimax_full(manxue)
    reference_video_paths: Optional[List[Optional[str]]] = None,  # 每段原视频片段路径（用于 ref2va）
) -> dict:
    """复刻模式（Director 版）：原视频切边界帧 → 替换产品 → Director 一次生成

    与 run_replica 的差异：
        - 每段时长用原始秒数（不再映射到 5/10/15）
        - 一次 Director 任务生成完整视频（无 ffmpeg concat）
        - 支持 gen_mode 混合：fl2v 段用首尾帧，i2v 段仅用首帧

    Args:
        engine: 引擎选择
            - runninghub_director: 导演台工作流（MiniMaxH3Director，全模式）
            - minimax_full:        manxue 网关（每段单调用 + ffmpeg 拼接）
        reference_video_paths: 每段对应的原视频片段路径（用于 ref2va 视频传参）；
            若提供且 engine 支持，则该段走 rv2v/ref2va 模式
    """
    print("\n" + "=" * 60)
    print(f"Step5 多镜头生成（复刻模式 · {engine}）")
    print("=" * 60)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    def _notify(status, idx=0, msg=""):
        if progress_callback:
            try:
                progress_callback(status, idx, msg)
            except Exception:
                pass

    # ── 1. 切 N+1 张边界帧 ──
    _notify("extracting", 0, "切取边界帧...")
    print(f"\n[1/4] 切取 {len(timeline) + 1} 张边界帧（共享帧策略）...")

    boundary_frames_dir = out / "boundary_frames"
    boundary_frames_dir.mkdir(parents=True, exist_ok=True)

    video_duration = _get_video_duration(video_path)
    print(f"  → 视频总时长: {video_duration:.1f}s")

    frame_paths = []
    for i, entry in enumerate(timeline):
        t = entry["time"]
        fp = boundary_frames_dir / f"frame_{i:02d}.jpg"
        _extract_frame(video_path, t, fp)
        frame_paths.append(fp)
        print(f"  ✓ frame_{i:02d} @ {t:.1f}s")

    last_t = timeline[-1]["time"] if timeline else 0
    end_t = min(video_duration - 0.3, last_t + 5.0)
    if end_t <= last_t:
        end_t = video_duration - 0.3
    last_fp = boundary_frames_dir / f"frame_{len(timeline):02d}.jpg"
    _extract_frame(video_path, end_t, last_fp)
    frame_paths.append(last_fp)
    print(f"  ✓ frame_{len(timeline):02d} @ {end_t:.1f}s (末帧)")

    # ── 2. 产品替换 N+1 帧 ──
    _notify("replacing", 0, f"替换 {len(frame_paths)} 张边界帧的产品...")
    print(f"\n[2/4] 产品替换 {len(frame_paths)} 张边界帧...")

    replaced_dir = out / "replaced_frames"
    replaced_frames = asyncio.run(_replace_all_frames(
        frame_paths=frame_paths,
        product_image_paths=product_image_paths,
        storyboard_json=storyboard_json,
        output_dir=replaced_dir,
        progress_callback=progress_callback,
    ))
    print(f"  ✓ 全部 {len(replaced_frames)} 帧替换完成")

    # ── 3. 计算每个片段的时长（用 segment_duration，不再映射） ──
    n_segments = len(timeline)
    durations = [segment_duration] * n_segments
    print(f"\n[3/4] 每段时长: {segment_duration}s × {n_segments} 段 = {segment_duration * n_segments}s")

    # ── 4. 构建 Director segments 并提交 ──
    segments = _build_director_segments_replica(
        replaced_frames=replaced_frames,
        storyboard_json=storyboard_json,
        durations=durations,
        prompt_template=SEGMENT_PROMPT_REPLICA,
        product_name=product_name,
        gen_modes=gen_modes,
    )
    # 注入 referenceVideoPath（用于 ref2va 视频传参；导演台 rv2v / manxue 支持）
    if reference_video_paths:
        for i, rv in enumerate(reference_video_paths):
            if i < len(segments) and rv and Path(rv).exists():
                segments[i]["referenceVideoPath"] = rv
                # 同时把产品参考图作为 refImagePaths（rv2v 混合参考）
                if product_image_paths:
                    segments[i]["refImagePaths"] = [
                        p for p in product_image_paths[:1] if p and Path(p).exists()
                    ]
        n_ref = sum(1 for s in segments if s.get("referenceVideoPath"))
        print(f"  → {n_ref} 段含参考视频（ref2va 模式）")

    _notify("generating", 0, f"{engine} 生成 {n_segments} 段视频...")
    print(f"\n[4/5] {engine} 生成 {n_segments} 段视频...")

    final_video = out / "final_video.mp4"
    final_path = asyncio.run(_run_director_async(
        segments=segments,
        output_path=str(final_video),
        aspect_ratio=aspect_ratio,
        progress_callback=progress_callback,
        engine=engine,
    ))

    # ── 5. 删除音轨（Director 各段独立 BGM 不统一，合并阶段统一配乐） ──
    _notify("mixing", 0, "删除音轨...")
    print(f"\n[5/5] 删除音轨（合并阶段统一配 BGM）...")
    final_path = _remove_audio(final_path, out)

    _notify("done", 0, f"视频生成完成: {final_path}")

    result = {
        "status": "success",
        "mode": "replica_director",
        "engine": engine,
        "video": final_path,
        "segments": [final_path],  # Director 是单文件
        "frames": replaced_frames,
        "n_segments": n_segments,
        "output_dir": str(out),
    }

    with open(out / "step5_multi_shot_result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n✓ 复刻模式（{engine}）完成: {final_path}")
    print(f"  片段数: {n_segments}")
    print(f"  输出目录: {out}")
    return result


def run_original_director(
    key_frame_paths: List[str],
    storyboard_json: list,
    product_name: str,
    output_dir: str,
    aspect_ratio: str = "9:16",
    segment_duration: int = 5,
    progress_callback: Optional[Callable] = None,
    end_frame_paths: Optional[List[Optional[str]]] = None,
    gen_modes: Optional[List[str]] = None,
    engine: str = "runninghub_director",  # runninghub_director | minimax_full(manxue)
    reference_video_paths: Optional[List[Optional[str]]] = None,  # 每段参考视频路径（用于 ref2va）
    product_image_paths: Optional[List[str]] = None,  # 产品参考图（rv2v 混合参考时使用）
) -> dict:
    """原创模式（Director 版，支持 gen_mode 混合）：使用已有关键帧 → Director 一次生成

    与 run_original 的差异：
        - 每段时长用原始秒数（不再映射到 5/10/15）
        - 一次 Director 任务生成完整视频（无 ffmpeg concat）
        - 支持 gen_mode 混合：fl2v 段用首尾帧，i2v 段仅用首帧

    Args:
        key_frame_paths: 首帧路径列表（N 个 shot）
        end_frame_paths: 尾帧路径列表（fl2v 段有路径，i2v 段为 None）；None 时退化为全 fl2v 共享帧
        gen_modes: 每段 gen_mode 列表（"fl2v" 或 "i2v"）；None 时退化为全 fl2v
        engine: 引擎选择（runninghub_director | minimax_full）
        reference_video_paths: 每段对应的参考视频路径（用于 ref2va 视频传参）；
            若提供且 engine 支持，则该段走 rv2v/ref2va 模式
        product_image_paths: 产品参考图（rv2v 混合参考时使用，仅取第 1 张）
    """
    print("\n" + "=" * 60)
    print(f"Step5 多镜头生成（原创模式 · {engine}·混合模式）")
    print("=" * 60)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    def _notify(status, idx=0, msg=""):
        if progress_callback:
            try:
                progress_callback(status, idx, msg)
            except Exception:
                pass

    n_frames = len(key_frame_paths)

    # 兼容旧调用：若无 gen_modes，全 fl2v；若有，每 shot 1 段
    if gen_modes is None:
        gen_modes = ["fl2v"] * n_frames

    # 新逻辑：每个 shot 1 段（不再 N-1 共享帧）
    n_segments = n_frames

    if n_frames < 1:
        raise RuntimeError(f"原创模式至少需要 1 张关键帧，当前只有 {n_frames} 张")

    fl2v_count = sum(1 for gm in gen_modes if gm == "fl2v")
    i2v_count = n_segments - fl2v_count
    print(f"  → 关键帧数: {n_frames}")
    print(f"  → 将生成 {n_segments} 个片段（混合模式: fl2v={fl2v_count}, i2v={i2v_count}）")
    print(f"  → 每片段时长: {segment_duration}s")

    for i, p in enumerate(key_frame_paths):
        if not Path(p).exists():
            raise FileNotFoundError(f"首帧不存在: {p}")
    if end_frame_paths:
        for i, p in enumerate(end_frame_paths):
            if p and not Path(p).exists():
                print(f"  ⚠ 尾帧不存在（shot {i+1}）: {p}，该段将降级为 i2v")
                end_frame_paths[i] = None
                if i < len(gen_modes):
                    gen_modes[i] = "i2v"

    durations = [segment_duration] * n_segments

    # ── 1. 构建 Director segments 并提交 ──
    segments = _build_director_segments_original(
        key_frame_paths=key_frame_paths,
        storyboard_json=storyboard_json,
        durations=durations,
        prompt_template=SEGMENT_PROMPT_ORIGINAL,
        product_name=product_name,
        end_frame_paths=end_frame_paths,
        gen_modes=gen_modes,
    )
    # 注入 referenceVideoPath（用于 ref2va 视频传参；导演台 rv2v / manxue 支持）
    if reference_video_paths:
        for i, rv in enumerate(reference_video_paths):
            if i < len(segments) and rv and Path(rv).exists():
                segments[i]["referenceVideoPath"] = rv
                # 同时把产品参考图作为 refImagePaths（rv2v 混合参考）
                if product_image_paths:
                    segments[i]["refImagePaths"] = [
                        p for p in product_image_paths[:1] if p and Path(p).exists()
                    ]
        n_ref = sum(1 for s in segments if s.get("referenceVideoPath"))
        print(f"  → {n_ref} 段含参考视频（ref2va 模式）")

    _notify("generating", 0, f"{engine} 生成 {n_segments} 段视频（混合模式）...")
    print(f"\n[1/2] {engine} 生成 {n_segments} 段视频（混合模式）...")

    final_video = out / "final_video.mp4"
    final_path = asyncio.run(_run_director_async(
        segments=segments,
        output_path=str(final_video),
        aspect_ratio=aspect_ratio,
        progress_callback=progress_callback,
        engine=engine,
    ))

    _notify("done", 0, f"视频生成完成: {final_path}")

    result = {
        "status": "success",
        "mode": "original_director",
        "engine": engine,
        "video": final_path,
        "segments": [final_path],
        "frames": key_frame_paths,
        "n_segments": n_segments,
        "gen_modes": gen_modes,
        "output_dir": str(out),
    }

    with open(out / "step5_multi_shot_result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n✓ 原创模式（{engine}·混合）完成: {final_path}")
    print(f"  片段数: {n_segments}（fl2v={fl2v_count}, i2v={i2v_count}）")
    print(f"  输出目录: {out}")
    return result


def run_director(
    mode: str = "original",
    **kwargs,
) -> dict:
    """Director 满血版统一入口

    Args:
        mode: "replica"（复刻，从原视频切帧）或 "original"（原创，使用关键帧）
        **kwargs: 对应模式的参数（含 engine: runninghub_director | minimax_full）

    引擎说明：
        - runninghub_director: 导演台工作流（MiniMaxH3Director，单任务多段，全模式）
        - minimax_full:        manxue 网关（每段单调用，4-15s 整数，ffmpeg 拼接）
    """
    if mode == "replica":
        return run_replica_director(**kwargs)
    elif mode == "original":
        return run_original_director(**kwargs)
    else:
        raise ValueError(f"未知模式: {mode}，支持 'replica' 或 'original'")
