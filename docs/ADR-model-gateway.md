# ADR：统一模型网关 + 积分适配层（skill 的模型调用底座）

- 状态：Accepted（P0-①② + HTTP 契约 + 价目/积分面板 已落地并实测；P0-③/真账本 待做）
- 日期：2026-10-01
- ✅ 已做：`gateway.py` 补 `text.complete`/`image.generate(default)` 适配器 + 积分账本桩(`Ledger` 四接口 precheck/preauth/commit/rollback, 本地 sqlite) + `invoke()` 信封 + `call` 子命令；实测 text.complete 返 "pong"、成功扣1、失败(未接适配器)回滚余额复原、流水正确。`AGENTS.md` + `AGENT-FRAMEWORK.md §4.1` 写入「模型调用轨」契约。
- ⏳ 待做：P0-③ 各 produce skill 的 SKILL.md 改引统一命令（灰度切，先不拆现有直连）；`image.generate` detail/generic + `video.generate` clone-r2v/agnes-v 适配器；P1 hypit provider 绑定 + 对账；P2 weeksir.com 真账本平替。
- ✅ 2026-10-01(续8a) **HTTP 契约已上线，宿主从 8090 换成常驻 8100**：`model_admin.py` 加 `/api/model/{caps,resolve,call,health}`（同进程复用 `gateway.invoke`）、`model_router.py` 删除、`app.py` 撤 include；实测 8100 `POST /api/model/call {text.complete/agnes}`→"ok"·扣1、未接 purpose→`ok:false`·charged0·余额不动、面板 `GET /` 200、8090 `/api/model/*` 404（无第二入口）。
- ✅ 2026-10-01(续8b) **价目/默认路由外置 + 积分面板**：`model_prices.json`（21 条价 + 7 条默认路由，mtime 懒加载、坏则回落内置）；`holds.route` 列 → 逐路由调用审计；8100 页面加"改价/选默认路由"（校验后原子写）与"余额/流水/审计"两张表；CLI `prices`/`credits`。⇒ **"设置积分"从此是面板动作、不是改代码**（真账本仍在 weeksir.com，见 P2）。
- 决策者：产品
- 相关：[[model-gateway-direction]] [[dsh-product-roadmap]] [[omni-delivery-contract]] [[super-clone-hypit-pipeline]] `docs/PRD-weeksir-platform.md`（§4.4 模型网关 / §6.x 积分账本） `docs/ADR-pipeline-server.md` `D:\video-skill\backend\gateway.py`

## 背景 / 问题
逐个装 skill 时反复"缺模型调用能力"。根因：**模型调用活在 agent 的上下文记忆里**（靠记忆把"这 skill 要用 X→去 D:\video-skill 调"串起来），不是一个**常驻、可被 skill 直接调**的服务。于是：上下文一清就断、每装一个 skill 重新接一遍、积分无处统一计量。

已排除的方案：
- **MCP 工具服务**：DSH 不加载 MCP（`AGENT-FRAMEWORK.md:6` 运行时只用自带 tool-jobs/tool-bash/tool-fs/tool-skill/tool-subagent）。MCP 只喂到 Qoder agent，喂不到 DSH skill。❌
- **DSH 插件给 agent 注入工具**：插件是 UI/命令面（ctx.slots/ctx.remote.commands），Agent 可调用工具集写死、"不另写 Agent 内核"。插件做不了调用层。❌（插件只适合做网关的**展示/仪表盘**，见 §展示层）

## 决策
**把模型调用做成"稳定契约 + 常驻服务"，skill 一律经 `tool-jobs`/`tool-bash` 调它；积分是这套网关对 weeksir.com 账本的适配层。** 分两层：
- **调用层（本 ADR 主体·治本）**：`D:\video-skill\backend\gateway.py` 为核，对外固定 CLI/HTTP 契约；skill 只认"能力名 + purpose"，凭证/模型/计价全在网关。
- **展示层（后续·可选）**：一个 DSH 插件页签看网关状态/积分余额/成本预估/批准卡（复用 pipeline-view 注册法）。

## 现状（grounded·`gateway.py`）
已有"大脑"，缺"契约 + 适配器 + 真积分"：
- `CAPABILITIES = text.complete / image.generate / video.generate / video.analyze`（:24）
- `ROUTES` 注册表：(capability,purpose)→{provider,client,fn,凭证字段}，密钥从 `settings/.env` 取、不落明文（:28-39）；`resolve()`/`safe_summary()`(脱敏)/`test_models()`(真探活) 齐（:57/80/151）。
- `call()` 分发**只实现了 `video.generate/omni-t2v` 与 `video.analyze`**，其余 `NotImplementedError`（:219-247）。
- `meter()` 是**积分桩**（按能力 flat 价 + 视频每 10s 加价，`ledger:"STUB"`，:90-98）。

## 契约设计（调用层）
### CLI 契约（skill 经 tool-jobs 定式调用，跨会话不变、不靠记忆）
```
python gateway.py caps
python gateway.py resolve <capability> [purpose]        # 脱敏摘要 + 预估积分
python gateway.py call  <capability> <purpose> --json '<params>'   # 归一 JSON 返回
python gateway.py test                                   # 各 route 真探活
```
`call` 返回统一信封（成功/失败都可机读）：
```json
{ "ok": true, "capability":"video.generate","purpose":"omni-t2v",
  "result": { "url":"...","path":"...","text":null },
  "credits": { "estimated":44,"charged":44,"hold_id":"h-...","balance_after":956 },
  "error": null }
```
失败：`ok:false` + `error`，且**已 rollback**（见积分流程）。

### HTTP 契约（同一 call() 再包一层·供 hypit provider / 外部工具 / 未来服务端）
**宿主 = 常驻管理进程 `backend/model_admin.py`（127.0.0.1:8100，计划任务 `WeeksirModelAdmin` 开机自启），不是 8090。**
端点：`POST /api/model/call {capability,purpose,account,params}`、`GET /api/model/caps`、`GET /api/model/resolve?capability=&purpose=`、`GET /api/model/health`（**并发探 21 条·实测 3.2s**）、
`GET /api/model/prices` + `POST /api/model/prices`（改价/改默认路由，校验后原子写）、`GET /api/model/credits`（余额+按路由审计+流水）、
**管理台专用**：`GET /api/model/board`（按能力聚合：入口/模型/密钥就绪/单价/默认/是否已接适配器/上次探活状态与延迟）、`GET /api/model/test?capability=&purpose=`（**单条点测**）。
探活结果在服务端缓存（`_HEALTH`，单条测也合并进去）→ 面板秒开、刷新不丢状态。

- 为什么同源 8100：管理面（写 .env / 测通 / 价目）与调用面（`/api/model/call`）**共用同一份 `gateway.ROUTES` 与同一把 key**，天然不分叉；8100 已常驻且已 `import gateway`，加路由零新依赖。
- 为什么**不挂 8090**：8090 是流水线任务服务器，模型端点挂它就得为激活而重启（丢内存态任务）、并把"统一 API"绑死在一个临时宿主上。原 `backend/model_router.py`（FastAPI 版）已删、`app.py` 的 `include_router` 已撤 → **8100 是唯一 HTTP 入口**，8090 只留流水线 + `/api/health/models`（8100 的 health 顺带读它）。
- CLI 轨不受影响：`gateway.py call` 仍是 skill 首选（`tool-jobs` 跑 Python，无需任何端口）。
- hypit 侧写**一个** provider 包把 baseUrl 指这里、bindings 把 seedance/wan/… 全绑过来 → hypit 生成也走同一入口（见 [[super-clone-hypit-pipeline]] provider 机制）。
- ⚠️ 8100 **无鉴权**，只绑 127.0.0.1、只运维自用；一旦要给客户机/公网调，必须先加 token 并整体挪到 weeksir 服务端（3.0）。

## 推理脑注册表（2026-10-01 全中转实测 · `text.complete` 现有 8 条腿）
模型名集中在 `config.py` 的「文本推理脑注册表」字段（.env 可覆盖），不散在代码里；单价按实测 token 分档（≈100 tok/分）。

| purpose | provider | 模型 | 实测 | 分/次 | 备注 |
|---|---|---|---|---|---|
| `agnes`(默认) | agnes 国内站 | agnes-3.0-flash | live-ok 0.9s | 1 | 兼作 `video.analyze` 的眼睛；文案短板见 §5.6 |
| `mimo` | mimo | mimo-v2.5 | live-ok 1.4s | 1 | |
| `mimo_pro` | mimo | **mimo-v2.6-pro** | live-ok 2.8s | 3 | 同题样本里最"人味"，推荐当剧本/口播脑 |
| `gemini38` | zexapi | **gemini-3.8-flash** | live-ok 3.9s | 6 | token 大户（~900） |
| `claude48` | zexapi | **claude-opus-4-8** | live-ok 3.2s | 2 | 便宜且干净 |
| `deepseekv4` | zexapi | deepseek-v4-pro | live-ok 2.2s | 7 | **思考型：max_tokens <2000 会"空正文"**（HTTP 200 但 content 空） |
| `qwen38max` | dashscope | qwen3.8-max | live-ok 1.4s | 2 | ⚠️ chat 必须走 `DASHSCOPE_CHAT_BASE_URL=…/compatible-mode/v1`，不是 `api/v1` |
| `gpt61` | nocannobb | gpt-6.1-sol | 上游抖（502/503） | 1 | 最省 token（~30）；**必须带浏览器 UA** |
| `zexapi` | zexapi | gemini-3.5-flash | — | 1 | 🔒 已屏蔽：503 无渠道（同 key 的 3.7/3.8/opus-4-8 正常 → 别把单条无渠道当整分组坏） |

**踩过的坑（写死在这里防止复发）**：`hub.nocannobb.com` 的 **403 error code: 1010 是 Cloudflare 拦"无 User-Agent 的脚本请求"**，不是 key 坏；网关所有 HTTP 请求现已统一带浏览器 UA。同理：探活路径 `/models` vs `/v1/models` 各家不同、route 无 model 时不能发 null、reasoning 模型小预算会返回空正文——**自家 probe 的现象 ≠ 上游事实，下"坏了"的结论前必须直连复现**。
**不可用（非代码问题，要你去处理）**：manxue 的 claude 系 410 渠道已撤、manxue gpt-5.5 403 余额不足（该 key 没钱）；dashscope 三种不同原因要分开看——deepseek-v3.2/r1 = **403 免费额度用尽**、`ZHIPU/GLM-5.2` = **400 产品未开通**（后台点一下）、**MiniMax 全系（`MiniMax/MiniMax-H3`、M2.1/M2.5/M2.7/M3）= 400 `code:"Arrearage"` 账户欠费**（同 key 的 qwen 系与 Kimi-K2 正常出词，所以不是模型不存在）；agnes 2.5-pro 三档 403 额度不足（要充值）。
**⚠️ MiniMax H3 语言模型的两条接入前提**（充值后也别当 OpenAI 兼容腿接）：① 它在 `compatible-mode/v1` 回 **404 "Unsupported model for OpenAI compatibility mode"**，只能走 dashscope **原生协议** `POST /api/v1/services/aigc/text-generation/generation`（body `{model, input:{messages}, parameters:{result_format:"message"}}`）→ 网关要单独写一个 `dashscope_native` 适配器，不能复用 `_chat_compat`；② "H3" 这个名字在别的中转全是**视频**模型（manxue `1ren-minimax-h3/-v2`=我们 `video.generate/h3` 在用；zexapi 另有 `minimax-h3-{480p,768p,1080p,max-*}` 六档未接），别把两者混成一个能力。
**默认脑切换**：面板「默认路由」一键改（`model_prices.json.defaults`），不改代码；正式定稿前建议跑 3 品类 ×2 次同题评测（打分维度=§5.5 合规 + §5.6 反幼稚腔）。

## 积分系统（账本适配层·对齐 PRD §6.x）

**原则：单一账本在 weeksir.com，网关只做"调用前预扣 / 成功提交 / 失败回滚"的适配，绝不在 DSH 另建用户/积分表。**（PRD:276/283）
接口（1.0 本地 sqlite 桩实现、3.0 换 weeksir.com API，**接口不变**）：
```
precheck(account)            -> {balance, ok}          # 余额够不够
preauth(account, cap, pur, p)-> {hold_id, cost}        # 按条预扣（PRD:346）
commit(hold_id, actual)      -> {charged, balance_after}
rollback(hold_id)            -> {refunded}             # 失败回滚该条
```
- **计价外置（2026-10-01 已落地）**：价目与"每能力默认路由"不再硬在代码里，而是 `D:\video-skill\model_prices.json`：
  ```json
  {"prices": {"text.complete/agnes": 1, "video.generate/omni-t2v": 40, ...},
   "defaults": {"text.complete": "agnes", "video.generate": "omni-t2v", ...}}
  ```
  `gateway.external_config()` 按 **mtime 懒加载**（面板保存后下一次调用即生效，无需重启）；文件缺失/损坏 → 打印一行警告并**回落 `PRICE_ROUTES`/`DEFAULT_PURPOSE` 内置兜底表**，配置坏掉不会让正在出片的管线停。CLI 可查：`gateway.py prices` / `gateway.py credits`。
- **调用审计**：`holds` 加 `route` 列（`PRAGMA` 检测后 `ALTER TABLE`，旧行 NULL）→ 可按 **路由×状态** 聚合次数与积分（`done`=已扣、`rolled_back`=失败已退），配合 `ledger` 流水即"谁在什么时候调了哪条模型、扣了几分"。管理入口=8100 页面两张表（价目/默认路由**可编辑**；余额/审计**只读**）。
- **保存校验**：`POST /api/model/prices` 只接受注册表里真实存在的 `cap/purpose`、价格必须是 0~100000 整数、默认路由必须是该能力下的合法 purpose；**任一条非法整批拒绝、不部分写入**，写文件用 `tmp + os.replace` 原子替换。
- 流程（`call()` 内串起来）：
```
resolve → 计价(注册表 price，或 skill manifest 声明的预估积分) 
 → precheck：不足→ 直接返回 ok:false + "去 weeksir.com 充值"，不发起生成（PRD:284/287）
 → preauth 预扣 → 调模型适配器 → 成功 commit / 失败 rollback → 写流水 → 返回信封
```
- **计价按路由逐条**（`capability/purpose` 粒度，取代早期按能力 flat 的桩）；视频按 `duration` 每 10s 折算一个单位。**扣在客户身份**（account = weeksir 客户，店铺 1:N，PRD:289），不是单个抖音号。
- **1.0 本机**：`ledger:"STUB"` 本地记账即可（PRD:458 本机阶段不接网站），但**接口就是上面四个**，3.0 平移到 weeksir.com 不改调用方。
- **与生成关口咬合**：omni/hypit 的"build 前预算批准"直接读 `resolve` 的 `credits.estimated`（[[omni-delivery-contract]] §9 预算护栏、[[super-clone-hypit-pipeline]] pricing 闸）。

## 待补适配器（P0）
`call()` 现缺：`text.complete`（各 purpose）、`image.generate`（default/detail/generic）。对着 ROUTES 里 `client.fn`（gpt_client.complete / agnes_image_client.create_image / runninghub_client / image2_client）补参数映射，返回归一 result。补完 `test_models()` 全绿即算底座通。

## 收敛：所有 skill 走同一入口
- omni：video-ui 内部改调网关 `call("video.generate", ...)`（现在 video-ui 直连 omni/agnes）。
- super-clone：:8090 runner 的 Wan 调用改走 `call("video.generate","clone-r2v")`。
- hypit：一个 provider 包 → 网关 HTTP（免 HypiHub 积分）。
- 新装 skill：`manifest` 声明"需要网关能力 X + 预估积分"（PRD:170），运行时 `tool-jobs: python gateway.py call ...`。**不再各自硬编码厂商 key/endpoint。**

## 安全（对齐 ADR-pipeline-server Gate 0）
- 网关只绑 `127.0.0.1`；`call` 会触发付费生成/子进程，**绝不 LAN/公网裸暴露**。
- 密钥只在 `.env`/settings，`safe_summary` 脱敏、CLI 不回显。
- 给客户前：只读化 + origin 白名单 + 执行端点鉴权（同 pipeline 网关红线）。

## 分期
- **P0（底座）**：定 CLI 契约 + 补 text/image 适配器 + 积分四接口本地桩 + 计价进注册表 + 写进 AGENTS.md（"要调模型=gateway.py call"）。
- **P1**：HTTP 契约 + hypit provider 绑定 + 与网站积分流水对账（PRD:474）。
- **P2**：weeksir.com 真账本 API 平替本地桩（接口不变）+ 展示层插件（余额/成本/批准卡页签）。

## 统一 API 注册表 + 积分价目（从 config.py 提取·✅已实现于 gateway.py）
`config.py` 是"有哪些 API"的唯一真相源（旧 model_admin 网页读同一份 → 不分叉）。已把全部 provider 汇入 `ROUTES`，每条带凭证字段 + `PRICE_ROUTES` 单价（相对值占位、可调）：

| capability | purpose | provider | 单价 | 适配器 |
| --- | --- | --- | --- | --- |
| text.complete | default/translate/agnes | gpt/openai/agnes | 1 | ✅default / ⏳其余 |
| text.complete | grok | grok | 2 | ⏳ |
| video.analyze | default/mimo | agnes/mimo | 6 | ✅ |
| image.generate | default/generic | agnes/image2 | 4 | ✅default / ⏳generic |
| image.generate | detail | runninghub-ideogram | 8 | ⏳ |
| image.edit | faceswap | runninghub DreamID-V | 20 | ⏳ |
| video.generate | omni-t2v | omni(zexapi) | 40/10s | ✅ |
| video.generate | agnes-v | agnes-video | 30/10s | ⏳ |
| video.generate | clone-r2v / wan3-dashscope / motion | wan3/dashscope/rh-motion | 50/10s | ⏳ |
| video.generate | seedance-2.5 / seedance-2.0 | vinted.cam(rhart sparkvideo·≥20s仅2.5) | 60/50 每10s | ⏳ |
| video.generate | h3 | manxue minimax | 60/10s | ⏳ |
| lipsync | default/vo | runninghub | 20/24 | ⏳ |
| transcribe | whisperx | 本地 | 0 | ⏳(本地免费) |

- 视频按 `duration` 每 10s 一单位折算（`meter()`）。`unit_price()`/`meter()` 供 `resolve` 与批准前估价。
- **注册表=全量目录（caps/resolve/meter 立即可用）；适配器(`call()`)渐进接**，未接 purpose 抛 `NotImplementedError`（实测：无论未接还是上游 503，都 `charged:0` + 回滚，余额不动）。
- 这张表就是**积分系统的数据底座**：账本(weeksir.com)按 `(capability,purpose)×时长` 扣这套单价。

## 结合 DSH 底层框架的落地方案（骑现成的轨，不另造机制）
DSH 的硬约束（`docs/AGENT-FRAMEWORK.md`）：skill 调外部计算**只有一条轨 = `tool-jobs` 跑 Python**（:140/:145，"生成代码不进插件"）；**LOOP.md 是中枢**、身份在 `LOOP.member/account`（:63/:69-70）；**批准只三类**、禁止每 HTTP 问一次（:175）；skill 在 SKILL.md 里声明"调哪条 Python"（:195）。网关就顺着这四条落：

### 1) 调用轨：契约写进文件，不写进记忆（这才是根治"缺模型调用"）
每个 produce 环 skill 的 SKILL.md 用**同一条命令模板**（`tool-jobs` 执行）：
```
python D:\video-skill\gateway.py call <capability> <purpose> \
       --account <LOOP.member> --json '<params>'
```
- 命令是**固定文本**、躺在 SKILL.md / AGENT-FRAMEWORK 里 → **新会话、新 skill 读文件即恢复**，不依赖 agent 记忆、不依赖 MCP/插件工具注入。这就是"模型能力常驻一等公民"在 DSH 里的等价实现。
- 唯一定义处：`AGENT-FRAMEWORK.md` 加一节「模型调用轨」+ `AGENTS.md` 加一句"produce 环调模型 = gateway.py call"；各 produce skill 引用同一模板（一处定义、处处引用）。

### 2) 身份：从 LOOP 取，积分扣在该身份
`--account` 传 `LOOP.member`（接网站后即 weeksir customer_id）。网关 precheck/preauth 都按这个 key → 天然对齐 PRD"扣在客户、店铺 1:N、账号隔离"（PRD:289）。

### 3) 积分预扣：折进框架已有的「方案批准」，不新增批准类
produce 环本来就有"方案批准"关口（:175）。把估价-预扣贴上去：
```
批准前： gateway.py resolve <cap> <pur>  → 出 credits.estimated → 摆进方案批准话术
人点头 → gateway.py call … → 内部 precheck(不足即停+提示充值) → preauth 预扣 → 调模型
        → 成功 commit / 失败 rollback → 返回信封(含 charged/balance_after)
```
不违反"批准只三类"——积分只是让**已有的方案批准**顺带显示成本与余额。

### 4) 产物与状态：走框架既有落盘
网关返 `result.path/url` → skill 写进 `productions/<task_id>/ASSETS.md`、更新 `LOOP.stage`、追加 `accounts/<acct>/SKILL_LOG.md`（框架已有这套）。产物本体留 D:\video-skill/outputs，指针进 productions（同 [[omni-delivery-contract]] 档案层）。

### 5) 1.0 账本 = 本地桩，接口不变
`ledger` 用本地 sqlite（key=`LOOP.member`），实现 §积分系统 那四接口；`AGENT-FRAMEWORK:207` 明确本阶段后置 weeksir.com 登录/积分 → P2 把本地桩平替成 weeksir.com API，**调用方与命令一字不改**。

### 6) 展示层（P2·可选·非调用轨）
一个 DSH 右栏插件页签，读 `LOOP.member` 的余额 + 最近 `gateway` 调用 + 批准前 estimated（复用 pipeline-view 的 `sidebarRightTabs.register` 注册法）。**只读**，不承担调用——调用永远走 §1 的 tool-jobs 命令。

### 一次请求在 DSH 里的完整走法（把网关嵌进去）
```
说话 → /weeksir 路由读 LOOP → 提名 produce skill → tool-skill 加载
 → skill 先 gateway.py resolve 估价 → user-questions「方案+预算批准」(已有关口)
 → 同意 → tool-jobs: gateway.py call --account <LOOP.member>
      → precheck/preauth → 模型适配器(gpt/agnes/omni/wan/…) → commit/rollback
 → 产物 path 落 productions/ → 更新 LOOP.stage → SKILL_LOG 记一条 → 回路由
```
**为什么这样就不"缺模型调用"**：调用轨 = 文件里的固定命令 + DSH 自带 tool-jobs；能力在代码+文档里、不在会话记忆里 → 上下文清空、换会话、装新 skill 都照样能调。

### 落地顺序（P0 具体动哪些）
1. `gateway.py`：补 `text.complete`/`image.generate` 适配器；`call` 收 `--account`；加四接口本地 sqlite 桩；CLI 契约（caps/resolve/call/test）。
2. `AGENT-FRAMEWORK.md` + `AGENTS.md`：加「模型调用轨」约定（一处定义）。
3. produce 三条 skill（omni/super-clone/hypit）SKILL.md：改引统一 `gateway.py call` 模板（hypit 另加 provider 包指网关 HTTP）。
4. 验证：新开会话、清空记忆下，一条 skill 仍能经 tool-jobs 调到模型 + 出 estimated。

## 距离目标评估 + 最短路径（2026-10-01·务实校准）
目标 = 统一 API 网关 + 可设置积分 + 商业化（DSH 为标准）。三目标分开看，代码/非代码分开：

- **A 统一 API 网关（≈65%）**：✅ 注册表覆盖 config 全 provider（含 seedance/vinted、zexapi、agnes/mimo）+ CLI `call` + HTTP `/api/model/{caps,resolve,call,health}`（**常驻 8100、已 curl 实测**）。差：① 真适配器 10 条（text agnes/mimo/zexapi、analyze default/mimo、image default/nano、omni-t2v、seedance 2.5/2.0），**剩 11 条 ⏳**（faceswap/lipsync/motion/h3/wan3-dashscope/detail/generic/agnes-v/transcribe，都不挡 omni）；② **所有 skill 还没改走它**（omni 开关默认关、super-clone 仍直连）；③ gemini/gpt/grok 上游抖，**当下只 agnes 文本稳**。
- **B 可设置积分（≈70%）**：✅ Ledger 四接口 + 逐条单价 + 失败不扣费 + **价目/默认路由外置 `model_prices.json`（mtime 懒加载、坏则回落内置）** + **8100 面板改价/选默认路由** + **`holds.route` 逐路由调用审计与余额/流水表**。差：① **真账本在 weeksir.com**（本地 sqlite 仍是桩、余额是假数、**没有充值入口**）——没 weeksir.com 积分 API 就不算能商用扣费；② 单价仍是人工拍的相对值，等接了上游真实计费再校准；③ 客户侧看不到"本次方案预计花多少分"的呈现（折进方案批准话术，见 §与生成关口咬合）。
- **C 商业化（早期·瓶颈是非代码三堵墙）**：① 🔴 **hypit 商用授权**（Apache-2.0 with conditions，给客户用/打包卖须找 Hypit.AI 谈）；②  **生成栈服务端化(3.0)+瘦客户端+发版流水线**（build_release/签名/更新源）；③ 🟠 **安全 Gate 0**（控制台无鉴权、cli 是 subprocess，给客户前只读化/origin 白名单/执行端点鉴权）。

**最短路径（按依赖）**：
1. ~~重启 8090 激活 `/api/model/*`~~ → **已不需要**：HTTP 入口搬到常驻 8100 并实测可达（8090 退回纯流水线服务器）。剩下的最短路径 = 用 `VIDEO_UI_VIA_GATEWAY=1` 重启 8765 跑一次 omni 端到端（含 `script_model=agnes`）。
2. 补 skill 真用的适配器（omni 的 nano 生图、seedance 视频）。
3. **P0-③**：omni 包 `zexp.chat` + env 开关 `VIDEO_UI_VIA_GATEWAY` 灰度走网关（第一个样板）。
4. ✅ ~~PRICE 外置 `model_prices.json` + model_admin 加改价/积分页~~ **已完成**（"设置积分"从改码变点面板；审计靠 `holds.route`）。
5. 并行推非代码墙：**问 Hypit.AI 商用授权** + **确认 weeksir.com 积分 API**（这俩决定 B/C 能否落地）。

**一句话**：统一入口已立、离"skill 都只认它"近（数天~两周）；可设积分卡在"改价面板 + weeksir.com 真账本"（中，含非代码）；商业化真正远的是 hypit 授权/服务端化/安全闸三堵非代码墙，不是再多写网关代码。

## 风险 / 待确认
- [ ] weeksir.com 是否已有正式积分/账本 API 可对接，还是 P1 先做适配层？（PRD:507）
- [ ] 单一账本"网站扣点 vs Agent 调模型扣点"如何对账、防两套漂移（PRD:498）——本地桩阶段先锁接口。
- [ ] video-ui/super-clone 改走网关的回归成本（它们现在直连模型）——先加网关旁路、灰度切。
- 回退：网关是薄适配层，call() 失败可临时让 skill 直连旧路径，不丢功能。
