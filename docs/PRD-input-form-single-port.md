# PRD：输入走品牌化表单、结果只进对话、UI 面收敛到 8100

状态：**A 框架已落码并在 8100 生效；试点线 one-shot-video 跑通到【提交→读回】；prompt-reverse 已迁完（四步全过）；F1 三张截图已交、真右栏那一下未点；F5 真出片未跑（要花钱）**｜日期：2026-10-09｜负责人：产品
生效证据：8100 现 PID **46856**（17:26:50 起）晚于 `model_admin.py`（16:12:45）、`hypit_bridge.py`（16:02:15）、`skill_form_page.py`（15:33:50）三个 mtime；闸全绿 —— 表单 / 意图 / presence / 8100 自愈 / 客户端 / 同步 / 合同 / 唤醒
🔴 **条数一律不写进本文档**（写死必烂：同一天里 sync 从 54 涨到 58、spend 从 59 涨到 61）。取数：
`for g in check-skill-form check-intent-rule check-workbench-presence check-svc-8100 check-workbench-client check-prd-task-sync check-skill-contract check-form-wake check-spend-approval; do python scripts/$g.py 2>&1 | tail -1; done`
（`node scripts/check-workbench-client.mjs | tail -1` 单独跑）
⚠️ **F4 今天红过一次，红得有理**：16:04 那次重启后，`model_admin.py` 在 16:12 又被改了一次（视频槽兜底名 + 体积文案）⇒ 按"只认 PID+StartTime 晚于 mtime"这条，16:12 之后的码就是【盘上有码、线上没跑】。17:26 重启后才对。这条判据存在的意义就在这一分钟。
决策依据：`docs/ADR-input-form-single-port.md`（含对 `ADR-pipeline-server.md`、`ADR-one-shot-inchat-form.md` 的三处改判）
任务清单：`docs/tasks-input-form-single-port.json`（**13 个任务，分 A 框架 / B 逐线 / C 独立快修三段**，每个带 `verify`；3 条成功指标 M1-M3）
回归闸：`scripts/check-skill-form.py` + `scripts/check-workbench-client.mjs` + `scripts/check-prd-task-sync.py`（本对已登记，PRD↔JSON 漂移会红）

> 🔴 **引用规矩（2026-10-09 审核改的）**：本 PRD 与任务 JSON 里**不钉 `model_admin.py` 的行号**，只钉符号名
> （`_FORM_TYPES` / `parse_skill_form` / `_form_store_files` / `form_gate_view` / `form_prefill` / `skill_form_index`）。
> 理由：那个文件今天一天从 +319 涨到 **+746** 行未提交，行号在并发写者上注定失效；符号名不会漂。
> 同理"未提交行数"这类快照**不写死**，写取数命令（`git -C backend diff --numstat -- backend/model_admin.py`）。

## 1. 问题（用户原话）

> 目前系统的交互还是非常不人性化的，且前期沟通占比时间太多，影响做视频的效率。

拆成三个可验的事实：

1. **输入是对话采集的**。omni 从"客户说开始"到能起跑要 8-9 项（模式/产品名/时长/主图/细节图/卖点/平台/国家+语言包/叙事结构）：必填契约在 `.dsh/skills/omni/SKILL.md:40-42`，3 选 1 故事方向与目标市场/语言在 `:55-59`，两处都明写"缺由 agent 追问补齐" ⇒ 5-8 轮问答。
   ⚠️ `:98` 是**另一件事**（成片批准后那轮"要不要存成模板"的固定追问），不属于输入采集，别拿它当输入基线的证据（2026-10-09 审核抓到的一次引用错位）。
2. **输出是引擎页面**。右栏自动台里三档 iframe 直指引擎自带的 HTML（`plugins/ops-gateway/lib/client.js` 的 `WORKBENCHES`：`:8765/`、`:5179/`，改前还有 `:8090/prompt-reverse`）⇒ 每加一条线就加一个口，且客户机必须能开那个页面。
3. **已建好的表单没人用得上**。2026-10-08 那套机制是完整的（解析/门控/校验/落盘/读回），但**40 个 skill 里只有 1 个配了字段表**，且当时 `.dsh/form_payload.json`、`.dsh/form-uploads/` 不存在 ⇒ 从来没产生过一次真实提交。

## 2. 目标 / 非目标

**目标**
- G1 输入 = 一条 skill 一张**品牌化表单页**，明确意图后出现在**右栏**，客户填完点提交；
- G2 agent 先分析客户已传的图/视频/要求，**自动回填**到字段框（带「AI 已填 · 可改」标记），客户只需改与确认；
- G3 过程由 agent 驱动 skill 工作流，**结果只进对话**（图片内联看、视频走"路径 + 8100 直链 + 抽帧贴图"，即 `scripts/deliver_pack.py` 三件套）；
- G4 **UI 面收敛**：客户机交互面只暴露 1 个口（8100）；8765/5179 是**点名的存量冻结例外**，🔴 待收敛项不是终态（见 U10）。增量一律挂 8100 路径，**端口数不得随 skill 数增长**（写进 `check-workbench-client.mjs` 的 0b/0c/0e/0f，不靠纪律）。
  ✅ 本轮已收掉一个：`prompt-reverse` 的 8090 iframe 已退出右栏，例外名单从 3 条缩到 2 条。
- G5 **效率指标（对应"前期沟通占比时间太多"，必须有数）**：一条片子从"客户开口"到"能起跑"的**追问轮次 ≤ 1**（对照基线 5-8 轮）；度量 = 数这条会话 transcript 里 agent 发出的提问轮次。
  🔴 **口径三条排除项（少写一条就会多数）**：① 方案 / 审帧 / 成片那三个批准关口不计（它们照旧要停）；② **成片批准后"要不要存成模板"那轮固定追问不计**（`.dsh/skills/omni/SKILL.md:98`，它是交付后的动作，不是"要到能起跑的输入"）；③ 只算【建了表的线】—— omni 已冻结 ⇒ 本轮不承诺 omni 的 G5（见 U10）。
  ⚠️ **基线（"8-9 项 / 5-8 轮"）目前是从 `omni/SKILL.md:40-42,55-59` 的"追问补齐"口径推的，不是实测** ⇒ **U12**：拿 2-3 条历史会话真数一遍，否则"≤1 轮"是拿没量过的数当分母。
- G6 **融合进来的 skill 必须脱离 8090/8091 这类手起进程**（用户原话"比如动作迁移、一句话生视频，都还是 8090 的环境调用，肯定是要脱离"）：
  - 动作迁移：**已脱离 8091 进程**（`gateway.py` 直调本地包、右栏第 7 档 `retired:true`），残留是 `D:/nzdh` 的**数据与凭据**依赖（`.env` / RH 账号池 / 旧 `tasks.db`）⇒ 由 T9b 收口；
  - 一句话生视频：**三腿全在 8090** ⇒ 由 T9a 收口（不花钱的两腿先搬；生成腿被 **U4** 挡着 = 要花的 225 分抓包）。

**砍什么 / 不砍什么（🔴 "输出和 SSE 完全砍掉"要按腿定性，别一刀切）**

| 对象 | 处置 | 依据 |
|---|---|---|
| 8090 那条 SSE（`app.py` 的 `/api/one-shot/events`，`frontend/one-shot-video.html` 用 `EventSource` 订阅） | ✅ **砍**（随引擎页面下线自动消失，0 成本） | 它只为那个页面存在 |
| hypit 工程流 SSE（`backend/services/hypit_workspace.py`） | ❌ **不砍** | hypit 已冻结（非目标 2） |
| 聊天流式输出（`backend/agent_router.py` 的 `/v1/chat/completions`） | ❌ **绝不能砍** | 这是 DSH 聊天本体逐字出，**不是"输出 UI"** |
| 右栏的 5s/8s 轮询（`ops-gateway/lib/client.js`、`super-clone-ui/lib/client.js`） | 保留（它不是 SSE；砍的是"右栏画输出"，不是轮询机制） | 轮询还要用来弹表单 |

**非目标（🔴 越界即违规。条目以 `tasks-input-form-single-port.json` 的 `hard_boundaries` 为准 —— 这 10 条是它的【子集】，不是"逐字同步"：JSON 还含实现级边界，两边条数本来就不同。改边界先改 JSON，再同步这里；`check-prd-task-sync.py` 的 B 组按子集关系判，反向（PRD 冒出 JSON 没有的条目）就红）**
1. 🔴 **omni 整条不动**：右栏那档继续指 `:8765/`，不建 `form:` 段，不改它的对话采集口径。理由：8765 那张页面本来就是输入表单（`templates/index.html`），且 agent 驱动 REST 建 run（`app.py` 的建 run 口）⇒ 它已经是本 PRD 要的形状，重写它=倒退。
2. 🔴 **hypit 整条不动**：5179 官方 studio 保留，不建表单（它的输入是 `.svml`/`.svrun` 工程 + 关口 A 的方案/pricing）。
3. 不做 `:8000` 单网关 mount（原 `ADR-pipeline-server.md` 已改判作废）：不迁前端、不改绝对路径、不引 a2wsgi。
4. **右栏不画结果、不画进度**：`ADR-one-shot-inchat-form.md` 的 P3 作废；过程与产物唯一出口是对话。
5. **表单不收花钱确认**：点头仍只有 `127.0.0.1:8100/approval` 那一次点击；表单提交 ≠ 批准，`prefill` ≠ `submitted`。
6. **不碰 composer**：不 setDraft、不挂附件、不 submit、不代发（2026-10-05/10-08 两次事故与一次否决）。
7. **一个 HTML 模板 + query 参数服务全部 skill**：不给每条 skill 写一页（`/ui/<skill>`），字段表唯一源是各 `SKILL.md` 的 `form:` 段（`skill_form_index()`），渲染器与 8100 都不许出现第二份。
8. 🔴 **不给跑不通的腿建表**：RunningHub 视频禁令（`config.py` 的 `WAN3_RH_ENABLED=False`、`runninghub_client.py` 的 `RH_VIDEO_EXEMPT=("edit","image")` 白名单默认拒）把 super-clone / replica-wan3 / H3 腿断了 ⇒ 等线路恢复或换道后再建。
9. 不顺手停 8090（它仍是 one-shot / prompt-reverse 的引擎宿主）；重启 8100/8765 前先 `hypit_bridge.py preflight` 独立调用看到 `safe_to_restart:true`。
10. 动这三个文件前先 `git diff --numstat` 对一遍 —— **`plugins/ops-gateway/lib/client.js`、`plugins/super-clone-ui/lib/client.js`、`backend/model_admin.py`**（2026-10-09 实测它们分别带 +103/-11、+157/-2、+746/-35 的【别的会话未提交改动】），只用精确插入式编辑，禁整文件覆盖、禁 `git checkout --`。

## 3. 交付时序（客户视角，全程三个动作）

```
① 点 skill（或首页能力卡）
② 右栏出现「填参数」页签 —— ≤5 秒（presence 轮询周期，现值 5s）
   已传素材/已说要求 ⇒ 对应字段已预填并标「AI 已填 · 可改」
③ 点【提交这张表】= 授权开工（不再要求客户回一句"开始"）
   → agent 读 payload（键名即引擎 param）→ 报价（🔴 要不要批以 `gateway.SPEND_APPROVAL_EXEMPT` 为唯一源，
     本 PRD 不抄能力名——抄了就会漂：这条口径今天改过三次）→ 客户在 /approval 点一次
   → 跑 → 关键帧、成片、实扣积分全部出现在对话里
```

🔴 **这一格的触发者已落码（T13，2026-10-09）**：客户点【提交这张表】发生在 agent 上一轮已经说完之后 ⇒
以前是"载荷落了盘、页面转了确认态，但没有任何 agent 被叫醒"（用户原话『我提交了表单，但是没有往下走啊』）。
现在的链路：`form_submit` 成功 ⇒ 该条记录挂 `wake={pending,at}` ⇒ 宿主插件（`plugins/ops-gateway/lib/index.js` 的 host 半）
每 5 秒从 `GET /api/skills/form/wake` 领队列 ⇒ 只对**活着且 idle** 的会话调 `Agent.followup()`（内核 `dsh-agent-loop:789`，
官方 dsh-schedule / dsh-webhook 同款）⇒ 叫完拿 `at` 去 `/wake/ack` 销账。
🔴 三条边界：① 只叫醒、**不代发**（不碰 composer）；② **唤醒 ≠ 批准**，花钱那一下仍只有 `/approval` 一次点击；
③ 会话没开着 ⇒ 不叫也不销账（等他打开再说）。判据只在 `form_wake_queue` 一份，回归 `scripts/check-form-wake.py`（29 条）。
⚠️ 待办：host 半在 harness 启动时才求值 ⇒ `Ctrl+R` 不够，要一次完整重启 DSH Desktop 才生效（会杀掉正在用的窗口 + 撞上 composer 竞态，未擅自做）。

## 4. 落点与实现要点（给执行 AI：照这个干，别"先研究再说"）

### 4.1 服务端（`backend/model_admin.py` + 新文件 `backend/skill_form_page.py`，在 `backend` 下）—— ✅ 已落码并已重启生效

| 改了什么 | 位置（**只写符号名，行号会漂**） | 状态 |
|---|---|---|
| 字段类型加 `number`/`video`，媒体槽允许多个 | `_FORM_TYPES`、`_FORM_MEDIA`、`_FORM_MAX_DEFAULT` | ✅ 已生效 |
| 字段表第 5 列在 number/images/video 上复用为区间（`10-90`/`1-7`）；第 7 列=这一槽自己的体积上限（`15MB`） | `_range_spec()`、`parse_skill_form()` | ✅ 已生效 |
| 文件按槽落盘 + 单值/数组按字段表判（`video` 上限1⇒字符串=引擎的 `video_path`，其余⇒数组） | `_form_store_files()`（替换原来 `next(...)` 只认第一个图片槽的写法） | ✅ 已生效 |
| 槽级体积上限下发给页面（前端预筛与服务端同源） | `_form_fields_wire()` | ✅ 已生效 |
| 回填口（只写 `defaults`、绝不写载荷、绝不算 submitted） | `form_prefill()` + `_form_prefill_paths()` + `FORM_PREFILL_PATH` | ✅ 已生效 |
| 门控合并 `defaults`/`default_files`/`prefill_dropped`，技能换了就作废 | `form_gate_view()` | ✅ 已生效 |
| POST 同口分档：一次请求只写一份 | `form_route_dispatch()` —— 🔴 曾写成"先 submit 再 prefill"，实测会替客户交表，已修并有 H 组值表钉住 | ✅ 已生效 |
| 表单档的判定口（哪条会话该弹表、弹哪个技能） | `form_presence_view()` + `hypit_bridge.register_form_view()` | ✅ 已生效（B8 打真判定口验过装配） |

为什么这些是必需的（不是审美）：`one_shot.html`、`super_clone_wan3.html`、`replica_wan3.html`、`prompt_reverse.html`、`motion_transfer.html` **五张页面的头号必填项都是视频上传**，而旧类型表根本没有 `video`；super-clone 要"产品图 1-10 + 面部图 + 场景图"三槽，旧代码只认第一槽 ⇒ 会把客户的脸图当产品图塞进引擎（**猜 = 事故**，所以多槽时缺 `field` 一律拒）。

### 4.2 表单页（品牌感）—— ✅ 已上线一个模板；四分区里"高级"本轮因无 number 字段未触发

现状是简陋的实证（改前）：`plugins/super-clone-ui/lib/client.js` 里那 20 行内联 CSS —— 一条边框 + `border-radius:12px` + **裸 `<input type="file">`**，为对话小卡设计，从未按右栏整屏设计过。

**品牌 token 的实测依据**（不许凭想象）：
- 主色两套并存是既有事实：批准卡 `--brand:#4d8dff`（`model_admin.py` 的 `APPROVAL_PAGE` `:root`）vs 产品面 `--weeksir-accent-orange:#fa6f08`（`super-clone-ui/lib/client.js` 顶部，实测出现 9 次，并把 `--dsw-alias-brand-primary` 覆盖成橙）。⇒ **第一步是定成一处**，不是加渐变。
- 官网 `weeksir.com` 实测：Nuxt SPA，未登录态真浏览器打开后 `h1`/`button`/`img` 全零命中，只有 `title="WEEKSIR"`；**唯一拿得到的品牌事实是字体族 `Inder, Inter, "Noto Sans SC", -apple-system, "Segoe UI", sans-serif`**。
- 素材真实存在：`/assets/weeksir-logo.png`（`model_admin.py` 的静态资源路由已在服务，实测按 `<img>` 读到 336px 渲染宽）、slogan「专注电商AI落地」。

| 层 | 要求 |
|---|---|
| **Token** | 一份 `WS_TOKENS` CSS 常量（`--bg/--panel/--line/--fg/--mut/--brand/--on-brand/--ok/--bad/--warn/--radius/--shadow`），批准页与表单页 `<style>` **共用同一份 ⇒ 全站只留一处 hex**（闸 F1/F2 数 `:root` 定义处）。主色橙 `#fa6f08`（同屏 dock 卡片就是它），蓝降为链接/info。 |
| **骨架** | 顶部品牌带（logo + 「专注电商AI落地」+ 技能 chip + 一句话说明这条片要干什么）→ 四分区「素材 / 内容 / 风格 / 高级（默认折叠）」→ 底部吸底操作条（提交 + 一句"提交只交参数，花钱仍要点批准"）。右栏宽度做**单列**。 |
| **控件** | 图片槽=拖拽+点选+多文件+缩略图+第一张自动标「主图」+可删可排序；视频槽=同上并显示体积与该槽上限；选项 ≤4 用 chip 单选组，长清单才原生 select；数字用步进 chip；必填未填 ⇒ 描红并滚到那一格；🔴 **回填值旁必须挂「AI 已填 · 可改」并显示依据**；状态=提交中转态/已提交转确认态（列出最终参数）/8100 没回话明说是故障（不许谎报成"没有表单"）。 |
| **文案** | 短句、大白话、去表单腔；字段名跟着销售语境（「商品卖点」而不是 `selling_points`）。 |

⚠️ 代价如实说：① 共用 token 动过批准页那 100+ 行内联 CSS（改前截了图，改后对比）；② 缩略图新增了一条**只读**回显口（`form_media_file()`：realpath 必须落在 `FORM_UPLOAD_DIR` 内 + 扩展名命中该槽允许集，不开任意文件读取）；③ 官网字体是 webfont，客户机可能离线 ⇒ 只做"同族 + 系统兜底"，不引 CDN（U8）。

#### 4.2.1 提交按钮（2026-10-09 产品定：未填完=灰，填完=黄）

| 态 | 视觉 | 点击行为 |
|---|---|---|
| 未完成 | 灰（`--panel` 底 + `--line` 描边 + `--mut` 字，明暗两套都要能读） | 🔴 **仍可点**：不提交，只把第一个缺失项**描红 + 滚动过去**，并在按钮旁逐条列出"还差：产品图、卖点" |
| 完成 | 黄/橙实心 | 提交（`Content-Type: text/plain` 简单请求，8100 无 `do_OPTIONS`） |
| 提交中 | 同色但禁用 + 内联 spinner | 防重复提交 |
| 已提交 | 整卡转确认态，列出最终参数供回看 | 要改必须重新提交（`submitted` 后 defaults 不再合并） |

**"算填完"的判据**（必须与服务端 `form_submit` 同一条，字段表下发，渲染器不抄第二份）：
1. `text`/`textarea`：非空（首尾空白剥掉后）。
2. `number`：非空 **且落在字段表给的区间内** —— 只判非空会出现"按钮是黄的、提交却被服务端拒"。
3. `select`：必填 ⇒ 已选；非必填 ⇒ 空也算过。
4. `images`/`video`：必填槽 ⇒ 至少 1 个；非必填槽 0 个也算过。**多槽各自独立判**。
5. 🔴 **agent 回填的值必须计入完成度**：`defaults` 有值就算填了，`default_files` 里的路径就算文件槽已满足 —— 否则"回填完还是灰的"等于白回。
6. 预筛：MIME 与体积按服务端同一份允许集判（`accept`/`max_bytes` 随 `fields` 下发）—— 图片 `png/jpeg/webp/gif` ≤ 8MB、视频 `mp4/mov/webm` ≤ 200MB，**字段表第 7 列可给单槽更小的上限**（prompt-reverse 的 15MB 就是引擎硬限）。

**配色实测**：`#fa6f08` 相对亮度 L≈0.316 ⇒ **白字只有 2.87:1，WCAG AA 不过**；近黑 `#1b1f27` 是 **7.3:1（AAA）** ⇒ 黄/橙按钮上的文字必须是**深色**（`--on-brand`）。要更亮的黄只改一处 token，🔴 但按钮上不许出现第二个 hex。
**判据写成纯函数** `formIsComplete(fields, values, files, defaults)` 并整块抠进闸跑值表（C 组 10 情形）。

### 4.3 右栏挂载（`plugins/ops-gateway/lib/client.js`）—— ✅ 已生效；🔴 待你在 DSH 里真点一次那档

- 新增一档 `weeksir-skill-form-view`（label「填参数」），🔴 **段内不硬编任何端口**：台里开哪个地址只由判定口的 `form.url` 给（闸 0d 钉这条）。
- `prompt-reverse` 那档的 8090 iframe **已下线**（`retired: true`，沿用 motion-transfer 的先例：条目留着只为僵尸页签清理，台里给"已下线 + 去哪填"）。
- 🔴 turnTail 那张表**整块摘掉**（202 行），对话里连"一行提示"都不挂 —— `conversation.chat.turnTail` 是 chain 槽、首个 select 非 null 独占，一挂就把官方交付卡整张顶掉。**同一件事不留第二个入口。**
- 排他规则延伸：**配了字段表的那条线，它自己的引擎台不再弹**（判定口回 `reason=form-is-input`，插件侧 `retired` 双保险）。没配表的线（omni/hypit）不受影响 —— 这条由 presence 闸 A16-T 双向钉、B8 在真判定口上验装配。

### 4.4 agent 侧（`scripts/intent.py`）—— ✅ 已加 `prefill` 子命令并真跑通

第三个子命令 `prefill`（写 `defaults` 用），与既有 `<skill>` / `payload` 同一形态：只搬运、判据全在 8100 那一份。🔴 顺带补了**意图档白名单**：以前 `args[0]` 不校验，打错字就往生产意图簿里写一条谁也清不掉的死档（我自己踩了一次）。

### 4.5 端口与闸

- **白名单（现状）**：主口 `8100` + 点名例外 `{8765: omni, 5179: hypit}`。写进 `check-workbench-client.mjs`：0b 每档端口要么主口要么点名例外、0c 实际用到的例外都得在名单里、0e 名单里每个端口都得真被用（**不留已废弃的例外骗自己"已收敛"**）、0f **代码行里零命中 `:8090`**。名单只许变短。
- **8090 收回环回（T8，未做）**：`config.py` 的 `HOST="0.0.0.0"`，且**三处文档化的启动命令原样写着 `--host 0.0.0.0`**（`ops-gateway/lib/client.js` 的 start 提示串、`one-shot-video/SKILL.md`、`prompt-reverse/SKILL.md`）⇒ 暴露面是我们自己教出来的，代码默认值与那三行必须一起改。（记忆里"10-06 已收回 127.0.0.1"是错的，实测 netstat 仍 0.0.0.0。）

## 5. 未验证 / 待拍（必须实测，别当前提）

| id | 问题 | 状态与怎么验 |
|---|---|---|
| U1 | 右栏 iframe 里的表单 POST 到 8100 是否仍同源 | ✅ **已验**：真浏览器打开 `/ui/<skill>` 点提交，Network 无 OPTIONS 预检、无 501（页面由 8100 自己发 ⇒ 同源） |
| U2 | `sidebarRightTabs` 那侧能否拿到 `sessionId` 并随会话切换重挂 | ✅ **部分已验**：判定口按 `?session=` 回表、`intent.py` 拿得到注入的会话 id；🔴 真 DSH 右栏里点一次仍未做 |
| U3 | 一个 skill 同时要"表单页签 + studio 页签"时排他口怎么回 | ✅ **已定并落码**：配了表 ⇒ 顶掉本线引擎台（`form-is-input`）；omni/hypit 无表 ⇒ 不受影响 |
| **U4** | 🔴 **weeksir v2 生视频腿的真实契约**：POST 体字段、`reference_image_ids` 的上传接口、token 怎么拿 | **未验，且验一次就要花钱**：已登录浏览器抓一条真提交 = **225 积分**（15s/720p 实测价，见 `ADR-input-form-single-port.md` §U4）⇒ 必须过报价关口。T9a 的生成腿被它挡住 |
| U5 | 8123（`tools/rh_panel`）、3080（`dsh web`）两个台账外口的归属场景 | 8123 ✅ **已停**（10-16 用户判"映射的方式打开不行"：右栏改成一条 `target=_blank` 链接交给系统浏览器，内核 `main/index.js:14098` 对 http(s) 一律 `shell.openExternal`，不需要镜像也不需要执行端点；`tools/rh_panel` 已移到 `tools/_retired-20261016/`，现网 8123 零监听）。3080 仍未定性 ⇒ 见 T12 |
| U6 | `number`/多槽 `video` 落进各引擎 body 是否真吃得下 | ✅ **两条线已逐字段对过 handler**（one-shot 6 字段、prompt-reverse 3 字段，闸 D 组钉）；其余线随 T7b 逐条对 |
| U7 | 主色取橙 / 蓝 / 官网色 | ✅ **已定**：橙 `#fa6f08`（真浏览器读到按钮 `rgb(250,111,8)` + 字 `rgb(27,31,39)`）；要改只动 `WS_TOKENS` 一处 |
| U8 | 官网字体族在客户机不可用时的降级观感 | 未验：系统兜底排版本截图对比 |
| U9 | `form-uploads/` 里的文件怎么回显成缩略图 | ✅ **已做**：`form_media_file()` 只读回显，realpath 守进 `FORM_UPLOAD_DIR`、扩展名命中该槽允许集（闸 G 组 + 修过一次 Content-Type 返成 `png` 的缺陷） |
| U10 | 🔴 **你的两句原话打架，要你拍**：「合在一个端口」要求 omni 的 8765 也并进来；「omni 不动」把它留在原地。A 给 omni 加 8100 反代（成本=前端绝对路径改造，原 ADR 记 37 处）｜B 两口并存 + 白名单钉死不再加第三个｜C 推翻"omni 不动"、把 omni 也表单化（效率收益最大） | 待产品拍；**现在实现的是 B**。拍 A/C 要重开 `ADR-pipeline-server` 的 mount/反代评估（上轮把它整条作废的理由是"前端不给客户看"，这条对 omni 不成立，已在 ADR §5 更正） |
| U11 | 8123 撞车：`agent_router.py` 默认绑 `127.0.0.1:8123`，实际在听的是 `c:/dsh/tools/rh_panel/server.py` | ✅ **撞车没了**（10-16 rh_panel 摘掉后 8123 无人监听 ⇒ `agent_router.py` 起得来；Windows 允许 `0.0.0.0` 与 `127.0.0.1` 同端并存那条教训留着：**"端口通"证明不了是谁在答**，要判就 netstat 拿 PID + CommandLine） |
| U12 | **M1 的基线（"5-8 轮"）到底是多少** | 未测：挑 2-3 条历史会话逐条数 agent 提问轮次（**按 §2 G5 那三条排除口径数**），分母没量过，"≤1 轮"就是猜的目标 |

## 6. 验收判据（不许拿"我写完了"当结论）

- **F1** 表单页在**真浏览器**里出明/暗两套 + 已提交态三张截图，贴回对话；
- **F2** `check-skill-form.py` 全绿（字段类型/多槽/回填/体积 + F 组品牌断言），每条配反向对照；
- **F3** `check-workbench-client.mjs` 的端口白名单断言生效（反向对照：临时把某档指到 `:9999` 必须红；把 8090 页面摆回右栏也必须红）；
- **F4** 8100 生效只认 **PID + StartTime 晚于文件 mtime**（`schtasks /run` 回"成功"不算）；
- **F5** 一条端到端真链路：点 `/one-shot-video` → 右栏表出现 → agent 回填可验证件 → 提交 → 报价 → `/approval` 点一次 → 成片三件套回到对话。**没跑到这一步就不算交付。**
- **F6** 🔴 **阶段各自独立验收**：框架完 = F1-F5 全过（含试点线端到端）；某条线"迁完" = §7 那四步全过，**少第 4 步（旧入口没摘）不算完**。

## 6.5 本轮落地实况（2026-10-09，写给下一个接手的人，不含计划）

- ✅ **已生效**（PID 46856 · 17:26:50）：字段类型/区间/多槽/单槽体积上限、分发只写一份、回填+依据回显、缩略图只读回显口、右栏「填参数」一档、prompt-reverse 表 + 引擎台下线、六套闸全绿。
- ✅ **真浏览器验收过**（DOM + 计算样式）：回填灌进控件、灰态点不哑、黄底深字、提交转确认态、params 键名即引擎入参、`video` 槽首个用例（拖一个视频 → 按钮转实心橙 → 提交 → `intent.py payload` 回 `video_path`）。
- ✅ **F1 三张截图已交**（headless Chrome + CDP `Emulation.setEmulatedMedia`，落 `productions/skill-form-f1/outputs/`，走 8100 直链内联进对话）：明 / 暗 / 已提交。
  ⚠️ 两条实测常识：**`--force-prefers-color-scheme` 与 `webprefs.preferred_css_scheme` 两条路都不生效**（明暗两张字节一模一样 = 差点交假证据），只有 CDP 真改到了 `matchMedia`；已提交那张顺带证明 16:12 的码确实上线了（视频槽兜底名是 `01-video.mp4` 而不是 `01-image.mp4`）。
- 🔴 **未做，别当成已完**：① 在 DSH 真右栏里点一次「填参数」（F1 的最后一格；本轮抓屏时前台是别的窗口 ⇒ 不抢焦点、不盲点坐标）；② 一条真出片（F5，要过报价与批准）；③ `motion-transfer` 等其他线的 `form:` 段（T7b 还剩）；④ T8 把 8090 收回环回；⑤ T9a/T9b 去 8090 与 `D:/nzdh` 收尾；⑥ T10 把地基（未提交的码与文档）落进版本控制或快照；⑦ T11 意图簿过期记录只增不清；⑧ T12 给 8123/3080 定性。
- ⚠️ **全部未提交 git**（工作区多会话并发 ⇒ 混合改动不提交）。这条本身就是风险，已升成任务 **T10**，不再只写在边界里。

## 7. 迁移策略：框架先行 · 逐线跳转（2026-10-09 产品定）

**为什么这么切**：框架里每一样都只许有一份（渲染器、字段类型、门控/校验、回填判据、按钮完成判据、右栏一档、端口白名单）；每条 skill 的差异**只在它自己那张字段表**。所以框架动一次、逐线只加数据不改码 —— 这不是省事，是防止"每条线一套交互"必然漂开。

🟢 现状已支持"加表不改码"：`skill_form_index()` 从各 `SKILL.md` 的 `intent:` + `form:` 两段建索引，新加一张表会自动出现在 8100 的答复里。
✅ **原来的唯一例外（闸写死"哪个文件、几个字段"）已拆掉**（T6）：A 组改成枚举全仓 `form:` 段 ⇒ 本轮加 prompt-reverse 第二张表，**一行闸代码都没改**（这是"加表不改码"的实证，不是承诺）。

| 归属 | 内容 | 份数 | 落在哪个任务 |
|---|---|---|---|
| **框架（一次）** | 字段类型/区间/多槽/单槽上限 + 门控 + 校验 + 回填 + 按钮判据 | 1 | T1、T2（✅ 已生效） |
| | 渲染器与表单页（`/ui/<skill>` 一个模板 + `WS_TOKENS`） | 1 | T3 |
| | 右栏挂载与排他、端口白名单 | 1 | T5、T6 |
| | agent 读参/回填命令 | 1 | T4 |
| **逐线（每线各一份）** | `SKILL.md` 的 `intent:` + `form:` 段 | 每线 1 张 | T7a（试点）/ T7b（推广） |
| | param 名对到该线引擎 handler（**逐条读码，不假设**） | 每线一次 | T7a/T7b（闸 D 组钉） |
| | 该线的端到端一条真链路 | 每线一次 | T7a/T7b |
| | 🔴 该线旧入口**整块摘掉**（引擎页面不再挂右栏、对话里不留第二个填法） | 每线一次 | T5（框架那一次）+ T7a/T7b（各自那条线） |

**每条线的跳转四步（可停在任意一步，前一步都是独立可用的）**
1. 写字段表（`form:` 段，字段照该线引擎页面抄，🔴 不许放改了也不生效的控件 —— prompt-reverse 就没放 `trim_start/trim_end`，因为引擎只在两个都给时才裁）；
2. 跑闸：param 名逐条对该线 handler 真读的字段（对不上就红，不是"看起来差不多"）；
3. 端到端一条真链路（点 skill → 右栏表 → 回填标记 → 提交 → 报价 → 批准 → 成片三件套回对话）；
4. 摘旧入口（该线的引擎 HTML 不再进右栏；该线若还留着 turnTail 之类旧位，一并摘）。

**两个必须挡住的坑**
- 🔴 **框架未上线前不铺表**：类型与线协议还在改（本轮刚加 `video`/`number`/多槽），先给 8 条线写完表 ⇒ 框架一改，八张表全返工。
- 🔴 **一条线不许留两条路**：迁完的线如果 8090/8765 那张页面还在右栏，就会出现"填表跑的"和"点页面跑的"两套行为（归属、报价、审计各做一遍）—— `ADR-one-shot-inchat-form.md` §2.5 早就定过"不许新增第二条调用路径"。

**逐线顺序（按"腿今天能不能跑通"排，不按重要性排）**
`one-shot-video`（✅ 试点）→ `prompt-reverse`（✅ 已迁完，四步全过）→ `motion-transfer`（本地 RPA 腿可跑，number+区间的首个用例）→ 🔴 `super-clone` / `replica-wan3` **等 RunningHub 禁令解除或换道** → `tvc` / `short-drama` **先沿调用链查它花的是哪条腿**再决定建不建。
