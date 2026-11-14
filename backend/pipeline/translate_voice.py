"""
英文翻译配音
─────────────────────────────────────────────────
对指定视频做:ASR(中文识别) → GPT 翻译(中→英) → edge_tts 美式女声配音

复用 video_translate_pipeline 的 Step 1/2 函数,只产出英文配音音频,
不走 LatentSync / HyperFrames(那些步骤耗时长且依赖外部服务)。

用途:
  给剪好气口的 92 号纯主播视频生成英文翻译配音音轨,
  供后续 LatentSync 对口型使用。

用法:
  python pipeline/translate_voice.py "<视频路径>"

输出(outputs/voice_translate_<ts>/):
  ├─ subtitle.json        - 中英对照字幕(带时间戳)
  ├─ voice_0000.mp3~      - 各段英文配音原始 mp3
  ├─ voice_0000.wav~      - 各段调速对齐后的 wav
  ├─ us_female_full.wav   - 合成的完整英文配音音轨(按原字幕时间轴对齐)
  └─ asr.json             - ASR 原始分段
"""
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# 把项目根目录加入 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.services.video_translate_pipeline import (
    run_asr,
    translate_text,
    run_us_female_tts,
)


async def main_async(video_path: str) -> str:
    # 输出目录
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = Path(video_path).stem
    out_dir = _PROJECT_ROOT / "outputs" / f"voice_translate_{stem}_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n输出目录: {out_dir}")

    # ── Step 1: ASR ──
    print(f"\n[Step1] ASR 语音识别...")
    asr_result = await run_asr(video_path, source_lang="zh", engine="whisper")
    segments = asr_result["segments"]
    print(f"  → 识别 {len(segments)} 段:")
    for i, s in enumerate(segments):
        print(f"    [{i:02d}] {s['start']:6.2f}-{s['end']:6.2f}  {s['text']}")

    # 保存 ASR 原始结果
    (out_dir / "asr.json").write_text(
        json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if not segments:
        raise RuntimeError("ASR 未识别到任何语音")

    # ── Step 2: GPT 翻译(中→英) ──
    print(f"\n[Step2] GPT 翻译(中→英)...")
    translated = await translate_text(
        segments=segments,
        source_lang="zh",
        target_lang="en",
        engine="gpt",
    )
    print(f"  → 翻译完成:")
    for i, s in enumerate(translated):
        print(f"    [{i:02d}] {s['start']:6.2f}-{s['end']:6.2f}")
        print(f"         ZH: {s['source_text']}")
        print(f"         EN: {s['target_text']}")

    # 保存字幕
    (out_dir / "subtitle.json").write_text(
        json.dumps(translated, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ── Step 3: edge_tts 美式女声配音 ──
    print(f"\n[Step3] edge_tts 美式女声配音(en-US-JennyNeural)...")
    full_audio = await run_us_female_tts(
        translated_segments=translated,
        output_dir=str(out_dir),
        voice="en-US-JennyNeural",
    )
    print(f"  ✓ 完整英文配音音轨: {full_audio}")

    # 打印结果
    print(f"\n{'='*60}")
    print(f"✓ 英文翻译配音完成")
    print(f"  视频时长:   {max(s['end'] for s in segments):.2f}s")
    print(f"  字幕段数:   {len(translated)}")
    print(f"  字幕文件:   {out_dir / 'subtitle.json'}")
    print(f"  完整音轨:   {full_audio}")
    print(f"  输出目录:   {out_dir}")
    print(f"{'='*60}")
    return str(full_audio)


def main():
    if len(sys.argv) < 2:
        print("用法: python pipeline/translate_voice.py <视频路径>")
        sys.exit(1)

    video_path = os.path.abspath(sys.argv[1])
    if not os.path.isfile(video_path):
        print(f"❌ 文件不存在: {video_path}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"英文翻译配音: {os.path.basename(video_path)}")
    print(f"{'='*60}")

    asyncio.run(main_async(video_path))


if __name__ == "__main__":
    main()
