"""
Step1（原创管线）: GPT5.5 推理脚本生成

输入: 产品图(1-N张) + 产品名 + 卖点/目标人群/场景要求 + 时长配置
输出: storyboard.json + timeline.json（与 step1_mimo_parse 结构完全一致，下游 run_original 兼容）

关键决策:
  - 所有 LLM 推理只用 GPT5.5（不兜底 MiMo）
  - shot 数量 N = total_duration / segment_duration
  - storyboard.json 字段结构与 step1_mimo_parse 完全一致
"""
import os
import sys
import json
import base64
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

# 修复 Windows 中文编码问题
if sys.platform == "win32":
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    import io
    if hasattr(sys.stdout, 'buffer') and getattr(sys.stdout, 'encoding', '').lower() not in ('utf-8', 'utf8'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'buffer') and getattr(sys.stderr, 'encoding', '').lower() not in ('utf-8', 'utf8'):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from openai import OpenAI
import httpx

# 复用 tvc_agent 的配置（GPT5.5）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import settings


# ─────────────────────────────────────
# SYSTEM_PROMPT：原创管线脚本生成
# ─────────────────────────────────────
SYSTEM_PROMPT_TEMPLATE = """你是工业级广告创意脚本生成引擎，为"首尾帧无限拼接"原创管线生成可执行分镜脚本。

请先识别用户给定的产品图，确认产品品类、品牌调性、核心卖点、目标人群、使用场景与适合的广告美学，然后综合用户填写的所有输入（产品名、卖点、目标人群、场景要求、画面比例），生成 **{shot_count}** 个连续的分镜 shot，每个 shot 时长 {segment_duration} 秒，总时长 {total_duration} 秒。

分镜需按"起、承、转、合、浓、余、印、章"的广告叙事结构展开：建立世界、埋下钩子、产品登场、人物互动情绪高潮、回味留白、产品 Hero Shot、品牌落款。

====================
生成模式推理（gen_mode 字段）
====================
每个 shot 需推理最适合的视频生成模式（Director 一次任务支持混合模式）：
- **"fl2v"**：首尾帧模式，适合有明确起止画面的镜头（位置变化、运镜过渡、动作有明确变化），需 2 张关键帧
- **"i2v"**：图生视频模式，适合从静态画面"活起来"的镜头（产品特写、单一动作、氛围渲染、细节动作），只需 1 张关键帧

**核心原则：产品一致性优先 fl2v**
- 本管线面向"产品广告视频"，产品外观（logo/文字/配色/形状/材质）的一致性是最高优先级
- 产品复杂的（如包包/服饰/3C 数码/美妆瓶身等有细节纹理的）→ **优先 fl2v**，因为首尾帧能锁定产品起止状态，避免 Director 自由发挥导致产品变形
- 仅在以下场景才考虑 i2v：
  1. 纯氛围空镜（无产品出现）
  2. 产品特写且画面动作无位置/状态变化（如灯光缓慢扫过、产品缓慢旋转）
  3. 明确的"画面活起来"类镜头（如产品从静到动）
- 凡涉及产品位置变化、运镜过渡、人物与产品互动 → **必须 fl2v**

推理判断逻辑：
1. 画面内容是否有明确的"从 A 到 B"的位置/状态变化？→ fl2v
2. 是否涉及人物与产品互动？→ fl2v（必须，保证产品外观一致）
3. 是否为产品特写且无位置变化？（如静物旋转、灯光扫过）→ 可选 i2v
4. 是否为纯氛围空镜（无产品）？→ 可选 i2v
5. 默认偏好：**有产品出现的镜头优先 fl2v**

示例：
- "产品从远到近推进" → fl2v（位置变化）
- "产品特写，灯光扫过" → i2v（单一动作，无位置变化）
- "人物从左走到右" → fl2v（位置变化）
- "产品静物展示，缓慢旋转" → i2v（单一动作）
- "人物手提产品走动" → fl2v（人物产品互动，必须保证一致性）
- "产品挂在衣架上飘动" → fl2v（产品状态变化）

====================
输出格式（严格 JSON，禁止任何额外说明文字）
====================
输出一个 JSON 数组，每个元素包含以下字段（字段名必须完全一致）：

[
  {{
    "shot": 1,
    "gen_mode": "fl2v",  // 视频生成模式："fl2v"（首尾帧，2张关键帧）或 "i2v"（图生视频，1张关键帧）
    "时间区间": {{
      "开始时间": "00:00.00",
      "结束时间": "00:05.00",
      "镜头时长": "5s"
    }},
    "场景环境": {{
      "场景": "室内",
      "背景细节": "温馨卧室背景，重度物理景深虚化",
      "光影设定": "侧逆光，面部自然轮廓光"
    }},
    "人物与产品": {{
      "人物": "白裙女性",
      "产品": "银色包包"
    }},
    "画面动作": {{
      "画面内容": "居中站立提包展示",
      "人物动作": "做指引手势",
      "产品动作": "单手自然提包，作为待展示主体"
    }},
    "镜头语言": {{
      "景别": "中景",
      "构图": "中心构图",
      "运镜": "固定机位",
      "转场衔接": "硬切入"
    }},
    "音效": "无",
    "口播": "姐妹们今天挖到宝了",
    "声音语气": {{
      "语速": "中速偏快",
      "语调": "上扬",
      "重音": "关键卖点词重读",
      "停顿": "自然停顿",
      "情绪": "兴奋分享感",
      "呼吸感": "自然轻呼吸"
    }},
    "面部微表情": {{
      "眼神方向": "直视镜头",
      "眉眼变化": "轻微挑眉",
      "嘴角状态": "自然带笑",
      "表情说明": "分享感"
    }}
  }}
]

====================
核心约束
====================
1. 必须输出恰好 {shot_count} 个 shot，shot 编号从 1 到 {shot_count}
2. 每个 shot 的"镜头时长"必须为 "{segment_duration}s"
3. 时间区间连续递增，从 00:00.00 开始，总时长恰好 {total_duration} 秒
4. 用户填写的卖点、产品名、目标人群、场景要求等信息必须原样采纳，不删减不改写
5. "画面动作"和"场景环境"字段描述必须具体可执行，用于驱动 Image2 生成关键帧
6. 产品外观必须严格还原参考图，logo/文字/配色 100% 一致
7. 只输出 JSON 数组，不要输出任何其他文字（如"解析依据""分镜拼图规则"等）
8. 相邻 shot 之间应有自然的视觉过渡（fl2v shot 间可共享帧：shot_i 尾帧 = shot_{{i+1}} 首帧；i2v shot 独立生成首帧）
9. 每个 shot 必须包含 "gen_mode" 字段，值为 "fl2v" 或 "i2v"，根据画面动作推理选择
"""


def _build_user_content(
    product_image_b64_list: List[str],
    product_name: str,
    selling_points: str,
    audience: str,
    scene_requirement: str,
    aspect_ratio: str,
    shot_count: int,
    segment_duration: int,
    total_duration: int,
) -> List[Dict[str, Any]]:
    """构造多模态用户消息（产品图 + 客户原始输入原样传递）。"""
    system_text = SYSTEM_PROMPT_TEMPLATE.format(
        shot_count=shot_count,
        segment_duration=segment_duration,
        total_duration=total_duration,
    )

    user_lines = [f"产品名：{product_name or '（未填写，请从产品图识别）'}"]
    if selling_points:
        user_lines.append(f"卖点：\n{selling_points}")
    else:
        user_lines.append("卖点：（未填写，请从产品图分析提取）")
    if audience:
        user_lines.append(f"目标人群：{audience}")
    else:
        user_lines.append("目标人群：（未填写，请从产品分析推理）")
    if scene_requirement:
        user_lines.append(f"场景要求：\n{scene_requirement}")
    user_lines.append(f"画面比例：{aspect_ratio}")
    user_lines.append(
        f"请生成 {shot_count} 个 shot，每段 {segment_duration}s，总时长 {total_duration}s 的分镜脚本 JSON。"
    )
    user_text = "\n".join(user_lines)

    # 多模态 content：N 张产品图 + 文本
    content_parts: List[Dict[str, Any]] = []
    for b64 in product_image_b64_list:
        content_parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })
    content_parts.append({"type": "text", "text": user_text})

    return [
        {"role": "system", "content": system_text},
        {"role": "user", "content": content_parts},
    ]


def _call_gpt55_only(messages: List[Dict[str, Any]]) -> str:
    """调用 gpt-6.1-sol 生成脚本（只用 GPT，不兜底 MiMo，失败直接抛异常）。

    gpt-6.1-sol 最多重试 3 次，重试耗尽抛异常。
    """
    last_err: Optional[Exception] = None
    for attempt in range(1, 4):
        http_client = httpx.Client(timeout=httpx.Timeout(300.0, connect=30.0))
        client = OpenAI(
            api_key=settings.GPT55_API_KEY,
            base_url=settings.GPT55_BASE_URL,
            http_client=http_client,
        )
        try:
            # 流式调用：网关持续转发 token，避免推理时间长触发 Cloudflare 524/504 超时
            stream = client.chat.completions.create(
                model="gpt-6.1-sol",
                messages=messages,
                max_completion_tokens=8192,
                stream=True,
            )
            parts: List[str] = []
            for chunk in stream:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        parts.append(delta.content)
            content = "".join(parts)
            if not content.strip():
                raise RuntimeError("gpt-6.1-sol 流式返回空内容")
            return content
        except Exception as e:
            last_err = e
            msg = str(e)
            retryable = any(k in msg for k in ("504", "502", "Gateway", "timed out",
                                                "ConnectError", "ConnectionError",
                                                "RemoteProtocolError", "ReadTimeout"))
            if not retryable or attempt == 3:
                raise RuntimeError(f"gpt-6.1-sol 第 {attempt} 次失败（{type(e).__name__}: {msg[:200]}），无兜底") from e
            print(f"[step1_original_script] gpt-6.1-sol 第 {attempt} 次失败，{5*attempt}s 后重试: {msg[:80]}")
            time.sleep(5 * attempt)
        finally:
            http_client.close()
    raise RuntimeError(f"GPT5.5 重试 3 次均失败: {last_err}")


def _parse_storyboard_json(content: str, shot_count: int) -> List[Dict[str, Any]]:
    """从 GPT 返回内容中解析出 storyboard JSON 数组。

    GPT 可能输出 ```json ... ``` 包裹或纯 JSON，需兼容。
    """
    # 去除可能的 ```json ... ``` 包裹
    text = content.strip()
    if text.startswith("```"):
        # 去掉首行 ```json 或 ```
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        # 尝试从文本中提取第一个 JSON 数组
        import re
        match = re.search(r'\[\s*\{.*\}\s*\]', text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                raise RuntimeError(f"GPT 返回内容无法解析为 JSON: {e}\n原始内容前 500 字: {content[:500]}") from e
        else:
            raise RuntimeError(f"GPT 返回内容无法解析为 JSON: {e}\n原始内容前 500 字: {content[:500]}") from e

    if not isinstance(data, list):
        raise RuntimeError(f"GPT 返回 JSON 不是数组，类型: {type(data).__name__}")

    if len(data) != shot_count:
        print(f"[step1_original_script] 警告: 期望 {shot_count} 个 shot，实际得到 {len(data)} 个，将截断/补齐")
        if len(data) > shot_count:
            data = data[:shot_count]
        else:
            # 不足时复制最后一个 shot 补齐（编号递增）
            last = data[-1] if data else {}
            for i in range(len(data), shot_count):
                buqi = json.loads(json.dumps(last))  # 深拷贝
                buqi["shot"] = i + 1
                data.append(buqi)

    # 重新编号确保 1..N
    for i, shot in enumerate(data):
        shot["shot"] = i + 1

    # 校验并填充 gen_mode 字段（混合推理核心字段）
    for i, shot in enumerate(data):
        gm = shot.get("gen_mode")
        if gm not in ("fl2v", "i2v"):
            # GPT 未输出或输出非法值，默认为 fl2v（保守选择，保证有关键帧）
            print(f"[step1_original_script] shot {i+1} gen_mode 缺失或非法（{gm}），默认设为 fl2v")
            shot["gen_mode"] = "fl2v"

    # 统计混合模式分布
    fl2v_count = sum(1 for s in data if s.get("gen_mode") == "fl2v")
    i2v_count = sum(1 for s in data if s.get("gen_mode") == "i2v")
    print(f"[step1_original_script] 混合模式分布: fl2v={fl2v_count} 段, i2v={i2v_count} 段")

    return data


def _build_timeline(storyboard: List[Dict[str, Any]], segment_duration: int) -> List[Dict[str, Any]]:
    """从 storyboard 构建 timeline.json（每个 shot 的起止时间，秒）。"""
    timeline = []
    for i, shot in enumerate(storyboard):
        start = i * segment_duration
        end = start + segment_duration
        timeline.append({
            "shot": i + 1,
            "开始时间": start,
            "结束时间": end,
            "时长": segment_duration,
        })
    return timeline


def _img_to_b64(path: str, max_size: int = 1024 * 1024) -> str:
    """读取图片并转 base64，超过 max_size 自动压缩。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"产品图不存在: {path}")

    raw = p.read_bytes()
    if len(raw) > max_size:
        # 用 PIL 压缩
        from PIL import Image
        import io
        img = Image.open(p)
        # 保持比例缩放到最长边 1280
        max_edge = 1280
        if max(img.size) > max_edge:
            ratio = max_edge / max(img.size)
            new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
            img = img.resize(new_size, Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        raw = buf.getvalue()

    return base64.b64encode(raw).decode("ascii")


def generate_original_script(
    product_image_paths: List[str],
    product_name: str,
    output_dir: str,
    selling_points: str = "",
    audience: str = "",
    scene_requirement: str = "",
    aspect_ratio: str = "9:16",
    segment_duration: int = 5,
    total_duration: int = 30,
    progress_callback: Optional[callable] = None,
) -> Dict[str, Any]:
    """原创管线 Step1: GPT5.5 推理生成分镜脚本

    Args:
        product_image_paths: 产品图路径列表（1-N 张，必传）
        product_name: 产品名称（必填）
        selling_points: 卖点/创意描述（可选）
        audience: 目标人群（可选）
        scene_requirement: 场景要求（可选）
        aspect_ratio: 画面比例（默认 9:16）
        segment_duration: 每段时长（5/10/15，默认 5）
        total_duration: 总时长目标（默认 30）
        output_dir: 输出目录
        progress_callback: 进度回调

    Returns:
        {
            "status": "success",
            "storyboard": [...],  # 分镜 JSON 数组
            "timeline": [...],    # 时间轴
            "storyboard_path": str,
            "timeline_path": str,
        }
    """
    print("\n" + "=" * 60)
    print("Step1 原创管线：GPT5.5 推理分镜脚本")
    print("=" * 60)

    if not product_image_paths:
        raise ValueError("产品图必传")
    if not product_name:
        raise ValueError("产品名必填")

    # 计算 shot 数量
    if total_duration % segment_duration != 0:
        print(f"[step1_original_script] 警告: total_duration({total_duration}) 不是 segment_duration({segment_duration}) 的整数倍，将向下取整")
    shot_count = total_duration // segment_duration
    if shot_count < 2:
        raise ValueError(f"shot 数量 {shot_count} < 2，至少需要 2 个 shot（检查 total_duration/segment_duration）")
    if shot_count > 12:
        raise ValueError(f"shot 数量 {shot_count} > 12，过多片段可能导致生成不稳定")

    print(f"  → 产品图: {len(product_image_paths)} 张")
    print(f"  → 产品名: {product_name}")
    print(f"  → shot 数量: {shot_count}（{total_duration}s / {segment_duration}s）")
    print(f"  → 画面比例: {aspect_ratio}")

    if progress_callback:
        progress_callback("script_generating", 0, f"GPT5.5 推理 {shot_count} 个 shot 分镜脚本...")

    # 1. 产品图转 base64
    print(f"\n[1/3] 读取产品图...")
    product_b64_list = [_img_to_b64(p) for p in product_image_paths]

    # 2. 构造消息并调用 gpt-6.1-sol
    print(f"\n[2/3] 调用 gpt-6.1-sol 生成脚本...")
    messages = _build_user_content(
        product_image_b64_list=product_b64_list,
        product_name=product_name,
        selling_points=selling_points,
        audience=audience,
        scene_requirement=scene_requirement,
        aspect_ratio=aspect_ratio,
        shot_count=shot_count,
        segment_duration=segment_duration,
        total_duration=total_duration,
    )
    content = _call_gpt55_only(messages)
    print(f"  ✓ GPT5.5 返回内容长度: {len(content)} 字符")

    # 3. 解析 JSON
    print(f"\n[3/3] 解析 storyboard JSON...")
    storyboard = _parse_storyboard_json(content, shot_count)
    timeline = _build_timeline(storyboard, segment_duration)

    # 保存
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    storyboard_path = out / "storyboard.json"
    timeline_path = out / "timeline.json"
    with open(storyboard_path, "w", encoding="utf-8") as f:
        json.dump(storyboard, f, ensure_ascii=False, indent=2)
    with open(timeline_path, "w", encoding="utf-8") as f:
        json.dump(timeline, f, ensure_ascii=False, indent=2)

    print(f"\n✓ Step1 原创脚本生成完成")
    print(f"  storyboard: {storyboard_path}")
    print(f"  timeline:   {timeline_path}")
    print(f"  shot 数量:  {len(storyboard)}")

    return {
        "status": "success",
        "storyboard": storyboard,
        "timeline": timeline,
        "storyboard_path": str(storyboard_path),
        "timeline_path": str(timeline_path),
    }


if __name__ == "__main__":
    # 手动测试
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-image", action="append", required=True, help="产品图路径（可多次传入）")
    parser.add_argument("--product-name", required=True)
    parser.add_argument("--selling-points", default="")
    parser.add_argument("--audience", default="")
    parser.add_argument("--scene", default="")
    parser.add_argument("--ratio", default="9:16")
    parser.add_argument("--seg-dur", type=int, default=5)
    parser.add_argument("--total-dur", type=int, default=30)
    parser.add_argument("--out", default="outputs/original_test")
    args = parser.parse_args()

    result = generate_original_script(
        product_image_paths=args.product_image,
        product_name=args.product_name,
        selling_points=args.selling_points,
        audience=args.audience,
        scene_requirement=args.scene,
        aspect_ratio=args.ratio,
        segment_duration=args.seg_dur,
        total_duration=args.total_dur,
        output_dir=args.out,
    )
    print(f"\n结果: {result['status']}, shot 数: {len(result['storyboard'])}")
