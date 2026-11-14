"""
画中画素材语义匹配
─────────────────────────────────────────────────
1. 抽素材视频代表帧
2. GPT-4V 识别素材内容(打标签)
3. 按英文字幕段语义匹配素材到时段
4. 输出匹配方案(JSON),供后续 HyperFrames 编排使用

用法:
  python pipeline/match_pip_materials.py
"""
import asyncio
import base64
import json
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.services.gpt_client import gpt_client

MATERIAL_DIR = r"C:\Users\Administrator\Desktop\视频翻译\画中画素材"
SUBTITLE_JSON = r"D:\video-skill\outputs\voice_clone_20260815_160224\subtitle.json"


async def describe_image(img_path: str) -> str:
    """用 GPT-4V 描述图片内容,返回简短中文标签。"""
    with open(img_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    messages = [
        {
            "role": "system",
            "content": "你是产品视频素材标注员。给定一帧图片,用简短中文描述(<=40 字)产品/场景/动作,用于后续与营销话术做语义匹配。"
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "请简短描述这帧画面:产品是什么(刀/叉/勺/工厂/仓库/包装/发货/原料/工艺等)、场景、动作。只输出关键标签,不要废话。"
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
                }
            ]
        }
    ]

    result = await gpt_client.chat_completion(messages, temperature=0.2, max_tokens=200)
    return result["choices"][0]["message"]["content"].strip()


async def match_materials_to_segments(materials: list, segments: list) -> list:
    """让 GPT 按英文字幕语义给每段选最合适的素材。

    输出: [{seg_index, material_index, reason}, ...]
    """
    mat_desc = "\n".join(f"[M{i}] {m['file']} ({m['duration']:.1f}s): {m['label']}"
                         for i, m in enumerate(materials))
    seg_desc = "\n".join(f"[S{i}] {s['start']:.1f}-{s['end']:.1f}s 中文:{s['source_text']} 英文:{s['target_text']}"
                          for i, s in enumerate(segments))

    messages = [
        {
            "role": "system",
            "content": "你是视频剪辑师。任务:为每段英文字幕选一个最匹配的素材视频,作为该时段主视频上叠加的画中画。匹配依据:字幕语义(中文+英文)与素材内容(产品/场景/动作)的关联度。原则:1)产品展示类字幕配产品特写素材;2)工厂/产能类字幕配工厂/库存素材;3)工艺类字幕配工艺/包装素材。"
        },
        {
            "role": "user",
            "content": f"素材库:\n{mat_desc}\n\n字幕段:\n{seg_desc}\n\n请输出 JSON,格式:\n{{\"matches\":[{{\"seg_index\":0,\"material_index\":0,\"reason\":\"...\"}},...]}}\n每段字幕必须匹配一个素材,可重复使用。"
        }
    ]
    result = await gpt_client.chat_completion(messages, temperature=0.3, max_tokens=800)
    raw = result["choices"][0]["message"]["content"].strip()
    # 提取 JSON
    import re
    m = re.search(r'\{[\s\S]*\}', raw)
    if m:
        return json.loads(m.group())["matches"]
    raise RuntimeError(f"无法解析 GPT 匹配结果: {raw[:200]}")


async def main_async():
    # Step 1: 抽每个素材代表帧
    ts = "20260815_162629"  # 复用已抽的帧目录
    frame_dir = _PROJECT_ROOT / "outputs" / f"pip_match_{ts}"
    if not frame_dir.exists():
        raise RuntimeError(f"代表帧目录不存在: {frame_dir}")

    # Step 2: 加载素材元数据
    materials = []
    for f in sorted(Path(MATERIAL_DIR).glob("video(*).mp4")):
        mid = frame_dir / f"{f.stem}_mid.jpg"
        if not mid.exists():
            continue
        # 读时长
        import subprocess
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(f)],
            capture_output=True, text=True
        )
        dur = float(p.stdout.strip())
        materials.append({"file": str(f), "name": f.name, "duration": dur, "frame": str(mid)})

    print(f"\n[Step1] 加载素材: {len(materials)} 个")
    for m in materials:
        print(f"  {m['name']} ({m['duration']:.1f}s)")

    # Step 3: GPT-4V 识别每个素材
    print(f"\n[Step2] GPT-4V 识别素材内容...")
    for m in materials:
        m["label"] = await describe_image(m["frame"])
        print(f"  {m['name']}: {m['label']}")

    # Step 4: 加载英文字幕
    segments = json.loads(Path(SUBTITLE_JSON).read_text(encoding="utf-8"))
    print(f"\n[Step3] 加载字幕: {len(segments)} 段")

    # Step 5: GPT 语义匹配
    print(f"\n[Step4] GPT 语义匹配素材到字幕段...")
    matches = await match_materials_to_segments(materials, segments)
    for x in matches:
        si = x["seg_index"]; mi = x["material_index"]
        print(f"  [S{si}] {segments[si]['source_text'][:20]} -> {materials[mi]['name']}: {x['reason']}")

    # 输出方案
    plan = {
        "materials": [{"name": m["name"], "file": m["file"], "duration": m["duration"], "label": m["label"]} for m in materials],
        "segments": [{"index": i, "start": s["start"], "end": s["end"],
                      "source_text": s["source_text"], "target_text": s["target_text"]} for i, s in enumerate(segments)],
        "matches": [{"seg_index": x["seg_index"], "material_index": x["material_index"],
                     "reason": x["reason"],
                     "start": segments[x["seg_index"]]["start"],
                     "end": segments[x["seg_index"]]["end"],
                     "material_file": materials[x["material_index"]]["file"],
                     "material_duration": materials[x["material_index"]]["duration"]} for x in matches],
    }
    out = frame_dir / "match_plan.json"
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✓ 匹配方案: {out}")
    return str(out)


if __name__ == "__main__":
    asyncio.run(main_async())
