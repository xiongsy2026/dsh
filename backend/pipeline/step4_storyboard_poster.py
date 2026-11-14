"""
============================================================================
⚠️ 爆款复刻工作流 - 受保护文件（整文件保护）
参见 BOUNDARY.md。修改本文件必须经过显式确认：
  1. 运行 python scripts/check_replica_boundary.py 记录变更
  2. 运行 pytest tests/test_replica_boundary.py -v 验证行为
  3. 运行 python scripts/update_replica_boundary.py 更新签名基准
未经流程确认的修改将破坏工作流稳定性。
============================================================================

Step4: 故事板海报生成
输入: 替换后的 shot 图片 + storyboard.json
输出: final_storyboard.jpg
"""
import asyncio
import base64
import io
import json
import math
from pathlib import Path
from typing import List, Optional

from PIL import Image, ImageDraw
from backend.services.image2_client import Image2Client

# ── 固定海报提示词（允许通过 prompts.json 覆盖） ──
POSTER_PROMPT = """组装一张"电商短视频分镜故事板海报"，采用专业影视分镜 + 带货脚本结构设计。
整体为一张横向信息海报，风格为"商业短视频分镜分析表 + 电商商品展示脚本 + 声音导演表"。

====================🚨🚨🚨 画面来源铁律（最高优先级，凌驾于全文一切规则之上）====================
⚠️⚠️⚠️ 这是一张"拼装型"海报，不是"创作型"海报。你做的工作是"把已有的关键帧图片拼到海报布局里 + 加文字标注 + 加边框装饰"，不是"看着关键帧重新画一遍"。
- 每个 Shot 卡片的图像区必须 100% 复刻 @图片1 中对应编号格子的原图画面
- 禁止重新生成画面、禁止二次创作、禁止按理解重画、禁止美化、禁止改风格
- 禁止改变：构图、人物姿态、产品位置、产品颜色、产品包装、背景、光影、人物表情
- 禁止打乱顺序：@图片1 左起第 1 个格子 = 海报 Shot 01 卡片，依次对应，一个错位整张作废
- 你只是"图框 + 文字 + 装饰"的组装工，画面本身已经定型，严禁动画面
- 唯一允许的图像处理：等比缩放（放进卡片）、加边框/圆角/投影装饰
- 若你动了画面里任何一个元素（哪怕只是把产品转个角度、把模特换个表情），整张海报判定失败

====================📐 画面结构（强制栅格系统，核心修复）====================
画布比例：16:9 横版
⚠️ 强制三段式栅格布局（必须严格执行，不可自由排版）：
整张画面被水平划分为 3 个固定区域：
① Header 顶部区域（固定高度 18%）② Main 中间主体区域（固定高度 64%）③ Footer 底部区域（固定高度 18%）

====================🚨 核心约束（解决重叠问题关键）====================
1. 三个区域必须严格"上下分层"，不可重叠
2. 所有元素必须被限制在各自区域边界内
3. 不允许任何文字/图片/图标跨区域
4. 必须存在明显视觉分割线：浅灰线或留白
5. 每个区域内部内容允许排版，但不能溢出边界
6. 禁止为了美观自动删减字段
7. 禁止省略"声音语气"字段
8. 禁止只保留"口播"而丢失声音表演指导信息

====================① HEADER 顶部区域（18%高度）====================
内容：
* 主标题 * 副标题 * 参数信息 * 风格标签
布局规则：
* 必须水平排布或三段分栏
* 所有内容垂直居中于 Header 区域
* 不可进入 Main 区域
* 不可下压或遮挡分镜内容
Header 内容：
主标题："电商短视频分镜故事板海报"
副标题："商业短视频分镜分析表 + 电商商品展示脚本 + 声音导演表"
参数信息："时长：15秒" "镜头：" "场景：" "类型："
风格标签："商业提案风" "电商广告分镜板" "轻工业设计感" "Pitch Deck Storyboard" "Voice Direction"

====================② MAIN 中间主体区域（64%高度）====================
MAIN 区域再拆分为左右结构：
左侧（70%宽度）：分镜网格 Shot Grid
右侧（30%宽度）：人物 + 产品 + 声音导演表展示区
左右区域必须有明确竖向分割线或留白，不允许互相侵入。

====================📌 左侧 Shot Grid 规则====================
* 按 Shot 顺序排列
* 每个 Shot 卡片必须包含：
  1. 上方分镜图
  2. 中部画面信息
  3. 下方声音语气精简条

⚠️ Shot 卡片强制规则：
* Shot 卡片不能超出 Main 区域
* 卡片之间必须有间距，不得重叠
* 文字必须在卡片内部换行，不可溢出
* 不允许文字压图
* 不允许图片压文字
* 每个 Shot 卡片都必须显示"声音语气精简条"
* 声音语气不可省略、不可合并成一句泛泛描述

====================📌 每个 Shot 卡片内部结构====================
每个 Shot 卡片采用"三段式卡片结构"：

A. 图像区，占卡片高度约 55%
* ⚠️【核心】直接复刻 @图片1 中对应编号格子的原图画面，禁止重画
* 做法：把 @图片1 中对应编号格子（Shot 01 卡片取左起第 1 格，Shot 02 取左起第 2 格，依此类推）的画面原封不动搬进卡片图像区
* 唯一允许的图像处理：等比缩放、加边框/圆角/投影
* 禁止任何二次创作：不得改构图、改人物姿态、改产品位置、改背景、改光影、改表情、改产品颜色
* 左上角黑底白字标注：Shot 01 / Shot 02 / Shot 03……
* 图像区只允许放图和 Shot 编号，不允许放大段文字

B. 画面信息区，占卡片高度约 30%
必须显示以下字段（从嵌套结构中提取）：
"Shot编号" "时间区间"（开始-结束/时长）
"场景"（场景环境.场景） "背景"（场景环境.背景细节） "光影"（场景环境.光影设定）
"人物"（人物与产品.人物） "产品"（人物与产品.产品）
"画面"（画面动作.画面内容） "人物动作"（画面动作.人物动作） "产品动作"（画面动作.产品动作）
"景别"（镜头语言.景别） "构图"（镜头语言.构图） "运镜"（镜头语言.运镜） "转场"（镜头语言.转场衔接）
"音效" "口播"
"微表情"（面部微表情.表情说明）

C. 声音语气精简条，占卡片高度约 15%
必须显示以下字段的压缩版：
"语速" "语调" "重音" "停顿" "情绪" "呼吸感"
展示格式示例：
"声：偏快｜强号召｜重音：闭眼冲｜停顿：时髦后｜情绪：催单｜呼吸：激昂"
⚠️ 注意：声音语气精简条必须出现在每个 Shot 卡片底部，不可省略。

====================📌 右侧人物 + 产品 + 声音导演表区域====================
右侧 30% 宽度为独立竖向区域，拆分为 3 个模块：
上方 35%：人物展示
中间 30%：产品展示
下方 35%：声音导演表
三个模块必须上下排列，互不重叠，全部限制在右侧区域内部。

---

## 右侧模块 1：人物展示
标题："人物展示"
内容：
* 使用模特参考图展示人物
* 保持人物形象、妆发、帽子、上衣、裤装风格一致
* 图片必须等比缩放
* 不得进入左侧 Shot Grid 区域

---

## 右侧模块 2：产品展示
标题："产品展示"
内容：
* 使用产品参考图展示服装
* 可展示平铺图/三视图/商品正面图
* 图片必须等比缩放
* 不得进入左侧 Shot Grid 区域
产品卖点短句：参考分镜脚本推理

---

## 右侧模块 3：声音导演表
标题："声音导演表 Voice Direction"
必须以小表格形式展示所有 Shot 的声音语气字段。
表格列名：
"Shot" "语速" "语调" "重音" "停顿" "情绪" "呼吸感"

⚠️ 声音导演表强制规则：
* 必须展示所有 Shot
* 每一行对应一个 Shot
* 每一行都必须包含 6 个声音维度
* 不允许省略任意字段
* 不允许只写"自然口语"或"推荐语气"这类笼统描述
* 表格字体可小，但必须清晰、整齐、可读
* 表格必须限制在右侧下方模块内，不得压到产品图或 Footer
声音导演表内容来自分镜 JSON 中的：
"声音语气": {"语速" "语调" "重音" "停顿" "情绪" "呼吸感"}

====================③ FOOTER 底部区域（18%高度）====================
必须包含 5 个横向均匀分布的信息模块：
1. 视觉设计（调色板图标）
2. 拍摄设备（相机图标）
3. 节奏结构（跑步图标）
4. 音乐建议（音符图标）
5. 风格标签（星标）
布局规则：
* 横向均匀分布
* 图标 + 标题 + 文字说明上下结构
* 所有内容必须居中对齐在 Footer 区域
* 不可上浮进入 Main 区域
* 模块之间使用浅灰竖线或留白分隔
Footer 文案根据分镜脚本推理填写。

====================🎬 分镜数据字段（必须完整读取，与Step1输出结构一致）====================
每个 Shot 为嵌套结构，必须读取并展示以下字段：

{
  "shot": 编号,
  "时间区间": {"开始时间", "结束时间", "镜头时长"},
  "场景环境": {"场景", "背景细节", "光影设定"},
  "人物与产品": {"人物", "产品"},
  "画面动作": {"画面内容", "人物动作", "产品动作"},
  "镜头语言": {"景别", "构图", "运镜", "转场衔接"},
  "音效": "...",
  "口播": "...",
  "声音语气": {"语速", "语调", "重音", "停顿", "情绪", "呼吸感"},
  "面部微表情": {"眼神方向", "眉眼变化", "嘴角状态", "表情说明"}
}

其中"声音语气"必须继续拆分为6个子字段：
"语速" "语调" "重音" "停顿" "情绪" "呼吸感"

⚠️ 字段展示规则：
1. Shot 卡片内展示（从嵌套结构中提取）：
* Shot编号
* 时间区间（开始-结束/时长）
* 场景（来自 场景环境.场景）
* 背景（来自 场景环境.背景细节）
* 光影（来自 场景环境.光影设定）
* 人物（来自 人物与产品.人物）
* 产品（来自 人物与产品.产品）
* 画面内容（来自 画面动作.画面内容）
* 人物动作（来自 画面动作.人物动作）
* 产品动作（来自 画面动作.产品动作）
* 景别（来自 镜头语言.景别）
* 构图（来自 镜头语言.构图）
* 运镜（来自 镜头语言.运镜）
* 转场（来自 镜头语言.转场衔接）
* 音效
* 口播
* 微表情（来自 面部微表情.表情说明）
* 声音语气精简条

2. 右侧声音导演表完整展示：
* 每个 Shot 的 声音语气.语速
* 每个 Shot 的 声音语气.语调
* 每个 Shot 的 声音语气.重音
* 每个 Shot 的 声音语气.停顿
* 每个 Shot 的 声音语气.情绪
* 每个 Shot 的 声音语气.呼吸感

3. 不允许删掉声音语气字段
4. 不允许只展示口播不展示声音语气
5. 不允许把声音语气归纳成一句话
6. 不允许把声音语气合并成一个总说明

====================🎯 视觉风格要求====================
* 商业提案风
* 电商广告分镜板
* 轻工业设计感
* 信息密度高
* Pitch Deck Storyboard 风格
* 专业影视分镜分析表
* 电商带货脚本拆解板
* 视觉干净、理性、清晰
* 使用白色/浅灰底色
* 使用黑色文字
* 使用细线框、表格、标签、模块化卡片
* 整体像专业服饰广告短视频策划方案，而不是普通拼图

====================❌ 强制禁止项====================
* 禁止任何元素跨越 Header/Main/Footer 边界
* 禁止文字压图
* 禁止图片压文字
* 禁止区域重叠
* 禁止自由散点排版
* 禁止 AI 自动"美化布局"
* 禁止删减 JSON 字段
* 禁止省略"声音语气"
* 禁止把声音语气只写成"推荐语气"
* 禁止右侧人物/产品/声音表互相覆盖
* 禁止 Shot 卡片文字溢出
* 禁止 Footer 上浮进入 Main 区域
* 必须严格栅格系统执行

====================✅ 最终画面目标====================
生成一张 16:9 横版"电商短视频分镜故事板海报"。
它必须同时具备：
1. 上方 Header：标题、参数、风格标签
2. 中间左侧：Shot 分镜卡片
3. 中间右侧：人物展示、产品展示、声音导演表
4. 底部 Footer：视觉设计、拍摄设备、节奏结构、音乐建议、风格标签
5. 每个 Shot 卡片内必须有"声音语气精简条"
6. 右侧必须有完整"声音导演表"
7. 所有模块必须严格在自己的区域内，不重叠、不跨界、不溢出"""


# 本步骤以 PRD 完整提示词为准，禁止被精简配置覆盖。


# ── 视频类型自动判定（符合 Seedance 2.0 可生成性优先原则）──
def _detect_video_type(storyboard: list) -> str:
    """基于分镜 JSON 自动判定视频类型：voiceover（口播）/ showcase（展示）/ action（动作）

    判定逻辑：
    - 口播类：≥50% shot 有非空口播（非"无"/"听不清"）
    - 动作类：口播稀少 + 人物动作镜头占比高
    - 展示类：口播稀少 + 产品镜头占主导（默认）
    """
    if not storyboard:
        return "showcase"

    total = len(storyboard)
    voiceover_count = sum(
        1 for s in storyboard
        if s.get("口播") and s["口播"] not in ("无", "听不清", "")
    )

    # 口播类：≥50% 的 shot 有口播
    if voiceover_count / total >= 0.5:
        return "voiceover"

    # 统计人物动作 vs 产品动作的镜头占比
    def _has_action(shot, key):
        action = shot.get("画面动作", {}) if isinstance(shot.get("画面动作"), dict) else {}
        val = action.get(key, "")
        return bool(val) and val not in ("无", "静止", "")

    action_shots = sum(1 for s in storyboard if _has_action(s, "人物动作"))
    product_shots = sum(1 for s in storyboard if _has_action(s, "产品动作"))

    # 动作类：人物动作镜头占比高
    if action_shots > product_shots:
        return "action"

    # 展示类：默认
    return "showcase"


# ── 口播类模板：强化声音导演，弱化产品三视图 ──
POSTER_PROMPT_VOICEOVER = """组装一张"口播类电商短视频分镜故事板海报"。

⚠️⚠️⚠️ 画面来源铁律（最高优先级）：这是拼装型海报，不是创作型。每个 Shot 卡片图像区必须 100% 复刻 @图片1 中对应编号格子的原图，禁止重新生成画面、禁止二次创作、禁止按理解重画。只允许等比缩放、加边框/圆角/投影。动了画面任何元素整张作废。

视频类型：口播类（人物对镜头说话为主，声音表演是核心）。

====================📐 画面结构（16:9 横版，三段式栅格）====================
① Header 顶部区域（18%高度）：标题"口播类分镜故事板"+ 时长 + 镜头数 + 类型标签
② MAIN 中间主体区域（64%高度）：左侧分镜卡片网格（70%）+ 右侧声音导演表（30%）
③ Footer 底部区域（18%高度）：节奏结构 + 音乐建议 + 风格标签

====================📌 左侧 Shot 卡片（精简字段，避免信息过载）====================
每个 Shot 卡片采用三段式结构：
A. 图像区（55%高度）：分镜图 + 左上角 Shot 编号
B. 口播与声音区（30%高度）：
   - 时间区间（开始-结束/时长）
   - 口播文字（完整台词）
   - 声音语气精简条："语速|语调|重音|停顿|情绪|呼吸感"
C. 表演指导区（15%高度）：
   - 眼神方向 + 嘴角状态（精简，每项不超过5字）

⚠️ Shot 卡片规则：
- 卡片不重叠、不溢出
- 口播文字必须完整显示，不得省略
- 声音语气精简条必须出现在每个 Shot 卡片底部

====================📌 右侧声音导演表（30%宽度，核心区域）====================
标题："声音导演表 Voice Direction"
表格列：Shot | 语速 | 语调 | 重音 | 停顿 | 情绪 | 呼吸感
- 必须展示所有 Shot
- 每行对应一个 Shot，包含完整 6 个声音维度
- 不允许省略字段或合并成笼统描述

====================② FOOTER 底部区域（18%高度）====================
5 个横向均匀分布模块：
1. 节奏结构（跑步图标）2. 音乐建议（音符图标）3. 声音优先级（口播>拟音>BGM）
4. 语速曲线 5. 风格标签

====================🎯 视觉风格====================
- 商业提案风 + 电商带货脚本板
- 白底黑字 + 细线框 + 模块化卡片
- 重点突出声音表演指导，弱化产品三视图
- 符合 Seedance 2.0 可生成性优先原则"""


# ── 展示类模板：强化产品材质，弱化声音语气 ──
POSTER_PROMPT_SHOWCASE = """组装一张"展示类电商短视频分镜故事板海报"。

⚠️⚠️⚠️ 画面来源铁律（最高优先级）：这是拼装型海报，不是创作型。每个 Shot 卡片图像区必须 100% 复刻 @图片1 中对应编号格子的原图，禁止重新生成画面、禁止二次创作、禁止按理解重画。只允许等比缩放、加边框/圆角/投影。动了画面任何元素整张作废。
视频类型：展示类（产品展示为主，无人物口播或口播稀少，视觉质感是核心）。

====================📐 画面结构（16:9 横版，三段式栅格）====================
① Header 顶部区域（18%高度）：标题"展示类分镜故事板"+ 时长 + 镜头数 + 类型标签
② MAIN 中间主体区域（64%高度）：左侧分镜卡片网格（70%）+ 右侧产品展示区（30%）
③ Footer 底部区域（18%高度）：视觉设计 + 拍摄设备 + 风格标签

====================📌 左侧 Shot 卡片（精简字段，聚焦产品视觉）====================
每个 Shot 卡片采用三段式结构：
A. 图像区（60%高度）：分镜图 + 左上角 Shot 编号
B. 产品视觉区（25%高度）：
   - 时间区间（开始-结束/时长）
   - 产品动作（来自 画面动作.产品动作）
   - 景别（来自 镜头语言.景别）
   - 光影设定（来自 场景环境.光影设定）
   - 商品占比与可见证据
C. 场景衔接区（15%高度）：
   - 场景（来自 场景环境.场景）
   - 转场衔接（来自 镜头语言.转场衔接）

⚠️ Shot 卡片规则：
- 卡片不重叠、不溢出
- 产品动作必须清晰描述，符合物理规律
- 光影设定必须明确光照方向、色调氛围
- 省略口播、声音语气、人物微表情（展示类无人物口播）

====================📌 右侧产品展示区（30%宽度，核心区域）====================
右侧 30% 宽度为独立竖向区域，拆分为 2 个模块：
上方 60%：产品三视图展示
  - 使用产品参考图展示
  - 平铺图/正面图/细节图
  - 图片等比缩放，禁止拉伸
下方 40%：核心卖点短句
  - 3-5 条产品卖点（参考分镜脚本推理）
  - 每条不超过 15 字
  - 聚焦可见证据，禁止抽象词

====================② FOOTER 底部区域（18%高度）====================
5 个横向均匀分布模块：
1. 视觉设计（调色板图标）2. 拍摄设备（相机图标）3. 材质质感
4. 光影方案 5. 风格标签

====================🎯 视觉风格====================
- 商业棚拍风 + 质感展示板
- 白底黑字 + 细线框 + 模块化卡片
- 重点突出产品材质与光影，弱化声音语气
- 符合 Seedance 2.0 可生成性优先原则（每镜一个卖点证据）"""


# ── 动作类模板：强化运镜转场，弱化口播声音 ──
POSTER_PROMPT_ACTION = """组装一张"动作类电商短视频分镜故事板海报"。

⚠️⚠️⚠️ 画面来源铁律（最高优先级）：这是拼装型海报，不是创作型。每个 Shot 卡片图像区必须 100% 复刻 @图片1 中对应编号格子的原图，禁止重新生成画面、禁止二次创作、禁止按理解重画。只允许等比缩放、加边框/圆角/投影。动了画面任何元素整张作废。
视频类型：动作类（人物动作演示为主，运镜变化丰富，动作连贯是核心）。

====================📐 画面结构（16:9 横版，三段式栅格）====================
① Header 顶部区域（18%高度）：标题"动作类分镜故事板"+ 时长 + 镜头数 + 类型标签
② MAIN 中间主体区域（64%高度）：左侧分镜卡片网格（70%）+ 右侧动作流程图（30%）
③ Footer 底部区域（18%高度）：节奏结构 + 拍摄设备 + 风格标签

====================📌 左侧 Shot 卡片（精简字段，聚焦动作与运镜）====================
每个 Shot 卡片采用三段式结构：
A. 图像区（55%高度）：分镜图 + 左上角 Shot 编号
B. 动作指导区（30%高度）：
   - 时间区间（开始-结束/时长）
   - 人物动作（来自 画面动作.人物动作，核心字段）
   - 产品动作（来自 画面动作.产品动作）
   - 景别（来自 镜头语言.景别）
   - 运镜（来自 镜头语言.运镜，核心字段）
C. 衔接指导区（15%高度）：
   - 转场衔接（来自 镜头语言.转场衔接）
   - 动作匹配点（与下一镜的衔接锚点）

⚠️ Shot 卡片规则：
- 卡片不重叠、不溢出
- 人物动作必须物理对应产品动作（不混成抽象描述）
- 运镜只选一种主运镜（固定/缓推/横移/跟拍/环绕/缓拉）
- 省略口播、声音语气（动作类无人物口播）

====================📌 右侧动作流程图（30%宽度，核心区域）====================
右侧 30% 宽度为独立竖向区域，拆分为 2 个模块：
上方 50%：动作流程图
  - 用箭头连接各 Shot 的关键姿态
  - 标注每个动作的起始状态 → 执行 → 自然回落
  - 确保动作三段式完整
下方 50%：连续性约束
  - 角色脸部与服装一致性
  - 左右手持物
  - 视线落点
  - 动作结束状态
  - 场景与光线连续性

====================② FOOTER 底部区域（18%高度）====================
5 个横向均匀分布模块：
1. 节奏结构（跑步图标）2. 拍摄设备（相机图标）3. 运镜方案
4. 动作匹配 5. 风格标签

====================🎯 视觉风格====================
- 专业影视分镜板 + 动作流程图
- 白底黑字 + 细线框 + 箭头连接
- 重点突出人物动作与运镜连贯，弱化口播声音
- 符合 Seedance 2.0 可生成性优先原则（每镜一个主动作）"""


# 模板路由表
POSTER_PROMPT_BY_TYPE = {
    "voiceover": POSTER_PROMPT_VOICEOVER,
    "showcase": POSTER_PROMPT_SHOWCASE,
    "action": POSTER_PROMPT_ACTION,
}


def _build_shot_reference(shots_image_paths: List[str], output_path: Path) -> None:
    """将所有 Shot 确定性拼成一张参考图，保证 @图片1 包含完整且有序的 Shot。

    采用单行水平排列（从左到右 Shot 01, 02, ..., NN），避免多行网格导致模型
    误判行优先/列优先顺序。格子间用粗分割线 + 大号顶部编号标签，让模型能
    清晰识别每个 Shot 的边界和编号。

    分辨率设计（2026-08-24 修复"画面糊死/故事板和替换分镜完全不一样"bug）：
    旧版 cell_w = min(512, 2048 // n)，n=10 时 cell_w=204，扣掉 label_h=44
    后图片区仅 160px 高 → 替换后的书包细节全糊死，image2 识别不出产品，
    故事板画面与 step3 替换分镜完全脱节。
    修复后：上限放宽到 4096（image2 max_px=2048 会自动缩，但模型识图能力远
    超过 160px），cell_h 保留原帧宽高比（不再强制正方形），让产品细节可识别。
    """
    frames = []
    for index, image_path in enumerate(shots_image_paths, start=1):
        with Image.open(image_path) as source:
            frames.append((index, source.convert("RGB").copy()))
    if not frames:
        raise ValueError("Step4 未收到任何 Shot 图片")

    n = len(frames)
    # 单行排列：总宽度上限 4096（image2 接受 max_px=2048 自动缩，
    # 但缩放前的清晰度决定模型能否识别产品细节）
    cell_w = min(512, 4096 // n)
    # 保留原帧宽高比（1024:1792），不再强制正方形
    # 用第 1 张图的宽高比作为基准（所有 shot 应同尺寸）
    _ref_w, _ref_h = frames[0][1].size
    _ratio = _ref_h / _ref_w if _ref_w > 0 else 1.4
    cell_h = int(cell_w * _ratio)
    label_h = 44  # 顶部编号标签高度
    gap = 8  # 格子间分割线宽度
    total_w = cell_w * n + gap * (n - 1)
    total_h = cell_h + label_h

    canvas = Image.new("RGB", (total_w, total_h), "white")
    draw = ImageDraw.Draw(canvas)

    for index, image in frames:
        image.thumbnail((cell_w, cell_h), Image.LANCZOS)
        cell_index = index - 1
        x0 = cell_index * (cell_w + gap)
        y0 = 0
        # 顶部编号标签（黑底白字，占满格子宽度）
        draw.rectangle((x0, y0, x0 + cell_w - 1, y0 + label_h - 1), fill="black")
        try:
            from PIL import ImageFont
            font = ImageFont.truetype("arial.ttf", 28)
        except Exception:
            font = ImageFont.load_default()
        label = f"Shot {index:02d}"
        try:
            bbox = draw.textbbox((0, 0), label, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except Exception:
            tw, th = 80, 20
        draw.text(
            (x0 + (cell_w - tw) // 2, y0 + (label_h - th) // 2 - 2),
            label, fill="white", font=font,
        )
        # 图片区域（标签下方），居中粘贴
        img_y = y0 + label_h
        img_x = x0 + (cell_w - image.width) // 2
        canvas.paste(image, (img_x, img_y))
        # 格子边框
        draw.rectangle((x0, y0, x0 + cell_w - 1, y0 + cell_h - 1), outline="black", width=2)

    canvas.save(output_path, format="JPEG", quality=95)


def _image_to_data_url(image_path: Path, max_px: int = 2048) -> str:
    with Image.open(image_path) as source:
        image = source.convert("RGB")
        if max(image.size) > max_px:
            image.thumbnail((max_px, max_px), Image.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=92)
    return f"data:image/jpeg;base64,{base64.b64encode(buffer.getvalue()).decode()}"


async def _run_async(
    shots_image_paths: List[str],
    storyboard_json: list,
    output_dir: str,
    product_image_path: str = None,
    model_image_path: str = None,
    size: str = "1536x1024",
    video_type: str = None,
    feedback: str = None,
    override_video_type: str = None,
    product_image_paths: Optional[List[str]] = None,
    replaced_storyboard_path: str = None,
):
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    client = Image2Client()

    if len(shots_image_paths) != len(storyboard_json):
        raise ValueError(
            f"Step4 Shot 数量不一致: 图片 {len(shots_image_paths)}，脚本 {len(storyboard_json)}"
        )

    # 视频类型判定：override 优先 > video_type > 自动判定
    # override_video_type 仅接受 "auto" 或具体类型；"auto"/空 走自动判定
    _effective_override = (override_video_type or "").strip().lower()
    if _effective_override and _effective_override != "auto":
        video_type = _effective_override
    # 视频类型自动判定（未指定时基于分镜 JSON 推断）
    if not video_type:
        video_type = _detect_video_type(storyboard_json)
    print(f"  → 视频类型: {video_type}（{'口播类' if video_type == 'voiceover' else '展示类' if video_type == 'showcase' else '动作类'}）")
    if feedback and feedback.strip():
        print(f"  → 审核修改意见（强制执行）: {feedback.strip()[:80]}{'...' if len(feedback.strip()) > 80 else ''}")
    if _effective_override and _effective_override != "auto":
        print(f"  → 视频类型手动覆盖: {_effective_override}")

    # 根据视频类型路由模板
    base_prompt = POSTER_PROMPT_BY_TYPE.get(video_type, POSTER_PROMPT_SHOWCASE)

    # @图片1 = step3/storyboard_replaced.jpg（替换好产品审核通过的分镜拼图）
    # 设计变更（2026-08-24 修复"故事板分镜图与替换分镜完全脱节"bug）：
    # 旧版：step4 自拼 shot_reference.jpg（单行条带，cell_w=204→160px 高，画面糊死）
    #        → image2 看不清产品细节 → 自由发挥 → 故事板画面与 step3 替换分镜脱节
    # 新版：直接复用 step3 已生成的高清拼图（4×3 多行网格，4096×5376）
    #        布局：≤5 张 cols=3，>5 张 cols=4，从左到右从上到下 Shot 01..NN
    #        每格顶部有"Shot XX"黑底白字编号标签
    if replaced_storyboard_path and Path(replaced_storyboard_path).exists():
        shot_reference_path = Path(replaced_storyboard_path)
        print(f"  → @图片1 (step3 替换拼图): {shot_reference_path}")
    else:
        # 兜底：若调用方未传 replaced_storyboard_path，仍自拼（高分辨率版）
        shot_reference_path = out / "shot_reference.jpg"
        _build_shot_reference(shots_image_paths, shot_reference_path)
        print(f"  → @图片1 (step4 自拼兜底): {shot_reference_path}")

    images = [_image_to_data_url(shot_reference_path, max_px=2048)]
    image_roles = [
        f"@图片1：替换好产品的分镜拼图（多行网格布局，从左到右从上到下依次是 Shot 01, Shot 02, ..., Shot {len(storyboard_json):02d}，"
        f"共 {len(storyboard_json)} 个格子，每格顶部有黑底白字编号标签。"
        f"海报左侧 Shot Grid 中每个 Shot 卡片的图像必须与 @图片1 中对应编号格子的画面一致（构图/人物/产品/背景/光影），"
        f"不得打乱顺序、不得混淆内容、不得合并相邻 Shot。"
    ]

    # 产品图入参：N 张全传（与 step3 主流程对齐，不再只传 1 张主图）
    # 设计原因：
    # 1. @图片1 是 step3 替换后分镜拼图，每格已是真实产品画面
    # 2. N 张产品图全传给 image2 作产品一致性参考（主图作 @图2，辅助图依次作 @图3..N）
    # 3. image2 自行识别角度匹配，无需 LLM 选图
    _all_product_paths = []
    if product_image_path and Path(product_image_path).exists():
        _all_product_paths.append(product_image_path)
    if product_image_paths:
        for p in product_image_paths:
            if p and Path(p).exists() and p not in _all_product_paths:
                _all_product_paths.append(p)
    for prod_path in _all_product_paths:
        images.append(_image_to_data_url(Path(prod_path), max_px=1536))
        image_roles.append(
            f"@图片{len(images)}：产品参考图，用于右侧产品展示区主图位及产品一致性参考，必须等比缩放且保持纹理与包装比例"
        )

    # 模特图不传：@图片1 中已含人物画面，右侧不需要独立模特参考

    script_str = json.dumps(storyboard_json, ensure_ascii=False, indent=2)
    prompt = base_prompt + f"""

====================分镜数据来源====================
{' '.join(image_roles)}
分镜脚本 JSON 如下：
{script_str}

最终执行铁律：
1. @图片1 是一张多行网格布局的替换后分镜拼图，从左到右从上到下依次是 Shot 01, Shot 02, ..., Shot {len(storyboard_json):02d}，共 {len(storyboard_json)} 个格子，每格顶部有黑底白字编号标签。你必须在左侧 Shot Grid 完整展示 {len(storyboard_json)} 个 Shot 卡片，按 Shot 01 到 Shot {len(storyboard_json):02d} 从左到右/从上到下排列，一个都不能少、不能合并、不能重复。
2. ⚠️⚠️⚠️【画面来源最高优先级 = 整个任务的生死线】海报中每个 Shot 卡片的图像区必须 100% 复刻 @图片1 中对应编号格子的原图，不是"参考着画"，是"原封不动搬进去"：
   - Shot 01 卡片图像区 = @图片1 第 1 格原图（左上角起）
   - Shot 02 卡片图像区 = @图片1 第 2 格原图
   - Shot 03 卡片图像区 = @图片1 第 3 格原图
   - 依次对应（从左到右从上到下编号），错一格整张海报作废
   - 禁止行为（任何一条违反都判定失败）：
     ❌ 重新生成画面（即使是"相似的画面"也不行，必须是同一张图）
     ❌ 二次创作（按理解重画）
     ❌ 美化/改风格（变成动漫风、变成写实风等）
     ❌ 改变构图、人物姿态、产品位置、产品颜色、产品包装、背景、光影、人物表情
     ❌ 打乱顺序（Shot 03 的画面跑到 Shot 05 位置）
     ❌ 混淆内容（把两个 Shot 的元素混合）
     ❌ 增删画面元素、改变构图比例
     ❌ 合并相邻 Shot 的画面
   - 唯一允许的图像处理：等比缩放（放进卡片）、加边框/圆角/投影装饰
   - 正确做法：把 @图片1 对应编号格子的画面当照片直接贴进卡片图像区，然后只在画面外（边框、文字标注区）做装饰
3. 保持 JSON 原结构，不改字段、不改顺序、不改 Shot 数量。
4. ⚠️【产品一致性最高优先级】@图片1 中的产品已经是替换后的真实产品（来自 Step3），
   海报中任何 Shot 卡片里的产品必须与 @图片1 中的产品完全一致：
   - 严格保留 @图片1 中产品的轮廓、长宽比、材质、纹理、Logo、颜色、包装
   - 禁止改回原始产品或臆造新产品
   - 禁止抹平纹理、简化包装、改变印刷图案
   - 产品展示区（如有）必须直接使用产品参考图，等比缩放，禁止拉伸/压扁/裁切
5. 严格执行 Header 18% + Main 64% + Footer 18% 三段式布局，不得替换成两段式布局。
6. 视频类型为 {video_type}，必须使用对应类型的字段精简规则，省略不相关字段。
"""

    # 审核修改意见：用户从前端提交，作为"必须严格执行"段追加到 prompt 末尾，覆盖模板默认行为
    _feedback_text = (feedback or "").strip()
    if _feedback_text:
        prompt += f"""

====================🚨 审核修改意见（必须严格执行，覆盖前文冲突项）====================
用户对上一版故事板海报的修改意见如下，必须逐条严格落实，优先级高于前文任何模板规则：
{_feedback_text}

执行要求：
- 上述意见为强制约束，不可忽略、不可弱化、不可"参考性"执行
- 若意见与前文模板字段精简规则冲突，以本意见为准
- 若意见要求增/删字段或调整区域比例，直接执行，不需保留模板原结构
- 完成后请在海报中体现所有修改点，不得遗漏
"""
        print(f"  → 已追加审核修改意见段（{len(_feedback_text)} 字）")

    # 持久化中间参数（供前端展示与调试）
    params_file = out / "step4_params.json"
    params_payload = {
        "video_type": video_type,
        "video_type_source": "override" if (_effective_override and _effective_override != "auto") else ("explicit" if video_type else "auto"),
        "template_name": {
            "voiceover": "POSTER_PROMPT_VOICEOVER",
            "showcase": "POSTER_PROMPT_SHOWCASE",
            "action": "POSTER_PROMPT_ACTION",
        }.get(video_type, "POSTER_PROMPT_SHOWCASE（fallback）"),
        "size": size,
        "shot_count": len(storyboard_json),
        "image_roles": image_roles,
        "images_count": len(images),
        "has_product_image": bool(_all_product_paths),
        "product_image_count": len(_all_product_paths),
        "has_model_image": bool(model_image_path and Path(model_image_path).exists()),
        "feedback": _feedback_text or None,
        "override_video_type": _effective_override or None,
        "prompt": prompt,
        "storyboard_json": storyboard_json,
    }
    params_file.write_text(json.dumps(params_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  → 中间参数已写入: {params_file}")

    result = await client.image_edit_sync(
        prompt=prompt, images=images, size=size, n=1,
        response_format="b64_json",
    )
    data_list = result.get("data", [])
    output_path = out / "final_storyboard.jpg"

    if data_list:
        url = data_list[0].get("url", "")
        b64 = data_list[0].get("b64_json", "")
        if b64:
            if b64.startswith("data:"):
                b64 = b64.split(",", 1)[1]
            output_path.write_bytes(base64.b64decode(b64))
        elif url:
            if url.startswith("data:"):
                b64_part = url.split(",", 1)[1]
                output_path.write_bytes(base64.b64decode(b64_part))
            else:
                import httpx
                resp = httpx.get(url, timeout=60.0)
                output_path.write_bytes(resp.content)

    # 校验最终海报是否生成成功
    if not output_path.exists():
        raise RuntimeError(
            f"Step4 图片编辑 API 未返回有效数据，final_storyboard.jpg 未生成。"
            f"API 返回: {result}"
        )

    return {"poster": str(output_path), "output_dir": str(out), "video_type": video_type, "params_file": str(params_file)}


def run(
    shots_image_paths: List[str],
    storyboard_json: list,
    output_dir: str,
    product_image_path: str = None,
    model_image_path: str = None,
    size: str = "1536x1024",
    video_type: str = None,
    feedback: str = None,
    override_video_type: str = None,
    product_image_paths: Optional[List[str]] = None,
    replaced_storyboard_path: str = None,
) -> dict:
    """
    Step4: 故事板海报生成（根据视频类型路由不同模板）

    Args:
        shots_image_paths: 替换后的 shot 图片路径列表
        storyboard_json: 分镜JSON列表
        output_dir: 输出目录
        product_image_path: 产品参考图路径（兼容单图模式，主产品图）
        model_image_path: 模特参考图路径（可选，右侧人物展示）
        size: 海报尺寸
        video_type: 视频类型（voiceover口播/showcase展示/action动作），None时自动判定
        feedback: 审核修改意见（非空时作为"必须严格执行"段追加到 prompt，覆盖模板默认行为）
        override_video_type: 视频类型手动覆盖（"auto"/"voiceover"/"showcase"/"action"），"auto"或空时走自动判定
        product_image_paths: 多张产品图路径列表（可选，所有产品图全部入参，
            与 product_image_path 合并去重，主产品图置首位，辅助参考图依次追加）
        replaced_storyboard_path: step3/storyboard_replaced.jpg 路径（替换好产品审核通过的分镜拼图，
            高清多行网格布局，作为 @图1 传给 image2，避免 step4 自拼低分辨率导致画面糊死）

    Returns:
        {"poster": str, "output_dir": str, "video_type": str, "params_file": str}
    """
    if not video_type:
        video_type = _detect_video_type(storyboard_json)
    return asyncio.run(_run_async(
        shots_image_paths, storyboard_json, output_dir,
        product_image_path, model_image_path, size, video_type,
        feedback=feedback, override_video_type=override_video_type,
        product_image_paths=product_image_paths,
        replaced_storyboard_path=replaced_storyboard_path,
    ))
