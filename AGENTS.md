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

> 为什么这么定：`omni` / `hypit-pipeline` / `product-video-pipeline` 三个 skill 的描述都含"剧本→关键帧→出片"，
> 电商带货与影视创作又天然重叠（2026-10-02 实测：一句"短剧带货钩子"被漂到 omni）。
> 靠描述匹配去路由必然反复错，所以判定权交回给用户。

## 🔴 右栏 UI 铁律：先明确意图，才有 UI

**新会话一条 UI 都不许出现。** 只有**用户显式点了某个 skill**之后，才允许挂对应的台，且**排他**——同时只挂一个。

被调用后的**第一个动作**、任何引擎操作之前，登记意图（这一条和"花钱前必须报价"同级）：

```
python scripts/intent.py <skill> [任务名]
#   <skill> = 用户刚点的那个：omni | hypit | super-clone | product-video | film
#   short-drama 与 ai-film-router 都归 "film" 档（登记 film）；它俩是"点了就走"的总控工作流，
#   显式调用后先登记、再干活（排他挡掉 omni/hypit 台）
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
- 回归（改这条逻辑必须先过）：`check-intent-rule.py` 15 条 · `check-workbench-presence.py` 40 条 · `check-workbench-client.mjs` 28 条 · `check-intent-anchor.py` 9 条（建 run⇒钉意图的锚点，含"登记炸了也不能让建片失败"）· `check-export-gates.py` 29 条。
- ⚠️ 未接入网关的 skill **不得调用**：`product-video-pipeline` 目前直连 `https://zexapi.com`（`SKILL.md:81`、`scripts/poll_task.ps1:3`），绕开统一网关的预扣/结算/审计，且那条 key 实测欠费。改造到 `gateway.py call` 之前，即使被显式调用也要先告知用户这一条、不要直接花钱。
- 因为"以调用为准"，**不再需要写电商/影视的划界规则**——判定权在用户点的 `/skill`，agent 不猜。

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

**什么才叫"达成"**：产物真的落在盘上并且能出示 —— 视频 = `productions/<task>/outputs/<ascii名>.mp4` + 8100 直链 + 抽帧贴图；图片 = 目标文件落盘且**人眼看过画面内容**。
"已提交 / 运行中 / 上游回了成功状态" 都只是中间态，**不算交付**，不得当结论收工。

**以下都算违规中止**：
- 没轮询到终态就把"我发起任务了"交回给人
- 上游 5xx、超时、SSL 掐线、排队久 ⇒ 直接汇报失败停下
- 一批里失败几段就整批放弃，而不是只补跑那几段
- 产物落盘了却没贴图/没路径/没直链 ⇒ 视为没交付（曾被用户点名"视频你给我看啊"）

**必须做到**：自己盯到终态；失败**按死因分类**处理 —— 抖动类退避重试**同一个** job_id（连续 6 次查询异常才判死）、参数/合规被拒改参数或换合法路由；一批里失败 N 段就补跑那 N 段；判死时也要把 taskId 与上游原因如实交回，并继续做不受影响的部分。

**边界（这条不推翻既有闸；冲突时以闸为准）**：
1. 🔴 **花钱仍要先报价并等明确同意** —— "不停"指的是**批准之后盯到底**，不是擅自加段数、换更贵的路由、扩到别的片型；范围一变就重新报价。
2. 不重新提交"已判死但上游可能仍在跑"的任务（= 双花）；孤儿任务用 `job_id` 捞回结果。
3. 三个批准关口（方案 / 审帧 / 成片）与客服升级照旧停，重启常驻服务照旧先 `preflight`。
4. **同一个失败原因连续 3 次仍不通 ⇒ 停下报证据**（PID / 账本 error / 上游原文），不许无限循环烧钱 —— 防止"不停"退化成"死磕"。

## 会话绑定

每个会话同时绑：

1. **资源管理器**：本项目目录（素材与 LOOP）
2. **账号浏览器**：一个自研指纹环境；该浏览器可登录同一店铺的多个平台（抖店 / 千川 / 飞鸽），不混店

用户说账号矩阵、有哪些号、切换浏览器 → `/account-matrix`（上架后）。

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

## 生成器

本阶段客户只在 DSH 对话。jobs 调用 `D:\video-skill` 现成 Python。不打开 8090。不换对话模型（Agnes 3.0 Flash）。

商品资料本阶段不建档：出片时在对话里现问产品图和卖点即可。

## 模型调用轨（所有 skill 调模型的唯一入口）

任何 skill 要调模型（文字 / 图 / 视频 / 看图），一律经 `tool-jobs` 跑统一网关，**不各自硬编码厂商 key/endpoint、不直连供应商**：

```
python D:\video-skill\backend\gateway.py call <capability> [purpose] --account <LOOP.member> --json '<params>'
```

- `capability`：`text.complete` / `image.generate` / `video.generate` / `video.analyze`；`purpose` 看 `gateway.py caps`。
- **唯一例外形态 = hypit**：它的 Provider 协议只认 HTTP，所以生成腿经自写 provider `@weeksir/provider-gateway` POST `http://localhost:8100/api/hypit`（`/uploads`、`/videos`、`/videos/{id}`、`/rates`），服务端 `hypit_bridge.py` 仍转 `gateway.invoke` ⇒ **同一套预扣/结算/审计**。不算第二条轨，只是同一轨换了入口；鉴权用 `.env` 的 `MODEL_GATEWAY_TOKEN`（Bearer）。详见 `.dsh/skills/hypit-pipeline/references/monitor-contract.md` §1.5。
- 返回归一 JSON：`{ok, result, credits:{estimated,charged,tokens,basis,balance_after}, error}`。
- 🔴 **hypit 只有三条"已绑 capability"能花钱**（2026-10-03 定）：`@hypit/seedance@1#seedance-2-mini`（`model="mini"`，50 分/5 秒）、`#seedance-2.5`（`model="2.5"`，60 分）、`@hypit/minimax-h3@1#minimax-h3`（`<h3:…>` 元素，无 `model` 属性，60 分）。官方 `hypihub.default` 端点已从 `hypit_runs/hypit.runtime.json` **摘掉**（备份 `hypit.runtime.json.bak-hypihub`）⇒ 未绑定的写法在 `hypit plan` 就报错、0 分，不再静默 POST `https://hypit.ai`（2026-10-03 实测漏绑一条就是这个走法，靠对方欠费才没花出去）。四张表（profile bindings / `provider.js` LANES / 桥 `HYPIT_MODELS`·`LANE_SPECS` / 网关 `ROUTES`）改任何一张都跑：`python scripts/check-runtime-bindings.py`。
- **积分**：网关调用前自动 校验余额→预扣→成功提交 / 失败回滚；不足则 `ok:false` 提示去 weeksir.com 充值。`--account` 用 `LOOP.member`。
  - `basis`：`usage`=按真实 token 结算（文本/看图，费率 `model_prices.json.token_rate`，1 分≈100 token）、`flat`=按条计费(生图/生视频)、`usage-capped`=超单条封顶被截。
  - `estimated` 只是**粗略预估**：思考型模型同题消耗实测波动 28→4396 token，报价/护栏看 `estimated`，**对账看 `charged`+`tokens`**。
- **估价**（批准前给人看成本）：`gateway.py resolve <cap> [purpose]`，或读信封 `credits.estimated`。折进"方案批准"，不新增批准类。
- 🔴 **花钱前必须在对话里报价并等到明确同意（铁律，对所有 skill 生效）**：调任何**按条计费**的能力（`video.generate` / `image.generate` / `image.edit`）之前，最后一条消息里必须列清：`路由(供应商+模型)`、`单价 分/段`、`段数或次数`、`预计总扣分`、`当前余额`，并明确问"跑不跑"。
  - **一次报价覆盖整条方案，不是每次生成都问**（见上面「批准粒度」）：omni 把报价并进【方案批准】那条消息、hypit 并进 `pricing` 关口；批准一次即自动跑到下一个关口。
  - 用户的同意**只覆盖那次列明的范围**：换路由、加段数、改片型 ⇒ 重新报价再确认。**超时/上游抖导致的自动重试不算扩范围**（失败本就不扣费），但要说明"这是重跑第几段"。
  - 文本/看图按 token 结算（1 分≈100 token），不必逐次确认；但**单条预估 >50 分** 或 **整批预估 >500 分** 要先报价。
  - 报价数字一律从 `gateway.py resolve` / `hypit pricing` 读，**禁止凭记忆写价**；没跑过的路由要标"无数据"。
  - 报价后**不要**在同一轮里顺手调用生成——等用户回话。
- 为什么走 tool-jobs 而非 MCP/插件：DSH 不加载 MCP、插件不能给 Agent 注入工具（见 `docs/AGENT-FRAMEWORK.md:6`）。命令契约写在这里 + 各 skill 的 SKILL.md，**跨会话读文件即恢复、不靠 agent 记忆**。
- 🔴 **重启常驻服务（8100/8765）前先 `python D:\video-skill\backend\hypit_bridge.py preflight`，必须看到 `safe_to_restart:true`**。它报的是：在途生成腿、在途导出、未结算预扣。2026-10-02 我就是把"查在途"和"杀进程"写进同一条命令，等于没查——带着 3 条在途腿重启，那 3 段被判死（预扣已回滚，0 分损失，但白等了）。遗留预扣用 `holds-pending` / `holds-rollback <id>` **指名**回收，不自动退（会退掉别的进程正在花的分）。
- 完整设计：`docs/ADR-model-gateway.md`。
