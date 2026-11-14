"""
视频翻译 Pipeline v2
流程: ASR+翻译 → 原音色克隆(IndexTTS) → 视频换人(DreamID-V) → 口型同步(LatentSync) → 成片合成

核心理念：中国人说中文 → 换成外国人说英文（对口型）
"""
import os
import json
import time
import asyncio
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)


# ═══════════════════════════════════════
# Step 1: ASR 语音识别 + 翻译
# ═══════════════════════════════════════

async def run_asr(video_path: str, source_lang: str, engine: str = "whisper") -> dict:
    """
    从视频中提取音频并识别文字
    返回: {"audio_path": str, "segments": [{"start", "end", "text"}, ...]}
    """
    audio_path = str(Path(video_path).with_suffix(".wav"))

    # 先检查视频是否有音频流
    probe_cmd = [
        "ffmpeg", "-i", video_path, "-hide_banner"
    ]
    probe_proc = subprocess.run(probe_cmd, capture_output=True, text=True, encoding="utf-8")
    probe_output = probe_proc.stderr or ""
    has_audio = "Audio:" in probe_output
    if not has_audio:
        raise RuntimeError(
            "视频文件没有音频流，无法提取文案。"
            "可能下载的是纯视频片段（抖音部分视频音视频分离）。"
            f"\nffmpeg 探测输出:\n{probe_output[-500:]}"
        )

    # 提取音频（16kHz 单声道，适合 ASR）
    extract_cmd = [
        "ffmpeg", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le",
        "-ar", "16000", "-ac", "1",
        "-y", audio_path
    ]
    proc = subprocess.run(extract_cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0 or not Path(audio_path).exists():
        raise RuntimeError(f"音频提取失败: {proc.stderr[-500:]}")

    # 同时提取一份高质量音频（给 IndexTTS 做音色参考）
    ref_audio_path = str(Path(video_path).with_suffix(".ref.wav"))
    ref_cmd = [
        "ffmpeg", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le",
        "-ar", "24000", "-ac", "1",
        "-y", ref_audio_path
    ]
    subprocess.run(ref_cmd, capture_output=True, text=True, encoding="utf-8")

    # 统一使用本地 faster_whisper。远程接口不提供 whisper-1，
    # 当前 FunASR 安装也存在模型注册和依赖冲突。
    segments = await _asr_whisper(audio_path, source_lang)

    return {
        "audio_path": audio_path,
        "ref_audio_path": ref_audio_path,
        "segments": segments,
    }


async def _asr_funasr(audio_path: str, lang: str) -> list:
    """FunASR 本地模型"""
    try:
        from funasr import AutoModel
        model = AutoModel(
            model="iic/speech_seal_pipeline",
            vad_model="iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
            punc_model="iic/punc_ct-transformer_cn-en-common-vocab471067-large",
        )
        res = model.generate(input=audio_path, batch_size_s=300)
        segments = []
        if res and len(res) > 0:
            text = res[0].get("text", "")
            timestamp = res[0].get("timestamp", [])
            if timestamp:
                for ts in timestamp:
                    segments.append({
                        "start": ts[0] / 1000.0,
                        "end": ts[1] / 1000.0,
                        "text": ts[2] if len(ts) > 2 else "",
                    })
            else:
                segments.append({"start": 0.0, "end": 0.0, "text": text})
        return segments
    except ImportError:
        return await _asr_whisper(audio_path, lang)


async def _asr_whisper(audio_path: str, lang: str) -> list:
    """本地 faster_whisper 语音识别（不依赖API）"""
    from faster_whisper import WhisperModel

    # lang 映射到 faster_whisper 格式（pt/pt-BR 均支持，faster_whisper 用 ISO 639-1）
    lang_map = {"zh": "zh", "en": "en", "ja": "ja", "ko": "ko", "pt": "pt"}
    fw_lang = lang_map.get(lang, lang)

    print(f"  → [ASR] 使用本地 faster_whisper (model=base, lang={fw_lang})")
    model = WhisperModel("base", device="cpu", compute_type="int8")

    segments = []
    segs, _info = model.transcribe(audio_path, language=fw_lang, vad_filter=True)
    for seg in segs:
        segments.append({
            "start": round(seg.start, 2),
            "end": round(seg.end, 2),
            "text": seg.text.strip(),
        })
    print(f"  → [ASR] 识别完成，共 {len(segments)} 段")
    return segments


async def translate_text(
    segments: list,
    source_lang: str,
    target_lang: str,
    engine: str = "gpt",
) -> list:
    """翻译文本段落，保留时间戳"""
    lang_names = {
        "zh": "中文", "en": "English", "ja": "日本語",
        "ko": "한국어", "fr": "Français", "de": "Deutsch", "es": "Español",
        "pt": "Português",
    }
    src_name = lang_names.get(source_lang, source_lang)
    tgt_name = lang_names.get(target_lang, target_lang)

    texts = [s["text"] for s in segments]
    bulk_text = "\n---\n".join(texts)

    if engine == "gpt":
        from openai import AsyncOpenAI
        from config import settings

        client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
        )

        prompt = (
            f"将以下文本从{src_name}翻译为{tgt_name}。"
            f"原文由 --- 分隔为多个段落，请保持分隔符和段落顺序，逐段翻译。"
            f"翻译要自然流畅，适合口语配音。\n\n{bulk_text}"
        )

        resp = await client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        translated = resp.choices[0].message.content.strip()
        translated_parts = [p.strip() for p in translated.split("---")]
    else:
        translated_parts = texts

    result = []
    for i, seg in enumerate(segments):
        t_text = translated_parts[i] if i < len(translated_parts) else seg["text"]
        result.append({
            "start": seg["start"],
            "end": seg["end"],
            "source_text": seg["text"],
            "target_text": t_text,
        })

    return result


# ═══════════════════════════════════════
# Step 2: 原音色克隆 (IndexTTS)
# ═══════════════════════════════════════

async def run_voice_clone(
    translated_segments: list,
    ref_audio_path: str,
    target_lang: str,
    output_dir: str,
) -> list:
    """
    使用 IndexTTS2 进行原音色克隆
    输入：翻译后文本 + 原始说话人参考音频
    输出：克隆音色的目标语言音频片段
    """
    os.makedirs(output_dir, exist_ok=True)

    # 合并所有翻译文本为一段完整配音稿
    full_text = " ".join(s["target_text"] for s in translated_segments)
    full_audio_path = os.path.join(output_dir, "cloned_full.wav")

    await _index_tts_clone(
        text=full_text,
        ref_audio=ref_audio_path,
        output_path=full_audio_path,
        target_lang=target_lang,
    )

    # 根据时间戳将完整音频切片为片段
    total_duration = _get_audio_duration(full_audio_path)
    video_duration = max(s["end"] for s in translated_segments) if translated_segments else total_duration

    result = []
    for i, seg in enumerate(translated_segments):
        seg_audio_path = os.path.join(output_dir, f"clone_{i:04d}.wav")
        start = seg["start"]
        end = seg["end"] if seg["end"] > 0 else total_duration

        # 用 ffmpeg 切片
        cmd = [
            "ffmpeg", "-y",
            "-i", full_audio_path,
            "-ss", str(start),
            "-to", str(end),
            "-c", "pcm_s16le",
            seg_audio_path,
        ]
        subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")

        dur = _get_audio_duration(seg_audio_path)
        result.append({
            **seg,
            "tts_audio_path": seg_audio_path,
            "tts_duration": dur,
            "full_cloned_audio": full_audio_path,
        })

    return result


async def run_us_female_tts(
    translated_segments: list,
    output_dir: str,
    voice: str = "en-US-JennyNeural",
) -> str:
    """按原字幕时间轴生成自然的美式女声完整音轨。"""
    import edge_tts

    os.makedirs(output_dir, exist_ok=True)
    timeline_parts = []
    cursor = 0.0
    for i, seg in enumerate(translated_segments):
        start = max(cursor, float(seg.get("start", cursor)))
        end = max(start + 0.2, float(seg.get("end", start + 0.2)))
        if start > cursor:
            silence_path = os.path.join(output_dir, f"silence_{i:04d}.wav")
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                "-t", str(start - cursor), "-c:a", "pcm_s16le", silence_path,
            ], capture_output=True, text=True)
            timeline_parts.append(silence_path)

        mp3_path = os.path.join(output_dir, f"voice_{i:04d}.mp3")
        wav_path = os.path.join(output_dir, f"voice_{i:04d}.wav")
        await edge_tts.Communicate(seg.get("target_text", ""), voice).save(mp3_path)
        source_duration = max(0.01, _get_audio_duration(mp3_path))
        target_duration = end - start
        tempo = source_duration / target_duration
        filters = []
        while tempo > 2.0:
            filters.append("atempo=2.0")
            tempo /= 2.0
        while tempo < 0.5:
            filters.append("atempo=0.5")
            tempo /= 0.5
        filters.append(f"atempo={tempo:.6f}")
        filters.append(f"apad=pad_dur={target_duration}")
        subprocess.run([
            "ffmpeg", "-y", "-i", mp3_path, "-af", ",".join(filters),
            "-t", str(target_duration), "-ar", "24000", "-ac", "1",
            "-c:a", "pcm_s16le", wav_path,
        ], capture_output=True, text=True)
        if not os.path.exists(wav_path):
            raise RuntimeError(f"美式女声片段生成失败: {i + 1}")
        timeline_parts.append(wav_path)
        cursor = end

    full_audio_path = os.path.join(output_dir, "us_female_full.wav")
    concat_path = os.path.join(output_dir, "voice_concat.txt")
    with open(concat_path, "w", encoding="utf-8") as f:
        for part in timeline_parts:
            f.write(f"file '{os.path.abspath(part).replace(chr(39), chr(39) * 2)}'\n")
    proc = subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_path,
        "-c:a", "pcm_s16le", full_audio_path,
    ], capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0 or not os.path.exists(full_audio_path):
        raise RuntimeError(f"美式女声音轨合成失败: {proc.stderr[-500:]}")
    return full_audio_path


async def _index_tts_clone(
    text: str,
    ref_audio: str,
    output_path: str,
    target_lang: str = "en",
):
    """
    调用 IndexTTS2 进行音色克隆
    强制使用 RunningHub 声音克隆 API，不兜底
    """
    # #region debug-point B:indextts-entry
    try:
        import urllib.request as _debug_ur
        _debug_ur.urlopen(_debug_ur.Request("http://127.0.0.1:7777/event", data=json.dumps({"sessionId":"video-translate-intelfine","runId":"pre-fix","hypothesisId":"B","location":"video_translate_pipeline.py:_index_tts_clone","msg":"[DEBUG] IndexTTS clone entry","data":{"targetLang":target_lang,"outputPath":output_path,"provider":"runninghub","appId":"1965614643077070850"},"ts":int(time.time()*1000)}).encode(), headers={"Content-Type":"application/json"}), timeout=2).read()
    except Exception:
        pass
    # #endregion
    await voice_clone_to_file(text, ref_audio, output_path)


def _clone_note_write(output_path: str, rec: dict) -> None:
    """把上游 taskId 写在产物旁边 —— 提交成功那一刻就写，不等音频落盘。

    非致命，但必须出声：没有这份留痕，进程一死就没人捞得回，而重投 = 双花（按 GPU 秒真扣）。
    """
    try:
        Path(str(output_path) + ".clone.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning(
            f"[VoiceClone] 🔴 taskId 留痕写不下（{type(exc).__name__}: {exc}）"
            f"⇒ 这条任务死了没人捞得回；先按 taskId={rec.get('task_id')} 只查询，别重投")


async def voice_clone_to_file(text: str, ref_audio: str, output_path: str) -> dict:
    """声音克隆的唯一咽喉：参考音频 + 待说文本 ⇒ 克隆音色的干声落盘。

    AI App 1965614643077070850（IndexTTS）。节点映射只有这一份：13=主模仿音色、15=情绪、14=文本。
    🔴 这条腿直连 RunningHub、【不在网关账本上】（无预扣/结算/审计）⇒ 跑前必须报价并等明确同意，
       跑完把返回的 usage 报回；`voice-clone` 档与超级复刻的改口播共用这一条腿，不许各写一份。
    提交成功那一刻就把 taskId 写在产物旁边的 `<产物>.clone.json` —— 进程死了还能只查询捞回，
    重新提交 = 双花（上游按 GPU 秒真扣）。
    强制使用此接口，不兜底。失败直接抛出异常。
    """
    from config import settings
    import httpx

    api_key = settings.RUNNINGHUB_API_KEY
    if not api_key:
        raise RuntimeError("RunningHub API Key 未配置")

    base_url = "https://www.runninghub.ai/openapi/v2"
    app_id = "1965614643077070850"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    # 上传参考音频 —— 🔴 只走网关那一条 upload（AGENTS.md：host/端点/key 优先级只在这一处解析）。
    #    原来这里自己 POST `/openapi/v2/media/upload/binary`，失败时 message 是空串 ⇒ 日志只剩
    #    一句"RunningHub 上传失败: "，没法定位；而同一个文件经 gateway.upload_media 一次就成
    #    （2026-10-14 现网：克隆卡在这一步，图却上传成功）。
    audio_url = await _public_url_via_gateway(ref_audio)
    logger.info(f"[VoiceClone] 参考音频已上传: {audio_url}")

    # 提交声音克隆任务
    submit_url = f"{base_url}/run/ai-app/{app_id}"
    body = {
        "nodeInfoList": [
            {
                "nodeId": "13",
                "fieldName": "audio",
                "fieldValue": audio_url,
                "description": "Need to imitate the sound (mainly)",
            },
            {
                "nodeId": "15",
                "fieldName": "audio",
                "fieldValue": audio_url,
                "description": "Emotional tone to be imitated (secondary)",
            },
            {
                "nodeId": "14",
                "fieldName": "value",
                "fieldValue": text,
                "description": "Input mimicked text",
            },
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
            resp = await client.post(submit_url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException as e:
        raise RuntimeError(f"RunningHub 声音克隆提交超时: {type(e).__name__}: {e}") from e
    except httpx.HTTPError as e:
        raise RuntimeError(f"RunningHub 声音克隆提交网络错误: {type(e).__name__}: {e}") from e

    task_id = data.get("taskId", "")
    if not task_id:
        raise RuntimeError(f"RunningHub 声音克隆提交失败，无 taskId: {data}")
    logger.info(f"[VoiceClone] 任务已提交, taskId={task_id}")
    _clone_note_write(output_path, {"task_id": task_id, "app_id": app_id, "text": text,
                                    "ref_audio": str(ref_audio), "submitted_at": time.time()})

    # 轮询等待完成（复用同一个 client，避免反复创建连接）
    query_url = f"{base_url}/query"
    max_wait = 900  # 15 分钟
    poll_interval = 5
    start = time.time()

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=30.0)) as client:
            last_status = ""
            while time.time() - start < max_wait:
                await asyncio.sleep(poll_interval)
                try:
                    resp = await client.post(
                        query_url, headers=headers, json={"taskId": task_id}
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except httpx.TimeoutException as e:
                    logger.warning(f"[VoiceClone] 查询超时，重试: {e}")
                    continue
                except httpx.HTTPError as e:
                    logger.warning(f"[VoiceClone] 查询网络错误，重试: {e}")
                    await asyncio.sleep(min(30, poll_interval * 2))
                    continue

                status = data.get("status", "")
                if status != last_status:
                    logger.info(f"[VoiceClone] 状态变更: {last_status} → {status}")
                    last_status = status

                if status == "SUCCESS":
                    results = data.get("results", [])
                    usage = data.get("usage") or {}
                    if results:
                        result_url = results[0].get("url", "")
                        if result_url:
                            # 下载音频到本地
                            await _download_file(result_url, output_path)
                            logger.info(f"[VoiceClone] 音频已下载: {output_path}")
                            # 如果输出是 mp3/m4a 等格式，转为 wav
                            if not output_path.endswith(".wav"):
                                wav_path = output_path.rsplit(".", 1)[0] + ".wav"
                                cmd = [
                                    "ffmpeg", "-y", "-i", output_path,
                                    "-acodec", "pcm_s16le", "-ar", "24000", "-ac", "1",
                                    wav_path,
                                ]
                                subprocess.run(cmd, capture_output=True, text=True)
                                if os.path.exists(wav_path):
                                    os.replace(wav_path, output_path)
                            # 留痕补全：usage 与产物写回同一份 clone.json —— 这条腿不在网关账本上，
                            # 报"实扣多少"只有这一份出处。
                            _clone_note_write(output_path, {
                                "task_id": task_id, "app_id": app_id, "text": text,
                                "ref_audio": str(ref_audio), "status": "SUCCESS",
                                "usage": usage, "result_url": result_url,
                                "finished_at": time.time()})
                            logger.info(f"[VoiceClone] usage={usage}")
                            return {"task_id": task_id, "output_path": str(output_path),
                                    "usage": usage, "result_url": result_url}
                    raise RuntimeError(f"RunningHub 声音克隆 SUCCESS 但无输出: {data}")
                elif status == "FAILED":
                    err_msg = data.get("errorMessage", "") or data.get("msg", "")
                    raise RuntimeError(f"RunningHub 声音克隆执行失败: {err_msg} (完整响应: {data})")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"RunningHub 声音克隆异常: {type(e).__name__}: {e}") from e

    raise RuntimeError(f"RunningHub 声音克隆超时（{max_wait}s），最后状态: {last_status}")


async def _public_url_via_gateway(path: str, key_hint: str = "wan3") -> str:
    """把本地素材交给【网关的 upload】换成公网直链 —— 素材上公网只允许这一条路。

    🔴 为什么不留第二条：这条腿原先自己拼 HTTP，失败时上游给的 message 是空串，
       日志就只剩"RunningHub 上传失败: "，人无法定位；网关那条同文件一次就成，
       且 host/端点/key 优先级只在那一处解析（AGENTS.md 的 upload 纪律）。
    """
    from backend import gateway as gw
    r = gw.upload_media(str(path), key_hint=key_hint) or {}
    res = r.get("result") or {}
    url = str(res.get("download_url") or "").strip() if isinstance(res, dict) else ""
    if not (r.get("ok") and url):
        raise RuntimeError(
            "素材上传没成功（走网关 upload）：%s ｜ 文件=%s"
            % (str(r.get("error") or "没回 download_url")[:220], os.path.basename(str(path))))
    return url


async def _runninghub_upload_file(file_path: str, api_key: str, base_url: str) -> str:
    """上传文件到 RunningHub，返回下载 URL"""
    import httpx

    upload_url = f"{base_url}/media/upload/binary"
    async with httpx.AsyncClient(timeout=120.0) as client:
        with open(file_path, "rb") as f:
            resp = await client.post(
                upload_url,
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (os.path.basename(file_path), f)},
            )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") == 0:
            return data["data"]["download_url"]
        else:
            raise RuntimeError(f"RunningHub 上传失败: {data.get('message', '')}")


# ═══════════════════════════════════════
# Step 3: 视频换人 (RunningHub DreamID-V)
# ═══════════════════════════════════════

async def run_face_swap(
    video_path: str,
    target_face_image: str,
    output_dir: str,
) -> str:
    """
    使用 RunningHub DreamID-V 工作流进行视频换人
    输入：原视频 + 目标人脸图片（外国人）
    输出：换人后的视频

    长视频自动拆分：超过 SEGMENT_SECONDS 秒的视频切成多段分别换脸再拼接，
    避免 RunningHub 实例 OOM。
    """
    from backend.services.runninghub_client import RunningHubClient
    from config import settings

    app_id = getattr(settings, "RUNNINGHUB_FACESWAP_WORKFLOW_ID", "")
    if not app_id:
        raise RuntimeError("未配置视频换人 AI App ID")

    os.makedirs(output_dir, exist_ok=True)
    # DreamID-V 用企业级 key（避免消费级 810 限制）— 上传也必须用同一 key
    enterprise_key = getattr(settings, "RUNNINGHUB_ENTERPRISE_API_KEY", "")
    image_url = await _upload_runninghub_ai_resource(target_face_image, api_key=enterprise_key)
    duration = max(1, round(_get_audio_duration(video_path)))
    output_path = os.path.join(output_dir, "face_swapped.mp4")

    # ── 长视频拆分：超过阈值则切段，逐段换脸再拼接 ──
    SEGMENT_SECONDS = 10  # 每段最大时长，超过 10s 拆分避免 OOM
    if duration <= SEGMENT_SECONDS:
        # 短视频：直接整段提交
        video_url = await _upload_runninghub_ai_resource(video_path, api_key=enterprise_key)
        return await _run_runninghub_ai_app(
            app_id,
            [
                {"nodeId": "172", "fieldName": "video", "fieldValue": video_url},
                {"nodeId": "149", "fieldName": "image", "fieldValue": image_url},
                {"nodeId": "135", "fieldName": "value", "fieldValue": "30"},
                {"nodeId": "154", "fieldName": "value", "fieldValue": str(duration)},
                {"nodeId": "145", "fieldName": "value", "fieldValue": "1280"},
                {"nodeId": "192", "fieldName": "text", "fieldValue": "保持人物面部表情和头部一致不变，增强真实感。手指要清晰可见"},
                {"nodeId": "170", "fieldName": "text", "fieldValue": "人"},
            ],
            output_path,
            api_key=enterprise_key,
        )

    # ── 长视频：按 SEGMENT_SECONDS 切段 → 逐段换脸 → ffmpeg 拼接 ──
    import subprocess as _sp
    seg_dir = os.path.join(output_dir, "segments")
    os.makedirs(seg_dir, exist_ok=True)
    # 1. ffmpeg 无损切片（按关键帧，避免音画不同步）
    seg_paths = []
    seg_idx = 0
    cur = 0.0
    while cur < duration:
        seg_dur = min(SEGMENT_SECONDS, duration - cur)
        seg_path = os.path.join(seg_dir, f"seg_{seg_idx:03d}.mp4")
        # -ss 在 -i 前：快速 seek；-c copy 无损拷贝（可能不在关键帧对齐，但 DreamID-V 会重新编码）
        cmd = ["ffmpeg", "-y", "-ss", f"{cur:.3f}", "-t", f"{seg_dur:.3f}",
               "-i", video_path, "-c", "copy", seg_path]
        proc = _sp.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if proc.returncode != 0 or not os.path.exists(seg_path):
            # copy 失败降级为重编码
            cmd = ["ffmpeg", "-y", "-ss", f"{cur:.3f}", "-t", f"{seg_dur:.3f}",
                   "-i", video_path, "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                   "-pix_fmt", "yuv420p", "-c:a", "aac", seg_path]
            proc = _sp.run(cmd, capture_output=True, text=True, encoding="utf-8")
            if proc.returncode != 0 or not os.path.exists(seg_path):
                raise RuntimeError(f"视频切片失败 seg_{seg_idx:03d}: {proc.stderr[-500:]}")
        seg_paths.append(seg_path)
        seg_idx += 1
        cur += seg_dur
    print(f"  [DreamID-V] 长视频({duration}s)拆分为 {len(seg_paths)} 段（每段≤{SEGMENT_SECONDS}s）", flush=True)

    # 2. 逐段上传 + 提交 DreamID-V（3 并发）
    import asyncio as _aio
    CONCURRENCY = 3

    async def _process_one_segment(i: int, sp: str) -> str:
        seg_dur = min(SEGMENT_SECONDS, duration - i * SEGMENT_SECONDS)
        seg_out = os.path.join(seg_dir, f"swapped_{i:03d}.mp4")
        # 复用检查：若已存在则跳过
        if os.path.exists(seg_out) and os.path.getsize(seg_out) > 1024:
            print(f"  [DreamID-V] 段 {i+1}/{len(seg_paths)} 已存在，跳过", flush=True)
            return seg_out
        print(f"  [DreamID-V] 段 {i+1}/{len(seg_paths)} 上传+提交...", flush=True)
        seg_url = await _upload_runninghub_ai_resource(sp, api_key=enterprise_key)
        await _run_runninghub_ai_app(
            app_id,
            [
                {"nodeId": "172", "fieldName": "video", "fieldValue": seg_url},
                {"nodeId": "149", "fieldName": "image", "fieldValue": image_url},
                {"nodeId": "135", "fieldName": "value", "fieldValue": "30"},
                {"nodeId": "154", "fieldName": "value", "fieldValue": str(seg_dur)},
                {"nodeId": "145", "fieldName": "value", "fieldValue": "1280"},
                {"nodeId": "192", "fieldName": "text", "fieldValue": "保持人物面部表情和头部一致不变，增强真实感。手指要清晰可见"},
                {"nodeId": "170", "fieldName": "text", "fieldValue": "人"},
            ],
            seg_out,
            api_key=enterprise_key,
        )
        print(f"  [DreamID-V] 段 {i+1}/{len(seg_paths)} 完成", flush=True)
        return seg_out

    sem = _aio.Semaphore(CONCURRENCY)

    async def _bounded_process(i: int, sp: str) -> str:
        async with sem:
            return await _process_one_segment(i, sp)

    print(f"  [DreamID-V] 启动 {CONCURRENCY} 并发处理 {len(seg_paths)} 段", flush=True)
    swapped_segs = await _aio.gather(*[_bounded_process(i, sp) for i, sp in enumerate(seg_paths)])

    # 3. ffmpeg concat 拼接
    concat_list = os.path.join(seg_dir, "concat_list.txt")
    with open(concat_list, "w", encoding="utf-8") as f:
        for sp in swapped_segs:
            # ffmpeg concat 要求：绝对路径 + 正斜杠（反斜杠会被转义误解析）
            abs_path = os.path.abspath(sp).replace(chr(92), '/')
            f.write(f"file '{abs_path}'\n")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
           "-c", "copy", output_path]
    proc = _sp.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0 or not os.path.exists(output_path):
        # copy 失败降级为重编码
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
               "-c:v", "libx264", "-preset", "fast", "-crf", "18",
               "-pix_fmt", "yuv420p", "-c:a", "aac", output_path]
        proc = _sp.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if proc.returncode != 0 or not os.path.exists(output_path):
            raise RuntimeError(f"视频拼接失败: {proc.stderr[-500:]}")
    print(f"  [DreamID-V] {len(swapped_segs)} 段拼接完成 → {output_path}", flush=True)
    return output_path


async def _download_file(url: str, output_path: str):
    """下载文件到本地"""
    import httpx
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        with open(output_path, "wb") as f:
            f.write(resp.content)


# ═══════════════════════════════════════
# Step 4: 口型同步 (RunningHub LatentSync 数字人)
# ─────────────────────────────────────────
# 工作流: App ID 2044675464228245506 (LatentSync)
#   - 逐帧人脸检测驱动口型，无人脸帧自动跳过不渲染
#   - 长视频支持（无单段时长限制）
#   - 与默认 RunningHub key 隔离，使用企业级专用 key
#
# 节点配置：
#   nodeId=228, fieldName=video  → 原视频
#   nodeId=125, fieldName=audio  → 校对音频
#   nodeId=343, fieldName=value  → 帧率 (30)
#   nodeId=345, fieldName=value  → 宽度 (720)
#   nodeId=344, fieldName=value  → 高度 (1280)
#   nodeId=342, fieldName=text   → 时长 (0:10)
#   nodeId=347, fieldName=text   → 说话状态描述
#
# 选择性处理策略（run_selective_lip_sync 为主入口）：
#   - 有人脸段 → 送对口型工作流
#   - 无人脸段 → 原样保留画面 + 替换音轨
#   - 拼接成片，支持"画面无人/转场视频"输入
# ═══════════════════════════════════════

async def run_lip_sync(
    video_path: str,
    audio_path: str,
    output_dir: str,
) -> str:
    """
    使用 RunningHub LatentSync 数字人工作流进行口型同步。
    输入：原视频/换人后视频 + 克隆音色英文音频
    输出：对口型的视频段（仅用于有人脸段，无人脸段应走 run_selective_lip_sync 路径）
    """
    from config import settings

    app_id = getattr(settings, "RUNNINGHUB_LIPSYNC_WORKFLOW_ID", "")
    if not app_id:
        raise RuntimeError("未配置口型同步工作流 ID")

    # 确保输出目录存在（调用方可能传未创建的子目录如 rh_000）
    os.makedirs(output_dir, exist_ok=True)

    # LatentSync 工作流使用专用企业级 key，与默认 key 隔离
    api_key = getattr(settings, "RUNNINGHUB_LIPSYNC_API_KEY", None) or settings.RUNNINGHUB_API_KEY
    video_url = await _upload_runninghub_ai_resource(video_path, api_key=api_key)
    audio_url = await _upload_runninghub_ai_resource(audio_path, api_key=api_key)
    output_path = os.path.join(output_dir, "lip_synced.mp4")

    # 自动获取视频元数据（时长、分辨率、帧率）以填充工作流参数
    import subprocess
    import json as _json
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", video_path],
            capture_output=True, text=True, timeout=30
        )
        streams = _json.loads(probe.stdout).get("streams", [])
        vstream = next((s for s in streams if s.get("codec_type") == "video"), {})
        duration = float(vstream.get("duration", 0)) or 10.0
        width = int(vstream.get("width", 0)) or settings.RUNNINGHUB_LIPSYNC_WIDTH
        height = int(vstream.get("height", 0)) or settings.RUNNINGHUB_LIPSYNC_HEIGHT
        fps_num, fps_den = (vstream.get("r_frame_rate", "30/1") or "30/1").split("/")
        fps = int(float(fps_num) / float(fps_den)) if float(fps_den) > 0 else settings.RUNNINGHUB_LIPSYNC_FPS
    except Exception:
        duration = 10.0
        width = settings.RUNNINGHUB_LIPSYNC_WIDTH
        height = settings.RUNNINGHUB_LIPSYNC_HEIGHT
        fps = settings.RUNNINGHUB_LIPSYNC_FPS

    # 时长格式化为 "M:SS"
    mins = int(duration // 60)
    secs = int(duration % 60)
    duration_str = f"{mins}:{secs:02d}"

    # LatentSync 工作流 (2044675464228245506) 节点配置：
    node_info = [
        {"nodeId": settings.RUNNINGHUB_LIPSYNC_VIDEO_NODE,   "fieldName": "video", "fieldValue": video_url},
        {"nodeId": settings.RUNNINGHUB_LIPSYNC_AUDIO_NODE,   "fieldName": "audio", "fieldValue": audio_url},
        {"nodeId": settings.RUNNINGHUB_LIPSYNC_FPS_NODE,     "fieldName": "value", "fieldValue": str(fps)},
        {"nodeId": settings.RUNNINGHUB_LIPSYNC_WIDTH_NODE,   "fieldName": "value", "fieldValue": str(width)},
        {"nodeId": settings.RUNNINGHUB_LIPSYNC_HEIGHT_NODE,  "fieldName": "value", "fieldValue": str(height)},
        {"nodeId": settings.RUNNINGHUB_LIPSYNC_DURATION_NODE, "fieldName": "text", "fieldValue": duration_str},
        {"nodeId": settings.RUNNINGHUB_LIPSYNC_DESC_NODE,    "fieldName": "text", "fieldValue": settings.RUNNINGHUB_LIPSYNC_DESC},
    ]
    return await _run_runninghub_ai_app(
        app_id,
        node_info,
        output_path,
        api_key=api_key,
    )


def _presenter_face_coverage(video_path: str, sample_interval: float = 1.0) -> float:
    """计算整条主播母版的人脸覆盖率，防止将已混入素材的视频送入口型模型。"""
    import cv2
    from backend.services.character_detector import _detect_faces

    duration = _get_audio_duration(video_path)
    if duration <= 0:
        return 0.0
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return 0.0
    sampled = hits = 0
    try:
        current = 0.0
        while current < duration:
            cap.set(cv2.CAP_PROP_POS_MSEC, current * 1000)
            ok, frame = cap.read()
            if ok:
                sampled += 1
                try:
                    if _detect_faces(frame):
                        hits += 1
                except Exception as e:
                    logger.warning(f"人脸检测器不可用，按纯主播输入继续: {e}")
                    return 1.0
            current += sample_interval
    finally:
        cap.release()
    return hits / sampled if sampled else 0.0


def _segment_has_face(video_path: str, start: float, end: float) -> bool:
    """在说话段内多点采样，至少两帧有人脸才允许口型同步。"""
    import cv2
    from backend.services.character_detector import _detect_faces

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return False
    duration = max(0.1, end - start)
    sample_times = [start + duration * ratio for ratio in (0.2, 0.5, 0.8)]
    hits = 0
    try:
        for sample_time in sample_times:
            cap.set(cv2.CAP_PROP_POS_MSEC, sample_time * 1000)
            ok, frame = cap.read()
            if ok and _detect_faces(frame):
                hits += 1
    finally:
        cap.release()
    return hits >= 2


def _cut_media(source: str, start: float, duration: float, output: str, audio_only: bool = False):
    cmd = ["ffmpeg", "-y", "-ss", str(start), "-t", str(duration), "-i", source]
    if audio_only:
        cmd += ["-vn", "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", output]
    else:
        cmd += ["-an", "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", output]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0 or not os.path.exists(output):
        raise RuntimeError(f"视频分段失败: {proc.stderr[-500:]}")


async def run_selective_lip_sync(
    video_path: str,
    audio_path: str,
    segments: list,
    output_dir: str,
) -> str:
    """选择性口型同步（LatentSync）：
    - 按 ASR segments 时间轴逐段做人脸检测
    - 有人脸段送 LatentSync 对口型，无人脸段原样保留画面
    - 支持纯主播 / 转场视频 / 无人画面 三类输入
    - 全程无人脸 → 直接返回原视频（后续 Step 5 只做音轨替换）
    """
    os.makedirs(output_dir, exist_ok=True)
    video_duration = _get_audio_duration(video_path)
    face_intervals = []
    for seg in segments:
        start = max(0.0, float(seg.get("start", 0)))
        end = min(video_duration, float(seg.get("end", start)))
        if end - start >= 0.25 and _segment_has_face(video_path, start, end):
            if face_intervals and start - face_intervals[-1][1] <= 0.25:
                face_intervals[-1][1] = end
            else:
                face_intervals.append([start, end])

    if not face_intervals:
        logger.info("[LatentSync] 所有说话段均未检测到稳定人脸，跳过口型同步（按原画面 + 替换音轨处理）")
        return video_path

    chunks = []
    cursor = 0.0
    for start, end in face_intervals:
        if start > cursor + 0.01:
            chunks.append((cursor, start, False))
        chunks.append((start, end, True))
        cursor = end
    if cursor < video_duration - 0.01:
        chunks.append((cursor, video_duration, False))

    normalized = []
    for index, (start, end, needs_lipsync) in enumerate(chunks):
        duration = end - start
        raw_video = os.path.join(output_dir, f"chunk_{index:03d}_raw.mp4")
        normalized_video = os.path.join(output_dir, f"chunk_{index:03d}.mp4")
        _cut_media(video_path, start, duration, raw_video)
        source_video = raw_video
        if needs_lipsync:
            chunk_audio = os.path.join(output_dir, f"chunk_{index:03d}.wav")
            _cut_media(audio_path, start, duration, chunk_audio, audio_only=True)
            source_video = await run_lip_sync(
                raw_video, chunk_audio, os.path.join(output_dir, f"rh_{index:03d}")
            )
        _cut_media(source_video, 0, duration, normalized_video)
        normalized.append(normalized_video)

    concat_file = os.path.join(output_dir, "concat.txt")
    with open(concat_file, "w", encoding="utf-8") as f:
        for path in normalized:
            f.write(f"file '{os.path.abspath(path)}'\n")
    output_path = os.path.join(output_dir, "selective_lip_synced.mp4")
    proc = subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file,
        "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p", output_path,
    ], capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0 or not os.path.exists(output_path):
        raise RuntimeError(f"口型分段拼接失败: {proc.stderr[-500:]}")
    return output_path


async def _merge_audio_track(video_path: str, audio_path: str, output_dir: str) -> str:
    """仅合并音轨（不做口型同步的回退方案）"""
    output_path = os.path.join(output_dir, "merged_video.mp4")
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
        output_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"音轨合并失败: {proc.stderr}")
    return output_path


async def _fallback_merge_av(video_path: str, audio_path: str, output_path: str) -> None:
    """HyperFrames 失败时的兜底：用 ffmpeg 把视频画面 + 英文克隆音轨合成成片。

    用途：
    - 无人脸段全程跳过口型同步 → final_video 是原视频画面，需替换音轨
    - HyperFrames CLI 渲染失败 → 仍要产出可用的翻译视频
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart",
        output_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0 or not os.path.exists(output_path):
        raise RuntimeError(f"音视频合并失败: {proc.stderr[-500:]}")


# ═══════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════

def _get_audio_duration(audio_path: str) -> float:
    """获取音频文件时长"""
    try:
        cmd = [
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def _generate_srt(segments: list, output_path: str):
    """生成 SRT 字幕文件"""
    with open(output_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            start = _format_srt_time(seg.get("start", 0))
            end = _format_srt_time(seg.get("end", 0))
            f.write(f"{i}\n")
            f.write(f"{start} --> {end}\n")
            f.write(f"{seg.get('target_text', '')}\n\n")


def _format_srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


async def _run_runninghub_ai_app(app_id: str, node_info_list: list, output_path: str, api_key: str = None) -> str:
    """提交 RunningHub AI App，并下载第一个视频结果。

    api_key 可选：指定专用 key（如 LatentSync 企业级 key），默认用 settings.RUNNINGHUB_API_KEY

    复用机制：提交新任务前，先查 output_path 同目录的 runninghub_tasks.json，
    若有该工作流的旧任务仍在 RUNNING/QUEUED 状态，直接复用并继续轮询，避免重复提交。
    """
    import httpx
    from config import settings

    api_key = api_key or settings.RUNNINGHUB_API_KEY
    base_url = "https://www.runninghub.ai"
    headers = {"Authorization": f"Bearer {api_key}"}

    # ── 复用检查：扫历史 RunningHub 任务记录，看是否有同 app_id 的任务仍可复用 ──
    task_id = ""
    stage = "AI App"
    reused_task = await _find_reusable_runninghub_task(output_path, app_id, api_key=api_key)
    if reused_task:
        task_id = reused_task["task_id"]
        stage = reused_task.get("stage", stage)
        logger.info(f"[{stage}] 检测到未完成任务 {task_id}，复用并继续轮询（不重新提交）")
    else:
        # 没有可复用任务，正常提交新任务
        async with httpx.AsyncClient(timeout=300.0) as submit_client:
            response = await submit_client.post(
                f"{base_url}/openapi/v2/run/ai-app/{app_id}",
                headers={**headers, "Content-Type": "application/json"},
                json={"nodeInfoList": node_info_list, "instanceType": "plus", "usePersonalQueue": "false"},
            )
            response.raise_for_status()
            submit_data = response.json()
            task_id = submit_data.get("taskId", "")
            if not task_id:
                raise RuntimeError(f"RunningHub AI App 提交失败: {submit_data}")

            stage = {
                "2044675464228245506": "LatentSync LipSync",
                "2075886555104890882": "HeyGem LipSync (legacy)",
                "2085898555902160898": "Video LipSync (legacy)",
                "2063861137065725953": "LatentSync (legacy)",
                "2028374986792116225": "DreamID-V",
            }.get(app_id, "AI App")
            _record_runninghub_task(output_path, stage, app_id, task_id)
            logger.info(f"[{stage}] 任务已提交, taskId={task_id}")

    # ── 轮询 + 下载（复用任务也走这里）──
    last_status_data = {}
    consecutive_5xx_errors = 0
    retried_for_805 = False  # 805 临时故障只自动重试 1 次
    async with httpx.AsyncClient(timeout=300.0) as client:
        for poll_index in range(1440):  # 2小时轮询窗口（5s × 1440 = 7200s）
            await asyncio.sleep(5)
            try:
                status_response = await client.post(
                    f"{base_url}/openapi/v2/query",
                    headers={**headers, "Content-Type": "application/json"},
                    json={"taskId": task_id},
                )
            except httpx.HTTPError as http_err:
                consecutive_5xx_errors += 1
                if consecutive_5xx_errors >= 10:
                    raise RuntimeError(f"RunningHub 查询连续网络异常 ({consecutive_5xx_errors}次): {http_err}")
                await asyncio.sleep(min(30, 5 * consecutive_5xx_errors))
                continue

            http_status = status_response.status_code
            if http_status >= 500:
                consecutive_5xx_errors += 1
                if consecutive_5xx_errors >= 10:
                    raise RuntimeError(f"RunningHub 查询连续 5xx 错误 ({consecutive_5xx_errors}次): HTTP {http_status}")
                await asyncio.sleep(min(60, 5 * (2 ** (consecutive_5xx_errors - 1))))
                continue

            if http_status >= 400:
                status_response.raise_for_status()

            consecutive_5xx_errors = 0
            status_data = status_response.json()
            last_status_data = status_data
            status = status_data.get("status", "")

            if status == "FAILED":
                _mark_runninghub_task_finished(output_path, task_id, "FAILED")
                # 805 + "please try again later" 是 RunningHub 服务端临时故障，自动重试 1 次
                err_msg = str(status_data.get("failedReason", {}).get("exception_message", ""))
                err_code = str(status_data.get("errorCode", ""))
                if (not retried_for_805) and (("please try again" in err_msg.lower()) or (err_code == "805")):
                    retried_for_805 = True
                    logger.warning(f"[{stage}] 任务 {task_id} 因服务端临时故障失败（805），自动重试 1 次...")
                    # 把刚才的失败任务标记完，下面走正常提交流程重新提交
                    async with httpx.AsyncClient(timeout=300.0) as submit_client:
                        response = await submit_client.post(
                            f"{base_url}/openapi/v2/run/ai-app/{app_id}",
                            headers={**headers, "Content-Type": "application/json"},
                            json={"nodeInfoList": node_info_list, "instanceType": "plus", "usePersonalQueue": "false"},
                        )
                        response.raise_for_status()
                        submit_data = response.json()
                        new_task_id = submit_data.get("taskId", "")
                        if not new_task_id:
                            raise RuntimeError(f"RunningHub AI App 重试提交失败: {submit_data}")
                        _record_runninghub_task(output_path, stage, app_id, new_task_id)
                        logger.info(f"[{stage}] 重试任务已提交, taskId={new_task_id}")
                        task_id = new_task_id
                        # 继续轮询这个新任务
                        continue
                raise RuntimeError(f"RunningHub AI App 执行失败: {status_data}")
            if status != "SUCCESS":
                continue

            results = status_data.get("results", [])
            video_url = next((r.get("url", "") for r in results if r.get("outputType") == "mp4"), "")
            if not video_url:
                video_url = next((r.get("url", "") for r in results if r.get("url")), "")
            if not video_url:
                raise RuntimeError(f"RunningHub 未返回视频结果: {status_data}")

            # 确保输出目录存在（否则 open() 会 FileNotFoundError）
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            async with client.stream("GET", video_url) as download_response:
                download_response.raise_for_status()
                with open(output_path, "wb") as output_file:
                    async for chunk in download_response.aiter_bytes():
                        output_file.write(chunk)
            _mark_runninghub_task_finished(output_path, task_id, "SUCCESS")
            return output_path

    raise TimeoutError(f"RunningHub AI App 超时: {app_id} (taskId={task_id})")


async def _find_reusable_runninghub_task(output_path: str, app_id: str, api_key: str = None) -> dict | None:
    """扫 output_path 同目录的 runninghub_tasks.json，
    找出 app_id 匹配且无 finish_status 标记的最近一条任务。

    找到候选后会先查 RunningHub API 确认任务在服务端是否还存在：
    - 仍存在且非 SUCCESS/FAILED → 返回该记录供复用
    - 已 SUCCESS → 返回该记录（调用方会在轮询时直接下载结果）
    - 已 FAILED 或服务端查不到（1004 任务不存在/已过期）→ 标记 finish_status=expired/failed，跳过
    """
    import httpx
    from config import settings

    api_key = api_key or settings.RUNNINGHUB_ENTERPRISE_API_KEY
    try:
        task_dir = os.path.dirname(os.path.abspath(output_path))
        record_path = os.path.join(task_dir, "runninghub_tasks.json")
        if not os.path.exists(record_path):
            return None
        with open(record_path, "r", encoding="utf-8") as f:
            records = json.load(f)
    except Exception as e:
        logger.warning(f"查找可复用 RunningHub 任务失败: {e}")
        return None

    # 倒序找最新的未标记完成记录
    for r in reversed(records):
        if r.get("app_id") != app_id or r.get("finish_status"):
            continue

        task_id = r.get("task_id", "")
        if not task_id:
            continue

        # 查 RunningHub 服务端确认任务状态
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://www.runninghub.ai/openapi/v2/query",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"taskId": task_id},
                )
            data = resp.json()
            err_code = data.get("errorCode", "")
            status = data.get("status", "")
            # 1004: 任务不存在或已过期 → 标记 expired，跳过
            if err_code == "1004":
                logger.info(f"RunningHub 任务 {task_id} 在服务端已不存在（expired），将标记为不可复用")
                _mark_runninghub_task_finished(output_path, task_id, "expired")
                continue
            # FAILED
            if status == "FAILED":
                _mark_runninghub_task_finished(output_path, task_id, "FAILED")
                continue
            # SUCCESS 也能复用（轮询时会直接下载）
            # RUNNING / QUEUED / 空 status（部分场景查询时 status 为空但任务仍有效）→ 复用
            return r
        except Exception as e:
            logger.warning(f"查询 RunningHub 任务 {task_id} 状态失败: {e}")
            # 查询失败时不阻断流程，乐观返回让上层去轮询
            return r

    return None


def _mark_runninghub_task_finished(output_path: str, task_id: str, status: str):
    """在 runninghub_tasks.json 里给指定 task_id 加上 finish_status 标记，
    避免下次 resume 时误复用已完成（成功或失败）的任务。"""
    try:
        task_dir = os.path.dirname(os.path.abspath(output_path))
        record_path = os.path.join(task_dir, "runninghub_tasks.json")
        if not os.path.exists(record_path):
            return
        with open(record_path, "r", encoding="utf-8") as f:
            records = json.load(f)
        for r in records:
            if r.get("task_id") == task_id:
                r["finish_status"] = status
                r["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _save_pipeline_state(record_path, records)
    except Exception as e:
        logger.warning(f"标记 RunningHub 任务完成状态失败: {e}")


async def _upload_runninghub_ai_resource(file_path: str, api_key: str = None) -> str:
    """上传 AI App 资源，返回 RunningHub 内部使用的 fileName。

    重要：上传用的 api_key 必须和提交任务用的 api_key 一致，
    否则 RunningHub 服务端会因 key 隔离而访问不到资源，
    导致 VoxCPM_TTS 等节点拿到空输入 → torch.cat(): expected a non-empty list of Tensors。
    优先返回 fileName（与测试脚本一致），其次 download_url/url。
    """
    import httpx
    from config import settings

    api_key = api_key or settings.RUNNINGHUB_ENTERPRISE_API_KEY
    path = Path(file_path)
    async with httpx.AsyncClient(timeout=300.0) as client:
        with open(path, "rb") as file_handle:
            response = await client.post(
                "https://www.runninghub.ai/openapi/v2/media/upload/binary",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (path.name, file_handle)},
            )
        response.raise_for_status()
        data = response.json()
        value = data.get("data", {})
        # 优先用 fileName（与 lipsync_new_api.py 测试通过版本一致），其次 download_url
        resource = value.get("fileName") or value.get("download_url") or value.get("url")
        if not resource:
            raise RuntimeError(f"RunningHub 资源上传失败: {data}")
        return resource


# ═══════════════════════════════════════
# 主流程
# ═══════════════════════════════════════

def _load_pipeline_state(state_path: str) -> dict:
    if not os.path.exists(state_path):
        return {"completed_steps": [], "artifacts": {}}
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
        state.setdefault("completed_steps", [])
        state.setdefault("artifacts", {})
        return state
    except (OSError, json.JSONDecodeError):
        return {"completed_steps": [], "artifacts": {}}


def _save_pipeline_state(state_path: str, state: dict):
    temp_path = state_path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(temp_path, state_path)


def _record_runninghub_task(output_path: str, stage: str, app_id: str, task_id: str):
    """持久化 RunningHub 外部任务号，供历史记录和前端回显。"""
    task_dir = os.path.dirname(os.path.abspath(output_path))
    record_path = os.path.join(task_dir, "runninghub_tasks.json")
    try:
        records = []
        if os.path.exists(record_path):
            with open(record_path, "r", encoding="utf-8") as f:
                records = json.load(f)
        records.append({
            "stage": stage,
            "provider": "RunningHub",
            "app_id": app_id,
            "task_id": task_id,
            "submitted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        _save_pipeline_state(record_path, records)
    except Exception as e:
        logger.warning(f"RunningHub 任务记录保存失败: {e}")


async def run_video_translate(
    video_path: str,
    source_lang: str,
    target_lang: str,
    target_face_image: str = "",
    asr_engine: str = "whisper",
    translate_engine: str = "gpt",
    character_mappings: list = None,
    task_id: str = "",
    status_callback=None,
    remove_subtitle: bool = False,
    voice_source: str = "original",
    uploaded_ref_audio: str = "",
) -> dict:
    """
    视频翻译完整流程 v3
    ━━━━━━━━━━━━━━━━━━━━
    Step 0: 字幕消除    (ProPainter: 消除原视频硬字幕，可选)
    Step 1: ASR + 翻译  (Whisper → GPT翻译)
    Step 2: 原音色克隆  (IndexTTS2: 用原声说英文)
    Step 3: 视频换人    (DreamID-V: 逐角色换人)
    Step 4: 口型同步    (LatentSync: 对上英文字幕口型)
    Step 5: 成片合成    (ffmpeg: 合并输出)

    character_mappings: [{"source_face_path": str, "target_face_path": str}, ...]
    remove_subtitle: 是否在流程开始前消除原视频硬字幕
    """
    output_dir = os.path.join("outputs", "video_translate", task_id)
    os.makedirs(output_dir, exist_ok=True)
    state_path = os.path.join(output_dir, "pipeline_state.json")
    state = _load_pipeline_state(state_path)
    completed_steps = set(state["completed_steps"])
    artifacts = state["artifacts"]

    if character_mappings is None:
        character_mappings = []

    # 把入参写入 artifacts（首次运行或上次未保存这些字段时兜底）
    if target_face_image:
        artifacts.setdefault("target_face_image", target_face_image)
    if voice_source:
        artifacts.setdefault("voice_source", voice_source)
    if uploaded_ref_audio:
        artifacts.setdefault("uploaded_ref_audio", uploaded_ref_audio)

    def update(step: int, progress: int, message: str):
        if status_callback:
            status_callback(step, progress, message)

    def artifact_exists(name: str) -> bool:
        value = artifacts.get(name, "")
        return bool(value and os.path.exists(value))

    def complete_step(step: int, **step_artifacts):
        artifacts.update(step_artifacts)
        completed_steps.add(step)
        state["completed_steps"] = sorted(completed_steps)
        state["artifacts"] = artifacts
        _save_pipeline_state(state_path, state)

    # ── Step 0: 字幕消除（可选） ──
    current_video = video_path
    if 0 in completed_steps and artifact_exists("current_video"):
        current_video = artifacts["current_video"]
        update(0, 30, "已复用字幕处理结果")
    elif remove_subtitle:
        update(0, 2, "正在消除原视频字幕（ProPainter）...")
        from backend.services.subtitle_remover import remove_subtitle as _remove_sub

        sub_out_dir = os.path.join(output_dir, "subtitle_removal")
        try:
            sub_result = await _remove_sub(
                video_path=video_path,
                output_dir=sub_out_dir,
                status_callback=lambda s, p, m: update(0, int(p * 0.3), m),
            )
            current_video = sub_result["clean_video_path"]
            update(0, 30, "原字幕消除完成，使用干净视频继续后续流程")
        except Exception as e:
            update(0, 30, f"字幕消除失败，使用原视频继续: {e}")
            current_video = video_path
        complete_step(0, current_video=current_video)
    else:
        complete_step(0, current_video=current_video)

    # ── Step 1: ASR + 翻译 ──
    subtitle_path = os.path.join(output_dir, "subtitle.json")
    if 1 in completed_steps and artifact_exists("subtitle_json") and artifact_exists("ref_audio_path"):
        with open(artifacts["subtitle_json"], "r", encoding="utf-8") as f:
            translated = json.load(f)
        ref_audio_path = artifacts["ref_audio_path"]
        subtitle_path = artifacts["subtitle_json"]
        update(1, 35, "已复用 ASR 与翻译结果")
    else:
        update(1, 5, "正在提取音频...")
        asr_result = await run_asr(video_path, source_lang, asr_engine)
        update(1, 15, f"语音识别完成，共 {len(asr_result['segments'])} 段")
        update(1, 25, f"正在将{source_lang}翻译为{target_lang}...")
        translated = await translate_text(
            asr_result["segments"], source_lang, target_lang, translate_engine
        )
        with open(subtitle_path, "w", encoding="utf-8") as f:
            json.dump(translated, f, ensure_ascii=False, indent=2)
        ref_audio_path = asr_result["ref_audio_path"]
        complete_step(1, subtitle_json=subtitle_path, ref_audio_path=ref_audio_path)
        update(1, 35, "ASR + 翻译完成")

    # ── Step 2: 配音（两种音源可选）──
    # voice_source: "original"  → 用原视频音色克隆（IndexTTS）
    #               "uploaded"  → 用用户上传的参考音频克隆（IndexTTS）
    voice_source = artifacts.get("voice_source") or "original"
    uploaded_ref_audio = artifacts.get("uploaded_ref_audio") or ""

    if (2 in completed_steps and artifact_exists("cloned_audio_path")
            and artifacts.get("voice_source") == voice_source):
        full_cloned_audio = artifacts["cloned_audio_path"]
        update(2, 55, f"已复用配音结果（音源={voice_source}）")
    else:
        completed_steps.discard(2); completed_steps.discard(4); completed_steps.discard(5)

        if voice_source == "original":
            update(2, 40, "正在用原视频音色克隆英文配音 (IndexTTS)...")
            cloned_segments = await run_voice_clone(
                translated_segments=translated,
                ref_audio_path=ref_audio_path,
                target_lang=target_lang,
                output_dir=os.path.join(output_dir, "voice_clone"),
            )
        elif voice_source == "uploaded":
            if not uploaded_ref_audio or not os.path.exists(uploaded_ref_audio):
                raise RuntimeError("已选上传音色模式，但未提供参考音频文件")
            update(2, 40, "正在用上传参考音色克隆英文配音 (IndexTTS)...")
            cloned_segments = await run_voice_clone(
                translated_segments=translated,
                ref_audio_path=uploaded_ref_audio,
                target_lang=target_lang,
                output_dir=os.path.join(output_dir, "voice_clone"),
            )
        else:
            raise RuntimeError(f"不支持的音源类型: {voice_source}（仅支持 original / uploaded）")

        # run_voice_clone 返回 segments list，每个 segment 含 full_cloned_audio 字段指向完整音频
        full_cloned_audio = ""
        if cloned_segments:
            full_cloned_audio = cloned_segments[0].get("full_cloned_audio", "")
        if not full_cloned_audio or not os.path.exists(full_cloned_audio):
            raise RuntimeError("配音未生成有效音频")
        complete_step(2, cloned_audio_path=full_cloned_audio, voice_source=voice_source)
        update(2, 55, f"配音完成（音源={voice_source}）")

    # ── Step 3: 人物处理（保持原人物 或 DreamID-V 替换为外国人脸）──
    # - 无可换脸配置（target_face_image 为空 且 character_mappings 空/无 target）→ 保持原人物
    # - 有单角色 target_face_image → 走 DreamID-V 单脸替换
    target_face_image = artifacts.get("target_face_image") or ""
    mappings_with_target = [m for m in character_mappings if m.get("target_face_path")]
    has_face_swap = bool(target_face_image) or bool(mappings_with_target)

    if has_face_swap:
        presenter_version = "faceswap-v1"
        if (3 in completed_steps and artifact_exists("swapped_video_path")
                and artifacts.get("presenter_version") == presenter_version):
            swapped_video = artifacts["swapped_video_path"]
            update(3, 70, "已复用 DreamID-V 换人结果")
        else:
            completed_steps.discard(3); completed_steps.discard(4); completed_steps.discard(5)
            # 单角色走 run_face_swap；多角色可扩展为逐角色换人（当前用第一个 target_face_image 兜底）
            swap_target = target_face_image or mappings_with_target[0]["target_face_path"]
            # 先看 face_swap 目录有没有可复用的 RunningHub 任务（避免重复提交）
            swap_output_dir = os.path.join(output_dir, "face_swap")
            swap_output_path = os.path.join(swap_output_dir, "face_swapped.mp4")
            reused = await _find_reusable_runninghub_task(swap_output_path, "2028374986792116225")
            if reused:
                update(3, 40, f"检测到 RunningHub 任务 {reused['task_id']} 仍在进行，继续轮询...")
            else:
                update(3, 40, "正在调用 DreamID-V 替换人脸（上传图→视频人物）...")
            swapped_video = await run_face_swap(
                video_path=current_video,
                target_face_image=swap_target,
                output_dir=swap_output_dir,
            )
            complete_step(
                3,
                swapped_video_path=swapped_video,
                presenter_version=presenter_version,
            )
            update(3, 70, "DreamID-V 换人完成，将进入 LatentSync 对口型")
    else:
        presenter_version = "original-presenter-v1"
        swapped_video = current_video
        if (3 in completed_steps and artifact_exists("swapped_video_path")
                and artifacts.get("presenter_version") == presenter_version):
            swapped_video = artifacts["swapped_video_path"]
            update(3, 70, "已复用原人物母版")
        else:
            completed_steps.discard(3); completed_steps.discard(4); completed_steps.discard(5)
            update(3, 70, "无可换脸配置，保持原人物，直接进入 LatentSync 选择性口型同步")
            complete_step(
                3,
                swapped_video_path=swapped_video,
                presenter_version=presenter_version,
            )

    # ── Step 4: 选择性数字人对口型 (LatentSync) ──
    # 支持任意输入视频（纯主播 / 转场视频 / 无人画面）：
    #   - 有人脸段 → 送 LatentSync 对口型
    #   - 无人脸段 → 原样保留画面，仅替换音轨
    #   - 全程无人脸 → 整体跳过口型同步，直接进入 Step 5 合成
    lip_sync_version = "latentsync-selective-v1"
    if (4 in completed_steps and artifact_exists("lip_synced_video_path")
            and artifacts.get("lip_sync_version") == lip_sync_version):
        final_video = artifacts["lip_synced_video_path"]
        update(4, 90, "已复用 LatentSync 选择性口型同步结果")
    else:
        face_coverage = _presenter_face_coverage(swapped_video)
        update(4, 72, f"人脸覆盖率 {face_coverage:.0%}，正在分析人脸段并提交 LatentSync...")
        final_video = await run_selective_lip_sync(
            video_path=swapped_video,
            audio_path=full_cloned_audio,
            segments=translated,
            output_dir=os.path.join(output_dir, "latentsync"),
        )
        complete_step(
            4,
            lip_synced_video_path=final_video,
            lip_sync_version=lip_sync_version,
            presenter_face_coverage=face_coverage,
        )
        update(4, 90, f"LatentSync 选择性口型同步完成（人脸覆盖率 {face_coverage:.0%}）")

    # ── Step 5: 成片合成 ──
    output_path = os.path.join(output_dir, "final_translated.mp4")
    srt_path = os.path.join(output_dir, "translated.srt")
    if 5 in completed_steps and artifact_exists("result_video_path"):
        output_path = artifacts["result_video_path"]
        srt_path = artifacts.get("subtitle_path", srt_path)
        update(5, 100, "已复用最终成片")
    else:
        update(5, 92, "正在用 HyperFrames 编排成片...")
        hf_output = await _compose_with_hyperframes(
            video_path=final_video,
            audio_path=full_cloned_audio,
            segments=translated,
            output_path=output_path,
        )
        if not hf_output:
            # HyperFrames 失败 fallback：直接 ffmpeg 合并视频 + 英文克隆音轨
            # （无人脸跳过口型同步的路径下，final_video 仍是原视频画面，必须替换音轨）
            await _fallback_merge_av(final_video, full_cloned_audio, output_path)
        _generate_srt(translated, srt_path)
        complete_step(5, result_video_path=output_path, subtitle_path=srt_path)
        update(5, 100, "视频翻译完成！")

    return {
        "result_video_path": output_path,
        "subtitle_path": srt_path,
        "subtitle_json": subtitle_path,
        "segments": translated,
        "cloned_audio_path": full_cloned_audio,
        "swapped_video_path": swapped_video if (character_mappings or target_face_image) else None,
    }


# ═══════════════════════════════════════
# Step 5 辅助: HyperFrames 编排成片
# ═══════════════════════════════════════

async def _compose_with_hyperframes(
    video_path: str,
    audio_path: str,
    segments: list,
    output_path: str,
) -> bool:
    """
    使用 HyperFrames 编排最终成片
    生成 HTML composition，调用 HyperFrames CLI 渲染为 MP4

    编排内容：
    - 换人+口型同步后的视频
    - 目标语言字幕叠加
    - 克隆音频
    """
    import shutil

    hf_cli = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "hyperframes", "packages", "cli", "dist", "cli.js"
    )
    if not os.path.exists(hf_cli):
        logger.warning(f"HyperFrames CLI 未找到: {hf_cli}，跳过编排")
        return False

    output_dir = os.path.dirname(output_path)
    project_dir = os.path.join(output_dir, "hf_project")
    os.makedirs(project_dir, exist_ok=True)

    # 视频文件绝对路径（HyperFrames 需要）
    video_abs = os.path.abspath(video_path)
    audio_abs = os.path.abspath(audio_path) if audio_path and os.path.exists(audio_path) else ""

    # 生成字幕 HTML 片段
    subtitle_clips = []
    for i, seg in enumerate(segments):
        start = seg.get("start", 0)
        end = seg.get("end", start + 3)
        text = seg.get("target_text", "").replace('"', "&quot;").replace("'", "&#39;")
        subtitle_clips.append(
            f'      <div class="clip" data-start="{start}" data-duration="{end - start}" '
            f'data-track-index="1" style="position:absolute;bottom:10%;left:50%;'
            f'transform:translateX(-50%);font-size:2.2vw;color:#fff;'
            f'background:rgba(0,0,0,.6);padding:6px 18px;border-radius:8px;'
            f'white-space:nowrap;text-shadow:1px 1px 3px #000;">'
            f'{text}</div>'
        )

    subtitle_html = "\n".join(subtitle_clips)

    # 生成 HyperFrames composition HTML
    composition_id = "translated-video"
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Video Translate Output</title>
<style>
  body {{ margin:0; background:#000; }}
  #stage {{ position:relative; width:100%; height:100%; overflow:hidden; }}
  .clip {{ position:absolute; }}
</style>
</head>
<body>
<div id="stage"
     data-composition-id="{composition_id}"
     data-start="0"
     data-width="1920"
     data-height="1080">

  <video class="clip"
         data-start="0"
         data-track-index="0"
         src="{video_abs.replace(os.sep, '/')}"
         muted playsinline
         style="width:100%;height:100%;object-fit:cover;">
  </video>

{subtitle_html}

  {"<audio data-start='0' data-track-index='2' data-volume='1.0' src='" + audio_abs.replace(os.sep, '/') + "'></audio>" if audio_abs else ""}

</div>
</body>
</html>"""

    html_path = os.path.join(project_dir, "index.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    # 调用 HyperFrames CLI 渲染
    cmd = [
        "node", hf_cli, "render",
        "--project", project_dir,
        "--composition", composition_id,
        "--output", output_path,
        "--format", "mp4",
    ]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", timeout=600
        )
        if proc.returncode == 0 and os.path.exists(output_path):
            logger.info(f"HyperFrames 渲染成功: {output_path}")
            return True
        else:
            logger.warning(f"HyperFrames 渲染失败: {proc.stderr[:500]}")
            return False
    except subprocess.TimeoutExpired:
        logger.warning("HyperFrames 渲染超时")
        return False
    except Exception as e:
        logger.warning(f"HyperFrames 渲染异常: {e}")
        return False
