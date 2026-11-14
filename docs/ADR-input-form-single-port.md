# ADR：输入走表单、结果只进对话、UI 面收敛到 8100 一个口

- 状态：**Accepted（两条挂点/范围已由产品拍板）· 未落码**
- 日期：2026-10-09
- 相关：`docs/ADR-one-shot-inchat-form.md`（本 ADR 改判其 P0a 挂点与 P3）、`docs/ADR-pipeline-server.md`（本 ADR 改判其 `:8000` 合并方案）、`docs/ADR-workbench-presence.md`、工作区 `AGENTS.md`『右栏 UI 铁律』『对话内表单』两节

## 0. 用户口径（原话摘要）

> 前期沟通占比时间太多，影响做视频的效率。
> (1) 输入参考 video-skill UI 的输入部分：明确意图后就弹出 UI 让客户填，**输出和 SSE 部分完全砍掉**；
> (2) 客户先在对话里传图/视频和要求的，**agent 自动分析后回填到 UI 字段框**，客户点提交；
> (3) agent 收到输入后跑完整 skill 工作流，**结果显示在 agent 对话中**。
> UI 端口太多，商业化后端口越多交付难度越大、出错概率越高 ⇒ 合在一个端口下不同路径。
> 之前融合的 skill（动作迁移、一句话生视频）还在 8090 环境调用，肯定要脱离。

**拍板两条（2026-10-09）**：
- **表单挂点 = 右栏页签**（推翻 2026-10-08 自己拍的"对话流回合尾部"；仍然一寸不碰 composer）。
- **砍引擎页 iframe，保留官方查看器**：提示词反推→8090 的 iframe 下线；Hypit Studio 与短剧看板保留。
- 🔴 **omni 整条不动（2026-10-09 产品定）**：`WORKBENCHES` 里 omni 那档继续指 `:8765/`，不给它建 `form:` 段，不改它的对话采集口径。
  **为什么这是对的决定而不是偷懒**：omni 的右栏台本身就是那张输入表（`templates/index.html:226-235` 有模式/产品名/时长/主图上传），且 agent 驱动它的 REST 建 run（`app.py:346-359` 的 `title`/`duration`/`product`）⇒ 客户切到右栏**看到的就是已填好的表**，本 ADR 要做的形状它已经天然具备。
  ⚠️ **8765 由此成为"UI 面只留 8100"这条的书面例外**（一口保留、不并入、不改造）。要改的只有：不许多条 skill 再各自加一个新口。
  未实测的余数：客户在 iframe 里改了表之后，agent 需重新 `GET /api/run/<name>` 才读得到（无反向推送）—— **不许当它已通**。
- 🔴 **hypit 同样整条不动（2026-10-09 产品定）**：5179 那个 `hypit studio` 官方查看器保留原样，不给 hypit 建 `form:` 段（它的输入本来就是 `.svml`/`.svrun` 工程文件 + 关口 A 的方案/pricing，换成交互表单反而是倒退）。
  ⇒ **例外两条**：`8765`(omni) 与 `5179+`(hypit studio) 冻结。

### 0.5 于是这条判据取代"只留一个端口"（要落成闸，不靠纪律）

> **存量例外冻结，增量一律挂 8100 的路径，不得再开任何新端口。**

- 白名单 = `{8100, 8765, 5179}`（8765/5179 只许 omni/hypit 这两条已冻结线用）。
- 落码：`check-workbench-client.mjs` 加一条断言 —— `WORKBENCHES` 每档的 `fallback`/iframe 端口必须命中白名单，加一档新工作台指向别的端口就红。**"端口随技能数线性增长"是被这条闸住的，不是被文档住意住的。**
- 已停（**U5**，2026-10-16）：8123 那份是 `tools/rh_panel` 的无头镜像面板 —— 用户判"映射的方式打开不行"，右栏那一格改成一条 `target=_blank` 链接交给系统浏览器（内核 `main/index.js:14098` 对所有 http(s) 走 `shell.openExternal`），面板移到 `tools/_retired-20261016/`、现网 8123 零监听 ⇒ 剩下 3080（`dsh web --no-open`）仍未定性，不在这条白名单里。

## 1. 现状事实（每条都带出处，实测非叙述）

### 1.1 在听的端口（netstat 拿 PID + `Get-CimInstance Win32_Process` 读 CommandLine，2026-10-09 13:00）

| 口 | 绑址 | 进程 | 性质 | 是否有 HTML 操作台 |
|---|---|---|---|---|
| 8090 | 🔴 `0.0.0.0` | `python -m uvicorn app:app` | 引擎 238 路由 | **32 页**（`backend/frontend\*.html`，路由桩 `app.py:402-545`） |
| 8100 | 127.0.0.1 | `pythonw backend\model_admin.py` | 网关/表单/批准/presence/直链 | 3 页（`/`、`/approval`、`/api/film/board`） |
| 8765 | 127.0.0.1 | `pythonw app.py`（video-ui/omni） | omni 引擎 34 路由 | 2 页（`/`、`/focus`） |
| 8123 | 已停（10-16） | ~~`c:\dsh\tools\rh_panel\server.py`~~ ⇒ 现在归 `agent_router.py`（默认同号，未起） | 台账外多出的一口，镜像面板已摘 | 否 |
| 3080 | 127.0.0.1 | `dsh web --no-open` | DSH CLI 自带 | 是 |
| 5179-5190 | `::1` | `hypit studio` 按需 spawn（`hypit_bridge.py:369-394`） | 官方工程查看器 | 是 |
| 8091 | 未监听 | `D:\nzdh` | **已下线**（见 §4） | — |

### 1.2 右栏现在的四档自动台把"UI"和"引擎端口"焊死了

`C:\dsh\plugins\ops-gateway\lib\client.js:26-50` 的 `WORKBENCHES`：流水线→iframe `:8765/` · Hypit Studio→iframe `:5179` · 短剧→`:8100/api/film/board` · 提示词反推→iframe `:8090/prompt-reverse` · 动作迁移→`retired:true` · 模型网关→`scope:"ops"` 不自动弹。
⇒ **端口之所以是交付难度，不是因为引擎有几个进程，是因为引擎页面被当成客户界面挂进了右栏。**

### 1.3 输入侧的表单机制已经存在（比"要从零建"多得多）

| 环节 | 位置 |
|---|---|
| 字段表唯一源 | 各 `.dsh/skills/<id>/SKILL.md` frontmatter 的 `form:` 段（行格式 `key\|type\|label\|必填\|选项\|引擎参数`） |
| 解析 | `backend/model_admin.py:655 parse_skill_form`、`:652 _FORM_TYPES`、`:741 skill_form_index`（`intent:` → skill 映射） |
| 门控 | `:777 form_gate_view` —— 源 = `hypit_bridge.intent_map()` + 24h TTL；没登记 ⇒ `show:false` |
| 提交与校验 | `:814 form_submit`（必填/选项归一/服务端一份校验）、`:799 _form_safe_name`（ASCII 防穿越）、`:1263` 只听环回 |
| 载荷落盘 | `<工作区>\.dsh\form_payload.json` + `.dsh\form-uploads\<sha12>` |
| agent 唯一读命令 | `python scripts/intent.py payload` → `C:\dsh\scripts\intent.py:56-74` |
| 渲染器 | `C:\dsh\plugins\super-clone-ui\lib\client.js:1335-1532`，8 秒轮询，挂 `conversation.chat.turnTail`（`:1517`） |
| 闸 | `C:\dsh\scripts\check-skill-form.py`（B 组钉挂点与不碰 composer，E 组真跑服务端 `form_submit`） |

### 1.4 但要服务全部 skill，缺的正好是这几样

1. **采用率 = 1/40**：只有 `one-shot-video` 有 `form:` 段（6 字段）。`check-skill-form.py:22` 把 SKILL.md 路径写死、`:60` 断言"正好 6 个字段" ⇒ 加第二张表必先改闸。
2. **没有 `video` 字段类型**：`_FORM_TYPES = ("text","textarea","select","images")`。而 `one_shot.html:111-158`、`super_clone_wan3.html:397-459`、`replica_wan3.html:272-318`、`prompt_reverse.html:414-694`、`motion_transfer.html:101-157` 五张页面的**头号必填项都是"爆款/参考视频"上传**。
3. **一条表只认第一个 images 槽**：`model_admin.py:845` 用 `next(...)` 取第一个 images 字段，`files` 是一维数组 ⇒ 表达不出 super-clone 的"产品图 1-10 + 面部图 + 场景图"三槽；上限还写死 7 张/8MB（`:721-722`）。
4. **没有 `number` 类型**：`motion_transfer.html` 有 8 个数值参数，`8765/templates/index.html:233` 时长是 number 10-90。
5. **🔴 完全没有回填通路**（用户要求 (2) = 零实现）：`intent.py` 只有 `<skill>` 和 `payload` 两个子命令；`form_gate_view` 的 `params` 只在"已提交"时回显（`:794`）；渲染器 `SF.values` 永远从空开始（`:1373`）。顺带一个既有缺陷：填一半刷新页面，未提交的值全丢。

### 1.5 沟通成本的量化（这就是"前期沟通占比太高"）

omni 从"客户说开始"到能起跑要 8-9 项：模式、产品/战役名、目标时长、产品主图、细节/角色图、卖点文字、平台、国家+语言包、叙事结构。其中卖点文字、全部图片、3 选 1 故事方向、语言包在 `omni/SKILL.md:40-42,56-59,98` 明写是**对话采集**，另外三个批准口也在对话里。⇒ 一张表 = 把 5-8 轮问答压成 1 次填写。

### 1.6 输出侧的事实

- 🔴 **"SSE 完全砍掉"要按【腿】定性，不是一刀切**（2026-10-09 实测更正；本 ADR 上一版写"全系统只有一条"是**错的**）：

  | SSE 端点 | 谁在用 | 该不该砍 |
  |---|---|---|
  | `backend/app.py:7640`（8090 `/api/one-shot/events`） | `frontend/one-shot-video.html` 的 `EventSource` | ✅ **砍** —— 随引擎页面下线自动消失，0 成本 |
  | `backend/services/hypit_workspace.py:687` | hypit 工程的生成流 | ❌ **不砍** —— hypit 已冻结（§0） |
  | `backend/agent_router.py:167`（`/v1/chat/completions`） | **DSH 聊天本体的流式输出** | ❌ **绝不能砍** —— 砍了聊天就不能逐字出，它不是"输出 UI" |

  ⇒ 判据与本机 RunningHub 禁令同一条纪律：**跟着腿走，不跟着目录/名字走**。右栏本身从来是 5s/8s 轮询（`ops-gateway\client.js:172`、`super-clone-ui\client.js:1344`），不是 SSE，所以"右栏不再画输出"这条改动**不涉及任何 SSE**。
- ⚠️ 顺带一条端口实况（10-16 更新）：`agent_router.py` 默认绑 `127.0.0.1:8123`，那个号原先被 `tools/rh_panel` 占着 ⇒ 谁后起谁失败；面板摘了，现在 8123 没人监听、这条撞车自然解开。仍要记的教训：Windows 允许 `0.0.0.0` 与 `127.0.0.1` 同端并存，**"端口通"证明不了是谁在答**（netstat 拿 PID + `Get-CimInstance` 读 CommandLine 才算）。
- 🔴 **DSH 对话流永不能内联视频**：`dsh-client-ui-chat/lib/client.js` 里 `<video` 零命中（本次复查）；本地 `C:\...` 路径不渲染，只有 http(s) 直链渲染成图。⇒ "结果在对话中"= 图片直接看 + 视频走"路径 + 8100 直链 + 抽帧贴图"，即 `scripts/deliver_pack.py` 已经在做的三件套。**不新造机制。**
- 审帧（贴关键帧图）在对话里成立；成片试听只能靠直链点开。

## 2. 决策：把两个"端口"概念分开，UI 面收敛到 8100

| 层 | 收敛后形态 | 现状 |
|---|---|---|
| **交互面**（表单 / 批准 / 产物直链 / presence 判定） | **只有 8100 一个口、一个 origin**，各 skill 走路径：`/ui/<skill>`（表单页）、`/approval`、`/api/skills/form`、`/api/film/file` 直链 | 8100 已经是这个形态：只绑 127.0.0.1、有 1 分钟看门狗、CORS 按需 `_send(cors=True)`、右栏已用 iframe 嵌过它的页面（film board）⇒ **通路已验证，不需要新机制** |
| **引擎 API 面**（8765/8090 的 `/api/*`） | agent 进程内部调用，**不是 UI、不对外暴露**；3.0 再服务端化 | 8090 现在 `0.0.0.0` + 无鉴权 + 含执行型端点 |
| **引擎 HTML 面** | **删**（不再挂进右栏，不再是交付物） | 32 页（8090）+ 2 页（8765） |

⇒ **`ADR-pipeline-server.md` 的 `:8000` 单网关方案作废**（本 ADR 改判，见 §5）。理由：它的全部成本都在"把各流水线前端 mount 进一个进程"（video-ui 前端 37 处绝对路径改造 + a2wsgi 挂 Flask + FastAPI `root_path` 实测），而新方向下**前端根本不给客户看** ⇒ 这三件事一件都不必做。要收敛的是"交互面端口数"，不是"引擎进程数"。

**排他规则随之改写**：一档 = 一个 skill 的台，台里画的是**这张表**（8100 `/ui/<skill>`），不再是引擎页面。唯一例外 = Hypit Studio：它是**编辑 `.svml` 工程**的官方查看器（不是输出显示器，无替代物），保留为同一 skill 的第二页签。⇒ 旧铁律"选了就要看到界面"的兑现物从"引擎台"换成"表单台"，**铁律一个字不删，只是画的内容变了**。

## 3. 回填（要求 (2)）的设计与一条必须守住的判据

```
agent 侧：scripts/intent.py prefill --set '<json>'   # 新增第三个子命令，只写不判
服务端：  POST /api/skills/form  body.stage="prefill" → <工作区>\.dsh\form_prefill.json（会话级）
门控：    form_gate_view 合并 → 回 fields + defaults（每项带 source:"agent"）
渲染器：  SF.values 用 defaults 初始化，字段旁标"agent 回填 · 可改"
```

- 🔴 **`prefill` ≠ `submitted`**。现在 `submitted:true` 会渲染"已填好，参数我这边收到了"（`client.js:1506-1508`）。若 agent 回填被算成已提交，就等于 **agent 替客户交了表** —— 那是"代点头"的另一种形态，与 2026-10-08 定的"不代发/不代点头"同源。必填校验、TTL、param 归一**只在 8100 那一份**，agent 侧与渲染器都不抄第二份。
- 图片回填是通的：聊天附件落在 `%APPDATA%\dsh-desktop\harness\attachments\v1\…`（`dsh-attachment-local/lib/index.js:661,986`），agent 有只读路径（`dsh-llm/lib/index.js:2048`）⇒ 复制进 `.dsh\form-uploads\<sid哈希>\` 复用现成的 ASCII 安全命名即可，引擎要的正是本地路径（`ADR-one-shot-inchat-form.md:37-41` 已坐实"图片只能上传、贴链接是假字段"）。
- "自动分析"花的是看图/看片腿（`video.analyze` / `text.complete`）⇒ 按 2026-10-09 口径**免批不免账**，直接跑，跑完在结果里带一句实扣分。

## 3.5 表单页的品牌与视觉规格（2026-10-09 产品加的要求：要有品牌感，不能是简陋表格）

**现状（实测）**：现在那张表是 `client.js:1395-1414` 里 20 行内联 CSS 的对话流卡片 —— 一条边框 + `border-radius:12px` + **裸 `<input type="file">`**，本来就没为右栏整屏设计过 ⇒ 产品说"简陋"成立。
**品牌色已经有两套**：批准卡 `--brand:#4d8dff`（蓝，`model_admin.py` APPROVAL_PAGE 的 `:root`）vs 产品面 `--weeksir-accent-orange:#fa6f08`（橙，`super-clone-ui/lib/client.js:16`，出现 9 次，还把 DSW 的 `--dsw-alias-brand-primary` 覆盖成橙）。
⚠️ **weeksir 官网色抠不到**：`weeksir.com` 是 Nuxt SPA，未登录时 DOM 为空（真浏览器实测 `body` 计算样式/h1/button 全零命中，只有 `title="WEEKSIR"`）⇒ **不许拿想象中的官网色当依据**，需要产品给一份色值或从已登录页面读。

**四层规格（每层都可落码、可断言）**

1. **Token 层**：一份 `WS_TOKENS` CSS 常量（`--bg/--panel/--line/--fg/--mut/--brand/--ok/--bad/--warn/--radius/--shadow`），照批准卡那套 `:root` + `@media (prefers-color-scheme:light)` 明暗自适应（已验证可跑）。批准页与表单页 `<style>` **共用同一份** ⇒ 全站只有一处 hex 清单。主色待产品拍：推荐橙 `#fa6f08`（同屏 dock 卡片就是它，蓝降为链接/info 色）。
2. **骨架层**：顶部品牌带（logo `/assets/weeksir-logo.png` + slogan「专注电商AI落地」+ 技能 chip + 这条片要干什么的一句话）→ 分区「素材 / 内容 / 风格 / 高级（默认折叠）」→ 底部吸底操作条（提交 + 一句"提交只交参数，花钱仍要点批准"）。右栏实际宽度做**单列**，不塞两列。
3. **控件层**（这几样才是"体验感"的来源，也都是要写的代码）：
   - 图片字段 = 拖拽 + 点选 + 多文件，出缩略图、第一张自动标「主图」、可拖排序、可删；🔴 不许裸 `<input type="file">`。
   - 视频字段 = 同上，附时长/体积；
   - 选项 ≤4 个用 chip 单选组（沿用 dock 卡片 `hover` 描品牌色 + `translateY(-3px)` 的语言），长清单才用原生 select；
   - 时长/数量 = 步进 chip 或滑尺，不让人打字；
   - 必填 = 未填时 label 描红并把视口滚到那一格，而不是只弹一句"必填没填"；
   - 🔴 **agent 回填必须标出来**：值旁小 tag「AI 已填 · 可改」，悬停显示依据（"从你上传的 3 张图读到"）—— 否则客户分不清哪些是自己填的、哪些是 agent 猜的，这条直接服务 §0 的要求 (2)；
   - 🔴 **提交按钮两态（2026-10-09 产品定：未填完=灰、填完=黄）**：灰态**仍可点**——不许做成哑按钮，点了要把第一个缺失项描红+滚过去并在按钮旁报「还差：X、Y」；黄态实心提交。文字必须深色（实测 `#fa6f08` 配白字对比度只有 2.87:1 不过 AA，配 `#1b1f27` 是 7.3:1）。"黄色"默认按品牌橙 `#fa6f08` 实现，要更亮的黄只改一处 token（按钮上不许出现第二个 hex）。
     完成判据 = 纯函数 `formIsComplete(fields, values, files, defaults)`，与服务端同源、字段表下发：文本非空／number 在区间／select 必填已选／每个必填文件槽 ≥1（多槽各判）／**`defaults`+`default_files` 计入完成度**（回填了还是灰=白回）／MIME+体积预筛。细则与值表见 `PRD-input-form-single-port.md` §4.2.1。
   - 状态 = 提交中转态/已提交转确认态（列出最终参数供回看）/8100 没回话明说是故障（不许谎报成"没有表单"）；空态 = 没登记意图 ⇒ 整块不画（铁律，不是样式）。
4. **文案层**：短句、大白话、去表单腔；字段名跟着销售语境（"商品卖点"而不是 `selling_points`）。

**硬约束**：8100 是手写 `BaseHTTPRequestHandler` ⇒ **一个 HTML 模板 + query 参数**（`/ui/<skill>`，字段表来自已有的 `skill_form_index()`），🔴 不许每条 skill 写一页 HTML（40 条线 = 40 个页面就是新泥球）。代价如实说：共用 token 要动批准页那 100+ 行内联 CSS ⇒ 动之前先截图留存，改完再截对比，不靠肉眼说"没变坏"。
**验收**：真浏览器打开 `/ui/<skill>` 交明/暗两套 + 已提交态截图；`check-skill-form.py` 加 F 组钉 ① 全站只有一份 `:root` token ② 引 logo 与 slogan ③ 图片字段不是裸 file input（有拖拽容器与缩略图渲染）④ 明暗自适应那条 media query 在 —— 每条配反向对照。

## 4. "去 8090"的真实现状（产品认知需更正）

- **动作迁移：已于 2026-10-08 脱离 8091 进程。** `gateway.py:1284-1285`「这里【直调本地包】，不再有 HTTP 转调、不再要求 8091 起着」；purpose `motion-rpa` 已绑价（`:97`，dispatch `:2122-2125 → _motion_rpa_run`）；豁免表 `SPEND_APPROVAL_AT_SOURCE` 已清空（`:2431-2434`）；右栏第 7 档 `retired:true`。
  残留的是**数据依赖**：`backend/services/motion_rpa/config.py:17,38,65` 仍读 `D:\nzdh` 的 `.env`、RH 账号池与旧 `tasks.db`；🔴 `D:\nzdh\config.py:13,18,20` 的 `IMAGE2_API_KEY`/`MIMO_API_KEY`/`OPENAI_API_KEY` **默认值是明文 key**，`D:\nzdh\rh_accounts.json` 存 3 组手机号+密码 ⇒ **建议轮换**。
  ⚠️ `C:\dsh\scripts\check-motion-rpa-lane.py` 的断言仍假设 8091 转发并引用 `gw.NZDH_OUTPUT_DIR`（该符号已不在 gateway 里）⇒ **这条闸已过期，不许当它是绿的**。
- **一句话生视频：三腿都还在 8090。** 编译腿账本外直连厂商（`app.py:7418` → `prompt_compiler.py:376-378` 裸 httpx，走 `OPENAI_*`/`SR_GROK_*`）；服务壳（上传/任务表/SSE/history）在 8090；生成腿是 Playwright RPA（`oneshot_video.py:339` → `pipeline/step5_video_generate.py:1095 run()`）。
  但 `ADR-one-shot-inchat-form.md §5.5` 已判 A：weeksir **有 REST v2、token 鉴权、实测价 15s/720p = 225 积分**（缓存 `f_000144`/`f_0001b8`），且 `client_request_id` 是幂等键。缺的是 U4 三块（POST 体、`reference_image_ids` 上传接口、token 获取），**翻案动作 = 已登录浏览器抓一条真提交 = 花 225 分 ⇒ 必须过报价关口**。
- 账本外直连的腿清单（不止这两条，本 ADR 不顺手全修）：prompt-reverse 的 MiMo/Grok（`prompt_reverser.py:570,694`）、voice-clone 的 RunningHub OpenAPI、动作迁移 Step2/3 生图（`motion_rpa/image2_client.py:105`）。

## 5. 对既有文档的三处改判（不删旧文，就地标注）

1. `ADR-pipeline-server.md:3` 状态 → 改判：**`:8000` 前端合并方案作废**，改为本 ADR 的"交互面收敛 8100"。它的 **Gate 0 三条强制门禁照旧有效**（只绑环回 / 执行端点鉴权+只读 / 生成计费在服务端）—— 收敛≠安全这条尤其要留。
2. `ADR-one-shot-inchat-form.md` **P0a 挂点**：`conversation.chat.turnTail` → **右栏页签**（8100 `/ui/<skill>`）。"不碰 composer、不 setDraft、不 submit、不代发"四条一寸不动；`check-skill-form.py` 的 B3/B4 从"钉 turnTail"改成"钉右栏注册 + 钉永不出现 `conversation.input.overlay`"。工作区 `AGENTS.md`『对话内表单』节的挂点判据同步改字。
3. `ADR-one-shot-inchat-form.md` **P3（右栏工作台 = 进度 + 成片）作废**：过程与结果一律进对话（`deliver_pack.py` 三件套），右栏只有表单。§3 那句"复用 presence 机制加一档"仍成立，只是档里画表单。

## 6. 阶段（每步独立可用；2026-10-09 二次校正：与 §0 的"omni/hypit 冻结"和 RunningHub 禁令对齐）

🔴 **分两大阶段（2026-10-09 产品定"先改交互框架，再一个 skill 一个 skill 跳"）**：
**A 框架**（P1-P3 + 表单页 + 挂点 = tasks T1-T6，只做一次、只此一份）→ **B 逐线跳转**（每线只加一张 `form:` 段，四步：表 → param 对闸 → 端到端 → **摘旧入口**，= T7a 试点 / T7b 推广）。
判据：**B 阶段任何一条线要改 A 的代码，就算框架缺陷**，回 A 修，不许为该线开洞；A 未完备前不铺第二条线的表（类型与线协议还在动，先铺=返工）。细则见 `PRD-input-form-single-port.md` §7。

| 阶段 | 做什么 | 依赖 | 判据 | 状态 |
|---|---|---|---|---|
| **P0** | 🔴 8090 改绑 `127.0.0.1` —— `config.py:474 HOST` 默认值 + **三处文档化的 `--host 0.0.0.0`**（`ops-gateway/lib/client.js:41`、`one-shot-video/SKILL.md:52`、`prompt-reverse/SKILL.md:30`）一起改；只改代码不够，暴露面是那三行教出来的 | 无 | netstat 实测 LocalAddress=127.0.0.1（生效只认 PID+StartTime） | 待开工（=tasks T8；独立快修，但重启 8090 前必须独立跑 preflight） |
| **P1** | 字段表达力：`_FORM_TYPES` 加 `video`/`number`、第 5 列复用为区间、images/video **多槽**（`_form_store_files`）、必填**文件槽**纳入校验 | 无 | 闸 G 组 21 条（含多槽不串、单值/数组按字段表判、缺 field 拒） | 🟡 **已落码·未重启·未生效** |
| **P2** | 回填通路（§3）：`form_prefill` + `defaults`/`default_files` 合并 + 路径必须落在上传目录内 | P1 | 闸 G13-G20 钉 `prefill ≠ submitted` + 变异对照（已做：改成写载荷/把 submitted 造假 ⇒ 都红） | 🟡 **已落码·未重启·未生效**；agent 侧 `intent.py prefill` 子命令未做（=T4） |
| **P3** | 闸泛化：`check-skill-form.py` 去掉"钉一个文件、6 个字段"，改成枚举全部 skill + 矩阵断言；`check-workbench-client.mjs` 加**端口白名单** `{8100, 8765, 5179}` | P1 | 反向对照两条（bogus 类型必红、某档指 `:9999` 必红） | 待开工（=T6） |
| **P4** | 表单页品牌化：8100 一个 `/ui/<skill>` 模板 + 一份 `WS_TOKENS`（批准页共用）+ 提交按钮灰/黄两态（§3.5） | P1,P2 | 真浏览器交明/暗/已提交三张截图 + F 组断言 | 待开工（=T3，🔴 主色与缩略图回显口未定） |
| **P5** | 挂点搬迁：表单进右栏一档；**只有 `prompt-reverse` 的 8090 iframe 下线**（换成表单页）；🔴 **omni(:8765) 与 hypit(:5179) 不动**；turnTail 整块摘掉、对话里只留一行指引 | P3,P4 | 过 `check-workbench-client.mjs` 0 号断言；B4 改判成新挂点判据 | 待开工（=T5） |
| **P6** | 建表：**one-shot-video**（已有表，迁挂点即端到端自检）→ **prompt-reverse**（`video` 槽的首个用例）。🔴 **super-clone / replica-wan3 / H3 这几条本轮不建表** —— wan3 腿被默认断开（`WAN3_RH_ENABLED=False`、`rh_video_guard` 白名单只有 `edit`/`image`），给跑不通的线做输入 UI 是反的；`tvc`/`short-drama` 要先查它走哪条腿再建 | P5 | 每条线 D 组把 param 名对到引擎 handler 真读的字段 | 待开工（=T7） |
| 之后 | one-shot 去 8090（编译腿走 `gateway.py call text.complete`、服务壳搬脚本、生成腿走 weeksir v2） | 🔴 U4 要先花 225 分抓包 | 沿用 `ADR-one-shot-inchat-form.md` | 未启动 |

🔴 **原表里"给 omni 建 form 段"这一行已删**：2026-10-09 产品定 omni 整条不动（它已经是本方案要的形状，见 §0）。

## 7. 未验证（必须实测，别当前提）

- **U1**：右栏 iframe 嵌 8100 页面时表单的 `fetch` 是否仍算同源（`/api/film/board` 已证 HTML 能嵌，但 board 是只读页、不发 POST；`form_submit` 那条 `Content-Type: text/plain` 简单请求从 iframe origin `127.0.0.1:8100` 发出，理论上同源免预检，**未实测**）。
- **U2**：`sidebarRightTabs.register` + `slots.inject("sidebar.right.pane.tab")` 能不能拿到 `sessionId` 并随会话切换重挂（现 turnTail 用 `currentSessionId(ctx)`，右栏那侧未验）。
- **U3**：一个 skill 同时要"表单页签 + Hypit Studio 页签"时，排他判定口（`PRESENCE_KEYS`）怎么回两档 —— 现在的契约明写"同时只挂一个"。
- **U4**：weeksir v2 的 POST 体/上传接口/token（= P6 前提，要花 225 分）。
- **U5**（10-16 半收）：8123 的 `tools/rh_panel` 已定性并摘（右栏不再"映"RunningHub，见 §端口那节）；3080（`dsh web`）这个台账外口是谁起的、能不能停，仍未查。
- **U6**：`number`/多槽 images 落进 `form_submit` 后，各引擎 body 形状是否真吃得下（要逐条对着 `frontend/*.html` 的提交体核，不能假设）。
