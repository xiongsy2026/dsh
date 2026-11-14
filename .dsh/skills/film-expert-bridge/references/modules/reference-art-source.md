---
name: ai-drama-reference-art
description: 编写AI短剧角色/场景参考图提示词（GPT Image生成）。当用户需要生成角色参考图、场景参考图、角色三视图、character sheet、角色设定图、场景设定图、参考图prompt时使用。包含参考图4原则、9宫格4原则、4面板character sheet格式、棚拍规则、色卡规则、角色差异化设计方法。适用于任何需要AI生成角色/场景参考图的项目。
---

# AI短剧参考图提示词编写

为AI短剧项目编写GPT Image参考图提示词，涵盖角色参考图（4面板character sheet）和场景参考图（9宫格多角度）两种类型。

## 参考图类型

### 角色参考图 = 4面板character sheet
角色参考图**必须**使用4面板2x2 grid格式，不是单张环境立绘。

**4面板布局**：
- 上排左：FACE FRONT（正面脸）
- 上排右：FACE SIDE（侧面脸）
- 下排左：BODY FRONT (NO FACE)（正面身·刻意无脸）
- 下排右：BACK（背面身）

**棚拍规则**：
- 纯色冷灰棚拍背景（#606570）
- 单一侧逆光45度，无填充光，冷钢蓝色调
- 底左panel刻意无脸（方便后续编辑）
- 面板标签 + 标题

### 模板锁定铁律（角色参考图，强制）
1. **唯一标准模板**：所有角色参考图一律使用「4面板 2×2」固定模板——左上=FACE FRONT 正面脸、右上=FACE SIDE 侧面脸、左下=BODY FRONT (NO FACE) 正面身刻意无脸、右下=BACK 背面身。**禁止**"三视图+正面脸""三视图+半身侧面"等任何其他排版；生图模型自行漂移出非标准结构时，视为生成失败，重新生成。
2. **骨架逐字复用**：布局与棚拍段落（四格位置/背景/灯光/标签）为固定骨架，跨角色、跨批次、跨项目**逐字复制、一字不改**；每个角色只替换【角色描述】字段（外貌/服装/装备/姿势）。禁止每次重写布局措辞。
3. **角色一致性**：同一角色四格服装、配色、细节完全一致；同一角色在参考图与 CLIP 提示词中的色卡描述必须一致。
4. **生成后必检**：每次生成后对照下方质检清单核查布局与顺序，不合格直接重新生成，不将就。

**标准骨架（中英对照，逐字复用）**：
```
中文版：
一张角色参考图，严格的2x2四宫格布局，纯色冷灰棚拍背景(#606570)，单一侧逆光45度，冷钢蓝色调：
- 左上格：FACE FRONT 正面脸部特写，正对镜头，表情【角色表情】
- 右上格：FACE SIDE 左侧面脸部特写，同一表情
- 左下格：BODY FRONT 全身正面站姿，刻意不画脸（无脸，方便后续编辑），姿势【角色姿势】
- 右下格：BACK 全身背面站姿，同一服装
【角色描述：年龄性别/体型/发型发色/五官特征/服装材质与颜色/标志性装备】
面板标签与标题齐全；同一角色四格服装、配色、细节完全一致。
```
```
English version:
A character reference sheet, strict 2x2 grid layout, four panels, pure gray studio backdrop (#606570),
single side-backlight at 45 degrees, cold steel blue tone:
- TOP LEFT: FACE FRONT, close-up face, facing camera directly, expression: [expression]
- TOP RIGHT: FACE SIDE, left side profile close-up, same expression
- BOTTOM LEFT: BODY FRONT, full body front stance, face intentionally omitted, pose: [pose]
- BOTTOM RIGHT: BACK, full body back view, same outfit
Character description: [age/gender/build/hairstyle/facial features/outfit materials and colors/signature gear]
Panel labels and title complete; the four panels share identical outfit, colors and details.
```

### 场景参考图 = 9宫格多角度
场景参考图使用9宫格格式，展示同一场景的多角度/多时段。

## 参考图4原则（场景参考图）

1. **色卡融入叙述** — 色号不单独列出，融入描述性段落中。如"石墙#B5B0A8"写成"灰褐色石墙"
2. **PHOTO TONE一段压完** — 照片色调/光线/氛围在一段内完整描述，不分散
3. **AVOID精简+场景特定** — NOT清单每条针对本场景可能出现的具体错误，不堆通用否定词。按类别分组
4. **MATERIAL TRUTH** — 材质真实描述，写明每种材质的物理特性（粗糙/光滑/湿润/风化等）

## 9宫格4原则（场景参考图）

1. **panel视觉钩子** — 每个panel用1-2句话锚定视觉重点
2. **色卡统一PRESERVE** — 9个panel色调统一，不出现色温跳变
3. **贯穿核心概念** — 9个panel围绕同一场景的核心特征展开
4. **绝对语言** — 用绝对肯定的描述语言，不用"可能""大约""似乎"

## 角色参考图写法要点

### 影视参考锚定法
引用电影/游戏给模型文化视觉锚点。如"The Road (2009) / Snowpiercer (2013) / Medieval peasant levy"。帮助模型理解整体美学方向。

### NOT清单分类组织法
否定指令按类别分组：
- FACE: no beard mismatch, no scar on wrong side
- BODY: no modern clothing, no clean fabric
- LIGHT: no fill light, no warm tone
- FORMAT: no environment background, no single portrait

### 姿势=心理状态写法
不写物理位置描述（"手放在腰间"），写角色能量和心理状态（"重心后脚，随意权威的姿态"）。每个panel的姿势反映角色性格。

### 标志性装备=角色身份
为角色设计DNA物件——一眼能认出是谁的标志性装备。在参考图中明确锚定。

### 角色差异化设计方法
同一阵营的多个角色必须有明确的视觉区分：
- 体形差异（矮壮vs瘦高）
- 面部特征差异（疤痕位置/胡须密度/鼻型）
- 护甲差异（材质来源/覆盖范围）
- 武器差异（类型/尺寸/材质）
- 标识物差异（同一标识但佩戴方式不同——如灰布条缠不同手臂、缠绕方式不同）
- 姿态差异（重心/手持武器方式/跟随者vs领头者气质）
- 参考美学差异（引用不同的影视参考）

## 色卡规则

- 参考图提示词中保留色号（如#4A4540）
- CLIP提示词中取消色号，改用描述性语言
- 跨文件色卡一致性验证：同一角色/场景在参考图和CLIP中的色卡描述必须一致
- 冷色调场景：100%冷色零暖色
- 暖色调场景：暖光主调但需有冷色平衡

## 质检清单

- [ ] 角色参考图是否为4面板character sheet格式？（不是单张立绘！）
- [ ] 是否为唯一标准 4 面板 2×2 模板（非"三视图+正面脸/半身侧面"等漂移排版）？
- [ ] 4面板布局是否正确？（正面脸/侧面脸/正面身无脸/背面身）
- [ ] 布局骨架是否逐字复用（未改写布局/棚拍段落）？
- [ ] 棚拍背景是否为纯色冷灰？
- [ ] 是否单一侧逆光45度无填充光？
- [ ] 底左panel是否刻意无脸？
- [ ] 同一阵营多角色是否有明确视觉区分？
- [ ] 色卡是否融入叙述而非单独列出？
- [ ] AVOID清单是否场景特定？
- [ ] 影视参考是否标注？
- [ ] 面板标签和标题是否完整？
