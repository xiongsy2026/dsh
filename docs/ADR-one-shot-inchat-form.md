# ADR：one-shot-video 去 8090 化 —— 对话内表单进、右栏出

- 状态：**Proposed（等两个探测回来定生成腿与表单原语）**｜日期：2026-10-08
- 🔴 **2026-10-09 两处改判（见 `docs/ADR-input-form-single-port.md` §5）**：① **P0a 挂点**由「对话流回合尾部 `conversation.chat.turnTail`」改为「**右栏页签**，页面由 8100 托管 `/ui/<skill>`」—— "不碰 composer / 不 setDraft / 不 submit / 不代发"四条一寸不动，改的是画在哪个容器；② **P3（右栏工作台 = 进度 + 成片）作废**，过程与结果一律进对话（`scripts/deliver_pack.py` 三件套），右栏只剩表单。§2 的字段表判据、§2.5 的内核事实、§5.5 的 P4 判 A 全部照旧有效。
- 首个实例：`one-shot-video`（用户指定，不用动作迁移先试）
- 用户口径（原话）：**"还要启用8090服务，把要输入的放单agent里面去填表，输出的右侧栏展示"**
  ⇒ 解读：不该还要起 8090；**输入 = 对话里的表单**，**过程与产物 = 右栏展示**。
- 相关：`.dsh/skills/one-shot-video/SKILL.md`、`docs/ADR-workbench-presence.md`、`docs/ADR-model-gateway.md`、`backend/BOUNDARY.md`

## 1. 先拆腿：8090 在这条 skill 里到底承担什么

| 腿 | 现在在哪 | 去 8090 后去哪 | 难度 |
|---|---|---|---|
| **编译腿**（一句话 → 41 字段 Seedance 提示词） | 8090 进程内 `backend/services/prompt_compiler.py`，直连 `OPENAI_*` / `SR_GROK_*` | **skill 脚本 + `gateway.py call text.complete`**（gpt-5.6-sol / grok 都是已注册文本腿） | 低 —— 纯 HTTP 调用，无浏览器、无登录态 |
| **服务壳**（上传、任务表、SSE、history） | 8090 的 `/api/one-shot/*` | **agent 侧脚本 + 既有账本/presence**；文件走 8100 的 uploads/assets 通路 | 中 —— 都是"把 Flask 里那几行搬成脚本"，没有不可替代的能力 |
| **生成腿**（出片） | `pipeline/step5_video_generate.py` 的 Playwright RPA：登录 weeksir.com、开 `/video`、选 `seedance-2.0-renlian` | **待探测**：能直连就换直连；不能就只能保留 RPA | 🔴 **高 —— 这才是"发不了客户"的真根因** |

**关键判断**：RPA 要①桌面浏览器 ②我们的 weeksir 登录态。客户机上这两样都没有，把登录态递出去等于交账号凭据 —— 这条不做。
所以**"去 8090"能不能等于"能商业化"，完全取决于生成腿**（探测中，见 §5）。

## 2. 表单：字段跟着 skill 走，渲染器只有一份

字段定义落在 **SKILL.md 的 frontmatter 新段 `form:`**（不是 `SKILL_DOCK_META` —— 那张表管首页卡片，不是输入）。理由：SKILL.md 已经是"跨会话读文件即恢复"的唯一契约源，加第二段表就是两个入口。

`one-shot-video` 的字段（照 SKILL.md §2/§3 的 `inputs` 抄，不自己发明）：

| 字段 | 类型 | 必填 | 落到 |
|---|---|---|---|
| 一句话意图 | 长文本 | ✅ | `user_intent` |
| 商品图 | 图片 1–7 张（上传） | — | `product_image_paths`（第一张主图） |
| 产品补充信息 | 长文本 | — | `product_info` |
| 客户补充意见 | 长文本（**最高优先级**） | — | `user_supplement` |
| 变体策略 | 下拉 `all/auto/A/B/C` | 默认 `all` | `variant_strategy` |
| 推理引擎 | 下拉 `gpt/grok` | 默认 gpt | `engine`（只影响编译腿） |

⚠️ 表单里**不放**时长/比例/分辨率 —— 生成腿固定 9:16 / 15s / 720p，放进去等于给客户一个改了也不生效的控件。

🔴 **图片字段必须是上传控件，不能是"贴链接"文本框**（2026-10-08 探测坐实，同一条结论对动作迁移 8091 也成立）：
这类引擎的图片入参一律 `Path(...).exists()`，生图侧只认本地路径/bytes/`data:` URI，
RunningHub 侧是读本地字节再塞进**已登录浏览器**上传 —— 全仓没有任何"本机文件→公网 URL"的能力，服务又只听环回。
⇒ "客户贴商品图链接"这个字段是假的。**抖音链接可以纯文本贴**（含整段分享文案，引擎内部正则抠 URL）：
文本框只给链接类字段，图片给上传控件，宿主把文件落成引擎读得到的路径再回填。

**三条硬规矩**（沿用批准页那套教训）：
1. **提交即冻结成输入快照** `productions/<任务名>/form.json`；报价、编译、生成三处都只读这份快照 ⇒ 杜绝"客户填的 ≠ 报价的 ≠ 跑的"。
2. **表单只收输入，不做花钱确认**。花钱确认仍走批准页那一次点击（`http://127.0.0.1:8100/approval`）—— 客户点两次头就是加戏。
3. **表单是加速器不是关卡**：客户直接在聊天里丢一句话+图，agent 必须照常能跑。

## 2.5 内核探测结论（2026-10-08，全部带 file:line）

`<ROOT>` = `…\DSH Desktop\resources\app\node_modules\@deepseek-ai\`

1. **`ask_user_question` 承载不了这个需求** —— 它是**分页向导**，一屏一题（`<ROOT>\dsh-client-ui-user-questions\lib\client.js:488`），
   题型只有 radio / checkbox + 一行自由文本；**URL 型 / 数字型 / 下拉 / 文件 / 必填校验 / 正则 全部零命中**；
   且单选下"打字会清空选项"（`:546`）。⇒ 别指望扩它。
2. **但接管机制和回填机制都是现成的**：
   - 接管位 = `conversation.composer`（chain，"首个 select 非 null 独占"，priority 升序 `<ROOT>\dsh-client-ui-slots\lib\index.js:130`）；
     现存 occupants 优先级：subagent `-10` < user-questions `0` < approval `1` ⇒ **要赢必须压到 -10 以下**。
   - 正规抢位方式不是硬塞 select，而是注册自己的 pending-interaction 域
     （`<ROOT>\dsh-client-ui-session\lib\client.js:160-177`，同分**后注册者胜** :213-221），chain 的 select 只认你的类实例。
   - **提交三件套**（`<ROOT>\dsh-client-ui-conversation\lib\client.js:12627-12639`）：
     `inputActions.setDraft(文本)` → `addAttachments(ids)` → `submit()` ⇒ 走 composer 完整 admission，等于"以用户身份发一条带图消息"。
   - **图片可程序化注入**，不用让用户点回形针：`conversation.createDrafts(sessionId, files)`（`:2967-2986`），
     图片只认 4 种 MIME（`:3218-3224` png/jpeg/webp/gif），subagent 会话拒收文件（`dsh-client-file-upload\lib\index.js:313`）。
3. 🔴 **本 ADR 的核心取舍：表单不直连引擎，提交物 = 一条普通用户消息 + 附件。**
   理由：① 不新增第二条调用路径（否则"填表跑的"和"聊天跑的"行为会漂移，批准/报价/归属都要各做一遍）；
   ② 天然满足"表单是加速器不是关卡"；③ agent 侧读到的就是 `SKILL.md` 已写好的 `inputs`，**一行 agent 逻辑都不用改**。
   落地：附件由宿主落到 `%APPDATA%\dsh-desktop\harness\attachments\v1\…`（`dsh-attachment-local\lib\index.js:661,986`），
   agent 拿只读路径（`dsh-llm\lib\index.js:2048`）→ **复制进工作区** → 走引擎自己的上传口
   （one-shot：`POST 8090/api/upload/image`；动作迁移：`POST 8091/api/upload/image`）→ 回填 `file_path`。
   ⇒ 上一节"图片只能上传、不能贴链接"这条，在这套设计下**自动成立**，不需要引擎支持 URL。
4. **内核没有"文件→公网 URL"**（`shareUrl|publicUrl|presigned|cdnUrl` 全树零命中）；要只能 `ctx.webServer.register` 自挂局域网路由。
5. **没有 schema→表单渲染器可复用**：`primitives` 只导出 `Button/Input/Switch/Modal/Menu/Tooltip/MarkdownText/Pill/Tag/HoverCard`，
   **无 Select/Textarea/Form**；官方自己的多字段小表单是手写的（`dsh-client-ui-message-feedback\lib\client.js:57-135` 的 `FeedbackDialog`，
   挂 `conversation.input.overlay`）。⇒ 渲染器我们写一份，全 skill 共用（§2 的 `form:` 段就是它的输入）。

### 未验证（必须实测才知道，别当前提）

- **U1**：composer 被接管时 `InputBar` 是 `display:none` 而非卸载（`dsh-client-ui-renderer\lib\client.js:872-878`）
  ⇒ 此时 `inputActions.submit()` 是否还能可靠走完 admission / 附件结算。
- **U2**：自定义 pendingInteraction 能否真抢到 composer seat 而不被 user-questions(0)/approval(1) 抢先。
- 🔴 **对策两步都已作废（2026-10-08 用户当场否掉挂点）**：原 P0a 挂 `conversation.input.overlay`
  （输入框上方覆盖层）确实渲染出来了，但那就贴在 composer 上 —— 用户原话**"你怎么把表单放到了对话框这里…
  agent 的对话中出现，不能动对话框"**。所以：
  - **新 P0a** = 挂 `conversation.chat.turnTail`（agent 那条回复的尾巴上，和"下一步推荐"同一个流内槽），
    且只在**这条会话登记过意图**时出现（8100 按会话回 `show`，源 = `intent_map()` + 24h TTL）；
  - **提交不再走 composer**：`POST /api/skills/form` → `<工作区>\.dsh\form_payload.json`（会话级），
    agent 用 `python scripts/intent.py payload` 读，键名已经是引擎 param 名 ⇒ 直接入参；
  - **P0b（接管 composer + 自动 submit）整条撤掉**：不是"等 U1/U2 实测"，是方向错了 ——
    往输入框里脚本写东西本来就是明令禁止的事。U1/U2 因此不再是任何任务的前置。

### 读编译产物得到的内核事实（2026-10-08，全部带 file:line；第 1/3 条随挂点作废降级为"别再踩"）

1. **`conversation.input.overlay` 的渲染点只传空 props**：`dsh-client-ui-conversation/lib/client.js:16069`
   = `renderSlot("conversation.input.overlay", {})`。它**常渲染**（在 `InputBar` 卡片顶部 `overlayAnchor` 里，
   `sessionId !== void 0` 就画）⇒ "按钮平时在不在"这个未知数已消除。
2. **但 `inputActions` / `useConversation` 会到**：该槽声明为 `kind:"list", scope:"session"`（`:16729-16732`），
   而 `ctx.uiSession.provide({ hooks:["conversation","input"], props:["inputActions"] })`（`:16590-16604`）
   由 session scope 适配器注入；hook 名按 `standardHookPropName` 变成 `useConversation` / `useInput`
   （`dsh-client-ui-session/lib/client.js:309`）。
3. **附件接口不在 hook 上，在服务上**（这条是 P0a 的真实缺陷，已修）：`createDrafts(sessionId, files)`
   定义在 `ConversationController`（`:2972`），官方调用点 `:16781` 用的是 `concreteConversation(ctx)` =
   `ctx.get("conversation")`（`:16505-16509`）；`useConversation()` 给的是 `BoundConversation.snapshot`
   （会话**数据**快照，`:2467-2484`）⇒ 拿它当附件接口必然落空，图片只会"填了文字没带图"。
   现在：`ctx.get("conversation")` + `props.sessionId || currentSessionId(ctx)` + `inputActions.addAttachments(ids)`，
   失败仍退化为"只填文字"并明说。回归：`scripts/check-skill-form.py` 的 **B10/B11**（含反向对照）。
   `inputActions` 的完整面 = `{setDraft, addAttachments, removeAttachment, pruneAttachments, submit}`（`:12627-12641`），
   `setDraft` 只重排正文、不动 `attachmentIds`（`:12758-12774`）⇒ 先填字后挂图与顺序无关。

## 3. 右栏：复用既有 presence 机制，不新造一套

`docs/ADR-workbench-presence.md` 已经把"这条会话该挂哪个台"收成判定口 `GET /api/workbench/presence?session=`，并且有 `WORKBENCHES` 表 + 排他规则。所以：

- 给 `one-shot-video` 在 `WORKBENCHES` 里加一档（**登记意图仍由 `scripts/intent.py` 触发**，符合"选了就要看到界面"）。
- 台里三块，数据源都是现成的：
  1. **表单回显**（只读，改了要重新提交）
  2. **进度**：变体 A/B/C 各自 `PENDING/RUNNING/SUCCESS/FAILED` + 最新日志尾（原来靠 8090 的 SSE，改读任务表/账本）
  3. **成片**：`productions/<任务名>/outputs/*.mp4` + 8100 直链 + 抽帧图 —— 交付三件套不变
- 🔴 不再往右栏塞"生成"按钮。右栏是**展示 + 批准**，不是输入口（`video-ui.md` 既有口径：按钮 A 是 agent 自动，B 才是人点头）。

## 4. 分阶段（每阶段独立可用，可停在任意一步）

| 阶段 | 做什么 | 依赖 | 收益 |
|---|---|---|---|
| **P0a**（已按新挂点重做） | 表单挂 `conversation.chat.turnTail`（agent 对话流的回合尾部），**意图登记后才出现**；提交只落 8100 会话级载荷，agent 用 `intent.py payload` 读 ⇒ 直接入参 | 无 | 解决"一张张传图"，且一寸不碰输入框 |
| ~~**P0b**~~（**作废**，非延期） | ~~接管 composer + 自动 `submit()`~~ ⇒ 往共享输入框里脚本输入本来就是禁止项 | — | — |
| **P1** | 字段定义进 SKILL.md `form:` 段 + 共用渲染器一份 | P0a | 其它 skill 照抄即得表单 |
| **P2** | 编译腿搬进 skill 脚本、走 `gateway.py call text.complete` | 无 | 少一条账本外直连；编译进积分账 |
| **P3** | 右栏工作台（进度 + 成片），去 SSE | 无 | 过程可见，不再开 8090 页面 |
| **P4** | 生成腿去 RPA | 🔴 探测中（weeksir 有无直连 API） | 决定能不能发客户 |
| P5 | SOP 收口（表单字段成为上架交付物） | 前面都稳 | 用户明示：最后一步 |

**P0a–P3 都不需要等 P4**，也就是说"去 8090 依赖"能先做到"8090 只剩生成腿一个转发口"，那时再决定 P4 换道还是保留。

## 5. 未决项

1. 🔴 **weeksir 有没有可直连的生成 API**（探测中）—— 这条决定 P4，也决定"去 8090"能不能等于"能发客户"。
2. ✅ ~~内核表单原语~~ —— 已回，见 §2.5：`ask_user_question` 不够用，但接管/回填/程序化附件三套机制都是现成的；
   剩两个必须实测的未知数记为 **U1/U2**（对策是先做零未知的 P0a）。

## 5.5 P4 探测结论（2026-10-08）：**判 A —— weeksir 有 REST v2，可直连，RPA 能去掉**

零消耗 GET 探活（不带任何凭据）：`/api/v2/videos/models`、`/api/v2/videos/tasks`、
`/api/v2/videos/tasks/{id}`、`/api/v2/video-preparations/capabilities` 全回
`401 {"code":"AUTH_REQUIRED","message":"missing login token"}`；对照组 `/api/v2/videos/models_no_such` 回 `404`
⇒ **接口真实存在、token 鉴权**。完整请求/响应形状躺在 `backend/weeksir_profile\Default\Cache\Cache_Data\`：
- `f_000144`（模型+价目）：`seedance-2-renlian` · durations `[5,10,15]` · `max_reference_images:9` ·
  **`15s/720p = 225 积分`**（15s/480p=150、15s/1080p=450、5s/720p=75）⇒ **这是实测价，不再是"占位"**。
- `f_0001b8`（任务记录=创建体原样回显）：`input.prompt.text` / `input_assets.reference_image_ids[]` /
  `output{aspect_ratio,duration_seconds,resolution}` / **`client_request_id`（幂等键，天然顶掉 8090 的 dedup）** /
  `billing.estimated_points` / `results[].urls.preview_url`（OSS 签名直链）。

**还缺三块才能开工（不许当前提）**：① 创建用的 **POST 路径与请求体**（缓存只存 GET；RPA 里
`page.on("response")` 只收 `.mp4`，`step5:699-716` ⇒ 仓库内零命中）；② `reference_image_ids` 那步的**上传接口**；
③ **token 怎么拿**（`/api/v2/auth/login` GET 404，形状未知）。
⇒ 唯一翻案动作：**在已登录浏览器里开 DevTools/HAR 跑一条真提交抓包**，那要花 **225 积分**，必须先过报价关口。

**BOUNDARY 不挡路**：`scripts/check_replica_boundary.py:45-50` 的 `PROTECTED_FILES` 是**穷举清单**
⇒ 新增旁路文件（如 `step5_via_api.py`）不在保护集内、`step5_video_generate.py` 一行不动即可。
⚠️ 但 SKILL.md 自律更严（`:45`、`:138`「不碰 step5」）⇒ 走旁路，别改原文件。

**后备 B**：`video.generate/seedance-2.5|2.0`（`gateway.py:86-87,2433-2441,632-691`）已是纯 HTTP，
代价 = 参考图必须先转公网 CDN（`image_urls` 原样塞 body、无本地路径校验）、60/50 每 10s 属占位未实测、
≥20s 只能 2.5、带**非确定性肖像闸**（见 [[likeness-guard-lane-choice]]）。

## 5.6 探测顺手挖出的四个必须处理的事实

1. 🔴 **weeksir 账号明文密码在受保护文件里**：`pipeline/step5_video_generate.py:186-187`
   `WEEKSIR_USERNAME="181…"` / `WEEKSIR_PASSWORD="…"`（本次调查把它读进了对话 ⇒ 按纪律算泄露，已主动报）。
   ⇒ 要改成 `.env` 必填 + **轮换密码**；同 [[upstream-calls-outside-ledger]] 那次的处置。
2. 🔴 **`/api/one-shot/events` 这条 SSE 对 one-shot 是死路径**：`app.py:7605` 过滤 `task_type == "one_shot"`，
   而写入的常量是 `oneshot_video.py:30 TASK_TYPE = "one_shot_video"` ⇒ 永不匹配、`running_count` 恒 0。
   ⇒ SKILL.md:100「或听 SSE」是错的，只能轮 `/status`。**这条会误导下一个接手的人。**
3. ⚠️ **SKILL.md §2 的 file:line 全部过期约 +110 行**：实测 upload `app.py:1124`、compile `:7418`、
   optimize `:7478`、generate `:7503`、status `:7563`、events `:7591`、history `:7640`。
4. ✅ **任务归属不用重新设计**：history 本就落在 `backend/data\tasks.db`（`task_store.py:32-34`，
   实测 3.1 MB）⇒ 去 8090 化时直接搬这个库，P3 右栏读它即可。

## 6. 风险与边界

- `pipeline/step5_video_generate.py` 受 `BOUNDARY.md` **整文件保护** ⇒ 任何替换都走它的确认流程；倾向**新增旁路文件、原文件不动**。
- 8090 同时是 **prompt-reverse 第 6 档台的宿主** ⇒ 本 ADR 只摘掉 one-shot 对它的依赖，**不许顺手停 8090**。
- 生成腿在网关账本外 ⇒ 报价口径照旧"禁止编造积分价"，按 weeksir 平台实扣（按条）说并等同意。
  ⇒ P4 落地后可改为走 weeksir v2 + 进积分账，届时这条要重写。
- 重启 8090/8100 前照旧先 `hypit_bridge.py preflight`。

- `pipeline/step5_video_generate.py` 受 `BOUNDARY.md` **整文件保护** ⇒ 任何替换都走它的确认流程；倾向**新增旁路文件、原文件不动**。
- 8090 同时是 **prompt-reverse 第 6 档台的宿主** ⇒ 本 ADR 只摘掉 one-shot 对它的依赖，**不许顺手停 8090**。
- 生成腿在网关账本外 ⇒ 报价口径照旧"禁止编造积分价"，按 weeksir 平台实扣（按条）说并等同意。
- 重启 8090/8100 前照旧先 `hypit_bridge.py preflight`。
