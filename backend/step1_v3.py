"""
Step1 v3: 严格按 PRD 流程一执行
- 使用 爆款复刻.skill.prd 流程一的原始提示词
- 输入：视频抽帧拼图 + 产品图
- 输出：【一】解析依据【二】分镜脚本JSON【三】FFmpeg时间轴JSON【四】拼图规则
- 同时识别客户上传的@产品，输出替换了新产品的分镜json
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

API = "https://codex.theyouyi.site/v1"
KEY = __import__("os").environ.get("STEP1_PROBE_KEY", "")   # 密钥不许入库（曾硬编码泄露，已轮换）
MODEL = "gpt-5.4-mini"

OUT_DIR = Path(r"d:\video-skill\outputs\step1_v3")
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ── 1. 均匀抽帧（PRD未指定间隔，使用1.5秒） ──
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
def make_contact_sheet(frames, cols=4, thumb_h=360, name="contact_sheet.jpg"):
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
        draw.rectangle([x, y, x + 80, y + 24], fill=(220, 30, 30))
        draw.text((x + 4, y + 4), f"{frames[i]['time']:.1f}s", fill="white")

    path = OUT_DIR / name
    canvas.save(str(path), quality=85)
    print(f"拼图: {canvas.size} → {path}")
    return path


# ── 3. PRD 流程一原始提示词（原样使用） ──
SYSTEM_PROMPT = """1:1爆款复刻提示词标准(15秒万能版)流程说明


流程一：

1.1、输入原视频提取分镜和提供ffmepeg进行精准抽帧（gemin3）:输入原视频+产品=新分镜脚本+分镜抽帧点json
角色设定：
你是工业级视频结构解析引擎（Video Structural Parsing Engine），只负责"视频还原 + 可执行时间轴 + 抽帧指令生成"。

你的输出必须可直接用于：
- 结构化数据存储（纯JSON）
- FFmpeg抽帧执行
- 分镜图拼接与视频复刻

禁止任何剧情推测、画面脑补或文字润色等创作行为。

====================
🚨 核心原则
====================
1. 100%还原真实视频：只描述"发生了什么"，不描述"为什么发生"或"感觉如何"。
2. 所有分镜切分必须基于"真实动作锚点"或"口播语义转折"。
3. 严格遵守JSON格式与指定字段，禁止自行增删字段或输出冗长说明。

====================
🧠 Step 1：视频解析与锚点提取
====================
在生成分镜前，后台自动判断并提取：
1. 是否有口播（有/无）
2. 切分依据（有口播看语义/无口播看镜头与动作）
3. 动作锚点（如：出现物品、举起、展示、递近、强调手势等，每个Shot必须基于一个锚点）

====================
🎬 Step 2：分镜脚本输出（核心层）
====================
去除冗余信息，输出严格的JSON数组。每个分镜（Shot）必须包含以下字段，且内容需高度概括（不超过10个字/字段，口播除外）：
- "shot": 编号 (整数)
- "场景": (如：室内 / 室外 / 街拍)
- "人物": (核心特征描述，如：白裙女性 / 仅双手)
- "画面内容": (纯客观事实，如：举起银色包包)
- "人物动作": (核心动作提取，如：食指强调 / 抚摸皮革)
- "景别": (特写 / 近景 / 中景 / 全景 / 远景)
- "音效": (特定环境音或音效，没有则填"无")
- "口播": (原句，听不清填"听不清"，无则填"无")
微动作与表情（需标注：眼神轨迹、头部微晃、重心转移等自然律动）
声音情感与语气（需详细标注：重音词汇、语速变化、停顿点、情绪形容词）
-
还需要包含统一的内容：视觉风格、拍摄参数、音效设计，动作设计和声音与情绪工程（包含：情感TTS韵律提取、真人呼吸感建模、禁画外音）！

同时识别客户上传的@产品，输出替换了新产品的分镜json
每个shot的输出维度示例：

[
  {
    "shot": 1,
    "场景": "室内",
    "人物": "白裙女性",
    "产品": "银色包包",
    "画面内容": "居中站立提包",
    "人物动作": "做"嘘"及指引手势",
    "产品动作": "单手自然提包，包位于身体前侧，作为待展示主体",
    "景别": "中景",
    "构图": "中心构图",
    "运镜": "固定机位",
    "音效": "无",
    "口播": "姐妹们今天挖到宝了",
    "声音语气": {
      "语速": "中速偏快",
      "语调": "开场上扬，像私下分享",
      "重音": ""挖到宝了"重读",
      "停顿": ""姐妹们"后可有轻微停顿",
      "情绪": "兴奋、神秘、分享感",
      "呼吸感": "自然轻呼吸，不要播音腔"
    },
    "面部微表情": {
      "眼神方向": "直视镜头",
      "眉眼变化": "轻微挑眉，带一点吸引注意力的神态",
      "嘴角状态": "自然带笑",
      "表情说明": "先神秘，再转入分享感"
    }
  }
]

====================
⚙️ Step 3：FFmpeg执行层（独立时间轴）
====================
提取每个Shot最具代表性的一帧，生成用于 `ffmpeg -ss` 的精确时间点（秒），输出为独立JSON数组：
[
  { "shot": 1, "time": 0.83 },
  { "shot": 2, "time": 2.10 }
]

====================
🖼 Step 4：分镜图拼接规则
====================
基于Shot总数输出拼图布局建议（不生成图片）：
- 1~5个Shot → 单行排列或2x3
- 6~8个Shot → 4×2
- 9~12个Shot → 3×4 / 4×3
规则：只允许说明Shot编号的排列方式，禁止其他解释。

====================
📦 输出结构要求
====================
必须严格按照以下顺序输出结果：
【一】解析依据（简述口播情况与主要切分锚点，50字以内）
【二】15秒分镜脚本（输出Step 2的JSON代码块）
【三】FFmpeg时间轴（输出Step 3的JSON代码块）
【四】分镜拼图规则（简述Step 4布局规则）
"""


# ── 4. 调用模型 ──
async def analyze_with_model(contact_sheet_path: Path, product_img_path: Path, duration: float):
    """按PRD流程一调用模型"""
    video_b64 = base64.b64encode(contact_sheet_path.read_bytes()).decode()
    product_b64 = base64.b64encode(product_img_path.read_bytes()).decode()

    user_prompt = f"""我上传了一段电商带货视频（时长约{duration:.1f}秒）和产品参考图。

第1张图：视频抽帧拼图，每帧左上角红色标签标注了对应的时间点（秒），按时间从左到右、从上到下排列。
第2张图：客户上传的产品参考图（@产品），用于产品识别和替换。

请严格按你的系统提示词（PRD流程一）执行：
1. 100%还原真实视频内容
2. 按真实动作锚点或口播语义转折切分分镜
3. 识别客户上传的@产品，输出替换了新产品的分镜json
4. 严格按【一】【二】【三】【四】顺序输出
"""

    max_retries = 4
    for attempt in range(1, max_retries + 1):
        try:
            print(f"  尝试 {attempt}/{max_retries}...")
            async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0)) as client:
                r = await client.post(
                    f"{API}/chat/completions",
                    headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
                    json={
                        "model": MODEL,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
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
                        "temperature": 0.5,
                    },
                )
                if r.status_code == 504:
                    print(f"  504 超时，重试...")
                    await asyncio.sleep(5 * attempt)
                    continue
                if r.status_code != 200:
                    print(f"API错误: {r.status_code} {r.text[:500]}")
                    raise Exception(f"API返回{r.status_code}")
                data = r.json()
                text = data["choices"][0]["message"]["content"]
                return text
        except httpx.ReadTimeout:
            print(f"  读取超时，重试...")
            await asyncio.sleep(5 * attempt)
        except httpx.ConnectError as e:
            print(f"  连接错误: {e}，重试...")
            await asyncio.sleep(5 * attempt)
    raise Exception(f"重试{max_retries}次后仍失败")


def parse_output(text: str):
    """解析模型输出，提取4部分"""
    result = {
        "解析依据": "",
        "分镜脚本JSON": "",
        "FFmpeg时间轴JSON": "",
        "拼图规则": "",
        "shots": [],
        "timeline": [],
    }

    # 提取【一】解析依据
    m = re.search(r"【一】[^\n]*\n(.*?)(?=【二】|$)", text, re.DOTALL)
    if m:
        result["解析依据"] = m.group(1).strip()

    # 提取【二】分镜脚本JSON
    m = re.search(r"【二】[^\n]*\n(.*?)(?=【三】|$)", text, re.DOTALL)
    if m:
        result["分镜脚本JSON"] = m.group(1).strip()
        # 从中提取JSON
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", m.group(1))
        if json_match:
            try:
                result["shots"] = json.loads(json_match.group(1))
            except:
                pass
        else:
            json_match = re.search(r"\[[\s\S]*\]", m.group(1))
            if json_match:
                try:
                    result["shots"] = json.loads(json_match.group(0))
                except:
                    pass

    # 提取【三】FFmpeg时间轴JSON
    m = re.search(r"【三】[^\n]*\n(.*?)(?=【四】|$)", text, re.DOTALL)
    if m:
        result["FFmpeg时间轴JSON"] = m.group(1).strip()
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", m.group(1))
        if json_match:
            try:
                result["timeline"] = json.loads(json_match.group(1))
            except:
                pass
        else:
            json_match = re.search(r"\[[\s\S]*\]", m.group(1))
            if json_match:
                try:
                    result["timeline"] = json.loads(json_match.group(0))
                except:
                    pass

    # 提取【四】拼图规则
    m = re.search(r"【四】[^\n]*\n(.*?)$", text, re.DOTALL)
    if m:
        result["拼图规则"] = m.group(1).strip()

    return result


def save_outputs(raw_text: str, parsed: dict, duration: float):
    """保存输出"""
    # 1. 原始模型输出
    raw_path = OUT_DIR / "raw_output.md"
    raw_path.write_text(raw_text, encoding="utf-8")
    print(f"\n✅ 原始输出: {raw_path} ({len(raw_text)}字符)")

    # 2. 分镜JSON
    if parsed["shots"]:
        shots_path = OUT_DIR / "storyboard.json"
        with open(shots_path, "w", encoding="utf-8") as f:
            json.dump({"shots": parsed["shots"], "timeline": parsed["timeline"], "duration": duration}, f, ensure_ascii=False, indent=2)
        print(f"✅ 分镜JSON: {shots_path} ({len(parsed['shots'])}个Shot)")

    # 3. 时间轴JSON
    if parsed["timeline"]:
        timeline_path = OUT_DIR / "timeline.json"
        with open(timeline_path, "w", encoding="utf-8") as f:
            json.dump({"duration": duration, "timeline": parsed["timeline"]}, f, ensure_ascii=False, indent=2)
        print(f"✅ 时间轴: {timeline_path} ({len(parsed['timeline'])}个Shot)")


async def main():
    print("=" * 60)
    print("Step1 v3: 严格按PRD流程一执行")
    print("=" * 60)

    # 1. 抽帧
    frames, duration = extract_uniform(VIDEO, interval_sec=1.5)

    # 2. 拼图
    contact_sheet = make_contact_sheet(frames, cols=4, thumb_h=360)

    # 3. 调用模型
    print(f"\n调用 {MODEL} (视频拼图 + 产品图 + PRD流程一提示词)...")
    raw_text = await analyze_with_model(contact_sheet, PRODUCT_IMAGE, duration)
    print(f"模型输出: {len(raw_text)}字符")

    # 4. 解析输出
    parsed = parse_output(raw_text)
    print(f"\n解析依据: {parsed['解析依据'][:100]}")
    print(f"分镜数: {len(parsed['shots'])}")
    print(f"时间轴: {len(parsed['timeline'])}个Shot")

    # 5. 保存
    save_outputs(raw_text, parsed, duration)

    # 6. 打印预览
    print("\n" + "=" * 60)
    print("原始输出预览（前2000字）:")
    print("=" * 60)
    print(raw_text[:2000])
    if len(raw_text) > 2000:
        print(f"\n... (共{len(raw_text)}字符)")


if __name__ == "__main__":
    asyncio.run(main())
