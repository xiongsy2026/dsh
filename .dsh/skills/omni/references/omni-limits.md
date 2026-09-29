# Omni 边界（10s 单段、纯文生视频、无原片）

## 时长

- 单段 5 / 8 / 10 秒。默认 10s。
- 客户要 5s 或 8s：在 `target_duration` 显式写，不传默认 10s。
- 客户要 12s / 15s / 20s：拆段 10s + 5s/10s，再 ffmpeg concat。**不要让 omni 单段超 10s**，模型不支持。
- 客户要 30s+：建议走 super-clone 复刻原片（有分镜结构），omni 不擅长长片。

## 输入

- **必须**：产品图 1–N 张 + 产品名 + customer_request（客户自然语言要求）
- **可选**：参考视频（仅风格参考，**不复刻其分镜**）、style_hint、aspect_ratio
- **禁止**：让客户上传「原片」期望 omni 复刻其结构。要复刻走 super-clone。

## 模型边界

- `omni_flash-10s`：t2v（text-to-video），**不接收参考图**
- 产品图不喂给 API；仅用于 Agent 描述产品形态、颜色、包装
- 不支持 image-to-video / video-to-video
- 不支持指定种子
- 不支持指定关键帧

## 内容规则

- Prompt 以英文为主。中文只用于产品名 / 印花文字 / 口播引号。
- 不得写「复制 / clone / 像某某视频」。Omni 不读参考视频。
- 不得写「按图1」之类弱引用。Agent 把产品图描述成具体文字（"amber dropper bottle"）。
- 成片默认无字幕、无花字、无账号水印。产品包装上的印刷按产品图保留。
- 不得在 prompt 里写竞品品牌名 / 真人姓名 / 受版权保护的 IP 形象。

## 调性

- 客户没指定 `style_hint`：默认「cinematic, soft morning sunlight, neutral background, no text overlay」
- 客户指定「电商白底」：prompt 写 "clean white seamless background, product photography lighting, no shadow on background"
- 客户指定「明亮」：prompt 写 "bright airy lighting, pastel palette, soft highlights"
- 客户指定「暗调」：prompt 写 "moody low-key lighting, deep shadows, single hard key light"

## 切号 / 记忆

- 切号 = 切记忆（PRD §4.1.1）
- 本 skill 写 `accounts/<account>/SKILL_LOG.md`：项目路径、skill 名、prompt 草稿、出片结果
- 可沉淀结论写入 `accounts/<account>/MEMORY.md`：该号偏好的 prompt 模板、调性、产品图描述方式
- 不读取其它 `accounts/*` 的记忆

## 批准粒度

只在这些地方停：

1. BRIEF（写目标）
2. prompt 草稿（出片前）
3. 成片验收

不要每段 omni 调用问一次。
