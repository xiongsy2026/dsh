"""超级复刻的后处理声音腿，两种形状各一条：

run_voiceover_replace —— 勾选片段替换 + 对口型（要动嘴形，多两条花钱腿）。
run_narration_swap   —— 改口播·音色不变（表单 voice_mode=rewrite_text）：
                        对【要交付的那一条成片】自己做 ASR + 人声分离，只重做改动的那几句，
                        未改句直接复用成片自己的原人声切片，改动窗口垫回伴奏，整轨重建后贴回。
                        不做口型同步（嘴形与新词对不上是这一档的已知代价，交付时要如实说）。
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Callable, Optional

import httpx

from config import settings

LogFn = Optional[Callable[[str], None]]


def _log(on_log: LogFn, msg: str) -> None:
    if on_log:
        on_log(msg)
        return
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        # 🔴 一行日志不许杀业务：控制台是 GBK 时，▶ / ✓ 这类字符直接 UnicodeEncodeError
        #    把整条腿打断（钱已经花在这一句上了）。降级按输出流编码 replace，内容不丢。
        import sys as _sys
        enc = getattr(_sys.stdout, "encoding", None) or "utf-8"
        _sys.stdout.write(str(msg).encode(enc, "replace").decode(enc, "replace") + "\n")
        _sys.stdout.flush()


async def extract_voiceover(video_path: str) -> dict:
    from backend.services.video_translate_pipeline import run_asr

    data = await run_asr(video_path, "zh")
    segments = data.get("segments") or []
    text = "".join((s.get("text") or "") for s in segments).strip()
    return {
        "text": text,
        "segments": segments,
        "audio_path": data.get("audio_path"),
        "ref_audio_path": data.get("ref_audio_path"),
    }


def _duration(path: str) -> float:
    from backend.services.video_translate_pipeline import _get_audio_duration
    return _get_audio_duration(path)


def _run_ffmpeg(cmd: list, err: str) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"{err}: {(proc.stderr or '')[-400:]}")


def _fit_audio_duration(audio_path: str, target_dur: float, out_path: str) -> str:
    vid = max(0.2, float(target_dur))
    aud = max(0.05, _duration(audio_path))
    tempo = aud / vid
    filters = []
    t = tempo
    while t > 2.0:
        filters.append("atempo=2.0")
        t /= 2.0
    while t < 0.5:
        filters.append("atempo=0.5")
        t /= 0.5
    filters.append(f"atempo={max(0.5, min(2.0, t)):.6f}")
    cmd = [
        "ffmpeg", "-y", "-i", audio_path,
        "-af", ",".join(filters),
        "-t", f"{vid:.3f}",
        "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
        out_path,
    ]
    _run_ffmpeg(cmd, "口播对齐失败")
    if not Path(out_path).exists():
        raise RuntimeError("口播对齐失败: 无输出文件")
    return out_path


def _fit_audio_to_video(audio_path: str, video_path: str, out_path: str) -> str:
    return _fit_audio_duration(audio_path, _duration(video_path), out_path)


def _cut_av(source: str, start: float, duration: float, output: str, *, audio_only: bool = False, video_only: bool = False) -> str:
    cmd = ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-t", f"{max(0.05, duration):.3f}", "-i", source]
    if audio_only:
        cmd += ["-vn", "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", output]
    elif video_only:
        cmd += ["-an", "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", output]
    else:
        cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", output]
    _run_ffmpeg(cmd, "切片失败")
    return output


def _concat_videos(paths: list, output: str) -> str:
    lst = Path(output).with_suffix(".txt")
    lst.write_text("".join(f"file '{Path(p).as_posix()}'\n" for p in paths), encoding="utf-8")
    _run_ffmpeg(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-c:a", "aac", "-b:a", "128k",
         "-movflags", "+faststart", output],
        "片段拼接失败",
    )
    return output


async def _poll_download(task_id: str, output_path: str, api_key: str, want: str = "any") -> str:
    from backend.services.video_translate_pipeline import _download_file

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=120.0) as client:
        for _ in range(720):
            await asyncio.sleep(5)
            resp = await client.post(
                "https://www.runninghub.ai/openapi/v2/query",
                headers=headers,
                json={"taskId": task_id},
            )
            if resp.status_code >= 500:
                continue
            data = resp.json()
            status = data.get("status") or ""
            if status == "SUCCESS":
                results = data.get("results") or []
                url = ""
                for item in results:
                    ext = (item.get("outputType") or "").lower()
                    if want == "audio" and ext in ("wav", "mp3", "m4a", "aac", "flac"):
                        url = item.get("url") or ""
                        break
                    if want == "video" and ext in ("mp4", "webm", "mov"):
                        url = item.get("url") or ""
                        break
                if not url and results:
                    url = results[0].get("url") or ""
                if not url:
                    raise RuntimeError(f"对口型成功但无文件: {data}")
                await _download_file(url, output_path)
                return output_path
            if status == "FAILED":
                raise RuntimeError(data.get("errorMessage") or str(data))
    raise RuntimeError("对口型超时")


async def _run_lipsync_workflow(video_path: str, audio_path: str, output_path: str, on_log: LogFn) -> str:
    from backend.services.video_translate_pipeline import _upload_runninghub_ai_resource

    wf_id = str(getattr(settings, "RUNNINGHUB_VO_LIPSYNC_WORKFLOW_ID", "") or "2088573214279876610")
    video_node = str(getattr(settings, "RUNNINGHUB_VO_LIPSYNC_VIDEO_NODE", "228") or "228")
    video_field = str(getattr(settings, "RUNNINGHUB_VO_LIPSYNC_VIDEO_FIELD", "video") or "video")
    audio_node = str(getattr(settings, "RUNNINGHUB_VO_LIPSYNC_AUDIO_NODE", "125") or "125")
    audio_field = str(getattr(settings, "RUNNINGHUB_VO_LIPSYNC_AUDIO_FIELD", "audio") or "audio")
    api_key = (
        str(getattr(settings, "RUNNINGHUB_API_KEY", "") or "").strip()
        or str(getattr(settings, "RUNNINGHUB_ENTERPRISE_API_KEY", "") or "").strip()
    )
    _log(on_log, f"  对口型工作流 {wf_id}（{video_node}/{video_field} + {audio_node}/{audio_field}）")
    video_file = await _upload_runninghub_ai_resource(video_path, api_key=api_key)
    audio_file = await _upload_runninghub_ai_resource(audio_path, api_key=api_key)
    nodes = [
        {"nodeId": video_node, "fieldName": video_field, "fieldValue": video_file},
        {"nodeId": audio_node, "fieldName": audio_field, "fieldValue": audio_file},
    ]
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=120.0) as client:
        last_err = None
        for endpoint in ("workflow", "ai-app"):
            resp = await client.post(
                f"https://www.runninghub.ai/openapi/v2/run/{endpoint}/{wf_id}",
                headers=headers,
                json={"addMetadata": True, "nodeInfoList": nodes, "instanceType": "plus", "usePersonalQueue": False},
            )
            data = resp.json()
            task_id = data.get("taskId") or ""
            if task_id:
                _log(on_log, f"  对口型已提交 {endpoint} taskId={task_id}")
                return await _poll_download(task_id, output_path, api_key, want="video")
            last_err = data.get("errorMessage") or str(data)
            _log(on_log, f"  {endpoint} 提交失败: {last_err}")
    raise RuntimeError(f"对口型工作流提交失败: {last_err}")


async def run_voiceover_replace(
    video_path: str,
    output_dir: str,
    replacements: list | None = None,
    new_text: str = "",
    on_log: LogFn = None,
) -> str:
    """只处理客户勾选的口播片段：克隆该段 → 时长对齐 → 对口型 → 贴回成片。"""
    from backend.services.video_translate_pipeline import voice_clone_to_file, run_lip_sync

    reps = []
    for item in replacements or []:
        start = float(item.get("start") or 0)
        end = float(item.get("end") or 0)
        new = str(item.get("new_text") or "").strip()
        old = str(item.get("original") or "").strip()
        if new and end > start + 0.15 and new != old:
            reps.append({"start": start, "end": end, "original": old, "new_text": new})
    reps.sort(key=lambda x: x["start"])
    if not reps and (new_text or "").strip():
        raise RuntimeError("请勾选要替换的口播片段，不要整段重配")
    if not reps:
        raise RuntimeError("没有可替换的口播片段")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    total = _duration(video_path)
    _log(on_log, f"▶ 换口播：只替换 {len(reps)} 个片段，贴回克隆视频")

    chunks: list[str] = []
    cursor = 0.0
    for i, rep in enumerate(reps):
        start, end = rep["start"], min(rep["end"], total)
        if start > cursor + 0.02:
            keep = str(out / f"keep_{i:02d}.mp4")
            _cut_av(video_path, cursor, start - cursor, keep)
            chunks.append(keep)
        dur = max(0.2, end - start)
        clip = str(out / f"clip_{i:02d}.mp4")
        ref = str(out / f"ref_{i:02d}.wav")
        cloned = str(out / f"clone_{i:02d}.wav")
        aligned = str(out / f"align_{i:02d}.wav")
        synced = str(out / f"sync_{i:02d}.mp4")
        _cut_av(video_path, start, dur, clip, video_only=True)
        _cut_av(video_path, start, dur, ref, audio_only=True)
        _log(on_log, f"  片段 {i + 1}: {start:.2f}-{end:.2f}s 「{rep['original'][:18]}」→「{rep['new_text'][:18]}」")
        await voice_clone_to_file(rep["new_text"], ref, cloned)
        _fit_audio_duration(cloned, dur, aligned)
        try:
            await _run_lipsync_workflow(clip, aligned, synced, on_log)
        except Exception as exc:
            _log(on_log, f"  指定对口型失败，改走 HeyGem：{exc}")
            try:
                hey = await run_lip_sync(clip, aligned, str(out / f"heygem_{i:02d}"))
                synced = hey if hey and Path(hey).exists() else ""
            except Exception as exc2:
                _log(on_log, f"  HeyGem 失败，该段只换声音：{exc2}")
                synced = ""
        if not synced or not Path(synced).exists():
            mux = str(out / f"mux_{i:02d}.mp4")
            _run_ffmpeg(
                ["ffmpeg", "-y", "-i", clip, "-i", aligned, "-c:v", "copy",
                 "-map", "0:v:0", "-map", "1:a:0", "-shortest", mux],
                "片段音轨替换失败",
            )
            synced = mux
        chunks.append(synced)
        cursor = end
    if cursor < total - 0.02:
        tail = str(out / "keep_tail.mp4")
        _cut_av(video_path, cursor, total - cursor, tail)
        chunks.append(tail)
    final = str(out / "voiceover_patched.mp4")
    _concat_videos(chunks, final)
    _log(on_log, f"✓ 片段已贴回成片 → {final}")
    return final


# ═══════════════════════════════════════════════════════════════════════
# 改口播 · 音色不变（表单 voice_mode=rewrite_text，超级复刻第 4 档）
#   事实源 = 要交付的那一条成片自己：在它上面跑 ASR + 人声分离，
#   所以不背"多段偏移"的账、也不依赖 MiMo 的镜时间（那个时间轴只管画面）。
# ═══════════════════════════════════════════════════════════════════════

TEMPO_MIN, TEMPO_MAX = 0.5, 2.0    # 单级 atempo 的合法范围；压出界 = 变调，不是"说得快"
REF_MIN_S, REF_MAX_S = 1.0, 15.0   # IndexTTS 参考音频：太短克隆不出音色，太长上游不收
DENSITY_WARN = 6.0                 # 中文口播约 4-6 字/秒；超了先提醒"这句压不回去"


def split_narration_script(script_text) -> list:
    """一行一句 => 按句序的列表（空白行丢掉，不补位、不猜）。"""
    return [ln.strip() for ln in str(script_text or "").splitlines() if ln.strip()]


def narration_swap_plan(lines: list, segments: list) -> tuple:
    """纯函数（闸直接跑值表）：新文案句序 × 成片 ASR 句窗口 => 逐句动作单。

    只做"对得上对不上"的判定，不做任何猜测：句数不等就点名拒 ——
    猜哪句配哪句，等于把台词安到错误的镜头上，那比不改更坏。
    返回 (rows, problems)；problems 非空 => 一个上游请求都不许发。
    """
    segs = [s for s in (segments or []) if str(s.get("text") or "").strip()]
    if not segs:
        return [], ["成片里没识别出口播（纯音乐或被分离掉了）=> 没有可替换的句子，这一档做不了"]
    if len(lines) != len(segs):
        listed = "；".join("%d.%s" % (i + 1, str(s.get("text") or "").strip())
                          for i, s in enumerate(segs[:12]))
        return [], ["句数对不上：成片有 %d 句口播，新文案给了 %d 句。"
                    "请照成片的句子顺序一句一行重写（成片这几句：%s）"
                    % (len(segs), len(lines), listed)]
    rows, problems = [], []
    for i, (ln, seg) in enumerate(zip(lines, segs)):
        start = round(float(seg.get("start") or 0.0), 3)
        end = round(float(seg.get("end") or 0.0), 3)
        old = str(seg.get("text") or "").strip()
        window = round(end - start, 3)
        row = {"seq": i + 1, "start": start, "end": end, "window": window,
               "old": old, "new": ln,
               "action": "keep" if "".join(old.split()) == "".join(ln.split()) else "swap",
               "ref_note": "", "density_warn": ""}
        if window <= 0:
            problems.append("第 %d 句时间窗是 %s-%s（结束不晚于开始）=> ASR 时钟坏了，先报这个"
                            % (i + 1, start, end))
            continue
        if row["action"] == "swap":
            if window < REF_MIN_S:
                problems.append("第 %d 句只有 %.2fs，太短，克隆不出音色（参考音频要 ≥%.0fs）："
                                "「%s」" % (i + 1, window, REF_MIN_S, ln))
                continue
            if window > REF_MAX_S:
                row["ref_note"] = "参考音频取前 %.0fs（窗口 %.1fs 超上游上限）" % (REF_MAX_S, window)
            if len("".join(ln.split())) / window > DENSITY_WARN:
                row["density_warn"] = ("这句 %.1fs 要塞 %d 字，大概率压不回原时长（>%.0f 字/秒）"
                                       "=> 建议改短文案或这一句别改"
                                       % (window, len("".join(ln.split())), DENSITY_WARN))
        rows.append(row)
    return rows, problems


def _mean_volume_db(path: str, start: float = None, dur: float = None):
    """读一段音频的平均响度（dB）。读不出来返回 None —— 响度对齐是"补不齐就别补"，不是判据。"""
    import re as _re
    cmd = ["ffmpeg", "-hide_banner", "-nostats"]
    if start is not None:
        cmd += ["-ss", "%.3f" % start]
    cmd += ["-i", str(path)]
    if dur is not None:
        cmd += ["-t", "%.3f" % dur]
    cmd += ["-vn", "-af", "volumedetect", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = _re.search(r"mean_volume:\s*(-?[0-9.]+)\s*dB", proc.stderr or "")
    return float(m.group(1)) if m else None


def build_narration_graph(swap_windows: list, *, rate: int = 44100, gains=None) -> str:
    """纯函数：拼 filter_complex。输入约定 [1]=vocals [2]=no_vocals [3..]=克隆干声。

    做法：① 整条人声轨当底，只把【改动窗口】抹掉（未改句就是原声，绝对像）；
          ② 每个克隆干声用 adelay 落到自己句子的起点，并按 gains 补响度
             （不补的形状：新声比原句小声 10dB，客户一听就知道"换了但换弱了"）；
          ③ 伴奏轨（demucs 的 no_vocals）整条垫回来 —— 少了这一步 BGM 会在改动窗口里消失。
    gains: 与 swap_windows 等长的线性增益列表（None = 这条不补，如实留在物证里）。
    """
    norm = "aresample=%d,aformat=sample_fmts=s16:channel_layouts=stereo" % rate
    if swap_windows:
        enable = "+".join("between(t,%.3f,%.3f)" % (s, e) for s, e in swap_windows)
        parts = ["[1:a]%s,volume=enable='%s':volume=0[vb]" % (norm, enable)]
    else:
        parts = ["[1:a]%s,anull[vb]" % norm]
    labels = ["[vb]"]
    gains = list(gains or [])
    for i, (start, _end) in enumerate(swap_windows):
        ms = int(round(start * 1000))
        chain = "[%d:a]%s,adelay=%d|%d" % (3 + i, norm, ms, ms)
        g = gains[i] if i < len(gains) else None
        if g:
            chain += ",volume=%.4f" % float(g)
        parts.append("%s[sv%d]" % (chain, i))
        labels.append("[sv%d]" % i)
    parts.append("[2:a]%s[bg]" % norm)
    labels.append("[bg]")
    parts.append("%samix=inputs=%d:normalize=0:duration=first[outa]" % ("".join(labels), len(labels)))
    return ";".join(parts)


def _usage_sum(rows: list) -> dict:
    """把每次克隆的 usage 合计成人话里的"实扣"（这条腿不在网关账本上，只有这一份出处）。"""
    out = {}
    for r in rows:
        for k, v in (r.get("usage") or {}).items():
            if isinstance(v, (int, float)):
                out[k] = round(float(out.get(k, 0.0)) + float(v), 4)
    return out


async def run_narration_swap(
    video_path: str,
    output_dir: str,
    script_text: str,
    *,
    clone_fn: Optional[Callable] = None,
    segments: Optional[list] = None,
    dry_run: bool = False,
    on_log: LogFn = None,
) -> dict:
    """改口播 · 音色不变：只重做改动的那几句，其余保留成片原声，整轨重建后贴回。

    🔴 花钱边界：每个 swap 句 = 一次 RunningHub 声音克隆，【不在网关账本上】（无预扣/结算/审计）
       => 提交前必须按 RunningHub 余额 + instanceType 口径报价并等明确同意，跑完把 usage 报回。
    dry_run=True => 只做 ASR/分离/对账并打印将执行的命令，一个克隆请求都不发、不落成片（0 分上游）。
    不做口型同步：嘴形与新词对不上是这一档的已知代价，交付时要如实说，不许当作已经对齐。
    """
    from backend.services.prompt_reverser import _extract_voiceover

    if clone_fn is None:
        from backend.services.video_translate_pipeline import voice_clone_to_file as clone_fn
    vid = str(video_path)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    total = _duration(vid)
    work = out / "narration_work"
    work.mkdir(parents=True, exist_ok=True)

    segs = segments
    if segs is None:
        _log(on_log, "▶ 改口播：对成片自己做 ASR + 人声分离（本地、0 分上游）")
        segs = await _extract_voiceover(vid, work, total)
    vocals = work / "demucs" / "htdemucs" / "asr_audio" / "vocals.wav"
    bgm = work / "demucs" / "htdemucs" / "asr_audio" / "no_vocals.wav"
    if not (vocals.exists() and bgm.exists()):
        raise RuntimeError(
            "人声分离没产出 vocals/no_vocals => 抹不掉改动窗口里的原人声，新声会和旧声叠在一起。"
            "不猜、不做：先查 demucs（%s）" % work)
    rows, problems = narration_swap_plan(split_narration_script(script_text), segs or [])
    if problems:
        raise RuntimeError("改口播对账没过：\n  - " + "\n  - ".join(problems))

    swaps = [r for r in rows if r["action"] == "swap"]
    _log(on_log, "  共 %d 句，要重做 %d 句（其余 %d 句保留成片原声，不克隆）"
         % (len(rows), len(swaps), len(rows) - len(swaps)))
    for r in rows:
        mark = "重做" if r["action"] == "swap" else "保留"
        warn = ("  ⚠ " + r["density_warn"]) if r["density_warn"] else ""
        _log(on_log, "  %2d %.2f-%.2fs [%s] 「%s」→「%s」%s"
             % (r["seq"], r["start"], r["end"], mark, r["old"][:16], r["new"][:16], warn))

    plan_rec = {"video_in": vid, "duration": total, "rows": rows,
                "clone_calls": len(swaps), "dry_run": dry_run,
                "lipsync": False, "note": "不做口型同步：嘴形与新词对不上是这一档的已知代价"}
    if not swaps:
        plan_rec["problems"] = ["新文案和成片口播逐句相同 => 没有要改的句子，不产第二条成片"]
        (out / "narration_swap.json").write_text(
            json.dumps(plan_rec, ensure_ascii=False, indent=2), encoding="utf-8")
        raise RuntimeError(plan_rec["problems"][0])

    if dry_run:
        cmd_preview = ["ffmpeg", "-y", "-i", vid, "-i", str(vocals), "-i", str(bgm)]
        cmd_preview += sum([["-i", str(out / ("clone_%02d.wav" % r["seq"]))] for r in swaps], [])
        cmd_preview += ["-filter_complex", build_narration_graph(
            [(r["start"], r["end"]) for r in swaps]),
            "-map", "0:v", "-map", "[outa]", "-c:v", "copy", "-c:a", "aac", "-shortest",
            str(out / "narration_swapped.mp4")]
        plan_rec["command"] = cmd_preview
        plan_rec["would_spend"] = "克隆 %d 次（RunningHub，不在网关账本上）" % len(swaps)
        (out / "narration_swap.json").write_text(
            json.dumps(plan_rec, ensure_ascii=False, indent=2), encoding="utf-8")
        _log(on_log, "✓ 档位=只对照 => 一个克隆请求都没发、没落成片。命令预览已写进 narration_swap.json")
        return plan_rec

    windows = []
    for r in swaps:
        ref = str(out / ("ref_%02d.wav" % r["seq"]))
        raw = str(out / ("clone_%02d_raw.wav" % r["seq"]))
        aligned = str(out / ("clone_%02d.wav" % r["seq"]))
        _cut_av(str(vocals), r["start"], min(r["window"], REF_MAX_S), ref, audio_only=True)
        _log(on_log, "▶ 第 %d 句克隆（参考 %.2fs 成片原人声）：「%s」"
             % (r["seq"], _duration(ref), r["new"][:20]))
        res = await clone_fn(r["new"], ref, raw) or {}
        r["task_id"] = str(res.get("task_id") or "")
        r["usage"] = res.get("usage") or {}
        got = _duration(raw)
        ratio = round(got / r["window"], 3) if r["window"] > 0 else 0.0
        r["clone_duration"], r["tempo"] = round(got, 3), ratio
        if not (TEMPO_MIN <= ratio <= TEMPO_MAX):
            r["status"] = "tempo_out_of_range"
            plan_rec["spent_so_far"] = _usage_sum(rows)
            (out / "narration_swap.json").write_text(
                json.dumps(plan_rec, ensure_ascii=False, indent=2), encoding="utf-8")
            raise RuntimeError(
                "第 %d 句压不回去：克隆出来 %.2fs，窗口只有 %.2fs（%.1f 倍，超出 %.1f-%.1f）。"
                "这一句的钱已经花了（usage=%s）=> 改短文案重跑这一句，或把这一句改回原话；"
                "不许拿变速糊过去（那会改变音色）。"
                % (r["seq"], got, r["window"], ratio, TEMPO_MIN, TEMPO_MAX, r["usage"]))
        _fit_audio_duration(raw, r["window"], aligned)
        target_db = _mean_volume_db(str(vocals), r["start"], min(r["window"], REF_MAX_S))
        got_db = _mean_volume_db(aligned)
        gain, delta = None, None
        if target_db is not None and got_db is not None:
            delta = round(target_db - got_db, 2)
            clamped = max(-12.0, min(12.0, delta))          # 超 12dB 不硬拉：那会把底噪一起拉起来
            gain = round(10.0 ** (clamped / 20.0), 4)
            r["loudness_delta_db"] = delta
            r["loudness_clamped"] = abs(delta) > 12.0
        r["gain_linear"] = gain
        r["status"] = "ok"
        windows.append({"start": r["start"], "end": r["end"], "path": aligned, "gain": gain})

    graph = build_narration_graph([(w["start"], w["end"]) for w in windows],
                                  gains=[w["gain"] for w in windows])
    final = str(out / "narration_swapped.mp4")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", vid, "-i", str(vocals), "-i", str(bgm)]
    cmd += sum([["-i", w["path"]] for w in windows], [])
    cmd += ["-filter_complex", graph, "-map", "0:v", "-map", "[outa]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-shortest", final]
    _run_ffmpeg(cmd, "音轨重建失败")
    plan_rec.update({"video_out": final, "usage_total": _usage_sum(rows),
                     "out_duration": round(_duration(final), 3)})
    (out / "narration_swap.json").write_text(
        json.dumps(plan_rec, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(on_log, "✓ 改口播完成 → %s（重做 %d 句 / 实扣合计 %s）"
         % (final, len(swaps), plan_rec["usage_total"]))
    return plan_rec


async def run_narration_audio(video_path: str, audio_path: str, output_dir: str,
                              on_log: LogFn = None) -> dict:
    """整条替换口播：把客户【已经念好新词的那条音频】贴上成片 —— 不克隆、不调上游、0 分。

    🔴 为什么必须有这一条（2026-10-14 现网）：wan3-prime 的音轨是从参考视频【搬】出来的
       （成片音轨 vs 源音轨相关系数 0.884），prompt 里的台词位不驱动发声；而克隆那条腿
       堵在 RunningHub `605 余额不足`。所以"口播要改"今天只剩这一条能立刻交付的路。
    与 `run_narration_swap` 的分工：那一条按句克隆、音色跟着原主播；这一条整条替换、
       音色 = 给音频的那个人 ⇒ **两者不能同时跑**（两条声叠在一起）。
    做法复用了同三件助手：`_fit_audio_to_video`（atempo 适配片长，不动画面窗口）、
       `build_narration_graph`（抹整条原人声 + 垫回 demucs 伴奏，缺伴奏 = BGM 一起没了）、
       `_mean_volume_db`（新声比原声小声就补增益，补过头 >12dB 如实标注不硬拉）。
    """
    from backend.services.prompt_reverser import _extract_voiceover

    vid, aud = str(video_path), str(audio_path)
    for path, what in ((vid, "成片"), (aud, "口播音频")):
        if not Path(path).is_file():
            raise RuntimeError("%s不在盘上: %s ⇒ 不做（不许拿没有的东西当已换）" % (what, path))
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    total = _duration(vid)
    if total <= 0:
        raise RuntimeError("成片时长读不出来: %s ⇒ 不做" % vid)
    work = out / "narration_audio_work"
    work.mkdir(parents=True, exist_ok=True)
    _log(on_log, "换口播：对成片做 ASR + 人声分离（本地、0 分上游），拿伴奏…")
    await _extract_voiceover(vid, work, total)
    vocals, bgm = (work / "demucs" / "htdemucs" / "asr_audio" / "vocals.wav"),                   (work / "demucs" / "htdemucs" / "asr_audio" / "no_vocals.wav")
    if not (vocals.exists() and bgm.exists()):
        raise RuntimeError(
            "人声分离没产出 vocals/no_vocals ⇒ 抹不掉原人声（会两条声叠在一起），"
            "也不许把伴奏一起丢掉。先查 demucs（%s）" % work)
    fitted = _fit_audio_to_video(aud, vid, str(work / "narration_fitted.wav"))
    src_db, dst_db = _mean_volume_db(str(vocals)), _mean_volume_db(fitted)
    gain, clamped = 1.0, False
    if src_db is not None and dst_db is not None:
        delta = round(src_db - dst_db, 2)
        clamped = abs(delta) > 12.0
        gain = round(10.0 ** (max(-12.0, min(12.0, delta)) / 20.0), 4)
    graph = build_narration_graph([(0.0, total)], gains=[gain])
    final = str(out / "narration_audio_swapped.mp4")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", vid, "-i", str(vocals), "-i", str(bgm),
           "-i", fitted, "-filter_complex", graph, "-map", "0:v", "-map", "[outa]",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-shortest", final]
    _run_ffmpeg(cmd, "整条换口播失败")
    rec = {"kind": "narration_audio", "video_in": vid, "audio_in": aud,
           "film_duration": round(total, 3), "audio_duration_in": round(_duration(aud), 3),
           "audio_duration_fitted": round(_duration(fitted), 3),
           "gain_linear": gain, "gain_clamped": clamped,
           "vocals_db": src_db, "new_voice_db": dst_db,
           "executor": "backend:invoke:voiceover_replace.run_narration_audio",
           "spend": 0, "note": "整条替换（音色=给的那条音频），未跑克隆、未跑对口型",
           "video_out": final, "out_duration": round(_duration(final), 3)}
    (out / "narration_audio.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(on_log, "✓ 口播整条换掉 → %s（%.2fs，增益 %.3f%s）"
         % (final, rec["out_duration"], gain, "，超 12dB 已夹住" if clamped else ""))
    return rec
