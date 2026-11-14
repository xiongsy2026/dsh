---
name: video-subtitle-reader
description: "Read a video's spoken content by converting it to subtitles (transcript) with zero LLM tokens. Given a Douyin/TikTok/Bilibili/YouTube URL or a local video file, download the media, extract audio, and transcribe with local Whisper to produce a text transcript the user can read. Use when the user pastes a short-video link and says '理解/读/总结这个视频', '转字幕', '转文字', '把视频内容给我', or otherwise wants the spoken content of a video without watching it. 中文触发词: 视频转字幕, 视频转文字, 读视频, 理解视频, 字幕提取, 语音转文字."
---

# Video subtitle reader · 视频转字幕

Convert a video (URL or local file) into a plain-text transcript using the local toolchain. **Zero LLM tokens**: every step runs on the host — `yt-dlp` (download), `ffmpeg` (extract audio), `whisper` (transcribe) — so the user can read a video's spoken content without the agent burning credits to watch it.

## Prerequisites (verify before first use)

All three tools must be on PATH. Check with:

```powershell
ffmpeg -version        # expects WinGet-installed ffmpeg.exe
yt-dlp --version
whisper --help         # Python CLI wrapper
```

If any are missing, tell the user which one and stop — do **not** silently install.

## Workflow

1. **Parse the input.**
   - If it is a URL (Douyin share link, Bilibili, YouTube, TikTok…), use it directly. Douyin short links (`v.douyin.com/...`) resolve to a longer URL; yt-dlp handles the redirect.
   - If it is a local file path, skip download and go straight to step 3.

2. **Download the media.**
   ```powershell
   yt-dlp --no-update -f "bestaudio/best" -x --audio-format mp3 -o "<workdir>\audio.mp3" "<URL>"
   ```
   - `--no-update` silences the version-check warning (yt-dlp 2026.07.04 is flagged stale but works).
   - `-f bestaudio/best` grabs audio first, falls back to full video.
   - `-x --audio-format mp3` extracts audio to MP3 (Whisper's preferred input).
   - If the platform blocks yt-dlp (Douyin frequently does), try the app extractor:
     ```powershell
     yt-dlp --no-update --extractor-args "douyin:app" -f "bestaudio" -x --audio-format mp3 -o "<workdir>\audio.mp3" "<URL>"
     ```
   - If that fails with **"Fresh cookies are needed"** (Douyin's post-2025 anti-scrape), import browser cookies:
     ```powershell
     # Option A: use Chrome's cookies directly (most reliable)
     yt-dlp --no-update --cookies-from-browser chrome -f "bestaudio/best" -x --audio-format mp3 -o "<workdir>\audio.mp3" "<URL>"

     # Option B: user saves cookies.txt from browser devtools and passes a file
     yt-dlp --no-update --cookies "<workdir>\cookies.txt" -f "bestaudio/best" -x --audio-format mp3 -o "<workdir>\audio.mp3" "<URL>"
     ```
     Tell the user which one to use, and stop here — do not try to scrape the page yourself or ask them to log in to Douyin for you.
   - If all download routes fail, **fall back to a local file**: ask the user to save the audio (or the whole video) from their phone/computer and pass you the path, then go straight to step 3.

3. **Extract / verify the audio.**
   If the download produced a video file (no audio extraction), convert:
   ```powershell
   ffmpeg -i "<input>" -vn -acodec libmp3lame -ab 128k -ar 16000 -ac 1 "<workdir>\audio.mp3" -y
   ```
   - `-ar 16000 -ac 1` is the 16 kHz mono sample rate Whisper was trained on; it avoids a decode resample inside Whisper and speeds things up.

4. **Transcribe with Whisper.**
   Pick a model size based on available memory and video length:
   - Short video (< 5 min) / low-RAM machine → `small`
   - Medium (5–30 min) / ≥ 16 GB RAM → `medium`
   - Long (> 30 min) / ≥ 32 GB RAM → `large-v3` (or `medium` if you want speed over accuracy)
   ```powershell
   whisper "<workdir>\audio.mp3" --model <size> --language zh --output_format txt --output_dir "<workdir>"
   ```
   - `--language zh` forces Chinese; omit it for English or multilingual auto-detect.
   - For mixed CN/EN content, drop `--language` and let Whisper auto-detect.
   - Output: `<workdir>\audio.txt` (or the source filename without extension).

5. **Return the transcript.**
   Read the `.txt` file and present it to the user:
   - If the video is short (< 2 min), paste the full transcript inline.
   - If longer, save it to a file in the session workspace, present it, and give a 3–5 bullet summary of the main points.

## Pitfalls

- **Douyin share links have junk in the URL.** The tail like `10/20 C@H.VY :1pm qrr:/` is an anti-copy guard, not part of the real URL. Strip it: the share URL is `https://v.douyin.com/WtHRsOoKonw/`.
- **Whisper `small` on Chinese** is usable but occasionally mis-transcribes names, product codes, and technical terms. If the user cares about precise names, re-run with `medium`.
- **Audio-only files (`.m4a`, `.aac`) work directly** — skip step 3, pass them to Whisper as-is.
- **Video with no audio** (silent B-roll, music-only): Whisper returns an empty transcript. Check with `ffmpeg -i <file> -af volumedetect -f null -` first if the transcript is suspiciously empty.
- **Don't re-download a video the user already has locally.** If the user gives a file path, skip steps 1–2 entirely.
- **yt-dlp version warning is harmless** — add `--no-update` to every yt-dlp call to silence it; the version is not actually broken.
- **Douyin 403 is a cookie problem, not a code bug.** Do not loop retrying without cookies; escalate to the cookie-import step or the local-file fallback.

## 中文说明

把一段视频（链接或本地文件）转成纯文字字幕，全程本地跑，**不花 LLM token**：

1. `yt-dlp` 下载视频/音频（抖音短链去掉末尾那串干扰码）
2. `ffmpeg` 抽成 16 kHz 单声道 MP3
3. `whisper` 本地转写（中文加 `--language zh`；模型按内存和时长选 small/medium/large）
4. 读 `.txt` 给你

**触发场景：** 用户贴了一段短视频链接说"理解下这个视频"、"把视频转成文字"、"读一下这个视频说了什么"，或给了本地视频文件要转字幕。

**抖音下载失败（需要 cookie）怎么办：** 优先用 `--cookies-from-browser chrome`，其次让用户手动存 cookies.txt，最后兜底让用户手机/电脑保存音视频文件给你路径。
