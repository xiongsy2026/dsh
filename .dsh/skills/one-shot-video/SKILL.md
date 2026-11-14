---
name: one-shot-video
description: >
  一句话生视频：一句话意图 → LLM 编译成 41 字段 Seedance 2.0 提示词（可编辑可自查）→ 提交 weeksir
  生成 9:16 / 15s / 720p 商品视频，默认 3 变体（痛点/场景/对比）。产物是 mp4 成片 + 编译提示词 JSON。
whenToUse: >
  用户给一句话（可选商品图）就要一条商品短视频，不写剧本、不过分镜审核链路。典型说法：
  "一句话给我出条视频"、"用这句卖点出 3 版 15 秒竖屏带货视频"、"痛点/场景/对比各出一版"、
  "就用这句话生视频，商品图给你"、"这个商品做个 15 秒广告，快就行"。
whenNot: >
  不做原片复刻/换产品/换脸（/super-clone）；不做多段剧本长片（/omni、/product-video-pipeline、/short-drama）；
  不做口播数字人、视频编辑、翻译（/video-translation）；不去水印（/video-dewatermark）、不升清（/video-enhance）；
  不反推提示词（/prompt-reverse）；不做批量 >3 段（属批量，另报价再开工）。
inputs: >
  一句话意图（必填）；商品图 1~7 张（可选，多张写 identityAnchors 防崩坏）；产品补充信息；
  客户补充意见（最高优先级）；变体策略 all/auto/A/B/C；推理引擎 gpt/grok
examples: |
  正例："泡脚桶一句话出 3 版 15s 竖屏视频" → 登记意图 → 传图 → compile 三变体 → 提示词交审 → 报价 → 逐变体 generate → 盯到 mp4 落盘交付
  正例："只出痛点那一版" → compile(variant_strategy="A") → generate → 交付
  反例："把这条爆款换成我的产品拍一版" → /super-clone（原片复刻，不是一句话生视频）
# ── 对话内表单的字段表（2026-10-08，见 docs/ADR-one-shot-inchat-form.md）──────────────
# 唯一源就是这里：8100 的 GET /api/skills/form?session= 解析这段投给前端，渲染器不硬编第二份。
# 行格式（刻意不用 yaml，免引依赖、少一种解析漂移）：key|type|label|必填|选项|引擎参数
#   type ∈ text | textarea | select | images
# 🔴 四个约束，违者算 bug（已由 scripts/check-skill-form.py 钉住）：
#   ① 每个 key 必须是引擎真实入参（app.py /api/one-shot/compile 收的那些），不许有"改了也不生效"的控件
#      ⇒ 所以这里【没有】时长/比例/分辨率：生成腿固定 9:16 / 15s / 720p；
#   ② 表单不出现任何积分/价格数字（价格必须从 gateway resolve 现读）；
#   ③ 表单挂在【右栏「填参数」页签】（2026-10-09 改判，页面由 8100 托管 /ui/<skill>），只在意图登记之后出现，
#      提交只落 8100 的会话级载荷；曾挂在对话流回合尾，已整块下线（那条 chain 槽会顶掉官方交付卡）；
#      🔴 绝不往输入框(composer)里写任何东西、不自动提交、不代替人点头；
#   ④ `intent:` 这行决定"登记成哪个档时挂这张表"（one-shot-video 登记的是 product-video 档）。
#      没有这行 ⇒ 这张表永远挂不出来。
intent: product-video
form: |
  user_intent|textarea|一句话意图（要拍什么）|yes| |user_intent
  product_images|images|商品图 1~7 张，第一张是主图|no| |product_image_paths
  product_info|textarea|产品补充信息（材质/尺寸/卖点）|no| |product_info
  user_supplement|textarea|你的意见（最高优先级，会压过其它推断）|no| |user_supplement
  variant_strategy|select|出几版|no|all 三变体痛点/场景/对比;auto 单条不限风格;A 痛点切入;B 场景沉浸;C 对比证明|variant_strategy
  engine|select|编译用哪个推理脑|no|gpt 默认;grok 备选|engine
version: 0.2.0
---

# 一句话生视频（one-shot-video）

一句话意图进、15 秒竖屏商品视频出。两条腿：**编译腿**（LLM 把一句话编译成 41 字段 Seedance 2.0
提示词，可编辑）+ **生成腿**（Playwright RPA 提交 weeksir.com/video 选 seedance-2.0-renlian 出片）。
引擎是 8090 那个工作台，本文件只写命令契约和关口；跨会话读这张纸就能接手，不靠记忆。

## 0. 先决条件（不满足就别往下走）

- **8090 在跑**：`app.py` 由 uvicorn 起（`python -m uvicorn app:app --host 0.0.0.0 --port 8090`，源码
  `backend/app.py`）。探测：`curl -s -o NUL -w "%{http_code}" http://127.0.0.1:8090/one-shot-video`
  要 200。没起就说"8090 工作台没起"并给出启动命令，**不要自己去 kill 或重启常驻服务**。
- **weeksir.com 登录态有效**：生成腿是 Playwright RPA（`pipeline/step5_video_generate.py` 的 `run()`），
  登录失效会死在"登录 weeksir"阶段。报登录问题让用户在浏览器里重登，**不许改 RPA 脚本**。
  - 🔴 **账号密码不再写在源码里**（2026-10-08 清零）：只从 `backend/.env` 的
    `WEEKSIR_USERNAME` / `WEEKSIR_PASSWORD` 读；缺配置时 step5 直接抛
    `RuntimeError: weeksir 登录凭据未配置…`（**不会再拿空串去点登录按钮**，那会伪装成"登录失败"）。
    报这个错 ⇒ 是 `.env` 没配/配错，不是账号密码错，别让用户去改密码。
- 🔴 **两条腿都是例外 route，在网关账本外**（编译腿走 `backend/services/prompt_compiler.py`：
  `engine="gpt"`=OPENAI_*（gpt-5.6-sol）、`engine="grok"`=SR_GROK_*（grok-4.6）；生成腿 = weeksir RPA
  seedance-2.0-renlian），**不经 `gateway.py`**。因此：
  - **禁止编造积分价**（"这条扣 X 分"一律不许写）——如实说"这条腿在网关账本外，扣的是 weeksir
    平台的生成额度，按条计"。
  - 生成腿按条花钱 ⇒ 提交前照样执行「花钱报价」关口（§4 关口 2）。
- 🔴 **边界文件只调不改**：`pipeline/step5_video_generate.py` 及 BOUNDARY.md 列的 `pipeline/step1~4`、
  `workflow.py` 等是 `backend/BOUNDARY.md` **整文件保护**的爆款复刻线路。本 skill 只调它的
  `run()`；要改先走 BOUNDARY.md §五 的确认流程（签名基准 + 回归测试），否则一律不动。

## 1. 第一步：登记意图（带引擎，不登记排他就不生效）

```
python scripts/intent.py product-video <任务名>
```

- **档位就是 `product-video`**（KNOWN_SKILLS 六档里的产品视频档，本来就无看板台）：登记的作用是
  **排他**——挡掉 omni/hypit/film/prompt-reverse 的台。跑完回显若说"判定口没有 product-video 这一档"，
  **属正常**（这一档无台可挂），看 `written` / `skill` 两行落盘即算生效。
- 会话 id 由 DSH 注入（PowerShell 写 `$env:DSH_SESSION_ID`，不是 bash 的 `$DSH_SESSION_ID`）；拿不到
  就加 `--session <会话id>`。任务名用**这条片子的别名**（如 `futie-泡脚桶-3变体`），`productions/<任务名>/`
  靠它对上。

## 1.5 登记完，先读表单再开口问（对话内表单的收口）

登记意图之后，这条会话的输入框上方区域（**agent 对话流的回合尾部**）会自动挂出这张 SKILL.md `form:` 段
描述的表单。客户填完点【提交这张表】⇒ 参数落到 8100 的会话级载荷里，**不经过输入框、不自动发消息**。
所以：

```
python scripts/intent.py payload
```

- `ok:true` ⇒ `params` 就是**可以直接喂给 `/api/one-shot/compile` 的入参**（键名 = 本文件 `form:` 段第 6 列
  的引擎参数名，`product_images` 已展开成 `product_image_paths` 的本地绝对路径数组）。
  **照它入参，别再逐项追问已经填过的东西**；只补缺的、或有歧义才问一次。
- `ok:false` / `params` 为空 ⇒ 客户没走表单，照常聊天收参数（表单是加速通道，不是唯一通道）。
- 🔴 判据（TTL、意图档匹配、必填、选项归一）只在 8100 那一份里，本脚本只搬运 —— 别在这里再抄一份校验。
- 客户回一句"开始"之后才允许进 §2 的编译腿；报价与批准口径不变（花钱前照旧报 `gateway resolve` 的实数）。

## 2. 端点契约（实测 file:line，别改写法）

基址 `http://127.0.0.1:8090`。⚠️ **认准 `/api/one-shot/*`**：`/api/one-shot-replica/*` 和页面 `/one-shot`
是"爆款复刻"（另一条线），本 skill 的页面是 `/one-shot-video`，别混。

| 步骤 | 端点 | 请求 | 关键返回 |
|---|---|---|---|
| 传商品图 | `POST /api/upload/image` (`app.py:1124`) | multipart，字段名 **`file`**，1~7 张各传一次 | `{success, path:"/uploads/img_xxx.png", file_path}` |
| 编译 | `POST /api/one-shot/compile` (`:7418`) | `{user_intent*, product_image_path?/product_image_paths?, product_info?, user_supplement?, variant_count?, variant_strategy?, engine?}` | 见 §3 |
| 自查优化 | `POST /api/one-shot/optimize-prompt` (`:7478`) | `{compiled_prompt*, engine?}` | `{optimizedPrompt, originalLength, optimizedLength, changed}` |
| 生成 | `POST /api/one-shot/generate` (`:7503`) | `{user_intent, compiled_prompt, metadata, product_image_path(s), aspect_ratio:"9:16", duration:"15s", resolution:"720p"}` | `{success, task_id, deduplicated?}` |
| 查状态 | `GET /api/one-shot/status?task_id=` (`:7563`) | — | `{status: PENDING/RUNNING/SUCCESS/FAILED, progress, stage, error, video_url, outputs, logs, params}` |
| 实时流 | `GET /api/one-shot/events` (`:7591`) | SSE | 运行中任务状态 + 最新 5 条日志。⚠️ **2026-10-08 前这条对 one-shot 是死的**（过滤写 `"one_shot"`、实际常量是 `"one_shot_video"`，恒不匹配），已修；且 **8090 要重启才生效**，没重启前照旧轮 `/status` |
| 历史 | `GET /api/one-shot/history?limit=` (`:7640`) | — | 历史任务（存 `backend/data\tasks.db`，服务重启后仍可查，别当"任务丢了"重跑） |

> ⚠️ 本表 file:line 于 2026-10-08 重新核对（旧版整体偏移约 +110 行，别再照旧行号跳）。

- `variant_strategy`：`"all"`（默认，3 变体）/ `"auto"`（单条无风格约束）/ `"A"`（痛点切入型）/
  `"B"`（场景沉浸型）/ `"C"`（对比证明型，指定风格单条）。
- `engine` 只影响编译腿；生成腿固定 weeksir seedance-2.0-renlian（9:16 / 15s / 720p）。
- `generate` 的 `metadata` 原样传编译回包里的 metadata（里面有 specVersion）。
- `generate` 带**防重复提交**（prompt+图+参数指纹）：同指纹在跑会回 `deduplicated:true` + 旧 task_id
  ⇒ 就去轮询那个任务，**不许再提交一次**（双花）。

## 3. 流程

1. **收输入**：一句话意图（必填，缺就追问一句）；商品图 1~7 张（可选）；产品补充信息；
   客户补充意见（`user_supplement`，最高优先级，编译器原样回显并压过其它推断）。
2. **登记意图**（§1）。
3. **传图**：每张 `POST /api/upload/image`，记返回的 `path`；多图全填 `product_image_paths`（第一张是主图）。
   多图是防崩坏手段——编译器会写 `identityAnchors` 锁死颜色/图案/版型/材质/logo/纹理。
4. **编译**：`POST /api/one-shot/compile`。两种回包：
   - 单条（`variant_strategy` = `auto`/`A`/`B`/`C`）：`{metadata, compiledPrompt, specVersion}`
   - 三变体（`all`）：`{variants:[{variantId, variantName, variantReasoning, metadata, compiledPrompt}], specVersion}`
     （A 痛点切入型 / B 场景沉浸型 / C 对比证明型）
   metadata 就是 41 字段规范（`identityAnchors` / `negativeConstraints` / `shot1~5` / `voiceoverScript` 等），
   `compiledPrompt` 是最终 Seedance 提示词。
5. **（可选）自查**：对每条 `compiledPrompt` 跑 `optimize-prompt`；`changed:false` 就用原版。
6. **⏸ 提示词交审**（§4 关口 1）：变体名 + `variantReasoning` + `compiledPrompt` 全文贴回对话。
   用户改哪个字都照改——**改完的版本才是生成用的版本**。
7. **⏸ 报价**（§4 关口 2），等明确同意。
8. **逐变体生成**：每个变体一次 `POST /api/one-shot/generate`（= weeksir 上一条 15s 视频）。
9. **盯到终态**：每 15 秒 `GET /api/one-shot/status?task_id=`（或听 SSE）到 `SUCCESS/FAILED`。成功读
   `outputs[0].local_video`（盘上 mp4）和 `video_serve_url`。🔴 "已提交/RUNNING"不是交付；一批里
   哪段失败只补那段，不整批重来。
10. **交付**（§5）。

## 4. 关口（只有这两个要停，别到处问）

1. **提示词交审** =【方案批准】：编译产物贴回对话（变体分条、提示词全文），用户直接改字。
2. **花钱报价**：生成腿按条扣 weeksir 平台额度（3 变体 = 3 条）。报价列清：`路由 = weeksir.com/video ·
   seedance-2.0-renlian（RPA 提交）`、`单价 = 页面/平台显示的单条扣费（照抄，不许凭记忆编）`、
   `条数 = 变体数`、`预计总扣 = 条数 × 单价`，并问"跑不跑"。等明确同意再 `generate`；同意只覆盖这次
   列明的条数——加变体、改时长/分辨率要**重新报价**。**报价后不要在同一轮顺手提交。**

## 5. 交付（产物没落盘 = 没交付）

- 成片照抄 `status` 回的 `outputs[0].local_video`（落盘于 `backend/outputs\one_shot_video\<task_id>\`，
  同目录还有 `_params.json`/`_result.json`/日志），**别猜文件名**；`video_url` 是 weeksir 直链，
  `video_serve_url` 是 8090 直链（`http://127.0.0.1:8090` + 该路径）。
- 按交付铁律归档：mp4 复制到 `productions/<任务名>/outputs/<ascii名>.mp4`，抽 1 帧贴图
  （`ffmpeg -ss 3 -i <mp4> -frames:v 1 frame.png`），8100 直链 = `http://127.0.0.1:8100/api/film/file?task=<任务名>&rel=outputs/<ascii名>.mp4`。
  交付消息给：mp4 绝对路径 + 直链 + 抽帧图 + 用了哪条变体/哪版提示词。
- 三个变体（或选定的那条）全部终态才算一轮交付。

## 6. 失败分类（照死因处理，不是一律重试）

| 死因 | 表现 | 动作 |
|---|---|---|
| 编译失败/空 JSON | 500 `编译失败: ...` 或字段缺失 | 换 `engine="grok"` 重试一次；再挂带原文停下，别改 41 字段规范 |
| 商品图不存在 | 400 `商品图不存在: ...` | 重传 `/api/upload/image`，用返回的 `path`，别手拼路径 |
| 防重复拦截 | `deduplicated:true` | 同一提交在跑 ⇒ 轮询那个 task_id，不许再交一次 |
| RPA 登录失效 | 卡在"登录 weeksir"后报错 | 让用户在浏览器重登 weeksir.com；不改 step5（BOUNDARY 保护） |
| RPA 页面改版 | 找不到元素/选择器报错 | 停下带截图报证据，**不许**改 `step5_video_generate.py` |
| 生成超时 | 20 分钟 `视频生成超时` / FAILED | 先查 `/api/one-shot/history` 和 work_dir（可能还在跑/已落盘），死透了只补这一条 |
| 同因连挂 3 次 | — | 停下报 task_id + error 原文 + 日志尾，不无限烧额度 |

## 7. 边界再确认

- 不做口播/数字人、视频编辑、翻译；批量 >3 段要另报价另开工。
- 不碰 `pipeline/step5_video_generate.py` 与 BOUNDARY.md 列的受保护文件；不把任何 key/凭据写进文件。
- 本 skill 出的是**单条 15s** 快片；要剧本到成片的长片链路交 `/omni` 或 `/product-video-pipeline`。
