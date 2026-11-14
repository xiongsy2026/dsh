"""
口播混剪 Pipeline - HyperFrames 版
─────────────────────────────────────────────────
以主视频(92号)的口播音频为时间轴，自动：
1. ASR 识别口播分段 + 静音检测
2. LLM 分析口播内容 → 生成 B-roll 切入方案
3. 生成 HyperFrames HTML 项目（主视频底层 + B-roll 覆盖 + 独立音轨）
4. 调用 hyperframes CLI 渲染 → 9:16 竖版 final.mp4

与 ffmpeg 版的区别：
- 主视频完整保留作为底层（含原音轨），气口先不剪
- B-roll 视频段作为上层覆盖，音频继续是主视频原音
- 用 hyperframes 渲染，可对话式修改
"""
import os
import re
import sys
import json
import time
import asyncio
import subprocess
import shutil
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config import settings

# 复用 ffmpeg 版的 ASR 逻辑
from pipeline.oral_mashup_pipeline import (
    run_asr_with_silence,
    _call_llm_with_fallback,
)


async def generate_edit_plan_for_hf(
    main_video: str,
    broll_videos: list,
    asr_result: dict,
    work_dir: Path,
    engine: str = "gpt",
) -> dict:
    """
    生成 HyperFrames 专用剪辑方案。

    与 ffmpeg 版的区别：每段明确区分
      - timeline_start/end: 该段在主视频时间轴上的位置（底层主视频的覆盖窗口）
      - media_start: 从源视频的哪里开始播放（main 段 = timeline_start，B-roll 段 = B-roll 源视频的起始偏移）

    方案格式:
      {
        "main_total_duration": float,  # 主视频总时长
        "broll_overlays": [
          {
            "broll_index": int,        # B-roll 素材索引
            "timeline_start": float,   # 在主视频时间轴上的起始位置（覆盖底层主视频画面）
            "timeline_end": float,     # 在主视频时间轴上的结束位置
            "media_start": float,      # 从 B-roll 源视频的哪里开始播
            "reason": "为何选这段"
          },
          ...
        ]
      }

    主视频本身作为底层全程播放（含原音轨），B-roll 段作为上层画面覆盖。
    """
    print(f"\n[Step2] LLM 生成剪辑方案 (HyperFrames 版, engine={engine})")

    # B-roll 元数据
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

    main_basename = os.path.basename(main_video)
    main_duration = asr_result["duration"]

    # 把 ASR 段映射到时间轴展示
    asr_view = []
    for s in asr_result["asr_segments"]:
        asr_view.append({
            "start": round(s["start"], 2),
            "end": round(s["end"], 2),
            "text": s["text"],
        })

    system_prompt = """你是一位专业的视频剪辑师，擅长把"主播口播 + B-roll 生产画面"混剪成一条节奏紧凑的 9:16 竖版短视频。

任务：根据主视频的口播内容，决定在主视频的哪些时间段切入 B-roll 生产画面，覆盖主播画面（但音频继续是主视频原音）。

剪辑原则：
1. 主视频(主播)的口播音频是整条视频的声音轨，全程不变，不要切音频
2. 当主播说到某个产品/工序/工厂时，切到对应的 B-roll 生产画面（覆盖主播画面，但音频继续是主播口播）
3. 主播说完该产品/工序后，切回主播画面（B-roll 覆盖结束，露出底层主播画面）
4. B-roll 切入时长通常 2-5 秒，不要太长
5. 主视频本身有成品展示收尾，结尾段（最后 3-5 秒）保留主播画面，不要切 B-roll
6. 不要每段都切 B-roll，只在语义对应处切入（保持节奏，整条视频 3-5 个 B-roll 切入点即可）
7. 输出 JSON 必须严格符合指定 schema，不要任何解释文字

输出 JSON Schema:
{
  "broll_overlays": [
    {
      "broll_index": B-roll 素材索引(整数, 0-based),
      "timeline_start": 该 B-roll 在主视频时间轴上的起始位置(秒, float),
      "timeline_end": 该 B-roll 在主视频时间轴上的结束位置(秒, float),
      "media_start": 从 B-roll 源视频的哪个时间点开始播放(秒, float, 通常 0),
      "reason": "为何选这段(简短)"
    }
  ]
}

注意：
- timeline_start/end 是该 B-roll 段在主视频时间轴上的覆盖窗口（这段时间主视频画面被 B-roll 覆盖）
- media_start 是从 B-roll 源视频的哪个时间点开始取素材（如果 B-roll 源视频开头有水印/无用画面，可以从 1-2 秒后开始）
- timeline_end - timeline_start = B-roll 切入时长，必须 <= B-roll 源视频剩余时长（duration - media_start）
- broll_overlays 按 timeline_start 升序排列
- 第一段（0-3s）和最后一段（最后 3-5s）通常不切 B-roll，保留主播画面
- 只输出 JSON，不要 markdown 代码块，不要任何文字说明"""

    user_content = f"""主视频: {main_basename} (时长 {main_duration:.2f}s, 全程保留作底层 + 音轨)

B-roll 素材列表（画面覆盖用，音频不用）:
{json.dumps(broll_meta, ensure_ascii=False, indent=2)}

主视频口播分段（ASR 识别）:
{json.dumps(asr_view, ensure_ascii=False, indent=2)}

主视频静音段（气口，可参考但不必处理）:
{json.dumps(asr_result['silence_segments'], ensure_ascii=False, indent=2)}

请基于口播内容判断：在主视频的哪些时间段切入哪个 B-roll？切入多长？从 B-roll 源视频的哪里开始取素材？

输出剪辑方案 JSON:"""

    plan_text = await _call_llm_with_fallback(system_prompt, user_content, engine)

    # 解析 JSON
    plan_text = plan_text.strip()
    if plan_text.startswith("```"):
        plan_text = re.sub(r"^```(?:json)?\s*", "", plan_text)
        plan_text = re.sub(r"\s*```$", "", plan_text)
    plan = json.loads(plan_text)

    # 补充 main_total_duration + 校验
    plan["main_total_duration"] = main_duration

    # 校验 B-roll 时长不超源视频剩余
    for ov in plan.get("broll_overlays", []):
        idx = ov["broll_index"]
        if idx >= len(broll_videos):
            raise RuntimeError(f"broll_index {idx} 越界（只有 {len(broll_videos)} 个 B-roll）")
        broll_dur = broll_meta[idx]["duration"]
        ms = ov["media_start"]
        tl_dur = ov["timeline_end"] - ov["timeline_start"]
        max_avail = broll_dur - ms
        if tl_dur > max_avail:
            # 截短 timeline_end
            ov["timeline_end"] = round(ov["timeline_start"] + max_avail, 2)
            print(f"  ⚠ B-roll {idx} 时长超源视频，截短到 {max_avail:.2f}s")

    # 按 timeline_start 排序
    plan["broll_overlays"].sort(key=lambda x: x["timeline_start"])

    # 写入 edit_plan.json
    (work_dir / "edit_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  → 剪辑方案: {len(plan['broll_overlays'])} 个 B-roll 覆盖段")
    for i, ov in enumerate(plan["broll_overlays"]):
        tl_dur = ov["timeline_end"] - ov["timeline_start"]
        print(f"    [{i:02d}] broll_{ov['broll_index']} timeline {ov['timeline_start']:6.2f}-{ov['timeline_end']:6.2f} ({tl_dur:.2f}s) media_start={ov['media_start']:.2f} | {ov.get('reason','')}")
    return plan


# ═══════════════════════════════════════
# Step 3: 生成 HyperFrames 项目
# ═══════════════════════════════════════

HYPERFRAMES_CLI = r"d:\video-skill\hyperframes\packages\cli\bin\hyperframes.mjs"


def build_hyperframes_project(
    plan: dict,
    main_video: str,
    broll_videos: list,
    work_dir: Path,
    output_resolution: tuple = (1080, 1920),
) -> Path:
    """
    按 HyperFrames 专用剪辑方案生成项目。

    结构：
      project/
        ├─ index.html
        └─ assets/
            ├─ main.mp4 (主视频，含音轨)
            ├─ broll_0.mp4, broll_1.mp4, ...
    """
    print(f"\n[Step3-HF] 生成 HyperFrames 项目")
    W, H = output_resolution
    project_dir = work_dir / "project"
    assets_dir = project_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    # 3.1 拷贝素材到 assets/
    print(f"  → 拷贝素材到 assets/...")
    main_dst = assets_dir / "main.mp4"
    shutil.copy2(main_video, main_dst)
    for i, v in enumerate(broll_videos):
        shutil.copy2(v, assets_dir / f"broll_{i}.mp4")

    # 3.2 解析方案
    main_total = float(plan["main_total_duration"])
    overlays = plan.get("broll_overlays", [])
    print(f"  → 主视频总时长: {main_total:.2f}s, B-roll 覆盖段: {len(overlays)} 个")

    # 3.3 生成 HTML
    # 策略：
    #   - 主视频作为底层（track 0），全程显示画面
    #   - 主视频音频作为独立音轨（track 10），全程播放
    #   - B-roll 段作为上层覆盖（track 1），按 timeline_start/end 显示，data-media-start 指定源视频偏移
    html_parts = []
    html_parts.append(f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width={W}, height={H}" />
  <title>口播混剪 - HyperFrames</title>
  <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: #000;
      color: white;
      font-family: system-ui, -apple-system, sans-serif;
    }}
    #root {{
      position: relative;
      width: {W}px;
      height: {H}px;
      overflow: hidden;
      background: #000;
    }}
    video {{
      position: absolute;
      top: 0; left: 0;
      width: 100%;
      height: 100%;
      object-fit: cover;
    }}
  </style>
</head>
<body>
  <div id="root"
       data-composition-id="main"
       data-start="0"
       data-width="{W}"
       data-height="{H}"
       data-duration="{main_total:.3f}"
       data-fps="30">

    <!-- 主视频底层（全程画面）-->
    <video id="main_video"
           src="assets/main.mp4"
           data-start="0"
           data-duration="{main_total:.3f}"
           data-track-index="0"
           data-media-start="0"
           muted
           playsinline></video>

    <!-- 主视频独立音轨（全程）-->
    <audio id="main_audio"
           src="assets/main.mp4"
           data-start="0"
           data-duration="{main_total:.3f}"
           data-track-index="10"
           data-volume="1"></audio>
""")

    # 添加 B-roll 覆盖段
    for i, ov in enumerate(overlays):
        b_idx = int(ov["broll_index"])
        tl_start = float(ov["timeline_start"])
        tl_end = float(ov["timeline_end"])
        tl_dur = tl_end - tl_start
        media_start = float(ov["media_start"])
        if tl_dur <= 0.1:
            continue
        if b_idx >= len(broll_videos):
            continue

        reason = ov.get("reason", "")
        reason_safe = reason.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")

        html_parts.append(f"""
    <!-- B-roll #{b_idx} overlay_{i}: {reason_safe} -->
    <video id="overlay_{i}"
           src="assets/broll_{b_idx}.mp4"
           data-start="{tl_start:.3f}"
           data-duration="{tl_dur:.3f}"
           data-track-index="1"
           data-media-start="{media_start:.3f}"
           muted
           playsinline></video>""")

    html_parts.append(f"""
  </div>

  <script>
    window.__timelines = window.__timelines || {{}};
    const tl = gsap.timeline({{ paused: true }});
    // 可在此添加 B-roll 切入淡入/淡出转场
    window.__timelines["main"] = tl;
  </script>
</body>
</html>""")

    index_path = project_dir / "index.html"
    index_path.write_text("\n".join(html_parts), encoding="utf-8")
    print(f"  ✓ 项目生成: {project_dir}")
    print(f"    ├─ index.html ({len(overlays)} 个 B-roll 覆盖段)")
    print(f"    └─ assets/ ({1 + len(broll_videos)} 个素材)")
    return project_dir


# ═══════════════════════════════════════
# Step 4: HyperFrames lint + render
# ═══════════════════════════════════════

def run_hyperframes_lint(project_dir: Path) -> bool:
    """运行 hyperframes lint 检查项目合法性。"""
    print(f"\n[Step4a] HyperFrames lint...")
    # CLI 第一个参数是项目目录（包含 index.html），不是 index.html 文件本身
    cmd = ["node", HYPERFRAMES_CLI, "lint", str(project_dir)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", cwd=str(project_dir))
    if proc.stdout:
        print(proc.stdout)
    if proc.stderr:
        print(proc.stderr)
    if proc.returncode != 0:
        print(f"  ✗ lint 失败 (exit={proc.returncode})")
        return False
    print(f"  ✓ lint 通过")
    return True


def run_hyperframes_render(project_dir: Path, output_path: Path) -> bool:
    """渲染 hyperframes 项目为 mp4。"""
    print(f"\n[Step4b] HyperFrames render...")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "node", HYPERFRAMES_CLI, "render",
        str(project_dir),
        "--output", str(output_path),
    ]
    print(f"  → {' '.join(cmd)}")
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        cwd=str(project_dir),
    )
    if proc.stdout:
        print(proc.stdout[-2000:])
    if proc.stderr:
        print(proc.stderr[-2000:])
    if proc.returncode != 0:
        print(f"  ✗ render 失败 (exit={proc.returncode})")
        return False
    if not output_path.exists():
        print(f"  ✗ 输出文件不存在: {output_path}")
        return False
    size_mb = output_path.stat().st_size / 1024 / 1024
    print(f"  ✓ render 成功: {output_path} ({size_mb:.2f} MB)")
    return True


async def revise_edit_plan_for_hf(
    current_plan: dict,
    instruction: str,
    main_video: str,
    broll_videos: list,
    asr_result: dict,
    work_dir: Path,
    engine: str = "gpt",
) -> dict:
    """根据 Agent 指令修改现有剪辑方案，不重复 ASR。"""
    broll_meta = []
    for i, video_path in enumerate(broll_videos):
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", video_path],
            capture_output=True, text=True, encoding="utf-8",
        )
        try:
            duration = float(json.loads(probe.stdout)["format"]["duration"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            duration = 0.0
        broll_meta.append({"index": i, "file": os.path.basename(video_path), "duration": round(duration, 2)})

    prompt = f"""你是专业视频剪辑师。根据用户指令修改现有 HyperFrames 剪辑方案。
只输出 JSON，不要 markdown，不要解释。必须保留 schema：
{{"main_total_duration": number, "broll_overlays": [{{"broll_index": integer, "timeline_start": number, "timeline_end": number, "media_start": number, "reason": string}}]}}

用户指令：{instruction}
现有方案：{json.dumps(current_plan, ensure_ascii=False)}
B-roll 素材：{json.dumps(broll_meta, ensure_ascii=False)}
主视频口播：{json.dumps(asr_result.get('asr_segments', []), ensure_ascii=False)}
要求：时间不越界，B-roll 不超过源素材可用时长，按 timeline_start 升序。"""
    text = await _call_llm_with_fallback(
        "你只负责修改视频剪辑时间线，输出严格 JSON。", prompt, engine
    )
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\\s*", "", text)
        text = re.sub(r"\\s*```$", "", text)
    plan = json.loads(text)
    plan["main_total_duration"] = float(asr_result["duration"])
    for overlay in plan.get("broll_overlays", []):
        idx = int(overlay["broll_index"])
        if idx < 0 or idx >= len(broll_videos):
            raise RuntimeError(f"broll_index {idx} 越界")
        media_start = max(0.0, float(overlay.get("media_start", 0)))
        max_duration = max(0.0, broll_meta[idx]["duration"] - media_start)
        overlay["media_start"] = media_start
        overlay["timeline_start"] = max(0.0, float(overlay["timeline_start"]))
        overlay["timeline_end"] = min(
            float(overlay["timeline_end"]),
            overlay["timeline_start"] + max_duration,
            float(asr_result["duration"]),
        )
    plan["broll_overlays"].sort(key=lambda item: item["timeline_start"])
    (work_dir / "edit_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return plan


# ═══════════════════════════════════════
# 端到端入口
# ═══════════════════════════════════════

async def run_mashup_hf_pipeline(
    main_video: str,
    broll_videos: list,
    engine: str = "gpt",
    output_root: Optional[str] = None,
    skip_asr: bool = False,
) -> dict:
    """
    端到端运行口播混剪 pipeline (HyperFrames 版)。

    Args:
      main_video: 主视频路径
      broll_videos: B-roll 素材列表
      engine: LLM 引擎 ("gpt" / "grok")
      output_root: 输出根目录
      skip_asr: 跳过 ASR（用于调试，复用上次 asr.json）

    Returns:
      {
        "work_dir": str,
        "asr_result": dict,
        "edit_plan": dict,
        "project_dir": str,
        "final_video": str,
      }
    """
    if not os.path.exists(main_video):
        raise FileNotFoundError(f"主视频不存在: {main_video}")
    for v in broll_videos:
        if not os.path.exists(v):
            raise FileNotFoundError(f"B-roll 素材不存在: {v}")

    ts = time.strftime("%Y%m%d_%H%M%S")
    output_root = output_root or os.path.join(_PROJECT_ROOT, "outputs")
    work_dir = Path(output_root) / f"oral_mashup_hf_{ts}"
    work_dir.mkdir(parents=True, exist_ok=True)
    print(f"=== 口播混剪 Pipeline (HyperFrames 版) 启动 ===")
    print(f"工作目录: {work_dir}")
    print(f"主视频: {main_video}")
    print(f"B-roll: {len(broll_videos)} 个素材")

    # Step1: ASR + 静音检测
    asr_result = await run_asr_with_silence(main_video, work_dir)

    # Step2: LLM 生成剪辑方案（HyperFrames 专用格式）
    edit_plan = await generate_edit_plan_for_hf(
        main_video, broll_videos, asr_result, work_dir, engine=engine
    )

    # Step3: 生成 HyperFrames 项目
    project_dir = build_hyperframes_project(
        edit_plan, main_video, broll_videos, work_dir
    )

    # Step4: lint + render
    final_path = work_dir / "final.mp4"
    if not run_hyperframes_lint(project_dir):
        print("⚠ lint 未通过，仍尝试 render...")
    if not run_hyperframes_render(project_dir, final_path):
        raise RuntimeError("HyperFrames 渲染失败")

    print(f"\n=== 全部完成 ===")
    print(f"最终视频: {final_path}")
    return {
        "work_dir": str(work_dir),
        "asr_result": asr_result,
        "edit_plan": edit_plan,
        "project_dir": str(project_dir),
        "final_video": str(final_path),
    }


if __name__ == "__main__":
    DEFAULT_DIR = r"C:\Users\Administrator\Desktop\视频翻译"
    main_v = os.path.join(DEFAULT_DIR, "video(92).mp4")
    broll = [
        os.path.join(DEFAULT_DIR, "video(93).mp4"),
        os.path.join(DEFAULT_DIR, "video(96).mp4"),
        os.path.join(DEFAULT_DIR, "video(97).mp4"),
        os.path.join(DEFAULT_DIR, "video(98).mp4"),
    ]

    if len(sys.argv) >= 2:
        main_v = sys.argv[1]
    if len(sys.argv) >= 3:
        broll = sys.argv[2].split(",")

    result = asyncio.run(run_mashup_hf_pipeline(main_v, broll, engine="gpt"))
    print(f"\n最终视频: {result['final_video']}")
