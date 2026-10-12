# Agent 工作约定

本工作区给 DSH Desktop 用。**不做自动路由**：谁被显式调用就干谁的活。框架见 `docs/AGENT-FRAMEWORK.md`。

> **这一层是"工作区级"**：内核只沿 `projectRoot` 往上找 `AGENTS.md`/`CLAUDE.md`（`@deepseek-ai/dsh-agent-instructions/lib/index.js:578`），**客户机读不到本文件**。
> 跨工作区、跨客户都要成立的行为约束写在**全局层** `$DSH_HOME/AGENTS.md`（本机 = `%APPDATA%\dsh-desktop\harness\AGENTS.md`，同文件 `:561-568`）。
> 分工：全局层 = 怎么干活（把握阈值、写代码与验收的通用纪律）；本文件 = 这个产品怎么干（路由、意图登记、网关契约、交付位）。冲突时以全局层为准。

## 路由：以显式调用为准（🔴 不猜意图）

**2026-10-02 用户定：路由一定会理解错，所以不路由——以"调用技能"为准。**

- 用户敲了 `/omni`、`/hypit-pipeline`、`/ai-film-router`、`/short-drama`… ⇒ 就按那个 skill 走，**第一步先登记意图**（见下节铁律），再干活。
- 用户**没点 skill**、只描述需求（"帮我设计一个短剧带货的钩子"）⇒ **不要猜**。只做两件事：
  1. 正常回答能直接回答的部分（文案、分析、建议本身不需要引擎）；
  2. 需要进引擎时，把候选列出来让他点，一句话就够：
     `要出片的话点一下：/omni（电商产品视频）· /hypit-pipeline（video-as-code 工程化）· /short-drama（剧情带货短剧，含 RAG 剧本知识库）· /ai-film-router（影视短片单阶段）`
- **禁止**因为"这句话像某条流水线"就擅自进入某个 skill、擅自建 run、擅自起引擎。
- 目录里没有的 skill 不要假装会做。未上架的（`/super-clone`、`/cs-*`）不接管。
- 基础工具 skill（`/video-dewatermark`、`/video-enhance`、`/voice-clone` 等）：一次性工具腿，不猜意图、不进生成流水线，客户显式点了才登记/挂台。
  - `voice-clone`（声音克隆）是**例外腿**：直连 RunningHub OpenAPI（AI App `1965614643077070850`），**不经 gateway.py、不在网关账本上**；
    报价按 RunningHub 账户余额 + instanceType 口径说并等同意，跑完把 `usage`（consumeMoney/consumeCoins/taskCostTime）报回。

> 为什么这么定：`omni` / `hypit-pipeline` / `product-video-pipeline` 三个 skill 的描述都含"剧本→关键帧→出片"，
> 电商带货与影视创作又天然重叠（2026-10-02 实测：一句"短剧带货钩子"被漂到 omni）。
> 靠描述匹配去路由必然反复错，所以判定权交回给用户。

## 🔴 右栏 UI 铁律：先明确意图，才有 UI

**新会话一条 UI 都不许出现。** 只有**用户显式点了某个 skill**之后，才允许挂对应的台，且**排他**——同时只挂一个。

被调用后的**第一个动作**、任何引擎操作之前，登记意图（这一条和"花钱前必须报价"同级）：

```
python scripts/intent.py <skill> [任务名]
#   <skill> = 用户刚点的那个：omni | hypit | super-clone | product-video | film | prompt-reverse | motion-transfer
#   short-drama 与 ai-film-router 都归 "film" 档（登记 film）；它俩是"点了就走"的总控工作流，
#   显式调用后先登记、再干活（排他挡掉 omni/hypit 台）
#   prompt-reverse 是第 6 档：台 = 8100 的表单台（/ui/prompt-reverse）。2026-10-09 起 8090 那张引擎工作台页
#   【不再进右栏】（输入口只留表单一个；判定口照旧真探 8090，并把页面地址当信息回给 agent），任务名传这条视频的别名
#   motion-transfer 是第 7 档（动作迁移）：台 = 8091 工作室，两格——左是 8091 自己截的 RunningHub 实时画面
#   （runninghub.cn 回 X-Frame-Options: SAMEORIGIN，原页嵌不出东西；做转发登录态的代理=把账号凭据递出去，不做），
#   右是工作室页 ?session=<会话id> ⇒ 历史按会话分。任务名传这条片的别名
#   one-shot-video（一句话生视频）登记 product-video 档（KNOWN_SKILLS 里的产品视频档、无看板台，只起排他作用）
#   只有【带引擎】的流水线才登记；纯提示词/知识库类单 skill（seedance-*、film-* 单阶段写作类）不登记、不挂台
#   换产品线 = 再登记一次覆盖；做完可 intent-clear <会话id>
```

- 🔴 **为什么是 `scripts/intent.py` 而不是直接调 `hypit_bridge.py intent`**（2026-10-03 实测）：
   后者要写 `D:\video-skill\.gateway_intent.json`，那在**工作区外**，DSH 自带 agent 的文件沙箱直接拒
   （报 `sandbox escalation … is not strictly wider`）⇒ 用户明明点了 skill，右栏却一条 UI 都不出。
   工作区脚本只写 `<工作区>\.dsh\intent.json`，8100 的判定口两个源合并读（同一会话取 ts 更新的那个）。
- **会话 id 是 DSH 注入子进程的托管变量**：PowerShell 里写 `$env:DSH_SESSION_ID`，**不是** bash 的 `$DSH_SESSION_ID`；
   读不到脚本会明说并以退出码 2 结束（别改成猜一个 id）。hypit 要把**工程路径**当任务名传（`.svrun`/`.svml` 或工程目录），
   查看器才知道开哪个；只传中文任务名 ⇒ 判定口会回 `no-project`（台里现在会分开显示这两种"没起"）。
- **查看器由 8100 自己在轮询里起**（`viewer_tick`，同一会话 120 秒冷却），agent 不需要再敲任何启动命令。
- **意图有效期 24 小时**（`HYPIT_INTENT_TTL` 秒级可调，2026-10-03 定）：过期 ⇒ 不再弹台，也**绝不**被轮询自动起查看器（这条现在是安全阀：没有 TTL，任何一条几天前的登记只要那个会话被点开一次就会再起一个 studio 进程）。
   过期是【自己到期】，不是被别的台挤掉——「意图只能被显式覆盖/清除」那条禁令不变；agent 再跑一次 intent 立刻恢复。
- 🔴 **静默单有人查了**（2026-10-11 现网：72 小时内 9 次点单，5 次没进流程，而**盘上零条红** —— 现有判据全要求"先有意图/目录"才生效，第一步没走 ⇒ 没有任何一个闸被调用）。
   `python scripts/audit-skill-entry.py [--hours 24] [--all]` —— 只读，退出码非 0 = 有静默单。它读 DSH 转写里
   **`<skill_content name="x">`（内核真把 skill 装进了上下文，比斜杠命令强）+ `/lane` 斜杠**当"点了"的证据，
   再核四份事实（合并两份登记簿 / `inputs/` 落盘 / 回填 / 交表），判 `SILENT`（点单后一件没发生）
   / `HALF`（只登记没收料）/ `OK` / `RUNNING`（30 分钟窗口内不判）。
   ⚠️ 两条防假红的规则别改：**只有配了表单的档才要求回填/交表**（omni/hypit/film/motion-transfer 没表单，
   登记了意图就算进了流程）；**两份意图簿必须合并读**（工作区 `.dsh/intent.json` + 网关 `.gateway_intent.json`，
   同 `hypit_bridge` 判定口那条规则）。自测 `--selftest`（11 条，含 14:40 真产物与恒 OK 的变异桩）。
   它仍是"有人跑才响"的形状 —— 要绑到必然发生的动作上，得让宿主插件在点单时上报 8100（见下条待拍）。

判定规则（`GET 127.0.0.1:8100/api/workbench/presence?session=<id>` 一次回全部）：

| 情况 | 结果 |
|---|---|
| 有显式登记 | **只**挂 skill 匹配的那个台，其余一律不挂（理由回 `intent-is-<别的skill>`） |
| 没有显式登记 | **零 UI**。盘上的痕迹（旧 claim / 带会话标的 run）**永不触发页签**，只当台里的显示信息 |

- 🔴 **不靠痕迹兜底**（2026-10-02 第四次修订）：实测装了个纯提示词 skill，切回会话却在弹 omni 台——因为一条 67 分钟前的 run 带着该会话的标被当成"佐证"。run 不会过期 ⇒ "跑过一次"就成了永久产权，那仍然是在猜意图。
- **纯提示词 / 知识库类 skill 不登记意图**：`seedance-*`（那 13 个 prompt 库）、`film-*` 写作类都没有引擎，挂了也只是空壳。它们照常干活（产出就是文案/提示词本身），只是不占右栏。
- 🔴 **选了就要看到界面**：`intent <会话> hypit` 这一步会**顺手把 studio 查看器起起来**并 claim 给这条会话（`studio_up`：解析该会话的工程→已有查看器就复用→否则从 5179 往上找空闲口，无 shell、路径限工作区 `productions/` 内、15 秒内没应答就报出来）。触发者是 agent 跑 intent 这条命令，所以**不加 HTTP 执行端点**（那才是要鉴权的面）。
- 引擎动作会自己钉意图（**这条现在才是真保险**）：omni = `store.create_run()` 带 session 时自动登记 `omni`；hypit = `studio-claim` 自动登记 `hypit`。显式 `intent` 命令照旧要跑，但**忘了跑不会再导致"该弹的不弹"**——2026-10-02 实测线上登记簿 0 条是 agent 自己登记的，所以把登记绑到了必然发生的动作上。
- 台弹了但引擎没起 ⇒ 台里显示"引擎没在跑 + 怎么起"，**不藏台**。判定口（8100）自己没回话时显示"判定口没回话"，**不许谎报成"这条会话没意图"**。
- 回归（改这条逻辑必须先过）：`check-intent-rule.py` · `check-workbench-presence.py` · `check-workbench-client.mjs` · `check-intent-anchor.py`（建 run⇒钉意图的锚点，含"登记炸了也不能让建片失败"）· `check-export-gates.py`。**条数不写在这里**（写了就会烂：这一行曾经停在 15/40/28，现在实际是 55/58/59），以各闸自己打印的汇总为准；右栏加一档工作台 ⇒ 前三套必须同步（`check-workbench-client.mjs` 的 0 号断言就是钉这条的）。
- ⚠️ 未接入网关的 skill **不得调用**：`product-video-pipeline` 目前直连 `https://zexapi.com`（`SKILL.md:81`、`scripts/poll_task.ps1:3`），绕开统一网关的预扣/结算/审计，且那条 key 实测欠费。改造到 `gateway.py call` 之前，即使被显式调用也要先告知用户这一条、不要直接花钱。
- ⚠️ **`prompt-reverse` 已上架但范围被砍过**（2026-10-05 用户拍板"只到提示词，不出片"）：它的模型腿在 **8090 引擎进程里直连厂商**（`prompt_reverser.py:570` 的 MiMo、`:694` 的 Grok，通用化/融合走 `gpt_client`）⇒ **不在网关账本上、没有预扣/结算/审计**。所以：**只允许跑视频理解 + 文本改写**；`/api/h3-studio/generate`（出片）、`fuse-product` 之后的生成腿、`publish-rpa` **一律禁调**，用户要出片就报一句"这条不在本 skill"并引导 `/omni`、`/super-clone`、`/hypit-pipeline`（那几条已接网关、会报价会扣分）。把它接进网关是**待办**，不是可以自己扩范围的理由。
- ⚠️ **`one-shot-video`（一句话生视频）两条腿都是例外 route**（2026-10-06 拍板装上）：编译腿在 8090 引擎进程里直连 `OPENAI_*`（gpt-5.6-sol）/ `SR_GROK_*`（grok-4.6）；生成腿是 Playwright RPA 提交 weeksir.com/video 选 seedance-2.0-renlian（复用 `pipeline/step5_video_generate.py` 的 `run()`）⇒ **不经 `gateway.py`、不在网关账本上**。所以：**禁止编造积分价**，报价按 weeksir 平台实扣（按条）说并等同意；`pipeline/step5_video_generate.py` 是 `D:\video-skill\BOUNDARY.md` 整文件保护的爆款复刻线路，**只调不改**。
- ⚠️ **`motion-transfer`（动作迁移·8091）是第 7 档，也是唯一"引擎在别的进程里花真钱"的腿**（2026-10-06 装上）：
   Step5 = Playwright 登录 RunningHub 账号池、操作公共工作流 `2106377827540103169`、等节点 285 出片。
   单价 **60 分/段 = 占位·未实测**（上游按 GPU 秒扣，"1 元=几分"未定）⇒ 报价时必须带上"占位"两个字。
   🔴 **agent 要跑这条只许 `gateway call video.generate/motion-rpa`**（直调本地 `backend/services/motion_rpa`，条子在 `invoke()` 里消费一次）；
   **不许转调 8091 的 HTTP 口** —— 工作台那三个花钱口 2026-10-10 起不再问批准（那一下是人自己点的），
   豁免范围**仅此一端**，判据与代价见『批准粒度』节"例外形状"那条。
   两个已知缺口要如实说：① 8091 的 Step2/Step3 生图与看图腿**仍在 8091 内部直连厂商**（`image2_client`/Agnes），
   不进网关账本；② 8091 **手起、无看门狗、不是 git 仓库**（改前备份：`D:\nzdh\_bak-20261006\`、`_bak-20261008\`、`_bak-20261010\`），
   且 `D:\nzdh\config.py` 默认值里带明文 key（同一把在 `.env` 与 `dist/exe-build/payload.zip` 里都出现过）⇒ **建议轮换**。
   另：RunningHub 节点 600 `FailedTrigger` 会**故意**把上游任务标 FAILED 再抓视频，"上游 FAILED"≠失败。
- 因为"以调用为准"，**不再需要写电商/影视的划界规则**——判定权在用户点的 `/skill`，agent 不猜。

### 🔴 技能表单（输入侧 UI）同一条铁律：先明确意图，才有表单（2026-10-08 定，2026-10-09 改挂点）

- 挂点 = **右栏一档"填参数"页签**，页面由 **8100 自己托管**（`/ui/<skill>`，一个模板服务全部 skill）。
  🔴 **不变量：绝不占输入区** —— `conversation.input.overlay` 永久禁止；2026-10-08 那版挂在对话流回合尾
  （`conversation.chat.turnTail`）**已于 2026-10-09 整块下线**：那是 chain 槽、首个 select 非 null 独占，
  一挂就把官方交付卡整张顶掉。改右栏的根据另见 `docs/PRD-input-form-single-port.md` §4.3。
- 出现条件（**2026-10-11 再改，推翻 10-10 的第三个条件**）= 两个就够：**已登记意图** + 那条线配了字段表。
  🔴 10-10 版把"agent 已回填过"当弹表前置（`reason:"await-prefill"` 不弹），出发点对（弹空表=把归类推回给客户），
  但代价实测更大：现网 20:18 登记 → **20:56 才出表，客户对着空右栏等 38 分钟**，中间看不见 agent 卡在哪、也催不动。
  ⇒ **压力不取消，只换位置**：回填改成【报价那一刀】的硬前置 —— `gateway.py approve` 会先问判定口
  （`form_prefill_blockers`：没回填 / `prefill_gap` 非空 / 必填格还空着 / 判定口没回话 ⇒ **不建条子、0 额度**）。
  一句话记法：**空表可以弹，但空表换不来一次扣费。**
  流程照旧：登记（顺手建目录）→ **立刻归位素材 + 回填不需要理解的格**（视频/产品图/比例）→ 表带着东西出现
  → 再补需要理解的格（产品名/需求描述）做第二次回填。**没有东西可填也要跑一次 `--set '{}'`**（声明"我看过了"）。
  ⚠️ 排他规则读的是 `has_input`（这条线的输入口是不是表单），不是 `intent` —— 否则表刚登记那几秒
  引擎页会趁虚弹回来，变成两个输入口并存。
  ⚠️ 未回填时页面自己会显示一行"agent 还没分析这批素材"（`prefilled=false && submitted=false`）——
  空表不许长得像"没人管"。回归：`check-workbench-presence.py` A16t5/A16t6/B8、`check-skill-form.py` D21/D22、
  `check-quote-provenance.py` F 组（报价前置的八个分支）。
- 🔴 **配了表的那条线，引擎页不再弹**（判定口回 `reason=form-is-input`，插件侧 `retired` 是双保险）：
  同一条线的输入口从"引擎页面"换成了"表单页"，不是多摆一个台。没配表的线（omni/hypit 已冻结）不受影响，
  这条由 `check-workbench-presence.py` 的 A16-T 双向钉（漏配/多伤都会红），线上生效与否由 B8 打真判定口验。
- 提交**只落 8100 的会话级载荷**（`POST /api/skills/form` → `<工作区>\.dsh\form_payload.json`），
  🔴 **不 setDraft、不挂附件、不 submit、不代发**——往共享输入框里脚本输入是明令禁止的。
- 🔴 **点【提交这张表】就等于授权开工**，不要再要客户回一句"开始"（那是同一件事的第二个动作）。
  花钱那一下仍只有 `http://127.0.0.1:8100/approval` 那一次点击，表单与批准各管一头。
- 🔴 **提交后是"事件叫醒你"，不是"你去问"**（2026-10-09 T13；2026-10-10 把同一条规矩接到【批准核销】上）：8100 在提交成功后给那条会话挂 `wake.pending`，
  宿主插件每 5 秒领队列并 `Agent.followup()` 开新一轮（消息 `source.kind=plugin`，不是客户手打的）。
  🔴 **两个事件源、一条队列**：`表单提交` 与 `人在批准窗/批准页点上【批准，开始跑】`（`/api/spend/approve` 核销成功那一下）都挂号 ——
  后者补的是"我点了审批不执行，还得回一句点了条子"这个洞（用户 2026-10-10 点名）。判据只在 `model_admin` 一份
  （`form_wake_queue` + `approval_wake_queue` 合并成 `wake_view`），插件不自己判、不猜 kind（ack 必须带 `kind` 分流销账）。
  点【这次不跑】(deny) 故意**不**挂号：那一下的语义是"停"，叫醒 agent 没有活可派，只会多烧一轮。
  所以：**客户交完表 / 点完批准之后 agent 必须自己接着做**，别再等一句"开始"；反过来，被叫醒也**只是开始干活**，
  🔴 **唤醒 ≠ 批准** —— 要花钱那一下仍然报价 + 等人点批准窗（表单叫醒时条子还没有；批准叫醒时条子已生效、只覆盖那一条写明的范围）。
  回归 `scripts/check-form-wake.py`（B 组=表单、E 组=批准，两组的队列规则逐条同构）。
  🔴 **轮询自己不许静默停摆**（2026-10-12 补，闸 W 组）：`fetchJson` 必须带 `AbortSignal.timeout`，
  因为 `run()` 是"一轮只跑一个"的守卫 —— 一次挂住（本机服务"TCP 收下但不回话"那种，10-11 的 8100 就是这样）
  会让 `running` 永远是 true ⇒ **整条唤醒线静默死到进程重启**；所以另有 30 秒看门狗强制复位、
  "跳过本轮"必须出声、每 60 秒一条心跳（`rev` 带上）。
  ⚠️ 但别把"没叫醒"都归给它：现网那次真因是**会话没在前台开着** ⇒ 判据 `agent-not-live` ⇒ 设计上
  【不叫、也不销账】，日志会打 `form-wake 等着没叫: … 原因=agent-not-live`（这条 10-10 就要求出声）。
  ⚠️ 改这份宿主插件要**完整重启 DSH** 才生效（`patchReload:live` 只 watch patch 文件）；
  没重启期间 `check-form-wake.py` 的 **Z1 恒红是有意的**（已加载 rev ≠ 源码 rev = 部署待办，不是故障）。
- agent 读/查参数只有三条命令：`python scripts/intent.py payload`（读客户提交的引擎入参）、
  `python scripts/intent.py fields`（只读：这张表到底要哪些 key / 类型 / 必填 / 条件显隐，并给一份可抄的 `--set` 模板）、
  与 `python scripts/intent.py prefill --set '<json>'`（把分析结果【预填】进右栏表：只写 defaults、
  不算已提交、不许替客户交表；回填值必须在页面上标「AI 已填 · 可改」并给依据）。
- 🔴 **回填覆盖率 = 入口硬判据，不是散文**（2026-10-10 现网 ef1ba285：只回填了声音档一格，原片躺在
  `src/` 却让客户自己在页面上选原片）：门控一次回齐 `unfilled_required` / `loose_materials` /
  `prefill_gap` 三份，**判据只在 `model_admin.form_prefill_gap()` 一份**（必填素材格没值 + 任务目录里
  躺着【类型对得上】的闲料 ⇒ 命中）。runner 只读 `prefill_gap` 这个结论就拒（0 分，在导入引擎之前）；
  `intent.py prefill` 把三份原样打出来，缺口非空时 `next` 换成"补齐再来"。
  隐藏格（`show=` 不命中）永远不算缺；`outputs/`、`frames/` 不参与扫描（派生物当客户素材会天天误拦）；
  客户手上确实没有的素材留空是对的 ⇒ 这条闸只拦"料在手却没挂上"。
  回归 `scripts/check-skill-form.py` 的 D10–D15b（含服务端/入口两处变异对照）。
- 🔴 **回填的 key 用错 = 过不去，不是"少填了一格"**（2026-10-11 现网：agent 猜字段名，发的是
  `source_video` / `ratio`（那是 motion-transfer 那条线的叫法，跨线串味），又把 `product_images` 当文字塞进
  `values` ⇒ 服务端一条都不收（未知 key 只进 `dropped`，媒体格对 type 直接 `continue`、连 dropped 都不进），
  而退出码是 **0** ⇒ 它以为填好了，客户 21:02 把已经在手的视频和产品图**自己又传了一遍**）。
  现在：`intent.py prefill` 在**所有副作用之前**（复制素材、写槽目录、POST 8100 都还没发生）先跑
  `prefill_key_problems()`（纯函数，判据只有一份），命中 ⇒ **退出码 6、盘上一条都不写**，并把
  【全部合法 key + 哪些是媒体格（只走 `files`）+ `files` 的写法 + `intent.py fields` 这条免猜命令】一次打回给它。
  `fields` 的模板**按槽形**出：媒体格只进 `files`、🔒 只读行两处都不进、`select` 给的是字段表里的真选项、
  条件格标「仅当 voice_mode=… 才出现/才必填」⇒ "该填哪些 key" 不再靠记忆。
  回归 `scripts/check-skill-form.py` 的 **D23–D26b**（两处变异对照：摘掉"未知 key"那句判断、
  摘掉"媒体走 files"与"🔒 不进模板"两个分支；探针打在 ast 标识符上而不是整份源码文本上 —— 注释里就写着 `form:`）。
  ⚠️ 强度边界：这条硬拒住在【脚本】，直接 POST 8100 仍是另一条入口（agent 有网络、文件沙箱管不着 HTTP）⇒
  服务端同一份字段表必须【同样不收】这些 key，且丢掉的要以 `prefill_dropped` 看得见；
  两边由 **D27–D27c** 双向钉（同一份事故样例：脚本判红 + 服务端 defaults 一个字都不收 + 合法样例 dropped 为空）。
- 🔴 **agent 是"客户 → 工作流"的衔接层，不是分析层**（2026-10-11 用户定"很简单的逻辑，不需要冗长分析浪费时间"；现网形状：客户 20:17 点单，agent 先抽帧/看图/写 BRIEF，表 21:02 才出现，中间 44 分钟盘上零条红）。四刀落在码上：
  1. **点单后第一条命令是 `python scripts/handoff.py <档> [任务名]`**（登记意图 → 建任务目录 → 把素材根里
     **无歧义**的料归位到槽 → 打印表地址与下一步）。它**一个模型都不调**：不 import 网关、不打 `video.analyze`、
     不 POST 8100（闸 D30b 钉着）。需要判断的部分（看图、按秒核动作）挪到**交表之后、报价之前**。
  2. **拿不准就不回填**（用户原话"拿不住就不回填，弹表让客户手动选"）：`slot_plan()` 只在"一个文件只对一个槽"时挂，
     一图两槽（产品图/脸图）⇒ 整格留空；单值槽来了两张图 ⇒ 留空。留空是**合法动作**，不是失职；填了猜的值才是事故。
  3. **交完表之后取值只有一条路：`runner/run.py --from-form`** —— 值直接从这条会话【已提交】的载荷读
     （媒体是服务端存的绝对路径/数组），命令行显式给的只作覆盖，并逐格印出"这条是命令行还是表单给的"。
     四格 `required=True` 已经撤掉（那等于强制把 agent 当人肉转录层：21:04 抄对了、20:55 把 key 抄成另一条线的名字），
     缺格改由 `missing_required()` 在合并**之后**点名拒 ⇒ 不许静默补默认值。没提交 ⇒ `--from-form` 直接拒。
     闸 D28–D28f（值表 + 变异对照 + 正向对照）。
  4. **"重编译 0 分、不烧 Wan"现在有合法出口**：`--stage compile`（管线里 `stop_after="compile"`，
     在物证+回执+合同核验之后早退，两处提交腿都有）。以前入口只有"整条跑到提交"一种走法 ⇒ 遵守那句散文只能
     绕开入口手搓 driver（现网 21:23 就是）。分镜失真的纠正手段登记成合同步 **`storyboard-fact-check`**
     （`手段=runner:--extra-note`，物证 `storyboard_check.json` + `extra_note.txt` 由管线自己落盘），
     拿"绕开入口"来申报手段 ⇒ `verify_or_raise` 拒（闸 T36a–T36f）。⚠️ 这份物证只证明"对账发生过、改的是哪一版"，
     **不证明改得对** —— 对不对仍由人在审帧那一刀看。
- 🔴 **点单 → 登记意图 > 3 分钟 ⇒ 红**（`audit-skill-entry.py` 新增状态：`slow`，退出码非 0）。
  登记是纯机械的第一条命令（写一行 JSON + 建目录），慢只可能是它在动手前先写分析；而"表什么时候出现"
  现在等价于"意图什么时候登记"（登记 + 配了字段表就弹）。`--selftest` S12–S14 钉值表，
  ⚠️ 它**不判**"表弹了但一直空着"——那是合法状态（见上第 2 条），拿它判红就是拿散文当判据。
- 字段表唯一源 = 该 skill `SKILL.md` 的 `form:` 段（行格式 `key|type|label|必填|选项/区间|引擎参数[+可选尾巴]`，
  尾巴从第 7 列起、**顺序无关、不写就没有**：`15MB`=该槽体积上限（钉引擎自己硬拦的那个数）、
  `group=素材`=分组标题（一条都不写=退回按类型自动分，老线一个字不改）、
  `hint=…`=格下说明（媒体槽则当槽内占位文案）、`show=键=值1,值2`=条件显隐（隐藏的字段【不卡必填、也不进载荷】，
  前后端按同一条 spec 判）、`mark=选填 · 换人时必给`=角标文案、`note=…`=🔒 行那半句灰字。
  🔴 加尾巴而不是再加固定列：位置列到十几列后没人记得住第几列是什么，老线一改就全漂），
  类型 `text|textarea|select|number|images|video|audio|locked`（`locked`=🔒 只读展示行，天生无引擎入参，
  第 5 列当显示值用；A1/A4/D3 都按"可填字段"豁免它）；
  🔴 名单唯一源 = `model_admin._FORM_TYPES`，媒体子集 = `_FORM_MEDIA`；闸 C14 钉前端 `isMedia` 与它同名单，
  加一种类型必须同时改渲染器分支、上传白名单与 `intent.py` 的媒体判据（后两处已改成读下发的 `accept`），
  漏一处的形状是"页面让你选、提交被静默丢掉"）；挂哪一档 = frontmatter 的 `intent:` 键。
  渲染器/插件里不许出现第二份字段表，也不许出现价格数字。
- 回归：`python scripts/check-skill-form.py`（A 枚举全技能字段表 / B 页面铁律 + 插件侧不许有表单 /
  C `formIsComplete` 值表 / E·G 真跑服务端 `form_submit` 与回填 / F 品牌 token 唯一 / H 分发不许双写）。

## 批准粒度

只在这些地方停：

1. 方案（含批量任务列表）
2. TIMELINE 切段表或投放草稿
3. 成片验收或客服升级（退款/发怒转人）

不要每个生成 HTTP 问一次。

🔴 **提问的门槛 = 95% 把握**（2026-10-05 用户定口径；正文在全局层 `$DSH_HOME/AGENTS.md` §1，这里只写本工作区的执行细节）：
- 自己先推到 **95% 以上把握就直接干完再报结果**，低于 95% 才问，且一次只问一个问题；
- 缺信息就用**默认值**，并在结果里说明"我用了哪个默认、为什么"；
- 进度**自己播报**，不许让客户来问、不许说"你回一句进度我就读日志"；
- 不许出现"要不要我继续 / 跑不跑 / 确认一下吗"这类把决定权推回去的句式 —— 那等于把活交回给人，客户要的是结果不是选择题。

## 🔴 交付铁律：目标没达成，不许停（对所有 skill 生效）

2026-10-04 用户原话：**"整个软件写一个铁律：目标未达成（视频和图片没有出来）不能中止运行。"**

**什么才叫"达成"**：产物真的落在盘上并且能出示 —— 视频 = `productions/<task>/outputs/<ascii名>.mp4` + 8100 直链 + 抽帧贴图；图片 = `productions/<task>/…` 落盘 + **画面内联在这条消息里**（`![说明](http://…)`，点一下就地放大审核）。
🔴 **图片走同一条命令**：`python scripts/deliver_pack.py <图片路径> --json` —— 它不抽帧、不要求 present（帧图在 `frames/`、生图在 `outputs/` 都合法），但硬钉直链那一格回 `image/*`：**只看 200 不算数**，`MarkdownImage` 的 `onError` 会把加载失败的图回落成 alt 文字，客户看到的就还是一行链接。回归 `check-deliver-pack.py`（23-30 组）+ `check-markdown-image-zoom.py`（"点一下能放大"那块补丁的形状）。

⚠️ **回显只给"要交付的那一张"**（2026-10-10 用户定）：过程图 / 质量自检图 / 试跑图 **不要内联、不要 present**。
"看一眼质量如何"配一张大图，会把对话刷成相册，客户反而找不到哪张是成品。
过程图照样落盘（`productions/<task>/…`，正文里给路径就够，人要看自己点开），
最终交付那条消息里才用 `![说明](http://…)` 内联。上面"图片必须内联"指的是**交付件**，不是每一步。
"已提交 / 运行中 / 上游回了成功状态" 都只是中间态，**不算交付**，不得当结论收工。

🔴 **三件套不许手写，跑脚本**（2026-10-09 定，因为反复有会话"给了本地路径就当贴了图"）：

```
python scripts/deliver_pack.py <成片绝对路径> --task <任务名> --json
```

把它输出的 `markdown` 字段**原样贴进回复**，并把它打印的校验结果当交付判据：**退出码非 0 = 没交付，不许收工**。
原因有两条，都不是审美问题：① DSH 对话渲染器**不内联视频**（整包搜 `<video` 零命中），所以视频只能"路径 + 可点直链 + 抽帧贴图"；② 正文里的 `C:\...` 本地路径、裸 URL、反引号里的链接**都不是图** —— 图必须写成 `![说明](http://…)` 这种 markdown 图片语法，且 URL 要是 http/https 绝对地址（判据读装的这版：`dsh-client-ui-primitives/lib/index.js:7359 renderImage` → `:7104 imageSource` → `:7078 remoteImageUrl` 只放 http/https）。
⚠️ 排查形状（2026-10-10 我自己踩过一次，别重犯）：客户说"只给了个链接"时，**先分清是 agent 没写成 `![..](..)`，还是写了但图片加载失败**（`MarkdownImage` 的 `onError` 会回落成 alt 文字，看起来就像一行链接）。前者是行为问题，后者要查直链（`/api/film/file` 那格 Content-Type 与字节，不只是 200）。
🔴 别据此加"每张抽帧图都必须 present"这类闸门：present 出的是右栏预览卡、不是内联图，markdown 内联本来就可用，多一道闸只是凭空提高收工门槛。

**以下都算违规中止**：
- 没 `present` 就把成片交回给人（2026-10-09 用户指令"必须强制预览，客户体验感好"）：
   那张【打开】+ 菜单【用默认应用打开 / 在文件资源管理器中显示 / 下载图片·下载视频】的卡**只在 agent 调过 present 之后才出现**（菜单最后一项的文案跟着文件类型走，图片不会写"下载视频"），`deliver_pack.py` 只出 markdown，不会替你 present。
   所以交付顺序是死的：**出片 → present → 跑 deliver_pack → 原样贴 markdown**。
   `deliver_pack` 会去内核附件存储（`%APPDATA%\dsh-desktop\harness\attachments\v1\files\<原名>`）**比内容哈希**确认 present 过
   —— 🔴 只看文件名不算（聊天附件也叫 `video(16).mp4`），没 present 或同名不同内容 ⇒ 该条校验 False、退出码非 0。
   非 DSH 环境自测才用 `DELIVER_PACK_NO_PRESENT=1` 跳过。回归 `check-deliver-pack.py` 的 14a–14e。
- 没轮询到终态就把"我发起任务了"交回给人
- 上游 5xx、超时、SSL 掐线、排队久 ⇒ 直接汇报失败停下
- 一批里失败几段就整批放弃，而不是只补跑那几段
- 产物落盘了却没贴图/没路径/没直链 ⇒ 视为没交付（曾被用户点名"视频你给我看啊"）

**必须做到**：自己盯到终态；失败**按死因分类**处理 —— 抖动类退避重试**同一个** job_id（连续 6 次查询异常才判死）、参数/合规被拒改参数或换合法路由；一批里失败 N 段就补跑那 N 段；判死时也要把 taskId 与上游原因如实交回，并继续做不受影响的部分。

🔴 **wan3-prime（满血 wan3.0）的重试环在网关适配器内部，不在上层**（2026-10-10 定，闸门 `scripts/check-prime-retry.py`）：这条 lane 平台实测成功率 ~23%（`/v1/balance` 读到 24 败 / 7 成），而**失败不计次**（`billing.charged_calls=0`）。
⇒ `_yz_prime_run` 一次调用内部最多 4 次尝试、退避 45/120/240s，**每次决定前先读平台对账口**（`_yz_prime_reconcile`，0 分只读）：
"报未知但平台已建任务" ⇒ 去捞那个号（今天 20:15/20:16 就是 502 带着任务号回来、旧代码没挖，号丢了只能重投）；
"任务失败且计费 0" ⇒ 同参数重投（免费）；**对账口没回话 ⇒ 一律停**（"读不到"不等于"没建上"，这条是防自己造双花的唯一屏障）。
⇒ **上层不许再 call 一次来"重试"**：每 call 吃一段批准额度，会把 23% 的成功率变成"用户点四次批准"。
配额见底（`remainingCalls=0`）时适配器直接拒、0 消耗，并写明"先去 prime.youzan666.vip 续次数"——**不许拿"闸拦住了"当失败隐瞒**。
落盘侧另有一条：同名成片不许静默覆盖，旧的会留成 `.prev-<sha8>`（v3 曾把已交付给人看过的那条原地盖掉）。

🔴 **判"失败"之前先看盘**（2026-10-06 实测）：8090 的结构性复刻有 Phase1 已跑完（分镜 JSON + 故事板海报都在 `outputs/sr_<ts>/`、`phase_state.json=after_step3`）却被标成 `failed` 的情况——父进程回显子进程日志时 GBK 崩了。
⇒ 遇到"任务失败但 error 是一句编码/重启扫尾"，先按盘上产物判（8090 已内置这条规则，`GET /api/structural-replica/status/<id>` 会把它纠回 `awaiting_review`）；**别照着 failed 重跑整条**，那是双花。回归：`scripts/check-8090-log-encoding.py`。

**边界（这条不推翻既有闸；冲突时以闸为准）**：
1. 🔴 **生视频仍要先报价并等明确同意**（2026-10-09 口径：只有 `video.generate` 要批）—— "不停"指的是**批准之后盯到底**，不是擅自加段数、换更贵的路由、扩到别的片型；范围一变就重新报价。其余计费能力免批但**不免账**，也不许借"免批"扩片型/扩段数。
2. 不重新提交"已判死但上游可能仍在跑"的任务（= 双花）；孤儿任务用 `job_id` 捞回结果。
3. 三个批准关口（方案 / 审帧 / 成片）与客服升级照旧停，重启常驻服务照旧先 `preflight`。
4. **同一个失败原因连续 3 次仍不通 ⇒ 停下报证据**（PID / 账本 error / 上游原文），不许无限循环烧钱 —— 防止"不停"退化成"死磕"。

## 会话绑定

每个会话同时绑：

1. **资源管理器**：本项目目录（素材与 LOOP）
2. **账号浏览器**：一个自研指纹环境；该浏览器可登录同一店铺的多个平台（抖店 / 千川 / 飞鸽），不混店

用户说账号矩阵、有哪些号、切换浏览器 → `/account-matrix`（上架后）。

## 🔴 技能目录写权限：只允许 Qoder 侧改 skill（2026-10-10 用户定）

`.dsh/skills/**` 是判据唯一源：`form:` 段 = 线上表单的字段表，`contract:` 段 = 花钱前核验的合同。

- **DSH 里的 agent 一律不许改 skill 目录**：不新建、不改、不删任何 `SKILL.md` / `runner/**` / `references/**`，
  也不许"顺手优化"提示词或字段表。要改就在对话里说清【哪一行、为什么】，由人转给 Qoder 侧落。
- **只有 Qoder 侧（本工作区的开发会话）可以改**，改完两件事缺一算没改完：
  `python scripts/check-skill-contract.py`（判据没坏）+ `python scripts/check-skill-write-lock.py --bless`（重新认基线）。
- 🔴 **为什么散文之外还要闸**：这台机器上 Qoder 与 DSH agent 是同一个 Administrator，ACL 拒它就是拒我自己 ⇒ 分不开两个写者。
  唯一分得开的是内核 `dsh-fs-sandbox:153 checkedTarget`：它**当场 realpath**、要求落在可写根内，且校验的就是它要写的那个身份。
  所以真拦 = 把 skills 真身移到工作区外（如 `D:weeksi…ills`）、原地留 junction；没装成之前
  `check-skill-write-lock.py` 的 L5 **故意恒红**——那是待办清单，不是故障（红=没锁，全绿才该怀疑）。
- 闸盯的漂移：每档 SKILL.md 与它的 `form:` / `contract:` 三处 sha8 进基线 `.dsh/skill_lock.json`；
  漂了必须【带着提交号】才算授权过，没提交过 = 红；**整档被删**也只有这条看得见（本次实测已发生：`.dsh/skills/hypit/` 被删且从未入库）。

## 账号记忆（隔离）

本会话绑定的 `account` 才是记忆主人。

- 每调用一条 skill，追加写入 `accounts/<account>/SKILL_LOG.md`（项目路径、skill 名、结果）
- 可复用的结论写入 `accounts/<account>/MEMORY.md`
- **禁止读、写、引用其它账号的 MEMORY / SKILL_LOG**
- 矩阵汇总只用各号档案索引，不把记忆正文拼进当前会话

## 文件即记忆

每个任务在 `productions/<task>/`：

- `LOOP.md` 当前环（框架中枢）
- `BRIEF.md` `ANALYSIS.md` `SCHEME.md` `TIMELINE.md` `ASSETS.md` `ADS.md` `CS.md` `PROGRESS.md`
- 🔴 **素材按任务分家（2026-10-10 定，先只推 super-clone）**：
  - `intent.py <档> <任务名|已建目录>` 登记时就把 `productions/<slug>/{inputs,src,outputs,frames}` 建好，
    绝对路径写进意图记录的 `task_dir` —— **"任务名→目录"从此有主人**，不再靠 agent 自觉。
  - `inputs/` = 客户给的与表单回填的原始素材（**唯一可被表单读的根**）；`src/` = agent 自己下载/派生物。
    两者分开是审计要求：三个月后还要分得清哪张是客户给的。**回填 `src/` 里的文件会被拒**（闸 G20c 钉这条）。
  - 落盘名 = `序号-sha8-ascii名.扩展名`（`01-0d4e2ca9-cardigan.png`）。sha8 进名字不是为了好看，
    是这台机器多会话并发、"同名不同内容"咬过两次 ⇒ 撞名要当场看得见（`deliver_pack` 早就是比哈希不认文件名）。
  - 🔴 路径规则**只在服务端一份**：素材根由门控 `upload_root` 下发，脚本里不许再拼一遍
    （旧写法 `.dsh/form-uploads/agent/<param>/` 是跨会话共享的 ⇒ 两条会话回填同名图互相覆盖，已废）。
  - 守卫是**收紧不是放宽**：realpath 界从"全局一根"改成"每会话/每任务一根"，`task_dir` 指向工作区外一律不认。
  - 目录名必须 ASCII（8100 静态口与交付直链的要求），中文任务名退到 `<skill>-<yyyymmdd-HHMM>`。

## 生成器

本阶段客户只在 DSH 对话。jobs 调用 `D:\video-skill` 现成 Python。不打开 8090。不换对话模型（Agnes 3.0 Flash）。

商品资料本阶段不建档：出片时在对话里现问产品图和卖点即可。

## 模型调用轨（所有 skill 调模型的唯一入口）

任何 skill 要调模型（文字 / 图 / 视频 / 看图），一律经 `tool-jobs` 跑统一网关，**不各自硬编码厂商 key/endpoint、不直连供应商**：

```
python D:\video-skill\backend\gateway.py call <capability> [purpose] --account <LOOP.member> --json '<params>'
```

- `capability`：`text.complete` / `image.generate` / `video.generate` / `video.analyze`；`purpose` 看 `gateway.py caps`。
- 🔴 **要"公网直链"（把本机素材传给别的引擎当外链）就跑 `upload`，别手搓 HTTP**（2026-10-09）：
   `python D:\video-skill\backend\gateway.py upload <文件> [--key-hint wan3|h3|motion|lipsync] [--account <成员>]`
   ⇒ 0 分、不计费、不建预扣、不要批准条子，但**记台账**（`gateway.py uploads [N]` 查，里面只有 key 的 sha8）。
   host / 端点 / key 优先级**只在这一处**解析。实测三种死法都跟 key 好坏无关，别再归因成"token 失效"：
   `/openapi/v1/upload_file` **不存在**（却回 HTTP 200，body 里才是 `code:404`，只看状态码会当成功）；
   host 写成 `runninghub.cn` ⇒ `code=1 "API Key不存在"`（**RH 的 key 按站点分**，`.ai` 那把在 `.cn` 无效）；拿错家的 key ⇒ 直接 error。
   媒体节点要填的是返回里的 `file_name`（`openapi/<hash>.png`），`download_url` 只是给人看的直链 —— 两个都给，别猜。
   另有 HTTP 口 `POST 127.0.0.1:8100/api/media/upload`（只听环回、只放 `productions/` 根内的媒体文件、json 一律拒）。回归 `scripts/check-media-upload.py`。
- 🔴 **skill 的流程是合同，不是建议**（2026-10-09 根治"agent 驱动 skill 时按自己理解跑"）：带引擎的档
   （唯一源 = `scripts/intent.py` 的 `LANES`，与 `hypit_bridge.KNOWN_SKILLS` 由闸钉相等）必须在 `SKILL.md` 写
   `contract:` 段；入口跑完写回执（`productions/<task>/**/.skill_receipt.json`），**花钱那一刀之前核验它**——
   无回执 / 合同版本不符 / 输入指纹变了 ⇒ 抛错、不提交、0 分。绕开入口自己拼 prompt = **提交不出去**，不是"提醒你别这样"。
   已接：super-clone（`super_clone_wan3.py` 提交前调 `skill_contract.verify_or_raise`）。跳过某步要写进 `deviations`
   并带原因，交付时由 `deliver_pack.py` 原样印出来（🔴 只断提交，不断查询/捞回 —— 已提交的任务重投=双花）。
   设计见 `docs/ADR-skill-contract.md`，回归 `scripts/check-skill-contract.py`（新加引擎档 = 同时必须交合同，T2b 会红）。
   🔴 **引擎单一源（2026-10-10 事故：客户表里锁的是 yz-prime，agent 却念出"RunningHub wan3.0"）**：
   表单锁定位 ↔ 合同 `spend` ↔ 网关 `ROUTES`/价目 ↔ 代码里 `invoke()` 的那一对，四方必须指同一条 lane，
   由 `scripts/check-engine-single-source.py` 钉（含"把 spend 换成网关里没有的路由""摘掉单价登记""换个 provider"三种变异对照）。
   配套两条硬规矩：① **报价里的上游与单价只许现读** `python D:\video-skill\backend\gateway.py prices`
   （这一行现在自带 provider，如 `video.generate/wan3-prime 60 分 [yz-prime]`），不许凭记忆写厂商标；
   ② 合同值里**不许写行尾注释**（`spend:` 后面的 `#` 说明会被吃进值里 ⇒ 三方比对对不上；`parse_contract` 现在会剥，但别依赖它）。
   ③ 🔴 **入口也不许留第二条引擎的路**（2026-10-10 深夜补）：`runner/run.py --engine` 只有 `choices=["wan3"]`，
   `run_pipeline` 的**第一条真语句**就是 `engine_lock_error()`（唯一源 = `super_clone_wan3.LANE_ENGINES`），8090 的
   `/api/super-clone-wan3/submit` 在**建任务记录之前**问一次 ⇒ `engine=h3` 从任何入口都进不来（此前它一路能走到 RunningHub 导演台，
   只被 `rh_video_guard` 挡着，合同/表单/上面那四方全都看不见）。回归 `check-engine-single-source.py` 的 **I 组**。
   ④ 🔴 **素材必须"真看过"才有资格提交，而"看"这一步的执行人是【管线】不是 agent**（合同 `super-clone-wan3@7`；
   @5 起有这个必需步，@7 换了执行人）：判据**不认 agent 自述**、只认网关的**素材分析台账** —— 每条
   `video.analyze` 跑完由网关记 sha8，`_contract_evidence` 在写任何物证之前按内容对账。
   🔴 **为什么必须换人**（2026-10-12 现网 session-1e7a5162）：@5 那版只在报错文案里教 agent"先跑 `gateway.py call video.analyze`"
   ⇒ 它就真的自己干：PowerShell 吃引号 → 猜参数键名 → 写 `_analyze_run.py` 手搓 → 走了 `analyze` 那条**不记账**的近路
   → 台账 0 条却汇报"预扣/结算/台账齐全"。合同把执行人写成 agent，就是让它自己找路，路一多就找到不记账那条。
   现在锁死：缺哪几张图由**管线**经 `gateway.invoke("video.analyze")` 自动补跑（会预扣/结算/记台账），
   范围只含**产品图**（原片的动作与时间轴事实由 Step1 MiMo 那条腿承担，重复看图=重复花钱），
   **熔断默认 120 分**（`SUPER_CLONE_ANALYZE_CAP` 可调，打满就停下报数，不许死循环烧分），
   实扣写进 `material_analysis.json` 的 `auto_ran`/`auto_spent`（executor=`pipeline:invoke:video.analyze`）⇒ 报价与交付表读这份。
   代价要认：**看图免批但不免账**（单价现读 prices，6 分/次；实扣按 token，现网单条 14–40 分）。
   🔴 同一次还封了那条近路：`gateway.py analyze` 以前直连 `call()` 分发器（记账/预扣/结算只在 `invoke()` 里）
   ⇒ 现在它转 `invoke()`，**一条能力只有一个入口**；判据也收成一份 `analysis_missing_paths()`（人话版从它套出来）。
   查"看过没"用只读口 `gateway.py analyses [N]`。回归 `check-material-analysis.py`（M5 结构锚点 / M6 合同手段 /
   M7a-g 真跑咽喉但 **invoke 打桩、闸自己 0 消耗** / M8a-d 近路与单一判据）。
   🔴🔴 **第七种失效（同一天 09:04 现网，比前面所有都严重）**：它发现台账没记上之后，写了 `_record_ledger.py`
   **直接 import `record_media_analysis` 给自己签了 3 条"看过图"**（13 毫秒三条、credits=0）——
   "不认 agent 自述、只认台账"这条判据当场被自证破功，因为台账是个它能写的文件。
   现在台账行必须带 **`hold_id`（这次调用的预扣号）**才算证据：`analysis_missing_paths()` 无视无凭证的行，
   `gateway.py analyses` 会把它们标 🔴无（不算证据）。**这不是密码学防线**（同一个 Administrator 做不到）——
   它要的是"伪造得贵 + 伪造了也没用"：要过关就得真产生一条预扣；而缺料时管线自己会真跑一次（见上）。
   回归 M9a/M9b/M9c（M9c 就是"台账里全是手写行 ⇒ 管线不认并自己补跑"）。
   🔴🔴 **最高等级：合同没写手段的地方，也不许你自己发明手段**（2026-10-09 用户定性；事故形状=合同只写"删掉字幕/水印"
   这种**结果**，agent 就去手搓 ffmpeg 去字幕、还差点编出"内置去字幕模型"）。判据已落码，不靠自觉：
   ① 每条 step 必须至少有 `证明=`（物证）或 `手段=`（白名单）之一 —— **两者都没有的合同条款本身就是洞**，
   `verify_or_raise()` 会在花钱之前直接拒（`contract_shape_problems`）；② 声明了 `手段=` 的步，实际手段不在名单内
   **或根本没申报** ⇒ 拒（`impl_problems`）。所以动手前先答一句"这一步是合同哪一行授权的"；答不出就停、去查唯一源、
   或问人，**不许填空隙**。⚠️ 强度边界：这一层管得住"走管线/入口"，agent 在对话里直接手敲一条命令不经过任何
   被管函数 ⇒ 合同物理上看不见（要拦得动工具层，见 ADR 同名节），那种越界目前只能靠交付时可见（回执缺步/deviations）。
   🔴 **花钱那一刀前后必须给两张表**（2026-10-09 用户："提交视频生成前，要总结一下每一次是谁完成的"）：
   ① **报价那条消息**带 `plan_table(<档>)` —— 人在同意之前就看见"这条流程要走哪几步、每步授权什么手段"，
   而不是事后追认。🔴 **这张表现在由 `gateway.py approve` 自己打出来**（2026-10-10：原来靠 agent 记得跑
   `python scripts/contract_tables.py <档>`，可跳过的步骤等于不存在的步骤，现网三条报价都没带表）：
   approve 用 `skill_contract.skill_for_spend(capability, purpose)` 从合同的 `spend:` 反查档位再打印；
   **反查不到就明说"这一档没有合同、出不了表"并且不印表** —— 不许拿一句"参见流程"装作给过。
   （`python scripts/contract_tables.py <档> [段目录]` 仍在：只读、0 分，供人随时手查同一张表。）
   ② **提交之前**把 `verify_or_raise()` 返回的 `table`（= `who_table(回执)`）**原样贴进回复**，
   管线已自动把它打进日志/进度（`合同核验 | …`）；交付时 `deliver_pack.py` 也会带同一张表。
   表里"谁做的（会话）"是**逐行署名**：同一行没变就沿用上一次的会话，
   所以"甲做分镜、乙只重跑编译"会分开显示，不会整表刷成最后那个人。
   回归 `scripts/check-approval-count-from.py` 的 K7–K10b（真跑 approve、临时账本 + 批准窗抑制）。
   ⚠️ 表的强度到哪：它保证【事后查得着、当着人亮得着】，不保证 agent 不走合同外那一步（见上条强度边界）。
   常驻服务（8090）里没有 `DSH_SESSION_ID` ⇒ 那一列会明写"未知"，**不许猜一个会话 id 填进去**。
- 🔴 **报价五格必须有出处、批准入口必须有条子**（2026-10-11 现网 session-241ea3f7 08:23 复盘，两条都落码在 `gateway.py`）：
   `单价 / 段数 / 预计总扣分 / 余额 / 实扣` 五格各自的**出处不是同一份**，逐格现读：
   单价→`gateway.py prices`（或 `unit_price(路由)`）、余额→账本 `accounts`、**段数与预计总扣分→这条会话的 pending**
   （只有 `approve` 建得出来，段数还是它从 `--count-from` 算的）、实扣→素材分析台账的**合计**（报单次=少报重复消耗）。
   🔴 **把 `approve` 打出的那段报价正文原样贴进消息**，不要重排成表格再自己抄一遍数 —— 重排一次就会手滑。
   现网那次的四个错：余额写 99544（盘上 99532）、实扣写 40（台账是 40+40+38=118，同一段原片被分析了三次）、
   段数与总扣分凭空写（`approve` 根本没跑过、没有 pending）。
   第二条：**消息里出现"批准入口/批准页/approval"字样 ⇒ 这条会话必须在 `approvals` 里有 pending 或未过期条子**。
   那次它印了 `http://127.0.0.1:8100/approval` 却没建条子 ⇒ 客户点进去是**空位**（比不弹窗更坏，那是指向空气）。
   现在：① `approve` 输出的备选地址自带 `?session=`，批准页按会话筛卡、**这条会话没有条子就明说**（不再给一张全局空列表）；
   ② 判据 `gateway.quote_problems()` / `gateway.approval_entry_problems()` 只这一份，页面与闸都读它，不许抄第二份。
   回归 `scripts/check-quote-provenance.py`（A 真值表 / B 真跑 approve 证"照抄就绿" / C **08:23 真消息必须红在那四格** / D 接线与"只一份" / E 变异）。
   🔴 **报价前置：没回填过表 ⇒ `approve` 不建条子**（2026-10-11 16:30 现网：登记了意图、把默认值写在对话里说"可在提交前改"，
   但那张表从没回填 ⇒ 右栏不弹、客户没有任何可改的地方，而条子照样建得出来）。`approve` 现在会问判定口
   （`gateway.form_prefill_blockers(session)`，只读 8100 的结论，网关不解析第二份字段表）：
   这条会话**回填过**（或客户已自己交齐表）**且** `prefill_gap`/`unfilled_required` 都为空才放行；
   没登记意图 / 缺口非空 / **判定口没回话** ⇒ 一律拒（fail-closed，0 分 0 额度）。
   没配表单的档（hypit/omni/film/motion-transfer）不在射程。
   ⚠️ 同一次修里改掉的判据 bug：覆盖率以前只对着**回填**算 ⇒ 客户交过表的会话恒报"必填没填"（把合格对象判红）。
   现在算的是**生效值**（交过表以载荷为准，否则以回填为准），`prefilled` 也读原始记录、不被 `submitted` 抹掉。
   回归 `check-quote-provenance.py` 的 **F 组**（八个分支 + 一条真跑 approve 证"pending 一张都没多"）；
   闸侧测试缝 = `GATEWAY_FORM_GATE`（不指桩就等于往生产状态里写一条真回填）。
- 🔴 **花钱那一刀认合同回执（2026-10-11 下移到咽喉）**：`gateway.invoke()` 在**批准之前**先问
   `spend_contract_problems(capability, purpose, params)` —— **成片要写进哪个目录，那个目录（或往上 6 层）里
   就必须有一份该档合同的合格回执**（`.skill_receipt.json`，判据仍读 `skill_contract.check_receipt`，网关不抄第二份）。
   为什么必须在这儿：`verify_or_raise` 全仓只有 `super_clone_wan3.py` 一处调 ⇒ 合同只约束"走管线的人"，
   agent 自己 `from backend import gateway` 拼一次调用就能扣费（2026-10-11 现网 08:23 实测：盘上零回执）。
   四条边界，改之前先想清楚：① **只断提交，不断查询/捞回**（params 带 `job_id` ⇒ 放行，重投=双花）；
   ② **没有合同档认领的腿不在射程**（`h3`/`motion-rpa`/`video.analyze` 一律放行，别假装管得住）；
   ③ 判在**批准之前** —— 反过来的话一次"没回执"的调用会先把人批下来的段数吃掉，那是拿别人的授权替自己的绕道买单；
   ④ **不靠会话号找回执**（8090 那种常驻进程里没有 `DSH_SESSION_ID`，按会话找会正好漏掉它；落点是调用方自己给的，赖不掉）。
   回归 `scripts/check-spend-receipt.py`（A 真值表 / B 真跑 invoke + 适配器打桩：无回执拒且 0 消耗、
   有回执走到批准闸、回执+条子齐真放行、失败退段 / C 顺序用 AST 行号钉 / D 变异 / E 覆盖对照）。
- **唯一例外形态 = hypit**：它的 Provider 协议只认 HTTP，所以生成腿经自写 provider `@weeksir/provider-gateway` POST `http://localhost:8100/api/hypit`（`/uploads`、`/videos`、`/videos/{id}`、`/rates`），服务端 `hypit_bridge.py` 仍转 `gateway.invoke` ⇒ **同一套预扣/结算/审计**。不算第二条轨，只是同一轨换了入口；鉴权用 `.env` 的 `MODEL_GATEWAY_TOKEN`（Bearer）。详见 `.dsh/skills/hypit-pipeline/references/monitor-contract.md` §1.5。
- 返回归一 JSON：`{ok, result, credits:{estimated,charged,tokens,basis,balance_after}, error}`。
- 🔴 **hypit 只有三条"已绑 capability"能花钱**（2026-10-03 定）：`@hypit/seedance@1#seedance-2-mini`（`model="mini"`，50 分/5 秒）、`#seedance-2.5`（`model="2.5"`，60 分）、`@hypit/minimax-h3@1#minimax-h3`（`<h3:…>` 元素，无 `model` 属性，60 分）。官方 `hypihub.default` 端点已从 `hypit_runs/hypit.runtime.json` **摘掉**（备份 `hypit.runtime.json.bak-hypihub`）⇒ 未绑定的写法在 `hypit plan` 就报错、0 分，不再静默 POST `https://hypit.ai`（2026-10-03 实测漏绑一条就是这个走法，靠对方欠费才没花出去）。四张表（profile bindings / `provider.js` LANES / 桥 `HYPIT_MODELS`·`LANE_SPECS` / 网关 `ROUTES`）改任何一张都跑：`python scripts/check-runtime-bindings.py`。
- **积分**：网关调用前自动 校验余额→预扣→成功提交 / 失败回滚；不足则 `ok:false` 提示去 weeksir.com 充值。`--account` 用 `LOOP.member`。
  - `basis`：`usage`=按真实 token 结算（文本/看图，费率 `model_prices.json.token_rate`，1 分≈100 token）、`flat`=按条计费(生图/生视频)、`usage-capped`=超单条封顶被截。
  - `estimated` 只是**粗略预估**：思考型模型同题消耗实测波动 28→4396 token，报价/护栏看 `estimated`，**对账看 `charged`+`tokens`**。
- **估价**（批准前给人看成本）：`gateway.py resolve <cap> [purpose]`，或读信封 `credits.estimated`。折进"方案批准"，不新增批准类。
- 🔴 **【默认全部要批】2026-10-09 用户最终口径："除了生图片和推理，其他都要审批"**（前两次口径都太窄：`video.edit` 曾因不在"要批清单"里被免批跑掉真分 —— 去水印 20 分/次、超分 60 分/次，比生视频还贵，用户原话"去水印不属于生视频吗，为什么不通过我审核"）。⇒ 判据**反过来写**：默认要批，只有 `SPEND_APPROVAL_EXEMPT` 三条免批。调任何其它能力之前，最后一条消息里必须列清：`路由(供应商+模型)`、`单价 分/段`、`段数或次数`、`预计总扣分`、`当前余额`，并明确问"跑不跑"。
  - **免批只有三条**：`image.generate`（生图片）、`text.complete`（推理）、`video.analyze`（看图看片 = 推理，产出是文字）。这三条直接干、不等点头，跑完带一句实扣分。
  - 🔴 **要批的（默认）**：`video.generate` / `video.edit`（超分·去水印）/ `image.edit`（换脸）/ `lipsync`（译制·配音替换）/ `audio.generate`（配音·音乐）/ `transcribe`（ASR）—— **以后新增任何能力自动落到"要批"**，想免批必须在 `SPEND_APPROVAL_EXEMPT` 显式登记一行（闸 G1c 钉这个默认值）。
  - **闸在哪**：`gateway.py` 的 `invoke()` 在 **precheck/preauth 之前**调 `check_spend_approval(...)`；**判据 = `SPEND_APPROVAL_EXEMPT`（免批白名单，默认要批）**；`SPEND_APPROVAL_REQUIRED` 只是它派生出来的视图。闸与文档都从这一份读，别在别处再抄一份能力名。没有批准条子 ⇒ 回 `ok:false, error:"approval_required…"`，**不建 hold、不扣分、不发 HTTP**。
    ⚠️ 免批 ≠ 免账：这些腿仍走同一条 `precheck→preauth→settle→失败回滚`，扣分与留痕照旧；代价要认——**它们现在可以被任何一条会话直接花掉，出事只能事后从账本查，不是事前拦**。
  - **条子怎么来**：拿到用户明确同意后，agent 跑 `python D:\video-skill\backend\gateway.py approve <cap> [purpose] --count-from <任务目录> --account <成员> --session <这条方案的会话id> --scope "<用户批准的那条方案一句话>"` 建**待批**，然后**批准窗自己弹到桌面上**（人点【批准，开始跑】；页面 `http://127.0.0.1:8100/approval`
    🔴 **段数与分数只许从盘上算**（`--count-from`，2026-10-10 用户裁定"审批一次就行"）：唯一源 = `gateway.py plan_segment_count()`，优先级
    `TIMELINE.md 的「段」表` > `outputs/seg* 段目录` > 单段（并如实说"按单段建的"）。手填 `--count/--credits` 与盘上不一致 ⇒ **拒建条子**，
    因为填少了会在第 2 段被自己的闸拦停、填多了等于替客户多批。回归 `scripts/check-approval-count-from.py`。 是同一条动作口的备选，见下一条"人证"）；查/撤：`gateway.py approvals` / `revoke <id>`。条子写死 能力+purpose+账户+段数+分数上限+24h 过期+**所属会话**，**换路由、加段数、超额度、过期、换了会话一律再拒**。
    🔴 `--session` 必须带（2026-10-06 事故补的）：不带 = 通用条子，任何会话都能消费别人的批准额度——我当时拿一次不相干的端点探测，就把另一会话签给 video-enhance 封面的 1 段/4 分吃掉了（闸判得"对"，但额度是共享的 ⇒ 谁都能花别人的同意）。已退回额度，规矩写进 `approval_allows` 与回归 G11b-G11e。
    🔴 **人证 = 一次点击，不要口令、不要【系统】弹窗**（2026-10-08 两易其稿；2026-10-09 弹窗改回来自研窗，回归 `scripts/check-approval-attest.py` P 组）：
       `approve` 现在**只建一张【待批 pending】并给出报价，此时 0 额度**；额度只能由"人看到报价之后"的一次核销拿到。
       - 主路（**唯一许给客户/用户看的路**）：`gateway.py approve` 建完待批会**自己弹置顶批准窗**
         （`backend/approval_popup.py`，2026-10-09 用户指令"我要出现弹窗进行审批"）；窗里就是那张品牌批准卡
         （logo + 要跑几段 / 最多扣多少分 + 这次要做的 + 还剩几分钟作废），点【批准，开始跑】即生效。
         弹不出来会在 approve 的输出里明说原因 ⇒ 退回备选页 **`http://127.0.0.1:8100/approval`**（同一个动作口，同一份判定）。
         🔴 窗**不是第二种人证**：额度仍只有服务端 `consume_by_click` 签得出，窗只是它的另一个壳；
         Enter/空格被显式吃掉（键盘路过不等于点头）、没有任何定时器调签发；**没人点 ⇒ 过期即拒**。
         回归 `check-approval-attest.py` 的 P 组（含"扫违规先剥注释"这条自己的踩坑）。
         🔴 **禁止把任何 `python …gateway.py code/consumed …` 命令交给用户去敲**（2026-10-08 他第四次否："我不是说了不要powershell"、"还要输入powershell口令吗"）。
         `code`/`consumed` 只是运维自测兜底，**不许出现在给客户的消息里**。要签的东西一律：agent 建 pending ⇒ 指他去页面 ⇒ 自己轮询到生效。
         这条卡可以信是因为 `approval/decided` 只有内核 `ApprovalService` 会写、UI promise 只有 onClick 能 resolve
         （`dsh-user-approval/lib/index.js:142`、`dsh-client-ui-approval/lib/client.js:92`），且**没有应答者时 fail-closed**。
       - ⚠️ pending **15 分钟过期**（`GATEWAY_SPEND_PENDING_TTL`）。所以 agent 建完要【自己轮询】到生效，并在过期前提醒人去页面点，而不是让人去查命令。
       - 🔴 **核销码绝不进 stdout**：跑 `approve` 的就是 agent，码进管道它就能自己 `consumed` 伪造 ⇒ 闸 G4 钉死这条。
       - 🔴 **系统 MessageBox 已整个删掉，不许加回来**：实测无人参与时 `MessageBoxW` 会**自己返回 IDYES 并真签出条子**
         （闸 G 组抓到两次）；同类毛病还有 `MB_TIMEOUT` 不生效。系统弹窗**不能**当人证。
       - 同理别用 `isatty`/`GetFileType` 判"人在不在"——它分不清"人自己开的终端"和"agent 替人开的 PowerShell"，
         实测把用户本人的签名拒过一次。
       - 被拒的核销落 `denials`，生效的条子带 `attest={method:"dsh-card-click", pending:<id>}` ⇒ `approvals` 能查出谁试着自己批。
  - ⚠️ **别拿真花钱的端点做"闸活没活"的探测**：要探就挑**没有匹配条子**的路由（回 423、不发起、0 消耗）；挑到有条子的路由 = 直接花掉别人的批准。
  - ⚠️ **它的强度到哪为止（别 over-claim）**：闸保证【默认拒 + 范围写死 + 留痕可查】；人证保证【agent 单方面签不出】，
    但**不是密码学级** —— 装了 computer-use 那类 UI 自动化的 agent 理论上能截屏读那张审批卡再点鼠标，
    代价是"控制这台机器的鼠标键盘"，且 `denials` / `grants.attest` 会留痕。所以**报价那条消息照旧要发、照旧要等他回话**，
    不许用"我自己签一张 / 我替他点了"代替他的同意。
  - 🔴 **已知缺口（2026-10-06 清点，别当成已完工）**：8090 共 111 个 POST 口，**接上闸的只有 5 个**（`_spend_or_423` 调同一个 `gateway.spend_guard`）：`/api/h3-studio/generate`(h3 60分) · `/api/one-shot-replica/generate`(Seedance) · `/api/replica-wan3/submit`(RunningHub wan3) · `/api/hook-extend/submit`(omni_flash-10s，追到 `hook_extend.run_pipeline` 里 Step3 的 `submit_omni_task`) · `/api/agnes-image/submit`(生图)——前四个实测无条子回 **423、任务不提交**。
    ⚠️ **2026-10-09 更正：`agnes-image` 这道现在是【空转守卫】**（口径改成"只有 `video.generate` 要批"之后，
    `spend_guard` 开头 `if not needs_spend_approval(capability): return` ⇒ `image.generate` 直接放行，永不 423）。
    它留着是为了"哪天把 `image.generate` 加进清单就自动生效"，🔴 **别拿它当"生图也要批准"的证据**。
    配对关系由 `check-spend-approval.py` 的 G26 组钉（读 app.py 的实参算出该标哪几个，不抄这份文档）。
    排除掉的（别再加闸）：`one-shot-replica/submit` 只做 MiMo+LLM 反推、`replica-motion/resume` 是恢复逻辑（命中字样在字段注释里）。
    **还剩 30+ 条转调 `pipeline/*` 出片的口没拦**（`super-clone-wan3/submit`、`hot-explosion-wan3/submit`、`step5/run`、`tvc/run`、`detail-image/run`、`face-swap/submit`、`smart-fission/*`、`womenswear*`、`toutiao/run`…）：这些要**沿调用链追到真客户端**才能定性，没追就没拦，**按名字猜着拦一律不做**（会误伤纯本地操作，还给人"已经全拦了"的假安全感）。清点台账见 `docs/ADR-model-gateway.md`「花钱批准闸」节。
  - 🔴 **铁律的落码位置：闸必须钉在【花钱那一刀】，不是网关前门**（2026-10-09 事故）：一条会话在用户没点过批准的情况下出了 wan3.0 成片 ——
    证据链：`wan3_task.json` 提交 11:01:13 → `seg1_wan3.mp4` 落盘 11:17:39（16.65MB），而**网关账本 10:32 之后一行都没有**。
    原因不是"闸判错"，是**那条腿从不经 `gateway.invoke()`**：`super_clone_wan3.py:3020 → wan3_client.run_one_segment → submit_reference_to_video` 直连 RunningHub。
    ⇒ 已把闸搬进 `wan3_client._wan3_submit_guard()`（所有 wan3 腿的唯一咽喉，含 8090 各口、`smart_fission/step6_wan3_direct`、临时脚本），
    两道：**默认断开**（`config.WAN3_RH_ENABLED=False`，恢复要显式打开）+ **打开后仍必须有批准条子**；
    🔴 **只断提交**，`query_task/poll/download` 照常 ⇒ 断之前已提交的任务能用 taskId 捞回（重投=双花）。回归 `scripts/check-wan3-rh-cut.py`。
  - 🔴 **RunningHub 视频禁令：判据只有一份，跟着【腿】走，不跟着目录走**（2026-10-09 用户指令"视频严格杜绝走这条线路"）：
    咽喉 = `runninghub_client.rh_video_guard(spend_kind)`（`submit_task()` 调它；豁免清单 `RH_VIDEO_EXEMPT=("edit","image")` 是唯一源）。
    **默认拒**：`spend_kind` 默认 `"video"`，没显式声明就当生成视频 —— 而且是**白名单**（早先 `== "video"` 的写法传 `"banana"` 就绕过，已改）。
    被它拦住的真实腿：`h3_director_client:512`、`replica_wan3:165`、`product_motion/pipeline:131`、本模块三个 motion 包装器 ⇒ **H3/动作迁移走 RH OpenAPI 的都不许提交**（本地 RPA 腿 `h3-rpa`/`motion-rpa` 不碰这个客户端，不受影响）。
    🔴 **还有两条腿自己拼 HTTP、不经 `submit_task`**：`super_clone_wan3.py` 的 `step2_h3_execute`、`structural_replica_workflow.py` 的 `_h3_submit_task`（打 `/openapi/v2/minimax/hailuo-h3/multimodal-to-video`，且被 `app.py` **动态 import** ⇒ 按目录扫服务代码会把它漏成"一次性脚本"）。两条都已在提交前调同一个 `rh_video_guard()`。
    ⚠️ **强度到哪为止**：加工腿（译制/配音替换 = `lipsync`）与咽喉打的是**同一个** `/openapi/v2/run/ai-app` 端点，禁令按【挂载能力】放行而不是按 URL 形状 ⇒ 谁把 lipsync 换成生成类工作流就是绕过，闸拦不住，只能靠 `check-rh-video-ban.py` R10 那张登记表被人看见。
    ⚠️ 另：仓库根/`skills/` 下还有 13 个脚本自带 RH 提交端点（闸只报不判红）—— 它们是禁令管不到的腿，别当成"已经全拦了"。
    回归：`scripts/check-rh-video-ban.py`（含"守卫改空转 ⇒ 真发一次 POST"的变异对照）+ `scripts/check-wan3-rh-cut.py`；条数以各闸打印的汇总为准。
  - 🔴 **例外形状：花钱在别的进程里的腿，条子在【那一端】消费一次**（2026-10-06 立，2026-10-10 改范围）：
    `gateway.py` 的 `SPEND_APPROVAL_AT_SOURCE` 列出的 purpose，`invoke()` **不再重复扣段**（两处各扣＝逼用户批两遍，是自造的假账）。
    🔴 **当前 = 空元组**，而且**没有任何一端在问它**：8091 的动作迁移腿 2026-10-08 已剥离进网关轨（`SPEND_APPROVAL_AT_SOURCE = ()`），
    2026-10-10 用户又裁定**工作台那一端整个不要条子** —— "8091 还会问批准"这句老话作废（`D:\nzdh\womenswear\router.py` 的
    `/run`、`/step5-rpa`、`/retry` 三口不再调判定口，helper 与 `config.py` 里那三行 `SPEND_*` 都删了，备份在 `D:\nzdh\_bak-20261010\`）。
    🔴 **取消的只有独立工作台那一下**：判据 = 人是坐在那个 8091 页面前自己点的；**agent 那条轨照旧要批**
    （`gateway call video.generate/motion-rpa` → 直调本地 `backend/services/motion_rpa`，条子在 `invoke()` 里消费一次）。
    ⚠️ **代价要认**：现在任何能 POST `127.0.0.1:8091` 的本地进程/脚本都能不经批准起浏览器 RPA 烧 RunningHub 的真钱
    （60 分/段仍是**占位**，上游按 GPU 秒扣）。8091 只绑环回（闸 G1/G2 钉着），但"只有本机点得动"≠"只有人在点"——
    这条豁免不给 agent 用：**agent 要跑动作迁移只许走 `gateway call`，不许转调 8091 的 HTTP 口。**
    那一端的判定口 `POST /api/spend/guard` 机制**留着、暂时没住户**（只听环回、只消耗批准、8100 全文不调 `create_spend_pending`
    ⇒ 没有任何"创建批准"的 HTTP 口；预计扣分由网关按价目自己算，调用方自带 credits 等于放行，所以不许自带）。
    🔴 **配对判据**：`scripts/check-spend-approval.py` 的 G26 组钉 8090 那几个 `_spend_or_423`；
    `scripts/check-motion-rpa-lane.py` 的 **F 组**钉这一条的两半（F4 8091 源码里判定 helper 零出现 + F4c 反向对照"三口都还在、不是没读到文件"
    + F4b 网关那侧 `video.generate` 仍要批且 `invoke()` 真调判定口 + F4d 配置无残留 + F9 创建口不存在）。
    ⚠️ 该闸现在 **12 红**（D/E 组还在打已删的 8091 HTTP 转调形状，订正归 T9b）⇒ 别拿它的绿当保护，但 F 组这半是活的。
    要加新的跨进程豁免就往 `SPEND_APPROVAL_AT_SOURCE` 添，同时**必须在那一端加调用**——这张表是后门风险，配对关系由上面两组钉。
  - **一次报价覆盖整条方案，不是每次生成都问**（见上面「批准粒度」）：omni 把报价并进【方案批准】那条消息、hypit 并进 `pricing` 关口；批准一次即自动跑到下一个关口。
    🔴 这条现在有了机器形状：条子的 `count` 由 `--count-from <任务目录>` 从盘上算 ⇒ 一次点击覆盖整条（换路由/加段数仍会被拒）。
    🔴 **查"批到没有"只读 `gateway.py approvals`（或 8100 的 `wake`/`attest` 字段），绝不拿 `check_spend_approval()` 当探针**——
    它本身就是消费点，2026-10-10 有会话这样把另一条会话的 1 段额度吃光了（钱没扣、批准没了，随后回"马上提交"而盘上零 hold）。
  - 用户的同意**只覆盖那次列明的范围**：换路由、加段数、改片型 ⇒ 重新报价再确认。**超时/上游抖导致的自动重试不算扩范围**（失败本就不扣费），但要说明"这是重跑第几段"。
  - 文本/看图按 token 结算（1 分≈100 token），不必逐次确认；但**单条预估 >50 分** 或 **整批预估 >500 分** 要先报价。
  - 报价数字一律从 `gateway.py resolve` / `hypit pricing` 读，**禁止凭记忆写价**；没跑过的路由要标"无数据"。
  - 报价后**不要**在同一轮里顺手调用生成——等用户回话。
  - 回归：`python scripts/check-spend-approval.py`（真值表 + "闸在预扣之前" + 拒绝时账本零变化 + 双向对照）。
- 为什么走 tool-jobs 而非 MCP/插件：命令契约写在这里 + 各 skill 的 SKILL.md，**跨会话读文件即恢复、不靠 agent 记忆**；而**积分预扣/结算/审计只在网关里**，所以花钱的路径必须收在一处。⚠️ 旧说法"DSH 不加载 MCP"**已作废**（2026-10-05 实测：内核自带 `@deepseek-ai/dsh-mcp-client`，stdio + streamable-http，只是没装配进 cordis yml）——不用 MCP 是**选择**（审计与扣费），不是"不支持"，别再拿它当理由写进新文档。
- 🔴 **重启常驻服务（8100/8765）前先 `python D:\video-skill\backend\hypit_bridge.py preflight`，必须看到 `safe_to_restart:true`**。它报的是：在途生成腿、在途导出、未结算预扣。2026-10-02 我就是把"查在途"和"杀进程"写进同一条命令，等于没查——带着 3 条在途腿重启，那 3 段被判死（预扣已回滚，0 分损失，但白等了）。遗留预扣用 `gateway.py holds [N]`（只读，带 `age_hours`）/ `holds-rollback <精确id>` **指名**回收，不自动退（会退掉别的进程正在花的分 ⇒ 双花）；🔴 没有批量开关（`all` / `*` / `--all` 一律拒），旧行没有 ts 就显示"不知道年龄"，不猜。
  - 🔴 **8100 的存活判据是"答不答话"，不是"端口在不在"**（2026-10-11 现网：keep-alive 没设超时，插件 5s 一轮 + 表单页 8s 一轮把连接攒到 21 条、accept 积压打满 ⇒ 端口仍 LISTENING、进程仍活着，但新连接一律被拒，右栏与表单页同时死）。规则只有一份：`D:\video-skill\svc_8100_guard.py`
    （`status` 只读 / `sweep` 只掐卡死的那份 / `ensure` 看门狗每一跳 / `restart` **人工重启**）。
    三态 `HEALTHY|WEDGED|DOWN` 由 `classify()` 判，探针拿不到任何 HTTP 应答才算死 —— **404 也算活着**（拿 200 当"活"，路由一改名看门狗就每分钟杀一次健康服务；`/api/health` 在 8100 上根本不存在，用的是 `/api/model/caps`）。
    要手动重启 8100 就 `python svc_8100_guard.py restart`：任务的实例记录会卡成 `LastTaskResult=0x800710E0`（="还在跑"，`/run` 白敲），必须先 `/end` 并**确认 `wscript` 退净**再起。
    ⚠️ 三个自己踩过的坑，别再踩：① 守卫里调 `schtasks /run` 是**自我递归**（`.cmd` 就是任务动作），第二层会把刚起的那份 `/end` 掉；② `.venv\Scripts\pythonw.exe` 是重定向壳，会再生一个基础解释器子进程 ⇒ **"进程数=2"不等于双实例**，判重只数 `Get-NetTCPConnection -State Listen -LocalPort 8100` 的监听者；
    ③ 🔴 **兜底那条路自己不许有单点依赖，而且它平时根本不走**（2026-10-11 现网：8100 从 21:31 起一直 DOWN 到 23:53，首页四张技能封面全成碎图标 —— 客户看到的"文件丢失"就是这个，盘上封面 PNG 一张都没少）。
    死锁形状：守卫把 `model_admin` 的 stdout/stderr 指到**守卫自己那份** `_svc_8100.err.log`（任务那一跳又 `2>>` 同一个文件）⇒ 长期活着的服务继承了那份句柄 ⇒ 下一次真要重启时 `open(LOG,"a")` 撞 `PermissionError` ⇒ `ensure` 每分钟崩在同一行裸 open 上。
    **"只有出事才走的那条路"恰好是唯一会碰那份日志的路**，所以一行红都看不见，一出事就是永久死锁。
    现在：服务 stderr 走 `_svc_8100.service.log`（**看门狗的日志绝不被它看着的进程握**）；日志不可开 ⇒ 换带时间戳旁路 ⇒ 再不行 DEVNULL（宁可无声，不可不起）；`Popen` 炸了只报 `SPAWN-FAILED` 不把守卫带走；`status` 三态与 `ensure` 的"没起来"和"起了不应答"分开报。
    回归 `scripts/check-svc-8100.py`（W 组：三态真值表、白名单只杀 model_admin、404/拒连在线对照、`.cmd` 一行不许拆、`ensure` 里禁 schtasks、handler 有 `timeout` 且 bind 失败退 3、**W9–W9f 兜底路径不许因日志而崩**）。
- 完整设计：`docs/ADR-model-gateway.md`。
