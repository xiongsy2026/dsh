# 提示词模板（Prompt Templates）

流水线各阶段的提示词模板。`{占位符}` 由执行时填充；除用户另有指定外，产出内容为中文。

## 核心理念：剧情优先，产品植入

> 观众不为产品停留，但会为一个好故事停留到底。产品不是主角，故事才是。产品自然地出现在故事中——被角色使用、被情节需要、被情感承载——观众记住的是故事，购买的是故事里的产品。

**三条铁律：**
1. **第 1 拍不展示产品**：钩子用人物/冲突/悬念/情感抓人，产品不在前 1.2 秒出现
2. **产品随剧情自然出现**：产品被角色"需要"而登场，不是被"展示"——它解决问题、承载情感、推动转折
3. **高潮属于故事不属于产品**：最动人的时刻是人的情感，产品在那个时刻"恰好在场"

## 0. 叙事结构选择（Stage 1 前，由执行者或用户选定）

根据产品特性和视频时长，从以下结构中选一个（用户未指定时由剧本模型看图自选并注明）。

### 10s（1 段 6 拍）

| 结构 | 故事脉络 | 产品何时入场 | 适合产品 | 节拍分配 |
|---|---|---|---|---|
| **悬念揭秘** | 钩子（人物/事件）→铺垫→悬念→产品触发揭秘→情感高潮→CTA | 第 4 拍，揭秘的触发器 | 有隐藏功能/惊喜效果 | 1/1.5/1.5/2/2.5/1.5 |
| **痛点解决** | 痛点（人物的痛）→困境→挣扎→产品出现解围→满足→CTA | 第 4 拍，拯救者 | 功能型产品 | 1.5/1.5/1.5/2/2/1.5 |
| **情感故事** | 场景（人物情感）→需求→寻找→产品承载情感→高潮时刻→CTA | 第 4 拍，情感载体 | 礼品、纪念、节日 | 1.5/1.5/1.5/2.5/2/1 |
| **UGC种草** | 真实场景→人物故事→自然提及→产品融入生活→个人感受→推荐 | 第 3 拍，生活的一部分 | 博主风格 | 1.2/1.5/1.5/1.5/2.5/1.8 |
| **高级质感** | 氛围营造→环境叙事→光影铺垫→产品自然入境→定格时刻→品牌 | 第 4 拍，视觉焦点 | 高端无人物纯产品 | 1.5/1.5/1.8/2/2/1.2 |
| **对比反转** | 现状（人物的困境）→冲突→产品介入→反转→震撼→CTA | 第 3 拍，转折点 | 有明显前后对比 | 1.2/1.2/1.5/2.5/2/1.6 |

### 20s（2 段 12 拍）

20s 视频分两幕：**幕 1 讲故事+埋悬念**（10s），**幕 2 产品入场+情感高潮+收尾**（10s）。

| 结构 | 幕 1（拍 1-6） | 幕 2（拍 7-12） | 产品何时入场 |
|---|---|---|---|
| **悬念揭秘** | hook（人物悬念）→故事铺垫→人物冲突→悬念加深→留白→衔接 | 揭秘触发（产品入场）→放大→情感高潮→感动→余韵→CTA | 幕 2 第 1 拍（全局第 7 拍） |
| **痛点解决** | 痛点（人物的痛）→困境→挣扎加深→困境顶点→留白→衔接 | 产品出现解围→效果→转变→满足→推荐→CTA | 幕 2 第 1 拍 |
| **情感故事** | 场景→人物→情感需求→寻找→期待→衔接 | 产品承载→高潮时刻→感动→品牌升华→余韵→CTA | 幕 2 第 1 拍 |
| **UGC种草** | 真实分享→人物故事→生活场景→自然提及→留白→衔接 | 产品融入→惊喜→个人感受→推荐→互动→CTA | 幕 2 第 1 拍 |
| **对比反转** | 现状困境→冲突→产品介入→初步反转→留白→衔接 | 完整反转→震撼→见证→满足→CTA | 幕 1 第 3 拍（全局第 3 拍） |

节拍分配建议：
- 幕 1：1.2/1.5/1.5/1.8/2/2（故事铺垫偏快，留白+衔接给足）
- 幕 2：1.2/1.5/2.5/2/1.8/1（产品入场快切，情感高潮 2.5s，收尾干脆）

### 30s（3 段 18 拍）

30s 视频分三幕：**幕 1 讲故事**（10s），**幕 2 产品入场+转折**（10s），**幕 3 情感高潮+收尾**（10s）。

| 结构 | 幕 1（拍 1-6） | 幕 2（拍 7-12） | 幕 3（拍 13-18） | 产品何时入场 |
|---|---|---|---|---|
| **悬念揭秘** | hook→故事铺垫→人物冲突→暗示→悬念→衔接 | 产品触发揭秘→部分揭示→转折→加深→悬念升级→衔接 | 全面揭秘→情感高潮→感动→价值升华→CTA→品牌 | 幕 2 第 1 拍 |
| **痛点解决** | 痛点→困境→挣扎→挣扎顶点→留白→衔接 | 产品出现→初步效果→转变→见证→深入→衔接 | 完美效果→满足→用户证言→品牌承诺→CTA | 幕 2 第 1 拍 |
| **情感故事** | 场景→人物→需求→寻找→期待→衔接 | 产品承载→使用过程→情感积累→转折→余韵→衔接 | 高潮时刻→感动→品牌升华→CTA→收尾定格 | 幕 2 第 1 拍 |
| **UGC种草** | 真实分享→人物故事→生活场景→暗示→留白→衔接 | 产品融入→惊喜→深度体验→个人感受→悬念→衔接 | 全面推荐→互动→总结→CTA→收尾 | 幕 2 第 1 拍 |

节拍分配建议：
- 幕 1：1.2/1.5/1.5/1.5/2/2.3（引入偏快）
- 幕 2：1.3/1.5/1.5/1.7/2/2（发展段中等节奏）
- 幕 3：1.2/1.5/2.5/2/1.8/1（情感高潮 2.5s，收尾干脆）

### 40s+（4 段以上）

40s 及以上视频按「引入→发展→转折→高潮+收尾」四幕结构展开。产品入场时机灵活，但总在第 2 段或之后——第 1 段永远只讲故事。

通用规则：
- 每段 6 拍，段内自成小弧线（有起承转合）
- 全局弧线跨段展开，beat_function 从 hook 到 cta 跨 S×6 拍递进
- 段间衔接拍（6K→6K+1）画面连续，情绪可转折
- 钩子拍始终 1-1.2s，高潮拍始终 2-2.5s，收尾拍始终 1-1.5s
- **产品不在第 1 段出现**（除非 10s 单段，产品在第 4 拍入场）

## 1. 剧本撰写（gemini-3.5-flash · Stage 1）

**System：**

> 你是资深短视频编剧兼导演，精通竖屏 9:16 叙事广告创作。你的信条是：观众不为产品停留，但会为一个好故事停留到底。你写的是故事——有人物、有冲突、有情感弧线、有让人想看下去的看点。产品不是主角，只是自然出现在故事里——被角色需要、被情节推动、被情感承载。前 1.5 秒用故事钩子抓住人，不用产品。高潮属于人的情感，产品在那个时刻恰好在场。对于多段视频（20s+），你将全局叙事弧线跨段展开——每段是"一幕"，有自己的小弧线，但服务于全局起承转合。

**User：**

```
产品名：{product_name}
产品卖点：{selling_points}
画幅：9:16 竖屏，{duration} 秒，共 {total_beats} 个节拍，分 {segments} 段每段 6 拍
目标平台：{platform}（抖音/小红书/Instagram/通用；未指定则「通用」）
叙事结构：{narrative_structure}（从叙事结构表选一个；未指定则看图自选）

请为这支产品广告写剧本，严格输出以下 JSON，不要输出任何其他内容：
{
  "title": "广告标题",
  "platform": "目标平台",
  "narrative_structure": "选用的叙事结构名",
  "global_arc": "全局故事弧线（1-2句话：这是一个什么故事，从哪开始到哪结束，情感如何递进）",
  "story_hook": "故事钩子描述（1句话：第1拍用什么人物/冲突/悬念/情感抓住观众——不用产品）",
  "emotional_core": "情感共鸣核心（1句话：这个故事触动人的是什么情感——怀旧/治愈/惊喜/共鸣/向往）",
  "product_entry": "产品在故事中的入场方式（1句话：产品如何被角色/情节自然需要而出现，不是被展示）",
  "subject_bible": {
    "product": "产品外观与视觉特征（供关键帧一致性锚定用）",
    "character": "人物设定：性别、年龄段、脸型、发型、体型、服装配色款式；无人物则写「无人物」",
    "scene": "场景设定：地点、陈设、光线基调、色调",
    "tone": "整体情绪基调（如：温暖治愈/惊喜反转/高级质感/UGC真实感/紧迫感）"
  },
  "segments": [
    {
      "segment": 1,
      "phase": "本段在全局弧线中的阶段（S=1时写「完整」；S>=2时写「引入/铺垫/发展/转折/高潮/收尾」等）",
      "segment_arc": "本段小弧线描述（1句话：本段内部起承转合，同时说明如何衔接全局弧线）",
      "beats": [
        {
          "id": 1,
          "segment": 1,
          "time": "0.0-1.2s",
          "duration": 1.2,
          "beat_function": "本拍功能（hook/setup/build/suspense/foreshadow/bridge/reveal/twist/escalate/climax/emotion/satisfaction/cta/brand）",
          "story_beat": "本拍故事内容（人物在做什么、发生了什么、情感如何变化——这是叙事主体）",
          "product_presence": "本拍产品是否出现：not_present（未出现）/ natural（自然出现，被角色使用或被情节需要）/ focus（产品成为画面焦点）。前3拍默认not_present，除非叙事结构另有规定",
          "shot": "景别与角度",
          "shot_motivation": "为什么用这个景别——服务于什么情绪或叙事目的",
          "action": "画面内容与动作（以人物和故事为主语描述，产品只是画面中的一部分）",
          "emotion": "本拍情绪标签（如：好奇/期待/悬念/震撼/感动/满足）",
          "dialogue": "对白或画外音文字；无声则写「无」",
          "sound": "音乐/音效提示",
          "keyframe_prompt": "生成这一拍关键帧的完整画面描述，含主体（人物和故事场景）、动作、环境、光线、色调，60-120字。产品只在product_presence为natural/focus时出现在画面中，描述为画面的一部分而非主体"
        }
      ]
    }
  ],
  "slogan": "结尾出现的文案",
  "cta": "行动号召"
}

要求：
【剧情优先原则（最重要）】
- 这首先是一个故事，其次才是产品广告。观众因为故事停留，因为情感共鸣而购买
- story_hook：第 1 拍的钩子是人物/冲突/悬念/情感，不是产品。前 1.2 秒产品不出现
- emotional_core：必须有明确的情感共鸣点——让观众感同身受的情感（怀旧/治愈/惊喜/共鸣/向往/心疼）
- product_entry：产品是被故事"需要"才出现的——角色遇到了问题/想要表达情感/发现了什么，产品自然登场
- product_presence：前 3 拍默认 not_present（除非叙事结构表规定产品更早入场）；产品入场后从 natural 开始，只在 climax/cta 拍才 focus
- story_beat：每拍以人物和故事为主语描述，不是"产品被展示"而是"角色在做某事，产品恰好在场"
- 高潮拍的 emotion 来自人的情感（感动/惊喜/满足），不是"产品好厉害"

【全局叙事弧线（多段时不要写成 S 个独立的 10s 故事）】
- global_arc 描述整支视频的故事弧线（起承转合）
- 每段的 phase 标明本段在全局弧线中的位置
- 每段的 segment_arc 描述本段内部的小弧线，同时说明如何衔接全局弧线
- beat_function 跨段递进：S=1时 hook→setup→build→twist→climax→cta；S>=2时按叙事结构表的多段分配展开
- 情绪弧线跨全部 S×6 拍展开，不能每段重置；climax 拍（倒数第 2-3 拍）情绪强度最高

【开头钩子】
- 全局第 1 拍（id=1）的 beat_function 必须为 hook，用人物/冲突/悬念/反常画面在前 1.2 秒抓住注意力
- 多段时第 2 段及之后的第 1 拍不需要再当 hook，而是承接上一段情绪继续发展

【节拍不等分】
- 钩子拍 1-1.2s，高潮拍 2-2.5s，收尾拍 1-1.5s，衔接拍 2s
- 各拍 duration 之和必须精确等于 10s（每段）

【段间衔接】
- 每段最后一拍（id = 6K）与下一段第一拍（id = 6K+1）构成衔接对
- 衔接拍的 beat_function 标记为 bridge
- 衔接拍必须描写同一时刻的不同角度或同一动作的自然延续，画面连续不跳戏
- 段间情绪可以转折（如幕1悬念→幕2揭秘），但画面必须连续

【对白与声音】
- dialogue：UGC 风格用第一人称口语；高级质感型可写「无」用纯视觉；情感故事型可用画外音
- 多段时对白要有呼吸感：幕1铺垫对白偏多，高潮拍可静音让画面说话，收尾段对白回扣主题
- sound：标注音乐节奏变化与关键音效；高潮拍要有声音变化；段间衔接处音乐可以过渡变奏

【一致性铁律】
- 产品外观必须严格使用参考图
- 如有人物，全部 S×6 拍必须是同一个人——脸型、发型、体型、服装全程不变
- 场景与光线基调全程不变（剧情确需换景时，在 subject_bible 和 action 里显式写明，且只换一次）
- 每条 keyframe_prompt 都要复述 subject_bible 中的人物外观与场景要点，作为该帧的锚定描述

【CTA 与收尾】
- 全局最后 1-2 拍的 beat_function 为 cta/brand
- cta 须有明确行动号召；多段时只在最后一段出现，前面段不要提前 CTA
- CTA 应与故事情感自然衔接，不是生硬跳转

【平台适配】
- 抖音：节奏快，前 1 秒必须有故事钩子，对白口语化，BGM 重要
- 小红书：UGC 真实感，细节展示充分，色调温暖，像博主分享生活
- Instagram：视觉优先，对白可少，音乐与美感重要
- 通用：兼顾节奏与质感
```

## 2. 关键帧一致性条款（Stage 2，追加到每帧 prompt 末尾）

```
保持产品外观、材质、颜色、比例与参考图完全一致；人物（若有）与第 1 张参考帧中为同一人，脸型、发型、体型、服装保持不变；场景陈设、光线与色调延续参考帧，不做任何改变；画面为 9:16 竖屏构图。只改变动作与镜头位置，不改变主体身份与环境。
```

**not_present 帧额外追加（产品未登场时必加，配合「不传产品图」策略）：**

```
重要：本帧产品不出现。人物脖子、手腕、身上没有任何项链、首饰、吊坠、手链、耳环等饰品，皮肤裸露，没有佩戴任何饰品。人物服装上没有任何装饰性挂件或配件。画面中不出现任何与产品相关的物品。
```

**衔接帧额外追加（帧 6K 与帧 6K+1）：**

```
本帧为段间衔接帧，画面须与上一帧/下一帧连续——同一时刻的不同角度或同一动作的自然延续，不得跳戏。人物姿态、场景、光线须与衔接帧保持一致。
```

## 2.5 产品锁块模板（Stage 0 生成，全片复用）

> 产品外观描述只在 Stage 0 生成一次，落盘为 `product_lock_block.json`，之后所有 natural/focus 帧逐帧原样追加，**禁止逐帧改写**。剧本 `keyframe_prompt` 里产品只允许出现占位符 `the {product_ref}`。

**product_lock_block（英文，40-70 词，按产品主图实际可见事实写）：**

```
Product lock (must match the attached product image exactly): {product_ref} = {部件1：名称+形状+朝向+材质+配色}; {部件2…}; {可见文字/logo，逐字}; {相对尺寸判据}. 
```

**product_negative_block（英文，逐条列出该产品的「不许变」项）：**

```
Product negative: do NOT make {product_ref} {材质/颜色/瓶型 1}, {2}, {3} that differ from the image; do NOT add labels, text, logos or parts not visible in the reference; keep the cap ribbing, logo and proportions unchanged.
```

**富铁软糖实例（供对照，新产品必须照自己主图重写，不得套用）：**

```
Product lock: {product_ref} = NGLEED iron gummy bottle, short stout RED bottle body, RED ribbed screw cap, RED paper label with WHITE serif "NGLEED" text, heart-Fe mark, "富铁软糖 / Iron-Rich Gummy / 净含量 60g" printed text, ~10-12 cm tall.
Product negative: do NOT make the bottle amber, brown, clear, glass, plastic, taller, thinner or wider; do NOT change the cap ribbing; do NOT add or remove any label text or logo; do NOT recolor the label or the gummy.
```

**使用纪律：**
- Stage 1：剧本模型生成 `subject_bible.product` 时，必须逐字采用锁块（不得改写措辞、不得补图上看不到的细节）；每拍 `keyframe_prompt` 里产品一律写 `the {product_ref}`。
- Stage 2：生 natural/focus 帧的 prompt = `keyframe_prompt`（产品已占位）+ `product_lock_block` + `product_negative_block` + 一致性条款；not_present 帧不加锁块，只加「产品不入画」负面条款。
- 产品主图或锁块变更时，旧帧必须全部重拍（入场帧起逐帧继承），不得只改后续帧。

## 3. 运镜提示词（gemini-3.5-flash · Stage 4，多模态，逐段生成）

**System：**

> 你是专业运镜指导，为竖屏叙事广告撰写运镜提示词。你的运镜服务于故事节奏与人物情感——故事钩子用快推快拉制造冲击，情感高潮用缓推停留让情绪落地，过渡拍流畅连接。运镜跟着人物走，产品出现时不刻意推近，保持故事的自然感。多段视频的运镜有全局节奏感：铺垫段流畅推进，转折段运镜变化增多，高潮段缓推停留。

**User**（content 数组：该段 6 张关键帧 `image_url` + 以下文本）：

```
以上是第 {seg_index}/{segments} 段（{start_time}-{end_time}秒）的 6 个关键帧（按时间顺序）与剧本：
{segment_beats_json}

本段在全局弧线中的阶段：{segment_phase}
全局弧线概述：{global_arc}

请输出一段**英文**运镜提示词（150-300 words），描述这 10 秒内的镜头运动。要求：
- 运镜跟随人物和故事，不刻意推向产品；产品出现时保持镜头的自然叙事节奏
- 运镜须服务于各拍的 beat_function 与 emotion：hook 拍用快推/快拉制造冲击；climax 拍用缓推+停留让情感落地；bridge 拍用平滑过渡保持画面连续
- 运镜须匹配本段在全局弧线中的 phase：引入段流畅平稳，转折段运镜变化增多，高潮段缓推停留
- 标注每拍对应的运镜动作（推/拉/摇/移/跟/固定）与速度
- 重点帧停留时长建议
- 段间衔接自然
- **必须用英文输出**（omni 视频模型对英文 prompt 兼容性最好，中文 prompt 易触发内容审核误判）
只输出提示词本身（英文）。
{multi_segment_note}
```

**multi_segment_note**（S >= 2 时追加，S=1 留空）：

```
本段为第 {seg_index}/{segments} 段（{segment_phase}阶段），段间衔接靠关键帧画面的连续性保证，不需要额外转场特效。{seg_index>1 时追加：本段开头须衔接上一段末尾画面，运镜从上一段的结束构图自然过渡。}{seg_index<segments 时追加：本段末尾须为下一段开头留出画面连续点，运镜收束平稳。}
```

## 4. 视频提示词组装（Stage 5，每段最终 prompt）

> **提示词必须用英文组装**。`camera_prompt` 来自 Stage 4（英文输出），其余组装部分也用英文拼接。omni 视频模型对英文 prompt 兼容性最好。

```
{camera_prompt}
画面以人物和故事为主体，产品自然存在于画面中（当该拍 product_presence 为 natural/focus 时）；产品外观严格参考提供的关键帧中对应帧；人物、场景全程与关键帧保持一致，人物身份（脸型/发型/服装）与场景陈设不得中途改变；过渡自然流畅，只呈现动作与镜头运动。运镜节奏与各拍 emotion 配合：快拍快切，慢拍缓推，高潮停留。本段为{segment_phase}阶段，运镜节奏须匹配该阶段在全局弧线中的定位。
{bridge_note}
```

**bridge_note**（多段时追加）：

```
本段为第 {seg_index}/{segments} 段。{seg_index>1 时：开头须衔接上一段末尾画面。}{seg_index<segments 时：结尾须为下一段留出画面连续点。}段间过渡靠画面连续性，不加转场特效。
```

## 5. 多语言口播提示词（omni 直出口播，Stage 5）

> omni_flash-hd-10s 支持直接生成带语音的视频。在运镜提示词中加入口播描述，模型会自动生成对应语言的旁白。**注意：omni 不支持指定具体音色 ID（如 en_us_001），只能用文字描述语音特征；多段视频的声音可能不完全一致。需要精确音色时用剪映 TTS 后期配音。**

### 通用模板

**开头（全局声明，放在 prompt 第一句）：**
```
The video includes a {gender} {Language} voiceover speaking naturally throughout.
```

**每拍（在运镜描述后追加）：**
```
The {Language} narrator says: "{台词}"
```

### 各语言示例

| 语言 | 开头声明 | 每拍口播 |
|---|---|---|
| 中文（普通话） | `female Chinese Mandarin voiceover` | `The Chinese narrator says: "东西太多了。"` |
| 英语（美式） | `female American English voiceover` | `The English narrator says: "Too much clutter."` |
| 英语（英式） | `female British English voiceover` | `The English narrator says: "Too much clutter."` |
| 日语 | `female Japanese voiceover` | `The Japanese narrator says: "物が多すぎる。"` |
| 韩语 | `female Korean voiceover` | `The Korean narrator says: "물건이 너무 많아."` |
| 法语 | `female French voiceover` | `The French narrator says: "Trop de choses."` |
| 德语 | `female German voiceover` | `The German narrator says: "Zu viel Kram."` |
| 西班牙语 | `female Spanish voiceover` | `The Spanish narrator says: "Demasiado desorden."` |

### 关键要点

1. **双重强化语言**：开头用 `{gender} {Language} voiceover` 全局声明，每拍用 `The {Language} narrator says:` 再次明确，避免模型串语言。
2. **台词用目标语言文字**：中文用汉字，日语用假名/汉字，韩语用谚文，不要用拼音或罗马音。
3. **口音锁定**：英语可写 `American English` / `British English`；日语可写 `Japanese (Tokyo accent)`；中文可写 `Chinese Mandarin` / `Chinese Cantonese`。
4. **性别指定**：`female` / `male` / `young woman` / `middle-aged man` 等。
5. **语速控制**：台词总词数需适配 10 秒/段，中文约 15-20 字/10 秒，英文约 20-30 词/10 秒。超出会被截断或语速过快。
6. **多段一致性**：两段都用相同的语言描述和性别描述，声音风格会比较接近，但不保证 100% 一致。需要绝对一致时用剪映 TTS 后期配音（同一 speaker ID）。
7. **omni 不支持参考音频**：API 无 `audio_reference` 参数，无法用外部音频锁定音色。

### 完整示例（中文口播，单拍）

```
Vertical 9:16, 10 seconds. A young Chinese woman in a small cluttered apartment. The video includes a female Chinese Mandarin voiceover speaking naturally throughout.

0 to 1.5s hook: Low-angle medium shot. Quick push-in as the woman stumbles over scattered books. The Chinese narrator says: "东西太多了。"
1.5 to 3s setup: Close-up. Her hand tries to place a coffee mug on an overcrowded table. The Chinese narrator says: "咖啡都没地方放。"

Camera follows the woman. Natural warm lighting. Photorealistic. Smooth transitions. Female Chinese Mandarin voiceover, clear and natural. No text, no watermarks.
```
