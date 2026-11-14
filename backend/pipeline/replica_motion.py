"""
爆款复刻-动作迁移 全流程 Pipeline（支持分阶段审核）

输入：原视频 + 新产品图
输出：复刻后的最终视频（产品替换 + 动作迁移）

流程（5步）：
  Step1: MiMo 视频结构解析 → storyboard.json + timeline.json
  Step2: ffmpeg 按分镜切视频片段 + 抽取关键帧
  Step3: image2 逐分镜替换关键帧产品（4 并发）
  ───── 暂停，等待审核（可重做单 shot 关键帧） ─────
  Step4: RunningHub 动作迁移（无产品分镜保留原段，有产品分镜 4 并发）
  Step5: ffmpeg concat 拼接所有片段成最终视频

对外接口：
  run_phase_1(...)      → Step1+2+3，返回待审关键帧
  redo_shot(...)        → 重做单 shot 关键帧替换
  run_phase_2(...)      → Step4+5，返回最终视频
  run_pipeline(...)     → 全流程一气呵成（不分阶段）
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

# 让子进程能 import 项目根
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import settings
from backend.services.gpt_client import gpt_client


# ─────────────────────────────────────────────────────────
# 通用工具
# ─────────────────────────────────────────────────────────
def _save_partial_state(work_dir: Path, payload: dict) -> None:
    """增量保存 state.json（断点续传用）。

    在关键步骤（Step1/Step2/基准图生成后）调用，写入部分状态，
    防止中途中断（服务重启/进程崩溃）导致断点信息丢失。
    已存在的 state.json 会被覆盖（payload 应包含 current_step）。
    """
    try:
        state_path = Path(work_dir) / "state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        # 合并已有 state（如果有），保留之前写入的字段
        existing = {}
        if state_path.exists():
            try:
                existing = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
        existing.update(payload)
        state_path.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"  ⚠ 保存断点状态失败（不阻塞）: {e}", flush=True)


def _to_sec(s: str) -> float:
    """'00:03.50' or '3.5' → 3.5"""
    if not s:
        return 0.0
    s = str(s).strip().rstrip("s")
    if ":" in s:
        m, sec = s.split(":")
        return int(m) * 60 + float(sec)
    return float(s)


def _ffprobe_video_size(video_path: Path) -> tuple[int, int]:
    """返回 (width, height)"""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-of", "default=noprint_wrappers=1", str(video_path)],
        capture_output=True, text=True, check=True
    )
    w = h = 0
    for line in r.stdout.splitlines():
        if line.startswith("width="):
            w = int(line.split("=")[1])
        elif line.startswith("height="):
            h = int(line.split("=")[1])
    return w, h


def _detect_product_shots(storyboard: list) -> tuple[list, set]:
    """从 storyboard 解析：有产品分镜列表 + 无产品分镜集合"""
    all_shots = [s["shot"] for s in storyboard]
    no_product = {
        s["shot"] for s in storyboard
        if s.get("人物与产品", {}).get("产品", "无") == "无"
    }
    product_shots = [n for n in all_shots if n not in no_product]
    return product_shots, no_product


_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _parse_mmss(value: str) -> float | None:
    """MM:SS.xx 或纯秒数 → 秒；解析失败返回 None"""
    try:
        if ":" in value:
            m, rest = value.split(":", 1)
            return int(m) * 60 + float(rest)
        return float(value)
    except (ValueError, AttributeError):
        return None


def _parse_replacement_instructions(product_name: str, storyboard: list) -> tuple[set, set]:
    """解析产品输入框里的分段替换指令（用户会把指令写进产品框）。

    支持「第一段0-5秒不用替换」「第2段5-12秒替换图1」这类写法，
    返回 (强制替换 shots, 强制跳过 shots)。没识别到指令时返回空集，走自动判定。
    指令优先于 MiMo 的"产品"字段判定（穿戴型产品常被误判成"无产品"）。
    """
    force: set = set()
    skip: set = set()
    if not product_name or not storyboard:
        return force, skip
    spans = []
    for s in storyboard:
        t = s.get("时间区间", {})
        t0, t1 = _parse_mmss(t.get("开始时间", "")), _parse_mmss(t.get("结束时间", ""))
        if t0 is not None and t1 is not None:
            spans.append((s["shot"], t0, t1))
    for seg in re.split(r"[，,。；;、\n]+", product_name):
        if "替换" not in seg:
            continue
        to_skip = any(k in seg for k in ("不用替换", "无需替换", "不需替换", "别替换", "不替换"))
        targets: set = set()
        m = re.search(r"第\s*([0-9一二三四五六七八九十]+)\s*段", seg)
        if m:
            token = m.group(1)
            n = int(token) if token.isdigit() else _CN_NUM.get(token)
            if n:
                targets.add(n)
        for rm in re.finditer(r"(\d+(?:\.\d+)?)\s*[-–~—]\s*(\d+(?:\.\d+)?)\s*秒", seg):
            t0, t1 = float(rm.group(1)), float(rm.group(2))
            for shot, s0, s1 in spans:
                if s0 < t1 and s1 > t0:
                    targets.add(shot)
        if not targets and any(k in seg for k in ("都", "全部", "所有", "两段", "整个")):
            targets = {s["shot"] for s in storyboard}  # "全部替换/都不替换" → 所有分镜
        if not targets:
            continue
        (skip if to_skip else force).update(targets)
    force -= skip  # 明确写了"不用替换"的以跳过为准
    return force, skip


# ─────────────────────────────────────────────────────────
# Step1: MiMo 视频结构解析
# ─────────────────────────────────────────────────────────
def step1_parse_storyboard(
    video_path: Path,
    product_name: str,
    product_image_paths: List[str],
    out_dir: Path,
    split_mode: str = "scene_cut",
) -> dict:
    """调用 MiMo-2.5 解析视频，输出 storyboard + timeline

    Args:
        split_mode: 分镜切分模式
            "scene_cut" = 镜头转场切分（默认）
            "action_grained" = 细颗粒度动作切分（产品形态变化时用）
    """
    from pipeline import step1_mimo_parse

    os.environ["MIMO_API_KEY"] = settings.MIMO_API_KEY

    result = step1_mimo_parse.run(
        video_path=str(video_path),
        product_name=product_name,
        product_image_paths=product_image_paths,
        output_dir=str(out_dir),
        split_mode=split_mode,
    )

    storyboard = result["storyboard"]
    timeline = result["timeline"]

    (out_dir / "storyboard_raw.json").write_text(
        json.dumps(storyboard, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "timeline_raw.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 复合边界分镜：场景切换 + 动作边界 + 短段合并(1.5s) + 长段拆分(8s)
    # 一个动作一个分镜，确保动作迁移稳定（每段≤240帧，WanVideo舒适区）
    storyboard, timeline = _align_to_scenecuts(
        video_path, storyboard, timeline, min_duration=1.5, max_duration=8.0
    )

    (out_dir / "storyboard.json").write_text(
        json.dumps(storyboard, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "timeline.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"storyboard": storyboard, "timeline": timeline}


def _ffprobe_duration(video_path: Path) -> float:
    """获取视频时长（秒）"""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
        capture_output=True, text=True, check=True
    )
    return float(r.stdout.strip())


def _detect_scenecuts(video_path: Path, threshold: float = 0.08) -> list:
    """
    用 ffmpeg 检测场景切换点（转场点）。
    返回转场时间点列表（秒），不包含 0 和视频末尾。
    threshold 越低越敏感（0.08 = 较敏感）。
    """
    r = subprocess.run(
        ["ffmpeg", "-i", str(video_path),
         "-filter:v", f"select='gt(scene,{threshold})',showinfo",
         "-an", "-f", "null", "-"],
        capture_output=True, text=True
    )
    cuts = []
    for line in r.stderr.splitlines():
        if "pts_time:" in line:
            try:
                pt = line.split("pts_time:")[1].split()[0]
                cuts.append(float(pt))
            except (ValueError, IndexError):
                continue
    # 去重（相邻 <0.6s 的视为同一转场，合并转场抖动）
    dedup = []
    for c in cuts:
        if not dedup or c - dedup[-1] > 0.6:
            dedup.append(c)
    return dedup


def _filter_false_scenecuts(video_path: Path, cuts: list) -> list:
    """帧级二次验证转场点：对 ffmpeg scene 检测到的每个转场点，
    抽取转场点前后各一帧，用 SSIM + 直方图巴氏距离判断两帧相似度。
    相似度高说明是假转场（固定机位人物走动/转身误报），过滤掉。

    背景：ffmpeg scene 滤镜对画面整体位移敏感，固定机位下人物走动、
    转身、镜头微抖都会触发假转场，导致一个完整 shot 被误拆成两段
    （两段描述完全相同），拼接处出现重复感跳变。

    判定规则（满足任一即判为假转场）：
      - SSIM > 0.70：两帧结构高度相似（真实硬切 SSIM 通常 < 0.5）
      - SSIM > 0.50 且直方图巴氏距离 < 0.15：结构较相似且色彩分布几乎一致

    Args:
        cuts: ffmpeg 检测到的转场时间点列表（秒）
    Returns:
        过滤后的真实转场点列表
    """
    if not cuts:
        return cuts

    try:
        import cv2
        import numpy as np
        from skimage.metrics import structural_similarity as _ssim
    except ImportError:
        print(f"  [Step1] ⚠ cv2/skimage 不可用，跳过帧级转场验证", flush=True)
        return cuts

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"  [Step1] ⚠ 无法打开视频做帧级验证，原样保留转场点", flush=True)
        return cuts

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_offset = 1.0 / fps  # ±1 帧

    def _read_frame_at(ts: float):
        """读取指定时间点的帧（按帧号 seek）"""
        frame_idx = max(0, int(ts * fps))
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        return frame if ret else None

    def _hist_bhatta(img_a, img_b) -> float:
        """HSV 直方图巴氏距离 [0,1]，越小越相似"""
        hsv_a = cv2.cvtColor(img_a, cv2.COLOR_BGR2HSV)
        hsv_b = cv2.cvtColor(img_b, cv2.COLOR_BGR2HSV)
        hist_a = cv2.calcHist([hsv_a], [0, 1], None, [50, 60], [0, 180, 0, 256])
        hist_b = cv2.calcHist([hsv_b], [0, 1], None, [50, 60], [0, 180, 0, 256])
        cv2.normalize(hist_a, hist_a, 0, 1, cv2.NORM_MINMAX)
        cv2.normalize(hist_b, hist_b, 0, 1, cv2.NORM_MINMAX)
        return float(cv2.compareHist(hist_a, hist_b, cv2.HISTCMP_BHATTACHARYYA))

    real_cuts = []
    filtered = 0
    try:
        for c in cuts:
            before = _read_frame_at(c - frame_offset)
            after = _read_frame_at(c + frame_offset)
            if before is None or after is None:
                # 抽帧失败，保守保留转场点
                real_cuts.append(c)
                continue

            # SSIM 需要灰度图
            gray_a = cv2.cvtColor(before, cv2.COLOR_BGR2GRAY)
            gray_b = cv2.cvtColor(after, cv2.COLOR_BGR2GRAY)
            ssim_val = float(_ssim(gray_a, gray_b, data_range=255))
            bhatta = _hist_bhatta(before, after)

            is_false = (ssim_val > 0.70) or (ssim_val > 0.50 and bhatta < 0.15)
            if is_false:
                print(f"  [Step1] 过滤假转场 @ {c:.2f}s "
                      f"(ssim={ssim_val:.3f}, bhatta={bhatta:.3f})", flush=True)
                filtered += 1
            else:
                real_cuts.append(c)
    finally:
        cap.release()

    print(f"  [Step1] 帧级验证: {len(cuts)} 个转场 → 过滤 {filtered} 假转场，"
          f"保留 {len(real_cuts)} 真转场", flush=True)
    return real_cuts


def _collect_action_boundaries(storyboard: list) -> list:
    """从 MiMo storyboard 提取动作边界点（每个 shot 的起止时间）。
    MiMo 按"动作起止"切分（SYSTEM_PROMPT 第②优先级），这些边界即动作边界。
    """
    bounds = []
    for s in storyboard:
        try:
            start = _to_sec(s["时间区间"]["开始时间"])
            end = _to_sec(s["时间区间"]["结束时间"])
            bounds.append(start)
            bounds.append(end)
        except (KeyError, ValueError):
            continue
    return bounds


def _split_long_segments(
    segments: list,
    max_duration: float,
    storyboard: list,
) -> list:
    """拆分 >max_duration 的段：只在 MiMo 动作边界处拆分，无合适边界则保留不拆。

    策略变更：宁可略长也不切碎，保证动作完整性。
    若段内找不到动作边界（与段首尾距离 ≥0.5s），说明 MiMo 认为这是一个完整动作，
    不再强制中点二分（中点拆分会破坏动作连贯性，导致 Step4 动作迁移跳帧）。
    """
    if not segments:
        return []

    # 收集所有动作边界点（用于选择拆分点）
    action_bounds = sorted(set(_collect_action_boundaries(storyboard)))

    result = []
    for seg in segments:
        if seg["duration"] <= max_duration + 0.1:
            result.append(seg)
            continue

        # 需要拆分：在段内找最接近中点的动作边界
        mid = seg["start"] + seg["duration"] / 2
        best_split = None
        best_dist = float("inf")
        for b in action_bounds:
            if seg["start"] + 0.5 < b < seg["end"] - 0.5:
                dist = abs(b - mid)
                if dist < best_dist:
                    best_dist = dist
                    best_split = b

        # 无合适动作边界：保留不拆（保护动作完整性）
        if best_split is None:
            print(f"  [Step1] 段 {seg['start']:.1f}-{seg['end']:.1f}({seg['duration']:.1f}s) "
                  f"超出 {max_duration}s 但无动作边界可拆，保留完整", flush=True)
            result.append(seg)
            continue

        # 递归拆分
        left = {"start": seg["start"], "end": best_split,
                "duration": best_split - seg["start"]}
        right = {"start": best_split, "end": seg["end"],
                 "duration": seg["end"] - best_split}
        result.extend(_split_long_segments([left, right], max_duration, storyboard))

    return result


def _align_to_scenecuts(
    video_path: Path,
    storyboard: list,
    timeline: list,
    min_duration: float = 1.5,
    max_duration: float = 6.0,
) -> tuple:
    """
    复合边界分镜：场景切换 + 动作边界 + 短段合并 + 长段拆分。

    策略（一个动作一个分镜）：
    1. 收集边界点（取并集）：ffmpeg scene 检测 + MiMo storyboard 动作边界
    2. 按边界点切段
    3. 合并 <min_duration 的短段到前一段
    4. 拆分 >max_duration 的长段（优先在动作边界处二分）
    5. 每个新分镜的描述：从原 storyboard 中取时间重叠最大的 shot
    6. 重新生成 timeline（取每个新 shot 的中点）

    Args:
        min_duration: 最短分镜时长（秒），<1.5s 合并。默认1.5s（避免过碎）。
        max_duration: 最长分镜时长（秒），>6.0s 拆分。默认6.0s（WanVideo动作迁移舒适区，约180帧）。
    """
    duration = _ffprobe_duration(video_path)
    cuts = _detect_scenecuts(video_path)
    print(f"  [Step1] ffmpeg 检测到 {len(cuts)} 个转场点: {[f'{c:.2f}' for c in cuts]}")

    # 帧级二次验证：过滤假转场（固定机位人物走动/转身误报）
    cuts = _filter_false_scenecuts(video_path, cuts)
    print(f"  [Step1] 视频时长: {duration:.2f}s")

    # 收集 MiMo 动作边界（每个 shot 的起止时间）
    action_bounds = _collect_action_boundaries(storyboard)
    print(f"  [Step1] MiMo 动作边界: {len(action_bounds)} 个: {[f'{b:.2f}' for b in sorted(set(action_bounds))]}")

    # 构建边界并集：0 → 转场点 + 动作边界 → duration
    boundaries = [0.0] + cuts + action_bounds + [duration]
    boundaries = [b for b in boundaries if 0 <= b <= duration]
    boundaries = sorted(set(boundaries))

    # 生成段：[start, end)
    segments = []
    for i in range(len(boundaries) - 1):
        seg_start = boundaries[i]
        seg_end = boundaries[i + 1]
        seg_dur = seg_end - seg_start
        if seg_dur > 0.05:  # 过滤掉零长段
            segments.append({"start": seg_start, "end": seg_end, "duration": seg_dur})

    print(f"  [Step1] 边界切分: {len(segments)} 段")

    # 合并短段（<min_duration 合并到前一段）
    merged = []
    for seg in segments:
        if merged and seg["duration"] < min_duration:
            merged[-1]["end"] = seg["end"]
            merged[-1]["duration"] = merged[-1]["end"] - merged[-1]["start"]
        elif merged and merged[-1]["duration"] < min_duration:
            merged[-1]["end"] = seg["end"]
            merged[-1]["duration"] = merged[-1]["end"] - merged[-1]["start"]
        else:
            merged.append(dict(seg))

    # 最后一段太短，合并到前一段
    if len(merged) >= 2 and merged[-1]["duration"] < min_duration:
        merged[-2]["end"] = merged[-1]["end"]
        merged[-2]["duration"] = merged[-2]["end"] - merged[-2]["start"]
        merged.pop()

    print(f"  [Step1] 合并短段后: {len(merged)} 段")

    # 拆分长段（>max_duration 优先在动作边界二分）
    if max_duration > 0:
        before = len(merged)
        merged = _split_long_segments(merged, max_duration, storyboard)
        if len(merged) != before:
            print(f"  [Step1] 拆分长段后: {len(merged)} 段")

    print(f"  [Step1] 最终 {len(merged)} 个分镜: "
          f"{[f'{m['start']:.1f}-{m['end']:.1f}({m['duration']:.1f}s)' for m in merged]}")

    # 为每个新分镜匹配原 storyboard 中时间重叠最大的 shot 描述
    new_storyboard = []
    new_timeline = []
    for i, seg in enumerate(merged, start=1):
        seg_start, seg_end = seg["start"], seg["end"]
        # 找原 storyboard 中时间重叠最大的 shot
        best_shot = None
        best_overlap = 0
        for s in storyboard:
            s_start = _to_sec(s["时间区间"]["开始时间"])
            s_end = _to_sec(s["时间区间"]["结束时间"])
            overlap = min(seg_end, s_end) - max(seg_start, s_start)
            if overlap > best_overlap:
                best_overlap = overlap
                best_shot = s

        # 构建新 shot
        if best_shot:
            new_shot = dict(best_shot)
        else:
            new_shot = {"shot": i, "人物与产品": {"产品": "无"}}

        new_shot["shot"] = i
        new_shot["时间区间"] = {
            "开始时间": f"{int(seg_start // 60):02d}:{seg_start % 60:05.2f}",
            "结束时间": f"{int(seg_end // 60):02d}:{seg_end % 60:05.2f}",
            "镜头时长": f"{seg['duration']:.2f}s",
        }
        new_storyboard.append(new_shot)

        # timeline：取该段中点
        mid = seg_start + seg["duration"] / 2
        new_timeline.append({"shot": i, "time": round(mid, 2)})

    return new_storyboard, new_timeline


# ─────────────────────────────────────────────────────────
# Step2: 切片段 + 抽关键帧
# ─────────────────────────────────────────────────────────
async def _pick_best_keyframe(
    seg_path: Path,
    duration: float,
    out_path: Path,
    shot_num: int,
) -> str:
    """从片段抽多帧，用 LLM 选一帧产品不遮挡人物面部/衣服的最佳帧。

    单图工作流（AI App 2068645846073307137）对关键帧质量要求更高：
    必须露出人物面部和上半身衣服，否则动作迁移时人物信息丢失。

    改进：抽 5 帧候选（1/6, 2/6, 3/6, 4/6, 5/6），LLM 严格筛选。

    Returns:
        str: 选中的关键帧路径
    """
    import tempfile

    # 抽 5 帧：1/6, 2/6, 3/6, 4/6, 5/6 位置（比原来 3 帧更多候选）
    offsets = [
        max(0.1, duration * 1/6),
        max(0.1, duration * 2/6),
        max(0.1, duration * 3/6),
        max(0.1, duration * 4/6),
        max(0.1, duration * 5/6),
    ]
    # 最后一个 offset 不能超过 duration
    offsets = [min(o, duration - 0.1) for o in offsets]

    tmp_frames = []
    for i, off in enumerate(offsets):
        tmp = out_path.parent / f"_shot{shot_num:02d}_cand_{i}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{off:.3f}", "-i", str(seg_path),
             "-frames:v", "1", "-q:v", "2", "-update", "1", str(tmp)],
            capture_output=True
        )
        if tmp.exists():
            tmp_frames.append((off, tmp))

    if len(tmp_frames) <= 1:
        # 只有一帧可用，直接用
        if tmp_frames:
            subprocess.run(["cmd", "/c", "copy", str(tmp_frames[0][1]), str(out_path)], capture_output=True) if False else None
            import shutil
            shutil.copy(tmp_frames[0][1], out_path)
        return str(out_path)

    # 用 LLM 选最佳帧
    import base64
    try:
        from backend.services import gpt_client
    except ImportError:
        import shutil
        shutil.copy(tmp_frames[len(tmp_frames) // 2][1], out_path)
        return str(out_path)

    def _to_b64(p: Path) -> str:
        return base64.b64encode(p.read_bytes()).decode()

    images_payload = []
    for i, (off, tmp) in enumerate(tmp_frames):
        images_payload.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{_to_b64(tmp)}"}
        })

    messages = [
        {
            "role": "system",
            "content": (
                "你是视频关键帧选择专家。任务：从多帧中选一帧最适合做产品替换的帧。"
                "核心原则：单图工作流要求关键帧必须露出人物面部和上半身衣服，"
                "否则替换后动作迁移时人物信息丢失。"
            ),
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"以下是同一个分镜的 {len(tmp_frames)} 个候选帧，"
                        f"分别对应时间点 {[f'{off:.1f}s' for off, _ in tmp_frames]}。\n\n"
                        "请选择一帧满足以下条件的最佳帧（必须全部满足）：\n"
                        "1. 【面部可见】人物面部清晰可见，没有被产品或手势遮挡\n"
                        "2. 【衣服露出】人物上半身衣服完整露出（肩膀、胸部、袖子可见）\n"
                        "3. 【产品可见】产品在画面中可见，便于替换\n"
                        "4. 【姿态清晰】人物姿态清晰，适合做动作迁移参考\n\n"
                        "输出格式：帧序号|原因\n"
                        "例如：3|面部清晰，衣服完整露出，产品在手中可见\n\n"
                        "如果所有帧都有遮挡，选遮挡最少的，并在原因中说明。"
                    ),
                },
            ] + images_payload,
        },
    ]

    try:
        result = await asyncio.wait_for(
            gpt_client.chat_completion(messages, temperature=0.1, max_tokens=100),
            timeout=30.0,
        )
        content = result["choices"][0]["message"]["content"].strip()
        # 解析帧序号（格式：序号|原因 或 纯数字）
        import re
        nums = re.findall(r'\d+', content.split('|')[0])
        if nums:
            idx = int(nums[0]) - 1  # 转为 0-based
            if 0 <= idx < len(tmp_frames):
                import shutil
                shutil.copy(tmp_frames[idx][1], out_path)
                reason = content.split('|', 1)[1].strip() if '|' in content else ""
                # 清理临时文件
                for _, tmp in tmp_frames:
                    try: tmp.unlink()
                    except: pass
                print(f"  [关键帧选择] shot {shot_num}: 选中第{idx+1}帧 (offset={tmp_frames[idx][0]:.1f}s) {reason}", flush=True)
                return str(out_path)
    except Exception as e:
        print(f"  [关键帧选择] shot {shot_num}: LLM选择失败({e})，用中间帧", flush=True)

    # 降级：用中间帧
    import shutil
    mid = len(tmp_frames) // 2
    shutil.copy(tmp_frames[mid][1], out_path)
    for _, tmp in tmp_frames:
        try: tmp.unlink()
        except: pass
    return str(out_path)


async def step2_split_segments(
    video_path: Path,
    storyboard: list,
    timeline: list,
    out_dir: Path,
) -> dict:
    """按分镜切视频片段 + 抽关键帧（LLM 选最佳帧，避免产品遮挡人物）"""
    seg_dir = out_dir / "segments"
    kf_dir = out_dir / "keyframes"
    seg_dir.mkdir(parents=True, exist_ok=True)
    kf_dir.mkdir(parents=True, exist_ok=True)

    time_map = {t["shot"]: t["time"] for t in timeline}
    segments = []

    for shot in storyboard:
        n = shot["shot"]
        t = shot["时间区间"]
        start = _to_sec(t["开始时间"])
        end = _to_sec(t["结束时间"])
        duration = end - start
        kf_time = time_map.get(n, start + duration / 2)

        seg_path = seg_dir / f"shot_{n:02d}.mp4"
        kf_path = kf_dir / f"shot_{n:02d}.jpg"
        # 断点续跑：切片和关键帧都已存在则跳过
        if seg_path.exists() and seg_path.stat().st_size > 1024 and kf_path.exists() and kf_path.stat().st_size > 1024:
            print(f"  [Step2] shot {n}: 跳过（切片+关键帧已存在）", flush=True)
            segments.append({"shot": n, "segment": str(seg_path), "keyframe": str(kf_path)})
            continue

        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(video_path),
             "-t", f"{duration:.3f}",
             "-c:v", "libx264", "-crf", "18", "-preset", "fast",
             "-c:a", "aac", "-movflags", "+faststart",
             str(seg_path)],
            check=True, capture_output=True
        )

        # 用 LLM 从 3 个候选帧中选一帧产品不遮挡人物的最佳帧
        await _pick_best_keyframe(seg_path, duration, kf_path, n)
        segments.append({"shot": n, "segment": str(seg_path), "keyframe": str(kf_path)})

    return {"segments": segments, "seg_dir": str(seg_dir), "kf_dir": str(kf_dir)}


# ─────────────────────────────────────────────────────────
# Step3 前置：生成全局基准参考图（固定人脸 + 固定场景）
# 所有 shot 的 Step3 替换共享这两张基准图，跨 shot 锁定人物/场景一致性。
# 原理：Step4 动作迁移只接收 1 张参考图（关键帧），工作流内部"脸部计算"
#       会跟随该参考图；因此只要 Step3 输出的关键帧人脸/场景一致，Step4
#       输出即一致。基准图在 Step3 替换时一并传给 image2 作为锁定锚点。
# ─────────────────────────────────────────────────────────
async def _save_image2_result_to_file(result_str: str, out_path: Path) -> None:
    """把 Image2Client 返回的 url 或 b64_json 保存成文件。"""
    import httpx, base64
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if str(result_str).startswith("http"):
        async with httpx.AsyncClient(timeout=60.0) as dl:
            r = await dl.get(result_str)
            r.raise_for_status()
            out_path.write_bytes(r.content)
    else:
        out_path.write_bytes(base64.b64decode(result_str))


async def _ensure_reference_images(
    video_path: str,
    refs_dir: Path,
    timestamp: float = 1.0,
    product_name: str = "",
) -> dict:
    """从视频抽帧，生成全局固定的参考图（缓存）。

    生成 4 张基准图：
    - face_ref.jpg:    人脸标准图（锁五官/发型/肤色）
    - scene_ref.jpg:   场景空镜图（锁背景/机位/光影）
    - model_full_ref.jpg: 人物全身图（锁衣服款式，解决产品遮挡问题）
    - base_frame.jpg:  抽取的基础帧

    Args:
        video_path: 原视频路径
        refs_dir: 基准图缓存目录（work_dir/refs/）
        timestamp: 抽帧时间点（秒），避开纯黑首帧

    Returns:
        {"face_ref", "scene_ref", "model_full_ref", "base_frame"}
        任一基准图生成失败时对应字段为 None（不阻塞主流程）
    """
    refs_dir.mkdir(parents=True, exist_ok=True)
    face_ref = refs_dir / "face_ref.jpg"
    scene_ref = refs_dir / "scene_ref.jpg"
    model_full_ref = refs_dir / "model_full_ref.jpg"
    base_frame = refs_dir / "base_frame.jpg"
    base_frame_full = refs_dir / "base_frame_full.jpg"

    # 已缓存则直接返回
    if face_ref.exists() and scene_ref.exists() and model_full_ref.exists():
        return {
            "face_ref": str(face_ref),
            "scene_ref": str(scene_ref),
            "model_full_ref": str(model_full_ref),
            "base_frame": str(base_frame) if base_frame.exists() else None,
        }

    # 1. 从视频抽帧（固定时间点）
    if not base_frame.exists():
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(timestamp), "-i", str(video_path),
             "-frames:v", "1", "-q:v", "2", str(base_frame)],
            check=True, capture_output=True
        )
    if not base_frame.exists():
        raise RuntimeError(f"抽帧失败: {video_path} @ {timestamp}s")

    print(f"  [基准图] 基础帧: {base_frame}", flush=True)

    # 1b. 从视频中点抽帧（用于人物全身图，避开首帧产品遮挡）
    if not base_frame_full.exists():
        try:
            duration = _ffprobe_duration(Path(video_path))
            mid_ts = max(0.5, duration * 0.5)
            subprocess.run(
                ["ffmpeg", "-y", "-ss", str(mid_ts), "-i", str(video_path),
                 "-frames:v", "1", "-q:v", "2", str(base_frame_full)],
                check=True, capture_output=True
            )
            print(f"  [基准图] 全身基础帧（视频中点 {mid_ts:.1f}s）: {base_frame_full}", flush=True)
        except Exception as e:
            print(f"  [基准图] ⚠ 抽中点帧失败，复用基础帧: {e}", flush=True)
            base_frame_full = base_frame  # fallback 复用

    # 2. 用 image2 提取人物标准图 + 场景空镜图 + 人物全身图
    from backend.services.image2_client import Image2Client
    import base64 as _b64
    client = Image2Client()
    img_b64 = _b64.b64encode(base_frame.read_bytes()).decode("ascii")

    # 人物全身图用中点帧（避免首帧产品遮挡衣服）
    full_b64 = None
    if base_frame_full and base_frame_full.exists() and base_frame_full != base_frame:
        full_b64 = _b64.b64encode(base_frame_full.read_bytes()).decode("ascii")

    pending = []
    if not face_ref.exists():
        pending.append(("face", face_ref, client.generate_product_image(img_b64, "model")))
    if not scene_ref.exists():
        pending.append(("scene", scene_ref, client.generate_product_image(img_b64, "scene")))
    if not model_full_ref.exists():
        # 优先用中点帧，没有就用首帧
        full_src_b64 = full_b64 if full_b64 else img_b64
        pending.append(("model_full", model_full_ref,
                        client.generate_product_image(full_src_b64, "model_full",
                                                      product_name_hint=product_name)))

    if pending:
        # 打印开始日志，避免用户以为任务卡死（image2 单张图通常 30-60s）
        kind_names = "、".join(t[0] for t in pending)
        print(f"  [基准图] 正在调用 image2 生成 {len(pending)} 张基准图（{kind_names}），预计 1-3 分钟...", flush=True)
        # 限制并发上限，避免同时打多个 image2 请求压垮上游（503/429/408）
        ref_sem = asyncio.Semaphore(2)
        # 每张图独立加超时（180s），避免 image2 异常时无限期挂起
        PER_IMAGE_TIMEOUT = 180
        async def _run_one(kind, target, coro):
            async with ref_sem:
                try:
                    res = await asyncio.wait_for(coro, timeout=PER_IMAGE_TIMEOUT)
                    return (kind, target, res, None)
                except asyncio.TimeoutError:
                    return (kind, target, None, f"超时（{PER_IMAGE_TIMEOUT}s）")
                except Exception as e:
                    return (kind, target, None, str(e))
        results = await asyncio.gather(
            *[_run_one(t[0], t[1], t[2]) for t in pending],
            return_exceptions=False,
        )
        for kind, target, res, err in results:
            if err is not None:
                print(f"  [基准图] ⚠ 生成 {kind} 失败（不阻塞）: {err}", flush=True)
                continue
            try:
                await _save_image2_result_to_file(res, target)
                print(f"  [基准图] 生成 {kind}: {target}", flush=True)
            except Exception as e:
                print(f"  [基准图] ⚠ 保存 {kind} 失败: {e}", flush=True)

    return {
        "face_ref": str(face_ref) if face_ref.exists() else None,
        "scene_ref": str(scene_ref) if scene_ref.exists() else None,
        "model_full_ref": str(model_full_ref) if model_full_ref.exists() else None,
        "base_frame": str(base_frame),
    }


# ─────────────────────────────────────────────────────────
# Step3: image2 逐分镜替换关键帧产品
# ─────────────────────────────────────────────────────────
REPLACEMENT_PROMPT = """对画面中的产品进行替换生成，严格保持人物、背景、场景完全不变，仅替换产品。

====================
第0步：原图识别（必须先完成，再替换）
====================
在替换前，必须先仔细识别第1张原图画面，充分判断以下信息：
- 原产品的种类、颜色、材质、形状、结构
- 原产品在画面中的位置、数量、堆叠方式、展开/折叠状态
- 原产品与人物的关系（手持/摆放/悬挂）及交互方式
- 人物动作、手势、与产品的接触点
- 背景场景的所有物品（货架、桌面、其他塑料袋、装饰物等）
- 画面中的字幕、文字、贴纸、水印位置
识别完成后，按原产品的位置/数量/形态/堆叠方式，用第2张图的新产品精准替换。
严禁凭空臆造产品形态，必须基于原图实际识别结果进行替换。

====================
最高优先级：三大一致性（缺一不可）
====================
本任务必须同时保证以下 3 项一致性，任何一项发生变化都视为失败：

1.【人物一致性·最严格】
   - 严格保留第1张图中人物的：发型、发色、五官、肤色、表情
   - 严格保留人物的：服装款式、颜色、图案、材质、褶皱
   - 严格保留人物的：姿势、动作、手部位置、朝向
   - 严格保留人物的：在画面中的位置、大小、占比
   - 禁止改变人物任何特征，禁止换装、换发型、换表情

2.【背景一致性·最严格】
   - 严格保留第1张图的：场景结构、家具、物品、装饰
   - 严格保留背景的：色调、光影、纹理、透视
   - 严格保留背景的：所有物品、道具、货架、商品（含原画面中其他塑料袋/袋子/包装）
   - 严格保留背景的：景深、虚化程度
   - 无痕删除原视频画面中的所有字幕、文字、贴纸、水印、LOGO
     （删除区域用周围背景自然修复，不得留白/留痕/变形）
   - 禁止增删背景物品，禁止改变背景色调

3.【产品替换】
   - 仅将画面中的原产品替换为第2张图中的新产品
   - 新产品的外观（颜色、材质、图案、结构）严格参考第2张图
   - 新产品必须保持原产品在画面中的：位置、大小、数量、堆叠方式、展开状态、手持方式

====================
强约束
====================
1. 严格保留参考帧的：
   - 构图（人物和产品在画面中的位置、大小）
   - 光照（光源方向、阴影、高光）
   - 相机角度（透视、视角）

2. 严格参考产品图：
   - 产品形态、结构、颜色、材质、图案
   - 比例关系

3. 替换后必须：
   - 人物特征零变化（发型/服装/姿势/表情完全一致）
   - 背景零变化（场景/物品/色调完全一致）
   - 仅产品外观按第2张图更新
   - 阴影方向与原场景一致
   - 边缘无拼接痕迹

====================
禁止事项
====================
- 禁止改变人物任何特征
- 禁止改变背景任何元素
- 禁止改变构图、景别、运镜
- 禁止生成新的人物或物品
- 禁止改变服装、发型、配饰
- 仅允许替换产品外观

====================
输入图说明
====================
- 第1张图：原始视频帧（人物/背景/构图/光照基准，必须严格保留）
- 第2张图：新产品参考图（仅参考产品外观）"""


def _build_shot_prompt(shot_desc: Optional[dict] = None, force_inject_product: bool = False, product_name: str = "") -> str:
    """根据 storyboard 的分镜描述构造定制化替换 prompt

    force_inject_product: 原"无产品"分镜被 redo 时传 True，产品替换指导改为
        "将新产品植入画面合适位置"而非"保持原产品数量"（原产品=无→数量0→不替换）。
    product_name: 新产品名称，注入产品替换指导，让 image2 明确新产品是什么。
    """
    base = REPLACEMENT_PROMPT
    if not shot_desc:
        return base
    # 从 storyboard 提取该 shot 的人物、场景、产品形态描述
    scene = shot_desc.get("画面动作", {})
    content = scene.get("画面内容", "")
    prod_action = scene.get("产品动作", "")
    people = shot_desc.get("人物与产品", {})
    prod_orig = people.get("产品", "")
    person_desc = people.get("人物", "")
    person_action = scene.get("人物动作", "")
    env = shot_desc.get("场景环境", {})
    scene_place = env.get("场景", "")
    bg_detail = env.get("背景细节", "")
    lighting = env.get("光影设定", "")
    camera = shot_desc.get("镜头语言", {})
    framing = camera.get("景别", "")
    composition = camera.get("构图", "")

    # 产品替换指导：force_inject_product 时改为"植入"而非"替换"（原产品=无）
    # product_name 注入：明确告诉 image2 用户指定要在原画面中查找并替换的目标产品
    product_name_line = (
        f"\n用户指定要在原画面中查找并替换的目标产品：{product_name}"
        if product_name else ""
    )
    if force_inject_product:
        product_guidance = f"""【产品植入指导】（原画面无产品，需植入新产品）
原画面产品：无（该分镜原本不含产品）
原画面内容：{content}
用户指定的目标产品：{product_name or "（未提供，请从第2张图识别）"}{product_name_line}
→ 原画面中没有产品，需将第2张图中的新产品植入到画面中合适的位置
→ 根据新产品类型和画面场景，判断最自然的植入方式：
  · 若产品为鞋类→穿在人物脚上；若为包类→手持或背挎；若为服饰→穿在人物身上
  · 若为食品/日用品→手持或放置在画面中合适的台面/桌面
  · 若无法判断合适位置→手持展示
→ 植入产品后保持人物动作/姿势/表情/位置不变，仅增加产品元素
→ 新产品外观（颜色、材质、图案、结构）严格参考第2张图
→ 植入位置必须自然合理，不得遮挡人物面部，不得悬浮/穿模"""
    else:
        product_guidance = f"""【产品替换指导】
原画面内容（描述的是原视频）：{content}
用户指定的目标产品：{product_name or "（未提供，请从第2张图识别）"}{product_name_line}
原产品动作/形态（描述的是原视频产品）：{prod_action}
→ ⚠️ 用户指定的「{product_name or "目标产品"}」是用户要在【原画面】中查找并替换的目标。
→ 严禁替换原画面中除「{product_name or "目标产品"}」以外的任何其他物品
  （即使 LLM 识别到扳手/工具盒/手袋等其他物品，也禁止替换它们）。
→ 原画面中的目标产品位置以「本分镜识别结果」LLM 分析为准（LLM 已在原图中
  定位到「{product_name or "目标产品"}」并描述其位置、形态、与人物的交互）。
→ 用第2张图中的新产品替换原画面中识别到的「{product_name or "目标产品"}」，
  保持目标产品的【数量、堆叠方式、展开状态、手持方式】。
→ 新产品外观（颜色、材质、图案、结构）严格参考第2张图。"""

    extra = f"""

====================
本分镜一致性锁定（必须严格遵循，否则视为失败）
====================

【人物锁定】
人物描述：{person_desc}
人物动作：{person_action}
→ 必须严格保留该人物的发型、服装、姿势、表情、位置，零变化

【背景锁定】
场景：{scene_place}
背景细节：{bg_detail}
光影：{lighting}
景别/构图：{framing} / {composition}
→ 必须严格保留该场景所有元素、色调、光影，零变化

【景别与人物距离锁定】（最高优先级，禁止任何变化）
景别：{framing}
→ 人物距离镜头的远近、人物在画面中的占比、人物顶/底边界位置、
  人物在画面中的水平位置（中心/偏左/偏右）必须与第1张原帧1:1像素级一致
→ 禁止拉近/推远/缩放/平移人物，禁止改变人物大小
→ 替换后人物轮廓bbox应与原帧人物轮廓bbox重合（允许±2%误差）

{product_guidance}

====================
最终自检（生成前确认）
====================
□ 人物发型/服装/姿势/表情是否与第1张图完全一致？
□ 背景场景/物品/色调是否与第1张图完全一致？
□ 构图/景别/光照是否与第1张图完全一致？
□ 人物距离/画面占比/在画面中的位置是否与第1张图1:1一致？（禁止拉近/推远/缩放）
□ 仅产品外观按第2张图更新？
五项全满足才可输出，任一不满足必须重新生成。
"""
    return base + extra


# 历史关键词法已废弃，LLM 现在按结构化格式输出 FOUND_TARGET_PRODUCT=YES/NO 标志位
# 保留常量仅供向后兼容性引用
_NO_PRODUCT_FOUND_MARKERS = (
    "未在画面中找到",
    "未找到",
    "画面中未发现",
    "未发现",
    "未出现",
    "不存在",
    "无法识别到",
    "看不到",
    "画面中没有",
    "无目标产品",
    "未识别到",
    "找不到",
)


def _is_target_product_not_found(llm_analysis: str) -> bool:
    """判断 LLM 分析结果是否表示画面中未找到用户指定的目标产品。

    优先解析 LLM 输出开头的结构化标志位 `FOUND_TARGET_PRODUCT=YES|NO`，
    仅在缺失标志位时降级用关键词法（用于兼容旧任务/旧 prompt 的响应）。
    """
    if not llm_analysis:
        return False
    text = llm_analysis.strip()

    # 1) 优先：解析 FOUND_TARGET_PRODUCT=YES/NO 标志位
    #    匹配前 500 字符，避免 LLM 把标志位埋在末尾
    head = text[:500]
    m = re.search(r"FOUND_TARGET_PRODUCT\s*[:=]\s*([A-Za-z]+)", head, re.IGNORECASE)
    if m:
        flag = m.group(1).strip().upper()
        if flag in ("NO", "FALSE", "N"):
            return True
        if flag in ("YES", "TRUE", "Y"):
            return False
        # 未识别值 → 继续走降级

    # 2) 降级：旧 prompt 响应或 LLM 未遵守格式 → 关键词法
    #    只在前 800 字符（第1-2节）内检测"未找到"类关键词，
    #    避免第5节"字幕位置"描述中的"未发现字幕"误触发
    head800 = text[:800]
    found_marker = any(m in head800 for m in _NO_PRODUCT_FOUND_MARKERS)
    if not found_marker:
        return False
    # 反向校验：若第1节明确写出找到/识别到目标产品，
    # 则视为"找到了"，不跳过替换
    positive_markers = (
        "已找到",
        "已识别到",
        "成功识别",
        "成功找到",
        "识别到目标产品",
        "定位到目标",
        "找到目标产品",
        "画面中找到指定产品",
        "画面底部可见",
        "画面中可见",
        "可见一块",
        "可见一个",
        "可见一张",
    )
    if any(m in head800 for m in positive_markers):
        return False
    return True


async def _analyze_keyframe_for_replacement(
    original_frame: Path,
    product_image: Path,
    shot_desc: Optional[dict] = None,
    force_inject_product: bool = False,
    product_name: str = "",
    user_feedback: str = "",
) -> str:
    """LLM 视觉分析：仔细识别原帧画面，生成针对该 shot 的精准替换指导。

    在 image2 替换前调用，让多模态 LLM 看懂原图（产品位置/形态/数量/堆叠、
    人物动作/手势/接触点、背景物品、字幕位置），输出针对该画面的替换指令。
    该指令会拼接到 REPLACEMENT_PROMPT 前，作为"本分镜识别结果"传给 image2。

    force_inject_product: 原"无产品"分镜被 redo 时传 True，LLM 分析 prompt 改为
        "识别画面中适合植入产品的位置"而非"识别原产品"。
    product_name: 新产品名称（如"地垫""手提包""硅胶贴片"）。注入 LLM prompt，
        让 LLM 明确知道新产品是什么，避免仅靠产品图猜测导致识别/替换错误。
    user_feedback: 用户审核修改意见（如"产品颜色偏深""缩小产品占比""去除右下角文字"）。
        传入时 LLM 会先理解用户意图，再生成针对性分析，调整 image2 替换提示词的方向。

    Returns:
        str: LLM 生成的本分镜替换指导文本（中文）。失败时返回空串降级。
    """
    import base64
    from PIL import Image as _PILImage
    import io as _io

    def _to_b64(p: Path, max_width: int = 768) -> str:
        """读图并压缩到 max_width 宽度（JPEG 质量 85），减小 base64 体积降低 LLM 服务器压力"""
        try:
            im = _PILImage.open(p).convert("RGB")
            if im.width > max_width:
                new_h = int(im.height * max_width / im.width)
                im = im.resize((max_width, new_h), _PILImage.LANCZOS)
            buf = _io.BytesIO()
            im.save(buf, format="JPEG", quality=85)
            return base64.b64encode(buf.getvalue()).decode()
        except Exception:
            # PIL 失败时降级直接读字节
            return base64.b64encode(p.read_bytes()).decode()

    try:
        ob = _to_b64(original_frame)
        pb = _to_b64(product_image)
    except Exception as e:
        print(f"  [LLM分析] 图片读取失败: {e}")
        return ""

    shot_hint = ""
    if shot_desc:
        scene = shot_desc.get("画面动作", {})
        people = shot_desc.get("人物与产品", {})
        # 注意：storyboard 的"产品"字段填的是新产品名（设计如此），不是原产品名
        # 不能作为"原产品"传给 LLM，否则 LLM 会以为原画面就是新产品，导致识别错误
        shot_hint = (
            f"分镜描述参考（辅助理解，以图为准）：\n"
            f"- 画面内容：{scene.get('画面内容', '')}\n"
            f"- 人物动作：{scene.get('人物动作', '')}\n"
            f"- 产品动作/形态：{scene.get('产品动作', '')}\n"
            f"- 人物：{people.get('人物', '')}\n"
        )

    # 用户指定目标产品名注入：明确告诉 LLM 在【第1张原图】中查找该产品
    # （而非让 LLM 自由识别原画面里的其他物品作为待替换目标）
    product_name_hint = ""
    if product_name:
        product_name_hint = (
            f"\n⚠️ 用户指定要在【第1张原图】中查找的目标产品：「{product_name}」\n"
            f"→ 必须在第1张原图中识别「{product_name}」的位置、形态、与周围元素的关系\n"
            f"→ 即使画面中存在其他物品（扳手/工具盒/手袋等），也仅识别「{product_name}」\n"
            f"→ 第2张图是用于替换该「{product_name}」的新产品图（保持新产品真实形态）\n"
        )

    # 用户审核修改意见注入：LLM 先理解用户意图，再针对性生成分析
    user_feedback_hint = ""
    if user_feedback and user_feedback.strip():
        user_feedback_hint = (
            f"\n⚠️ 用户审核修改意见（必须严格执行，优先级最高）：\n{user_feedback.strip()}\n"
            f"→ 请先理解用户意图，再针对第1张原图生成针对性分析，"
            f"调整 image2 替换提示词的方向（如用户要求缩小产品占比，"
            f"就在「替换指导」中明确产品在画面中的目标占比）\n"
            f"→ 用户意见与本分镜描述冲突时，以用户意见为准\n"
        )

    if force_inject_product:
        analysis_prompt = (
            "第1张图：原视频关键帧（该分镜原本无产品，需植入新产品）。\n"
            "第2张图：新产品图（需要植入到画面中）。\n\n"
            f"{shot_hint}{product_name_hint}{user_feedback_hint}\n"
            "请仔细识别第1张原图，按以下格式输出（必须严格遵守）：\n"
            "首先在第一行输出标志位：`FOUND_TARGET_PRODUCT=YES`（植入模式固定为 YES）\n"
            "然后输出以下分析：\n"
            "1. 【画面识别】画面中的人物、场景、物品；人物动作/姿势/手势；"
            "人物在画面中的占比和位置\n"
            "2. 【人物距离与景别】人物距离镜头的远近（近景/中景/远景）、"
            "人物在画面中的位置（中心/偏左/偏右）、人物顶/底边界位置\n"
            "3. 【背景物品】画面中所有物品，逐一列出\n"
            "4. 【字幕位置】画面是否有字幕/文字/贴纸/水印\n"
            "5. 【产品植入指导】根据第2张新产品图的类型和第1张原图的画面场景，"
            "判断新产品应植入到画面中的什么位置、以什么方式呈现：\n"
            "  · 识别新产品类型（鞋/包/服装/食品/日用品等）\n"
            "  · 根据产品类型和画面中人物的动作/身体部位，判断最自然的植入方式"
            "（如看到脚→穿鞋，看到手→手持，看到桌面→放置）\n"
            "  · 给出具体的植入位置、角度、与人物的交互方式\n"
            "  · 植入后保持人物动作/姿势/背景不变\n"
            "  · 人物距离/画面占比/景别必须与原帧1:1一致\n\n"
            "用简明中文输出，每节2-4句，不要冗长。"
        )
    else:
        analysis_prompt = (
            "第1张图：原视频关键帧（含用户指定的待替换目标产品）。\n"
            "第2张图：新产品图（用于替换第1张图中的目标产品）。\n\n"
            f"{shot_hint}{product_name_hint}{user_feedback_hint}\n"
            "⚠️ 严格区分原帧 vs 新产品特征（最高优先级禁令，违反即废）：\n"
            "  · 原帧的特征（如黑色背垫/网孔/特定纹路）只属于原帧，禁止硬塞到新产品描述里\n"
            "  · 新产品的特征必须仅基于【第2张图】实际可见的内容描述\n"
            "  · 如果原帧有某特征但新产品图没有（如原帧书包有背垫，新产品图无背垫），"
            "必须明确写「新产品无此特征，替换后不要保留该特征」\n"
            "  · 反之，如果新产品图有某特征但原帧没有（如新产品图有挂牌，原帧无），"
            "必须明确写「新产品有此特征，按新产品图如实呈现」\n"
            "  · 严禁出现「保留新产品的XX」这种把原帧特征误判为新产品的措辞\n\n"
            "请仔细识别第1张原图，按以下格式输出（必须严格遵守）：\n"
            "首先在第一行输出标志位（仅 YES 或 NO，决定后续是否替换）：\n"
            f"  · `FOUND_TARGET_PRODUCT=YES` ← 画面中【确实】出现了用户指定的目标产品「{product_name}」\n"
            f"  · `FOUND_TARGET_PRODUCT=NO`  ← 画面中【没有】出现「{product_name}」（如画面里只有扳手/工具盒，"
            "没看到任何地毯）\n"
            "判定准则：以用户指定的目标产品是否在画面中实际可见为准，"
            "禁止因其他物品（扳手/工具盒）存在而判 YES，"
            "也禁止因画面中字幕/文字/工具/手等元素存在而误判 NO（只要地毯等目标产品可见就 YES）。\n"
            "然后输出以下分析：\n"
            "1. 【用户指定目标产品识别】在画面中查找用户指定的目标产品"
            f"「{product_name or '（用户未指定，请识别画面中最突出的产品）'}」，"
            "详细识别该产品的颜色、形状、材质；在画面中的位置、数量、堆叠方式、"
            "展开/折叠状态；与人物/背景的接触关系。\n"
            "  ⚠️ 即使画面中存在其他物品（如扳手、工具盒、手袋、瓶罐等），"
            f"也仅识别用户指定的「{product_name or '目标产品'}」作为待替换目标，"
            "禁止把其他物品当作待替换目标。\n"
            "2. 【人物交互】人物动作、手势、与目标产品的接触点、"
            "目标产品被拿持/放置的方式\n"
            "3. 【人物距离与景别】人物在画面中的占比（如占画面高度几分之几）、"
            "人物距离镜头的远近（近景/中景/远景）、人物在画面中的位置（中心/偏左/偏右）、"
            "人物顶/底边界在画面中的大致位置\n"
            "4. 【背景物品】画面中除人物和目标产品外的所有物品"
            "（桌面商品、其他塑料袋、货架、装饰物等），逐一列出（这些物品保持不变，"
            "禁止替换）\n"
            "5. 【字幕/文字识别】画面是否有字幕/标题/说明文字/贴纸/Logo/水印/弹幕/字幕条，"
            "详细描述位置（如右下角/底部中央/顶部左侧）、大小、颜色、覆盖范围；"
            "若无任何文字，明确写「无字幕」\n"
            "6. 【替换指导】基于以上识别，给出精准替换指令："
            f"将画面中识别到的目标产品「{product_name or '目标产品'}」用第2张图的新产品替换，"
            "新产品放在目标产品的位置、以什么形态/数量/堆叠方式呈现；"
            "如何保持人物动作、手势、背景物品不变；"
            "⚠️ 在写【替换指导】时，必须严格区分："
            "  - 「原帧特征」描述原帧产品的特征（如黑色背垫/网孔/纹路）；"
            "  - 「新产品特征」仅描述第2张新产品图实际可见的特征；"
            "  - 在指导 image2 替换时，新产品的外观必须【完全以第2张图为准】，"
            "禁止说「保留新产品的黑色背垫」这种把原帧特征误判为新产品的措辞；"
            "  - 如果新产品图没有原帧的某特征，必须明确写「新产品无XX，替换后不要保留XX」；"
            "  - 如果新产品图有原帧没有的特征，必须明确写「按新产品图如实呈现XX」。\n"
            "⚠️ 必须消除原帧中的所有字幕/文字/贴纸/水印，用周围背景像素自然填充字幕区域，"
            "不得在新关键帧中保留或新增任何文字；"
            "特别强调：人物距离/画面占比/景别/在画面中的位置必须与原帧1:1一致，"
            "禁止拉近/推远/改变人物大小；禁止替换未识别为目标产品的其他物品\n\n"
            "用简明中文输出，每节2-4句，不要冗长。"
        )

    messages = [
        {
            "role": "system",
            "content": (
                "你是工业级图像分析师，负责在产品替换前对原视频帧做精准识别。"
                "仔细观察原图，识别所有视觉元素，输出简明精准的替换指导。"
                "禁止臆造，只描述图中实际可见的内容。"
            ),
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": analysis_prompt,
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{ob}"},
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{pb}"},
                },
            ],
        },
    ]

    try:
        result = await asyncio.wait_for(
            gpt_client.chat_completion(messages, temperature=0.3, max_tokens=1200),
            timeout=300.0,
        )
        content = result["choices"][0]["message"]["content"].strip()
        print(f"  [LLM分析] 完成，{len(content)} 字")
        return content
    except asyncio.TimeoutError:
        print(f"  [LLM分析] 超时(120s)降级，跳过分析")
        return ""
    except Exception as e:
        print(f"  [LLM分析] 失败降级: {e}")
        return ""


async def _rewrite_storyboard_json_for_new_product(
    storyboard_json: list,
    product_image: Path,
    product_name: str = "",
) -> list:
    """LLM 看新产品图 + 旧 storyboard JSON，重写描述产品特征的字段，使其与新产品图一致。

    背景：Step1 MiMo 输出的 storyboard JSON 描述的是原视频中的产品特征（如"蜂窝背垫"），
    而 Step3 已替换产品（新产品图无背垫）。Step4 收到矛盾输入导致故事板画面与替换分镜脱节。
    本函数在 Step3 替换后调用，让 LLM 根据新产品图实际特征重写 JSON 中描述产品特征的字段，
    保证 Step4 收到的 JSON 与替换后的分镜拼图一致。

    仅重写以下字段（保留所有结构字段不变：shot/gen_mode/时间区间/景别/构图/运镜/转场衔接/音效/口播等）：
      - 画面动作.画面内容
      - 画面动作.产品动作
      - 人物与产品.产品
      - 场景环境.背景细节（如提到原产品特征则修正）

    Args:
        storyboard_json: Step1 输出的原始分镜 JSON（list of dict）
        product_image: 新产品图路径（必选）
        product_name: 新产品名称（如"书包""手提包""硅胶贴片"）

    Returns:
        list: 重写后的 storyboard JSON。失败时返回原 JSON（降级）。
    """
    import base64 as _b64
    import json as _json
    from PIL import Image as _PILImage
    import io as _io

    def _to_b64(p: Path, max_width: int = 768) -> str:
        try:
            im = _PILImage.open(p).convert("RGB")
            if im.width > max_width:
                new_h = int(im.height * max_width / im.width)
                im = im.resize((max_width, new_h), _PILImage.LANCZOS)
            buf = _io.BytesIO()
            im.save(buf, format="JPEG", quality=85)
            return _b64.b64encode(buf.getvalue()).decode()
        except Exception:
            return _b64.b64encode(p.read_bytes()).decode()

    if not product_image or not Path(product_image).exists():
        print("  [JSON重写] 未找到产品图，跳过重写")
        return storyboard_json

    try:
        pb = _to_b64(Path(product_image))
    except Exception as e:
        print(f"  [JSON重写] 产品图读取失败: {e}")
        return storyboard_json

    # 仅传新产品图 + 旧 JSON 文本，让 LLM 根据新产品图实际特征重写描述字段
    old_json_text = _json.dumps(storyboard_json, ensure_ascii=False, indent=2)
    product_name_hint = f"用户指定目标产品名：「{product_name}」\n" if product_name else ""

    rewrite_prompt = (
        "第1张图：新产品图（Step3 已用此图替换原视频中的产品，请基于此图实际可见的特征重写 JSON）。\n"
        f"旧分镜 JSON（描述的是原视频中的产品，与新产品可能不一致）：\n{old_json_text}\n\n"
        f"{product_name_hint}"
        "⚠️ 任务（最高优先级禁令，违反即废）：\n"
        "  · 仅基于【第1张新产品图】实际可见的特征重写 JSON 中描述产品特征的字段\n"
        "  · 旧 JSON 中描述原产品特征的字段（如'蜂窝背垫''黑色插扣'等），"
        "若新产品图无此特征，必须删除该描述，不得保留\n"
        "  · 若新产品图有新特征（如挂牌、不同颜色、不同结构），必须如实写入新 JSON\n"
        "  · 严禁把旧 JSON 中原帧特征硬塞到新产品描述里\n"
        "  · 严禁改变 JSON 结构（字段名、字段顺序、Shot 数量、时间区间、景别、构图、运镜、"
        "转场衔接、音效、口播、声音语气、面部微表情 等结构字段必须原样保留）\n\n"
        "✅ 允许重写的字段（仅以下字段，其他字段原样保留）：\n"
        "  - 画面动作.画面内容（如旧 JSON 写'展示蜂窝背垫'但新产品无背垫，改为'展示书包背面'）\n"
        "  - 画面动作.产品动作（同步修正，如'背垫被按压'改为'背面被展示'）\n"
        "  - 人物与产品.产品（如旧 JSON 写'书包（带蜂窝背垫）'改为'书包（无背垫）'或仅写'书包'）\n"
        "  - 场景环境.背景细节（仅当描述包含原产品特征时修正）\n\n"
        "❌ 禁止重写的字段（原样保留，不动一个字符）：\n"
        "  - shot / gen_mode / 时间区间 / 镜头语言.景别/构图/运镜/转场衔接\n"
        "  - 音效 / 口播 / 声音语气 / 面部微表情 / 画面动作.人物动作\n\n"
        "输出格式（必须严格遵守）：\n"
        "仅输出重写后的完整 JSON 数组，不要输出任何解释文字、不要 markdown 代码块、"
        "不要前后空行。第一个字符必须是 [，最后一个字符必须是 ]。"
        "JSON 中所有字段名、嵌套结构、Shot 数量必须与输入完全一致，仅允许修改上述 4 个字段的值。"
    )

    messages = [
        {
            "role": "system",
            "content": (
                "你是工业级 JSON 重写器，负责根据新产品图实际特征重写分镜 JSON 中描述产品特征的字段。"
                "严格保留 JSON 结构，仅重写指定字段。禁止臆造，只基于图中实际可见的内容描述。"
            ),
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": rewrite_prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{pb}"}},
            ],
        },
    ]

    try:
        result = await asyncio.wait_for(
            gpt_client.chat_completion(messages, temperature=0.2, max_tokens=4000),
            timeout=180.0,
        )
        content = result["choices"][0]["message"]["content"].strip()
        # 容错：剥离 markdown 代码块
        if content.startswith("```"):
            content = content.split("```")
            # 第 2 段是代码内容
            content = content[1] if len(content) > 1 else ""
            if content.startswith("json"):
                content = content[4:]
        content = content.strip()
        if not (content.startswith("[") and content.endswith("]")):
            print(f"  [JSON重写] 输出不是 JSON 数组，降级返回原 JSON。首字符: {content[:30]!r}")
            return storyboard_json
        new_json = _json.loads(content)
        if not isinstance(new_json, list) or len(new_json) != len(storyboard_json):
            print(f"  [JSON重写] Shot 数量不一致（{len(new_json) if isinstance(new_json, list) else '非list'} vs {len(storyboard_json)}），降级返回原 JSON")
            return storyboard_json
        print(f"  [JSON重写] 完成，{len(new_json)} 个 Shot")
        return new_json
    except asyncio.TimeoutError:
        print("  [JSON重写] 超时(180s)降级，返回原 JSON")
        return storyboard_json
    except Exception as e:
        print(f"  [JSON重写] 失败降级: {e}")
        return storyboard_json


async def _replace_one_keyframe(
    client,
    shot_num: int,
    in_frame: Path,
    product_image: Path,
    out_frame: Path,
    shot_desc: Optional[dict] = None,
    face_ref: Optional[Path] = None,
    scene_ref: Optional[Path] = None,
    model_full_ref: Optional[Path] = None,
    force_inject_product: bool = False,
    product_name: str = "",
    video_width: int = 0,
    video_height: int = 0,
) -> dict:
    """替换单个关键帧

    face_ref/scene_ref/model_full_ref: 全局基准图（跨 shot 共享），锁定人物脸型/场景/衣服一致性。
        传给 image2 作为额外参考图，所有 shot 共用同一张，保证拼接后无跳变。
        model_full_ref: 人物全身图，特别用于解决原帧中产品遮挡衣服的问题。
    force_inject_product: 原"无产品"分镜被 redo 时传 True，让 LLM 识别画面后
        判断产品植入位置（如脚上/手上），而非因产品="无"跳过替换。
    product_name: 新产品名称，注入 LLM 分析 prompt 和 image2 替换 prompt，
        让模型明确新产品是什么，避免仅靠产品图猜测导致识别/替换错误。
    video_width/video_height: 原视频实际尺寸，用于按比例选 image2 输出尺寸，
        避免方形原帧被强塞竖屏导致画面崩坏。
    """
    import httpx
    from PIL import Image

    if not in_frame.exists():
        return {"shot": shot_num, "status": "skip", "reason": "原帧不存在"}

    # 留档原帧
    orig_bak = out_frame.parent / f"shot_{shot_num:02d}_original.jpg"
    if not orig_bak.exists() and in_frame.exists():
        shutil.copy(in_frame, orig_bak)

    # 删除旧结果（重做场景）
    if out_frame.exists():
        try:
            out_frame.unlink()
        except Exception:
            pass

    final_prompt = _build_shot_prompt(shot_desc, force_inject_product=force_inject_product, product_name=product_name)

    # LLM 视觉分析：先识别原帧，生成精准替换指导，拼到 prompt 前
    if force_inject_product:
        print(f"  [shot {shot_num:02d}] LLM 视觉分析原帧（强制植入产品模式）...")
    else:
        print(f"  [shot {shot_num:02d}] LLM 视觉分析原帧...")
    llm_analysis = await _analyze_keyframe_for_replacement(
        in_frame, product_image, shot_desc,
        force_inject_product=force_inject_product,
        product_name=product_name,
    )
    if llm_analysis:
        print(f"  [shot {shot_num:02d}] LLM 分析结果:\n{llm_analysis[:500]}", flush=True)
        # 检测 LLM 是否判定画面中未找到用户指定的目标产品
        # 若是，跳过替换：复制原帧作为 Step3 输出，Step4 自动保留原段
        if (
            (not force_inject_product)
            and product_name
            and _is_target_product_not_found(llm_analysis)
        ):
            print(
                f"  [shot {shot_num:02d}] LLM 判定画面中未找到目标产品「{product_name}」，"
                f"跳过替换，自动保留原帧，Step4 将保留原段",
                flush=True,
            )
            shutil.copy(in_frame, out_frame)
            return {
                "shot": shot_num,
                "status": "no_product_skipped",
                "reason": f"画面中未找到目标产品「{product_name}」",
            }
        final_prompt = (
            "====================\n"
            "本分镜识别结果（LLM 基于原图实际识别，必须严格遵循）\n"
            "====================\n"
            f"{llm_analysis}\n\n"
            + final_prompt
        )
    else:
        print(f"  [shot {shot_num:02d}] LLM 分析失败，降级用 storyboard prompt", flush=True)

    # 构建参考图列表：原帧 + 产品图 + 全局基准图（人脸/场景/全身，跨 shot 共享）
    images = [in_frame, product_image]
    role_notes = [
        "第1张图：原始视频帧（人物/背景/构图/光照基准，必须严格保留）",
        "第2张图：新产品参考图（仅参考产品外观）",
    ]
    if face_ref and Path(face_ref).exists():
        images.append(Path(face_ref))
        role_notes.append(
            f"第{len(images)}张图：全局固定人脸基准（所有分镜共享同一张），"
            "人物五官/发型/肤色必须严格对齐此图"
        )
    if scene_ref and Path(scene_ref).exists():
        images.append(Path(scene_ref))
        role_notes.append(
            f"第{len(images)}张图：全局固定场景基准（所有分镜共享同一张），"
            "仅作场景结构/机位/光影/色调参考，不得据此删除原帧中的物品；"
            "所有分镜的背景/场景/机位/构图/光影必须与该图完全一致，禁止任何场景变化"
        )
    if model_full_ref and Path(model_full_ref).exists():
        images.append(Path(model_full_ref))
        role_notes.append(
            f"第{len(images)}张图：全局固定人物全身基准（所有分镜共享同一张），"
            "人物衣服款式/颜色/图案/版型必须严格对齐此图；"
            "若原帧中产品遮挡了人物衣服，按此图补全被遮挡的衣物，确保衣服完整连贯"
        )

    if len(images) > 2:
        final_prompt += (
            "\n\n====================\n"
            "全局基准图说明（跨分镜一致性锁定，最高优先级，凌驾于分镜描述之上）\n"
            "====================\n"
            + "\n".join(role_notes)
            + "\n\n→ 第3张及以后的基准图在【所有分镜中完全相同】。强制要求：\n"
            "  · 人脸：人物脸型/五官/发型/肤色以人脸基准图为准，所有分镜完全一致\n"
            "  · 场景：背景/场景/机位/构图/光影/色调以场景基准图为准，所有分镜完全一致\n"
            "    （禁止因分镜描述改变场景，统一使用场景基准图的画面）\n"
            "  · 衣服：人物穿着的服装款式/颜色/图案/版型以全身基准图为准，所有分镜完全一致；"
            "若原帧产品遮挡衣物，必须按全身基准图补全衣物\n"
            "  · 仅产品按第2张图替换，人物动作/姿态按第1张原帧保留\n"
            "确保不同分镜的人物和场景完全相同，拼接后无任何跳变。"
        )

    # 按原视频宽高比选择 image2 输出尺寸，避免比例失真
    # 方形 → 1024x1024；竖屏 → 1024x1792；横屏 → 1792x1024
    if video_width and video_height and video_width > 0 and video_height > 0:
        ratio = video_width / video_height
        if abs(ratio - 1.0) < 0.05:
            img_size = "1024x1024"
        elif ratio < 0.9:
            img_size = "1024x1792"
        elif ratio > 1.1:
            img_size = "1792x1024"
        else:
            img_size = "1024x1024"
    else:
        img_size = "1024x1792"  # 默认竖屏（向后兼容）
    print(f"  [shot {shot_num:02d}] image2 size={img_size} (源 {video_width}x{video_height})", flush=True)

    max_attempts = 6
    for attempt in range(1, max_attempts + 1):
        try:
            result = await client.image_edit_sync(
                prompt=final_prompt,
                images=images,
                size=img_size,
                n=1,
            )
            data_list = result.get("data", [])
            if not data_list:
                raise RuntimeError("Image2 返回 data 为空")
            url = data_list[0].get("url", "")
            b64 = data_list[0].get("b64_json", "")

            if url:
                async with httpx.AsyncClient(timeout=60.0) as dl:
                    r = await dl.get(url)
                    r.raise_for_status()
                    out_frame.write_bytes(r.content)
            elif b64:
                import base64
                out_frame.write_bytes(base64.b64decode(b64))
            else:
                raise RuntimeError("Image2 未返回图片")

            with Image.open(out_frame) as img:
                img.verify()
            return {"shot": shot_num, "status": "success"}

        except Exception as e:
            err = str(e)
            if "内容审核拒绝" in err or "Content blocked" in err or "451" in err:
                shutil.copy(in_frame, out_frame)
                return {"shot": shot_num, "status": "blocked", "reason": err[:200]}
            if attempt < max_attempts:
                await asyncio.sleep(8 * attempt)
            else:
                shutil.copy(in_frame, out_frame)
                return {"shot": shot_num, "status": "failed", "reason": err[:200]}
    return {"shot": shot_num, "status": "failed"}


async def step3_replace_products(
    keyframes_dir: Path,
    product_image_paths: List[str],
    out_dir: Path,
    product_shots: List[int],
    concurrency: int = 4,
    storyboard: Optional[List[dict]] = None,
    face_ref: Optional[str] = None,
    scene_ref: Optional[str] = None,
    model_full_ref: Optional[str] = None,
    product_name: str = "",
    video_width: int = 0,
    video_height: int = 0,
) -> dict:
    """对有产品的分镜关键帧做 image2 替换

    face_ref/scene_ref/model_full_ref: 全局基准图路径，所有 shot 共享，锁定跨 shot 一致性。
    product_name: 新产品名称，注入 LLM/image2 prompt，避免识别错误。
    video_width/video_height: 原视频实际尺寸，用于按比例选 image2 输出尺寸。
    """
    from backend.services.image2_client import Image2Client

    out_dir.mkdir(parents=True, exist_ok=True)
    client = Image2Client()
    product_image = Path(product_image_paths[0])

    # storyboard 索引：shot_num → 该 shot 的描述
    sb_map = {s["shot"]: s for s in storyboard} if storyboard else {}

    # 限制并发上限，避免同时打多个 image2 请求压垮上游（503/429/408）
    # 即使前端传 concurrency=4，Step3 替换也限制在 2 以内
    effective_concurrency = min(concurrency, 2)
    sem = asyncio.Semaphore(effective_concurrency)

    async def run_one(shot_num: int):
        async with sem:
            in_frame = keyframes_dir / f"shot_{shot_num:02d}.jpg"
            out_frame = out_dir / f"shot_{shot_num:02d}.jpg"
            return await _replace_one_keyframe(
                client, shot_num, in_frame, product_image, out_frame,
                shot_desc=sb_map.get(shot_num),
                face_ref=face_ref, scene_ref=scene_ref,
                model_full_ref=model_full_ref,
                product_name=product_name,
                video_width=video_width,
                video_height=video_height,
            )

    tasks = [run_one(n) for n in product_shots]
    results = await asyncio.gather(*tasks)
    return {"results": results}


async def redo_single_shot(
    keyframes_dir: Path,
    product_image_paths: List[str],
    out_dir: Path,
    shot_num: int,
    shot_desc: Optional[dict] = None,
    face_ref: Optional[str] = None,
    scene_ref: Optional[str] = None,
    model_full_ref: Optional[str] = None,
    force_inject_product: bool = False,
    product_name: str = "",
    video_width: int = 0,
    video_height: int = 0,
) -> dict:
    """重做单个 shot 的关键帧替换（审核后用）

    face_ref/scene_ref/model_full_ref: 全局基准图，与首次替换保持同一基准。
    force_inject_product: 原"无产品"分镜被用户主动 redo 时传 True，
        让 LLM 视觉识别原帧后判断产品植入位置（而非跳过替换）。
    product_name: 新产品名称，注入 LLM/image2 prompt，避免识别错误。
    video_width/video_height: 原视频实际尺寸，用于按比例选 image2 输出尺寸。
    """
    from backend.services.image2_client import Image2Client

    client = Image2Client()
    product_image = Path(product_image_paths[0])
    in_frame = keyframes_dir / f"shot_{shot_num:02d}.jpg"
    out_frame = out_dir / f"shot_{shot_num:02d}.jpg"
    return await _replace_one_keyframe(
        client, shot_num, in_frame, product_image, out_frame,
        shot_desc=shot_desc,
        face_ref=face_ref, scene_ref=scene_ref,
        model_full_ref=model_full_ref,
        force_inject_product=force_inject_product,
        product_name=product_name,
        video_width=video_width,
        video_height=video_height,
    )


# ─────────────────────────────────────────────────────────
# Step4: RunningHub 动作迁移
# ─────────────────────────────────────────────────────────
async def _download_video(url: str, out_path: Path):
    import httpx
    async with httpx.AsyncClient(timeout=300.0) as dl:
        r = await dl.get(url)
        r.raise_for_status()
        out_path.write_bytes(r.content)


def _get_segment_frame_count(seg_path: Path) -> int:
    """获取视频片段的总帧数"""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-count_frames", "-show_entries", "stream=nb_read_frames",
         "-of", "default=noprint_wrappers=1:nokey=1", str(seg_path)],
        capture_output=True, text=True, check=True
    )
    return int(r.stdout.strip())


async def _transfer_one_shot(
    shot_num: int,
    seg_path: Path,
    kf_path: Path,
    out_path: Path,
    width: int,
    height: int,
    is_no_product: bool,
    face_ref: Optional[Path] = None,
    scene_ref: Optional[Path] = None,
    model_full_ref: Optional[Path] = None,
    product_ref: Optional[Path] = None,
    motion_mode: Optional[str] = None,
) -> dict:
    """处理单个分镜：无产品→复制原段；有产品→动作迁移

    多参考图模式（V9工作流，按作者说明）：
    - 主图(299): 替换后的关键帧（必填，已包含产品替换结果）
    - 副图1(507): 人脸参考图（固定五官/发型/肤色）
    - 副图2(508): 场景参考图（固定背景/机位/光影）
    - 副图3(509): 人物全身图（固定衣服款式，解决产品遮挡）
    - 510/517: 预留位，不传值（节点保留，避免工作流报错）

    注：多参开关默认开启，副图按顺序前N张起作用。
    产品锁定靠主图（Step3已替换产品），product_ref 参数保留但不传给工作流。
    """
    from backend.services.runninghub_client import RunningHubClient, submit_motion_transfer

    if not seg_path.exists():
        return {"shot": shot_num, "status": "error", "reason": "segment_missing"}

    if is_no_product:
        shutil.copy(seg_path, out_path)
        return {"shot": shot_num, "status": "copied"}

    if not kf_path.exists():
        shutil.copy(seg_path, out_path)
        return {"shot": shot_num, "status": "kf_missing"}

    # 构建参考图列表：主图（关键帧） + 副图（人脸/场景/全身）
    # 顺序必须和 RUNNINGHUB_MOTION_IMAGE_NODES 配置一致：
    # 299(主图) → 507(人脸) → 508(场景) → 509(全身) → 510(预留) → 517(预留)
    # 按作者说明：副图是人物多角度/场景参考图，不是产品图；510/517 不传值但要保留节点
    image_paths = [str(kf_path)]
    if face_ref and Path(face_ref).exists():
        image_paths.append(str(face_ref))
        print(f"  shot {shot_num}: 附带人脸参考图 {Path(face_ref).name}", flush=True)
    if scene_ref and Path(scene_ref).exists():
        image_paths.append(str(scene_ref))
        print(f"  shot {shot_num}: 附带场景参考图 {Path(scene_ref).name}", flush=True)
    if model_full_ref and Path(model_full_ref).exists():
        image_paths.append(str(model_full_ref))
        print(f"  shot {shot_num}: 附带人物全身图 {Path(model_full_ref).name}", flush=True)
    # product_ref 不传给工作流（主图已包含产品替换结果，副图位按作者设计用于人物/场景多角度）
    if product_ref and Path(product_ref).exists():
        print(f"  shot {shot_num}: 产品参考图 {Path(product_ref).name} 已存在但不传给V9（主图已含产品）", flush=True)

    print(f"  shot {shot_num}: 传 {len(image_paths)} 张参考图给RH（主图+{len(image_paths)-1}副图）", flush=True)

    # 按原段实际帧数设置 max_frames（保留原时长），最少 81，向上取整到 4 的倍数
    seg_frames = _get_segment_frame_count(seg_path)
    max_frames = max(81, ((seg_frames + 3) // 4) * 4)
    print(f"  shot {shot_num}: 原段 {seg_frames} 帧 → max_frames={max_frames}", flush=True)

    t0 = time.time()
    try:
        result = await submit_motion_transfer(
            video_path=str(seg_path),
            image_paths=image_paths,
            width=width,
            height=height,
            steps=int(getattr(settings, "RUNNINGHUB_MOTION_STEPS", 9)),
            cfg=float(getattr(settings, "RUNNINGHUB_MOTION_CFG", 16.0)),
            lora_strength=float(getattr(settings, "RUNNINGHUB_MOTION_LORA_STRENGTH", 1.0)),
            cn_strength=float(getattr(settings, "RUNNINGHUB_MOTION_CN_STRENGTH", 0.2)),
            fps=30,
            max_frames=max_frames,
            motion_mode=motion_mode,
        )
    except Exception as e:
        print(f"  shot {shot_num}: ❌ 提交失败，回退原段: {str(e)[:200]}", flush=True)
        shutil.copy(seg_path, out_path)
        return {"shot": shot_num, "status": "submit_failed", "reason": str(e)[:200]}

    task_id = result.get("taskId", "")
    if not task_id:
        print(f"  shot {shot_num}: ❌ RH 未返回 taskId（响应: {result}），回退原段", flush=True)
        shutil.copy(seg_path, out_path)
        return {"shot": shot_num, "status": "no_taskid"}

    print(f"  shot {shot_num}: ✓ 提交成功 taskId={task_id}，开始轮询（最长 30 分钟）...", flush=True)

    client = RunningHubClient()
    max_wait = 1800
    last_status = None
    poll_count = 0
    while time.time() - t0 < max_wait:
        await asyncio.sleep(10)
        poll_count += 1
        try:
            sd = await client.query_task(task_id)
            st = sd.get("taskStatus", "?")
            # 状态变化时打印，或每 30 次（5 分钟）打印一次心跳
            if st != last_status:
                print(f"  shot {shot_num}: 状态 {last_status or 'QUEUED'} → {st}（已等待 {int(time.time()-t0)}s）", flush=True)
                last_status = st
            elif poll_count % 30 == 0:
                print(f"  shot {shot_num}: 仍在 {st}（已等待 {int(time.time()-t0)}s）...", flush=True)

            if st == "SUCCESS":
                outputs = await client.get_task_output(task_id)
                video_outs = [o for o in outputs.get("outputs", []) if o.get("fileType") == "mp4"]
                if not video_outs:
                    video_outs = outputs.get("outputs", [])
                if not video_outs:
                    print(f"  shot {shot_num}: ⚠ SUCCESS 但无输出，回退原段", flush=True)
                    shutil.copy(seg_path, out_path)
                    return {"shot": shot_num, "status": "no_output"}

                url = video_outs[0].get("fileUrl", "")
                if not url:
                    print(f"  shot {shot_num}: ⚠ SUCCESS 但输出无 URL，回退原段", flush=True)
                    shutil.copy(seg_path, out_path)
                    return {"shot": shot_num, "status": "no_url"}

                try:
                    await _download_video(url, out_path)
                    print(f"  shot {shot_num}: ✓ 完成下载，耗时 {int(time.time()-t0)}s", flush=True)
                    return {"shot": shot_num, "status": "success",
                            "url": url, "elapsed": int(time.time() - t0)}
                except Exception as e:
                    print(f"  shot {shot_num}: ⚠ 下载失败，回退原段: {str(e)[:200]}", flush=True)
                    shutil.copy(seg_path, out_path)
                    return {"shot": shot_num, "status": "download_failed", "reason": str(e)[:200]}

            if st == "FAILED":
                msg = sd.get("msg", "")[:200]
                err_code = sd.get("errorCode", "")
                failed_reason = sd.get("failedReason", {})
                print(
                    f"  shot {shot_num}: ❌ RH 任务失败（code={err_code}, msg={msg}, "
                    f"failedReason={failed_reason}），回退原段",
                    flush=True,
                )
                shutil.copy(seg_path, out_path)
                return {
                    "shot": shot_num,
                    "status": "failed",
                    "reason": msg,
                    "errorCode": err_code,
                    "failedReason": failed_reason,
                }
        except Exception as e:
            print(f"  shot {shot_num}: ⚠ 轮询异常（继续）: {str(e)[:150]}", flush=True)

    print(f"  shot {shot_num}: ❌ 轮询超时（{max_wait}s），回退原段", flush=True)
    shutil.copy(seg_path, out_path)
    return {"shot": shot_num, "status": "timeout"}


async def step4_motion_transfer(
    segments_dir: Path,
    replaced_kf_dir: Path,
    out_dir: Path,
    all_shots: List[int],
    no_product_shots: set,
    width: int = 576,
    height: int = 768,
    concurrency: int = 4,
    refs_dir: Optional[Path] = None,
    product_image_path: Optional[str] = None,
    motion_mode: Optional[str] = None,
) -> dict:
    """对所有分镜执行动作迁移（无产品保留原段，有产品走 RunningHub）

    motion_mode: "multi"=多参考图V9 / "single"=单图AI App（None 用 config 默认值）
    多参考图模式：若 refs_dir 中有 face_ref.jpg / scene_ref.jpg / model_full_ref.jpg，
    会作为副参考图传给 V9 工作流，锁定人物/场景/衣服一致性。
    product_image_path: 用户上传的产品图，传给 517 节点锁定产品外观。
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # 加载全局基准图（如果存在）
    face_ref = None
    scene_ref = None
    model_full_ref = None
    if refs_dir and refs_dir.exists():
        fr = refs_dir / "face_ref.jpg"
        sr = refs_dir / "scene_ref.jpg"
        mfr = refs_dir / "model_full_ref.jpg"
        if fr.exists():
            face_ref = fr
            print(f"  [Step4] 使用人脸基准图: {fr.name}", flush=True)
        if sr.exists():
            scene_ref = sr
            print(f"  [Step4] 使用场景基准图: {sr.name}", flush=True)
        if mfr.exists():
            model_full_ref = mfr
            print(f"  [Step4] 使用人物全身图: {mfr.name}", flush=True)

    # 产品参考图（用户上传的原图）
    product_ref = None
    if product_image_path and Path(product_image_path).exists():
        product_ref = Path(product_image_path)
        print(f"  [Step4] 使用产品参考图: {product_ref.name}", flush=True)

    print(f"  [Step4] 动作迁移模式: {motion_mode or '(config默认)'}", flush=True)

    # 限制并发上限，避免同时打多个视频生成请求压垮上游（503/429/408）
    effective_concurrency = min(concurrency, 2)
    sem = asyncio.Semaphore(effective_concurrency)

    async def run_one(shot_num: int):
        async with sem:
            seg_path = segments_dir / f"shot_{shot_num:02d}.mp4"
            kf_path = replaced_kf_dir / f"shot_{shot_num:02d}.jpg"
            out_path = out_dir / f"shot_{shot_num:02d}.mp4"
            # 断点续跑：输出已存在且有效则跳过
            if out_path.exists() and out_path.stat().st_size > 1024:
                print(f"  [Step4] shot {shot_num:02d}: 跳过（输出已存在）", flush=True)
                return {"shot": shot_num, "status": "skip", "reason": "已存在，断点跳过"}
            return await _transfer_one_shot(
                shot_num, seg_path, kf_path, out_path,
                width, height,
                is_no_product=shot_num in no_product_shots,
                face_ref=face_ref,
                scene_ref=scene_ref,
                model_full_ref=model_full_ref,
                product_ref=product_ref,
                motion_mode=motion_mode,
            )

    tasks = [run_one(n) for n in all_shots]
    results = await asyncio.gather(*tasks)
    return {"results": results}


# ─────────────────────────────────────────────────────────
# Step5: ffmpeg concat 拼接
# ─────────────────────────────────────────────────────────
def step5_concat(
    segments_dir: Path,
    all_shots: List[int],
    out_dir: Path,
    width: int = 576,
    height: int = 768,
    fps: int = 30,
) -> dict:
    """统一编码 + concat 拼接成最终视频"""
    out_dir.mkdir(parents=True, exist_ok=True)
    norm_dir = out_dir / "normalized"
    norm_dir.mkdir(exist_ok=True)

    for n in all_shots:
        src = segments_dir / f"shot_{n:02d}.mp4"
        dst = norm_dir / f"shot_{n:02d}.mp4"
        if dst.exists():
            continue
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src),
             "-c:v", "libx264", "-crf", "18", "-preset", "fast",
             "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p",
             "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
             "-r", str(fps), "-movflags", "+faststart",
             str(dst)],
            check=True, capture_output=True
        )

    list_file = out_dir / "concat_list.txt"
    with list_file.open("w", encoding="utf-8") as f:
        for n in all_shots:
            p = norm_dir / f"shot_{n:02d}.mp4"
            f.write(f"file '{p.as_posix()}'\n")

    final_path = out_dir / "final_replica.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", str(list_file), "-c", "copy", str(final_path)],
        check=True, capture_output=True
    )
    return {"final_video": str(final_path)}


# ─────────────────────────────────────────────────────────
# 阶段入口：Phase 1（Step1+2+3）
# ─────────────────────────────────────────────────────────
def _run_hand_mode_phase_1(
    video_path: Path,
    product_image: Path,
    work_dir: Path,
    split_mode: str,
    motion_mode: Optional[str],
) -> dict:
    """无人物手部模式 Phase1：调 product_motion.run_product_motion_phase1

    流程：切分原视频 → 抽关键帧（光流峰值帧）→ 生成替换后关键帧（=产品图）
    完成后暂停等待审核关键帧替换效果（参考常规 step3 的 review 结构）。

    特殊路径：若未检测到主体（"没有产品就跳过"分支），phase1 直接拼接保留
    原视频并返回 phase=done，本次返回 phase="done" 让上层直接走完成态。

    Args:
        split_mode: "no_subject" 表示走主体检测+光流切分
        motion_mode: "hand" 表示走手部预设
    """
    from backend.services.product_motion import run_product_motion_phase1

    # preset 选择：hand 模式 → preset="hand"
    preset = "hand" if motion_mode == "hand" else None

    print("\n" + "=" * 60 + "\n[Hand模式] Phase1: 切分 + 抽关键帧 + 替换关键帧\n" + "=" * 60, flush=True)
    print(f"  preset: {preset or 'default'}", flush=True)
    print(f"  split_mode: {split_mode}", flush=True)
    print(f"  motion_mode: {motion_mode}", flush=True)

    p1 = asyncio.run(run_product_motion_phase1(
        video_path=str(video_path),
        product_image_path=str(product_image),
        out_dir=work_dir,
        preset=preset,
        subject_keyword=None,  # 让 preset 决定（hand 预设自动用 "手"）
    ))

    timeline = p1["timeline"]

    # ── "没有产品就跳过"分支：phase1 已直接拼接原视频返回 done ──
    if p1.get("skipped_no_subject"):
        final_video = p1["video_path"]
        # 顶层 state.json：phase=done，让 run_phase_2 直接 no-op 返回
        _save_partial_state(work_dir, {
            "current_step": "done",
            "phase": "done",
            "video_path": str(video_path),
            "product_image_paths": [str(product_image)],
            "split_mode": split_mode,
            "motion_mode": motion_mode or "hand",
            "preset": preset or "default",
            "final_video": final_video,
            "hand_mode_timeline": timeline,
            "skipped_no_subject": True,
        })
        print(f"\n[Hand模式] ✓ 未检测到主体，整段保留原视频: {final_video}", flush=True)
        return {
            "phase": "done",  # 直接 done，跳过审核 + Phase2
            "work_dir": str(work_dir),
            "storyboard": [],
            "timeline": timeline,
            "all_shots": list(range(len(timeline))),
            "product_shots": [],
            "no_product_shots": list(range(len(timeline))),
            "width": 0, "height": 0,  # skipped 路径无尺寸需求
            "review": None,
            "final_video": final_video,
            "hand_mode": True,
            "skipped_no_subject": True,
        }

    # ── 常规路径：进入 awaiting_review 状态 ──
    review = p1.get("review", {})
    # 用 seg 编号模拟 shot 编号（让前端 review UI 能识别）
    n_segs = len(timeline)
    all_shots = list(range(n_segs))
    # 迁移段编号 = shot 编号（前端按 shot 展示 review item）
    product_shots = [item["seg"] for item in review.get("items", [])]
    no_product_shots = [i for i in all_shots if i not in product_shots]

    # 自动检测原视频尺寸（让前端 step5 预览能显示）
    width, height = _ffprobe_video_size(video_path)
    if not width or not height:
        width, height = 720, 1280

    # 顶层 state.json：phase=after_step3，等用户审核后进 Phase2
    _save_partial_state(work_dir, {
        "current_step": "after_step3",
        "phase": "after_step3",
        "video_path": str(video_path),
        "product_image_paths": [str(product_image)],
        "split_mode": split_mode,
        "motion_mode": motion_mode or "hand",
        "preset": preset or "default",
        "all_shots": all_shots,
        "product_shots": product_shots,
        "no_product_shots": no_product_shots,
        "width": width,
        "height": height,
        "hand_mode": True,  # 标识让 run_phase_2 走 product_motion_phase2
        "hand_mode_timeline": timeline,
        "hand_mode_review": review,
    })
    print(f"\n[Hand模式] Phase1 ✓ 完成，等待审核 {len(review.get('items', []))} 个关键帧替换", flush=True)
    n_migrated = sum(1 for e in timeline if e.get("kind") == "migrated")
    n_original = sum(1 for e in timeline if e.get("kind") == "original")
    print(f"  迁移段={n_migrated}, 保留原视频段={n_original}", flush=True)

    return {
        "phase": "after_step3",  # 与常规 Phase1 一致，让上层进入 awaiting_review
        "work_dir": str(work_dir),
        "storyboard": [],  # 无 MiMo 解析
        "timeline": timeline,
        "all_shots": all_shots,
        "product_shots": product_shots,
        "no_product_shots": no_product_shots,
        "width": width,
        "height": height,
        "review": review,
        "hand_mode": True,  # 标识 hand 模式（让 app.py 走常规审核分支）
    }


def run_phase_1(
    video_path: str,
    product_image_paths: List[str],
    product_name: str,
    work_dir: str,
    concurrency: int = 4,
    task_id: str = "",
    split_mode: str = "scene_cut",
    motion_mode: Optional[str] = None,
    resume: bool = True,
) -> dict:
    """Phase 1: Step1 + Step2 + Step3，完成后暂停等待审核关键帧

    Args:
        resume: 断点续跑（默认 True）。检查各 step 产物，已完成的跳过：
            - Step1: step1/storyboard.json + step1/timeline.json
            - Step2: step2/segments/shot_XX.mp4 + step2/keyframes/shot_XX.jpg（数量=分镜数）
            - 基准图: refs/face_ref.jpg + refs/scene_ref.jpg + refs/model_full_ref.jpg
            - Step3: step3/shot_XX.jpg（对所有 product_shots）
            任一 shot 缺失则从该 step 续跑（已完成的 shot 跳过）。

    Returns:
        {
            "phase": "after_step3",
            "work_dir": str,
            "storyboard": list,
            "timeline": list,
            "all_shots": list,
            "product_shots": list,
            "no_product_shots": list,
            "width": int, "height": int,
            "review": {...},
        }
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"视频不存在: {video_path}")
    if not product_image_paths:
        raise ValueError("未提供产品图")
    product_image = Path(product_image_paths[0])
    if not product_image.exists():
        raise FileNotFoundError(f"产品图不存在: {product_image}")

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    print(f"[Phase1] 工作目录: {work_dir}", flush=True)
    print(f"[Phase1] 视频: {video_path}", flush=True)
    print(f"[Phase1] 产品: {product_image}", flush=True)
    if resume:
        print(f"[Phase1] 断点续跑: 已开启（已完成的 step 将跳过）", flush=True)

    # ── 无人物主体检测模式分支：走 product_motion 光流切分链路 ──
    # 仅 split_mode="no_subject" 触发；motion_mode="hand" 不再走此分支，
    # hand 模式与 multi/single 一样走正常 Step1→2→3，仅 Step4 换 SCAIL2 工作流。
    if split_mode == "no_subject":
        return _run_hand_mode_phase_1(
            video_path=video_path,
            product_image=product_image,
            work_dir=work_dir,
            split_mode=split_mode,
            motion_mode=motion_mode,
        )

    # ── Step1: MiMo 视频结构解析（断点：storyboard.json + timeline.json 存在则跳过）──
    sb_path = work_dir / "step1" / "storyboard.json"
    tl_path = work_dir / "step1" / "timeline.json"
    if resume and sb_path.exists() and tl_path.exists():
        storyboard = json.loads(sb_path.read_text(encoding="utf-8"))
        timeline = json.loads(tl_path.read_text(encoding="utf-8"))
        print(f"\n[Step1] 跳过（已有 storyboard.json + timeline.json）", flush=True)
        print(f"  分镜数: {len(storyboard)}", flush=True)
    else:
        print("\n" + "=" * 60 + "\nStep1: MiMo 视频结构解析\n" + "=" * 60, flush=True)
        s1 = step1_parse_storyboard(
            video_path, product_name, product_image_paths,
            work_dir / "step1",
            split_mode=split_mode,
        )
        storyboard = s1["storyboard"]
        timeline = s1["timeline"]
        print(f"  分镜数: {len(storyboard)}", flush=True)

    all_shots = [s["shot"] for s in storyboard]
    product_shots, no_product_shots = _detect_product_shots(storyboard)
    # 用户在产品框里写了分段替换指令（如"第一段不用替换，第2段替换图1"）时指令优先，
    # 否则穿戴型产品（假发/头发）被 MiMo 归入"人物"，全判无产品，Step3 会无事可做
    force_shots, skip_shots = _parse_replacement_instructions(product_name, storyboard)
    if force_shots or skip_shots:
        no_product_shots = (no_product_shots | skip_shots) - force_shots
        product_shots = [n for n in all_shots if n not in no_product_shots]
        print(f"  [替换指令] 强制替换: {sorted(force_shots)} | 强制跳过: {sorted(skip_shots)}", flush=True)
    print(f"  有产品: {product_shots}", flush=True)
    print(f"  无产品: {sorted(no_product_shots)}", flush=True)

    # 自动检测原视频尺寸
    width, height = _ffprobe_video_size(video_path)
    if not width or not height:
        width, height = 576, 768
    print(f"  视频尺寸: {width}x{height}", flush=True)

    # ── Step2: 切片段 + 抽关键帧（断点：所有 shot 的 mp4+jpg 齐全则跳过）──
    seg_dir = work_dir / "step2" / "segments"
    kf_dir = work_dir / "step2" / "keyframes"
    step2_done = (
        resume
        and all((seg_dir / f"shot_{n:02d}.mp4").exists() for n in all_shots)
        and all((kf_dir / f"shot_{n:02d}.jpg").exists() for n in all_shots)
    )
    if step2_done:
        print(f"\n[Step2] 跳过（{len(all_shots)} 个切片+关键帧已齐全）", flush=True)
        kf_dir_str = str(kf_dir)
    else:
        print("\n" + "=" * 60 + "\nStep2: 切片段 + 抽关键帧\n" + "=" * 60, flush=True)
        s2 = asyncio.run(step2_split_segments(
            video_path, storyboard, timeline,
            work_dir / "step2"
        ))
        print(f"  切片: {len(s2['segments'])} 个", flush=True)
        kf_dir_str = s2["kf_dir"]

    # Step2 完成后增量保存 state.json（含断点信息，防止中断后丢失）
    _save_partial_state(work_dir, {
        "current_step": "after_step2",
        "video_path": str(video_path),
        "product_image_paths": [str(p) for p in product_image_paths],
        "product_name": product_name,
        "split_mode": split_mode,
        "motion_mode": motion_mode or "multi",
        "storyboard": storyboard,
        "timeline": timeline,
        "all_shots": all_shots,
        "product_shots": product_shots,
        "no_product_shots": sorted(no_product_shots),
        "width": width,
        "height": height,
    })

    # ── 基准图（断点：3 张基准图都存在则跳过）──
    refs_dir = work_dir / "refs"
    face_ref_p = refs_dir / "face_ref.jpg"
    scene_ref_p = refs_dir / "scene_ref.jpg"
    model_full_ref_p = refs_dir / "model_full_ref.jpg"
    if resume and face_ref_p.exists() and scene_ref_p.exists() and model_full_ref_p.exists():
        face_ref = str(face_ref_p)
        scene_ref = str(scene_ref_p)
        model_full_ref = str(model_full_ref_p)
        print(f"\n[基准图] 跳过（3 张已存在）", flush=True)
    else:
        print("\n" + "=" * 60 + "\n生成全局基准图（固定人脸 + 固定场景 + 人物全身）\n" + "=" * 60, flush=True)
        refs = asyncio.run(_ensure_reference_images(
            str(video_path), refs_dir, product_name=product_name
        ))
        face_ref = refs.get("face_ref")
        scene_ref = refs.get("scene_ref")
        model_full_ref = refs.get("model_full_ref")
        print(f"  人脸基准: {face_ref}", flush=True)
        print(f"  场景基准: {scene_ref}", flush=True)
        print(f"  人物全身: {model_full_ref}", flush=True)

    # ── Step3: 产品替换（断点：对每个 product_shot 检查 step3/shot_XX.jpg，已存在跳过）──
    step3_dir = work_dir / "step3"
    step3_dir.mkdir(parents=True, exist_ok=True)
    # 区分已完成/待处理的 product shots
    done_shots = []
    pending_shots = []
    if resume:
        for n in product_shots:
            out_frame = step3_dir / f"shot_{n:02d}.jpg"
            if out_frame.exists() and out_frame.stat().st_size > 1024:
                done_shots.append(n)
            else:
                pending_shots.append(n)
    else:
        pending_shots = list(product_shots)

    if pending_shots:
        print("\n" + "=" * 60 + f"\nStep3: image2 替换关键帧产品（{len(pending_shots)}/{len(product_shots)} 待处理）\n" + "=" * 60, flush=True)
        s3_new = asyncio.run(step3_replace_products(
            Path(kf_dir_str), product_image_paths,
            step3_dir,
            pending_shots,
            concurrency=concurrency,
            storyboard=storyboard,
            face_ref=face_ref, scene_ref=scene_ref,
            model_full_ref=model_full_ref,
            product_name=product_name,
            video_width=width,
            video_height=height,
        ))
        ok_new = sum(1 for r in s3_new["results"] if r["status"] == "success")
        print(f"  本次替换成功: {ok_new}/{len(pending_shots)}", flush=True)
        s3_results = s3_new["results"]
        # 已跳过的 shot 补一条 skip 记录
        for n in done_shots:
            s3_results.append({"shot": n, "status": "skip", "reason": "已存在，断点跳过"})
    else:
        print(f"\n[Step3] 跳过（{len(product_shots)} 个关键帧已全部替换）", flush=True)
        s3_results = [{"shot": n, "status": "skip", "reason": "已存在，断点跳过"} for n in product_shots]

    ok_total = sum(1 for r in s3_results if r["status"] in ("success", "skip"))
    print(f"  替换总计: {ok_total}/{len(product_shots)}", flush=True)

    # Step3 完成后，把 LLM 判定画面无目标产品的 shot 从 product_shots 移到 no_product_shots
    # 这些 shot 不走动作迁移，Step4 会复制原段保留
    llm_no_product = {
        r["shot"] for r in s3_results
        if r.get("status") == "no_product_skipped"
    }
    if llm_no_product:
        no_product_shots = set(no_product_shots) | llm_no_product
        product_shots = [n for n in product_shots if n not in llm_no_product]
        print(
            f"  [Step3] LLM 判定无目标产品的分镜：{sorted(llm_no_product)}，"
            f"已自动加入 no_product_shots，Step4 将保留原段",
            flush=True,
        )

    # 保存状态供 Phase2 使用
    state = {
        "phase": "after_step3",
        "work_dir": str(work_dir),
        "video_path": str(video_path),
        "product_image_paths": product_image_paths,
        "product_name": product_name,
        "storyboard": storyboard,
        "timeline": timeline,
        "all_shots": all_shots,
        "product_shots": product_shots,
        "no_product_shots": sorted(no_product_shots),
        "width": width,
        "height": height,
        "step3_results": s3_results,
        "face_ref": face_ref,
        "scene_ref": scene_ref,
        "model_full_ref": model_full_ref,
        "split_mode": split_mode,
        "motion_mode": motion_mode,
    }
    (work_dir / "state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    # 构造待审数据
    kf_replaced_dir = work_dir / "step3"
    kf_orig_dir = work_dir / "step2" / "keyframes"
    # 建立 shot → 时间区间 映射（从 storyboard 提取开始/结束时间）
    shot_time_map = {}
    for sb in storyboard:
        sn = sb.get("shot")
        if sn is None:
            continue
        tr = sb.get("时间区间") or sb.get("时间段") or {}
        start = tr.get("开始时间") or tr.get("开始") or ""
        end = tr.get("结束时间") or tr.get("结束") or ""
        dur = tr.get("镜头时长") or ""
        if start or end:
            shot_time_map[sn] = {"start": str(start), "end": str(end), "duration": str(dur)}
    review_items = []
    for n in all_shots:
        replaced_path = kf_replaced_dir / f"shot_{n:02d}.jpg"
        orig_path = kf_orig_dir / f"shot_{n:02d}.jpg"
        review_items.append({
            "shot": n,
            "replaced": str(replaced_path) if replaced_path.exists() else None,
            "original": str(orig_path) if orig_path.exists() else None,
            "is_no_product": n in no_product_shots,
            "time_range": shot_time_map.get(n, {}),
        })

    return {
        "phase": "after_step3",
        "work_dir": str(work_dir),
        "storyboard": storyboard,
        "timeline": timeline,
        "all_shots": all_shots,
        "product_shots": product_shots,
        "no_product_shots": sorted(no_product_shots),
        "width": width,
        "height": height,
        "review": {
            "keyframes_dir": str(kf_replaced_dir),
            "original_keyframes_dir": str(kf_orig_dir),
            "items": review_items,
        },
    }


# ─────────────────────────────────────────────────────────
# 单 shot 重做（审核后用）
# ─────────────────────────────────────────────────────────
def redo_shot(
    work_dir: str,
    shot_num: int,
    product_image_paths: Optional[List[str]] = None,
) -> dict:
    """重做单个 shot 的关键帧替换

    Args:
        work_dir: 工作目录
        shot_num: 分镜号
        product_image_paths: 可选，覆盖原产品图
    """
    work_dir = Path(work_dir)
    state_file = work_dir / "state.json"
    if not state_file.exists():
        raise FileNotFoundError("state.json 不存在，无法重做")

    state = json.loads(state_file.read_text(encoding="utf-8"))
    if product_image_paths is None:
        product_image_paths = state["product_image_paths"]

    kf_dir = work_dir / "step2" / "keyframes"
    out_dir = work_dir / "step3"

    # 检测是否为"无产品"分镜（原视频中该镜头本无产品，用户主动 redo 要求植入）
    no_product_shots = set(state.get("no_product_shots", []))
    force_inject_product = shot_num in no_product_shots

    # 读取 storyboard，为该 shot 注入产品形态描述
    shot_desc = None
    sb_file = work_dir / "step1" / "storyboard.json"
    if sb_file.exists():
        try:
            sb = json.loads(sb_file.read_text(encoding="utf-8"))
            for s in sb:
                if s.get("shot") == shot_num:
                    shot_desc = s
                    break
        except Exception:
            pass

    result = asyncio.run(redo_single_shot(
        kf_dir, product_image_paths, out_dir, shot_num, shot_desc=shot_desc,
        face_ref=state.get("face_ref"), scene_ref=state.get("scene_ref"),
        model_full_ref=state.get("model_full_ref"),
        force_inject_product=force_inject_product,
        product_name=state.get("product_name", ""),
        video_width=state.get("width", 0) or 0,
        video_height=state.get("height", 0) or 0,
    ))

    # redo 后根据 LLM 判定结果同步更新 state.json 的 product_shots / no_product_shots
    # 让 Phase2a/Step4 能正确决定该 shot 是否走动作迁移
    status = result.get("status")
    cur_no_product = set(state.get("no_product_shots", []))
    cur_product = list(state.get("product_shots", []))
    if status == "no_product_skipped":
        # LLM 判定该 shot 画面无目标产品 → 加入无产品集合，从有产品列表移除
        if shot_num not in cur_no_product:
            cur_no_product.add(shot_num)
        if shot_num in cur_product:
            cur_product = [n for n in cur_product if n != shot_num]
    elif status == "success":
        # 用户主动 redo 成功植入/替换 → 从无产品集合移除（如原 force_inject_product 场景）
        if shot_num in cur_no_product:
            cur_no_product.discard(shot_num)
        if shot_num not in cur_product:
            cur_product.append(shot_num)
            cur_product.sort()
    if cur_no_product != set(state.get("no_product_shots", [])):
        state["no_product_shots"] = sorted(cur_no_product)
        state["product_shots"] = cur_product
        state_file.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    return result


# ─────────────────────────────────────────────────────────
# 阶段入口：Phase 2a（仅 Step4 动作迁移 → after_step4 待审）
# ─────────────────────────────────────────────────────────
def run_phase_2a(
    work_dir: str,
    concurrency: int = 4,
    motion_mode: Optional[str] = None,
) -> dict:
    """Phase 2a: 仅 Step4 动作迁移，输出各 shot 片段（不拼接）

    完成后 state.phase = after_step4，供前端审核每个动作迁移片段。
    motion_mode 决定调用哪个工作流：multi(V9)/single(AI App)/hand(SCAIL2)/wan22。
    """
    work_dir = Path(work_dir)
    state_file = work_dir / "state.json"
    if not state_file.exists():
        raise FileNotFoundError("state.json 不存在，请先执行 Phase1")
    state = json.loads(state_file.read_text(encoding="utf-8"))

    _cur_phase = state.get("phase", "")
    if _cur_phase not in ("after_step3", "after_step4", "done"):
        raise RuntimeError(
            f"Phase1 未完成（state.json phase={_cur_phase or '未设置'}），请先完成 Phase1"
        )

    all_shots = state["all_shots"]
    no_product_shots = set(state.get("no_product_shots", []))
    width = state.get("width", 576)
    height = state.get("height", 768)
    if not motion_mode:
        motion_mode = state.get("motion_mode")

    seg_dir = work_dir / "step2" / "segments"
    kf_replaced_dir = work_dir / "step3"
    refs_dir = work_dir / "refs"
    product_image_paths = state.get("product_image_paths", [])
    product_image_path = product_image_paths[0] if product_image_paths else None

    print("\n" + "=" * 60 + "\n[Phase2a] Step4: RunningHub 动作迁移\n" + "=" * 60, flush=True)
    s4 = asyncio.run(step4_motion_transfer(
        seg_dir, kf_replaced_dir,
        work_dir / "step4",
        all_shots, no_product_shots,
        width=width, height=height,
        concurrency=concurrency,
        refs_dir=refs_dir,
        product_image_path=product_image_path,
        motion_mode=motion_mode,
    ))

    state["phase"] = "after_step4"
    state["step4_results"] = s4["results"]
    (work_dir / "state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return {
        "phase": "after_step4",
        "work_dir": str(work_dir),
        "shots": s4["results"],
    }


# ─────────────────────────────────────────────────────────
# 阶段入口：Phase 2b（仅 Step5 拼接 → done）
# ─────────────────────────────────────────────────────────
def run_phase_2b(work_dir: str) -> dict:
    """Phase 2b: 仅 Step5 拼接，输出最终视频

    前提：state.phase = after_step4（Step4 片段已生成且审核通过）
    """
    work_dir = Path(work_dir)
    state_file = work_dir / "state.json"
    if not state_file.exists():
        raise FileNotFoundError("state.json 不存在")
    state = json.loads(state_file.read_text(encoding="utf-8"))

    all_shots = state["all_shots"]
    width = state.get("width", 576)
    height = state.get("height", 768)

    print("\n" + "=" * 60 + "\n[Phase2b] Step5: ffmpeg concat 拼接\n" + "=" * 60, flush=True)
    s5 = step5_concat(
        work_dir / "step4",
        all_shots,
        work_dir / "step5",
        width=width, height=height, fps=30,
    )
    final_video = s5["final_video"]

    state["phase"] = "done"
    state["final_video"] = final_video
    (work_dir / "state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return {
        "phase": "done",
        "final_video": final_video,
        "work_dir": str(work_dir),
        "shots": state.get("step4_results", []),
    }


# ─────────────────────────────────────────────────────────
# 重做单个 shot 的动作迁移（Step4）
# ─────────────────────────────────────────────────────────
def redo_motion_shot(work_dir: str, shot_num: int) -> dict:
    """重做单个 shot 的动作迁移：删旧输出 → 重新 _transfer_one_shot → 更新 state"""
    work_dir = Path(work_dir)
    state_file = work_dir / "state.json"
    if not state_file.exists():
        raise FileNotFoundError("state.json 不存在")
    state = json.loads(state_file.read_text(encoding="utf-8"))

    no_product_shots = set(state.get("no_product_shots", []))
    width = state.get("width", 576)
    height = state.get("height", 768)
    motion_mode = state.get("motion_mode")

    seg_dir = work_dir / "step2" / "segments"
    kf_replaced_dir = work_dir / "step3"
    refs_dir = work_dir / "refs"
    product_image_paths = state.get("product_image_paths", [])
    product_image_path = product_image_paths[0] if product_image_paths else None

    # 加载基准图
    face_ref = scene_ref = model_full_ref = None
    if refs_dir and refs_dir.exists():
        fr = refs_dir / "face_ref.jpg"
        sr = refs_dir / "scene_ref.jpg"
        mfr = refs_dir / "model_full_ref.jpg"
        if fr.exists(): face_ref = fr
        if sr.exists(): scene_ref = sr
        if mfr.exists(): model_full_ref = mfr

    # 删除旧输出（step4 原片 + step5 normalized 缓存，强制重生成）
    out_path = work_dir / "step4" / f"shot_{shot_num:02d}.mp4"
    if out_path.exists():
        out_path.unlink()
    norm_path = work_dir / "step5" / "normalized" / f"shot_{shot_num:02d}.mp4"
    if norm_path.exists():
        norm_path.unlink()

    seg_path = seg_dir / f"shot_{shot_num:02d}.mp4"
    kf_path = kf_replaced_dir / f"shot_{shot_num:02d}.jpg"
    product_ref = Path(product_image_path) if product_image_path and Path(product_image_path).exists() else None

    result = asyncio.run(_transfer_one_shot(
        shot_num, seg_path, kf_path, out_path,
        width, height,
        is_no_product=shot_num in no_product_shots,
        face_ref=face_ref,
        scene_ref=scene_ref,
        model_full_ref=model_full_ref,
        product_ref=product_ref,
        motion_mode=motion_mode,
    ))

    # 更新 state.step4_results 中对应项
    results = state.get("step4_results", [])
    updated = False
    for i, r in enumerate(results):
        if r.get("shot") == shot_num:
            results[i] = result
            updated = True
            break
    if not updated:
        results.append(result)
    state["step4_results"] = results
    (work_dir / "state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return result


# ─────────────────────────────────────────────────────────
# 阶段入口：Phase 2（Step4+5，全流程，不分段）
# ─────────────────────────────────────────────────────────
def run_phase_2(
    work_dir: str,
    concurrency: int = 4,
    motion_mode: Optional[str] = None,
) -> dict:
    """Phase 2: Step4 + Step5，输出最终视频

    Args:
        work_dir: 工作目录（必须已执行 Phase1）
        motion_mode: 动作迁移模式 "multi"/"single"/"hand"（None 时读取 state 或 config 默认值）
    """
    work_dir = Path(work_dir)
    state_file = work_dir / "state.json"
    if not state_file.exists():
        raise FileNotFoundError("state.json 不存在，请先执行 Phase1")

    state = json.loads(state_file.read_text(encoding="utf-8"))

    # 防护：只有 Phase1 真正完成（phase=after_step3）才允许跑 Phase2
    # phase=done 表示 Phase2 已完成，允许重跑（用户想重新生成视频）
    _cur_phase = state.get("phase", "")
    if _cur_phase not in ("after_step3", "done"):
        raise RuntimeError(
            f"Phase1 未完成（state.json phase={_cur_phase or '未设置'}），"
            f"请先通过断点重试完成 Phase1（Step1/2/3）"
        )
    all_shots = state["all_shots"]
    no_product_shots = set(state["no_product_shots"])
    width = state.get("width", 576)
    height = state.get("height", 768)

    # 动作迁移模式：参数优先，其次 state（phase_1 保存），最后 config 默认
    if not motion_mode:
        motion_mode = state.get("motion_mode")

    seg_dir = work_dir / "step2" / "segments"
    kf_replaced_dir = work_dir / "step3"
    refs_dir = work_dir / "refs"

    # 产品图路径（传给 step4 的 517 节点，锁定产品外观）
    product_image_paths = state.get("product_image_paths", [])
    product_image_path = product_image_paths[0] if product_image_paths else None

    # Step4: 动作迁移（传入基准图目录 + 产品图，启用多参考图模式）
    print("\n" + "=" * 60 + "\nStep4: RunningHub 动作迁移\n" + "=" * 60, flush=True)
    s4 = asyncio.run(step4_motion_transfer(
        seg_dir, kf_replaced_dir,
        work_dir / "step4",
        all_shots, no_product_shots,
        width=width, height=height,
        concurrency=concurrency,
        refs_dir=refs_dir,
        product_image_path=product_image_path,
        motion_mode=motion_mode,
    ))
    succ = sum(1 for r in s4["results"] if r["status"] in ("success", "copied"))
    try:
        print(f"  完成: {succ}/{len(all_shots)}", flush=True)
    except Exception:
        pass

    # Step5: 拼接
    try:
        print("\n" + "=" * 60 + "\nStep5: ffmpeg concat 拼接\n" + "=" * 60, flush=True)
    except Exception:
        pass
    s5 = step5_concat(
        work_dir / "step4",
        all_shots,
        work_dir / "step5",
        width=width, height=height, fps=30,
    )
    final_video = s5["final_video"]
    print(f"  最终视频: {final_video}", flush=True)

    # 更新 state
    state["phase"] = "done"
    state["final_video"] = final_video
    state["step4_results"] = s4["results"]
    (work_dir / "state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    return {
        "phase": "done",
        "final_video": final_video,
        "work_dir": str(work_dir),
        "shots": s4["results"],
    }


# ─────────────────────────────────────────────────────────
# 全流程入口（不分阶段，一气呵成）
# ─────────────────────────────────────────────────────────
def run_pipeline(
    video_path: str,
    product_image_paths: List[str],
    product_name: str = "",
    work_dir: str = "",
    concurrency: int = 4,
    task_id: str = "",
    split_mode: str = "scene_cut",
) -> dict:
    """全流程一气呵成（不分阶段）

    Args:
        split_mode: 分镜切分模式 "scene_cut"(默认) | "action_grained"
    """
    if not work_dir:
        work_dir = str(Path(settings.OUTPUT_DIR) / "replica_motion" / (task_id or "default"))

    # Phase1
    p1 = run_phase_1(
        video_path=video_path,
        product_image_paths=product_image_paths,
        product_name=product_name or "新产品",
        work_dir=work_dir,
        concurrency=concurrency,
        task_id=task_id,
        split_mode=split_mode,
    )
    # Phase1 完成校验：必须写 state.json 且 phase=after_step3 才能继续 Phase2
    # 否则 Step3 产品替换失败时，state.json 不含 phase=after_step3，Phase2 防护会 FAIL
    state_file = Path(work_dir) / "state.json"
    phase_ok = False
    if state_file.exists():
        try:
            _sd = json.loads(state_file.read_text(encoding="utf-8"))
            phase_ok = (_sd.get("phase") == "after_step3")
        except Exception:
            pass
    if not phase_ok:
        raise RuntimeError(
            "Phase1 未完成（Step3 产品替换失败），无法继续 Phase2。"
            f"请通过断点重试重跑 Phase1（已完成的 Step1/2 会自动跳过）。"
        )

    # Phase2
    p2 = run_phase_2(work_dir=work_dir, concurrency=concurrency)

    return {
        "success": True,
        "final_video": p2["final_video"],
        "work_dir": work_dir,
        "storyboard": p1["storyboard"],
        "timeline": p1["timeline"],
        "shots": p2["shots"],
    }
