"""Step1: 从视频均匀抽帧 → Codex多模态分析 → 生成分镜JSON+时间轴"""
import cv2
import base64
import json
import asyncio
import httpx
from pathlib import Path
from PIL import Image

VIDEO = Path(r"d:\video-skill\13429015810167142.mp4")
API = "https://codex.theyouyi.site/v1"
KEY = __import__("os").environ.get("STEP1_PROBE_KEY", "")   # 密钥不许入库（曾硬编码泄露，已轮换）
MODEL = "gpt-5.4-mini"
OUT_DIR = Path("outputs/step1_rerun")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── 1. 均匀抽帧 ──
def extract_uniform(video_path: Path, interval_sec: float = 1.5):
    """每隔 interval_sec 秒抽一帧"""
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total / fps
    print(f"视频: fps={fps:.1f}, 总帧={total}, 时长={duration:.1f}s")

    frames_dir = OUT_DIR / "uniform_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    frame_idx = 0
    saved = []
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        t = frame_idx / fps
        if abs(t - len(saved) * interval_sec) < 1.0 / fps:
            path = frames_dir / f"frame_{len(saved):03d}_{t:.1f}s.jpg"
            cv2.imwrite(str(path), frame)
            saved.append({"idx": len(saved), "time": round(t, 1), "path": str(path)})
        frame_idx += 1
    cap.release()
    print(f"均匀抽帧: {len(saved)} 帧")
    return saved


# ── 2. 拼图 + 多模态分析 ──
def make_contact_sheet(frames, cols=4, thumb_h=360, name="contact_sheet.jpg"):
    """把帧拼成一张大图"""
    imgs = []
    for f in frames:
        img = Image.open(f["path"])
        ratio = thumb_h / img.height
        img = img.resize((int(img.width * ratio), thumb_h))
        imgs.append(img)

    w, h = imgs[0].size
    rows = (len(imgs) + cols - 1) // cols
    canvas = Image.new("RGB", (w * cols, h * rows), (255, 255, 255))
    from PIL import ImageDraw
    draw = ImageDraw.Draw(canvas)
    for i, img in enumerate(imgs):
        x = (i % cols) * w
        y = (i // cols) * h
        canvas.paste(img, (x, y))
        draw.text((x + 4, y + 4), f"{frames[i]['time']:.1f}s", fill="red")

    path = OUT_DIR / name
    canvas.save(str(path), quality=75)
    print(f"拼图: {canvas.size} → {path}")
    return path


async def analyze_with_codex(contact_sheet_path: Path):
    """用Codex多模态分析拼图，生成分镜JSON"""
    img_b64 = base64.b64encode(contact_sheet_path.read_bytes()).decode()

    prompt = """你是一位专业的短视频分镜分析师。请仔细观察这张视频抽帧拼图（每帧左上角标注了时间点），完成以下任务：

1. 识别视频中的人物、场景、产品
2. 按画面内容变化切分分镜（Shot），每个Shot是画面内容相对连续的片段
3. 为每个Shot输出完整的分镜脚本
4. 输出时间轴（每个Shot的起始时间）

请严格基于画面实际内容输出，不要编造任何画面中不存在的内容。

输出格式（JSON）：
{
  "shots": [
    {
      "shot": 1,
      "场景": "...",
      "人物": "...",
      "产品": "...",
      "画面内容": "...",
      "人物动作": "...",
      "产品动作": "...",
      "景别": "...",
      "构图": "...",
      "运镜": "...",
      "音效": "...",
      "口播": "...",
      "声音语气": {"语速":"...", "语调":"...", "重音":"...", "停顿":"...", "情绪":"..."},
      "面部微表情": "...",
      "字幕": "..."
    }
  ],
  "timeline": [
    {"shot": 1, "time": 0.0},
    {"shot": 2, "time": ...}
  ]
}

只输出JSON，不要输出其他内容。"""

    async with httpx.AsyncClient(timeout=180.0) as client:
        r = await client.post(
            f"{API}/chat/completions",
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
            json={
                "model": MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                        ],
                    }
                ],
                "max_tokens": 4000,
            },
        )
        if r.status_code != 200:
            print(f"API错误: {r.status_code} {r.text[:500]}")
            raise Exception(f"API返回{r.status_code}")
        data = r.json()
        text = data["choices"][0]["message"]["content"]
        return text


def parse_json_from_text(text: str) -> dict:
    """从模型输出中提取JSON"""
    # 尝试直接解析
    try:
        return json.loads(text)
    except:
        pass
    # 尝试提取 ```json ... ```
    import re
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        try:
            return json.loads(m.group(1))
        except:
            pass
    # 尝试找 { ... }
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except:
            pass
    raise ValueError(f"无法从输出中提取JSON:\n{text[:500]}")


async def main():
    # 1. 抽帧
    frames = extract_uniform(VIDEO, interval_sec=1.5)

    # 2. 分批拼图（每批9帧，缩到240px高度）
    batch_size = 9
    batches = []
    for i in range(0, len(frames), batch_size):
        batch = frames[i:i + batch_size]
        sheet = make_contact_sheet(batch, cols=3, thumb_h=240, name=f"sheet_{i//batch_size}.jpg")
        batches.append(sheet)

    # 3. 逐批分析
    all_results = []
    for idx, sheet in enumerate(batches):
        print(f"\n分析第 {idx+1}/{len(batches)} 批...")
        try:
            raw_text = await analyze_with_codex(sheet)
            print(f"模型输出 (前500字): {raw_text[:500]}")
            result = parse_json_from_text(raw_text)
            all_results.append(result)
            print(f"  → {len(result.get('shots', []))} 个Shot")
        except Exception as e:
            print(f"  分析失败: {e}")
            all_results.append(None)

    # 4. 合并结果
    all_shots = []
    all_timeline = []
    for r in all_results:
        if r is None:
            continue
        all_shots.extend(r.get("shots", []))
        all_timeline.extend(r.get("timeline", []))

    # 重新编号
    for i, s in enumerate(all_shots):
        s["shot"] = i + 1
    for i, t in enumerate(all_timeline):
        t["shot"] = i + 1

    merged = {
        "shots": all_shots,
        "timeline": sorted(all_timeline, key=lambda x: x.get("time", 0)),
    }

    print(f"\n合并结果: {len(all_shots)} 个Shot, {len(all_timeline)} 个时间点")

    # 5. 保存
    out_path = OUT_DIR / "storyboard.json"
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"保存: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
