"""
提示词反推服务 - 输入爆款视频，LLM 多模态分析后输出符合 H3 规范的视频生成 prompt。

流程：
1. 用户上传爆款视频
2. 调用 LLM 多模态分析视频，输出场景描述
3. 复用 backend/services/h3_prompt_compiler 输出最终 H3 prompt

支持 engine：
  - mimo : 小米 MiMo-V2.5 原生视频理解（video_url + base64，跳过抽帧）
  - grok : 复用 SR_GROK_* 配置（hub.nocannobb.com 网关，grok-4.6，需抽帧多图输入）
  - both : mimo + grok 并行，返回两个结果供前端对比
"""
import asyncio
import base64
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from backend.services.gpt_client import gpt_client
from backend.services.h3_prompt_compiler import compile_h3_prompt
from config import settings


# 抽帧参数（grok 引擎走多图，需要抽帧）
FRAME_INTERVAL_SEC = 1.5
MIN_FRAMES = 6
MAX_FRAMES = 16
FRAME_WIDTH = 768
JPEG_QUALITY = 85

# MiMo 视频参数
MIMO_MODEL = settings.TEXT_MODEL_MIMO
MIMO_FPS = 2.0
MIMO_MEDIA_RESOLUTION = "default"  # default / max


def _get_video_duration(video_path: str) -> float:
    """用 ffprobe 获取视频时长（秒）"""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=duration",
        "-of", "json", video_path,
    ]
    try:
        r = subprocess.run(cmd, check=True, capture_output=True, text=True)
        info = json.loads(r.stdout)
        s = info.get("streams", [])
        if s and s[0].get("duration"):
            return float(s[0]["duration"])
    except Exception as e:
        print(f"  [反推] ffprobe 失败: {e}")
    return 0.0


def _plan_frame_timestamps(duration: float) -> List[float]:
    """按时长自适应规划抽帧时间点"""
    if duration <= 0:
        return [i * 0.5 for i in range(MIN_FRAMES)]
    n = max(MIN_FRAMES, min(MAX_FRAMES, int(duration / FRAME_INTERVAL_SEC)))
    if n == 1:
        return [duration / 2]
    step = duration / n
    return [min(duration - 0.05, step * i + 0.05) for i in range(n)]


def _extract_frame(video_path: str, t: float, out: Path) -> bool:
    """FFmpeg 抽单帧到 JPEG"""
    cmd = [
        "ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", video_path, "-vframes", "1",
        "-vf", f"scale='if(gt(iw,{FRAME_WIDTH}),{FRAME_WIDTH},iw)':-2",
        "-q:v", str(JPEG_QUALITY), str(out),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return out.exists()
    except Exception as e:
        print(f"  [反推] 抽帧失败 t={t:.2f}s: {e}")
        return False


def _file_to_b64(path: Path, mime: str = "image/jpeg") -> str:
    return base64.b64encode(path.read_bytes()).decode()


def _video_to_b64(video_path: str, mime: str = "video/mp4") -> str:
    return f"data:{mime};base64,{base64.b64encode(Path(video_path).read_bytes()).decode()}"


# ============================================================================
# mimo-v2.5-asr 云端识别（优先）——本地 FunASR / faster_whisper 兜底
# ============================================================================

async def _mimo_asr_transcribe(audio_path, duration: float) -> Optional[list]:
    """mimo-v2.5-asr 云端识别，返回 [{start,end,text}]；失败返回 None 让本地 ASR 兜底。

    实测（2026-10-14）：MiMo ASR 走 chat/completions + input_audio（只发音频、不带 text，
    文字提示由网关注入），只回转写文字、不返回时间戳。故按标点分句 + 字数比例估算时间戳。
    """
    if not settings.MIMO_BASE_URL or not settings.MIMO_API_KEY:
        return None
    audio_path = Path(audio_path)
    try:
        from openai import OpenAI
        client = OpenAI(api_key=settings.MIMO_API_KEY, base_url=settings.MIMO_BASE_URL)
        b64 = base64.b64encode(audio_path.read_bytes()).decode()
        text = None
        for attempt in (1, 2):
            try:
                r = client.chat.completions.create(
                    model="mimo-v2.5-asr",
                    messages=[{"role": "user", "content": [
                        {"type": "input_audio", "input_audio": {"data": b64, "format": "wav"}},
                    ]}],
                    max_tokens=4000,
                )
                text = (r.choices[0].message.content or "").strip()
                if text:
                    break
                print(f"  [反推-ASR] mimo-asr 返回空（{attempt}/2）", flush=True)
            except Exception as e:
                print(f"  [反推-ASR] mimo-asr 调用失败（{attempt}/2）：{str(e)[:160]}", flush=True)
        if not text:
            return None
        print(f"  [反推-ASR] mimo-v2.5-asr 识别 {len(text)} 字：{text[:40]}...", flush=True)
        # mimo-asr 不返回时间戳：按标点分句 + 字数比例估算每句时段
        import re
        sentences = [s.strip() for s in re.split(r"(?<=[。！？；，、])", text) if s.strip()]
        total_chars = sum(len(s) for s in sentences) or 1
        out, t = [], 0.0
        for s in sentences:
            seg_dur = duration * len(s) / total_chars
            out.append({"start": round(t, 2), "end": round(t + seg_dur, 2), "text": s})
            t += seg_dur
        return out
    except Exception as e:
        print(f"  [反推-ASR] mimo-asr 异常：{e}", flush=True)
        return None


async def _extract_voiceover(
    video_path: str, out_dir: Path, duration: float, llm_client=None
) -> Optional[List[Dict[str, Any]]]:
    """从视频提取口播文案（ASR + LLM 纠错）

    llm_client：纠错/分段用的 LLM 客户端，缺省 gpt_client（GPT55）；account-kb 传 qwen 客户端。

    流程：
    1. ffmpeg 提取音频
    2. faster_whisper 识别
    3. GPT 纠错（修复同音错字、繁简混排）

    Returns:
        [{"start": float, "end": float, "text": str}, ...] 或 None（无语音/失败）
    """
    import subprocess as _sp

    audio_path = str(out_dir / "asr_audio.wav")

    # 检查是否有音频流
    probe = _sp.run(
        ["ffmpeg", "-i", video_path, "-hide_banner"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if "Audio:" not in (probe.stderr or ""):
        print("  [反推-ASR] 视频无音频流", flush=True)
        return None

    # 读取时长（秒）
    duration = 0.0
    try:
        probe2 = _sp.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, encoding="utf-8",
        )
        duration = float((probe2.stdout or "0").strip() or 0)
    except Exception:
        pass

    # 提取音频（16kHz 单声道，适合 ASR）
    extract = _sp.run(
        ["ffmpeg", "-i", video_path, "-vn", "-acodec", "pcm_s16le",
         "-ar", "16000", "-ac", "1", "-y", audio_path],
        capture_output=True, text=True, encoding="utf-8",
    )
    if extract.returncode != 0 or not Path(audio_path).exists():
        print(f"  [反推-ASR] 音频提取失败: {extract.stderr[-200:]}", flush=True)
        return None

    # #region debug-point A:asr-input
    try:
        import importlib.util as _debug_importlib, json as _debug_json, os as _debug_os, urllib.request as _debug_request
        _debug_cfg = {}
        with open(_debug_os.path.join(".dbg", "audio-storyboard-preservation.env"), encoding="utf-8") as _debug_f:
            _debug_cfg = dict(line.strip().split("=", 1) for line in _debug_f if "=" in line)
        _debug_event = {"sessionId": _debug_cfg.get("DEBUG_SESSION_ID", "audio-storyboard-preservation"), "runId": "pre-fix", "hypothesisId": "A", "location": "prompt_reverser.py:_extract_voiceover", "msg": "[DEBUG] ASR input audio prepared", "data": {"audio_path": audio_path, "demucs_available": bool(_debug_importlib.find_spec("demucs")), "audio_stream_detected": True}}
        _debug_request.urlopen(_debug_request.Request(_debug_cfg.get("DEBUG_SERVER_URL", "http://127.0.0.1:7777/event"), data=_debug_json.dumps(_debug_event).encode(), headers={"Content-Type": "application/json"}), timeout=2).read()
    except Exception:
        pass
    # #endregion

    # 优先分离人声，避免背景歌曲歌词进入 ASR；失败时回退混合音频。
    vocal_path = Path(out_dir) / "asr_vocals.wav"
    try:
        separate = _sp.run(
            ["python", "-m", "demucs.separate", "--two-stems=vocals",
             "-n", "htdemucs", "-o", str(out_dir / "demucs"), audio_path],
            capture_output=True, text=True, encoding="utf-8", timeout=600,
        )
        demucs_vocal = out_dir / "demucs" / "htdemucs" / Path(audio_path).stem / "vocals.wav"
        if separate.returncode == 0 and demucs_vocal.exists():
            vocal_path.write_bytes(demucs_vocal.read_bytes())
            audio_path = str(vocal_path)
            print("  [反推-ASR] Demucs 人声分离成功，使用 vocals.wav", flush=True)
        else:
            print("  [反推-ASR] Demucs 分离失败，回退原始混合音频", flush=True)
    except Exception as exc:
        print(f"  [反推-ASR] Demucs 不可用，回退原始混合音频：{exc}", flush=True)

    # mimo-v2.5-asr 云端识别优先；失败/为空回退本地 FunASR / faster_whisper
    try:
        _mimo_segs = await _mimo_asr_transcribe(audio_path, duration)
        if _mimo_segs:
            return _mimo_segs
        print("  [反推-ASR] mimo-asr 未出结果，回退本地 FunASR", flush=True)
    except Exception as exc:
        print(f"  [反推-ASR] mimo-asr 异常，回退本地 FunASR：{exc}", flush=True)

    # FunASR (paraformer-zh) 识别 — 中文识别效果远优于 whisper
    # FunASR 是同步阻塞调用，必须放独立线程避免阻塞 asyncio 事件循环
    import asyncio

    def _run_funasr_sync():
        from funasr import AutoModel
        model = AutoModel(
            model="paraformer-zh",
            vad_model="fsmn-vad",
            punc_model="ct-punc",
            disable_update=True,
        )
        return model.generate(input=audio_path, batch_size_s=300)

    print("  [反推-ASR] FunASR (paraformer-zh) 识别中（独立线程）...", flush=True)
    try:
        res = await asyncio.to_thread(_run_funasr_sync)
    except ImportError:
        print("  [反推-ASR] funasr 未安装，回退 faster_whisper", flush=True)
        return await _asr_whisper_fallback(audio_path)
    except Exception as e:
        print(f"  [反推-ASR] FunASR 失败 {e}，回退 faster_whisper", flush=True)
        return await _asr_whisper_fallback(audio_path)

    raw_segments = []
    if res:
        timestamp = res[0].get("timestamp", []) or []
        text_full = res[0].get("text", "") or ""
        if timestamp and isinstance(timestamp, list):
            # FunASR 返回词级时间戳 [[start_ms, end_ms], ...]，无对应文字
            # 聚合成句子段（按停顿 >300ms 或段长 >2000ms 切分）
            agg_segs = _aggregate_funasr_timestamps(timestamp)
            if len(agg_segs) >= 2 and text_full:
                # 先结合完整上下文纠正同音字、重复字和明显病句，再按时间段切分。
                _cli = llm_client or gpt_client
                text_full = await _gpt_correct_asr_text(text_full, _cli)
                print(f"  [反推-ASR] FunASR 词级时间戳聚合为 {len(agg_segs)} 段，LLM 分配文案...", flush=True)
                raw_segments = await _gpt_split_text_to_segments(text_full, agg_segs, _cli)
            if not raw_segments and text_full:
                # 回退：整段文字
                raw_segments = [{"start": 0.0, "end": round(duration, 2), "text": text_full.strip()}]

    if not raw_segments:
        print("  [反推-ASR] FunASR 无识别结果", flush=True)
        return None

    # FunASR 识别已很准确，无需重写纠错；仅当回退到 whisper 时才走 GPT 纠错
    # 这里直接返回 raw_segments
    print(f"  [反推-ASR] FunASR 识别 {len(raw_segments)} 段", flush=True)
    return raw_segments


async def _asr_whisper_fallback(audio_path: str) -> Optional[List[Dict[str, Any]]]:
    """faster_whisper fallback：FunASR 不可用时使用"""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("  [反推-ASR] faster_whisper 也未安装，ASR 失败", flush=True)
        return None
    print("  [反推-ASR] faster_whisper 识别中（model=base, lang=zh）...", flush=True)
    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments_raw, _ = model.transcribe(
        audio_path, language="zh", beam_size=5, vad_filter=True,
    )
    raw_segments = []
    for seg in segments_raw:
        text = seg.text.strip()
        if text:
            raw_segments.append({
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "text": text,
            })
    return raw_segments if raw_segments else None


def _aggregate_funasr_timestamps(timestamps: List, max_seg_ms: int = 2200, min_gap_ms: int = 300) -> List[List[int]]:
    """聚合 FunASR 词级时间戳成句子段

    Args:
        timestamps: [[start_ms, end_ms], ...]（无对应文字）
        max_seg_ms: 段落最长时长（超过强制切分）
        min_gap_ms: 段间停顿阈值（超过则切分）

    Returns:
        [[start_ms, end_ms], ...] 聚合后的句子段
    """
    if not timestamps:
        return []
    segments = []
    cur_start = timestamps[0][0]
    cur_end = timestamps[0][1]
    for ts in timestamps[1:]:
        if len(ts) < 2:
            continue
        gap = ts[0] - cur_end
        seg_len = ts[1] - cur_start
        # 切分条件：停顿超过阈值 或 段长已超过上限
        if gap > min_gap_ms or seg_len > max_seg_ms:
            segments.append([cur_start, cur_end])
            cur_start = ts[0]
        cur_end = ts[1]
    segments.append([cur_start, cur_end])
    return segments


async def _gpt_correct_asr_text(text_full: str, gpt_client) -> str:
    """结合完整上下文校对 ASR 文本，不改变原口播含义。"""
    try:
        response = await gpt_client.chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是中文口播 ASR 校对器。只修正有充分上下文依据的同音错字、重复字、"
                        "漏字、明显病句和标点；保持原意、语气、营销信息和句子顺序。"
                        "品牌名、产品名、成分名不确定时结合全文选择最合理写法，不得虚构新卖点。"
                        "不要总结、扩写、删减或润色。只输出校对后的完整口播文本。"
                    ),
                },
                {"role": "user", "content": text_full},
            ],
            temperature=0,
            max_tokens=3000,
        )
        corrected = (
            response.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
        if corrected:
            print(
                f"  [反推-ASR] GPT 上下文校对完成：{len(text_full)} → {len(corrected)} 字",
                flush=True,
            )
            return corrected
    except Exception as exc:
        print(f"  [反推-ASR] GPT 校对失败，保留 FunASR 原文：{exc}", flush=True)
    return text_full


async def _gpt_split_text_to_segments(
    text_full: str,
    time_segments: List[List[int]],
    gpt_client,
) -> List[Dict[str, Any]]:
    """让 GPT 把整段文案按给定时间段切分回去

    Args:
        text_full: 整段文案
        time_segments: [[start_ms, end_ms], ...]
        gpt_client: GPT 客户端

    Returns:
        [{"start": float, "end": float, "text": str}, ...]
    """
    import json as _json
    import re
    try:
        n = len(time_segments)
        ts_str = "\n".join(
            f"{i+1}. [{s[0]/1000:.1f}-{s[1]/1000:.1f}s]" for i, s in enumerate(time_segments)
        )
        sys_prompt = (
            "你是中文语音文案切分助手。给定一段通顺的中文文案和 N 个时间段，"
            "请把文案严格按时间段数量切成 N 段，每段对应一个时间段。\n\n"
            "严格要求：\n"
            "1) 切出的 N 段按顺序拼起来必须等于原文案，不增删任何字符（含标点）\n"
            "2) 切分点优先选择标点（，。！？，、等）和语义停顿处\n"
            "3) 每段的字数应与对应时间段的长度（秒）大致成比例\n"
            "4) 标点符号归到前一段（避免下一段以标点开头）\n\n"
            "输出 JSON 数组，每个元素包含 start、end、text 三个字段，不要输出任何其他内容。"
        )
        user_msg = (
            f"把下面这段文案严格切成 {n} 段，对应这 {n} 个时间段：\n\n"
            f"时间段：\n{ts_str}\n\n"
            f"文案：\n{text_full}\n\n"
            f"只输出 JSON 数组，格式示例：\n"
            f'[{{"start": 0.3, "end": 1.2, "text": "赚钱第一步，"}}, ...]'
        )
        resp = await gpt_client.chat_completion(
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=3000,
        )
        out = (
            resp.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
        ).strip()

        # 提取 JSON 数组（去掉 markdown 代码块包裹）
        json_match = re.search(r'\[\s*\{.*?\}\s*\]', out, re.DOTALL)
        result = []
        if json_match:
            try:
                parsed = _json.loads(json_match.group(0))
                for item in parsed:
                    if "start" in item and "end" in item and "text" in item:
                        result.append({
                            "start": round(float(item["start"]), 2),
                            "end": round(float(item["end"]), 2),
                            "text": str(item["text"]),
                        })
            except _json.JSONDecodeError:
                pass

        # 校验拼接完整性
        if len(result) == n:
            joined = "".join(r["text"] for r in result)
            # 去掉空白后比对
            if joined.replace(" ", "") == text_full.replace(" ", ""):
                return result
            # 如果拼接不完整但有结果，仍然使用（可能 GPT 微调了标点）
            print(
                f"  [反推-ASR] GPT 切分 {len(result)} 段，拼接校验: "
                f"原文 {len(text_full)} 字 vs 拼接 {len(joined)} 字",
                flush=True,
            )
            return result

        # GPT 切分失败，按字符比例 + 标点位置智能分配
        print(
            f"  [反推-ASR] GPT 切分 {len(result)} 段（期望 {n}），按时长比例+标点切分",
            flush=True,
        )
        return _split_text_by_time_and_punctuation(text_full, time_segments)
    except Exception as e:
        print(f"  [反推-ASR] GPT 切分失败：{e}", flush=True)
        return _split_text_by_time_and_punctuation(text_full, time_segments)


def _split_text_by_time_and_punctuation(
    text_full: str,
    time_segments: List[List[int]],
) -> List[Dict[str, Any]]:
    """按时间比例 + 标点位置切分文案

    策略：先按各段时间比例计算每段应得字数，再向最近的标点对齐。
    """
    import re
    n = len(time_segments)
    if n == 0 or not text_full:
        return []
    total_dur = sum(s[1] - s[0] for s in time_segments)
    if total_dur <= 0:
        # 全 0 时长时等分
        chunk = len(text_full) // n
        result = []
        for i, s in enumerate(time_segments):
            start = i * chunk if i < n - 1 else len(text_full) - chunk * (n-1)
            end = start + chunk if i < n - 1 else len(text_full)
            result.append({
                "start": round(s[0] / 1000.0, 2),
                "end": round(s[1] / 1000.0, 2),
                "text": text_full[start:end].strip(),
            })
        return result

    # 标点集合（用于对齐切分点）
    punctuation = set("，。！？；：、,.!?;:\n")

    result = []
    pos = 0
    for i, ts in enumerate(time_segments):
        start_ms, end_ms = ts
        start_sec = start_ms / 1000.0
        end_sec = end_ms / 1000.0

        if i == n - 1:
            # 最后一段拿剩余
            seg_text = text_full[pos:]
        else:
            # 按时长比例计算目标字数
            seg_dur = end_ms - start_ms
            target_chars = max(1, int(len(text_full) * seg_dur / total_dur))
            # 🔴 clamp：无口播/极少口播的视频里 len(text_full) 远小于段数 × 每段最少 1 字，
            #    pos + target_chars 会越过字符串末尾 ⇒ 下面 `text_full[j-1]` 抛 IndexError，
            #    整条编译在 Step1 就崩（2026-10-20 实测：12.7s 只有一声模糊人声、FunASR 出 2 个字、
            #    8 个时间段的音频直接把 run_pipeline 打断）。
            target_pos = min(pos + target_chars, len(text_full))
            # 向后搜索最近的标点（在 ±50% 范围内）
            search_start = max(pos + 1, pos + target_chars // 2)
            search_end = min(len(text_full), pos + target_chars * 2)
            best_pos = target_pos
            for j in range(search_start, search_end):
                if text_full[j] in punctuation:
                    best_pos = j + 1  # 标点归到前一段
                    break
            # 如果向后没找到标点，向前搜索
            if best_pos == target_pos:
                for j in range(target_pos, search_start, -1):
                    if text_full[j-1] in punctuation:
                        best_pos = j
                        break
            seg_text = text_full[pos:best_pos]
            pos = best_pos

        result.append({
            "start": round(start_sec, 2),
            "end": round(end_sec, 2),
            "text": seg_text.strip(),
        })

    return result


def _build_reverse_prompt(duration: float, n_frames: int, extra_hint: str, voiceover_segments: Optional[List[Dict[str, Any]]] = None) -> str:
    """构造反推指令（给 LLM 用，输出 H3 风格的中文场景描述）

    Args:
        duration: 视频时长
        n_frames: 抽帧数（MiMo 走视频直传时 n_frames=0）
        extra_hint: 用户附加提示
        voiceover_segments: ASR 识别的口播文案段（含 start/end/text），None 表示无口播
    """
    hint = f"\n\n⚠️ 用户附加说明：\n{extra_hint.strip()}\n" if extra_hint and extra_hint.strip() else ""

    # 口播维度（有口播时加入）
    voiceover_section = ""
    if voiceover_segments:
        voiceover_text = " ".join(s["text"] for s in voiceover_segments)
        timeline = "\n".join(f"  [{s['start']:.1f}-{s['end']:.1f}s] {s['text']}" for s in voiceover_segments)
        voiceover_section = (
            f"\n9. 【口播文案】以下为 ASR 识别的口播内容（带时间戳），请在场景描述中"
            f"按时间轴自然融入，标注说话时段：\n{timeline}\n"
            f"完整口播文案：{voiceover_text}\n"
        )

    has_voiceover = bool(voiceover_segments)
    dim_count = "9" if has_voiceover else "8"
    dim_list = (
        "1. 【主体】画面中的人物/产品/道具（数量、形态、穿着、颜色）\n"
        "2. 【场景】室内/室外、具体环境、背景物品\n"
        "3. 【人物动作】人物姿势、手势、与产品的交互方式、动作节奏\n"
        "4. 【产品呈现】产品如何被展示（手持/摆放/特写）、产品包装/颜色/材质\n"
        "5. 【镜头景别】整体以近景/中景/远景为主，各帧景别变化\n"
        "6. 【运镜】镜头是否推拉摇移，运动节奏\n"
        "7. 【光影氛围】色调、光线方向、整体氛围\n"
        "8. 【转场节奏】画面切换频率、是否快剪\n"
    )
    if has_voiceover:
        dim_list += "9. 【口播文案】按 ASR 时间戳描述说话时段和内容，标注人物是否对口型\n"

    return (
        f"以下是爆款视频按时间顺序抽取的 {n_frames} 张关键帧"
        f"（视频总时长 {duration:.1f}s）。请仔细分析每一帧画面，输出一段中文场景描述，"
        f"用于驱动 wan3.0 视频复刻模型重新生成一段相近风格的视频。\n\n"
        f"⚠️ 输出必须包含以下维度（按顺序）：\n{dim_list}\n"
        "✅ 输出要求：\n"
        "- 中文为主，可使用专业镜头语言术语\n"
        "- 一段连续描述，不要分镜编号、不要列表、不要 markdown\n"
        f"- 8-15 句话，避免冗长，但要覆盖以上 {dim_count} 个维度\n"
        "- 末尾追加一行「H3 模式建议：T2VA」 或 「H3 模式建议：I2VA」\n"
        f"{voiceover_section}{hint}"
    )


# ============================================================================
# MiMo 引擎（原生视频理解，跳过抽帧）
# ============================================================================

async def _mimo_analyze_video(
    video_path: str,
    duration: float,
    extra_hint: str,
    voiceover_segments: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """MiMo-V2.5 原生视频理解 API（OpenAI 兼容协议，base64 直传）"""
    video_b64 = _video_to_b64(video_path)
    prompt_text = _build_reverse_prompt(duration, n_frames=0, extra_hint=extra_hint, voiceover_segments=voiceover_segments).replace(
        f"以下是爆款视频按时间顺序抽取的 0 张关键帧", "以下是爆款视频"
    ).replace(
        f"（视频总时长 {duration:.1f}s）", f"（视频总时长 {duration:.1f}s，已按 fps={MIMO_FPS} 抽帧喂给模型）"
    )

    content = [
        {
            "type": "video_url",
            "video_url": {"url": video_b64},
            "fps": MIMO_FPS,
            "media_resolution": MIMO_MEDIA_RESOLUTION,
        },
        {"type": "text", "text": prompt_text},
    ]

    messages = [
        {
            "role": "system",
            "content": (
                "你是工业级视频提示词反推工程师和审片员。必须先逐时段审查视频，再输出完整的 H3 视频提示词。"
                "只描述视频中实际可见或能由连续动作明确推断的内容，禁止补写画面没有出现的品牌、颜色、材质、人物、道具或动作。"
                "必须建立人物连续性：所有手、手臂、腿和身体部位都要归属于画面中已经出现的同一人物；局部手部操作必须说明手臂与人物身体的连接关系，禁止凭空出现一双手、悬空手臂、画外第三人或突然增加操作者。"
                "必须按视频真实顺序保留每个镜头切换、时间点、景别、运镜、动作起止、人物朝向、产品交互和声音变化；背对、侧身、转身与局部特写的前后关系必须一致。"
                "口播必须同时听音频并对照画面口型核验。ASR 文本只是候选结果，不得机械照抄明显错误、残缺或与音频不符的内容；只能修正有充分听觉和上下文依据的同音错字、漏字和断句，无法确认时不要臆造新的口播。"
                "输出前自检：镜头数量和顺序、时间轴、人物身份、手部来源、产品交互、口播段落和音效是否互相一致。输出简明精准的中文场景描述。"
            ),
        },
        {"role": "user", "content": content},
    ]

    base_url = settings.MIMO_BASE_URL
    api_key = settings.MIMO_API_KEY

    payload = {
        "model": MIMO_MODEL,
        "messages": messages,
        "max_completion_tokens": 4096,
        "temperature": 0.3,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # MiMo 偶发返回空 content（finish_reason=length 推理 tokens 用尽），自动重试 2 次
    for attempt in range(1, 4):
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                r = await client.post(
                    f"{base_url}/chat/completions",
                    headers=headers, json=payload,
                )
                r.raise_for_status()
                data = r.json()
                content_str = data["choices"][0]["message"]["content"] or ""
                # #region debug-point mimo-prompt-audit
                try:
                    import json as _audit_json, os as _audit_os, re as _audit_re, urllib.request as _audit_request
                    _audit_cfg = {}
                    with open(_audit_os.path.join(".dbg", "mimo-prompt-audit.env"), encoding="utf-8") as _audit_f:
                        _audit_cfg = dict(line.strip().split("=", 1) for line in _audit_f if "=" in line)
                    _audit_event = {
                        "sessionId": _audit_cfg.get("DEBUG_SESSION_ID", "mimo-prompt-audit"),
                        "runId": "pre-fix",
                        "hypothesisId": "A-C",
                        "location": "prompt_reverser.py:_mimo_analyze_video",
                        "msg": "[DEBUG] MiMo output captured for audit",
                        "data": {
                            "duration": duration,
                            "output_chars": len(content_str),
                            "shot_count": len(_audit_re.findall(r"\\[Shot \\d+\\]", content_str)),
                            "time_count": len(_audit_re.findall(r"\\b\\d+(?:\\.\\d+)?s\\b", content_str)),
                            "dialogue_count": len(_audit_re.findall(r"<d>.*?</d>", content_str, _audit_re.DOTALL)),
                            "has_hand_action": any(term in content_str for term in ("手", "手部", "拿", "打开", "放入")),
                            "has_continuity_terms": any(term in content_str for term in ("同一位", "同一人物", "自己的双手", "手臂与身体")),
                        },
                    }
                    _audit_request.urlopen(_audit_request.Request(_audit_cfg.get("DEBUG_SERVER_URL", "http://127.0.0.1:7777/event"), data=_audit_json.dumps(_audit_event, ensure_ascii=False).encode(), headers={"Content-Type": "application/json"}), timeout=2).read()
                except Exception:
                    pass
                # #endregion
                if content_str.strip():
                    return content_str.strip()
                print(f"  [反推-mimo] 第 {attempt} 次返回空 content，重试...", flush=True)
        except Exception as e:
            # 打印完整响应体，便于定位 400 真实原因
            _resp = getattr(e, "response", None)
            _status = getattr(_resp, "status_code", None)
            _body = ""
            if _resp is not None and hasattr(_resp, "text"):
                _body = _resp.text[:2000]
            elif hasattr(e, "args") and e.args:
                import json as _e_json
                try:
                    _body = _e_json.dumps(e.args, ensure_ascii=False, default=str)[:2000]
                except Exception:
                    _body = str(e.args)[:2000]
            print(f"  [反推-mimo] 第 {attempt} 次失败: {type(e).__name__}: {str(e)[:200]} | status={_status} | body={_body}", flush=True)
            if attempt == 3:
                raise
            await asyncio.sleep(3)
    return ""


# ============================================================================
# Grok 优先，GPT 兜底
# ============================================================================

async def _probe_grok(max_attempts: int = 2) -> bool:
    """轻量探测 mimo 通道，最多尝试两次。"""
    if not settings.MIMO_BASE_URL or not settings.MIMO_API_KEY:
        return False
    headers = {
        "Authorization": f"Bearer {settings.MIMO_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.TEXT_MODEL_MIMO_PRO,
        "messages": [{"role": "user", "content": "ping"}],
        "temperature": 0,
        "max_tokens": 1,
    }
    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                response = await client.post(
                    f"{settings.MIMO_BASE_URL.rstrip('/')}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                if data.get("choices"):
                    print(f"  [反推] Grok 通道可用（第 {attempt} 次探测）", flush=True)
                    return True
        except Exception as e:
            print(f"  [反推] Grok 通道探测失败（第 {attempt}/{max_attempts} 次）：{e}", flush=True)
    print("  [反推] Grok 通道不可用，切换 GPT 兜底", flush=True)
    return False


async def _grok_chat_completion(messages: list, temperature: float, max_tokens: int) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {settings.MIMO_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.TEXT_MODEL_MIMO_PRO,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(
            f"{settings.MIMO_BASE_URL.rstrip('/')}/chat/completions",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return response.json()


async def _grok_analyze_frames(
    frame_paths: List[Path],
    duration: float,
    extra_hint: str,
    voiceover_segments: Optional[List[Dict[str, Any]]] = None,
    use_grok: bool = True,
) -> str:
    """GPT/Grok 多模态分析关键帧序列（复用 gpt_client）"""
    frame_contents = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{_file_to_b64(p)}"},
        }
        for p in frame_paths
    ]
    prompt_text = _build_reverse_prompt(duration, len(frame_paths), extra_hint, voiceover_segments=voiceover_segments)

    messages = [
        {
            "role": "system",
            "content": (
                "你是工业级视频提示词反推工程师，擅长从关键帧序列中还原爆款视频的"
                "镜头语言、人物动作、产品呈现方式。输出简明精准的中文场景描述，"
                "禁止臆造，只描述关键帧中实际可见的内容。"
            ),
        },
        {"role": "user", "content": [{"type": "text", "text": prompt_text}] + frame_contents},
    ]

    try:
        result = await asyncio.wait_for(
            gpt_client.chat_completion(messages, temperature=0.3, max_tokens=1800),
            timeout=300.0,
        )
        return result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  [反推-grok] 失败: {e}", flush=True)
        return f"爆款视频复刻，总时长 {duration:.1f}s，包含 {len(frame_paths)} 个关键镜头。"


# ============================================================================
# 主入口
# ============================================================================

async def reverse_engineer_prompt(
    video_path: str,
    output_dir: str,
    extra_hint: str = "",
    engine: str = "mimo",
) -> Dict[str, Any]:
    """爆款视频 → H3 视频生成 prompt

    Args:
        video_path: 输入视频绝对路径
        output_dir: 工作目录
        extra_hint: 用户附加提示
        engine: mimo / grok / both（both = mimo + grok 并行对比）

    Returns:
        单引擎: {frames, scene_description, h3_prompt, mode, duration, engine, result_path}
        both: {engine: "both", mimo: {...}, grok: {...}, frames, duration, result_path}
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    duration = _get_video_duration(video_path)
    print(f"  [反推] 视频 {Path(video_path).name}，时长 {duration:.2f}s，engine={engine}", flush=True)

    # ── ASR 语音识别：提取口播文案 ──
    voiceover_segments = None
    try:
        voiceover_segments = await _extract_voiceover(video_path, out, duration)
        if voiceover_segments:
            print(f"  [反推-ASR] 识别到口播，共 {len(voiceover_segments)} 段", flush=True)
        else:
            print(f"  [反推-ASR] 无口播（纯背景音或无人声）", flush=True)
    except Exception as e:
        print(f"  [反推-ASR] 失败（跳过口播维度）：{e}", flush=True)

    # 单引擎 helper
    async def _run_single(eng: str) -> Tuple[str, List[Path], str]:
        if eng == "mimo":
            desc = await _mimo_analyze_video(video_path, duration, extra_hint, voiceover_segments=voiceover_segments)
            print(f"  [反推-mimo] 完成，{len(desc)} 字", flush=True)
            return "mimo", [], desc
        # grok：需抽帧
        timestamps = _plan_frame_timestamps(duration)
        fps = [out / f"frame_{i:02d}.jpg" for i, t in enumerate(timestamps) if _extract_frame(video_path, t, out / f"frame_{i:02d}.jpg")]
        if not fps:
            raise RuntimeError("抽帧失败，请检查 ffmpeg")
        print(f"  [反推-grok] 抽帧 {len(fps)} 张完成", flush=True)
        desc = await _grok_analyze_frames(fps, duration, extra_hint, voiceover_segments=voiceover_segments)
        print(f"  [反推-grok] 完成，{len(desc)} 字", flush=True)
        return "grok", fps, desc

    async def _compile_one(eng: str, desc: str, frames: List[Path]) -> Dict[str, Any]:
        h3 = await compile_h3_prompt(
            user_description=desc,
            image_paths=None,
            channel="runninghub",
            voiceover_segments=voiceover_segments,
            duration=duration,
            output_language="zh",
        )
        return {
            "engine": eng,
            "frames": [str(p) for p in frames],
            "scene_description": desc,
            "h3_prompt": h3.get("prompt", ""),
            "mode": h3.get("mode", "T2VA"),
            "duration": duration,
            "voiceover_segments": voiceover_segments or [],
            "voiceover_text": " ".join(s["text"] for s in voiceover_segments) if voiceover_segments else "",
        }

    if engine == "both":
        # 并行：mimo + grok
        (eng_mimo, frames_mimo, desc_mimo), (eng_grok, frames_grok, desc_grok) = await asyncio.gather(
            _run_single("mimo"), _run_single("grok"),
        )
        res_mimo = await _compile_one(eng_mimo, desc_mimo, frames_mimo)
        res_grok = await _compile_one(eng_grok, desc_grok, frames_grok)
        result = {
            "engine": "both",
            "mimo": res_mimo,
            "grok": res_grok,
            # 兼容字段（用 grok 的抽帧做预览，mimo 没有抽帧）
            "frames": res_grok["frames"],
            "duration": duration,
            "voiceover_segments": voiceover_segments or [],
            "voiceover_text": " ".join(s["text"] for s in voiceover_segments) if voiceover_segments else "",
        }
    else:
        eng, frames, desc = await _run_single(engine)
        compiled = await _compile_one(eng, desc, frames)
        result = compiled

    # 产品图拍摄角度建议（基于反推结果）
    try:
        h3_for_analysis = result.get("h3_prompt") or (result.get("mimo", {}) or {}).get("h3_prompt", "")
        scene_for_analysis = result.get("scene_description") or (result.get("mimo", {}) or {}).get("scene_description", "")
        if h3_for_analysis:
            print("  [产品图角度] 开始分析...", flush=True)
            angle_advice = await analyze_product_image_angles(h3_for_analysis, scene_for_analysis)
            result["product_image_angles"] = angle_advice
            print(f"  [产品图角度] 完成：{angle_advice.get('summary', '')}", flush=True)
    except Exception as exc:
        print(f"  [产品图角度] 跳过：{exc}", flush=True)

    # 持久化
    result_path = out / "reverse_result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["result_path"] = str(result_path)
    return result


# ─────────────────────────────────────────────────────────
# 通用化：把反推出的带具体产品描述的 prompt 改成通用模板
# 目标：用户拿到通用 prompt + 任意同类产品图，即可直接喂 H3 出视频
# ─────────────────────────────────────────────────────────

async def _gpt_generalize_chat(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """通用化节点：mimo-v2.6-pro 优先，失败两次后切换 GPT。"""
    payload = {
        "model": settings.TEXT_MODEL_MIMO_PRO,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 4000,
    }
    grok_error = None
    for attempt in range(1, 3):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{settings.MIMO_BASE_URL.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.MIMO_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                if content and content.strip():
                    print(f"  [通用化] Grok 第 {attempt} 次调用成功", flush=True)
                    return result
                grok_error = RuntimeError("Grok 返回空内容")
        except Exception as e:
            grok_error = e
            print(f"  [通用化] Grok 第 {attempt}/2 次失败：{e}", flush=True)
        if attempt < 2:
            await asyncio.sleep(1)

    print(f"  [通用化] Grok 不可用，切换 GPT 兜底：{grok_error}", flush=True)
    return await gpt_client.chat_completion(
        messages=messages,
        temperature=0.1,
        max_tokens=4000,
    )


_GENERALIZE_SYS_PROMPT = """你是视频提示词通用化专家。

输入：一段爆款视频反推出的详细提示词（含具体产品颜色、款式、品牌、材质等具体描述）。
任务：把它改写成「通用模板」，配合任意同类产品图（reference_images）即可直接喂给 H3 视频生成模型，无需修改一个字。

严格要求：
1. 保留：镜头语言（景别/运镜/焦段/Static Shot/Tracking 等）、动作节奏、场景结构、时长分配、光影风格、音效描述——这些与具体产品无关的部分一字不改地保留。
2. 泛化所有可替换的画面外观信息：产品品牌/型号/价格/颜色/款式/材质/装饰、人物服装颜色与款式、具体店铺/房间/背景布置、家具颜色与造型、道具品牌与颜色，都必须改写为同类通用描述或由 reference_images 决定；不得保留「卡其色工装套装」「白色衬衫」「黑色椅子」「现代简约服装店」这类原视频专属细节。只保留对动作、构图、景别、光影和节奏有必要的抽象属性。
3. 保留通用分镜骨架：所有原始时间节点、动作顺序、人物与产品交互、景别、运镜、幅度、速度、一镜到底/剪辑关系和时长分配必须逐项保留。禁止合并、删除、重排或重新分配任何时间节点；不得删除产品展示动作本身。
4. 口播必须原样保留：所有 `<d>...</d>` 标签、语言标签、说话人、口播文字、口播时间和顺序一律不得修改、翻译、总结、补写或删除。口播中的商品名称和商品特点也必须保留，因为口播属于原视频复刻内容。
5. 清理无关中间标记：仅清理画面描述中的 `[PRODUCT]`、`【产品】`、`<Subject 1>` 等反推占位符；不得清理或改写 `<d>...</d>` 内的任何内容。
6. 修正格式：将明显错误的时间表达统一为秒数格式（例如「1分80秒」改为「1.80s」），不改变时间点的实际含义；镜头运动参数保持原意，不擅自增加动作。
7. 输出前自检：逐一对照输入中的每个时间节点，确保输出全部存在且时间值、动作和运镜未被改变；检查画面中是否还残留具体服装、店铺、家具、道具或产品外观专属信息，发现后继续泛化。
8. 适配：改写后的 prompt 末尾加一行说明：「产品外观由 reference_images 提供，模型需严格遵循参考图的颜色/材质/结构/比例/纹理/装饰等所有可见细节」。

输出格式：
1. 第一行：product_category（产品品类，如「女鞋」「连衣裙」「口红」「美食」，用于列表展示，10字以内）
2. 空一行
3. 后面是通用化后的完整 prompt 文本（可直接喂 H3）

示例输入：
「0.00s 起，人物手持红色尖头高跟鞋在腿部旁缓慢展示并转动，展示鞋面、鞋口、鞋跟…」
示例输出：
女鞋

0.00s 起，人物手持 [PRODUCT] 在腿部旁缓慢展示并转动，展示鞋面、鞋口、鞋跟…
（后续镜头/动作/场景描述照搬保留）

产品外观由 reference_images 提供，模型需严格遵循参考图的颜色/材质/结构/比例/纹理/装饰等所有可见细节。
"""


async def _gpt_generalize_prompt(specific_prompt: str) -> Dict[str, str]:
    """用 GPT 把反推出的具体 prompt 通用化成模板。

    Returns:
        {
            "product_category": str,   # 产品品类（用于列表展示）
            "general_prompt": str,     # 通用化后的完整 prompt
        }
    """
    try:
        messages = [
            {"role": "system", "content": _GENERALIZE_SYS_PROMPT},
            {"role": "user", "content": f"请通用化以下反推提示词：\n\n{specific_prompt}"},
        ]
        resp = await _gpt_generalize_chat(messages)
        out = (
            resp.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
        ).strip()

        # 第一行是 product_category，空行后是 prompt
        lines = out.split("\n", 2)
        product_category = lines[0].strip() if lines else "未知产品"
        general_prompt = lines[2].strip() if len(lines) > 2 else out

        # fallback：如果 GPT 没按格式输出，整体当 prompt
        if not general_prompt:
            general_prompt = out
            product_category = "通用产品"

        import re as _generalize_re
        original_times = _generalize_re.findall(r"\b\d+(?:\.\d+)?s\b", specific_prompt)
        original_dialogue = _generalize_re.findall(r"<d>.*?</d>", specific_prompt, _generalize_re.DOTALL)

        # 第二道质检：首轮结果仍残留具体画面信息时，要求模型只修正画面泛化，不能改动分镜和口播。
        quality_messages = [
            {
                "role": "system",
                "content": (
                    "你是视频通用提示词终审器。输入包含原始提示词和首轮通用化结果。"
                    "只输出修正后的完整通用提示词，不要解释。"
                    "必须删除画面中的具体品牌、型号、价格、颜色、服装款式、店铺名称、"
                    "房间风格、家具颜色造型、道具外观和产品专属外观；改成同类通用描述，"
                    "或改成‘reference_images中的产品’。人物、场景、家具只保留完成动作所必需的抽象关系。"
                    "必须逐项保留原始提示词的全部时间点、动作顺序、人物与产品交互、景别、"
                    "运镜、幅度、速度、剪辑关系、时长分配、人物朝向、overall_soundscape、non_diegetic_music。"
                    "审查并修正原始提示词中的事实错误、动作归属错误、人物连续性错误和无来源的手/手臂；"
                    "所有手、手臂、腿和身体部位必须属于画面中已有的同一人物，禁止画外第三人或凭空出现的手。"
                    "如果原始提示词把局部手部操作写成了独立人物，必须改为当前人物自己的手，并明确手臂与身体的连接关系。"
                    "所有<d>...</d>标签、语言标签、说话人、口播文字、口播时间和顺序必须保留；"
                    "只有在原始口播明显是 ASR 错误、残缺或与音频/画面对不上时，才允许根据可验证证据纠正口播，禁止臆造。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "原始提示词：\n" + specific_prompt +
                    "\n\n首轮通用化结果：\n" + general_prompt +
                    "\n\n请输出最终通用提示词。"
                ),
            },
        ]
        try:
            quality_resp = await _gpt_generalize_chat(quality_messages)
            quality_out = (
                quality_resp.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            if quality_out:
                general_prompt = quality_out
        except Exception as exc:
            print(f"  [通用化] 终审失败，保留首轮结果：{exc}", flush=True)

        output_times = _generalize_re.findall(r"\b\d+(?:\.\d+)?s\b", general_prompt)
        output_dialogue = _generalize_re.findall(r"<d>.*?</d>", general_prompt, _generalize_re.DOTALL)
        if original_times and output_times[:len(original_times)] != original_times:
            print("  [通用化] 终审结果时间节点不一致，回退首轮结果", flush=True)
            general_prompt = out.split("\n\n", 1)[1].strip() if "\n\n" in out else specific_prompt
        if original_dialogue and output_dialogue != original_dialogue:
            print("  [通用化] 终审结果口播不一致，回退原始 Prompt", flush=True)
            general_prompt = specific_prompt

        # 将仍残留的具体场景称谓收敛为通用空间描述，不触碰口播内容。
        _visual_replacements = {
            "现代简约服装店": "明亮的室内零售展示空间",
            "服装店内": "室内零售展示空间内",
            "室内服装店": "室内零售展示空间",
            "服装店": "室内零售展示空间",
        }
        _dialogue_spans = list(_generalize_re.finditer(r"<d>.*?</d>", general_prompt, _generalize_re.DOTALL))
        _parts = []
        _cursor = 0
        for _span in _dialogue_spans:
            _parts.append(general_prompt[_cursor:_span.start()])
            _parts.append(general_prompt[_span.start():_span.end()])
            _cursor = _span.end()
        _parts.append(general_prompt[_cursor:])
        for _i in range(0, len(_parts), 2):
            for _old, _new in _visual_replacements.items():
                _parts[_i] = _parts[_i].replace(_old, _new)
        general_prompt = "".join(_parts)

        # #region debug-point C:generalization-shape
        try:
            import json as _debug_json, os as _debug_os, re as _debug_re, urllib.request as _debug_request
            _debug_cfg = {}
            with open(_debug_os.path.join(".dbg", "audio-storyboard-preservation.env"), encoding="utf-8") as _debug_f:
                _debug_cfg = dict(line.strip().split("=", 1) for line in _debug_f if "=" in line)
            _time_re = r"\b\d+(?:\.\d+)?s\b"
            _input_times = _debug_re.findall(_time_re, specific_prompt)
            _output_times = _debug_re.findall(_time_re, general_prompt)
            _specific_terms = [term for term in ("卡其色工装套装", "白色衬衫", "黑色椅子", "现代简约服装店", "ASICS", "黑色皮质短裙", "白色中筒袜") if term in specific_prompt]
            _remaining_terms = [term for term in _specific_terms if term in general_prompt]
            _debug_event = {"sessionId": _debug_cfg.get("DEBUG_SESSION_ID", "audio-storyboard-preservation"), "runId": "pre-fix", "hypothesisId": "C", "location": "prompt_reverser.py:_gpt_generalize_prompt", "msg": "[DEBUG] Generalization shape checked", "data": {"input_time_count": len(_input_times), "output_time_count": len(_output_times), "input_times": _input_times, "output_times": _output_times, "specific_terms": _specific_terms, "remaining_terms": _remaining_terms}}
            _debug_request.urlopen(_debug_request.Request(_debug_cfg.get("DEBUG_SERVER_URL", "http://127.0.0.1:7777/event"), data=_debug_json.dumps(_debug_event, ensure_ascii=False).encode(), headers={"Content-Type": "application/json"}), timeout=2).read()
        except Exception:
            pass
        # #endregion

        return {
            "product_category": product_category[:20],
            "general_prompt": general_prompt,
        }
    except Exception as e:
        print(f"  [通用化] GPT 处理失败：{e}", flush=True)
        return {
            "product_category": "通用产品",
            "general_prompt": specific_prompt,
        }


# ─────────────────────────────────────────────────────────
# 产品图拍摄角度建议
# 目标：基于反推出的 H3 prompt 和场景描述，告诉客户需要上传哪些角度的产品图
# ─────────────────────────────────────────────────────────

_PRODUCT_ANGLE_SYS_PROMPT = """你是电商短视频产品图拍摄顾问。

输入：爆款视频反推出的 H3 分镜 Prompt + 场景描述。
任务：分析视频中产品出现的镜头角度和展示需求，告诉客户为了复刻这条爆款，需要准备哪些角度的产品参考图。

分析维度：
1. 产品在视频中的主要展示角度（正面/侧面/俯视/仰视/背面/45度斜角/特写/细节等）
2. 每个角度的用途（整体展示/材质特写/功能演示/穿戴效果/包装等）
3. 至少需要几张图才能覆盖视频所需的产品信息
4. 如果视频涉及模特穿戴/使用场景，额外建议上传使用场景图

输出格式（严格 JSON，不要额外文字）：
{
  "product_category": "产品品类（10字内）",
  "min_images": 2,
  "recommended_images": [
    {
      "angle": "角度名称（如『正面整体』『侧面 45°』『材质特写』）",
      "purpose": "这个角度在视频中的用途",
      "required": true,
      "shot_refs": ["Shot 1", "Shot 3"]
    }
  ],
  "optional_images": [
    {
      "angle": "可选角度",
      "purpose": "加分项说明",
      "required": false
    }
  ],
  "summary": "一句话总结：客户至少需要上传几张什么角度的产品图"
}
"""


async def analyze_product_image_angles(h3_prompt: str, scene_description: str = "") -> Dict[str, Any]:
    """基于反推结果分析所需的产品图拍摄角度。

    Args:
        h3_prompt: 反推出的 H3 分镜 prompt
        scene_description: 场景描述（可选）

    Returns:
        {
            "product_category": str,
            "min_images": int,
            "recommended_images": List[Dict],
            "optional_images": List[Dict],
            "summary": str,
        }
    """
    import re as _re

    user_content = f"""H3 分镜 Prompt：
{h3_prompt}

场景描述：
{scene_description or '（无独立场景描述，请从 H3 prompt 中提取）'}

请分析这条爆款视频中产品展示所需的角度，输出 JSON。"""

    try:
        payload = {
            "model": settings.TEXT_MODEL_MIMO_PRO,
            "messages": [
                {"role": "system", "content": _PRODUCT_ANGLE_SYS_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.2,
            "max_tokens": 2000,
        }
        grok_error = None
        for attempt in range(1, 3):
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.post(
                        f"{settings.MIMO_BASE_URL.rstrip('/')}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {settings.MIMO_API_KEY}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                    if resp.status_code == 200:
                        break
                    grok_error = f"Grok HTTP {resp.status_code}"
                    await asyncio.sleep(2)
            except Exception as exc:
                grok_error = str(exc)
                await asyncio.sleep(2)
        else:
            # Grok 两次失败，切 GPT 备用
            payload["model"] = "gpt-5.6-sol"
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.SR_GROK_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )

        raw = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "") or ""
        raw = raw.strip()

        # 提取 JSON
        json_match = _re.search(r"\{[\s\S]*\}", raw)
        if json_match:
            parsed = json.loads(json_match.group())
            return parsed
        return {
            "product_category": "未知产品",
            "min_images": 1,
            "recommended_images": [{"angle": "正面整体", "purpose": "基础展示", "required": True, "shot_refs": ["Shot 1"]}],
            "optional_images": [],
            "summary": f"分析失败（{grok_error or '解析错误'}），默认建议上传 1 张正面图",
        }
    except Exception as e:
        print(f"  [产品图角度] 分析失败：{e}", flush=True)
        return {
            "product_category": "未知产品",
            "min_images": 1,
            "recommended_images": [{"angle": "正面整体", "purpose": "基础展示", "required": True, "shot_refs": ["Shot 1"]}],
            "optional_images": [],
            "summary": f"分析异常：{e}，默认建议上传 1 张正面图",
        }
