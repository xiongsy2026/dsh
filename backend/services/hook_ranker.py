"""钩子精排改写服务（调 MiMo）

把 retrieve() 输出的 top-5 候选钩子 + 用户具体需求发给 MiMo，执行：
1. 选 1-2 条最佳候选
2. 改写为短剧带货前 3 秒（或前 15 秒）的 Seedance Prompt
3. 输出"引用源 → 改写后"对照
4. 给出后续衔接指令（衔接 short-drama-director）

入口函数：
- rank_and_rewrite(user_input: dict, candidates: list[dict]) -> dict
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from backend.services.gpt_client import GPT55Client

# MiMo 精排系统提示词模板（与 references/example-match.md 一致）
RANKER_SYSTEM_PROMPT = """你是好莱坞大片钩子精排改写引擎。任务：从候选钩子中选 1-2 条最佳，改写为短剧带货前 3 秒的 Seedance Prompt。

【用户需求】
{user_input_block}

【候选钩子】（按向量相似度排序）
{candidates_block}

【精排规则】
1. 从候选中选 1-2 条最佳，说明选择理由（1-2 行）
2. 改写成符合以下要求的 Seedance Prompt：
   - 纯中文，结构化
   - 前 3 秒钩子（产品介入前的悬念）
   - 物理动作精确，时长精确到 0.5 秒
   - 角色视线锁定产品或痛点部位
   - 不出现品牌名，只描述功能
3. 输出"引用源 → 改写后"对照
4. 给出后续衔接指令（衔接 short-drama-director 的 Step 2 角色圣经）

【输出格式】严格 JSON（无任何额外文字、无 Markdown 代码块）
{{
  "selected": [
    {{
      "id": "hook_016",
      "title": "《黑天鹅》开场——镜中",
      "reason": "镜面反射适配自我审视眼下细纹的痛点建立"
    }}
  ],
  "rewritten_prompt": "【0-3秒】...（完整 Seedance 钩子 Prompt）",
  "follow_up": {{
    "character_identity": "林娜，32岁，都市女白领，广告公司资深文案",
    "hook_end_state": "右手悬停眼下细纹处，瞳孔放大",
    "next_shot_start": "第4秒开始：右手从眼下收回，转向洗手台",
    "rhythm_points": [
      "第4秒：动作切换点",
      "第5秒：产品介入点",
      "第8秒：使用中段",
      "第12秒：效果反馈点"
    ]
  }},
  "adjustment_notes": "1-3 行核心改进点"
}}
"""


def _format_user_input(user_input: dict) -> str:
    """把用户输入字典格式化为可读文本块。"""
    lines = []
    for k, v in user_input.items():
        lines.append(f"- {k}: {v}")
    return "\n".join(lines)


def _format_candidates(candidates: list[dict]) -> str:
    """把候选钩子格式化为编号文本块。"""
    lines = []
    for i, c in enumerate(candidates, 1):
        lines.append(
            f"{i}. {c.get('title', '')}（{c.get('hook_pattern', '')}_"
            f"{c.get('opening_structure', '')}）"
        )
        lines.append(f"   第一帧：{c.get('first_frame', '')}")
        lines.append(f"   前3秒：{c.get('first_3s', '')}")
        if c.get("similarity") is not None:
            lines.append(f"   相似度：{c['similarity']:.3f}")
        lines.append("")
    return "\n".join(lines)


def _build_messages(user_input: dict, candidates: list[dict]) -> list[dict]:
    """构造 LLM 请求 messages。"""
    system_prompt = RANKER_SYSTEM_PROMPT.format(
        user_input_block=_format_user_input(user_input),
        candidates_block=_format_candidates(candidates),
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "请输出精排改写 JSON。"},
    ]


async def rank_and_rewrite(
    user_input: dict,
    candidates: list[dict],
    model: str = "mimo-large",
    temperature: float = 0.7,
    max_tokens: int = 4000,
) -> dict[str, Any]:
    """调用 MiMo 执行精排 + 改写。

    Args:
        user_input: 用户输入字典（4 维度 + 可选字段）
        candidates: retrieve() 返回的 merged 候选列表
        model: MiMo 模型名（默认 mimo-large）
        temperature: 0.7 平衡创意与稳定
        max_tokens: 4000 足够覆盖改写后 Prompt

    Returns:
        {
            "selected": [...],
            "rewritten_prompt": "...",
            "follow_up": {...},
            "adjustment_notes": "...",
            "raw_response": {...}   # 原始 LLM 响应
        }
    """
    messages = _build_messages(user_input, candidates)
    client = GPT55Client()

    response = await client.chat_completion(
        messages=messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        fallback_models=("gpt-5.6-sol",),
    )

    # 解析 JSON 响应
    try:
        content = response["choices"][0]["message"]["content"]
        # 兼容模型偶尔包裹 ```json
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        result = json.loads(content.strip())
    except (KeyError, json.JSONDecodeError, IndexError) as e:
        return {
            "error": f"LLM 响应解析失败: {e}",
            "raw_content": response.get("choices", [{}])[0]
                           .get("message", {}).get("content", ""),
            "raw_response": response,
        }

    result["raw_response"] = response
    return result


def rank_and_rewrite_sync(
    user_input: dict, candidates: list[dict], **kwargs
) -> dict[str, Any]:
    """同步包装，便于非异步调用方使用。"""
    return asyncio.run(rank_and_rewrite(user_input, candidates, **kwargs))


if __name__ == "__main__":
    # 命令行测试（需先跑过 hook_retriever.py）
    from backend.services.hook_retriever import retrieve

    test_input = {
        "product_category": "美妆",
        "target_audience": "都市女白领",
        "scene_type": "公司",
        "emotional_tone": "紧张",
        "platform": "抖音",
        "duration": 3,
        "supplement": "突显加班熬夜的眼下细纹痛点",
    }
    retrieval = retrieve(test_input, k=5)
    candidates = retrieval["merged"]
    print("候选钩子：")
    for c in candidates:
        print(f"  - {c['title']}（sim={c.get('similarity', 0):.3f}）")

    print("\n调用 MiMo 精排改写...")
    result = rank_and_rewrite_sync(test_input, candidates)
    print(json.dumps(result, ensure_ascii=False, indent=2))
