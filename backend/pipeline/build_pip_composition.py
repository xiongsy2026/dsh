"""
HyperFrames B-roll 切换成片编排
─────────────────────────────────────────────────
基于 GPT 语义匹配方案,生成 HyperFrames composition:
  - 主视频:LatentSync 英文版主播口型(全程作为底层,贡献英文音轨)
  - B-roll 素材:按字幕语义在对应时段全屏切换覆盖(无声,只贡献画面)
  - 字幕:底部英文叠加

模式:B-roll 全屏切换,音频为主轴(英文克隆配音全程播放),
画面在主播口型和素材镜头之间按字幕语义切换。

输入:
  - 主视频(lipsync_92.mp4, LatentSync 产物,含英文音轨)
  - 匹配方案(match_plan.json)
  - 字幕(subtitle.json)

输出:
  - HyperFrames project(hf_project/index.html + assets/)
  - 渲染成片 broll_final.mp4

用法:
  python pipeline/build_pip_composition.py
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.services.gpt_client import gpt_client

# 输入
# 主视频: 92 号画面 + 用户提供的克隆英文音轨(xiaren flac),33.42s
# - 视频画面拉慢 1.1485 倍匹配音轨时长
# - 音轨用 92 原音色克隆英文翻译配音(原速)
# - 场景素材以 B-roll 全屏切换覆盖
LIPSYNC_VIDEO = r"D:\video-skill\outputs\broll_main_flac2_20260815_180854\main_with_en_audio.mp4"
MATCH_PLAN = r"D:\video-skill\outputs\pip_match_20260815_162629\match_plan.json"
SUBTITLE_JSON = r"D:\video-skill\outputs\voice_clone_20260815_160224\subtitle.json"
# 字幕时间轴放缩系数(原 27.65s → 现 33.42s,1.1485/0.95=1.2089)
TIME_SCALE = 1.1485 / 0.95  # ≈1.2089

# HyperFrames CLI
HF_CLI = _PROJECT_ROOT / "hyperframes" / "packages" / "cli" / "dist" / "cli.js"

# 输出
TS = datetime.now().strftime("%Y%m%d_%H%M%S")
OUT_DIR = _PROJECT_ROOT / "outputs" / f"broll_compose_{TS}"
PROJECT_DIR = OUT_DIR / "hf_project"
ASSETS_DIR = PROJECT_DIR / "assets"
OUTPUT_MP4 = OUT_DIR / "broll_final.mp4"


async def decide_scene_or_speaker(segments: list, matches: list) -> list:
    """让 GPT 判断每段是'留主播(人)'还是'切素材(场景)'。

    返回:[{seg_index, mode: "speaker"|"scene", reason}]
      - speaker: 该段留主播口型画面(主播在说话/品牌主张/邀请/反问)
      - scene: 该段切到场景素材(展示产品/工厂/工艺/库存/供货)
    """
    seg_desc = "\n".join(
        f"[S{i}] {s['start']:.1f}-{s['end']:.1f}s ({s['end']-s['start']:.1f}s) 中文:{s['source_text']} 英文:{s['target_text']}"
        for i, s in enumerate(segments)
    )
    match_desc = "\n".join(
        f"[S{m['seg_index']}] 素材:{os.path.basename(m['material_file'])} 理由:{m['reason']}"
        for m in matches
    )

    messages = [
        {
            "role": "system",
            "content": "你是短视频剪辑导演。任务:为每段字幕判断画面是'留主播'还是'切场景素材'。\n\n判断原则:\n- 留主播(speaker):主播在表达品牌主张、邀请、反问、个人观点、对观众说话、强调自身实力或承诺(如'我不允许还有人不知道''想看看我们源头工厂的实力吗''我们一直有大量备货''全产业链都在我手心里''是原料经验独家开模打磨')\n- 切场景(scene):字幕语义需要展示具体产品/工厂/工艺/库存画面(如'不锈钢刀叉勺源头工厂''从五金到包装''绝不因为交期导致断货')\n\n关键要求(主播画面要多,避免频繁切换):\n1. 10段中至少6段留主播,其余切场景(主播始终全屏,素材以小窗叠加)\n2. 切场景的段不要连续超过2段,中间应回到主播\n3. 相邻段如果都是切场景,优先选择同一个素材(连续展示同一镜头),避免一秒内切多次\n4. 短段(<1.5秒)倾向合并到相邻段(与相邻段同mode),不要单独切一次画面\n5. 一秒内不允许切换两次画面"
        },
        {
            "role": "user",
            "content": f"字幕段:\n{seg_desc}\n\n素材匹配候选:\n{match_desc}\n\n请输出 JSON: {{\"plan\":[{{\"seg_index\":0,\"mode\":\"speaker\",\"reason\":\"...\"}},...]}}\n每段必须有一个条目,mode 只能是 speaker 或 scene。注意S3只有1秒,应与相邻段同mode避免单秒切换。"
        }
    ]
    result = await gpt_client.chat_completion(messages, temperature=0.3, max_tokens=800)
    raw = result["choices"][0]["message"]["content"].strip()
    import re
    m = re.search(r'\{[\s\S]*\}', raw)
    if m:
        return json.loads(m.group())["plan"]
    raise RuntimeError(f"无法解析 GPT 判断结果: {raw[:200]}")


def merge_short_and_adjacent(switch_plan: list, segments: list, matches: list) -> list:
    """合并相邻同mode段 + 短段合并到相邻段,避免1秒内多次切换。

    返回:[{start, end, mode, material_file, seg_indices:[...], reason}]

    规则:
    1. 短段(<1.5s)的mode和素材沿用前段(无前段则后段)
    2. 相邻段同mode + 同素材 → 合并为一个长clip
    3. 合并后短段(<1.5s)继续向后合并
    """
    match_by_seg = {m["seg_index"]: m for m in matches}
    plan = [dict(p) for p in switch_plan]
    # subtitle.json 无 index 字段,按列表顺序作 index
    seg_by_idx = {i: dict(s, index=i) for i, s in enumerate(segments)}

    # 1) 短段(<1.5s)的mode和material_file沿用前段(无前段则后段)
    for i, p in enumerate(plan):
        seg_idx = p["seg_index"]
        seg = seg_by_idx[seg_idx]
        dur = seg["end"] - seg["start"]
        if dur < 1.5:
            if i > 0:
                prev_p = plan[i-1]
                prev_seg_idx = prev_p["seg_index"]
                prev_match = match_by_seg.get(prev_seg_idx)
                p["mode"] = prev_p["mode"]
                if prev_match:
                    # 同时沿用前段素材,确保能和前段合并
                    p["_material_file_override"] = prev_match["material_file"]
                p["reason"] = f"短段({dur:.1f}s)沿用前段mode+素材: {p['reason']}"
            elif i < len(plan) - 1:
                next_p = plan[i+1]
                next_seg_idx = next_p["seg_index"]
                next_match = match_by_seg.get(next_seg_idx)
                p["mode"] = next_p["mode"]
                if next_match:
                    p["_material_file_override"] = next_match["material_file"]
                p["reason"] = f"短段({dur:.1f}s)沿用后段mode+素材: {p['reason']}"

    # 2) 合并相邻同mode + 同素材段
    def get_material(p):
        if "_material_file_override" in p:
            return p["_material_file_override"]
        m = match_by_seg.get(p["seg_index"])
        return m["material_file"] if m else None

    merged = []
    for p in plan:
        seg_idx = p["seg_index"]
        seg = seg_by_idx[seg_idx]
        material_file = get_material(p)
        mode = p["mode"]

        if merged:
            prev = merged[-1]
            prev_seg_idx = prev["seg_indices"][-1]
            prev_seg = seg_by_idx[prev_seg_idx]

            if (prev["mode"] == mode
                and prev["material_file"] == material_file
                and abs(seg["start"] - prev_seg["end"]) < 0.01):
                prev["seg_indices"].append(seg_idx)
                prev["end"] = seg["end"]
                prev["reason"] = prev["reason"] + " + " + p["reason"]
                continue

        merged.append({
            "start": seg["start"],
            "end": seg["end"],
            "mode": mode,
            "material_file": material_file,
            "seg_indices": [seg_idx],
            "reason": p["reason"],
        })

    # 3) 合并后短段(<1.5s)继续与后段合并(沿用后段mode+素材)
    final = []
    for c in merged:
        if final and (final[-1]["end"] - final[-1]["start"]) < 1.5:
            prev = final[-1]
            # 沿用后段(c)的mode和素材
            prev["mode"] = c["mode"]
            prev["material_file"] = c["material_file"]
            prev["end"] = c["end"]
            prev["seg_indices"].extend(c["seg_indices"])
            prev["reason"] = prev["reason"] + " +→" + c["reason"]
            continue
        final.append(c)

    return final


def prepare_assets(main_video: str, matches: list) -> dict:
    """复制主视频 + 所有用到的素材到 assets/,返回映射 {原路径: 相对路径}。"""
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    # 主视频
    main_dst = ASSETS_DIR / "main.mp4"
    shutil.copy2(main_video, main_dst)
    print(f"  ✓ 主视频 -> assets/main.mp4")

    # 素材:按 material_file 去重
    mapping = {}
    for i, m in enumerate(matches):
        src = m["material_file"]
        if src in mapping:
            continue
        dst = ASSETS_DIR / f"material_{i:02d}.mp4"
        shutil.copy2(src, dst)
        mapping[src] = f"assets/{dst.name}"
        print(f"  ✓ {os.path.basename(src)} -> {dst.name}")

    return {"main": "assets/main.mp4", "materials": mapping}


def build_composition_html(main_asset: str, material_mapping: dict,
                           matches: list, segments: list,
                           merged_clips: list) -> str:
    """生成 HyperFrames composition HTML。

    结构(B-roll 全屏切换 + 音频为主轴 + 中英双字幕):
      #stage (1080×1920 竖屏)
        ├─ 主视频 (track 0, 全屏, 含英文音轨,全程贡献音频)
        ├─ B-roll 素材层 (track 1, 全屏覆盖,无声,按合并后时段切换画面)
        └─ 字幕层 (track 2, 底部,中英双行黄色,逐段精确配对)

    merged_clips: [{start, end, mode, material_file, seg_indices, reason}]
      - mode == "speaker" → 留主播(不加 B-roll)
      - mode == "scene"   → 切到对应素材(B-roll 全屏覆盖)
    """
    # 计算总时长
    total_dur = max(float(matches[-1]["end"]), 27.4)

    # B-roll video 标签(只对 mode=scene 的合并段)
    # 主播→场景交界处:素材 data-start 推迟 0.4s,给主播动作收尾
    ACTION_TAIL = 0.4
    broll_clips = []
    for i, clip in enumerate(merged_clips):
        if clip["mode"] != "scene":
            broll_clips.append(
                f'    <!-- {clip["start"]:.1f}-{clip["end"]:.1f}s mode=speaker 留主播: {clip["reason"]} -->'
            )
            continue
        start = float(clip["start"])
        end = float(clip["end"])
        # 前一段是 speaker → 素材出现推迟 0.4s(让主播动作收尾)
        if i > 0 and merged_clips[i-1]["mode"] == "speaker":
            start = start + ACTION_TAIL
        dur = end - start
        asset = material_mapping[clip["material_file"]]
        broll_clips.append(
            f'''    <!-- {start:.1f}-{end:.1f}s mode=scene: {clip["reason"]} -->
    <video class="broll-clip"
           src="{asset}"
           data-start="{start:.3f}"
           data-duration="{dur:.3f}"
           data-track-index="1"
           data-media-start="0"
           muted
           playsinline
           loop></video>'''
        )

    # 字幕已删除(用户要求)

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=1080, height=1920" />
  <title>B-roll 切换成片 - 92号视频英文翻译</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: #000;
      color: white;
      font-family: system-ui, -apple-system, sans-serif;
    }}
    #stage {{
      position: relative;
      width: 1080px;
      height: 1920px;
      overflow: hidden;
      background: #000;
    }}
    /* 主视频: 全屏覆盖(底层) */
    .main-video {{
      position: absolute;
      top: 0; left: 0;
      width: 100%; height: 100%;
      object-fit: cover;
      z-index: 1;
    }}
    /* B-roll 素材: 全屏覆盖(盖住主播,但音频由 main 贡献) */
    .broll-clip {{
      position: absolute;
      top: 0; left: 0;
      width: 100%; height: 100%;
      object-fit: cover;
      z-index: 2;
      background: #000;
    }}
  </style>
</head>
<body>
  <div id="stage"
       data-composition-id="broll-final"
       data-start="0"
       data-width="1080"
       data-height="1920"
       data-duration="{total_dur:.3f}"
       data-fps="30">

    <!-- 主视频: LatentSync 英文版主播口型(含对齐英文音轨,全程贡献音频) -->
    <video class="main-video"
           src="{main_asset}"
           data-start="0"
           data-duration="{total_dur:.3f}"
           data-track-index="0"
           data-media-start="0"
           playsinline></video>

{chr(10).join(broll_clips)}

  </div>
</body>
</html>"""
    return html


def render_with_hyperframes(project_dir: str, composition_id: str,
                            output_path: str) -> bool:
    """调 HyperFrames CLI 渲染。"""
    cmd = [
        "node", str(HF_CLI), "render",
        project_dir,
        "--composition", "index.html",
        "--output", output_path,
        "--format", "mp4",
        "--fps", "30",
        "--resolution", "portrait",
        "--browser-timeout", "180",
        "--player-ready-timeout", "120000",
    ]
    print(f"\n[Step4] 调 HyperFrames CLI 渲染...")
    print(f"  cmd: {' '.join(cmd[:6])} ...")

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", timeout=1800
        )
        if proc.returncode == 0 and os.path.exists(output_path):
            print(f"  ✓ 渲染成功")
            # 打印最后部分输出
            tail = (proc.stdout or "")[-500:]
            if tail:
                print(f"  日志尾部: {tail}")
            return True
        else:
            print(f"  ✗ 渲染失败 (exit={proc.returncode})")
            print(f"  stdout: {(proc.stdout or '')[-800:]}")
            print(f"  stderr: {(proc.stderr or '')[-800:]}")
            return False
    except subprocess.TimeoutExpired:
        print(f"  ✗ 渲染超时")
        return False


def main():
    print(f"\n{'='*60}")
    print(f"HyperFrames B-roll 切换成片编排(人 vs 场景)")
    print(f"{'='*60}")
    print(f"  主视频: {LIPSYNC_VIDEO}")
    print(f"  匹配方案: {MATCH_PLAN}")
    print(f"  字幕: {SUBTITLE_JSON}")
    print(f"  输出目录: {OUT_DIR}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)

    # 加载匹配方案 + 字幕
    plan = json.loads(Path(MATCH_PLAN).read_text(encoding="utf-8"))
    matches = plan["matches"]
    segments = json.loads(Path(SUBTITLE_JSON).read_text(encoding="utf-8"))
    # 按 TIME_SCALE 放缩字幕段和匹配段时间轴,对齐慢速主视频
    for s in segments:
        s["start"] = round(s["start"] * TIME_SCALE, 3)
        s["end"] = round(s["end"] * TIME_SCALE, 3)
    for m in matches:
        m["start"] = round(m["start"] * TIME_SCALE, 3)
        m["end"] = round(m["end"] * TIME_SCALE, 3)
    print(f"\n[Step1] 加载匹配方案: {len(matches)} 段匹配, {len(segments)} 段字幕")

    # GPT 判断每段是留主播还是切场景
    print(f"\n[Step2] GPT 判断人 vs 场景切换...")
    switch_plan = asyncio.run(decide_scene_or_speaker(segments, matches))
    for p in switch_plan:
        mode_label = "留主播" if p["mode"] == "speaker" else "切场景"
        print(f"  S{p['seg_index']}: {mode_label} - {p['reason']}")

    # 合并相邻同mode段 + 短段,避免1秒内多次切换
    print(f"\n[Step2.5] 合并相邻同mode段(避免1秒内多次切换)...")
    merged_clips = merge_short_and_adjacent(switch_plan, segments, matches)
    for c in merged_clips:
        mode_label = "留主播" if c["mode"] == "speaker" else "切场景"
        asset_name = os.path.basename(c["material_file"]) if c["material_file"] else "-"
        print(f"  {c['start']:.1f}-{c['end']:.1f}s ({c['end']-c['start']:.1f}s) {mode_label} [{asset_name}] S{c['seg_indices']}")
    print(f"  → 合并后 {len(merged_clips)} 段(原 {len(switch_plan)} 段)")

    # 复制 assets
    print(f"\n[Step3] 复制素材到 assets/")
    asset_map = prepare_assets(LIPSYNC_VIDEO, matches)

    # 生成 composition HTML
    print(f"\n[Step4] 生成 composition HTML...")
    html = build_composition_html(asset_map["main"], asset_map["materials"],
                                   matches, segments, merged_clips)
    html_path = PROJECT_DIR / "index.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"  ✓ {html_path}")

    # 渲染
    success = render_with_hyperframes(str(PROJECT_DIR), "broll-final", str(OUTPUT_MP4))

    if success:
        size_mb = os.path.getsize(OUTPUT_MP4) / (1024 * 1024)
        print(f"\n{'='*60}")
        print(f"✓ B-roll 切换成片完成")
        print(f"  产物: {OUTPUT_MP4}")
        print(f"  大小: {size_mb:.2f} MB")
        print(f"{'='*60}")
    else:
        print(f"\n✗ 渲染失败,请检查 HyperFrames CLI 输出")
        sys.exit(1)


if __name__ == "__main__":
    main()
