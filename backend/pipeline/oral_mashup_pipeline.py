"""
口播混剪 Pipeline (MVP)
─────────────────────────────────────────────────
以主视频(92号)的口播音频为时间轴，自动：
1. ASR 识别口播分段
2. ffmpeg silencedetect 检测静音段（气口）
3. LLM(gpt/grok 轮询) 分析口播内容 → 自动匹配 B-roll 素材
4. 生成剪辑方案 edit_plan.json
5. ffmpeg 切片 + 拼接 → 9:16 竖版 final_mashup.mp4

输入：
  main_video: 主视频路径（提供口播音频 + 主播画面，如 video(92).mp4）
  broll_videos: B-roll 素材列表（如 [video(93).mp4, video(96).mp4, ...]）

输出：
  outputs/oral_mashup_<ts>/
    ├─ asr.json          - ASR 原始分段
    ├─ silence.json      - 静音段（气口）
    ├─ edit_plan.json    - LLM 生成的剪辑方案
    ├─ segments/         - 切片临时目录
    │   ├─ seg_000.mp4
    │   └─ ...
    └─ final_mashup.mp4  - 最终 9:16 竖版视频
"""
import os
import re
import sys
import json
import time
import asyncio
import subprocess
from pathlib import Path
from typing import Optional

# 把项目根目录加入 sys.path，便于直接 python 运行
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config import settings


# ═══════════════════════════════════════
# Step 1: ASR + 静音检测
# ═══════════════════════════════════════

async def run_asr_with_silence(video_path: str, work_dir: Path) -> dict:
    """
    对主视频做 ASR + 静音检测。

    返回:
      {
        "audio_path": str,
        "duration": float,
        "asr_segments": [{"start", "end", "text"}, ...],
        "silence_segments": [{"start", "end", "duration"}, ...],
        "keep_segments": [{"start", "end"}, ...]  # 去掉静音后保留的段
      }
    """
    print(f"\n[Step1] ASR + 静音检测: {video_path}")
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1.1 提取音频（16kHz 单声道，给 ASR）
    audio_path = work_dir / "audio_asr.wav"
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        str(audio_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"音频提取失败: {proc.stderr[-500:]}")

    # 1.2 视频时长
    dur_cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", video_path,
    ]
    dur_proc = subprocess.run(dur_cmd, capture_output=True, text=True, encoding="utf-8")
    duration = float(json.loads(dur_proc.stdout)["format"]["duration"])
    print(f"  → 视频时长: {duration:.2f}s")

    # 1.3 ASR（复用 faster_whisper）
    print(f"  → [ASR] 调用 faster_whisper (model=base)...")
    from faster_whisper import WhisperModel
    model = WhisperModel("base", device="cpu", compute_type="int8")
    segs, _info = model.transcribe(str(audio_path), language="zh", vad_filter=True)
    asr_segments = [
        {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
        for s in segs
    ]
    print(f"  → [ASR] 识别 {len(asr_segments)} 段")
    (work_dir / "asr.json").write_text(
        json.dumps(asr_segments, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 1.4 静音检测（< -35dB 持续 >0.5s）
    print(f"  → [silencedetect] 检测静音段...")
    sil_cmd = [
        "ffmpeg", "-i", video_path, "-af",
        "silencedetect=noise=-35dB:d=0.5", "-f", "null", "-",
    ]
    sil_proc = subprocess.run(sil_cmd, capture_output=True, text=True, encoding="utf-8")
    sil_stderr = sil_proc.stderr or ""

    silence_segments = []
    starts = re.findall(r"silence_start: ([\d.]+)", sil_stderr)
    ends = re.findall(r"silence_end: ([\d.]+)", sil_stderr)
    n = min(len(starts), len(ends))
    for i in range(n):
        s, e = float(starts[i]), float(ends[i])
        silence_segments.append({
            "start": round(s, 2),
            "end": round(e, 2),
            "duration": round(e - s, 2),
        })
    print(f"  → 检测到 {len(silence_segments)} 个静音段")
    (work_dir / "silence.json").write_text(
        json.dumps(silence_segments, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 1.5 计算保留段（去掉静音后的"有效口播段"）
    keep_segments = []
    cursor = 0.0
    for sil in silence_segments:
        if sil["start"] > cursor + 0.1:
            keep_segments.append({
                "start": round(cursor, 2),
                "end": round(sil["start"], 2),
            })
        cursor = sil["end"]
    if cursor < duration - 0.1:
        keep_segments.append({
            "start": round(cursor, 2),
            "end": round(duration, 2),
        })
    print(f"  → 保留有效口播 {len(keep_segments)} 段")

    return {
        "audio_path": str(audio_path),
        "duration": duration,
        "asr_segments": asr_segments,
        "silence_segments": silence_segments,
        "keep_segments": keep_segments,
    }


# ═══════════════════════════════════════
# Step 2: LLM 生成剪辑方案
# ═══════════════════════════════════════

async def generate_edit_plan(
    main_video: str,
    broll_videos: list,
    asr_result: dict,
    work_dir: Path,
    engine: str = "gpt",
) -> dict:
    """
    调用 LLM(gpt/grok) 分析口播内容，生成剪辑方案。

    剪辑方案格式:
      {
        "total_duration": float,  # 预估总时长
        "segments": [
          {
            "source": "main" | "broll_0" | "broll_1" | ...,
            "source_file": 实际文件名,
            "start": float,  # 该片段在源视频中的起始时间
            "end": float,    # 该片段在源视频中的结束时间
            "reason": "为何选这段"
          },
          ...
        ]
      }
    """
    print(f"\n[Step2] LLM 生成剪辑方案 (engine={engine})")

    # 准备 B-roll 元数据（路径 + 时长）
    broll_meta = []
    for i, v in enumerate(broll_videos):
        dur_cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", v,
        ]
        dp = subprocess.run(dur_cmd, capture_output=True, text=True, encoding="utf-8")
        try:
            d = float(json.loads(dp.stdout)["format"]["duration"])
        except Exception:
            d = 0.0
        broll_meta.append({
            "index": i,
            "file": os.path.basename(v),
            "duration": round(d, 2),
        })

    # 把 ASR 段（按 keep_segments 过滤，只保留有效段）映射到去气口后的新时间轴
    # 简化处理：直接把 keep_segments 作为口播有效段，让 LLM 看到原始 ASR 文本
    main_basename = os.path.basename(main_video)

    # 构造 ASR 文本展示（合并 keep_segments 内的 ASR 文本）
    asr_view = []
    for ks in asr_result["keep_segments"]:
        # 找该段内的 ASR 文本
        texts = [
            s["text"] for s in asr_result["asr_segments"]
            if s["start"] >= ks["start"] - 0.3 and s["end"] <= ks["end"] + 0.3
        ]
        asr_view.append({
            "seg_index": len(asr_view),
            "original_start": ks["start"],
            "original_end": ks["end"],
            "duration": round(ks["end"] - ks["start"], 2),
            "text": " ".join(texts) if texts else "(无明显语音)",
        })

    system_prompt = """你是一位专业的视频剪辑师，擅长把"主播口播 + B-roll 生产画面"混剪成一条节奏紧凑的 9:16 竖版短视频。

任务：根据主视频的口播分段，生成一份剪辑方案 JSON。

剪辑原则：
1. 主视频(主播)的口播音频是整条视频的声音轨，全程不变
2. 当主播说到某个产品/工序时，切到对应的 B-roll 生产画面（主播画面隐藏，B-roll 画面展示，但音频继续是主播口播）
3. 主播说完该产品/工序后，切回主播画面
4. B-roll 时长通常 2-5 秒，不要太长
5. 主视频本身有成品展示收尾，结尾段保留主视频画面
6. 不要每段都切 B-roll，只在语义对应处切入（保持节奏）
7. 输出 JSON 必须严格符合指定 schema，不要任何解释文字

输出 JSON Schema:
{
  "segments": [
    {
      "source": "main" 或 "broll_N" (N 为 B-roll 索引，从 0 开始),
      "start": 该片段在源视频中的起始时间(秒, float),
      "end": 该片段在源视频中的结束时间(秒, float),
      "reason": "为何选这段(简短)"
    }
  ]
}

注意：
- "main" 指主视频(主播放视频)
- "broll_0" 指第 0 个 B-roll 视频，"broll_1" 指第 1 个，以此类推
- segments 必须按时间顺序排列
- 第一段和最后一段通常是 "main"（主播开场和成品收尾）
- 段与段时间可以连续（end 等于下一段 start），也可以有重叠（重叠部分会做硬切）
- 只输出 JSON，不要 markdown 代码块，不要任何文字说明"""

    user_content = f"""主视频: {main_basename} (时长 {asr_result['duration']:.2f}s, 提供口播音频 + 主播画面)

B-roll 素材列表:
{json.dumps(broll_meta, ensure_ascii=False, indent=2)}

主视频口播分段（已去掉静音气口）:
{json.dumps(asr_view, ensure_ascii=False, indent=2)}

请基于口播内容判断：
- 哪些段需要切 B-roll？切到哪个 B-roll？切多长？
- 哪些段保留主播画面？

输出剪辑方案 JSON:"""

    # 调用 LLM（gpt 优先，失败 fallback grok）
    plan_text = await _call_llm_with_fallback(system_prompt, user_content, engine)

    # 解析 JSON（容错：去掉可能的 markdown 代码块）
    plan_text = plan_text.strip()
    if plan_text.startswith("```"):
        plan_text = re.sub(r"^```(?:json)?\s*", "", plan_text)
        plan_text = re.sub(r"\s*```$", "", plan_text)
    plan = json.loads(plan_text)

    # 补充 source_file 字段
    for seg in plan["segments"]:
        src = seg.get("source", "main")
        if src == "main":
            seg["source_file"] = main_video
        elif src.startswith("broll_"):
            idx = int(src.split("_")[1])
            seg["source_file"] = broll_videos[idx] if idx < len(broll_videos) else main_video
        else:
            seg["source_file"] = main_video

    # 写入 edit_plan.json
    (work_dir / "edit_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  → 剪辑方案: {len(plan['segments'])} 段")
    for i, seg in enumerate(plan["segments"]):
        print(f"    [{i:02d}] {seg['source']:8s} {seg['start']:6.2f}-{seg['end']:6.2f} ({seg['end']-seg['start']:.2f}s) {seg.get('reason','')}")
    return plan


async def _call_llm_with_fallback(system_prompt: str, user_content: str, engine: str) -> str:
    """调用 LLM，支持 gpt/grok 轮询 fallback。"""
    from openai import AsyncOpenAI

    engines_to_try = [engine]
    if engine == "gpt":
        engines_to_try.append("grok")
    elif engine == "grok":
        engines_to_try.append("gpt")

    last_err = None
    for eng in engines_to_try:
        try:
            if eng == "grok":
                api_key = settings.SR_GROK_API_KEY
                base_url = settings.SR_GROK_BASE_URL
                model = settings.SR_GROK_MODEL
                label = "Grok"
            else:
                api_key = settings.OPENAI_API_KEY
                base_url = settings.OPENAI_BASE_URL
                model = settings.OPENAI_MODEL
                label = "GPT"

            print(f"  → [{label}] 调用 {model}...")
            client = AsyncOpenAI(api_key=api_key, base_url=base_url)
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.4,
            )
            content = resp.choices[0].message.content or ""
            if content.strip():
                print(f"  ✓ [{label}] 返回 {len(content)} 字符")
                return content
            print(f"  ✗ [{label}] 返回空 content")
        except Exception as e:
            print(f"  ✗ [{label}] 调用失败: {e}")
            last_err = e

    raise RuntimeError(f"所有 LLM 引擎均失败: {last_err}")


# ═══════════════════════════════════════
# Step 3: ffmpeg 切片 + 拼接
# ═══════════════════════════════════════

def execute_edit(
    plan: dict,
    main_video: str,
    audio_path: str,
    work_dir: Path,
    output_resolution: tuple = (1080, 1920),
) -> str:
    """
    按剪辑方案切片 + 拼接，输出 9:16 竖版视频。

    策略：
      1. 每段单独切出 → 转码到统一规格(1080x1920, h264, 30fps, 无音频或静音)
      2. 用 concat demuxer 拼接所有片段
      3. 把主视频原始音频作为整条音轨（按去气口后的时间轴拼接）
      4. 最终 mux 视频 + 音频

    Args:
      plan: 剪辑方案 dict
      main_video: 主视频路径（音频来源）
      audio_path: ASR 提取的音频路径（备用，实际用主视频原音）
      work_dir: 工作目录
      output_resolution: (w, h) 默认 1080x1920

    Returns:
      最终视频路径
    """
    print(f"\n[Step3] ffmpeg 执行剪辑")
    W, H = output_resolution
    seg_dir = work_dir / "segments"
    seg_dir.mkdir(exist_ok=True)

    segments = plan["segments"]
    print(f"  → 共 {len(segments)} 段，开始切片...")

    # ── 3.1 切片：每段单独导出为 1080x1920 h264 无音频 ──
    seg_files = []
    for i, seg in enumerate(segments):
        src_file = seg["source_file"]
        start = float(seg["start"])
        end = float(seg["end"])
        duration = end - start
        if duration <= 0.1:
            print(f"    [{i:02d}] 跳过(过短): {seg['source']} {start}-{end}")
            continue

        out_file = seg_dir / f"seg_{i:03d}.mp4"
        # scale 到 1080x1920：先缩放保持比例填满，再居中裁剪
        vf = (
            f"scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H}"
        )
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{start:.3f}",
            "-to", f"{end:.3f}",
            "-i", src_file,
            "-vf", vf,
            "-an",  # 切片不带音频（音频单独处理）
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            "-t", f"{duration:.3f}",
            str(out_file),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if proc.returncode != 0 or not out_file.exists():
            print(f"    [{i:02d}] 切片失败: {proc.stderr[-300:]}")
            continue
        seg_files.append(str(out_file))
        print(f"    [{i:02d}] ✓ {seg['source']:8s} {start:6.2f}-{end:6.2f} ({duration:.2f}s) → {out_file.name}")

    if not seg_files:
        raise RuntimeError("没有有效的切片")

    # ── 3.2 拼接视频片段 ──
    print(f"\n  → 拼接 {len(seg_files)} 个片段...")
    concat_list = work_dir / "concat_list.txt"
    concat_list.write_text(
        "\n".join(f"file '{f}'" for f in seg_files),
        encoding="utf-8",
    )
    video_only = work_dir / "video_only.mp4"
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        str(video_only),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        # concat copy 失败，重编码拼接
        print(f"  → concat copy 失败，重新编码拼接...")
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", "30",
            str(video_only),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if proc.returncode != 0:
            raise RuntimeError(f"拼接失败: {proc.stderr[-500:]}")

    print(f"  ✓ 视频拼接完成: {video_only.name}")

    # ── 3.3 提取主视频对应时段的音频，按 segments 顺序拼接 ──
    # 主视频原音作为整条音轨（只取 source=main 的段的音频，B-roll 段静音或延续上一段）
    # 简化方案：直接从主视频提取音频，根据 segments 中 main 段的累计时长静音填充
    print(f"\n  → 构建音轨（主视频原音）...")
    audio_segs = []
    for i, seg in enumerate(segments):
        start = float(seg["start"])
        end = float(seg["end"])
        duration = end - start
        if duration <= 0.1:
            continue
        if seg["source"] == "main":
            # 从主视频提取该段音频
            a_out = seg_dir / f"aud_{i:03d}.wav"
            cmd = [
                "ffmpeg", "-y",
                "-ss", f"{start:.3f}",
                "-t", f"{duration:.3f}",
                "-i", main_video,
                "-vn", "-acodec", "pcm_s16le",
                "-ar", "44100", "-ac", "2",
                str(a_out),
            ]
            subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
            audio_segs.append(str(a_out))
        else:
            # B-roll 段：生成对应时长的静音
            a_out = seg_dir / f"aud_{i:03d}.wav"
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", f"anullsrc=cl=stereo:r=44100",
                "-t", f"{duration:.3f}",
                "-acodec", "pcm_s16le",
                str(a_out),
            ]
            subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
            audio_segs.append(str(a_out))

    # 拼接音频
    audio_concat_list = work_dir / "audio_concat_list.txt"
    audio_concat_list.write_text(
        "\n".join(f"file '{f}'" for f in audio_segs),
        encoding="utf-8",
    )
    audio_full = work_dir / "audio_full.wav"
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(audio_concat_list),
        "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2",
        str(audio_full),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"音频拼接失败: {proc.stderr[-500:]}")
    print(f"  ✓ 音轨构建完成: {audio_full.name}")

    # ── 3.4 mux 视频和音频 ──
    print(f"\n  → 合成最终视频...")
    final_path = work_dir / "final_mashup.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_only),
        "-i", str(audio_full),
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(final_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"合成失败: {proc.stderr[-500:]}")

    size_mb = final_path.stat().st_size / 1024 / 1024
    print(f"\n[完成] 输出: {final_path}")
    print(f"       大小: {size_mb:.2f} MB")
    print(f"       规格: {W}x{H} (9:16)")
    return str(final_path)


# ═══════════════════════════════════════
# 端到端入口
# ═══════════════════════════════════════

async def run_mashup_pipeline(
    main_video: str,
    broll_videos: list,
    engine: str = "gpt",
    output_root: Optional[str] = None,
) -> dict:
    """
    端到端运行口播混剪 pipeline。

    Args:
      main_video: 主视频路径
      broll_videos: B-roll 素材列表
      engine: LLM 引擎 ("gpt" / "grok")
      output_root: 输出根目录（默认 outputs/）

    Returns:
      {
        "work_dir": str,
        "asr_result": dict,
        "edit_plan": dict,
        "final_video": str,
      }
    """
    if not os.path.exists(main_video):
        raise FileNotFoundError(f"主视频不存在: {main_video}")
    for v in broll_videos:
        if not os.path.exists(v):
            raise FileNotFoundError(f"B-roll 素材不存在: {v}")

    # 工作目录
    ts = time.strftime("%Y%m%d_%H%M%S")
    output_root = output_root or os.path.join(os.getcwd(), "outputs")
    work_dir = Path(output_root) / f"oral_mashup_{ts}"
    work_dir.mkdir(parents=True, exist_ok=True)
    print(f"=== 口播混剪 Pipeline 启动 ===")
    print(f"工作目录: {work_dir}")
    print(f"主视频: {main_video}")
    print(f"B-roll: {len(broll_videos)} 个素材")

    # Step1: ASR + 静音检测
    asr_result = await run_asr_with_silence(main_video, work_dir)

    # Step2: LLM 生成剪辑方案
    edit_plan = await generate_edit_plan(
        main_video, broll_videos, asr_result, work_dir, engine=engine
    )

    # Step3: ffmpeg 执行剪辑
    final_video = execute_edit(
        edit_plan, main_video, asr_result["audio_path"], work_dir
    )

    return {
        "work_dir": str(work_dir),
        "asr_result": asr_result,
        "edit_plan": edit_plan,
        "final_video": final_video,
    }


if __name__ == "__main__":
    import sys

    # 默认测试参数
    DEFAULT_DIR = r"C:\Users\Administrator\Desktop\视频翻译"
    main_v = os.path.join(DEFAULT_DIR, "video(92).mp4")
    broll = [
        os.path.join(DEFAULT_DIR, "video(93).mp4"),
        os.path.join(DEFAULT_DIR, "video(96).mp4"),
        os.path.join(DEFAULT_DIR, "video(97).mp4"),
        os.path.join(DEFAULT_DIR, "video(98).mp4"),
    ]

    # 命令行参数覆盖
    if len(sys.argv) >= 2:
        main_v = sys.argv[1]
    if len(sys.argv) >= 3:
        broll = sys.argv[2].split(",")

    result = asyncio.run(run_mashup_pipeline(main_v, broll, engine="gpt"))
    print(f"\n=== 全部完成 ===")
    print(f"最终视频: {result['final_video']}")
