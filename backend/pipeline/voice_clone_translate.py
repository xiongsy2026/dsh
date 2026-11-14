"""
音色克隆英文翻译配音
─────────────────────────────────────────────────
用指定视频的音色,给 92 号视频的英文翻译配音。

流程:
  1. 从音色参考视频提取前 10 秒音频(24kHz mono wav)→ 作为 IndexTTS 参考音色
  2. 复用 92 号视频已有的英文翻译字幕(subtitle.json,带时间戳和中英对照)
  3. 整段英文文本一次性调 IndexTTS → 完整克隆英文音频(语调连贯)
  4. 整体 atempo 调速 + apad 补静音,让音轨时长 = 视频时长
     (LatentSync 只要求音频时长 ≈ 视频时长,不需要按字幕切片对齐)

为何整段传而非分段:
  - IndexTTS 是 TTS 模型,整段输入能让段间停顿和语调衔接更自然
  - 1 次 API 调用 vs 10 次,节省时间和上传开销
  - LatentSync 内部会对齐口型与音频,不需要音频按字幕段对齐

用途:
  LatentSync 对口型需要英文音轨 + 主播口型视频。本脚本产出
  与 92 号视频时长一致的克隆音色英文音轨。

用法:
  python pipeline/voice_clone_translate.py <音色参考视频> [字幕json] [输出目录]

  字幕 json 默认指向最新一次 92 号翻译的 subtitle.json
  输出目录默认 outputs/voice_clone_<ts>/
"""
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.services.video_translate_pipeline import (
    _index_tts_clone,
    _get_audio_duration,
)


def extract_ref_audio(video_path: str, out_wav: str, max_duration: float = 10.0) -> str:
    """从视频提取前 max_duration 秒 24kHz mono wav,作为 IndexTTS 参考音色。

    IndexTTS 参考音频不需要太长,5-10 秒就足以建模音色。
    过长会让上传和克隆变慢,且可能引入杂音段。
    """
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le",
        "-ar", "24000", "-ac", "1",
        "-t", f"{max_duration:.3f}",
        out_wav,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0 or not os.path.exists(out_wav):
        raise RuntimeError(f"参考音频提取失败: {p.stderr[-500:]}")
    return out_wav


async def clone_full_text(full_text: str, ref_audio: str, out_wav: str) -> str:
    """整段文本一次性调 IndexTTS 克隆,产出连贯的完整英文音频。"""
    await _index_tts_clone(
        text=full_text,
        ref_audio=ref_audio,
        output_path=out_wav,
        target_lang="en",
    )
    if not os.path.exists(out_wav):
        raise RuntimeError(f"克隆产物缺失: {out_wav}")
    return out_wav


def fit_to_duration(full_wav: str, target_dur: float, out_wav: str) -> str:
    """整体调速 + 补静音,让音轨时长 = target_dur。

    - 原音频比 target 长: atempo 调快(上限 2.0,超出链式),裁剪到 target
    - 原音频比 target 短: 不调速,apad 补静音到 target(保持自然语速)
    - 接近(差<0.3s): 直接 atempo 微调
    """
    source_dur = max(0.01, _get_audio_duration(full_wav))
    filters = []

    diff = source_dur - target_dur
    if abs(diff) < 0.3:
        # 微调
        tempo = source_dur / target_dur
        filters.append(f"atempo={tempo:.6f}")
    elif diff > 0:
        # 源比目标长,调快
        tempo = source_dur / target_dur
        t = tempo
        while t > 2.0:
            filters.append("atempo=2.0")
            t /= 2.0
        while t < 0.5:
            filters.append("atempo=0.5")
            t /= 0.5
        filters.append(f"atempo={t:.6f}")
    else:
        # 源比目标短,补静音(不调速)
        filters.append(f"apad=pad_dur={-diff:.3f}")

    cmd = [
        "ffmpeg", "-y", "-i", full_wav,
        "-af", ",".join(filters),
        "-t", f"{target_dur:.3f}",
        "-ar", "24000", "-ac", "1",
        "-c:a", "pcm_s16le",
        out_wav,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0 or not os.path.exists(out_wav):
        raise RuntimeError(f"时长对齐失败: {p.stderr[-500:]}")
    return out_wav


def find_latest_subtitle_json() -> str:
    """找最新一次 92 号视频翻译的 subtitle.json。"""
    pattern = "voice_translate_video(92)_no_breath_*"
    dirs = sorted(_PROJECT_ROOT.glob(pattern))
    if not dirs:
        # 兜底: 任意 92 号翻译目录
        dirs = sorted(_PROJECT_ROOT.glob("outputs/voice_translate_video(92)*"))
    if not dirs:
        raise RuntimeError("未找到 92 号视频翻译字幕,请先运行 translate_voice.py")
    sub = dirs[-1] / "subtitle.json"
    if not sub.exists():
        raise RuntimeError(f"字幕文件不存在: {sub}")
    return str(sub)


async def main_async(ref_video: str, subtitle_json: str, out_dir: Path) -> str:
    print(f"\n输出目录: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: 提取参考音色(前 10 秒)
    print(f"\n[Step1] 从音色参考视频提取前 10 秒音频(24kHz mono)...")
    ref_audio = str(out_dir / "ref_audio.wav")
    extract_ref_audio(ref_video, ref_audio, max_duration=10.0)
    ref_dur = _get_audio_duration(ref_audio)
    print(f"  ✓ 参考音频: {ref_audio} ({ref_dur:.2f}s)")

    # Step 2: 加载字幕
    print(f"\n[Step2] 加载 92 号英文翻译字幕: {subtitle_json}")
    translated = json.loads(Path(subtitle_json).read_text(encoding="utf-8"))
    print(f"  ✓ {len(translated)} 段中英对照字幕")

    # Step 3: 整段英文文本一次性克隆
    print(f"\n[Step3] 整段文本一次性调 IndexTTS 克隆(1 次 API 调用,语调连贯)...")
    full_text = " ".join(s.get("target_text", "").strip() for s in translated if s.get("target_text"))
    full_clone_wav = str(out_dir / "cloned_full_raw.wav")
    print(f"  全文({len(full_text)} 字符): {full_text[:80]}{'...' if len(full_text)>80 else ''}")
    await clone_full_text(full_text, ref_audio, full_clone_wav)
    raw_dur = _get_audio_duration(full_clone_wav)
    print(f"  ✓ 整段克隆完成: {full_clone_wav} ({raw_dur:.2f}s)")

    # Step 4: 整体调速 + 补静音,对齐到 92 号视频时长
    # 92 号视频(剪好气口版)时长 27.65s,LatentSync 要求音频时长 ≈ 视频时长
    target_dur = float(translated[-1].get("end", 27.65))  # 用字幕 last end 作为目标时长
    print(f"\n[Step4] 整体 atempo+apad 对齐到 {target_dur:.2f}s...")
    full = str(out_dir / "cloned_translated_full.wav")
    fit_to_duration(full_clone_wav, target_dur, full)
    full_dur = _get_audio_duration(full)

    # 保存中英对照字幕副本
    (out_dir / "subtitle.json").write_text(
        json.dumps(translated, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n{'='*60}")
    print(f"✓ 音色克隆英文翻译配音完成")
    print(f"  参考音色视频: {ref_video}")
    print(f"  参考音色:    {ref_audio} ({ref_dur:.2f}s)")
    print(f"  字幕段数:    {len(translated)}")
    print(f"  原始克隆:    {raw_dur:.2f}s")
    print(f"  目标时段:    {translated[0]['start']:.2f}s - {translated[-1]['end']:.2f}s")
    print(f"  产物音轨:    {full}")
    print(f"  产物时长:    {full_dur:.2f}s")
    print(f"  输出目录:    {out_dir}")
    print(f"{'='*60}")
    return full


def main():
    if len(sys.argv) < 2:
        print("用法: python pipeline/voice_clone_translate.py <音色参考视频> [字幕json] [输出目录]")
        sys.exit(1)

    ref_video = os.path.abspath(sys.argv[1])
    if not os.path.isfile(ref_video):
        print(f"❌ 音色参考视频不存在: {ref_video}")
        sys.exit(1)

    subtitle_json = sys.argv[2] if len(sys.argv) > 2 else find_latest_subtitle_json()

    if len(sys.argv) > 3:
        out_dir = Path(sys.argv[3])
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = _PROJECT_ROOT / "outputs" / f"voice_clone_{ts}"

    print(f"\n{'='*60}")
    print(f"音色克隆英文翻译配音")
    print(f"  音色参考: {os.path.basename(ref_video)}")
    print(f"  字幕:    {subtitle_json}")
    print(f"{'='*60}")

    asyncio.run(main_async(ref_video, subtitle_json, out_dir))


if __name__ == "__main__":
    main()
