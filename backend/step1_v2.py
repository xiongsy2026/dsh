"""
Step1 v2: 严格执行优化提示词的视频解析
- 使用 backend/优化提示词.md 作为系统提示词
- 输入：视频抽帧拼图 + 产品图
- 输出：【N—N秒】结构化提示词正文 + 时间轴JSON
- 严格按"15秒 3—4镜"规则（不再生成13镜）
"""
import cv2
import base64
import json
import re
import asyncio
import httpx
from pathlib import Path
from PIL import Image, ImageDraw

# ── 路径配置 ──
VIDEO = Path(r"C:\Users\Administrator\Desktop\测试视频\裤子\video(83).mp4")
PRODUCT_IMAGE = Path(r"d:\video-skill\canvas-history-22426276.png")
SYSTEM_PROMPT_FILE = Path(r"d:\video-skill\backend\优化提示词.md")

API = "https://codex.theyouyi.site/v1"
KEY = __import__("os").environ.get("STEP1_PROBE_KEY", "")   # 密钥不许入库（曾硬编码泄露，已轮换）
MODEL = "gpt-5.4-mini"

OUT_DIR = Path(r"d:\video-skill\outputs\step1_v2")
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ── 1. 均匀抽帧（每2秒一帧，避免过密） ──
def extract_uniform(video_path: Path, interval_sec: float = 2.0):
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
    next_time = 0.0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        t = frame_idx / fps
        if t >= next_time:
            path = frames_dir / f"frame_{len(saved):03d}_{t:.1f}s.jpg"
            cv2.imwrite(str(path), frame)
            saved.append({"idx": len(saved), "time": round(t, 1), "path": str(path)})
            next_time += interval_sec
        frame_idx += 1
    cap.release()
    print(f"均匀抽帧: {len(saved)} 帧 (每{interval_sec}秒一帧)")
    return saved, duration


# ── 2. 拼图 ──
def make_contact_sheet(frames, cols=4, thumb_h=400, name="contact_sheet.jpg"):
    """把帧拼成一张大图，每帧左上角标注时间"""
    imgs = []
    for f in frames:
        img = Image.open(f["path"])
        ratio = thumb_h / img.height
        img = img.resize((int(img.width * ratio), thumb_h))
        imgs.append(img)

    w, h = imgs[0].size
    rows = (len(imgs) + cols - 1) // cols
    gap = 6
    canvas = Image.new("RGB", (w * cols + gap * (cols + 1), h * rows + gap * (rows + 1)), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    for i, img in enumerate(imgs):
        x = gap + (i % cols) * (w + gap)
        y = gap + (i // cols) * (h + gap)
        canvas.paste(img, (x, y))
        # 时间标签
        draw.rectangle([x, y, x + 80, y + 24], fill=(220, 30, 30))
        draw.text((x + 4, y + 4), f"{frames[i]['time']:.1f}s", fill="white")

    path = OUT_DIR / name
    canvas.save(str(path), quality=88)
    print(f"拼图: {canvas.size} → {path}")
    return path


# ── 3. 调用模型（系统提示词 + 视频 + 产品图） ──
async def analyze_with_model(contact_sheet_path: Path, product_img_path: Path, system_prompt: str, duration: float):
    """严格按优化提示词输出结构化提示词正文 + 时间轴"""
    video_b64 = base64.b64encode(contact_sheet_path.read_bytes()).decode()
    product_b64 = base64.b64encode(product_img_path.read_bytes()).decode()

    user_prompt = f"""我上传了一段电商带货视频（时长约{duration:.1f}秒）和产品参考图。

视频抽帧拼图：每帧左上角标注了对应的时间点（红色标签），按时间从左到右、从上到下排列。
产品参考图：用户提供的商品图，用于锁定商品身份。

请严格按你的系统提示词规则执行：
1. 识别视频中的产品身份，与产品参考图对齐
2. 严格按"15秒 3—4镜"规则切分（不要切成10+镜）
3. 输出完整的【全局设定】【素材引用】【0—N秒】...【连续性约束】【声音设计】【负向约束】结构化提示词正文
4. 每个分镜必须包含全部维度：场景/人物/产品/画面内容/人物动作/产品动作/景别/构图/运镜/口播/声音语气/面部微表情
5. 输出完结构化提示词正文后，最后另起一行输出"===TIMELINE==="，再输出FFmpeg时间轴JSON数组（每个Shot一个代表帧时间点）

时间轴JSON格式：
===TIMELINE===
[
  {{"shot": 1, "time": 0.5}},
  {{"shot": 2, "time": 3.5}},
  {{"shot": 3, "time": 8.0}},
  {{"shot": 4, "time": 12.0}}
]
"""

    async with httpx.AsyncClient(timeout=300.0) as client:
        r = await client.post(
            f"{API}/chat/completions",
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{video_b64}"}},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{product_b64}"}},
                        ],
                    },
                ],
                "max_tokens": 6000,
                "temperature": 0.7,
            },
        )
        if r.status_code != 200:
            print(f"API错误: {r.status_code} {r.text[:500]}")
            raise Exception(f"API返回{r.status_code}")
        data = r.json()
        text = data["choices"][0]["message"]["content"]
        return text


def split_prompt_and_timeline(text: str):
    """分离结构化提示词正文和时间轴JSON"""
    # 找 ===TIMELINE=== 分隔符
    m = re.search(r"===TIMELINE===\s*", text)
    if m:
        prompt_part = text[:m.start()].strip()
        timeline_part = text[m.end():].strip()
    else:
        # 兼容：找最后的JSON数组
        prompt_part = text
        timeline_part = ""

    # 从timeline_part提取JSON
    timeline = []
    if timeline_part:
        m = re.search(r"\[[\s\S]*\]", timeline_part)
        if m:
            try:
                timeline = json.loads(m.group(0))
            except:
                pass

    return prompt_part, timeline


def save_outputs(prompt_text: str, timeline: list, duration: float):
    """保存输出"""
    # 1. 结构化提示词正文
    prompt_path = OUT_DIR / "storyboard_prompt.md"
    prompt_path.write_text(prompt_text, encoding="utf-8")
    print(f"\n✅ 结构化提示词: {prompt_path} ({len(prompt_text)}字符)")

    # 2. 时间轴JSON
    timeline_path = OUT_DIR / "timeline.json"
    with open(timeline_path, "w", encoding="utf-8") as f:
        json.dump({"duration": duration, "timeline": timeline}, f, ensure_ascii=False, indent=2)
    print(f"✅ 时间轴: {timeline_path} ({len(timeline)}个Shot)")

    # 3. 解析分镜为JSON（便于后续使用）
    shots = parse_shots_from_prompt(prompt_text)
    shots_path = OUT_DIR / "storyboard.json"
    with open(shots_path, "w", encoding="utf-8") as f:
        json.dump({"shots": shots, "timeline": timeline, "duration": duration}, f, ensure_ascii=False, indent=2)
    print(f"✅ 分镜JSON: {shots_path} ({len(shots)}个Shot)")

    return shots


def parse_shots_from_prompt(prompt_text: str):
    """从结构化提示词正文中解析分镜为JSON"""
    shots = []
    # 找所有【N—N秒】或【N-N秒】标签
    pattern = r"【(\d+(?:\.\d+)?)\s*[—\-]\s*(\d+(?:\.\d+)?)秒】\s*([^\n]+(?:\n(??!【)[^\n]+)*)"
    matches = re.findall(pattern, prompt_text)

    for idx, (start, end, content) in enumerate(matches, 1):
        shot = {
            "shot": idx,
            "时间": f"{start}—{end}秒",
            "原始内容": content.strip(),
        }
        shots.append(shot)
    return shots


async def main():
    print("=" * 60)
    print("Step1 v2: 严格按优化提示词解析视频")
    print("=" * 60)

    # 0. 加载系统提示词
    system_prompt = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8")
    print(f"系统提示词: {len(system_prompt)}字符")

    # 1. 抽帧
    frames, duration = extract_uniform(VIDEO, interval_sec=2.0)

    # 2. 拼图
    contact_sheet = make_contact_sheet(frames, cols=4, thumb_h=400)

    # 3. 调用模型
    print(f"\n调用 {MODEL} (视频拼图 + 产品图 + 系统提示词)...")
    raw_text = await analyze_with_model(contact_sheet, PRODUCT_IMAGE, system_prompt, duration)
    print(f"模型输出: {len(raw_text)}字符")

    # 4. 分离提示词正文和时间轴
    prompt_text, timeline = split_prompt_and_timeline(raw_text)
    print(f"\n提示词正文: {len(prompt_text)}字符")
    print(f"时间轴: {len(timeline)}个Shot")

    # 5. 保存
    shots = save_outputs(prompt_text, timeline, duration)

    # 6. 打印预览
    print("\n" + "=" * 60)
    print("结构化提示词正文预览（前1500字）:")
    print("=" * 60)
    print(prompt_text[:1500])
    if len(prompt_text) > 1500:
        print(f"\n... (共{len(prompt_text)}字符)")

    print("\n" + "=" * 60)
    print(f"时间轴: {timeline}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
