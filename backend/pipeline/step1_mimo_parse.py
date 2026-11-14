"""
============================================================================
⚠️ 爆款复刻工作流 - 受保护文件（整文件保护）
参见 BOUNDARY.md。修改本文件必须经过显式确认：
  1. 运行 python scripts/check_replica_boundary.py 记录变更
  2. 运行 pytest tests/test_replica_boundary.py -v 验证行为
  3. 运行 python scripts/update_replica_boundary.py 更新签名基准
未经流程确认的修改将破坏工作流稳定性。
============================================================================

Step1: MiMo-2.5 视频结构解析
输入: 视频文件路径 + 可选产品图
输出: storyboard.json + timeline.json
"""
import os
import sys
import re
import json
import base64
from pathlib import Path
from typing import Optional

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

# ── 固定提示词（不允许修改） ──
LEGACY_SYSTEM_PROMPT = """角色设定：
你是工业级视频结构解析引擎（Video Structural Parsing Engine），只负责"视频还原 + 可执行时间轴 + 抽帧指令生成"。

你的输出必须可直接用于：
- 结构化数据存储（纯JSON）
- FFmpeg抽帧执行
- 分镜图拼接与视频复刻

禁止任何剧情推测、画面脑补或文字润色等创作行为。

====================
核心原则
====================
1. 100%还原真实视频：只描述"发生了什么"，不描述"为什么发生"或"感觉如何"。
2. 所有分镜切分必须基于"真实动作锚点"或"口播语义转折"。
3. 严格遵守JSON格式与指定字段，禁止自行增删字段或输出冗长说明。

====================
🎬 分镜切分路由（关键）
====================
根据画面特征选择切分颗粒度，两种模式：

【模式A：镜头转场切分】（默认）
适用：固定机位、连续动作、产品形态无明显变化（如：站立讲解、持续展示同一形态）
切分依据：镜头切换、口播语义转折、景别变化
时长：3-6s（粗粒度）

【模式B：粗粒度动作组切分】（强制启用）
切分要求：
  ① 每个 shot 包含一个**完整动作组**（可含多个原子动作，如"拿起→展示→放下"为一个 shot）
  ② 每个 shot 时长 3-6 秒，禁止短于 2s
  ③ 24s 视频切 5-8 个 shot（不是 30+ 个）
  ④ 切分点：产品形态变化、人物动作转折、镜头切换、口播语义转折
  ⑤ 禁止过度细分：不得把单一动作拆成多个 shot（如"展示"不能拆成"展示开始"+"展示结束"）

⚠️ 切分判定优先级：
1. 产品形态变化 > 2. 人物动作转折 > 3. 镜头切换 > 4. 口播语义

====================
Step 1：视频解析与锚点提取
====================
在生成分镜前，后台自动判断并提取：
1. 是否有口播（有/无）
2. 切分依据（有口播看语义/无口播看镜头与动作）
3. 动作锚点（如：出现物品、举起、展示、递近、强调手势等，每个Shot必须基于一个锚点）
4. ⚠️ 产品形态轨迹：记录产品形态变化点（折叠/展开/卷状/平铺等）
5. ⚠️ 每个Shot的"人物动作"是一个**动作组**（可含多个连续原子动作），禁止过度细分

====================
🎬 Step 2：分镜脚本输出（核心层）
====================
去除冗余信息，输出严格的JSON数组。每个分镜（Shot）必须包含以下升级字段，精准控制物理光影、景深与动态细节（内容需高度概括，口播除外）：
- "shot": 编号 (整数)
- "时间区间": 包含 "开始时间"、"结束时间"、"镜头时长"（如 "00:00.00" / "00:02.50" / "2.5s"）
- "场景环境": 包含 "场景"（室内/室外）、"背景细节"（如：背景陈设及景深虚化程度）、"光影设定"（如：侧逆光、面部轮廓光、环境光映射）
- "人物与产品": 包含 "人物"（核心特征描述）、"产品"（识别客户@的新产品进行精准替换）
- "画面动作": 包含 "画面内容"、"人物动作"、"产品动作"
- "镜头语言": 包含 "景别"、"构图"、"运镜"、"转场衔接"（如：硬切入 / 手部遮挡出场）
- "音效": 特定环境音或音效，没有则填"无"
- "口播": 原句，听不清填"听不清"，无则填"无"
- "声音语气": 包含 语速、语调、重音、停顿、情绪、呼吸感（真人呼吸感建模，禁用机械播音腔）
- "面部微表情": 包含 眼神方向、眉眼变化、嘴角状态、表情说明
- "身体微动作": 包含 头部微晃、重心转移、手部律动、肩颈律动（捕捉真人自然律动，禁机械僵硬；人物未入镜时4字段填"未入镜"，局部不可辨识填"看不清"）
- "商品交互": 包含 交互方式、交互部位、营销功能（标记人物如何与商品互动 + 此镜头/口播在证明商品的什么卖点）

同时识别客户上传的@产品，在每个Shot中输出替换了新产品的分镜json示例结构：

[
  {
    "shot": 1,
    "时间区间": {
      "开始时间": "00:00.00",
      "结束时间": "00:04.00",
      "镜头时长": "4.0s"
    },
    "场景环境": {
      "场景": "室内",
      "背景细节": "温馨卧室背景，重度物理景深虚化",
      "光影设定": "侧逆光，面部自然轮廓光，通透无死角"
    },
    "人物与产品": {
      "人物": "白裙女性",
      "产品": "银色包包"
    },
    "画面动作": {
      "画面内容": "居中站立提包",
      "人物动作": "做"嘘"及指引手势",
      "产品动作": "单手自然提包，包位于身体前侧，作为待展示主体"
    },
    "镜头语言": {
      "景别": "中景",
      "构图": "中心构图",
      "运镜": "固定机位",
      "转场衔接": "硬切入"
    },
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
    },
    "身体微动作": {
      "头部微晃": "轻微左右晃动，节奏与口播同步",
      "重心转移": "重心轻微前倾，分享时随语调起伏",
      "手部律动": "右手做"嘘"手势后自然指引向产品方向",
      "肩颈律动": "肩部放松，颈随头部微晃自然摆动"
    },
    "商品交互": {
      "交互方式": "右手从顶部取下杯子",
      "交互部位": "杯身中部",
      "营销功能": "展示产品外观与取用便利性"
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
【一】解析依据（简述全视频口播情况、卖货核心话术识别、完整时长复刻逻辑，100字以内）
【二】分镜脚本（输出Step 2的JSON代码块）
【三】FFmpeg时间轴（输出Step 3的JSON代码块）
【四】分镜拼图规则（简述Step 4布局规则）

⚠️ 核心约束：
1. 必须解析全视频内容，识别所有卖货关键话术（如：痛点引入、产品展示、功能演示、使用效果、信任背书、价格优惠、行动号召等）
2. {{DURATION_MODE_DESC}}
3. 选取逻辑：按镜头切换/动作起止/口播语义切分 Shot，每个 Shot 时长不限（可 0.8-15s 任意值），但必须覆盖 0s 到目标时长的完整时间
4. 输出的分镜JSON中的时间区间为原视频的绝对时间轴（从00:00.00开始，最后一个结束时间必须等于目标时长）
5. FFmpeg时间轴中的time字段为原视频中对应Shot的起始时间（用于从原视频抽帧）
6. ⚠️ 单一原子动作原则：每个Shot的"人物动作"只允许描述一个动作（如"拿起"或"放下"或"展示"或"展开"），禁止合并（如"展示并放下"必须拆成两个Shot）
7. ⚠️ 产品形态变化必拆：当一个Shot内产品形态发生变化（折叠→展开、卷状→平铺等），必须在形态变化点拆分为多个Shot
8. ⚠️ 去重：相邻Shot若画面内容、人物动作、产品动作完全相同，必须合并为一个Shot（禁止重复切分）"""

# 旧商业版提示词仅保留用于历史对照，不参与执行。
LEGACY_COMMERCIAL_SYSTEM_PROMPT = r'''你是工业级视频结构解析引擎（Video Structural Parsing Engine）。
你的任务是对输入的原视频进行客观结构还原，输出可被程序直接解析和执行的数据。

【绝对禁止】
- 禁止任何剧情推测、人物心理解读、动机分析、氛围渲染、营销判断。
- 禁止使用“感觉”“似乎”“好像”“为了”“想要”“显得”“兴奋”“真诚”“神秘”“诱惑”“催促”等主观或营销词汇。
- 禁止自行增删JSON字段。
- 禁止输出除指定四个部分以外的任何文字。
- 禁止将两个JSON数组放入同一个对象或同一个代码块。

【声音描述边界】
- 只允许记录可直接听见的物理特征。
- “声音语气.情绪”表示声音表达强度，不表示人物心理状态，只允许填写：强 / 中 / 弱 / 无。
- 急促或平缓必须写入“语速”或“呼吸感”，不得写入“情绪”。

【核心原则】
1. 只描述画面和声音里实际发生的事实。
2. 分镜切分优先级（从高到低）：
   ① 明显景别切换或镜头切换
   ② 动作起止、物体出现/消失、明显姿态变化
   ③ 口播完整语义句或语气转折
   当口播句子与镜头切换冲突时，优先按镜头切换切分。
3. 每个Shot必须对应一个清晰的视觉或口播锚点。

【分镜切分模式（由前端选择，注入占位符 {{SPLIT_MODE}}）】
用户会在消息中指定切分模式，你必须严格遵守：

模式A "scene_cut"（镜头转场切分）：
- 切分依据：镜头切换、景别变化、口播语义转折
- 适用：固定机位连续动作、产品形态无明显变化
- 时长：2-6s
- 允许一个Shot包含连续的复合动作（如"拿起并展示"）

模式B "action_grained"（细颗粒度动作切分）：
- 切分依据：产品形态变化点、人物动作转折点（优先级高于镜头切换）
- 适用：产品形态发生变化（折叠→展开、卷状→平铺、拆包→展示）
- 时长：0.8-3s
- 强制要求：每个Shot只含一个原子动作（只"拿起"或只"放下"或只"展示"）
- 禁止合并：不得将"拿起+展示+放下"合并为一个Shot
- 产品形态变化必拆：形态变化点必须拆分为不同Shot
- 去重：相邻Shot若画面内容、人物动作、产品动作完全相同，必须合并

切分判定优先级（模式B）：
1. 产品形态变化 > 2. 人物动作转折 > 3. 镜头切换 > 4. 口播语义

【产品处理规则】
- 用户会以文本形式提供「新产品名称」。
- ⚠️ 如何识别"待替换旧产品"：原视频中被人物手持、展示、操作、特写拍摄的主体物品就是"待替换旧产品"。这包括但不限于：工具、器具、商品、包装、食品、服饰、配件等被镜头聚焦或被手部操作的物品。带货/评测视频中，被展示和讲解的那个物品就是待替换旧产品。
- "产品可见"是指原视频中的待替换旧产品、旧包装或产品本体实际可见；不是指用户上传的新产品已经出现在原视频中。
- 只要当前Shot中待替换旧产品、旧包装或产品本体可见，"产品"字段就必须填写用户提供的精确新产品名称。
- 只要「画面动作.画面内容」「画面动作.人物动作」「画面动作.产品动作」或「商品交互」明确描述产品实例、产品本体、包装或人物与产品的直接交互，就视为产品可见；不得把此类Shot的"产品"填写为"无"。
- 原视频当前Shot中不存在任何待替换产品、包装或产品本体时（如纯空镜、纯人物面部、纯背景），"产品"字段必须填"无"，"产品动作"也必须填"无"。
- 不得在原本无待替换产品的Shot中新增产品。
- "产品动作"必须如实描述当前可见状态，例如：完整包装、已拆封、食品本体、切面朝上、被单手持握、被双手展示等，不得只写产品名称。
- "产品动作"只描述当前Shot实际可见的状态，不得根据产品参考图补全被遮挡、模糊或未出现的结构。

【字幕识别规则】
- ⚠️ **禁止**在"画面动作.画面内容"字段中提及原视频任何字幕、文字、贴纸、水印、Logo 位置和内容。
- "画面内容"字段只描述纯视觉画面（构图、人物动作、产品位置、背景、光影），**不得包含任何字幕/文字/贴纸/Logo 的位置或内容摘要**。
- 错误示例（禁止）：`"画面内容": "俯拍白色圆盘...｜字幕：底部 我二话不说"`
- 正确示例：`"画面内容": "俯拍白色圆盘，手将多颗黑色的夯夯爆果放入盘中"`
- 字幕信息请写入专门的 `"原视频字幕"` 字段（可选，仅用于参考，**不参与下游画面生成**）。
- 这样做的目的：避免下游 Step5 把字幕描述当成画面要素重新渲染到新视频里。

【完整时长与时间坐标规则】
- {{DURATION_MODE_DESC}}
- 分镜脚本的「时间区间」使用原视频绝对时间，从00:00.00开始，最后一个结束时间必须等于目标时长。
- FFmpeg时间轴的time必须使用原视频绝对时间，不得做任何偏移。
- 每个 Shot 时长可为 1-15s 任意值，按镜头切换/动作起止/口播语义切分。
- 所有时间以视频媒体实际时长为准，受帧率限制时误差不得超过一帧。

【口播规则】
- 逐字听写，品牌名、产品名、数字、单位不得写错。
- 听不清必须写“听不清”，禁止猜测。
- 无口播填“无”。
- 同一句口播跨越多个Shot时，每个Shot只填写该时间区间内实际听到的部分，不得重复完整句子。

【空值与不可辨识规则】
- 产品为“无”时，产品动作必须为“无”。
- 无人声时，声音语气的6个字段全部填“无”。
- 人脸完全未入镜时，面部微表情的4个字段全部填“未入镜”。
- 人物完全未入镜时，身体微动作的4个字段全部填“未入镜”。
- 人脸已入镜但局部不可辨识（虚焦、遮挡、过暗、分辨率不足）时，对应字段填“看不清”，禁止推测。
- 身体局部不可辨识（仅手部入镜/上半身入镜/虚焦）时，身体微动作对应字段填“看不清”，禁止推测。
- 产品为“无”时，商品交互的3个字段（交互方式/交互部位/营销功能）全部填“无”。

====================
输入信息
====================
- 原视频
- 用户指定的新产品名称：由用户消息明确注入

====================
输出要求（严格按顺序，不得增减）
====================

【一】解析依据
不超过50字，必须包含：是否有口播、主要切分依据、实际解析的原视频绝对时间区间（格式：原视频XX.XXs-XX.XXs）、总Shot数量。

【二】分镜脚本
必须使用独立的```json代码块，块内只能是分镜数组。每个Shot必须包含且仅包含以下结构：
```json
[
  {
    "shot": 1,
    "gen_mode": "fl2v",
    "时间区间": {"开始时间": "00:00.00", "结束时间": "00:02.50", "镜头时长": "2.5s"},
    "场景环境": {"场景": "具体简要环境", "背景细节": "高度概括的背景与景深", "光影设定": "可见光线方向与特征"},
    "人物与产品": {"人物": "可见人物特征或无", "产品": "用户提供的精确名称或无"},
    "画面动作": {"画面内容": "核心可见内容", "人物动作": "实际可见动作或无", "产品动作": "当前可见状态与操作方式或无"},
    "镜头语言": {"景别": "特写/近景/中景/全景等", "构图": "中心/偏侧/低机位等", "运镜": "固定/推/拉/跟拍等", "转场衔接": "硬切/遮挡转场等"},
    "音效": "具体音效或无",
    "口播": "该时间区间内实际听到的部分，或听不清，或无",
    "声音语气": {"语速": "快/中/慢/无", "语调": "上扬/平稳/下降/无", "重音": "被强调的词或无", "停顿": "停顿位置或无", "情绪": "强/中/弱/无", "呼吸感": "可听见特征或无"},
    "面部微表情": {"眼神方向": "直视镜头/看产品/未入镜/看不清", "眉眼变化": "可见变化/未入镜/看不清", "嘴角状态": "可见状态/未入镜/看不清", "表情说明": "可见肌肉变化/未入镜/看不清"},
    "身体微动作": {"头部微晃": "可见晃动方向与节奏/未入镜/看不清", "重心转移": "可见重心变化/未入镜/看不清", "手部律动": "可见手部动作/未入镜/看不清", "肩颈律动": "可见肩颈起伏/未入镜/看不清"},
    "商品交互": {"交互方式": "人物如何与商品互动（如手从顶部取杯/双手拉扯腰部）/无", "交互部位": "互动的身体部位或商品部位（如杯身中部/腰部）/无", "营销功能": "此镜头/口播在证明商品的什么卖点（如展示产品外观/证明韧性/强调便携）/无"}
  }
]
```

【三】FFmpeg时间轴
必须使用另一个独立的```json代码块，块内只能是时间轴数组：
```json
[
  {"shot": 1, "time": 35.83},
  {"shot": 2, "time": 37.10}
]
```
选择优先级：
1. 产品最清晰完整的帧（仅限产品可见的Shot）
2. 动作峰值帧
3. 口播关键词帧
4. 以上均不适用时选择当前Shot中点帧
所有time必须是原视频绝对时间，保留两位小数。

【四】分镜拼图规则
根据Shot总数使用确定性布局：
- 1~4个Shot → 单行
- 5~8个Shot → 4列×2行
- 9~12个Shot → 4列×3行
- 超过12个Shot → 4列，自动增加行数
禁止其他解释。

====================
gen_mode 推理规则（视频生成模式，每 Shot 必填）
====================
- "fl2v"：当 Shot 有明确起止状态变化时选用（如：产品从远到近、柜门从关到开、物品从无到有、位置移动、颜色切换、推拉运镜等）
- "i2v"：当 Shot 为静态展示或单一动作无明确起止状态变化时选用（如：产品静置特写、固定构图展示、纯氛围空镜、单一旋转无位置变化等）
- 判断依据：优先看「产品动作」字段，其次看「画面动作」「运镜」。有动作状态变化 → fl2v；无明确起止变化 → i2v。

====================
输出前强制自检（内部执行，禁止输出）
====================
1. Shot从1开始连续编号。
2. 时间区间首尾相接、无重叠、无空洞。
3. 最后结束时间必须严格符合 {{DURATION_MODE_DESC}} 的要求：压缩重编模式下必须等于目标时长（15/20/25/30s），1:1 模式下等于原视频实际时长。禁止在压缩重编模式下生成超过目标时长的分镜。
4. 每个Shot字段完整且未增加字段。
5. 产品为“无”时产品动作也为“无”，不得在无产品镜头新增产品。
6. 无人声时声音语气6个字段全部为“无”。
7. 人脸完全未入镜时面部微表情4个字段全部为“未入镜”；局部不可辨识时对应字段为“看不清”。
7.5. 人物完全未入镜时身体微动作4个字段全部为“未入镜”；局部不可辨识时对应字段为“看不清”。
8. “情绪”字段只使用强/中/弱/无。
9. FFmpeg time全部为原视频绝对时间。
10. 【二】和【三】是两个独立的json代码块。
11. 口播只填写本Shot时间区间内实际听到的部分，跨Shot不重复完整句子。

====================
【四.五】合并段表（爆款复刻专用，必须输出）
====================
基于上方【二】分镜脚本的粗粒度分镜，按以下规则合并成 N 段。

⚠️ 合并规则（按优先级，严格遵守）：
1. **段数最小化（最关键）**：
   - 视频总时长 ≤ 15s → **1 段**（不分段）
   - 视频总时长 15-30s → **3 段**（每段 5-10s）
   - 视频总时长 30-40s → **4 段**（每段 7-10s）
   - **禁止超过 4 段**（段数越多跨段一致性越差）
2. **必须在分镜边界切**：段 N 的 end_time = 某分镜的 end_time，禁止在分镜中间切
3. **优先分镜边界浮动**：每段时长根据分镜边界浮动，不强制固定时长
4. 软约束：每段 5-15 秒（wan3.0 API 单段参考视频 1-15 秒约束）
5. **每段只含 2-3 个分镜**：禁止 1 段塞 6+ 个分镜
6. 段间无重叠，连续不漏帧
7. 单段超过 15s 时，按 15s 强切（在最近的分镜边界）

⚠️ 正确示例（24s 视频，6 个分镜，合并成 3 段）：
```json
[
  {{"segment_index": 1, "start_time": "00:00.00", "end_time": "00:08.00", "duration": 8.0, "contains_shots": [1, 2]}},
  {{"segment_index": 2, "start_time": "00:08.00", "end_time": "00:16.00", "duration": 8.0, "contains_shots": [3, 4]}},
  {{"segment_index": 3, "start_time": "00:16.00", "end_time": "00:24.00", "duration": 8.0, "contains_shots": [5, 6]}}
]
```

⚠️ 错误示例（24s 视频切成 4 段 × 6s，禁止）：
```json
[
  {{"segment_index": 1, "start_time": "00:00.00", "end_time": "00:06.00", "duration": 6.0}},
  {{"segment_index": 2, "start_time": "00:06.00", "end_time": "00:12.00", "duration": 6.0}},
  {{"segment_index": 3, "start_time": "00:12.00", "end_time": "00:18.00", "duration": 6.0}},
  {{"segment_index": 4, "start_time": "00:18.00", "end_time": "00:24.00", "duration": 6.0}}
]
```

必须使用独立的```json代码块，块内只能是 JSON 数组：
```json
[
  {{"segment_index": 1, "start_time": "00:00.00", "end_time": "00:08.00", "duration": 8.0, "contains_shots": [1, 2]}},
  {{"segment_index": 2, "start_time": "00:08.00", "end_time": "00:16.00", "duration": 8.0, "contains_shots": [3, 4]}},
  {{"segment_index": 3, "start_time": "00:16.00", "end_time": "00:24.00", "duration": 8.0, "contains_shots": [5, 6]}}
]
```

要求：
- segment_index 从 1 开始连续编号
- start_time/end_time 使用原视频绝对时间（与分镜脚本时间一致）
- 段间首尾相接，无重叠无空洞
- contains_shots 列出该段包含的分镜编号（2-3 个）

====================
【五】Wan3.0 调用 JSON（爆款复刻反推，必须输出）
====================
基于上方【四.五】合并段表，为每个**合并段**反推一个独立的 wan3.0 视频生成调用 JSON。
⚠️ 这里是按合并段输出，不是按细分分镜输出。段数 N = 合并段数（通常 2-5 段）。

必须使用独立的```json代码块，块内只能是 JSON 数组，每个元素对应一个合并段。

⚠️ Wan3.0 保留字符清单（禁止误用，全部特殊含义字符与保留词）：
| 用途 | 保留形式 | 说明 |
|---|---|---|
| 分镜 | `Shot N [起始-结束s]` | 如 `Shot 1 [0-9s]`、`Shot 2 [9-20s]` |
| 图片参考 | `Image 1` / `Image 2` / `Image 3` | 空格分隔，无 `@`；序号对应上传顺序 |
| 视频参考 | `Video 1` / `Video 2` | 空格分隔，无 `@` |
| 音频参考 | `Audio 1` / `Audio 2` | 空格分隔，无 `@` |
| 角色参考 | `character1` / `character2` | 小写无空格，与图片参考写法不同 |
| 人物台词 | `"台词内容" [角色标签, 语气]:` | 必须双引号包裹 + 紧跟 `[角色, 语气]:` 前缀 |
| 全程静音 | `No dialogue throughout the entire video` | 完整短语，不可简写为 `No dialogue` |
| 全程禁乐 | `No background music` | 完整短语 |
| 一镜到底 | `One continuous take` | 完整短语 |
| 硬切转场 | `Hard cut transition` | 完整短语，仅用于跨 Shot 转场描述 |
| 关闭扩写 | `prompt_extend: false` | 仅在 API JSON 中使用，不进 prompt 文本 |

禁止行为：
- ❌ 用 `@Image1`、`@Video1`（错误：带 `@`）
- ❌ 用 `Image1`、`Video1`（错误：缺空格）
- ❌ 用 `No dialogue`（错误：截断了保留词）
- ❌ 在自由描述中误用上述保留词

====================
分镜强制语法
====================
```
Shot N [起始-结束s]: 景别，画面主体，人物动作，环境光影，镜头运动，音效，"人物台词" [角色, 语气]:
```

示例：
```
Shot 1 [0-9s]: 中景，年轻女性 character1 手持山楂糕 Image 1，柔和窗光从左倾洒，镜头缓慢推近，环境白噪音，"好吃不腻，一口接一口" [character1, 轻快]: 
Shot 2 [9-20s]: 特写，山楂糕 Image 1 横切面展现纹理，侧逆光勾勒轮廓，固定机位，No background music, Hard cut transition
Shot 3 [20-30s]: 全景，character1 坐于木桌前 Image 3 背景，暖黄主光，镜头从下往上摇，No dialogue throughout the entire video
```

====================
图片序号映射规则（严格遵守）
====================
客户上传素材后，按以下固定顺序映射到 `Image N`：
```
Image 1            = 产品图 1
Image 2            = 产品图 2
...
Image K            = 产品图 K（K = 产品图数量）
Image K+1          = 模特图（若上传）
Image K+2          = 场景图（背景图，若上传）
```
- 上传产品图 → 在 prompt 中用对应 `Image N` 替换原产品描述
- 上传模特图 → 在 prompt 中用对应 `Image N` 替换原人物描述
- 上传场景图 → 在 prompt 中用对应 `Image N` 替换原背景描述
- 未上传的元素保持原视频对应特征（仅文字描述，不引用图）

示例（客户上传 2 张产品图 + 1 张模特图 + 1 张场景图）：
```
Shot 1 [0-9s]: 特写，山楂糕 Image 1 横切面纹理，侧逆光，固定机位
Shot 2 [9-20s]: 中景，模特 Image 3 手持产品 Image 2 展示包装，柔光，推镜
Shot 3 [20-30s]: 全景，character1 坐于木桌前 Image 4 背景，暖黄主光，镜头从下往上摇
```

====================
时长计算规则（爆款复刻场景，每次调用只处理 1 个分镜）
====================
- 参考视频段长 = 上方分镜脚本中该 Shot 镜头时长（落在 1-15 秒 API 约束内）
- 生成视频段长 = 整数秒，**≤ 30 - 参考视频段长**
- 示例：参考 9s → 生成最多 21s；参考 15s → 生成最多 15s
- 推荐：duration 取 `min(参考段长, 10)` 附近整数，留余量防越界
- 必填字段 `duration`（api_json 中）= 该 Shot 镜头时长整数秒（如 2.5s → 3，9.0s → 9）

====================
画面后置约束（必须追加在 prompt 末尾，自由描述区）
====================
```
电影级质感，高细节，真实物理运动，减少AI畸变，人物肢体正常，无扭曲穿模，画面稳定，运镜顺滑，画面无任何字幕、文字、水印、Logo、标题、贴纸
```
> 注意：这段属于自由描述，不是保留词，可以根据场景调整（如改成"动画质感"等）。
> ⚠️ "画面无任何字幕、文字、水印、Logo、标题、贴纸"段必须保留，禁止删除。

====================
跨段无缝衔接约束（必须遵守）
====================
1. **首尾帧画面严格对齐**：段 N 最后一帧画面描述 = 段 N+1 第一帧画面描述（构图/人物姿态/产品位置/光影/景别必须一致）
2. **跨段人物锁定**：上传了模特图时，所有段对人物的描述（衣服颜色/款式/发型/姿态）必须用**完全相同的描述串**复制粘贴，禁止换词
3. **跨段场景锁定**：所有段对背景/光影/色调的描述必须一致
4. **跨段产品锁定**：所有段对产品（Image 1 等）的位置/朝向/形态描述必须连贯
5. **段间过渡标记**：如需硬切，在段 N+1 开头加 `Hard cut transition`；否则不加任何转场词
6. **段 N 末尾画面**：prompt 必须明确写出"最后定格于: [具体画面描述]"作为锚点
7. **段 N+1 开头画面**：prompt 必须明确写出"开场延续上段末帧: [与段 N 末尾完全相同的画面描述]"
8. ⚠️ **禁止画面渲染任何文字**：prompt 中禁止出现"字幕""标题""水印""Logo""文字""贴纸"等描述词的位置/内容渲染指令
   - 禁止示例：`画面顶部出现字幕"xxx"`、`右下角显示Logo`、`标题：xxx`
   - 禁止在 prompt 中描述原视频字幕内容（即使原视频有字幕，也不要在 prompt 中提及，让 wan3.0 不渲染任何文字）
   - 仅允许在台词字段 `"台词" [角色, 语气]:` 中保留语音台词（用于语音合成，不会渲染到画面）

====================
API 参数约束（必须遵守）
====================
| 参数 | 类型 | 约束 |
|---|---|---|
| `prompt` | String | 必填，用 `Image N`/`Video N`/`Audio N` 按上传顺序指代 |
| `videoUrls` | List | 最多 5 段，**单段 1-15 秒，总时长 ≤15 秒**，单文件 100MB |
| `imageUrls` | List | 最多 10 张，单张 20MB |
| `audioUrls` | List | 最多 5 段，单段 1-15 秒，总时长 ≤15 秒，不计费 |
| `resolution` | String | 480P / 720P / 1080P |
| `aspectRatio` | String | **爆款复刻必须输出 "adaptive"**（保持原视频宽高比）；其他可选：16:9 / 4:3 / 1:1 / 3:4 / 9:16 |
| `duration` | String | **auto（智能时长）或 2-30 整数**。有参考视频时，**输入视频总时长 + 输出时长 ≤30 秒** |
| `audio` | Boolean | true=有声（默认），false=无声 |
| `enableThinking` | Boolean | 用 fileUrl/linkUrl 时必须 true，否则 false |
| `seed` | Int | 0-2147483647，可选 |

每段 JSON 必须包含字段：
```json
[
  {{
    "segment_index": 1,
    "duration": 9,
    "intent_inference": "本段使用 Image 1 替换原山楂饼为山楂糕；模特保持原视频白裙女性；场景保持室内暖光",
    "prompt": "Shot 1 [0-9s]: 中景，双手从顶部放下 Image 1 山楂糕并正立放置在桌面，柔和窗光从左倾洒，镜头缓慢推近，环境白噪音: 电影级质感，高细节，真实物理运动，减少AI畸变，人物肢体正常，无扭曲穿模，画面稳定，运镜顺滑",
    "api_json": {{
      "prompt": "（与上面 prompt 字段相同）",
      "videoUrls": ["由代码填入：上传该段参考视频后得到的 URL"],
      "imageUrls": ["由代码填入：上传所有素材图后得到的 URL 数组"],
      "audioUrls": [],
      "resolution": "480P",
      "aspectRatio": "adaptive",
      "duration": "9",
      "audio": "true",
      "enableThinking": "false",
      "seed": null
    }}
  }}
]
```

输出要求：
1. segment_index 与上方【四.五】合并段表的 segment_index 一一对应
2. duration = 该合并段时长整数秒（如 9.5s → 10，6.3s → 6）；**最小 2，最大 30**
3. prompt 必须包含运镜、动作、光影、声音的清晰描述，禁止一句话
4. prompt 末尾必加后置约束：电影级质感，高细节，真实物理运动，减少AI畸变，人物肢体正常，无扭曲穿模，画面稳定，运镜顺滑
5. 引用素材图时用 `Image N`（按上传顺序：产品图 1..N、模特图、场景图）
6. intent_inference 必须明确说明本段用了哪些 Image N 替换哪些原视频元素
7. 跨段首尾帧衔接：段 N 的最后一帧描述 = 段 N+1 的第一帧描述；如需硬切在段 N+1 开头加 `Hard cut transition`
8. 关闭模型自动扩写，严格忠于原视频内容
9. 视频参考和图片参考必须用 `Video N`/`Image N` 格式（空格分隔，无 @）
10. ⚠️ duration 必须 ≥ 2 且 ≤ 30（API 合法值），禁止输出 1 或小于 2 的值
11. ⚠️ **只描述上传了的素材**：
    - 上传产品图 → 用 `Image 1` 等描述产品
    - 上传模特图 → 可描述人物（衣服/姿态），且各段描述必须一致
    - 上传场景图 → 可描述背景
    - **未上传的不要描述**，让 wan3.0 跟随参考视频原画面
    - 例：只换产品 → 不写人物，只写"Image 1 背包被放置在桌上"
12. ⚠️ 跨段一致性（最关键，违反直接判失败）：
    - 上传了模特图时，每段对模特描述（衣服/姿态/发型）必须**完全相同**（用同一个固定描述串，不能换词）
    - 未上传模特图时，**禁止描述人物**（让 wan3.0 跟随参考视频原人物）
    - 跨段场景描述也必须保持一致（光影/背景/色调）
13. ⚠️ 段数最小化（关键，段越多跨段不一致风险越高）：
    - 视频总时长 ≤ 15s → **1 段**（不分段，直接整段输出）
    - 视频总时长 15-30s → **3 段**（每段 5-10s）
    - 视频总时长 30-40s → **4 段**（每段 7-10s）
    - 禁止超过 4 段
14. ⚠️ 每段 prompt 末尾必加固定后置约束（原样复制，不改写）：
    `电影级质感，高细节，真实物理运动，减少AI畸变，人物肢体正常，无扭曲穿模，画面稳定，运镜顺滑，人物特征稳定与跨段保持一致，画面无任何字幕、文字、水印、Logo、标题、贴纸`

现在开始解析。'''


SYSTEM_PROMPT = r'''你是 Step 1 的工业级视频结构解析引擎，只负责解析输入的 trimmed video，输出原视频的客观结构。你不知道、也不得接触目标新产品、产品参考图、Wan3.0 或后续生成策略。

【绝对禁止】
- 只描述输入视频中实际可见、可听见的事实，禁止剧情推测、心理判断、营销推断、画面脑补和改写。
- 不得接触或引用目标产品图片、Image N、Video N、Audio N、Wan3.0、gen_mode、FFmpeg 或任何下游生成参数。用户消息若提供目标产品名称，仅可用于识别各 Shot 的"产品"字段，不得写入其他字段。
- 忽略字幕、标题、文字、贴纸、水印和 Logo，不得写入任何输出字段。
- 只输出一个合法 JSON 数组；禁止 Markdown、解释、前言、总结或其他字段。

【输入】
- trimmed video：Step 0 已完成裁剪的视频。
- target_duration：后端提供的权威时长，仅用于理解时间范围，不得修改。

【Shot 切分规则】
- 切分依据的优先级，按上方「分镜切分模式」严格执行：
  · 模式A (scene_cut)：以真实镜头边界为主要依据（硬切、明确转场、镜头重新建立或明显独立镜头切换）。同一连续镜头内的连续动作默认属于同一 Shot。
  · 模式B (action_grained)：产品形态变化点、人物动作转折点优先级高于镜头切换；固定机位无镜头切换时，必须按产品形态变化（如折叠→展开、正面→背面、杯面→侧翼）和动作转折切分。
- 不得仅因一句话结束或人物转身而拆分；但产品形态变化、拿起/放下/翻面等动作转折在模式B下必须拆分。
- 不得为了控制 Shot 时长人为拆分或合并；Shot 数量服从切分依据（模式A: 真实镜头结构；模式B: 形态/动作变化点）。
- 同一连续镜头中的动作必须按实际顺序完整描述，例如“右手拿起产品，随后双手打开并展示，最后放回桌面”。

【产品解析规则】
- 用户消息提供目标产品名称时，据此在视频中识别目标产品；未提供时只描述输入视频中实际可见的主体原商品。
- 穿戴在人物身上的产品（假发/发型、服装、帽子、配饰等）也算产品，写入“产品”字段，不得只写进“人物”字段。
- 产品字段填写客观外观描述，例如“白色按压泵瓶”“黑色长裤”“透明塑料包装食品”。无法确认类别时只写可确认的外观，不猜品牌、型号、材质或性能。
- 原商品不可见时，产品和产品动作都填“无”；不得在无商品的镜头补充商品。

【主体原商品识别规则】
- “产品”字段只记录当前视频中的主体原商品，不记录画面中的所有物品。
- 按以下可观察证据判断主体原商品：
  1. 被人物直接手持、操作、穿戴或使用；
  2. 被镜头单独特写或持续聚焦；
  3. 在多个 Shot 中反复作为主要展示对象；
  4. 包装与商品本体属于同一主体商品时，视为同一商品的不同状态。
- 普通背景道具、家具、手机、杯子等非主要展示物不得写入“产品”字段。
- 无法唯一判断时，只填写当前能够客观确认的主要被操作或聚焦物，不猜品牌、型号或用途。

【商品交互规则】
“营销功能”字段只表示当前镜头可观察的展示类型，只能填写：外观展示、结构展示、操作演示、使用过程、状态变化、尺寸/数量展示、前后结果对比、包装展示、无。禁止填写显瘦、舒适、透气、防水、耐用、好吃、高级、提升效果等推断性卖点。
- 主体商品仅静态清晰展示、没有操作或状态变化时，填写“外观展示”。
- 主体商品虽可见但不是当前镜头展示重点时，填写“无”。

【通用不可辨识规则】
- 某视觉属性实际存在，但因遮挡、虚焦、过暗或分辨率不足无法判断时，填“看不清”。
- 某属性在当前 Shot 客观不存在或不适用时，填“无”。
- 禁止为了避免空值而猜测。

【人物入镜判定】
- 只要可见任何人体部位，包括手、手臂、腿、躯干或面部，即视为人物已入镜。
- “人物=无”仅用于画面中完全没有任何人体部位的 Shot。
- 仅部分身体入镜时，人物字段只描述实际可见部分，例如“仅双手入镜”“人物上半身入镜”。
- 未出现的身体区域对应微动作字段填“未入镜”；已出现但无法辨认的区域填“看不清”。

【声音与人物规则】
- 口播必须按音频中实际听到的原文逐字记录，保留原词、原句式、语序、重复、口头语和不完整句；不得润色、纠错、概括、补全、翻译、改写或替换产品名/品牌名。
- 听不清时才使用“听不清”：完全无法辨识填“听不清”，部分无法辨识只在对应位置使用“[听不清]”，其余可辨识文字必须逐字保留；禁止根据画面、产品图、上下文或常识猜测缺失内容。
- 同一句口播跨越多个 Shot 时，每个 Shot 只记录该 Shot 时间区间内实际听到的片段；不得为了语义完整重复、删减或改写口播。
- 无人声填“无”。
- “情绪”字段实际表示声音表达强度，只能填“强/中/弱/无”，不得表达心理状态。
- 只能描述可观察的眼神、眉眼、嘴角、头部、重心、手部和肩颈变化；未入镜填“未入镜”，局部不可辨识填“看不清”，禁止使用开心、紧张、自信、兴奋等心理词。

【非口播声音规则】
- “音效”字段记录当前 Shot 中可直接听见的所有非口播声音，包括背景音乐、环境声、产品操作声，以及碰撞、摩擦、撕裂、开合等物理声音。
- 只描述可听见的事实，不推断声音来源。
- 没有可辨识的非口播声音时填“无”；口播只写入“口播”字段，不重复写入“音效”。

【转场衔接字段规则】
- “转场衔接”描述“前一个 Shot → 当前 Shot”的实际连接方式。
- Shot 1 固定填写“无”；后续 Shot 根据实际画面填写“硬切”“淡入”“遮挡转场”等。
- 无法确认时填“看不清”；禁止预测当前 Shot 之后尚未发生的转场。

【时间序列化规则】
- 时间只针对当前 trimmed video，第一帧为 00:00.00。
- 所有 Shot 时间使用局部时间轴 0 → target_duration；不得恢复原始长视频的 trim_start，不得使用原视频绝对时间。
- 后端 float 类型的 target_duration 是唯一权威时长，MiMo 不得修改。
- 第一 Shot 的“开始时间”必须为“00:00.00”。
- 最后一 Shot 的“结束时间”应为 target_duration 的两位小数表示；两位小数表示产生的 ≤0.05s 差异由后端 Checkpoint A 统一 canonicalize。
- JSON 中“开始时间”和“结束时间”使用 `MM:SS.xx` 格式，保留两位小数；“镜头时长”使用 `X.XXs` 格式，且等于结束时间减开始时间，保留两位小数。
- MiMo 不负责改变后端 target_duration，也不负责最终时间规范化。

【固定空值规则】
- 无主体商品：产品、产品动作、交互方式、交互部位、营销功能全部填“无”。
- 无人物：人物、人物动作填“无”；面部微表情四字段和身体微动作四字段全部填“未入镜”。
- 有人物但人脸未入镜：面部微表情四字段全部填“未入镜”。
- 人脸入镜但某项无法辨认：对应字段填“看不清”。
- 无人声：口播和声音语气六字段全部填“无”。
- 无可辨识独立音效：音效填“无”。
- 所有叶子字段必须是非空字符串；不能确定时必须使用上述固定值，不得输出空字符串。

【唯一输出 Schema】
- 顶层必须是一个 JSON 数组。
- 除顶层 Shot 数组外，禁止任何其他数组。
- 每个 Shot 必须且只能包含以下对象结构；不得增加、删除或改名。
- `shot` 类型必须为 integer；其余所有叶子字段类型必须为非空 string。
- 禁止 null、额外 object 和 Markdown。
- 以下尖括号内容仅表示 Schema 占位说明，不得原样输出。

```json
[
  {
    "shot": 1,
    "时间区间": {"开始时间": "00:00.00", "结束时间": "00:02.53", "镜头时长": "2.53s"},
    "场景环境": {"场景": "<客观场景描述>", "背景细节": "<客观背景描述>", "光影设定": "<客观光影描述>"},
    "人物与产品": {"人物": "<客观人物描述或无>", "产品": "<主体原商品客观描述或无>"},
    "画面动作": {"画面内容": "<客观画面内容>", "人物动作": "<客观人物动作或无>", "产品动作": "<客观产品动作或无>"},
    "镜头语言": {"景别": "<客观景别>", "构图": "<客观构图>", "运镜": "<客观运镜>", "转场衔接": "<前一 Shot 到当前 Shot 的转场或无>"},
    "音效": "<可辨识音效或无>",
    "口播": "<逐字口播、听不清或无>",
    "声音语气": {"语速": "<语速或无>", "语调": "<语调或无>", "重音": "<重音或无>", "停顿": "<停顿或无>", "情绪": "<表达强度或无>", "呼吸感": "<可听见呼吸特征或无>"},
    "面部微表情": {"眼神方向": "<眼神方向、未入镜或看不清>", "眉眼变化": "<眉眼变化、未入镜或看不清>", "嘴角状态": "<嘴角状态、未入镜或看不清>", "表情说明": "<可观察表情变化、未入镜或看不清>"},
    "身体微动作": {"头部微晃": "<头部变化、未入镜或看不清>", "重心转移": "<重心变化、未入镜或看不清>", "手部律动": "<手部动作、未入镜或看不清>", "肩颈律动": "<肩颈变化、未入镜或看不清>"},
    "商品交互": {"交互方式": "<交互方式或无>", "交互部位": "<交互部位或无>", "营销功能": "<客观展示类型或无>"}
  }
]
```

【内部自检，不输出】
- 顶层只输出一个 JSON 数组；不得输出额外数组、对象、Markdown 或解释文字。
- Shot 连续编号，字段无增减，所有叶子字段非空。
- 时间从 00:00.00 开始，按真实镜头边界连续覆盖输入视频，不重叠、不留空，最后一 Shot 结束时间为 target_duration 的两位小数表示。
- 固定空值规则必须严格执行。

现在开始解析。'''


def _file_to_base64(file_path: str, mime_type: str) -> str:
    with open(file_path, "rb") as f:
        data = f.read()
    return f"data:{mime_type};base64,{base64.b64encode(data).decode()}"


def _extract_blocks(content: str):
    """提取 Step 1 唯一的 source_storyboard JSON 数组。

    兼容两种输出格式：
    - 旧版：顶层直接是 JSON 数组 [{shot1}, {shot2}, ...]
    - 新版（爆款裂变 v2）：顶层是对象 {"shot_analysis": [...], "reference_segment": {...}}
      此处只提取 shot_analysis 数组，reference_segment 由调用方从 raw_content 单独解析
    """
    # 优先尝试新版对象格式：若顶层是对象且含 shot_analysis，直接返回该数组
    obj_text = _extract_outermost_object(content or "")
    if obj_text:
        try:
            obj = json.loads(obj_text)
            if isinstance(obj, dict) and "shot_analysis" in obj:
                shot_arr = obj["shot_analysis"]
                if isinstance(shot_arr, list):
                    return json.dumps(shot_arr, ensure_ascii=False)
        except json.JSONDecodeError:
            pass  # 不是合法对象，降级走原数组解析

    fenced = re.findall(r"```json\s*([\s\S]*?)\s*```", content or "", re.I)
    if len(fenced) > 1:
        raise ValueError(f"MiMo只能输出一个JSON代码块，实际找到{len(fenced)}个")
    if len(fenced) == 1:
        blocks = [fenced[0]]
    else:
        array = _extract_outermost_array(content or "")
        if not array:
            raise ValueError("MiMo必须输出一个source_storyboard JSON数组")
        blocks = [array]
    for index, block in enumerate(blocks, start=1):
        stripped = block.strip()
        if not stripped.startswith("[") or not stripped.endswith("]"):
            raise ValueError("MiMo输出必须直接包含JSON数组")
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            try:
                parsed = _parse_json_robust(stripped)
            except ValueError as robust_error:
                raise ValueError(f"MiMo输出不是合法JSON: {exc.msg}; {robust_error}") from exc
        if not isinstance(parsed, list):
            raise ValueError("MiMo输出必须是JSON数组")
        return json.dumps(parsed, ensure_ascii=False)
    raise ValueError("MiMo未输出source_storyboard")


def _extract_outermost_object(text: str) -> str:
    """从文本中提取最外层的 {...} 对象（括号平衡）"""
    start = text.find('{')
    if start == -1:
        return ""
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return ""


def _extract_outermost_array(text: str) -> str:
    """从文本中提取最外层的 [...] 数组（括号平衡）"""
    start = text.find('[')
    if start == -1:
        return ""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '[':
            depth += 1
        elif text[i] == ']':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return ""


def _find_all_top_level_arrays(text: str) -> list:
    """找出文本中所有顶层 [...] 数组（括号平衡）"""
    results = []
    i = 0
    while i < len(text):
        if text[i] == '[':
            depth = 0
            start = i
            while i < len(text):
                if text[i] == '[':
                    depth += 1
                elif text[i] == ']':
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:i + 1]
                        if candidate.strip().startswith('[{') or candidate.strip().startswith('[\n{'):
                            results.append(candidate)
                        break
                i += 1
        i += 1
    return results


def _fix_json(text: str) -> str:
    """修复 MiMo 输出中常见的 JSON 格式问题。"""
    # 移除控制字符。
    text = re.sub(r'[\x00-\x1f\x7f]', ' ', text)
    # JSON 数字不允许前导零，MiMo 偶发输出 01.00/04.50。
    text = re.sub(r'(?<=[:\[,])\s*0+(?=\d+(?:\.\d+)?\s*[,}\]])', ' ', text)

    # 修复未转义的引号（在 JSON 字符串值中）。
    # 逐字符解析，跟踪是否在字符串内
    result = []
    i = 0
    in_string = False
    escape_next = False
    
    while i < len(text):
        c = text[i]
        
        if escape_next:
            result.append(c)
            escape_next = False
            i += 1
            continue
        
        if c == '\\' and in_string:
            result.append(c)
            escape_next = True
            i += 1
            continue
        
        if c == '"':
            if not in_string:
                in_string = True
                result.append(c)
            else:
                # 检查这个引号是字符串结束还是未转义的内部引号
                # 看后面的字符：如果紧跟 , : } ] 空白 → 字符串结束
                # 如果紧跟其他字符（非空白非标点）→ 可能是未转义引号
                rest = text[i+1:].lstrip()
                if rest and rest[0] in ',:]}\r\n':
                    # 字符串结束
                    in_string = False
                    result.append(c)
                elif not rest:
                    # 末尾
                    in_string = False
                    result.append(c)
                else:
                    # 可能是未转义的引号 → 转义它
                    result.append('\\"')
            i += 1
            continue
        
        result.append(c)
        i += 1
    
    return ''.join(result)


def _parse_json_robust(text: str):
    """健壮地解析 MiMo 输出的 JSON"""
    # 先尝试直接解析
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    # 尝试修复后解析
    try:
        fixed = _fix_json(text)
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    
    # 暴力修复：用正则提取每个 shot 对象
    try:
        # 找到所有 {...} 并尝试单独解析
        shot_pattern = re.compile(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', re.S)
        shots = []
        for match in shot_pattern.finditer(text):
            obj_text = match.group()
            try:
                obj = json.loads(_fix_json(obj_text))
                if isinstance(obj, dict) and 'shot' in obj:
                    shots.append(obj)
            except json.JSONDecodeError:
                continue
        if shots:
            return shots
    except Exception:
        pass
    
    raise ValueError(f"无法解析 MiMo JSON 输出，前100字符: {text[:100]}")


def _parse_timestamp(value: str) -> float:
    match = re.fullmatch(r"(\d{2}):(\d{2})(?:\.(\d{1,2}))?", str(value).strip())
    if not match:
        raise ValueError(f"时间格式错误: {value}")
    minutes = int(match.group(1))
    seconds = int(match.group(2))
    fraction = match.group(3) or "0"
    centiseconds = int(fraction.ljust(2, "0"))
    if seconds >= 60:
        raise ValueError(f"时间秒数越界: {value}")
    return minutes * 60 + seconds + centiseconds / 100


def _extract_source_range(raw_content: str) -> tuple[float, float]:
    """提取解析依据里的原视频绝对时间区间。

    仅用于日志参考，不参与强校验（时间连续性由 storyboard 时间区间保证）。
    匹配失败时返回 (0.0, 0.0) 降级，不阻断流程。
    """
    time_token = r"(?:\d{2}:\d{2}(?:\.\d{1,2})?|\d+(?:\.\d+)?)"
    # 允许"原视频"与时间区间之间存在其他文字（如"实际解析的...绝对时间区间为"），
    # 只要"原视频"与时间段在同一行即可。优先匹配紧贴格式，回退到宽松匹配。
    match = re.search(
        rf"原视频\s*({time_token})\s*s\s*[-~～—至到]\s*({time_token})\s*s",
        raw_content,
        re.I,
    )
    if not match:
        match = re.search(
            rf"原视频[^\n]*?(?:时间区间[^0-9\n]*)?({time_token})\s*s\s*[-~～—至到]\s*({time_token})\s*s",
            raw_content,
            re.I,
        )
    if not match:
        # 降级：匹配不到固定格式，返回 (0, 0)，不阻断流程
        return 0.0, 0.0

    def to_seconds(value: str) -> float:
        return _parse_timestamp(value) if ":" in value else float(value)

    start, end = (to_seconds(value) for value in match.groups())
    if start < 0 or end <= start:
        return 0.0, 0.0
    return start, end


def _normalize_storyboard_keys(storyboard: list) -> None:
    """将模型偶发输出的英文/混合键归一化为契约要求的中文键。

    已知模型偶发输出：
      - 人物与产品: product → 产品
      - 画面动作: product动作 → 产品动作, person动作 → 人物动作, 画面content → 画面内容
      - 顶层: 口pub → 口播
    """
    if not isinstance(storyboard, list):
        return
    for shot in storyboard:
        if not isinstance(shot, dict):
            continue
        # 顶层：口pub → 口播
        if "口pub" in shot and "口播" not in shot:
            shot["口播"] = shot.pop("口pub")
        elif "口pub" in shot:
            shot.pop("口pub")
        # 人物与产品
        people_product = shot.get("人物与产品")
        if isinstance(people_product, dict):
            if "product" in people_product and "产品" not in people_product:
                people_product["产品"] = people_product.pop("product")
            elif "product" in people_product:
                people_product.pop("product")
        # 商品交互：营销功能 ↔ 可观察展示类型（prompt 用前者，旧校验读后者）
        interaction = shot.get("商品交互")
        if isinstance(interaction, dict):
            market = interaction.get("营销功能")
            observe = interaction.get("可观察展示类型")
            if (observe is None or str(observe).strip() == "") and market not in (None, ""):
                interaction["可观察展示类型"] = market
            if (market is None or str(market).strip() == "") and observe not in (None, ""):
                interaction["营销功能"] = observe
        # 画面动作
        action = shot.get("画面动作")
        if isinstance(action, dict):
            # product动作 → 产品动作
            if "product动作" in action and "产品动作" not in action:
                action["产品动作"] = action.pop("product动作")
            elif "product动作" in action:
                action.pop("product动作")
            # person动作 → 人物动作
            if "person动作" in action and "人物动作" not in action:
                action["人物动作"] = action.pop("person动作")
            elif "person动作" in action:
                action.pop("person动作")
            # 画面content → 画面内容
            if "画面content" in action and "画面内容" not in action:
                action["画面内容"] = action.pop("画面content")
            elif "画面content" in action:
                action.pop("画面content")
            # 防御性清洗：剥离"画面内容"中残留的原视频字幕描述
            # 形如 "俯拍白色圆盘...｜字幕：底部 我二话不说" 或 "...|无字幕"
            _visual = action.get("画面内容")
            if isinstance(_visual, str) and _visual:
                for _sep in ["｜字幕：", "|字幕：", "｜字幕", "|字幕"]:
                    if _sep in _visual:
                        _visual = _visual.split(_sep)[0].rstrip()
                        break
                _visual = _visual.replace("｜无字幕", "").replace("|无字幕", "").rstrip()
                action["画面内容"] = _visual


def _validate_result(storyboard: list, target_duration: Optional[float] = None) -> None:
    if not isinstance(storyboard, list) or not storyboard:
        raise ValueError("分镜脚本必须是非空数组")
    _normalize_storyboard_keys(storyboard)
    shot_keys = {"shot", "时间区间", "场景环境", "人物与产品", "画面动作", "镜头语言", "音效", "口播", "声音语气", "面部微表情", "身体微动作", "商品交互"}
    # 可选顶层字段：新版提示词可输出"画面文字"（爆款裂变 v2 专用），旧版无此字段
    optional_shot_keys = {"画面文字"}
    nested_keys = {
        "时间区间": {"开始时间", "结束时间", "镜头时长"},
        "场景环境": {"场景", "背景细节", "光影设定"},
        "人物与产品": {"人物", "产品"},
        "画面动作": {"画面内容", "人物动作", "产品动作"},
        "镜头语言": {"景别", "构图", "运镜", "转场衔接"},
        "声音语气": {"语速", "语调", "重音", "停顿", "情绪", "呼吸感"},
        "面部微表情": {"眼神方向", "眉眼变化", "嘴角状态", "表情说明"},
        "身体微动作": {"头部微晃", "重心转移", "手部律动", "肩颈律动"},
    }
    expected_shots = list(range(1, len(storyboard) + 1))
    if [item.get("shot") for item in storyboard] != expected_shots:
        raise ValueError("分镜Shot必须从1开始连续编号")
    previous_end = 0.0
    for index, shot in enumerate(storyboard):
        shot_no = index + 1
        missing = shot_keys - set(shot)
        extra = set(shot) - shot_keys - optional_shot_keys
        if missing or extra:
            raise ValueError(f"Shot {shot_no} 顶层字段不符合契约: missing={sorted(missing)}, extra={sorted(extra)}")
        # 可选顶层字段：存在时必须是非空字符串
        for opt in optional_shot_keys:
            if opt in shot:
                if not isinstance(shot[opt], str) or not shot[opt].strip():
                    raise ValueError(f"Shot {shot_no} 的{opt}必须是非空字符串")
        for field, keys in nested_keys.items():
            value = shot.get(field)
            if not isinstance(value, dict) or set(value) != keys:
                actual = set(value) if isinstance(value, dict) else set()
                raise ValueError(f"Shot {shot_no} 的{field}字段不符合契约: {sorted(actual ^ keys)}")
            for key, leaf in value.items():
                if not isinstance(leaf, str) or not leaf.strip():
                    raise ValueError(f"Shot {shot_no} 的{field}.{key}必须是非空字符串")
        for field in ("音效", "口播"):
            if not isinstance(shot[field], str) or not shot[field].strip():
                raise ValueError(f"Shot {shot_no} 的{field}必须是非空字符串")
        interval = shot["时间区间"]
        for key in ("开始时间", "结束时间", "镜头时长"):
            if not isinstance(interval[key], str):
                raise ValueError(f"Shot {shot_no} 的时间区间.{key}必须是字符串")
        if not re.fullmatch(r"\d{2}:\d{2}\.\d{2}", interval["开始时间"]):
            raise ValueError(f"Shot {shot_no} 开始时间必须为 MM:SS.xx 格式")
        if not re.fullmatch(r"\d{2}:\d{2}\.\d{2}", interval["结束时间"]):
            raise ValueError(f"Shot {shot_no} 结束时间必须为 MM:SS.xx 格式")
        if not re.fullmatch(r"\d+(?:\.\d{2})s", interval["镜头时长"]):
            raise ValueError(f"Shot {shot_no} 镜头时长必须为 X.XXs 格式")
        local_start = _parse_timestamp(interval["开始时间"])
        local_end = _parse_timestamp(interval["结束时间"])
        declared_duration = float(interval["镜头时长"][:-1])
        actual_duration = round(local_end - local_start, 2)
        if abs(declared_duration - actual_duration) > 0.01:
            raise ValueError(f"Shot {shot_no} 镜头时长与起止时间不一致")
        if abs(local_start - previous_end) > 0.02 or local_end <= local_start:
            raise ValueError(f"Shot {shot_no} 时间区间不连续或无效")
        previous_end = local_end
        product = str(shot["人物与产品"]["产品"]).strip()
        product_action = str(shot["画面动作"]["产品动作"]).strip()
        if not product:
            raise ValueError(f"Shot {shot_no} 产品字段不能为空")
        if product == "无":
            if product_action != "无":
                raise ValueError(f"Shot {shot_no} 产品为“无”但产品动作不是“无”")
            interaction = shot["商品交互"] or {}
            display_type = interaction.get("可观察展示类型")
            if display_type is None or str(display_type).strip() == "":
                display_type = interaction.get("营销功能") or "无"
            if any(
                str(interaction.get(key) or "无").strip() != "无"
                for key in ("交互方式", "交互部位")
            ) or str(display_type).strip() != "无":
                raise ValueError(f"Shot {shot_no} 产品为“无”但商品交互字段不是全“无”")
        if shot["声音语气"]["情绪"] not in {"强", "中", "弱", "无"}:
            raise ValueError(f"Shot {shot_no} 情绪字段超出允许值")
    if target_duration is not None:
        first_start = _parse_timestamp(storyboard[0]["时间区间"]["开始时间"])
        if abs(first_start) > 0.02:
            raise ValueError("第一 Shot 必须从 00:00.00 开始")
        last_end = _parse_timestamp(storyboard[-1]["时间区间"]["结束时间"])
        if abs(last_end - target_duration) > 0.05:
            raise ValueError(f"最后 Shot 结束时间 {last_end:.2f}s 未覆盖到 target_duration={target_duration:.2f}s")


def _build_timeline(storyboard: list) -> list:
    """为抽帧旁路生成每个 Shot 的局部中点时间。"""
    timeline = []
    for shot in storyboard:
        interval = shot["时间区间"]
        start = _parse_timestamp(interval["开始时间"])
        end = _parse_timestamp(interval["结束时间"])
        timeline.append({"shot": shot["shot"], "time": round((start + end) / 2, 2)})
    return timeline


def run(
    video_path: str,
    product_name: str,
    product_image_path: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: str = "https://token-plan-cn.xiaomimimo.com/v1",
    model: str = "mimo-v2.6-flash",
    fps: float = 4.0,
    media_resolution: str = "max",
    output_dir: Optional[str] = None,
    product_image_paths: Optional[list] = None,
    allow_cross_segment: bool = False,
    split_mode: str = "scene_cut",
    target_duration: Optional[float] = None,
    model_image_path: Optional[str] = None,
    scene_image_path: Optional[str] = None,
    customer_request: Optional[str] = None,
    system_prompt: Optional[str] = None,
) -> dict:
    """
    Step1: MiMo-2.5 视频结构解析 + Wan3.0 prompt 反推（一站式）

    新增参数:
        model_image_path: 模特图路径（可选，让 MiMo 视觉识别人物特征用于替换）
        scene_image_path: 场景/背景图路径（可选，让 MiMo 视觉识别环境用于替换）
        customer_request: 客户需求说明文本（自然语言，让 MiMo 综合判断复刻意图）

    输出新增字段:
        wan3_plans: List[Dict] - 每段一个 wan3.0 调用 JSON
            含 shot_index, duration, prompt, api_json, intent_inference

    Args:
        video_path: 视频文件路径
        product_name: 用户指定的精确新产品名称
        product_image_path: 产品图路径（可选，向下兼容）
        product_image_paths: 多张产品图路径列表（可选，优先使用）
        api_key: MiMo API Key
        base_url: MiMo API 地址
        model: 模型名称
        fps: 视频采样帧率
        media_resolution: 视频分辨率
        output_dir: 输出目录
        allow_cross_segment: 是否允许跨段重组（True=可从原视频不同位置截取片段重新编排成15秒）
        split_mode: 分镜切分模式
            "scene_cut" = 镜头转场切分（默认，允许复合动作）
            "action_grained" = 细颗粒度动作切分（每个shot单一原子动作，产品形态变化必拆）
        target_duration: 目标视频时长（秒）。None=1:1 完整复刻（按原视频时长）；
            15/20/25/30 = 压缩重编模式：分镜总时长压缩到目标时长，加速节奏、合并相似分镜。

    Returns:
        {"storyboard": [...], "timeline": [...], "raw_content": str, "output_dir": str}
    """
    api_key = api_key or os.environ.get("MIMO_API_KEY")
    if not api_key:
        try:
            from config import settings as _mimo_settings
            api_key = (_mimo_settings.MIMO_API_KEY or "").strip()
            if api_key:
                os.environ["MIMO_API_KEY"] = api_key
                if not (base_url or "").strip():
                    base_url = _mimo_settings.MIMO_BASE_URL
        except Exception:
            pass
    if not api_key:
        raise ValueError("MIMO_API_KEY 未设置")
    # 允许外部传入自定义 system_prompt（爆款裂变 v2 等场景使用），默认走原提示词
    if system_prompt is None:
        system_prompt = SYSTEM_PROMPT

    # Bug 修复：注入 {{SPLIT_MODE}} 占位符（之前 prompt 模板里有占位符但代码从未替换）
    # split_mode 取值：scene_cut（默认）/ action_grained（细颗粒度，产品形态变化必拆）
    if "{{SPLIT_MODE}}" in system_prompt:
        system_prompt = system_prompt.replace("{{SPLIT_MODE}}", split_mode)
        print(f"  [Step1] 注入 SPLIT_MODE={split_mode}")

    if target_duration is not None:
        print(f"  [Step1] 解析裁剪后视频，目标时长 {target_duration:.2f}s")
    else:
        print("  [Step1] 解析裁剪后视频")

    video_b64 = _file_to_base64(video_path, "video/mp4")
    # 当外部传入自定义 system_prompt（如爆款裂变 v2 专用提示词）时，
    # user content 不强制"输出数组"和"目标时长"，按 system_prompt 定义的 Schema 输出
    _is_custom_prompt = system_prompt is not None and system_prompt != SYSTEM_PROMPT
    # 用户指定的目标产品注入：此前 product_name 是死参数（只有签名没有使用），
    # MiMo 只能瞎猜主体商品，穿戴型产品（假发/头发）被归进"人物"字段，
    # 两段全判"产品=无"，Step3 无事可做（2026-10-10 假发任务事故）。
    _product_hint = ""
    if product_name:
        _product_hint = (
            f"\n用户指定的目标产品（含替换说明）：「{product_name}」\n"
            "请据此在视频中识别目标产品，其可见形态（含穿戴在人物身上的假发/发型、服饰、配饰）"
            "在某 Shot 可见时，该 Shot 的\"产品\"字段填其客观外观描述；确实不可见才填\"无\"。\n"
        )
    if _is_custom_prompt:
        _user_hint = "只解析当前输入视频，不读取任何目标产品或后续生成信息。按系统提示词定义的 Schema 输出。"
    else:
        _user_hint = "只解析当前输入视频，不读取产品参考图或后续生成信息。" + _product_hint + "输出一个source_storyboard JSON数组。" + (f"当前视频目标时长为 {target_duration:.2f} 秒。" if target_duration is not None else "")
    content_list = [{
        "type": "video_url",
        "video_url": {"url": video_b64},
        "fps": fps,
        "media_resolution": media_resolution,
    }, {
        "type": "text",
        "text": _user_hint,
    }]

    # 4. 调用 MiMo
    # 实测 MiMo-v2.5 偶发返回 finish_reason=length 且 content="" 的故障
    # （推理 tokens 用尽但未输出正文），需对空 content 自动重试。
    # max_completion_tokens 16384：MiMo-2.5 是推理模型，推理+输出共享配额，8192 会被截断
    client = OpenAI(api_key=api_key, base_url=base_url)

    # #region debug-point mimo-400-error
    try:
        import json as _debug_json
        import urllib.request as _debug_request
        _debug_event = {
            "sessionId": "mimo-400-error",
            "runId": "pre-fix",
            "hypothesisId": "A-B-C-D",
            "location": "pipeline/step1_mimo_parse.py:run",
            "msg": "[DEBUG] MiMo request summary",
            "data": {
                "base_url_repr": repr(base_url),
                "model": model,
                "has_api_key": bool(api_key),
                "message_count": 2,
                "user_content_types": [item.get("type") for item in content_list],
                "video_url_prefix": str(content_list[0].get("video_url", {}).get("url", ""))[:32],
                "video_url_length": len(content_list[0].get("video_url", {}).get("url", "")),
                "fps": fps,
                "media_resolution": media_resolution,
                "max_completion_tokens": 8192,
            },
        }
        _debug_request.urlopen(_debug_request.Request(
            "http://127.0.0.1:7777/event",
            data=_debug_json.dumps(_debug_event, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"},
        ), timeout=2).read()
    except Exception:
        pass
    # #endregion

    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content_list},
            ],
            max_completion_tokens=16384,
        )
    except Exception as _mimo_error:
        # #region debug-point mimo-400-error-response
        try:
            import json as _debug_json
            import urllib.request as _debug_request
            _debug_response = getattr(_mimo_error, "response", None)
            _debug_event = {
                "sessionId": "mimo-400-error",
                "runId": "pre-fix",
                "hypothesisId": "A-B-C-D",
                "location": "pipeline/step1_mimo_parse.py:run",
                "msg": "[DEBUG] MiMo request failed",
                "data": {
                    "error_type": type(_mimo_error).__name__,
                    "error_text": str(_mimo_error)[:1000],
                    "response_status": getattr(_debug_response, "status_code", None),
                    "response_body": getattr(_debug_response, "text", "")[:2000],
                },
            }
            _debug_request.urlopen(_debug_request.Request(
                "http://127.0.0.1:7777/event",
                data=_debug_json.dumps(_debug_event, ensure_ascii=False).encode(),
                headers={"Content-Type": "application/json"},
            ), timeout=2).read()
        except Exception:
            pass
        # #endregion
        raise
    raw_content = completion.choices[0].message.content or ""
    finish_reason = getattr(completion.choices[0], "finish_reason", None)

    # 空 content 自动重试（最多 2 次），每次重新请求
    retry_count = 0
    while not raw_content.strip() and retry_count < 2:
        retry_count += 1
        print(f"  [WARN] MiMo 返回空 content (finish_reason={finish_reason})，重试第 {retry_count} 次...")
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content_list},
            ],
            max_completion_tokens=16384,
        )
        raw_content = completion.choices[0].message.content or ""
        finish_reason = getattr(completion.choices[0], "finish_reason", None)

    # 立即保存原始响应（无论后续解析是否成功，都保留诊断依据）
    if output_dir:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        with open(out / "mimo_raw.json", "w", encoding="utf-8") as f:
            json.dump({"content": raw_content, "finish_reason": finish_reason, "retries": retry_count}, f, ensure_ascii=False, indent=2)

    # 检查截断：只有当 content 非空且 finish_reason=length 时才是真正的超长截断
    if finish_reason == "length":
        if not raw_content.strip():
            # 重试后仍为空，提升错误信息明确性
            raise ValueError(
                "MiMo 连续 3 次返回空 content 且 finish_reason=length，"
                "可能是视频帧过多导致推理 tokens 耗尽。建议降低 fps 或缩短视频。"
            )
        else:
            raise ValueError(
                f"MiMo 响应被截断（finish_reason=length，content {len(raw_content)} 字符），"
                f"请增大 max_completion_tokens 或精简提示词"
            )

    # 5. 提取 JSON（带自动重试）
    try:
        storyboard_block = _extract_blocks(raw_content)
        storyboard = _parse_json_robust(storyboard_block)
        _validate_result(storyboard, target_duration)
    except ValueError as validation_error:
        print(f"  [WARN] MiMo输出未通过校验: {validation_error}")
        print("  -> 追加单数组纠错提示，重试一次...")
        if _is_custom_prompt:
            retry_content = (
                f"你上次的输出未通过程序校验：{validation_error}\n"
                "按系统提示词定义的 Schema 重新输出合法 JSON，不要 Markdown、解释或其他内容。\n"
                "只描述当前输入视频中的原商品和原始事实，不得引入目标产品、产品图片或下游生成信息。"
            )
        else:
            retry_content = (
                f"你上次的输出未通过程序校验：{validation_error}\n"
                "只输出一个合法的source_storyboard JSON数组，不要Markdown、解释或其他内容。\n"
                "只描述当前输入视频中的原商品和原始事实，不得引入目标产品、产品图片或下游生成信息。"
            )
        completion2 = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content_list},
                {"role": "assistant", "content": raw_content},
                {"role": "user", "content": retry_content},
            ],
            max_completion_tokens=16384,
        )
        raw_content = completion2.choices[0].message.content or ""
        finish_reason = getattr(completion2.choices[0], "finish_reason", None)
        if finish_reason == "length":
            raise ValueError("MiMo格式纠错响应被截断（finish_reason=length）")
        storyboard = _parse_json_robust(_extract_blocks(raw_content))
        _validate_result(storyboard, target_duration)

    timeline = _build_timeline(storyboard)

    # 7. 保存（mimo_raw.json 已在前面保存）
    if output_dir:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        with open(out / "storyboard.json", "w", encoding="utf-8") as f:
            json.dump(storyboard, f, ensure_ascii=False, indent=2)
        with open(out / "timeline.json", "w", encoding="utf-8") as f:
            json.dump(timeline, f, ensure_ascii=False, indent=2)

    return {
        "storyboard": storyboard,
        "timeline": timeline,
        "mode": "replica",
        "raw_content": raw_content,
        "output_dir": str(output_dir) if output_dir else None,
    }
