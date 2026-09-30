---
name: "product-video-pipeline"
description: "Product image to vertical ad video (10s base, multi-segment for >=20s): script -> keyframes -> human review -> camera prompt -> video, via zexapi.com gateway. ffmpeg concat for multi-segment."
---

# 产品图生视频流水线（Product Video Pipeline）

将一张产品图加工成竖屏（9:16）叙事广告视频的端到端流水线，基于 zexapi.com 统一网关。

**核心理念：剧情优先，产品植入。** 观众不为产品停留，但会为一个好故事停留到底。产品不在前 1.2 秒出现，产品被故事"需要"才自然登场。高潮属于人的情感，产品在那个时刻恰好在场。

- **10 秒视频**：单段完成（6 帧 -> 1 段视频），全部流程一次走完
- **>=20 秒视频**：分段生成，每段 10 秒，段间靠衔接帧画面连续 + ffmpeg 拼接

```
产品图 ──▶ 1. 写剧本（故事优先：story_hook + emotional_core + product_entry）
              │           前 3 拍讲故事不出现产品，产品被剧情需要才入场
              ▼
           1.5. ⏸ 剧本审核（必须给用户看，标明产品在哪拍出现，通过后才继续）
              │
              ▼
           2. 逐拍生成 N×6 张关键帧（顺序执行）
              产品图 + 第1帧锚点 + 上一帧；
              product_presence 控制产品是否入画
              段间衔接帧 6K→6K+1 画面连续
              │
              ▼
           3. ⏸ 人工审核（逐帧给用户看，标注产品是否应出现，重点查产品泄露）
              │
              ▼
           4. 逐段写运镜提示词（运镜跟人物走，不刻意推向产品）
              │
              ▼
           5. 逐段提交 omni_flash-hd-10s → N 个 10s 片段
              ffmpeg concat → 完整视频
```

## 多段拼接原理（>=20s）

omni 单次上限 10 秒。更长的视频按以下规则拆段：

| 目标时长 | 段数 S | 总帧数 | 总节拍 | 幕结构 |
|---|---|---|---|---|
| 10s | 1 | 6 | 6 | 完整（1 段闭环） |
| 20s | 2 | 12 | 12 | 幕 1 铺设悬念 + 幕 2 揭秘高潮 |
| 30s | 3 | 18 | 18 | 幕 1 引入 + 幕 2 发展转折 + 幕 3 高潮收尾 |
| 40s+ | >=4 | S×6 | S×6 | 引入→发展→转折→高潮收尾（发展/转折可扩展） |

**全局叙事弧线**：多段视频不是 S 个独立的 10s 故事拼在一起，而是全局弧线跨段展开。每段是"一幕"，有自己的小弧线（起承转合），但服务于全局起承转合。剧本 JSON 的 `global_arc` 描述完整弧线，每段的 `phase` 标明在全局中的位置，`segment_arc` 描述本段小弧线。

**beat_function 跨段递进**：不再每段重复 hook→cta，而是跨 S×6 拍递进：
- S=1：hook→setup→build→twist→climax→cta
- S=2：幕1 hook→setup→build→suspense→foreshadow→bridge，幕2 reveal→twist→escalate→climax→emotion→cta
- S=3：幕1 hook→setup→build→foreshadow→suspense→bridge，幕2 reveal→escalate→twist→deepen→suspense升级→bridge，幕3 climax→emotion→satisfaction→value→cta→brand
- S>=4：新增幕用于扩展 development/transition，climax 始终在最后一段

**情绪弧线跨段**：全部 S×6 拍的 emotion 必须有起伏变化，不能每段重置；climax 拍（倒数第 2-3 拍）情绪强度最高。段间衔接处情绪可转折（如幕 1 悬念→幕 2 揭秘），但画面必须连续。

**衔接帧**：第 K 段的最后 1 帧（帧 6K）与第 K+1 段的第 1 帧（帧 6K+1）构成衔接对，beat_function 标记为 bridge。两帧必须是同一时刻的不同角度，或同一动作的自然延续——画面连续不跳戏。衔接靠 Stage 2 的参考图策略天然保证：帧 6K+1 的"上一帧"就是帧 6K，与段内其他帧的逻辑完全一致，无需特殊处理。

**拼接**：各段视频用同一模型同一参数生成，编码/分辨率/帧率一致，ffmpeg `-c copy` 直接拼接；若个别段降级导致不一致，走 re-encode 路径。

## 前置条件

1. **API Key（仅视频生成需要）**：zexapi.com 的 Bearer Key，用于 Stage 5 视频生成（omni_flash-hd-10s）。优先读环境变量 `ZEXAPI_API_KEY`，否则向用户索取。**禁止把 Key 写进任何文件。** 关键帧生成（Stage 2）默认用豆包 image_gen / image_edit，不需要 API Key。
2. **运行目录**：每次运行建 `video_runs/<run-name>/`，含子目录：
   - `frames/` — 关键帧（`frame_01.png` … `frame_{S*6:02d}.png`）
   - `frames/thumb/` — 缩样（供参考与视频提交复用）
   - `segments/` — 各段视频（`segment_1.mp4` … `segment_S.mp4`）
   - `video/` — 最终成片（单段直接存此，多段拼接后存此）
   - 剧本 `script.json` 与任务记录 `task_log.json` 存根目录
3. **语言**：剧本、关键帧 prompt 默认中文；用户另有指定则跟随。**运镜提示词必须用英文**（omni 视频模型对英文 prompt 兼容性最好，中文 prompt 容易触发内容审核误判或生成质量下降，2026-09-15 实测确认）。
4. **默认人物**：除非用户明确指定其他种族/性别，剧本人物默认设为**欧美女性**（Western/European woman）。`subject_bible.person` 中默认使用欧美外貌描述（如 fair skin, honey-blonde or brown hair, blue/green/hazel eyes 等）。
5. **默认台词**：剧本 `dialogue` 字段默认写**英文台词**，并在 Stage 5 视频生成时启用 omni 英文口播（voiceover）。用户明确要求无声或其他语言时才更改。
6. **ffmpeg**：多段拼接优先用 ffmpeg。运行前调 `ffmpeg -version` 确认可用；缺失则提示安装。**注意：Windows 上预装/便携版 ffmpeg 可能是极简构建（只有 scale/fps 滤镜，无 concat demuxer/filter，无 mpegts muxer），无法直接拼接。此时自动回退到 Python moviepy 方案**（pip install moviepy，自带完整 ffmpeg）。
7. **Python 3+**：moviepy 回退方案需要 Python；优先检测系统是否已有。
8. PowerShell 5 环境，调用规范见下节。

## HTTP 调用规范（重要）

- 全部接口 `https://zexapi.com`，鉴权头 `Authorization: Bearer <KEY>`。
- PowerShell 下**必须用 `Invoke-RestMethod` + UTF-8 字节数组传 JSON**，否则中文提示词乱码：
  ```powershell
  $bytes = [Text.Encoding]::UTF8.GetBytes($json)
  Invoke-RestMethod -Uri $uri -Method Post -Headers $h -ContentType "application/json" -Body $bytes
  ```
- 不要在 PowerShell 里用 curl.exe 发含中文的 JSON（引号转义问题）。
- **PowerShell Get-Content 巨坑（2026-09-15 实测）**：`Get-Content` 返回的不是纯字符串，而是带 PSNoteProperty 附加属性的对象（如 PSPath、PSParentPath、ReadCount 等文件系统元数据）。用 `ConvertTo-Json` 序列化时，这些属性会被全部展开写进 JSON，导致 prompt 字段从 1.7KB 膨胀到 2.5MB，触发 400 Bad Request。**解决方案：所有文件读取一律用 .NET 方法**：
  ```powershell
  # 读文本（返回纯字符串，无附加属性）
  $text = [IO.File]::ReadAllText($path, [Text.Encoding]::UTF8)
  # 读字节
  $bytes = [IO.File]::ReadAllBytes($path)
  ```
  此坑影响所有从文件读取 prompt / 剧本 / 配置后再 JSON 序列化的场景。
- 轮询统一用 `scripts/poll_task.ps1`（图片任务读 `url`、视频任务读 `video_url`，通吃，且不落盘 Key）。
- 多段拼接用 `scripts/concat_segments.ps1`（检测 ffmpeg 能力，优先 ffmpeg，失败自动回退 moviepy）。

## Stage 0 — 产品图接收

- 要求：单一产品主体清晰，JPEG/PNG/WEBP，<=10MB，白底或简洁背景最佳。
- 询问目标时长（默认 10 秒；用户要更长则进入多段流程）。
- 计算段数 `S = ceil(duration / 10)`，总帧数 `total_frames = S * 6`。
- 制作 Base64 传输副本：最长边缩到 <=640px 的 JPEG（约 30KB），原图保留。
- 记录产品名与卖点（用户口述；缺失时由剧本模型看图拟写并请用户确认）。
- **生成产品锁块（强制，一次性）**：
  - 固定字段：`product_lock_block`（英文，40-70 词，逐部件写明：形状/部件名称/朝向/材质/配色/可见文字），作为全 run **唯一**产品外观描述。
  - 固定负面字段：`product_negative_block`（英文，明确禁止把产品改成原图之外的瓶型/颜色/材质/标签/文字，逐条列出该产品的「不许变」项，如 `no amber glass, no clear bottle, no taller/thinner bottle`）。
  - 规则：
    - 只能来自产品主图（+ 细节图）的可见事实，看不清的一律进 `uncertain`，不写进锁块。
    - 一旦写入 run 根目录（`product_lock_block.json`），后续 Stage 1 / Stage 2 / Stage 5 **不得**再改写产品外观描述；要改必须回到 Stage 0 重新提取并经用户确认。
    - 锁块是「占位描述」：Stage 1 剧本里产品只用占位符 `{product_ref}` 引用，**禁止**在 `keyframe_prompt` 里自由发挥产品颜色/材质/瓶型/标签/logo。

## Stage 1 — 剧本（gemini-3.5-flash）

- `POST /v1/chat/completions`，model `gemini-3.5-flash`。
- 用 `references/prompts.md` 的「叙事结构选择」表确定结构（按 S 段选对应表，用户指定或模型自选），再用「剧本撰写」模板，要求严格输出 JSON。
- **剧情优先原则（核心）**：这首先是一个故事，其次才是产品广告。观众因为故事停留，因为情感共鸣而购买。产品不在前 1.2 秒出现，产品被故事"需要"才自然登场。
- **默认人物（欧美女性）**：除非用户明确指定其他种族/性别，`subject_bible.person` 默认设为欧美女性，使用欧美外貌特征描述（如 fair skin, honey-blonde/brown hair, blue/green/hazel eyes, natural makeup 等）。用户指定亚洲人/其他人种时才覆盖默认值。
- **默认欧美剧情风格**：剧本叙事、场景、情感表达、人物关系均需符合欧美文化语境。包括：欧美家居环境（公寓/loft/别墅/花园等）、欧美节日与生活场景（圣诞节/情人节/周末brunch/road trip等）、欧美情感表达方式（直接/外放/拥抱/亲吻额头等）、欧美常见剧情桥段（anniversary surprise/secret admirer/late-night discovery/proposal等）。避免亚洲特有剧情元素（如春节/中秋/婆媳关系/含蓄内敛表达等），除非用户明确要求。
- **默认英文台词**：`dialogue` 字段默认写英文台词（非「无」），用于 Stage 5 omni 英文口播。台词应简短自然，符合角色情感，每拍 5-15 词。台词风格须符合欧美口语习惯（如 "Oh my god, this is beautiful" / "I can't believe you remembered" / "This is the most perfect gift" 等），避免翻译腔。用户明确要求无声或其他语言时才更改。
- **全局叙事弧线（S>=2 关键）**：JSON 必须含 `global_arc`（1-2 句话描述故事弧线）。多段时不是 S 个独立故事，而是全局弧线跨段展开。
- **故事三要素**（JSON 必含）：
  - `story_hook`：故事钩子（第 1 拍用什么人物/冲突/悬念抓人——不用产品）
  - `emotional_core`：情感共鸣核心（触动人的是什么情感——怀旧/治愈/惊喜/共鸣/向往）
  - `product_entry`：产品入场方式（产品如何被角色/情节自然需要而出现）
- **段结构**：JSON 的 `segments` 数组含 S 个段对象，每段含 `phase`、`segment_arc`、`beats`（6 拍）。
- **每拍字段**：
  - `beat_function`：跨段递进（S=1: hook→cta；S>=2 按多段结构表分配）
  - `story_beat`：本拍故事内容（以人物和故事为主语）
  - `product_presence`：not_present / natural / focus（前 3 拍默认 not_present，产品入场后从 natural 开始，只在 climax/cta 拍才 focus）
  - `shot_motivation`：景别选择的叙事目的
  - `emotion`：本拍情绪标签
  - `dialogue`：对白/画外音（无声写「无」）
  - `sound`：音乐/音效提示
  - `duration`：拍时长（不等分，钩子短/高潮长/衔接 2s）
- **开头钩子**：全局第 1 拍 `beat_function` 必须为 hook，用人物/冲突/悬念/反常画面；多段时第 2 段及之后第 1 拍承接上一段。
- **情绪弧线跨段**：全部 S×6 拍 `emotion` 必须有起伏，不能每段重置；climax 拍（倒数第 2-3 拍）情绪强度最高。高潮的 emotion 来自人的情感（感动/惊喜/满足），不是"产品好厉害"。
- **节拍不等分**：各拍 `duration` 之和精确等于 10s（每段）；钩子拍 1-1.2s，高潮拍 2-2.5s，收尾拍 1-1.5s，衔接拍 2s。
- **衔接拍**：每段最后一拍（id = 6K）与下一段第一拍（id = 6K+1）beat_function 标记为 bridge，画面必须连续。
- **CTA**：只在全局最后 1-2 拍出现 cta/brand，前面段不要提前 CTA。CTA 与故事情感自然衔接。
- **产品占位符规则（强制，8765 运行时会兜底）**：
  - 剧本 JSON 中 `subject_bible.product` 只写产品**名称与用途占位**（如 `the {product_ref} bottle`），不写外观细节；`subject_bible` 应带 `product_ref` 字段（与 `product_card.product_ref` 一致）。
  - 每拍 `keyframe_prompt` 里凡涉及产品，一律写 `the {product_ref}`，**禁止**描述产品颜色、材质、瓶型、标签文字、logo（这些由 Stage 0 锁块负责）。
  - 校验：Stage 1 校验器需扫描全部 `keyframe_prompt`，出现产品外观词（颜色/材质/瓶型/logo 等）即报错打回。
  - **8765 运行时兜底（prompts.py）**：即使剧本里漏写了外观词，`assemble_frame_prompt` 会先调用 `_sanitize_keyframe_product_text` 把产品外观词（`amber glass bottle`/`clear plastic bottle`/`red label`/品牌名 NGLEED 整句 等）替换成 `product_ref` 占位，再拼 `product_lock_block` + `product_negative_block`。这是防止 keyframe 自由描述与 lock block 打架的最后防线；但**不能**替代 Stage 1 校验——校验要在剧本阶段就拦住，不要让兜底去洗脏数据。
- 校验：S×6 拍时间连续覆盖 0-{duration}s；每拍含全部必填字段；`keyframe_prompt` 中文 60-120 字、相邻帧动作差异明显但不跳戏；全部拍的人物外观、服装、场景不得随拍变化（换景/换装需在剧本里显式作为剧情并只出现一次）；每段节拍 duration 之和 = 10s；product_presence 与叙事结构表的产品入场时机一致；story_hook 不涉及产品。
- 校验失败把错误喂回模型重生成，最多 2 次。

## Stage 1.5 — 剧本审核检查点（强制，不可跳过）

**剧本写完后必须先交给用户审核，通过后才能开始生成关键帧。**

- 将剧本 JSON 的关键信息以人类可读的形式呈现给用户：
  - `global_arc`：全局叙事弧线
  - `story_hook` / `emotional_core` / `product_entry`：故事三要素
  - 按段/拍列表展示，每拍显示：拍号、`beat_function`、`story_beat`、`product_presence`、`emotion`、`duration`
  - **特别标注 product_presence 变化点**：明确告诉用户"产品从第 X 拍开始出现（natural/focus）"，之前所有拍为 not_present
- 呈现时用表格或分拍列表，让用户一眼看出产品在哪帧出现、哪帧不出现
- **必须等用户明确表示通过**（如「可以/通过/继续」）才能进入 Stage 2
- 用户要求修改时调整剧本后重新提交审核

## Stage 2 — 关键帧（顺序执行）

> **用户指定默认方案（2026-09-21 实测更新）**：关键帧生图用 **gflow-cli 的 `nano-pro`（Nano Banana Pro / GEM_PIX_2）**，不用豆包 image_gen/image_edit，也**不用 zexapi 生图**。
>
> **可执行文件**：`C:\Users\Administrator\.local\bin\gflow-cli.exe`。**必须显式传 `--project <id>`**（不传会走 createProject → 401 AuthExpired）。
>
> **两种模式（实测）**：
> - 文生图 t2i（无参考，首帧/纯场景）：
>   `gflow-cli image t2i "<中文 prompt>" --model nano-pro --aspect 9:16 --project <id> --out <run>/frames --json`
> - 图生图 i2i（锁脸/锁产品，本流水线主力）：
>   `gflow-cli image i2i "<中文 prompt>" --ref <本地参考图路径> --model nano-pro --aspect 9:16 --project <id> --out <run>/frames --json`
>
> **i2i 参考图硬规则（2026-09-21 flow.google.com 实测，必记）**：
> 1. **`--ref` 只接受本地文件路径，且只接受一个**。传第二个 `--ref`（如 `--ref 锚点 --ref 上一帧`）会报 `ReferenceNotFoundError`。动作衔接靠 prompt 描述，不要堆多张参考图。
> 2. **不要传媒体 UUID 或 `@Name` 做 `--ref`**——迁移后前端不支持，会报 `FlowHostMigratedError`。本地文件自动上传（日志出现 `migrated.frame_uploaded` + `media_id`）。
> 3. **Flow 已从 labs.google 迁到 flow.google.com**。报 `FlowHostMigratedError` 时设 `GFLOW_CLI_FLOW_HOST=flow.google.com`（默认 auto 已处理）。
> 4. 产物在 `--out` 目录（`<media_id>_<n>.jpg`）。**抓最新文件时按 `LastWriteTime` 且限定近几分钟内**——i2i 失败不删旧文件，否则会把上一张误拷成新帧。
> 5. **人锚策略**：先 t2i 出一张用户批准的人物锚点图，后续每帧 i2i 都 `--ref <这张锚点图>`，prompt 写「保持与参考图同一人、同一套穿搭、同一场景，只改变动作」。脸/穿搭全程锁定。
> - **前置**：gflow-cli 需先 `gflow auth login` 完成 Google Flow 登录（等到 Flow 编辑器/输入框加载，只登录 Google 不算数）；`gflow credits list` 报 authentication failed 即需重登。
> - **备选**：gflow 不可用时回退豆包 `image_gen`（首帧）/ `image_edit`（后续帧）。

对节拍 i=1..S*6 依次生成（等上一帧完成再发下一帧）：

- **第 1 帧**：用 `image_gen` 文生图，prompt 为该拍 `keyframe_prompt` + 主体设定摘要，尺寸选 9:16 竖屏。
- **第 2 帧及以后**：用 `image_edit` 图生图，传入参考图（按下方参考图策略），prompt 为该拍 `keyframe_prompt` + 产品特征清单 + 一致性条款。
- **参考图策略（重要：产品没登场的帧不给模型看产品图）**：
  - **核心原则**：图生图模型的视觉参考权重远大于文字描述。传了产品图，模型就会不自觉地把产品画进去（哪怕 prompt 写 not_present）。因此 **product_presence = not_present 的帧，参考图中不得包含产品图**。
  - 按帧分组：
    - **not_present 帧**（产品未登场，如前 3 拍）：
      - 第 1 帧：**不传产品图**，靠 prompt 描述人物和场景。（注：首帧无身份锚点，人物一致性靠 prompt 强约束）
      - 第 2 帧：`[第1帧缩样]`（人物+场景锚点，不带产品）
      - 第 3 至最后一个 not_present 帧：`[第1帧缩样, 上一帧缩样]`（人物锚点 + 动作衔接，均不带产品）
    - **入场帧**（product_presence 从 not_present 变为 natural 的那一拍，产品首次出现）：
      - `[产品图, 第1帧缩样, 上一帧缩样]`（首次引入产品图，产品一致性靠产品图 + 人物锚点双重保证）
    - **natural/focus 帧**（产品登场后）：
      - `[产品图, 第1帧缩样, 上一帧缩样]`（产品保真 + 人物锚点 + 动作衔接）
  - 多段时帧 6K+1（下一段第 1 帧）的"上一帧"天然是帧 6K（上段末帧），无需额外处理。
  - **多段首帧处理**：如果某段第 1 拍 product_presence 为 not_present（极少见，通常产品已登场），则该帧不传产品图，只传上一段末帧 + 第 1 帧锚点。
- **产品登场帧一致性强化（natural/focus 帧，核心）**：产品一旦出现，每一帧的产品形态必须与产品原图高度一致，帧间也必须一致。从三个层面同时控制：
  1. **参考图层面**（最有效）：入场帧起每帧都传产品原图 + 上一帧缩样 + 第1帧人锚。产品原图保证"长什么样"，上一帧保证"跟上一帧一样"。**重拍某帧时，必须也传产品原图，不能只传上一帧**——否则产品会逐渐漂移。
  2. **prompt 层面（产品占位，禁止逐帧改写）**：
     - 每一帧的 `keyframe_prompt` 里产品只出现占位符 `the {product_ref}`，不写产品外观。
     - 生帧时统一追加 Stage 0 的 **`product_lock_block` + `product_negative_block`**（逐帧原样使用，不随帧变化措辞），再追加本帧动作/景别描述。
     - **禁止**把产品外观写进 `keyframe_prompt`（如 `amber glass bottle` / `clear plastic bottle` / `red label` 这类词一旦出现在 keyframe_prompt 里，就和参考图打架，导致模型画成中间态）。产品描述只允许出现在锁块里，且锁块一次生成、全片复用。
     - 锁块示例（按产品实际写，不要套模板词）：
       ```
       Product lock (must match the attached product image exactly): {product_ref} = a short stout RED bottle, RED ribbed screw cap, RED label with WHITE text and heart-Fe mark, standard 60g supplement sizing.
       Product negative: do NOT make it amber, glass, clear, plastic, brown, taller, thinner or wider; do NOT add labels or text not in the image; do NOT change the cap ribbing or logo.
       ```
  3. **角度与尺寸约束**：
     - **角度一致性**：产品在不同帧里可以有不同角度（正面/侧面/倾斜），但变化必须符合物理规律（被手转动/镜头移动），不能突然翻转。相邻帧产品角度差异 ≤ 45°。
     - **尺寸比例一致性**：产品相对于手/脖子的大小必须一致。用"手持约占手指宽度的 X 倍"或"佩戴时吊坠长度约为锁骨到下巴的 1/3"等相对描述锚定尺寸。
     - **颜色材质一致性**：金属色（亮银/925银）、宝石色、表面质感（抛光/磨砂）逐帧一致。prompt 中固定写材质描述，不随帧变化。
- **入场帧是产品锚点，必须严卡**：产品首次出现的那一帧（从 not_present 到 natural 的转折点）是后续所有帧的"产品基准帧"。这帧必须：产品完整无裁切、角度与产品原图一致、尺寸比例正确。入场帧不合格立即重拍，不带病进入后续帧。
- `prompt`：该拍 `keyframe_prompt`（产品已用 `{product_ref}` 占位）+ 该拍的主体设定摘要（人物外观/服装/场景，来自 `subject_bible`）+ **Stage 0 的 `product_lock_block` 与 `product_negative_block`（natural/focus 帧必加，逐帧原样）** + `references/prompts.md` 的「关键帧一致性条款」。
- **not_present 帧**：不追加任何产品描述与锁块，只追加负面约束（产品/包装/瓶子/糖果/相关物一律不入画）。
- **product_presence 控制 + 负面 prompt 强化**：
  - `not_present`：prompt 不描述产品，**追加强负面描述**：「人物脖子、手腕、身上没有任何项链、首饰、吊坠等饰品，皮肤裸露，没有佩戴任何饰品」。只用文字不够，必须配合「不传产品图」策略。
  - `natural`：产品作为画面的一部分自然出现（被角色使用/拿在手里/放在桌上）。
  - `focus`：产品成为画面焦点（但仍服务于情感，不是硬展示）。
  - keyframe_prompt 已按 product_presence 规则编写，执行时额外追加负面 prompt。
- 衔接帧（6K、6K+1）的 prompt 额外追加：`「本帧为段间衔接帧，画面须与上一帧/下一帧连续，同一时刻或连续动作，不得跳戏。」`
- 豆包工具生成的图片直接保存到 `frames/frame_{i:02d}.png`（image_gen / image_edit 返回图片文件）。
- 每帧产出后做 <=640px 缩样存 `frames/thumb/`，供下一帧参考与视频提交复用；第 1 帧缩样全流程保留。
- **关键帧画面质量控制（生成前 prompt 层面）**——防止"尺寸不对"和"不该出现的东西出现"：
  - **产品完整性控制**：当产品在画面中出现（natural/focus）时，prompt 必须明确写出产品完整展示的要求，例如：「the ENTIRE pendant is FULLY VISIBLE - complete crescent moon, cat silhouette, black sphere, star charm, and chain all clearly shown, not cropped, not partially hidden」。只写「手持坠链」容易只画出局部。
  - **产品占位符纪律**：`keyframe_prompt` 里出现产品必须写 `the {product_ref}`，不得带任何外观形容词。外观只由 `product_lock_block` 表达；如果锁块里没有某个部件，就不许在 keyframe_prompt 里补。
  - **产品尺寸/比例控制**：用景别+位置双重约束。例如「held at chest level in medium shot」比「close-up of pendant」更可控。close-up 容易比例失真，medium shot 更接近真实佩戴/握持尺寸。
  - **不该出现的元素（负面约束）**：
    - not_present 帧：不传产品图 + 强负面 prompt（脖子/手腕/身上没有任何项链首饰）
    - 纯人像帧：追加「NO text, no words, no slogans, no overlays, no graphics, no watermarks anywhere in the image」
    - 室内场景：注意控制不要出现多余文字（如蜡烛标签、书本封面文字），必要时加负面描述
  - **多余手/手指问题**：图生图常见手部畸形。prompt 中写「natural hands with correct anatomy」帮助有限，更有效的是减少手的戏份或用物体遮挡。
- **跨帧一致性自查（逐帧 10 项）**（每帧下载后逐项目检，不合格立即重拍该帧，不带病进入下一帧）：
  - **产品完整性**（natural/focus 帧）：产品是否完整展示——关键部件是否全部可见，有无裁切或缺损
  - **产品形态一致性**（natural/focus 帧）：形状/结构/部件位置与产品原图一致（如新月开口方向、猫的姿势、球体位置、星星位置）
  - **产品配色材质一致性**（natural/focus 帧）：金属色、宝石色、表面质感（抛光/磨砂/镶钻）逐帧一致，与产品原图一致
  - **产品尺寸比例一致性**（natural/focus 帧）：产品相对于手/身体的大小比例合理且逐帧一致，不会这帧大那帧小
  - **产品角度合理性**（natural/focus 帧）：相邻帧产品角度变化自然（≤45°），不突然翻转或变形
  - **产品泄露检查**（not_present 帧）：画面中是否完全没有产品——脖子、手腕、桌面、背景都没有产品或产品部件
  - **人物一致性**：脸型、发型、体型、服装与第 1 帧一致（耳钉等小配饰也要一致）
  - **场景一致性**：地点、陈设、光线色调与第 1 帧一致（剧本显式换景除外）
  - **多余元素检查**：是否出现了不该出现的东西——文字叠加、水印、多余的手、畸形手指、乱码文字、多余物品
  - **衔接帧连续性**（帧 6K 与 6K+1）：画面是否连续——动作衔接、场景一致、光线匹配
- 记录每帧的生成方式（豆包 image_gen / image_edit / zexapi）和 prompt。

### 备选：zexapi 生图（nano_banana_pro-2K）

当豆包工具不可用、产品一致性要求极高、或需要批量自动化时，用 zexapi.com 的 `nano_banana_pro-2K`：

- `POST /v1/videos`，model `nano_banana_pro-2K`，`aspect_ratio: "9:16"`，`image_size: "1K"`。
- `images` 参数必须是字符串数组（base64 data URI），不能是对象数组 `{"url": "..."}`。
- 轮询用 `scripts/poll_task.ps1`；completed 后立即下载（URL 仅 5 小时有效）。
- 参考图策略与上方一致（not_present 帧不传产品图，入场帧起传产品图+第1帧+上一帧）。
- 记录每帧任务返回的原始 OSS `url`（Stage 5 可直接复用，免重复上传）。

## Stage 3 — 人工审核检查点（强制，不可跳过）

**关键帧全部生成后必须逐帧交给用户审核，通过后才能进入视频生成。**

- 将全部 S×6 张关键帧逐帧呈现给用户，每帧附标注：
  - 帧号 + `beat_function` + `product_presence` 状态标签（**not_present 帧标注"⚠ 产品不应出现"，natural/focus 帧标注"✓ 产品应出现"**）
  - `story_beat`：本帧剧情说明
  - `shot_motivation`：景别说明
- **逐帧重点核对**：
  - **not_present 帧产品泄露检查（最高优先）**：画面中是否完全没有产品 — 脖子、手腕、桌面、背景都没有产品或产品部件。一旦发现 not_present 帧出现产品，立即标记为不合格并重拍。
  - **natural/focus 帧产品完整性**：产品完整无裁切，关键部件全部可见，与产品原图一致
  - 人物身份一致性（脸型/发型/服装）、场景一致性
  - **多段时额外核对衔接帧画面是否连续**
- 段间标注「衔接帧 6K→6K+1」，提醒用户注意画面连续性
- **必须等用户明确表示通过**（如「可以/通过/继续」）才能进入 Stage 4
- 用户要求修改时只重生成受影响的帧（如「重拍第 3 帧，改成…」）；重拍仍走 Stage 2 的三层参考图策略与一致性自查
- 审核意见与重拍记录写入运行目录

## Stage 4 — 运镜提示词（gemini-3.5-flash，多模态，逐段生成）

- **逐段独立调用**：对每段 K=1..S，`POST /v1/chat/completions`，content 中该段的 6 张关键帧以 `image_url`（base64 data URI）传入，附该段对应的剧本 beats、`segment_phase` 和 `global_arc`。
- 用 `references/prompts.md` 的「运镜提示词」模板，**输出英文 150-300 字**（omni 视频模型对英文 prompt 兼容性最好，中文 prompt 易触发内容审核误判，2026-09-15 实测确认）。
- 运镜须匹配本段 phase：引入段流畅平稳，转折段运镜变化增多，高潮段缓推停留；衔接段平滑过渡。
- 按「视频提示词组装」模板拼出该段最终视频提示词（含 segment_phase 定位）。
- S 段共 S 次 chat 调用，产出 S 个独立运镜提示词。

## Stage 5 — 视频（omni_flash-hd-10s + ffmpeg concat）

### 5a — 逐段生成

对每段 K=1..S：

- `POST /v1/videos`，model `omni_flash-hd-10s`，`size: "1080x1920"`，`prompt` 为该段组装好的**英文**提示词。**注意参数名是 `size`（值如 "1080x1920"），不是 `ratio`**。
- **prompt 必须用英文**：omni 视频模型对英文 prompt 兼容性最好，中文 prompt 易触发内容审核误判或被拒（2026-09-15 实测：中文运镜词被 omni 内容审核拦截，同样的剧情换成英文运镜词后通过）。
- `images`：6 张关键帧，按节拍顺序传入。omni 接口 images 上限 7 张。
  - **有产品出现的段（含 natural/focus 帧）**：6 张关键帧 + 1 张产品图 = 7 张参考图。同时在 prompt 中写明产品外观描述（材质、尺寸、结构、关键部件），确保模型生成的产品形态与原图一致。
  - **无产品的段（全部 not_present 帧）**：仅传 6 张关键帧，不加产品图。prompt 中不描述产品。
  - **优先用 Stage 2 记录的 OSS 直链**（5h 有效期内），body 最小，提交最快。
  - **过期或无 URL 时传 Base64 data URI**：用 `frames/thumb/` 下的 640px 缩略图（约 500KB/张，6 张合计 ~4MB），不要用原图（1K 图可能 3-6MB/张，6 张合计 20-35MB，易超时或被拒）。
  - Base64 data URI 格式：`data:image/png;base64,<base64_string>`（omni 接口已实测支持）。
- 轮询参数 `-IntervalSec 10 -TimeoutSec 900`；完成即下载 `video_url` 存 `segments/segment_K.mp4`。
- **降级链**（逐级尝试，失败原因如实报告用户）：
  1. `1080x1920` 被拒 -> 改 `720x1280` + `omni_flash-10s`
  2. omni 全系 503 -> 报告渠道故障，可建议 `veo_3_1` 系列替代（需用户确认）
- 记录每段任务 ID、耗时、实际分辨率。

### 5b — 英文口播（omni 直出，默认启用）

omni_flash-hd-10s 支持直接生成带语音的视频。**默认启用英文口播**：在运镜提示词中加入英文口播描述，模型会自动生成英文旁白。用户明确要求无声或其他语言时才关闭或切换。

- **写法**：开头声明 `The video includes a {gender} {Language} voiceover speaking naturally throughout.`，每拍追加 `The {Language} narrator says: "{台词}"`。
- **支持语言**：中文（Mandarin/Cantonese）、英语（American/British）、日语、韩语、法语、德语、西班牙语等。
- **限制**：omni 不支持指定具体音色 ID（如 en_us_001），只能用文字描述语音特征；多段视频声音可能不完全一致；不支持参考音频锁定音色。
- **需要精确音色时**：用剪映云端 TTS（SAMI 引擎）后期配音，同一 speaker ID 生成全部台词，声音完全一致。
- **详细模板与各语言示例**：见 `references/prompts.md` 第 5 节「多语言口播提示词」。

### 5c — 拼接（S >= 2 时）

- 调 `scripts/concat_segments.ps1 -SegmentsDir segments/ -OutputPath video/final.mp4`。
- **拼接策略（两级回退）**：
  1. **ffmpeg concat demuxer + -c copy**（首选，秒级无损）：要求各段编码/分辨率/帧率完全一致，且 ffmpeg 支持 concat demuxer。
  2. **moviepy 拼接**（回退方案）：当系统 ffmpeg 是极简构建（无 concat demuxer/filter）时，自动用 Python moviepy（`pip install moviepy`，自带完整 ffmpeg binary）做 re-encode 拼接。质量略降但保证可用。
- S=1 时跳过拼接，直接将 `segments/segment_1.mp4` 复制到 `video/final.mp4`。

### 5d — 去水印（omni 生成视频后处理）

omni_flash-hd-10s 生成的视频右下角带银色四角星水印（约 50px，位置约 x=900-990, y=1690-1800），必须去除后交付。

- **工具**：Python OpenCV `cv2.inpaint()`，需 `pip install opencv-python`。
- **算法**：`cv2.INPAINT_NS`（Navier-Stokes），半径 7，效果优于 TELEA。
- **水印区域**（1080x1920 视频）：`x=880, y=1690, w=110, h=110`（覆盖银色四角星 + 余量）。
- **处理流程**：
  1. 逐帧读取视频段（`cv2.VideoCapture`）
  2. 创建固定 mask（水印区域填白）
  3. 逐帧 `cv2.inpaint(frame, mask, 7, cv2.INPAINT_NS)`
  4. 写入清洁视频段（`cv2.VideoWriter`，四cc `mp4v`）
  5. 用 moviepy 合并清洁视频 + 原始音频（`VideoFileClip(clean).with_audio(VideoFileClip(orig).audio)`）
  6. 拼接清洁段为最终成片
- **注意**：OpenCV VideoWriter 不保留音频，必须从原始视频段提取音频再合并。
- **脚本模板**：
  ```python
  import cv2, numpy as np
  WM_X, WM_Y, WM_W, WM_H = 880, 1690, 110, 110
  mask = np.zeros((h, w), dtype=np.uint8)
  mask[WM_Y:WM_Y+WM_H, WM_X:WM_X+WM_W] = 255
  frame_clean = cv2.inpaint(frame, mask, 7, cv2.INPAINT_NS)
  ```

## Stage 6 — 交付与自查

- 汇总输出：最终成片路径 + 各段路径 + 全部帧路径 + 剧本 + 各任务 ID 与耗时 + 实际规格（分辨率/时长/段数）。
- 自查清单：
  - 成片可播放，总时长符合预期（每段 10s，总 S×10s）
  - 帧序正确，运镜与剧本吻合
  - 产品外观、人物身份、场景跨帧一致（对照第 1 帧）
  - **产品完整性**：natural/focus 帧中产品完整无裁切，关键部件全部可见
  - **产品尺寸比例**：产品在画面中尺寸合理，符合真实佩戴/握持比例
  - **产品零泄露**：not_present 帧（前 3 拍等）中完全看不到产品，脖子/手腕/背景干净
  - **无多余元素**：没有不该出现的文字叠加、乱码文字、畸形手指、多余物品
  - **无水印**：确认 omni 银色四角星水印已去除（Stage 5d），右下角无残留
  - **多段：段间衔接处无跳戏/无黑帧/无闪屏**（对照帧 6K 与帧 6K+1）
  - 无遗留临时 Key

## 工具速查

| 用途 | 默认工具 | 备选（zexapi.com） | 关键参数 |
|---|---|---|---|
| 文本/剧本 | 豆包对话 | `POST /v1/chat/completions` gemini-3.5-flash | messages |
| 关键帧（首帧） | 豆包 `image_gen` 文生图 | `POST /v1/videos` nano_banana_pro-2K | prompt / 9:16 |
| 关键帧（后续帧） | 豆包 `image_edit` 图生图 | `POST /v1/videos` nano_banana_pro-2K | prompt / images(<=8) / aspect_ratio / image_size |
| 视频生成 | zexapi `omni_flash-hd-10s`（豆包无视频生成） | — | prompt / images(<=7) / size |
| 任务轮询 | — | `GET /v1/videos/{task_id}` | status / progress |

> **关键帧默认用豆包 image_gen / image_edit**，无需 API Key，产品一致性靠参考图策略保证。zexapi nano_banana_pro-2K 仅在豆包工具不可用或需要批量自动化时作为备选。视频生成必须用 zexapi omni（豆包无视频生成能力）。

## 成本估算

| 时长 | chat 调用 | 图片生成 | 视频生成 | ffmpeg | 约计 |
|---|---|---|---|---|---|
## 成本估算

> 关键帧默认用豆包 image_gen / image_edit（免费），仅视频生成消耗 zexapi 额度。若用 zexapi 生图则另计。

| 时长 | chat 调用 | 关键帧（豆包，免费） | 视频生成（zexapi） | ffmpeg | 约计 |
|---|---|---|---|---|---|
| 10s | 2 | 6 | 1 | 无 | 基准 |
| 20s | 3 | 12 | 2 | 1 次 | ~2.2x |
| 30s | 4 | 18 | 3 | 1 次 | ~3.3x |
| 通用 | S+1 | S*6 | S | 1 次（S>=2） | — |

视频生成调用真实计费；关键帧用豆包工具无额外 API 费用。

## 已知坑与实测结论（2026-09-15 本工作区 E2E 验证）

- 图片任务 `object` 实际返回 `image`（文档写 `video`）；判断任务类型用 model 名，别用 object。
- 响应同时含 `id` 与 `task_id`（同值），任取其一。
- nano_banana_pro-2K 轮询结果字段可能返回 `url` 或 `image_url`，poll_task.ps1 已兼容两者（先查 `url`，再查 `image_url`）。
- nano_banana_pro-2K 必须带 `image_size: "1K"` 参数，否则可能报错或输出尺寸异常。
- nano_banana_pro-2K 也可能返回 `status: "succeeded"`（而非 `completed`），poll_task.ps1 需兼容（已在已知坑中记录）。
- 图片/视频 URL 均只保 5 小时，完成即下载。
- 图片模型 flare / sunburst 系列渠道 503 不可用；可用档：nano_banana_pro-2K（默认）/ nano_banana_pro-1K / gpt-image-2 / gpt-image-2.5 / gpt-image-2-2K。
- 视频任务运行态文档写 `processing`，图片任务为 `in_progress`；轮询只认终态 completed / failed（nano_banana_pro 也可能返回 succeeded）。
- `omni_flash-hd-10s` 已在 `/v1/models` 在列但文档未更新；`1080x1920` 尺寸于首次运行实测验证，失败走降级链。
- **omni 视频接口参数名是 `size` 不是 `ratio`**（2026-09-15 实测）：传 `ratio: "9:16"` 会 400 Bad Request，正确参数是 `size: "1080x1920"`。图片生成接口（nano_banana 等）用的是 `aspect_ratio`，两者参数名不同，不要混淆。
- **omni 接口支持 base64 data URI**（2026-09-15 实测）：`images` 数组可以传 URL，也可以传 `data:image/png;base64,...`。无 URL 时直接用本地文件 base64 提交即可。
- **Base64 提交建议用缩略图**（2026-09-15 实测）：6 张 1K 原图 base64 后 body 可能达 20-35MB，易超时或被拒；用 640px 缩略图（~500KB/张）body 约 3-4MB，稳定提交。omni 生成视频的质量主要靠模型插值，参考图 640px 足够。
- **PowerShell Get-Content JSON 膨胀巨坑**（2026-09-15 实测）：`Get-Content` 返回的字符串对象带 PSNoteProperty 附加属性（PSPath、PSParentPath、ReadCount 等），`ConvertTo-Json` 会把这些属性全部序列化，导致 1.7KB 的 prompt 膨胀成 2.5MB 的 JSON，触发 400。**所有文件读取用 `[IO.File]::ReadAllText()` / `[IO.File]::ReadAllBytes()`**，不要用 `Get-Content`。
- **Windows 预装 ffmpeg 可能是极简构建**（2026-09-15 实测）：只有 `scale` 和 `fps` 两个滤镜，无 concat demuxer/filter，无 mpegts muxer，无法做视频拼接。检测方式：`ffmpeg -filters | findstr concat`，无输出即为极简版。**回退方案**：用 Python moviepy（`pip install moviepy`，自带完整 ffmpeg binary）做拼接。
- ffmpeg concat 要求各段编码一致；同模型同参数通常一致，但若某段走了降级链（如 720x1280），需 re-encode 统一再拼。
- **not_present 帧的产品泄露问题（2026-09-15 月猫坠链 20s 实测）**：图生图模型的视觉参考权重远大于文字。即使 prompt 写了「产品不出现」，只要参考图里有产品图，模型就会不自觉地把产品画进去（如人物脖子上多了项链）。**解决方案**：not_present 帧的参考图里绝对不能包含产品图，改用人锚帧（第 1 帧缩样）做身份参考，同时 prompt 追加强负面描述（「脖子上没有项链首饰」）。入场帧才首次引入产品图。详见 Stage 2 参考图策略。
- **omni 视频生成 prompt 必须用英文**（2026-09-15 "The Box He Hid" 30s 实测）：中文运镜提示词被 omni 内容审核拦截（5 次），同样的剧情换成英文运镜提示词后通过。根因：omni 对中文文本的理解不如英文，容易把剧情描述中的中性词误判为违规内容。**Stage 4 运镜提示词和 Stage 5 组装提示词必须用英文。** 剧本 JSON 和关键帧 prompt 仍可用中文。
- **omni 内容审核不稳定**（2026-09-15 "The Box He Hid" 30s 实测）：同一组帧 + 同一条英文 prompt，6 次提交中 1 次到 97% 超时、1 次成功、4 次被拦截。审核结果不一致是 omni 渠道特性。**应对策略**：多段视频每段独立提交，失败的段重试 3-5 次；超时（非内容违规）重试即可；内容违规需软化措辞或改写剧情。冲突场景（争吵、夺门、哭泣）比温馨场景更容易被拦截。
- **omni 支持直出口播**（2026-09-16 收纳推车 20s 实测）：在 prompt 中写 `The video includes a female Chinese Mandarin voiceover speaking naturally throughout.` + 每拍 `The Chinese narrator says: "台词"`，omni 会直接生成带语音的视频（含 AAC 音频流）。生成的视频无需后期配音。
- **omni 口播不支持指定音色 ID**（2026-09-16 实测）：无法像剪映 TTS 那样传 `en_us_001` 等 speaker ID，只能用文字描述语音特征（性别/语言/年龄/语调）。多段视频的声音可能不完全一致。需要精确音色时用剪映 TTS 后期配音。
- **omni 不支持参考音频**（2026-09-16 实测）：API 无 `audio_reference` 参数，传 `audio` / `audio_reference` 字段虽不报错但提交超时，无法用外部音频锁定音色。
- **omni 口播台词长度限制**（2026-09-16 实测）：10 秒段约可容纳中文 15-20 字 / 英文 20-30 词，超出会被截断或语速过快。多段时每段台词需独立适配时长。
- **omni 人脸审核误判（2026-09-21 跳舞视频实测）**：同一组关键帧提交 omni_flash-hd-10s（含改尺寸 omni_flash-10s、改脸重画关键帧）反复返回 `PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED`，被误判为公众人物，重试/降级/改脸均无效。**对策**：视频改用 eo_3_1-lite（zexapi.ps1 video -Model veo_3_1-lite）即过。注意 veo-lite 单段上限约 8s、参考图上限 3 张（取首/中/尾帧），要 10s 需另想办法（omni 解限或拼接）。
- **【更新 2026-09-21】omni 人脸审核首选对策——戴口罩**：PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED 用戴白色医用口罩遮住口鼻（只露眼睛）后重画 6 帧再提 omni，已验证直接过审，能拿到 1080x1920/10s。比 veo-lite（仅 8s/720p）优先。
- **【更新 2026-09-21】omni 出片右下角模型水印必除**：omni 视频右下角有白色四角星小水印。交付前用 ffmpeg delogo 抹掉：先抽帧 fmpeg -ss 3 -i in.mp4 -frames:v 1 frame.png + crop=W:H:x:y 定位，再 fmpeg -i in.mp4 -vf "delogo=x=X:y=Y:w=W:h=H" -c:a copy -crf 18 out.mp4，框略大于水印。
- **【更新 2026-09-21】gflow i2i 已在 flow.google.com 跑通**：本地文件 --ref 单个即可锁脸锁穿搭，必带 --project <id>；传第二个 --ref 或媒体 UUID 会报错。详见 Stage 2。
- **【更新 2026-09-30】富铁软糖 30s 关键帧产品漂移事故**：产品基准卡把产品写成 `clear plastic bottle`，剧本 keyframe_prompt 又写成 `amber glass bottle`，两者与 product.jpg（红色瓶）打架，且每帧自由改写产品描述，第 4 帧入场帧没拦住错误就继续生成，导致 4-18 帧产品形态全漂。**根治**：产品外观只允许 Stage 0 一次性生成的 `product_lock_block` + `product_negative_block`（占位描述），剧本 `keyframe_prompt` 里产品一律写 `the {product_ref}`，禁止逐帧改写产品外观；入场帧（not_present→natural 第一帧）必须人工验收通过后才能继续后续帧。详见 Stage 0/1/2 新规则。