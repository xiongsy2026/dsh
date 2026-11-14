# ADR：统一模型网关 + 积分适配层（skill 的模型调用底座）

- 状态：Accepted（P0-①② + HTTP 契约 + 价目/积分面板 已落地并实测；P0-③/真账本 待做）
- 日期：2026-10-01
- ✅ 已做：`gateway.py` 补 `text.complete`/`image.generate(default)` 适配器 + 积分账本桩(`Ledger` 四接口 precheck/preauth/commit/rollback, 本地 sqlite) + `invoke()` 信封 + `call` 子命令；实测 text.complete 返 "pong"、成功扣1、失败(未接适配器)回滚余额复原、流水正确。`AGENTS.md` + `AGENT-FRAMEWORK.md §4.1` 写入「模型调用轨」契约。
- ⏳ 待做：P0-③ 各 produce skill 的 SKILL.md 改引统一命令（灰度切，先不拆现有直连）；`image.generate` detail/generic + `video.generate` clone-r2v/agnes-v 适配器；P1 hypit provider 绑定 + 对账；P2 weeksir.com 真账本平替。
- ✅ 2026-10-01(续8a) **HTTP 契约已上线，宿主从 8090 换成常驻 8100**：`model_admin.py` 加 `/api/model/{caps,resolve,call,health}`（同进程复用 `gateway.invoke`）、`model_router.py` 删除、`app.py` 撤 include；实测 8100 `POST /api/model/call {text.complete/agnes}`→"ok"·扣1、未接 purpose→`ok:false`·charged0·余额不动、面板 `GET /` 200、8090 `/api/model/*` 404（无第二入口）。
- ✅ 2026-10-01(续8b) **价目/默认路由外置 + 积分面板**：`model_prices.json`（21 条价 + 7 条默认路由，mtime 懒加载、坏则回落内置）；`holds.route` 列 → 逐路由调用审计；8100 页面加"改价/选默认路由"（校验后原子写）与"余额/流水/审计"两张表；CLI `prices`/`credits`。⇒ **"设置积分"从此是面板动作、不是改代码**（真账本仍在 weeksir.com，见 P2）。
- 决策者：产品
- 相关：[[model-gateway-direction]] [[dsh-product-roadmap]] [[omni-delivery-contract]] [[super-clone-hypit-pipeline]] `docs/PRD-weeksir-platform.md`（§4.4 模型网关 / §6.x 积分账本） `docs/ADR-pipeline-server.md` `backend/gateway.py`

## 背景 / 问题
逐个装 skill 时反复"缺模型调用能力"。根因：**模型调用活在 agent 的上下文记忆里**（靠记忆把"这 skill 要用 X→去 backend 调"串起来），不是一个**常驻、可被 skill 直接调**的服务。于是：上下文一清就断、每装一个 skill 重新接一遍、积分无处统一计量。

已排除的方案：
- **MCP 工具服务**：DSH 不加载 MCP（`AGENT-FRAMEWORK.md:6` 运行时只用自带 tool-jobs/tool-bash/tool-fs/tool-skill/tool-subagent）。MCP 只喂到 Qoder agent，喂不到 DSH skill。❌
- **DSH 插件给 agent 注入工具**：插件是 UI/命令面（ctx.slots/ctx.remote.commands），Agent 可调用工具集写死、"不另写 Agent 内核"。插件做不了调用层。❌（插件只适合做网关的**展示/仪表盘**，见 §展示层）

## 决策
**把模型调用做成"稳定契约 + 常驻服务"，skill 一律经 `tool-jobs`/`tool-bash` 调它；积分是这套网关对 weeksir.com 账本的适配层。** 分两层：
- **调用层（本 ADR 主体·治本）**：`backend/gateway.py` 为核，对外固定 CLI/HTTP 契约；skill 只认"能力名 + purpose"，凭证/模型/计价全在网关。
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
python gateway.py quote   <capability> [purpose] --json '<params>'   # 只算不花钱：分档后的预计扣分（按时长计费的腿必须用它，光看单价算不出）
python gateway.py prices                                  # 价目源（外置 json / 内置兜底）+ 每条路由的分
python gateway.py credits                                 # 余额 + 按路由的流水汇总
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
- hypit 侧写**一个** provider 包把 baseUrl 指这里、bindings 把 seedance/wan/… 全绑过来 → hypit 生成也走同一入口（✅ 已实装，见下面「hypit 生成腿接网关」节）。
- ⚠️ 鉴权现状（实测）：8100 只绑 127.0.0.1；**`/api/hypit/*` 已验 `Authorization: Bearer $MODEL_GATEWAY_TOKEN`**（`.env` 里非空即生效，无 token=401），**`/api/model/*` 与面板 `/` 仍是裸的**（本机运维自用）。要给客户机/公网调，必须把校验铺到全部写端点并整体挪到 weeksir 服务端（3.0）。

## hypit 生成腿接网关（2026-10-01 实装 · 2026-10-02 端到端实测）

hypit 的 Provider 协议只认 HTTP（`POST /uploads`、`POST /videos`、`GET /videos/{id}`、`GET /rates`），不吃 Python 函数 ⇒ 它是统一网关的**唯一 HTTP 客户端**，不是第二条轨。

- **服务端** = `backend/hypit_bridge.py`（挂在 8100 的 `/api/hypit/*`）：模型白名单 `HYPIT_MODELS`→网关 `(capability,purpose)`；`handle_submit` 建 sqlite jobs 行 + 守护线程跑 `gateway.invoke`（**预扣/结算/回滚全复用**），成功把 mp4 移到 `backend/outputs\_hypit_jobs\<jid>.mp4` 并以 `/api/hypit/assets/<f>` 出片（jobs 状态存 `.gateway_jobs.sqlite`）；`check_support` 对超 10s、非 720p、视频/音频参考**明确拒绝**（不静默丢弃）。
- **客户端** = 自写包 `C:\dsh\hypit_runs\packages\provider-gateway`（`@weeksir/provider-gateway`），Profile 里 endpoint `weeksir.gateway` + binding `@hypit/seedance@1#seedance-2-mini`；凭据 `weeksir.gateway` 走 hypit 平台凭据存储（`{store,platform,key}` 引用，明文不落盘）。
- **三条硬约束**（都踩过）：① hypit 的 Profile 校验只收 https 或**主机名写作 `localhost`** 的 http，填 `127.0.0.1` 被拒 ⇒ baseUrl/PUBLIC_BASE 一律 `http://localhost:8100`（解析到 127.0.0.1，`::1` 不通）；② `pricing.kind="page"` 要求 https ⇒ 不声明 `pricing`，改 `readPricing()` 拉 `/rates`；③ 上游要能自己抓参考图 ⇒ `/uploads` 必须回**公网 URL**（现走 imgbb）。
- **实测到哪一步（2026-10-02 端到端·已花钱）**：
  - **回传链路确证通过**：网关侧资产 `outputs\_hypit_jobs\f5731b02….mp4`（1,870,530 字节 · sha1 前缀 `50520bfef489`）与 hypit 自己结果库 `.hypit\results\2026-10-01\bld_…8CE40F93B3\files\file-0001.mp4` **同大小同哈希** ⇒ `gateway.invoke` → 记账 50 分 → `/api/hypit/assets` → hypit collect → 落进 Result Repository 全程无损耗。`hypit plan/pricing` 也确认生成腿 `weeksir.gateway (@weeksir/provider-gateway)`、价目读我们的 `/rates`。
  - **✅ 2026-10-02 全链首次跑穿（1 段最小工程 `hypit_runs\productions\e2e-one\`）**：`hypit build` 完成 6 步、产出 `final.video`；付费段资产 `a9d37276….mp4`（2,392,271 字节 · sha1 `f5c49119a89a`）与结果库 `file-0001.mp4` 同大小同哈希，且 `result.json` 里它的输出名就是 `seg1-video.video` ⇒ **生成→扣费→回传→收片→归一化→渲染→成片** 闭合。全程 ~30 分钟，其中 ~25 分钟在等那一段付费生成。
  - **重试实测有效（这次正是被它救回来的）**：账本流水为 `preauth → rollback(5a58a48247da) → preauth → rollback(63a63c0be978) → preauth → done(15916d9a0d4c)` = 前两次上游失败、第 3 次成功（`MAX_ATTEMPTS=3` 刚好用满）。⇒ 上游抖的时候，**没有重试的旧版必然整条失败**（此前 8 连败含这种情况），别把它判成"我们接线坏了"。
  - **单段真实延迟 305 / 359 / 373 / 405 秒**（5s·720p·seedance-2-mini）；上游自己会在 ~400s 判 `生成超时，请稍后重试（积分已退回）`。⇒ 我们轮询上限已从 900s 提到 **1800s**（900s 曾把正在成的片误判超时）。
  - **失败全在上游，但要分两类（重试只对该重的那类有效）**：真实 27 段里 **瞬时类**=上游生成超时×4、503 参考图下载繁忙（原话"请稍后使用同样的请求重试"）×2、502×1、参考图处理失败 400×1；**策略类**= `参考图未通过肖像保护，请更换为本人肖像或非人物参考图后重试（积分已退回）` **1 次（≈4%）**。⇒ `RETRYABLE=(502,503,504,service_unavailable,参考图,生成超时)`，`NON_RETRYABLE=(肖像,本人肖像,内容不合规,违规,敏感)` **先排除**；⚠️ 都不含"轮询超时"（我们等够≠上游没出片，重试=重复花费）。历史 bug 复盘：肖像拒绝正文以"参考图"开头 ⇒ 被旧 RETRYABLE 误判成可重试，那 1 段白跑 3 次（≈15 分钟）。12 类历史错误已全部回放校验。
  - **人脸参考图不是硬墙**：含人脸的段成功过 10 次，肖像保护只在 1/27 出现；但它是**非确定性**的（同图同词上次过、这次拒），所以对"一次要全成 4 段"的 build 仍是乘性风险 ⇒ 真人片型的真解法是**段级复用**（成功段固化成素材，只补缺段），不是换模型。
  - **一条 build 要 N 段全成才过**：单遍统计 27 条真实段 ⇒ **段级(含重试)成功率 15/26 ≈ 56%**，4 段全成 ≈ **10%**。所以"8 次 build 全灭"是**上游稳定性 + 组合概率**问题，不是接线问题 ⇒ 结论：**别盲目重跑多段 build**；先拿"1 段最小工程"（`hypit_runs\productions\e2e-one\`，6 请求里 5 个本地免费、只 1 段付费）验链，再谈多段；正解是**段级复用**（成功段固化成素材、只补缺段）。
  - 我方曾犯的错也记着：**422「请填写提示词」的真因在 provider 侧**——`POST /videos` 送的是 `{model, input:{字段}}` 信封（`{...request}` 没摊平），而服务端只读平铺字段 ⇒ prompt 传空。修法两处：`provider.js` 提交改成 `{...request.input, model, account}`（根因），`hypit_bridge._normalize()` 再把两种形状都摊平（兜底，已用两条真实请求回放验证）；另外**重启常驻 8100 会打断在途生成**（现在启动时把上个进程遗留的 `queued/running` 判死、遗留 `pending` 预扣回滚）。
  - **未证实**：同期 Google Flow 公告（"Image uploads are failing for some users"）与我们的 `参考图` 类失败**没有证据表明同源**——vinted 与 Google Flow 是不同上游，只是症状同名；对照实验（同一条 1 段工程只换参考图托管源）用户决定暂不跑。
- **license 边界**：hypit = *Apache-2.0 with conditions*（非纯开源）。自用、以及用它是给我们自己客户出片（产物归我们）可走；**把 hypit 打包分发给客户、或多租户对外服务，需 Hypit.AI 书面授权**（sales）。3.0 服务端化前先把这条谈定。

## DSH agent 脑的准入判据（2026-10-05 AFS/chat2api 事故 · 回归 `scripts/check-agent-brain-lane.py` 7 条）

事故形态：往 `settings.yaml` 加了个 `providers.glm`，baseURL 指 `http://127.0.0.1:8080/v1`，model id 手写 `GLM-5.3 / GLM-5.3-thinking / Kimi3 / Kimi-K3.1`。8080 上是 **AFS（内部名 chat2api）**——它把 chatglm.cn / kimi **网页会员账号里的托管智能体**包成 OpenAI 形状：对面自带沙箱、自带系统提示、自带工具，**拿不到也不执行 DSH 的 39 个本地工具**。拿 DSH 真实请求重放，它回 `{"name":"finish"}`（上游智能体的收尾哨兵，不在我们工具表里 ⇒ DSH 派发不到）。

**判据（一句话）**：agent 脑必须是**裸模型 API + 真 function calling**。账号桥只能当**文本腿**，且当腿时 `basis` 不许是 `usage`（四条 lane 的 usage 实测恒为 `prompt_tokens:1, completion_tokens:1` = 假计量）。

**关键事实：接云端 GLM/Kimi 不需要装任何插件。** DSH 的模型层（`@earendil-works/pi-ai`，`dsh-llm-pi-ai` 经 `providers/all` 的 `getBuiltinProviders` 枚举）已经把官方裸模型端点内置在册：

| provider id | 端点 | 在册模型（节选） | 鉴权 env |
|---|---|---|---|
| `zai-coding-cn` | `https://open.bigmodel.cn/api/coding/paas/v4` | **glm-5.3** / glm-5.3-flash / glm-5.2 / glm-4.7 | `ZAI_CODING_CN_API_KEY` |
| `zai`（国际站） | `https://api.z.ai/api/coding/paas/v4` | glm-5.3 / glm-5.2 / glm-4.7 | `ZAI_API_KEY` |
| `moonshotai-cn` | `https://api.moonshot.cn/v1` | **kimi-k3** / kimi-k2.7-code / kimi-k2.6 | `MOONSHOT_API_KEY` |
| `kimi-coding` | `https://api.kimi.com/coding`（`anthropic-messages`） | k3 / k3-256k / kimi-for-coding | `KIMI_API_KEY` |

官方 `glm-5.3` 的 `compat` 里带 `thinkingFormat:"zai"` + `zaiToolStream:true` —— **工具流有专门适配**，这正是桥完全没有的那部分。所以"名字叫 GLM-5.3"从来不等于"接的是 GLM 的 API"，差别只在 baseURL 与一把 key。

**闸钉了什么 / 故意没钉什么**：L1–L3 钉默认脑不得解析到 loopback provider、model id 不得落在桥 lane 名单里；L4 钉桥地址只准出现在 `scripts/probe_*.py`（任何 skill/插件/文档硬编码它 = 长出第二条调用轨）；L5–L7 钉上表这三份注册表文件确实在册。**故意不钉**"AFS 进程活着没""托管开关在不在关闭"——那是外部进程状态，钉了就是随机假红的假保险；桥 lane 将来登记进网关时的 `basis != usage` 也留到登记那一刻再断言。

## 花钱批准闸（2026-10-06 落码 · 回归 `scripts/check-spend-approval.py` 50 条；人证前置 2026-10-08 · 回归 `scripts/check-approval-attest.py` 44 条）

用户口径（原始，2026-10-06）：**"只要是生成视频相关的，必须算账我确认"**。原状是纸面的——`gateway.py` 里 `grep approve|consent|批准` **零命中**，
任何一条会话敲 `gateway.py call video.generate …` 都会正常 precheck→preauth→发起。现在钉在发起口上。
🔴 2026-10-09 口径收窄成一句：**只有生视频要批，其他都不要**（见下表"清单"行；闸 G1/G1b/G1c/G1d 钉住这条，含"清单被清空/被误扩回四类都要判红"的变异对照）。

| 件 | 落点 | 说明 |
|---|---|---|
| 清单（唯一源） | `gateway.SPEND_APPROVAL_REQUIRED` | 🔴 **2026-10-09 用户改口径："除了生视频要审批，其他都不要审核"** ⇒ 现在**只剩 `video.generate`**。不再要批：`image.generate` / `image.edit`（生图·改图）、`video.edit`（超分·去水印），加上本来就免批的 `text.complete`、`video.analyze`。<br>⚠️ **免批 ≠ 免账**：这些腿仍走同一条 `precheck→preauth→settle→失败回滚`，扣分与留痕照旧；代价要认——它们现在可被任何一条会话直接花掉，出事只能事后从账本查，不是事前拦。<br>底线钉在闸里（G1a）：`video.generate` 绝不允许被移出清单，移出 = 闸整个失效。 |
| 判定 | `gateway.needs_spend_approval()` + `check_spend_approval()` + 纯函数 `approval_allows()` | 纯函数单独拿出来，真值表能整块抠出来跑（够/不够/过期/换 purpose/换账户/通配 `*`/坏条子） |
| 位置 | `invoke()` 内，**在 `ledger().precheck/preauth` 之前** | 拒绝 ⇒ 不建 hold、不扣分、不发 HTTP；闸在预扣之后等于没闸 |
| 条子 | `backend/.gateway_spend_approvals.json`（env `GATEWAY_SPEND_APPROVALS` 可隔离，自测走临时目录） | 写死 能力+purpose+账户+段数+分数上限+24h 过期；`used/used_credits` 当场记账，用完/过期自动回到拒 |
| CLI | `gateway.py approve / approvals / revoke` | `approve` 少参数回用法并以 2 结束；顺手修了 `_cli` 返回码被丢弃的老毛病（用法错误以前也退 0，调用方会把"没执行"当成功） |
| 绕网关的腿 | 8090 `app.py` 的 5 个已核实出口 | `_spend_or_423(cap, purpose, label)` 是 app.py 里唯一的接入口，内部调**同一个** `gateway.spend_guard` ⇒ 一套条子、一处判定。实测无条子时 `/api/h3-studio/generate`、`/api/hook-extend/submit`、`/api/one-shot-replica/generate` 都回 **423**、任务不提交 |
| 会话隔离 | 条子可带 `session` | 🔴 2026-10-06 补：我拿一次不相干的端点探测，**吃掉另一会话为 video-enhance 封面签的 1 段/4 分**（闸按 capability/purpose 匹配，判得"对"，但额度是共享的 ⇒ 谁都能花别人的批准）。现在签条子可加 `--session <会话id>`，绑定的条子只有那条会话能用；探测方不报会话就用不了别人的额度。回归 G11b-G11e + 反向对照 M11/M12 |

⚠️ **边界要说清（不许假装它是人证）**：~~agent 技术上也能跑 `approve` 伪造一张~~
**（2026-10-08 已改：这一条不再成立，见下面"人证"节）** 闸本身的作用是
【默认拒 + 范围写死（能力/purpose/账户/段数/分数/过期/会话）+ 留痕可查（`scope` 记用户批准的原话）】。
因此**报价那条消息照旧要发、照旧等回话**，`approve` 只是把已经拿到的同意
落成有形状、可撤销、可对账的授权；拿"我自己 approve 过了"代替用户的同意 = 违规，且事后能从 `scope` 里查出来。

### 人证：把"人点了头"变成机制（2026-10-08 第二版 · 回归 `scripts/check-approval-attest.py` 39 条）

2026-10-08 全量审计的结论：**`gateway.py approve` 全程零校验**（没有 tty 检查、没有交互确认、没有来源白名单），
agent 在自己的 shell 里一条命令就能签出任意 cap/purpose/段数/分数的条子；更糟的是
`check_spend_approval` 的**拒绝文案里印着现成的伪造命令**（旧 `:2738`）—— 等于把说明书交给调用方。
盘上还找到了实例：`C:\dsh\scratch\wan3-smart-run.py`（19:20）、`scratch/h3-rpa-916-run.py`（10-06 21:38）
都是**程序化调 `grant_spend_approval` 签条子**的脚本（未入库，别的会话写的，我没动）。

#### 两版试错都记下来，别再走回去

| 版本 | 形状 | 为什么废 |
|---|---|---|
| v1 | 系统弹窗点【是】+ 回敲 6 位码 | 用户否掉："太麻烦了换个方式"、"也不要输入口令了"。且我拿 `isatty`/`GetFileType` 当"人在不在"的判据，**把用户本人的真签名拒了一次**（它分不清"人自己开的终端"和"agent 替人开的 PowerShell"） |
| v2 | 只弹系统 MessageBox 点【是】 | 🔴 **实测不可信**：`subprocess`（stdin=DEVNULL、全程无人）跑一次，`MessageBoxW` **自己返回 IDYES 并真签出了条子**，回归闸 G1/G2/G5 两次抓到（假条子落在闸的 scratch 账本，生产 json 未受污染）。同类毛病：`MB_TIMEOUT` 这台机器上不生效。**系统弹窗这条路整个删掉，不留兜底** |
| v3（现） | pending + 一次性码 + DSH 内核审批卡 | 见下 |

#### v3 的形状（一次点击，零口令）

```
agent: gateway.py approve …      → 只建【pending】（报价 + 6 位码），此时 0 额度
       gateway.py call video.generate … → 没有条子 ⇒ 照旧拒（approval_required）
人（两条路任选）：
   ① DSH 对话里的品牌审批卡（主路，待落码）：logo + 「专注电商AI落地」+ 报价 + 码 → 点【允许一次】
   ② 人在【自己的终端】：gateway.py code <pending_id> 看到码 → gateway.py consumed <id> <码> --session <会话>
```

| 件 | 落点 | 说明 |
|---|---|---|
| 待批 | `create_spend_pending()` / `spend_pending()` | 默认读法**不吐码**，只有插件渲染那条路显式 `with_code=True`；TTL 15 分钟（`GATEWAY_SPEND_PENDING_TTL`） |
| 🔴 码不进 stdout | `approve` 的输出 | 跑 `approve` 的多半就是 agent —— **码一旦进管道，它自己就能跑 `consumed` 伪造核销，整套人证归零**。闸 G4 把这条钉死（断言 approve 输出里【没有】码） |
| 核销 | `consume_spend_approval(id, code, session)` | 判据：存在 / 未过期 / 仍是 pending / **会话必须一致** / 码用 `hmac.compare_digest` 比（大小写不敏感）。任一不过 ⇒ 0 额度 + 落 `denials`；成功 ⇒ pending 出列（**不可重放**），条子带 `attest={method:"dsh-card-click", pending:<id>}` |
| 免批类 | `text.complete` / `video.analyze` | 不受人证前置影响（否则每条文案都要点头，纪律会废掉） |
| 闸自测后门 | `ALLOW_GRANT_WITHOUT_ATTEST`（默认 `False`） | **不是环境变量也不是 CLI 参数**，只能进程内赋值；双保险 `SPEND_APPROVALS_PATH != _APPROVALS_PATH_DEFAULT` ⇒ 即便置 True 也**物理上写不进生产 json**（闸 E2/E3 双向钉）。6 个既有闸各自显式声明自测模式 |
| 拒绝文案 | `check_spend_approval` | 不再印 `gateway.py approve`、不再印可照抄的 `--credits/--count` 参数串（闸 F1/F2） |
| 留痕 | 同一份 json 的 `denials` + `grants.attest` | `approvals` 打"条子 N 张 / 被拒 M 次 / 待批 K 张"，每张条子标人证方式（`dsh-card-click` vs `gate-selftest` vs 旧条子的 `None`） |

#### 批准 = 点火（2026-10-14，用户裁定"审批条子的时候就是提交wan3.0直接触发，别通过agent，太慢了"）

现网依据不是"慢了几十秒"，是**根本没人跑**：session-cb4687fe 16:14 批准核销成功，唤醒队列挂着 5 条，
`form-wake 等着没叫: 原因=agent-not-live` ⇒ 会话不在前台 = 永远不叫 ⇒ 67 分钟后账本连一笔 preauth 都没有。
"必须盯到终态"这条铁律挂在一条**可以不存在**的通道上（agent 活着），而【提交】这一步本身是机械的。

| 件 | 落点 | 说明 |
|---|---|---|
| 点火 | `model_admin.kickoff_submit(sid, grant)` | 人在批准窗/批准页点【批准，开始跑】那一下，`/api/spend/approve` 里**先起后挂号**：`runner/run.py --from-form --session <id> --stage submit`，牌子 `.submit.pid`、日志 `submit.log`。顺序反了 = agent 醒来看到"没人提交"再 call 一次 = 双提交 |
| 为什么服务端可以花这一刀 | 条子本身 | 批准那一下已是**完整授权**：capability/purpose/account/段数/分数上限/会话/提交计划指纹 `plan_id`/24h 全写死在条子里，"该提交什么"不靠 agent 现场判断 |
| 五道护栏（每条不起都要出声） | 同上 | ① `attest.method=dsh-card-click`（人点过）② 未过期 ③ `used < count` ④ 该 lane 被某档合同 `spend:` 认领**且**在 `KICKOFF_RUNNERS` 白名单 ⑤ 任务目录没有活着的 `.submit.pid`。`why` 原样进唤醒项 `kickoff_why` |
| 唤醒文案分叉 | `plugins/ops-gateway` `wakeText()` | `kicked_off` ⇒「服务端已替你提交，这一轮你只做交付」（明写不要再 `gateway call` / 不要再敲 runner）；没点成 ⇒ 念出**为什么没点**，这一条仍由 agent 发起 |
| 🔴 防撞硬闸（不靠文案自觉） | `gateway.kickoff_collision_problems()` | 判在 `invoke()` **消费批准之前**（与认回执同一位置）：牌子上是**别人的活 pid** ⇒ 拒、0 消耗、适配器没被调。牌子上是自己 / 进程已退出 / 牌子超龄 ⇒ 放行——**补跑失败的段必须是合法出口**。带 `job_id`（捞回）与无合同认领的腿（h3/motion-rpa）不在射程 |
| 牌子寿命 | `SUBMIT_PID_MAX_AGE = 6h` | 这张牌**没人摘**（submit 没有 `compiled_report` 那样的回调）。pid 会被系统复用 ⇒ 必须有年龄上限，否则一条躺了半月的旧文件把这条任务永久锁死（闸把合格对象判红是最坏形状） |
| 🔴 判活唯一源 | `gateway.pid_alive(pid)` | ⚠️ Windows 上 `os.kill(pid,0)` 对【已退出、但父进程还握着句柄】的子进程**照样返回成功**，而起 runner 的 8100 正是那个父进程 ⇒ 拿它判活 = "那台早跑完了却读成还在跑"。真判据 = `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` + `GetExitCodeProcess` 比对 `STILL_ACTIVE(259)`。`model_admin._proc_alive`（含老的 `.compile.pid` 那条腿）现在也读它，两边不再各算一份 |

不变的那半：**建条子仍只有 agent 能发起**，人点批准窗仍是唯一人证；盯终态、失败补跑、交付贴图、范围变了重新报价，仍归 agent。
回归：`scripts/check-form-wake.py` **K11–K16**（Popen 打桩验命令形状、五道护栏逐条、牌子双向、`wakeText` 分流值表）
+ `scripts/check-spend-receipt.py` **B9–B10 / C3 / F 组**（活牌用真起的子进程、死牌=把同一个进程杀掉，
同一张文件前后改判 ⇒ 钉的是"进程在不在"，不是"文件在不在"；含"摘掉死牌跳过那句 ⇒ 活牌也放行"的变异对照）。

#### 为什么 DSH 内核那张卡可以信（探针结论，2026-10-08，dsh-desktop 0.9.2 / 内核包 0.1.5-rc.2）

- `approval/decided` **只有 `ApprovalService` 自己会写**（`dsh-user-approval/lib/index.js:142-145`），值来自 waterfall 应答者；
  UI 侧那个 promise **只有 `ApprovalPanel` 的 onClick 能 resolve**（`dsh-client-ui-approval/lib/client.js:92-96`）。
- 把 PTC 目录（agent 能调的服务清单，`dsh-tool-cordis/lib/index.js`）翻了一遍：**没有任何"往会话塞事件"的方法**
  （`publish` 只属于 inspector 遥测），也**没有"注册 approval 应答者"给 agent 用的口**。
- **没有应答者时内核 fail-closed**（`dsh-user-approval/lib/index.js:179` 默认回 `unavailable`）⇒ 插件没装/没起来
  只会"签不出"，不会退化成"没人也放行" —— 这正是系统弹窗的死因。
- ⚠️ 卡上的正文 `reason` 是**调用方传进去的字符串**（也就是我传）⇒ 报价不能靠 agent 手写，
  必须让卡显示 8100 侧签发的 pending 内容 + 码，人核对"码一致"再点；插件核销时回交 `pending_id + code + session`。

诚实边界（别 over-claim）：`code`+`consumed` 这条兜底路径**不是人证**（agent 技术上也能跑），
它的价值是"码不在它看得见的输出里"；真正的人证是【那张卡出现在人眼前 + 人点了】。
终局形态 = 只剩插件那条点击路径，兜底留给插件没装的场合。
现存证据：生产 `grants` 里那 7 张 `attest=None` 的旧条子（10-06/10-07 签的）就是"旧版可被单方面签出"的实物，
需要的话按 id `revoke`（`approvals` 会直接标出"无人证字段·旧条子"）。

⚠️ **一次真实失误要记在这里**：探测 `agnes-image/submit`（当时盘上已有别人签的 `image.generate/default` 条子）⇒ 闸放行 ⇒ **真出了一张图**（`outputs/agnes_image/agnes_image_2274f8b7/result.png`）。
处置：额度已按 1 段/4 分**退回那张条子**并在 `notes` 里写明原因；教训是**别拿真花钱的端点做"闸活没活"的探测**，要探就挑没有匹配条子的路由（video.generate 那几条当时无条子 ⇒ 423 且不花钱）。

🔴 **2026-10-08 全量审计（后台逐 handler 追到真客户端，规模核对）**：8090 那 111 个 POST 里**只有 5 个调 `_spend_or_423`**，
`backend/services/` 与 `pipeline/` 两个目录 `grep spend|approval` **零命中** ⇒ 拦与不拦完全取决于路由头那一行代码。
第③类（既不经 `gateway.invoke`、也没问 `/api/spend/guard`）清点出来的面比下面这份旧清单大得多，按代价排：
① **weeksir.com Playwright RPA 出片家族**（按条真扣、最贵、一条 POST 就触发）：`one-shot/generate`、`step5/run`、`workflow/run`、
   `replica/run-phased|approve|retry|redo-shot`、`structural-replica/run-phased|approve|resume`、`tvc/run|approve|retry`、`toddler-chef/pipeline`；
② **RunningHub 按 GPU 秒**：`face-swap`、`video-person-scene`、`super-clone-wan3`、`hot-explosion-wan3`、`replica-wan3-pipeline`、
   `motion-transfer`、`h3-director`、`detail-image`、`womenswear-studio(/step5-rpa)`、`smart-fission`、`one-shot/submit`、`video-translate`(音频) + 8093 `/api/generate` + 8091 的 Step2/3 生图腿；
③ **zexapi**（8765 `camera`/`frame_review`，那把 key 已知欠费 —— 历史上"没花出去"只是运气）；
④ **image2 / agnes 直连生图生视频**：`agnes-flash`、`agnes-25`、`h3-9grid/generate-images`、`womenswear flat-lay|generate`、`replica-motion`。
⚠️ 另外两条**结构性矛盾**要拍板才能修：
(a) `check_spend_approval` 按 session 绑条，但 8090 `_spend_or_423` 与 hypit bridge **都不传 session** ⇒ 按纪律签的会话条
    在这些闸口永远消费不掉，实际在逼人签"通用条"（谁都能花），把 10-06 事故修出来的隔离又退回去；
(b) `_spend_or_423` 一次只消耗 1 段、按调用次数扣，**不反映一条流水线真实段数**（一次 run 出 N 段）。
(c) **全链路没有任何熔断/日预算**：只有单次余额检查 + 文本 token 护栏 + 单张条子的段数/分数上限。
     grep circuit/breaker/daily/max_credits/throttle 在 gateway/8090/8091/8093/8765 全零命中。

🔴 **已知缺口（下次要补，别当成已完工）**：8090 共 111 个 POST 口，**接上闸的只有这 5 个**（逐个读 handler 确认它们真花钱）。
扫出但**排除了**的：`one-shot-replica/submit`（Step3 才花钱，本体只做 MiMo+LLM 反推）、`replica-motion/resume`（恢复逻辑，命中字样在字段注释里）。
**剩下的没接**：`super-clone-wan3/submit`、`hot-explosion-wan3/submit`、`video-person-scene/submit`、`face-swap/submit`、`tvc/run`、`step5/run`、`detail-image/run`、
`womenswear*`、`toutiao/run`、`smart-fission/*`、`publish*` 等 30+ 条**转调 `pipeline/*`/其它 service 出片**的口——
这些要沿调用链追到真客户端才能定性，**没追就没拦**，也不按名字猜着拦（会误伤纯本地操作，还给人假安全感）。
补法二选一：接进 `gateway.invoke`（首选，与积分/审计同源）或在入口调 `_spend_or_423`。

## 动作迁移腿：批准条子在【干活那一端】消费（2026-10-06 · 回归 `scripts/check-motion-rpa-lane.py` 54 条 + 7 种变异对照）

`video.generate/motion-rpa`（skill=`motion-transfer`，引擎=本机 8091 `D:\nzdh`）是第一条**花钱动作不发生在网关进程里**的腿：
第五步由 8091 自己开 Playwright、登录 RunningHub 账号池、操作公共工作流 `2106377827540103169`、等节点 285 出片，
网关只是转调它。于是出现一个既有形状回答不了的问题：**条子在哪消费**？

| 方案 | 结局 |
|---|---|
| 网关扣一次 + 8091 再扣一次 | 同一次生成吃两段批准 ⇒ 用户要批两遍 = **自造的假账**，否 |
| 只在网关扣，8091 不查 | 人在工作台手点 / 别的脚本转调 8091 = **完全绕过批准**（历史上"账本外的上游调用"就是这么来的），2026-10-06 否 ⇒ **2026-10-10 改判为选定方案**，见下 |
| 8091 只"查"不"扣" | 一张活条子可被无限次手点消费：拦得住第一次拦不住第二次，否 |
| ✅ **条子在源头消费一次**（2026-10-06 选定）：网关对这条 purpose 豁免（`SPEND_APPROVAL_AT_SOURCE`），8091 在**建任务/改状态之前**经 8100 的 `POST /api/spend/guard` 消费 | 一次生成一次扣，两端都拦得住，且没有第二份价目 |

🔴 **2026-10-10 修订（用户裁定，形状变了）**：动作迁移的五步流水线已剥离进网关进程（`backend/services/motion_rpa`，
`SPEND_APPROVAL_AT_SOURCE` 回到空元组），**条子回到 `invoke()` 里消费一次**；同时用户把 8091 工作台那一端的
批准闸**整个拆了**（三口不再调判定口，helper 与 `config.py` 的 `SPEND_*` 三行删除，备份 `D:\nzdh\_bak-20261010\`）。
理由 = **8091 是独立于 DSH 的产品，那一下是人坐在工作台前自己点的**，再弹一张"去别处点头"的窗是把同一个人动作拆成两步。
所以当年那行"否"现在成立的**前提变了**：不是"批准不需要"，而是**点的人已经在场**。
⚠️ 换来的代价要写清（不是已完工）：任何能 POST `127.0.0.1:8091` 的本地进程都能不经批准烧 RunningHub 的真钱
（60 分/段仍是占位）；8091 只绑环回，但"只有本机点得动"≠"只有人在点"。**这条豁免不给 agent 用** ——
agent 要跑动作迁移只许 `gateway call video.generate/motion-rpa`，那条轨的条子照旧在网关消费。

三个必须一起成立的约束（都由闸钉着，不是散文）：

1. **两端各管各的**（2026-10-10 改写）：F 组现在同时钉两半 —— 
   **8091 那一端**：判定 helper 在 `womenswear/router.py` 源码里零出现（F4），并有反向对照证明"三口都还在、
   不是没读到文件"（F4c）、配置无残留（F4d）；**网关这一端**：`video.generate` 仍 `needs_spend_approval`，
   且 `invoke()` 真的调了判定口（F4b）。只钉一半就漏"取消一路扩到网关"或"8091 自己漂回去"。
   `invoke()` 里仍写 `needs_spend_approval(...) and not approval_consumed_at_source(...)`（**不是整块跳过闸**）⇒
   往 `SPEND_APPROVAL_AT_SOURCE` 加豁免依旧必须在那一端配真实调用，F3 钉"豁免=必须是已接的腿"。
2. **判定口只消耗、不创建**：8100 上 `approvals`/`pending` 只读，`approve`/`deny` 只消费**已有** `pending_id`，
   `guard` 留给跨进程的腿（现在没住户）；**没有任何"创建批准"的 HTTP 口**（那等于让机器自己替用户点头，F9 钉 `create_spend_pending` 在 model_admin 里零调用）；
   局域网里的机器不该能消耗本机用户签的批准 ⇒ 这几个口**只听环回**（F8/F8b/F8c 钉"环回判据只有一份"）。
3. **价格单一源**：预计扣分由 guard 端点用 `gateway.meter()` 自己算，调用方自带 credits 一律不理
   （传 `credits=0` 就等于放行，这个口子必须堵）；价目只在 `model_prices.json` + `PRICE_ROUTES`，工作台侧不出现硬编码分数。

其它落码事实：段价 **60 分/段 = 占位·未实测**（上游按 GPU 秒扣；`meter()` 给这条 purpose 开了"按时段不按秒"的例外，
`duration` 只用于展示）；`/run` 自己会一路跑到 Step5 ⇒ 适配器**绝不补发** `step5-rpa`（D1b + M3 变异）；
`job_id` 只捞不重投（D6）；产物落盘做字节校验，4 字节的"200 错误页"判失败（D9）；
提交后立即向交付台账记一条"欠一条成片"，没会话 id 不记（E1/E2），记账炸了不影响本次调用（E3）；
右栏第 7 档两格 = **8091 自己截的 RPA 实时画面** + 工作室页 `?session=`（`runninghub.cn` 回
`X-Frame-Options: SAMEORIGIN`，嵌进来只会是空白；而"让它能嵌"= 开一个转发登录态的反向代理 = 把账号凭据经本机端口递出去 ⇒ 不做。
这句取舍写在页面文案里，由 H2/H3 钉住，防后人图省事改成代理）；8091 已从 `0.0.0.0` 收回 `127.0.0.1`（`config.py` 默认值 + `.env` 那行都改了）。

## 结构性复刻的编码崩溃与"状态以盘为准"（2026-10-06 · 回归 `scripts/check-8090-log-encoding.py` 23 条 + 5 种变异对照）

事故：`/api/structural-replica/run-phased` 的 Phase1（MiMo 解析 + GPT 迁移分镜 + Image2 故事板）**已经跑完并落盘**
（`outputs/sr_<ts>/phase_state.json = after_step3`、海报 2.2MB），却被父进程一句
`print(f"[SR-{task_id}] {msg}", flush=True)` 打死 —— Windows 控制台是 GBK，子进程回传的日志里一个 `✓`(U+2713)
就抛 `UnicodeEncodeError`，被外层 `except` 接住 ⇒ **任务标成 failed**。表现是"任务失败: 'gbk' codec…"，
人看到的是一句英文编码错，其实是活干完了、只是没人知道。

三条规则落码（都由闸钉住，不是散文）：

1. **父进程回显子进程日志只有一个出口 `echo()`**：按输出流编码先试、失败再 `errors=replace`，
   且 `sys.stdout is None`（pythonw 无控制台）时静默丢掉。绝不 `sys.stdout.reconfigure`——那条路实测会造
   "零输出 + 退出码 0" 的静默假绿。同一失效面一共五处（SR 两处、one-shot、redo_shot、replica-motion），全收进 `echo()`。
   ⚠️ 注意：app.py 里那些 `sys.stdout = io.TextIOWrapper(utf-8)` 全在**生成的子进程脚本模板**内，
   保护的是子进程；父进程一直裸着——这就是为什么"子进程明明 UTF-8，父进程还是炸"。
2. **状态以盘上产物为准**：`_sr_state_from_disk(task)` 一份实现三处调用（内存纠正、`_sr_ensure_in_memory`、task_store 兜底）。
   `run_dir` 在崩的时候还没写回任务 ⇒ 从这条任务自己的日志里找回（子进程第一行就打"运行目录: …"）；
   `phase` 以子进程写的 `phase_state.json` 为准，不看入口那个 `phase_1`。
   🔴 只纠 `failed`，**不许纠 `RUNNING`**（盘上产物可能是上一轮的，或子进程真在跑）；纠完把那句
   "服务重启，任务标记为失败"一起清掉——留着它就是"待审 + 一条错误"自相矛盾。
3. **重启扫尾会自己造误标**：uvicorn 关停把 RUNNING 扫成 FAILED，所以"重启一次 ⇒ 多几条假失败"是必然的；
   靠 ② 才能把它们捞回来。实测两条被误标的 SR 任务在重启后都恢复到 `awaiting_review` + 海报可见。

🔴 顺手查出的**闸外花钱口**（这条 ADR 的「花钱批准闸」清点里补上）：
`/api/structural-replica/run-phased` 与 `/approve`（触发 Phase2 视频生成）**没有调 `_spend_or_423`**，
Phase1 的 MiMo/GPT/Image2 也不在网关账本上（`mimo_api_key` 从请求体明文传、还落进 `phase_state.json`）。
⇒ 这次任务能跑起来不是"闸漏了没拦住"，是**这条线整个在闸外**。要接进 `SPEND_APPROVAL_REQUIRED` 形状
（在两个入口调 `gateway.spend_guard`，或改成经 `gateway.invoke`）需要用户拍板——它会把手点工作台的路径也一起拦住。

## 看图看片的真实边界与抖动重试（2026-10-08 · 回归 `scripts/check-analyze-retry.py` 44 条）

**实测事实（别再当成"某某模型不能识别视频"）**：`video.analyze/default` = agnes-3.0-flash 吃 `video_url` 的 data URL，
上游回 `prompt_tokens_details.video_tokens` ⇒ **真的在按视频理解**，不是把视频塞成图片。
用户那个 8.25MB / h264 / 712×1290 / 15s 的 `.mov`，同一个文件、同一条路由：**3 成 2 败**
（败形两种：HTTP 200 空 content、HTTP 500 `AgnesAI_error / do_request_failed`）。

**根因不是能力，是"一次不中就判死"**：`analyze_media` 原来一次请求定成败 ⇒ DSH 里的 agent 照着"这个模型不支持视频"收工，
把上游故障误报成能力缺失（它甚至正确地说了"不是格式不支持"，然后停下来问人要哪条）。

**修法**：`_analyze_retryable()` 纯函数分类死因 + 循环最多 `ANALYZE_ATTEMPTS = 3` 次、退避 3s/6s，返回信封加 `attempts`。

- 🔴 为什么这里可以重发、而"结果未知不重发"那条禁令不管：`video.analyze` 是**只读**能力
 （不落产物、不占生成额度、失败即 0 token），重发最多是再问一遍；那条禁令管的是花钱出片的腿（提交成功但结果未知 ⇒ 重发=再买一条）。
- 4xx（参数 / 鉴权 / 合规 / 额度）**立刻不重试** —— 重发还是拒，别白烧 11MB 上传。
- 报错文案自己把结论说完：`…（已试 3/3 次，可判定为上游故障而非格式不支持）`，让 agent 拿到判定而不是拿着一句"空内容"去猜。

**成本口径（价目表那个"6 分"是低估）**：视频理解按 token 结算，`video_tokens` 占大头 ⇒
实测 5s 小片 8 分、8.25MB/15s 的 mov **37 分/次**（预估都是 6）。报价与护栏别拿 `estimated` 当上限。

**生效条件**：8100 是常驻进程，改 `gateway.py` 必须重启才换代码。本次走
`python scripts/check-svc-8100.py --selfheal`（先 `hypit_bridge.py preflight` 看到 `safe_to_restart:true`、0 在途），
19 秒自愈、27 条断言全绿、单监听者。判"跑的是哪版"的指纹 = `/api/model/call` 返回里的 `result.attempts`（旧版没这个字段）。

## xjai.beauty WAN 3.0 腿：`video.generate/wan3` 与 `/wan3-prime`（2026-10-06 接入 · 2026-10-08 用户点名移除、连适配器与回归闸一并下线，本节只留决策史料）

**契约**（用户 2026-10-06 给的文档 + 当天只读探测核实）：
提交 `POST {base}/v1/videos/generations` → `{id,status:"processing"}`；轮询 `GET {base}/v1/videos/{id}`
→ `processing | success(result.url) | failed(error)`；取片 `GET {base}/v1/videos/{id}/content`。
参数边界：`duration` 1–30 整数秒、`resolution` `480P|720P|1080P`（默认 1080P）、`ratio`
`16:9|4:3|1:1|3:4|9:16`、`audio` 默认 false（要声音必须显式 true）、`media[]` 只吃**公网 URL**。

- 🔴 **和 vinted 那条 Seedance 腿最大的差别：`/content` 也要 Bearer。** vinted 的 `signed_url` 是免鉴权签名链，
  这条不是。漏了头就是 401，而 401 极易被误诊成"key 坏了"（记忆里那类误判）。闸用 D3/D3b 钉住三条请求都带鉴权头，
  并用 M1（把下载头删掉）证明这条断言真会红。
- **两个 model = 两个价的上游档** ⇒ 两条 purpose（`wan3` / `wan3-prime`），各读各的模型名字段
  （`XJAI_VIDEO_MODEL` / `XJAI_PRIME_MODEL`）。合并或读错字段 = 写 prime 却跑标准档，是自造假账（M6 钉这条）。
- **只读探测（0 消耗）读到的事实**：`GET /v1/models` 200，两条 model 自报 `price=18` / `price=20`，
  `resolutions=[480P,720P,1080P]`、`default_resolution=1080P`、参考料上限 images≤10 / videos≤5 / audios≤5 / 合计≤20、
  支持模式 `text|image|first_last_frame|reference`；`GET /v1/videos/{不存在的 id}` 回 404 `{"error":"任务不存在"}`
  （错误体是**字符串**，不是文档里那种 `{error:{message}}` ⇒ 适配器按两种形状都读）。
  🔴 **`price` 那个数没有单位字段**（不是 currency，也没说按秒还是按条）⇒ 只当观测记下，**没拿它当价**，
  内置价仍是照同族 wan3 腿借的占位量级 50/60（`model_prices.json` 可覆盖，改价不动代码）。跑通第一条后按上游实扣校准。
- **参考料上限在本地拦**（`_xjai_payload`），数字来自上面那次探测，不是我拍的（C18/C18b/C19 + M10）。
- **失败形状照旧**：查询断线只退避重问**同一个** task、连续 6 次才判死并留 `job_id`（绝不重发提交 = 双花，M2）；
  提交回 4xx 立刻停（4xx 是"只有你能修的事"，退避重问=白等）；`/content` 下到 <1KB 判失败且不在盘上留假 mp4（M8）。
- **花钱批准闸照常生效**：`video.generate` 在 `SPEND_APPROVAL_REQUIRED` 里，而这两条**不在**
  `SPEND_APPROVAL_AT_SOURCE` 豁免表 ⇒ 没条子就回 `approval_required`、不建预扣、不发 HTTP（E1/E4/E5 双向对照，M7 证明塞进豁免表会红）。
- **管理台（8100）**：面板以 `ROUTES` 为单一源，所以两条腿自动出现；探活走 `GET /models` ⇒ 实测 `reachable`（150ms 上下）。
  ⚠️ 常驻 8100 进程在 `.env` 改动后仍用旧值，本次已按看门狗重启（新 PID + `key_present:true` 复验）。
- **顺带揪出一个既有缺陷**：`_probe_reachable()` 的"网络类异常"出口引用了只在 `HTTPError` 分支里赋值的 `ms`
  ⇒ 任何上游 DNS 失败/超时/拒连都会 `NameError` 把探活炸掉，面板显示成"这条没结果"而不是"探不到"。
  已改为该出口自己计时（A7/A7b + M9 反向对照）。这条 bug 与 xjai 无关，是所有非文本腿共用的探活口。

**用法**（要出片必须先报价、拿到同意、由人在终端签条子）：
```
python backend/gateway.py resolve video.generate wan3          # 看路由/模型/单价
python backend/gateway.py approve video.generate wan3 --count 1 --credits <分上限> \
       --account <成员> --session <会话id> --scope "<用户批准的那条方案>"
python backend/gateway.py call video.generate wan3 --account <成员> --json \
       '{"prompt":"...","duration":8,"resolution":"720P","ratio":"9:16","image_urls":["https://.../a.jpg"],"output_path":"D:\\...\\out.mp4"}'
```

**已知缺口（别当已完工）**：① 没有任何 skill/右栏台绑到这两条腿（只是网关里能调）；② 从没实跑过一条 ⇒ 价、
时长上限、`first_last_frame`/`reference` 模式都只有文档与探测形状，没有实测；③ 上游自报 price 的单位没确认；
④ 顺带发现 `("video.generate","wan3-dashscope")` 那条（未适配的 TODO）模型名字段 `DASHSCOPE_WAN3_MODEL`
   → **2026-10-09 这条 lane 已按用户指令删除**（不是修好）：`client` 一直是 `TODO(无现成 client…)`、`fn="-"`，
   而 `model` 还写着 `wan3.0-video-prime`（与 yz-prime 那条同名）⇒ 它在 caps/resolve 清单里像个可选项，选了必失败。
   墓碑留在 `model_prices.json.disabled`，判据见 `scripts/check-wan3-rh-cut.py` K10（含"塞回 ROUTES 拒因必须变"的变异对照）。
默认值就是 `wan3.0-video-prime` —— 和 xjai 的模型名撞字符串，真接 dashscope 前要确认那是不是它的合法模型 ID。

## WAN 3.0 的第三条入口：youzan666.vip（`video.generate/wan3-youzan` 与 `/wan3-youzan-prime` · 2026-10-07 接入 · 2026-10-08 用户点名移除，含 `wan3-smart` 共三条连适配器与回归闸一并下线，本节只留决策史料）

同一模型族（wan3.0-video / -prime）现在有三条中转入口：xjai 两条 + 这家两条。**故意不合并**，因为两家的参数边界不一样，
共用常量 = 改一家把另一家带下水（M10 就是钉这条）。

**契约**（用户 2026-10-07 给的文档 + 当天【不带 key】的只读路径探测）：
提交 `POST {base}/v1/videos/generations` → 202 `{id,status:"processing",concurrency}`；
轮询 `GET {base}/v1/videos/{id}` → `processing | success(result.url, result.expiresAt) | failed(error)`；
下载 = 直接 GET `result.url`（**网关自己的持久直链，不是 xjai 那种要带 Bearer 的 `/content` 端点**）。
探测记录：以上路径不带 key 全回 401「缺少API Key」，故意打不存在的路径回 404 `API_NOT_FOUND` ⇒ 路径形状为真；
**但接入当天没有 key，一次真提交都没跑过**（面板上这两条 `key_present=false` 就是这个状态）。

## 🔴 但文档的模型名是错的（拿到 key 后 0 消耗实测）
`GET /v1/models` 返回 11 个 id，**里面没有 `wan3.0-video`、也没有 `wan3.0-video-prime`**，
wan3.0 家族实际是 `wan3.0-smart` 与 `wan3.0-video-prime-1080p`（另有 `wan2.7-i2v/r2v`、`happyhorse-1.x i2v/r2v/video-edit`、`qwen-image-3.0/-pro`）。
用一条**必然在预扣之前就被拒**的提交做判别（素材指 `https://127.0.0.1/…` ⇒ 文档说回 `EXTERNAL_MEDIA_URL_BLOCKED`）：

| 提交的 model | 回包 | 含义 |
|---|---|---|
| `wan3.0-video`（文档名） | **400** 「模型 wan3.0-video 不存在或未启用」 | 卡在模型名校验 ⇒ 这把 key 没开 |
| `wan3.0-video-prime`（文档名） | **400** 同上 | 同上 |
| `wan3.0-video-prime-1080p` | **422** `EXTERNAL_MEDIA_URL_BLOCKED` | 模型名已过、卡在素材 |
| `wan3.0-smart` | **422** 同上 | 同上 |
| `wan3.0-not-a-model`（对照） | **400** | 判别法本身成立 |

⇒ **判"模型名通不通"不必花钱**：400=卡在模型名、422=过了模型名卡在素材。
处置：`video.generate/wan3-youzan`（文档标准名）**在 `model_prices.json` 里屏蔽并写明原因**（恢复=删掉那条 disabled）；
`wan3-youzan-prime` 的模型字段改指实测的 `wan3.0-video-prime-1080p`；新增 **`video.generate/wan3-smart`**（`wan3.0-smart`）。
prime 的"只 30 秒 + 1080p"判定改成 `startswith("wan3.0-video-prime")`（否则带 `-1080p` 后缀的真 id 逃过校验，M15 钉这条）。

**智能档的形状**（用户 2026-10-07 给的说明：明确 30 秒 + 1080P 优先走 Prime，其余规格与自动时长走标准版，积分按 Smart 配置计费）：
- 我们只发 `model=wan3.0-smart`，**选路在上游做**；因此它**不受** prime 的 30 秒/1080p 限制（M17 钉住别把 prime 规则加到它身上）；
- 允许**不传 duration = 自动时长**：那种请求的 body 里干脆没有 `duration` 键，我们不替它填 5（M16 钉住——填了就等于抢上游的调度权、还让报价与实际片长不符）；
- 价 60 分/10 秒档 = **占位·无数据**（"按 Smart 配置计费"没有公开数），估价时 `duration` 缺省按 1 档算，报价必须写"无数据、以平台实扣为准"。

**与 xjai 那两条的四点不同（都各自有常量，不共用）**
| | xjai | youzan666 |
|---|---|---|
| prompt 上限 | 20000 | **19000** |
| resolution 写法 | `480P/720P/1080P` | **`480p/720p/1080p`**（文档示例口径） |
| media 类型 | image/video/audio | **多 `first_frame`/`last_frame`**（各 ≤1） |
| 下载 | `/content` 必须带 Bearer | **直链，默认不带 key** |
| prime 档 | 未声明限制 | **只支持 30 秒 + 1080p**（写别的时长/分辨率 ⇒ 大声拒，不静默改） |

**🔴 下载凭据纪律（这条是安全项，不是风格）**：`result.url` 是上游给的字符串，可能指向别的域。
把中转 key 顺手发给第三方就是凭据泄露 ⇒ `_yz_download` 的规则是：**首探一律不带 Authorization**；
只有回 401/403 **且主机仍是 base 那一台**才补一次 Bearer；重定向一离开本机域就剥掉头
（自己接管 `redirect_request`，因为 urllib 默认会把头带到跨域重定向）。C1-C4b 五条钉它，M1/M2/M3 三种改法全部咬红。

**重发提交只允许一种情形**：`429`（文档明说并发超限、带 concurrency、任务根本没建）⇒ 退避重发同一条，上限 `max_submit_retry=3`。
其余一律"只捞不重投"：查询断线退避重问同一个 task、6 次判死留 `job_id`；4xx（400/402 积分不足/403 无权限）当场停；
下载 <1KB 判失败不留假 mp4。（M4=不重试、M5=无限重试、M6=断线重发提交，三种坏法都会红。）

**价与闸**：50/60 分/10 秒档，⚠️**占位·无实测**（文档只说"视频按秒扣积分、失败自动退还"，没给每秒多少分）；
计价走默认**每 10 秒一档**（这家按秒扣 ⇒ 时长必须是乘数，A6/A6c 钉住别误学 RPA 腿的按次例外）；
批准条子在**网关这一端**消费，不进 `SPEND_APPROVAL_AT_SOURCE`（M13 钉住）。

**用法**（拿到 key 之后）：
```
# 1) key 只进 .env（源码里 YOUZAN_API_KEY 默认空串）：YOUZAN_API_KEY=...
python backend/gateway.py resolve video.generate wan3-youzan        # 看路由/模型/单价
python backend/gateway.py test video.generate wan3-youzan           # 只读探活，0 消耗
python backend/gateway.py approve video.generate wan3-youzan --count 1 --credits <分上限> \
       --account <成员> --session <会话id> --scope "<用户批准的那条方案>"
python backend/gateway.py call video.generate wan3-youzan --account <成员> --json \
  '{"prompt":"...","duration":6,"resolution":"720p","ratio":"9:16",\
    "image_urls":["https://公网/a.png"],"output_path":"C:\\dsh\\productions\\<task>\\outputs\\yz_01.mp4"}'
```

**已知缺口**：① 没 key ⇒ 提交/轮询/下载三段只有假传输层证据，`resolution` 大小写、`ratio` 枚举、
`first_frame/last_frame` 的真实行为、以及直链到底要不要鉴权，**全都没实测**；② 这家还有两条路径没接
（DashScope 风格 `/api/v1/*`、原生 `/api/generate-video`）与两类能力没接（`qwen-image-3.0/-pro` 生图、`/v1/chat/completions` 文本）——
按用户口径这次只接 wan3.0 视频；③ 素材是 URL 直传 ⇒ 本地图必须先有公网 https 地址（防盗链/签名过期会让任务失败）。

## H3 的第二条腿：浏览器 RPA（`video.generate/h3-rpa` · 本机 `C:\Minimax-H3` 的 8093 工作台 · 2026-10-06 接入 · 回归 `scripts/check-h3-rpa-lane.py` 61 条 + 12 种变异对照）

**为什么是两条腿**：`video.generate/h3` 走 RunningHub **OpenAPI**（我方 key + 工作流号 2095474072795865090），
这条走 **Playwright 浏览器 RPA**（复用 `runninghub_profile/` 登录态 → 打开工作流 `2106376910329704449`（带坏节点版）→
把参数注入画布节点 [12] `MiniMaxH3Director` → 点运行 → 抓 [7] `SaveVideo` 的预览 cos_url 下载）。
两者坏法完全不同（key 欠费/工作流改版 vs 登录态过期/串行锁被占），所以**故意不合并**——合并了就没人知道这次走的是哪条。
调 H3 时选渠道 = 选 purpose：`h3`（API）/ `h3-rpa`（浏览器）。

**网关侧形状**（`_h3_rpa_payload` + `_h3_rpa_run`，都照 8093 的真实代码抄，不照 README 猜）：
- `POST /api/generate` → `{taskId}`；轮询 `GET /api/status/<id>` → `status: running|done|error` + `output`（**8093 自己下载好的本地绝对路径**）。
- 🔴 **这条腿的成片不是网关下载的**：8093 内部 `download_video_with_audio()` 已经落盘，网关只做三件事——
  校验文件真在盘上、字节 ≥1KB、按调用方要的 `output_path` 归位。所以没有 `/content`、也没有 Bearer 那一类坑（那是 xjai 腿的）。
- **8093 的任务表只在内存里**（`TASKS: dict`）⇒ 进程一重启 taskId 就查不到（404）。
  适配器遇到 404 **不重提交**，而是先按 `output` 路径看盘、并在拒话里点名"按盘上 mp4 判，别重跑=双花"。
- `POST` 回 **409** = 浏览器 RPA 的全局串行锁被占 ⇒ 报"已有任务在跑"，不排队、不轮询、不重试。
- 🔴 **判失败之前先看盘**（AGENTS.md 那条规矩在这条腿上最要命）：H3 工作流末尾有**防翻录机关**
  （`VHS_VideoCombine: 'bool' object has no attribute 'cpu'`，设计内行为），8093 有时会把已经出片的任务标成 `error`
  ⇒ 适配器在 error 分支仍查一次盘，盘上有够大的 mp4 就判成功并写明原因（M2 证明这条会咬人）。
- 只捞不重投：给 `job_id` ⇒ 零 POST（D3/D10/D12）；查询断线退避重问同一个 id，6 次才判死且必带 `job_id`。

**参数边界（都有出处，不是拍的）**：`engine` plus/lite/ultra；`task_type` t2v/i2v/fl2v/r2v/v2v/rv2v（不给就由 8093 按素材推）；
参考图 ≤3 张且**只吃本地文件**（[12] 走上传，公网 URL 一律拒 —— 与 xjai 那条正好相反）；
`width/height` 要成对给、按这条工作流的对齐步长 `multiple=32` 收（非 32 倍数没验证过，先拒）。

**9:16 支持吗：支持。** 三条证据：
1. 8093 的回收结构里 [12].output 自己写着 `aspectRatio: "9:16 (竖屏)"` → `width 480 / height 864`（`multiple: 32`、`megapixels: 0.4`）；
2. 同一条 H3 导演台的 **API 腿在 9:16 上真出过片**：`productions/2026-10-02-富铁软糖剧情带货短剧/05_shots/videos/h3-director/SEG-1-01-h3-dir.mp4` ffprobe = **576×1024**；
   同批 `SEG-1-02A-h3-v2.mp4` = 720×1280（也是 9:16）。
3. 这台工作bench自己的竖屏产物 768×1024（3:4）也实测过 ⇒ fixed 模式下写死宽高上游是照做的。
⇒ 网关把 `ratio:"9:16"` 映射成 **480×864**（上游那一档的原算值），也接受任何 32 倍数的自定义尺寸（576×1024 可以）。
✅ **2026-10-06 21:41→21:50 经网关实跑通第一条 9:16**（用户批准"试跑一条 9:16 的实测片验证链路"）：
`gateway.invoke("video.generate","h3-rpa")` → 条子 `ok_8d3fdfd4` → 提交 8093 → taskId `09348d2b24b9` →
轮询到 `done` → 落盘 `productions/2026-10-06-H3-RPA-9x16链路实测/outputs/h3-rpa-916-01.mp4`
= **480×864 / 6.583s / 158 帧 / 719,171 字节**（ffprobe 实测），信封 `charged=60 basis=flat balance_after=99620`，墙钟 **572.8s**。
画面人眼核过：米色风衣女性在落地窗前转身、夕阳逆光、竖构图 ⇒ 提示词与尺寸都真的被上游吃进去了。
⚠️ 左上角带 **"RunningHub AI生成"** 水印 ⇒ 商用要走 `video.edit/dewatermark` 那道腿，别直接投。

**这一跑顺带炸出两个网关级缺陷（都已修 + 反向对照，与 H3 无关，所有腿共用）**：
1. 🔴 `invoke()` 算批准段数那行是 `int(params.get("segments") or params.get("count") or 1)`，
   而按上游签名传 `segments=[{...}]`（列表）的调用方**一进来就 TypeError 炸在闸里**——
   炸点在预扣之前 ⇒ 0 消耗、条子 used 0/1、上游一次都没被碰（方向是安全的，但闸自己成了故障点）。
   现在列表取 `len`、认不出的按 1 段（真正的上限是 `credits`）。回归 `check-spend-approval.py` G25a-G25c。
2. `/api/film/file`（交付三件套里"可点直链"那条）拿 `.mp4` 去查键为 `mp4` 的类型表 ⇒ **永远查不中**，
   所有直链都回 `application/octet-stream` ⇒ 浏览器把视频当附件下载、图片不内联。已 `lstrip(".")`。
   回归 `check-assets-range.py` 9/9b/9f（反向对照：把 lstrip 去掉 ⇒ 三条同时红）。

**价与闸**：`PRICE_ROUTES` 记 60 分/次（照 H3 API 腿借的量级；⚠️**价仍是占位·未与 RunningHub 账单对过**，
链路通了不等于知道对方实扣多少人民币），`meter()` 与 motion-rpa 一样**按次不按秒**（A9/A9b/A9c）。
批准条子在**网关这一端**消费：这条路的必经入口就是 `gateway call`，
所以闸放这里就是真闸；它**不在** `SPEND_APPROVAL_AT_SOURCE` 豁免表里（M8 证明塞进去会红）。

**用法**：
```
python backend/gateway.py resolve video.generate h3-rpa
python backend/gateway.py approve video.generate h3-rpa --count 1 --credits 60 \
       --account <成员> --session <会话id> --scope "<用户批准的那条方案>"
python backend/gateway.py call video.generate h3-rpa --account <成员> --json \
  '{"prompt":"...","segments":[{"prompt":"...","durationSec":6}],"ratio":"9:16","engine":"plus",\
    "ref_images":["C:\\path\\角色1.png"],"output_path":"C:\\dsh\\productions\\<task>\\outputs\\h3_01.mp4"}'
```

**已知缺口（别当已完工）**：① 8093 现在绑 **0.0.0.0**（局域网能直接点它花钱）、没有会话归属、也没有源侧批准闸——
和当初 8091 的三条一样，要收回 127.0.0.1 得改 `C:\Minimax-H3\app.py`（**不是 git 仓库**，改前先备份）；
② `C:\Minimax-H3\config.py` 里 RunningHub key 是**明文默认值**（建议挪进 `.env` 并轮换）；
③ 只实测过 **t2v 单段**（480×864）⇒ 参考图/r2v/首尾帧/多段一次跑 与 "非 32 倍数上游到底怎么处理" 都还没跑过；
   价 60 分/次仍**没和 RunningHub 账单对过**（只知道墙钟 572.8s/条，不知道对方实扣多少钱）；
④ 右栏没有 H3 的工作台页签（8093 自带网页，要挂进 DSH 得再走一次 WORKBENCHES/presence 那套装配）。

## 推理脑注册表（2026-10-01 全中转实测 · `text.complete` 现有 8 条腿，2026-10-04 下线 2 条后）
模型名集中在 `config.py` 的「文本推理脑注册表」字段（.env 可覆盖），不散在代码里；单价按实测 token 分档（≈100 tok/分）。

| purpose | provider | 模型 | 实测 | 分/次 | 备注 |
|---|---|---|---|---|---|
| `agnes`(默认) | agnes 国内站 | agnes-3.0-flash | live-ok 0.9s | 1 | 兼作 `video.analyze` 的眼睛；文案短板见 §5.6 |
| `mimo` | mimo | **mimo-v2.6-flash** | live-ok 2026-10-09（89–91 tok·1 分） | 1 | 2026-10-09 从 mimo-v2.5 升上来；模型名走 `config.TEXT_MODEL_MIMO` |
| `mimo_pro` | mimo | **mimo-v2.6-pro** | live-ok 2.8s | 3 | 同题样本里最"人味"，推荐当剧本/口播脑 |
| `gemini38` | zexapi | **gemini-3.8-flash** | live-ok 3.9s | 6 | token 大户（~900） |
| `claude48` | zexapi | **claude-opus-4-8** | live-ok 3.2s | 2 | 便宜且干净 |
| `deepseekv4` | zexapi | deepseek-v4-pro | live-ok 2.2s | 7 | **思考型：max_tokens <2000 会"空正文"**（HTTP 200 但 content 空） |
| `qwen38max` | dashscope | qwen3.8-max | — | 2 | 🔒 已屏蔽：**阿里百炼账户欠费 `Arrearage`**（3.7-max/3.6-plus 同错；早间能出词只是免费额度未用尽）→ 充值后一键恢复。⚠️ 接通时 chat 必须走 `DASHSCOPE_CHAT_BASE_URL=…/compatible-mode/v1`，不是 `api/v1` |
| `qwen38-ninfer` | qn-ninfer | **qwen3.8-27b** | live-ok 2026-10-13（19 tok / 1 分，`basis=usage` 真扣，账本带 `hold_id`） | 1 | 2026-10-13 用户给端点+key 点名接入。**qwen3.8-27b 现在只有这一条腿**（阿里百炼同模型名的腿同日删除，见下"2026-10-13"节）。`GET /models`（0 消耗）该 key 只有 `qwen3.8-27b` 一个模型，`max_model_len=131072`，`owned_by=ninfer` ⇒ 别往上发别的模型名。base_url `QN_NINFER_BASE_URL`、key 只从 `.env` 的 `QN_NINFER_API_KEY` 读（源码无明文）；走通用 `openai_compat`/`_chat_compat`，只挂 `text.complete` 一条能力 |
| `gpt61` | nocannobb | gpt-6.1-sol | 可用但**抖**（连打 3 次全 OK，2.5/8.9/12.3s；偶发 502/503） | 1 | 必须带浏览器 UA；⚠️ 见下面"固定分不准" |
| `gpt56` | nocannobb | gpt-5.6-sol | ⚠️ **3/3 回 401 `INVALID_API_KEY`**（2026-10-04） | 1 | 用独立字段 `GPT56_API_KEY`（**不覆盖** `GPT55_API_KEY`）。模型名在册（`GET /v1/models` 用旧 key 列出 `gpt-5.5 / 5.6-sol / 5.6-terra / 6-astra / 6-sol / 6.1-sol`），旧 key 实发 `gpt-5.6-sol` **能出词**（106 tok，84 是 reasoning）⇒ 端点/调用形/模型名全对，只差一把有效 key |

### 2026-10-04 下线两条 + 一条硬规矩（用户定：gpt 只认 hub.nocannobb）
连测 **3 轮**取稳定结论后删除，不是"某一次没通就删"：
- ~~`text.complete/zexapi`~~（gemini-3.5-flash）— 3/3 `disabled`，503 无渠道。
- ~~`text.complete/qwen38max`~~（qwen3.8-max）— 3/3 `disabled`，阿里百炼整把 key 欠费 `Arrearage`。
- 同时清掉 `_MODEL_FALLBACK` 里三个**死兜底**：`gpt` / `openai` / `manxue` —— 没有任何路由再用这些 provider，留着等于宣称"换条腿能兜底"，实际一调就 `RouteError`。
- `model_prices.json` 里这两条的 `prices`/`disabled` 键一并清（27→25 价、disabled 归零）。
- **规矩**：`gpt-*` 系模型**只允许走 `hub.nocannobb.com`**（`GPT55_BASE_URL`）。别的中转（zexapi/manxue/dashscope）报的 gpt 名字一律不进路由表——同名不同源，扣费和内容都对不上。
- 修好后要加回来：dashscope 充值 ⇒ 恢复 `qwen38max`（注意 chat 必须走 `…/compatible-mode/v1`）；zexapi 有渠道 ⇒ 恢复 `zexapi`。加回来前先跑 3 轮探活，别凭"以前能通"。

**踩过的坑（写死在这里防止复发）**：`hub.nocannobb.com` 的 **403 error code: 1010 是 Cloudflare 拦"无 User-Agent 的脚本请求"**，不是 key 坏；网关所有 HTTP 请求现已统一带浏览器 UA。同理：探活路径 `/models` vs `/v1/models` 各家不同、route 无 model 时不能发 null、reasoning 模型小预算会返回空正文——**自家 probe 的现象 ≠ 上游事实，下"坏了"的结论前必须直连复现**。
**不可用（非代码问题，要你去处理）**：manxue 的 claude 系 410 渠道已撤、manxue gpt-5.5 403 余额不足（该 key 没钱）；**dashscope 这把 key 现在整片被账户欠费拦住**——`qwen3.8-max/3.7-max/3.6-plus` 与 MiniMax 全系都回 **400 `type/code:"Arrearage"`**（早间 qwen 能出词=免费额度尚未用尽；**别把"当时能通"当"这个模型没问题"**），另有 deepseek-v3.2/r1 = 403 免费额度用尽、`ZHIPU/GLM-5.2` = 400 产品未开通（后台点一下）；agnes 2.5-pro 三档 403 额度不足（要充值）。
**⚠️ 固定单价不准（直接影响积分体系设计）**：同一句极简提问，`gpt-6.1-sol` 的 `total_tokens` 从早间 28 跳到 4396、`gpt-5.6-sol` 22366 —— reasoning 模型消耗随问题与思考长度**波动 100 倍以上**。所以 `model_prices.json` 的分数**只能当粗略预估**（报价/预算护栏用），真实扣费必须**按上游返回的 usage 结算**（= P2 的 weeksir.com 账本；`commit(hold_id, actual)` 早已留了 `actual` 位，现在只是 `charged = estimated`）。
**面板状态语义（别把抖动当故障）**：`live-ok/reachable`=通；`live-fail/auth-fail/config-error`=**要你处理**（4xx＝欠费/未开通/模型名或端点错）；`upstream-5xx/slow`=**上游抖动**（5xx 或 20s 墙钟超时，不是我们 key/配置坏，别乱屏蔽）；`disabled`=已屏蔽（不计故障）；`no-probe/local`=探不了、要实跑。
**⚠️ MiniMax H3 语言模型的两条接入前提**（充值后也别当 OpenAI 兼容腿接）：① 它在 `compatible-mode/v1` 回 **404 "Unsupported model for OpenAI compatibility mode"**，只能走 dashscope **原生协议** `POST /api/v1/services/aigc/text-generation/generation`（body `{model, input:{messages}, parameters:{result_format:"message"}}`）→ 网关要单独写一个 `dashscope_native` 适配器，不能复用 `_chat_compat`；② "H3" 这个名字在别的中转全是**视频**模型（manxue `1ren-minimax-h3/-v2`=我们 `video.generate/h3` 在用；zexapi 另有 `minimax-h3-{480p,768p,1080p,max-*}` 六档未接），别把两者混成一个能力。
**默认脑切换**：面板「默认路由」一键改（`model_prices.json.defaults`），不改代码；正式定稿前建议跑 3 品类 ×2 次同题评测（打分维度=§5.5 合规 + §5.6 反幼稚腔）。

### 2026-10-13 qwen3.8-27b：接 ninfer、删 dashscope（用户裁定）

- **接进来的**：`text.complete/qwen38-ninfer`（provider `qn-ninfer`，`QN_NINFER_BASE_URL=https://api.qn-einnp.com/ninfer/v1`，key 只住 `.env` 的 `QN_NINFER_API_KEY`）。
  真跑一条 = 19 tok / **1 分** / `basis=usage` / 账本带 `hold_id`；`GET /models`（0 消耗）这把 key 底下**只有 `qwen3.8-27b`**（`max_model_len=131072`）。
- **删掉的**：`text.complete/qwen38`（provider `dashscope`，复用 `DASHSCOPE_CHAT_BASE_URL`/`DASHSCOPE_API_KEY`）。删的理由不是"模型坏"，是**那条腿今天实调仍回 `400 Arrearage`**（阿里百炼整把 key 欠费，失败回滚 0 分）⇒ 留在注册表里等于宣称"qwen 出问题时还有第二条腿"，而它从没通过。
  连带清掉 `_MODEL_FALLBACK["dashscope"]` —— 删完这条路由后**没有任何路由再用 `dashscope` 这个 provider**，那个兜底就成了 §508 说过的"死兜底"同一种形状。
- **没删的**：`config.DASHSCOPE_*`（账户级凭证，别的原生协议腿与 `.env` 还在用）与 `TEXT_MODEL_QWEN38_27B`（模型名只留一份，现在唯一的读者是 ninfer 那条）。
- **充值后要加回来**：照 `qwen38-ninfer` 那一行改 `base_url/api_key` 为 `DASHSCOPE_CHAT_BASE_URL`/`DASHSCOPE_API_KEY` 即可（chat 必须走 `…/compatible-mode/v1`），加回来前先跑 3 轮探活，别凭"以前能通"。
- **生效范围**：**8100 常驻进程要重启**才看不见被删的这条（`/api/model/caps`·`board` 是从进程内的 `ROUTES` 出的）；重启前照旧先 `hypit_bridge.py preflight` 看 `safe_to_restart`。
- ⚠️ 顺带发现的一条**旧漂移**（本次没动）：`scratch/video-ui/**/zexp.py:142` 的模型名前缀映射里 `qwen → qwen38max`，而 `qwen38max` 早在 2026-10-04 就删了 ⇒ 若把 omni 的 `script_model` 设成 qwen 系，走网关会直接 `RouteError`。要么改成指向 `qwen38-ninfer`，要么把这条前缀删掉——那是 omni 的选脑逻辑，单独拍。

### 2026-10-09 mimo 升到 2.6（网关侧三条 lane，回归 `scripts/check-mimo-lane-version.py` 13 条）

- 🔴 **先问上游再改名**：拿网关 lane 的凭据 GET `/v1/models`（0 消耗）实测这个 key 只有
  `mimo-v2.5 / -asr / -pro / -tts×3` + **`mimo-v2.6-flash` / `mimo-v2.6-pro`** ⇒ **裸 `mimo-v2.6` 不存在**，
  写上去会被上游判 unknown-model（400）。"升级到 2.6" 的正确落点是 flash 与 pro 两档。
- 改了三处 + 一个新增字段：`config.TEXT_MODEL_MIMO = "mimo-v2.6-flash"`（新），
  `ROUTES[("text.complete","mimo")]`、`ROUTES[("video.analyze","mimo")]` 的 `fields.model` 从 `None` 绑到该字段，
  `_MODEL_FALLBACK["mimo"]` 同步改 2.6-flash（否则 .env 缺值会静默掉回 2.5 = 假升级）。
- 实测：文本 `1 分/89 tok` 出词正常；看图 `video.analyze/mimo` 4 分/407 tok 正确描述了一帧画面。8100 已重启（52 秒自愈、27 条断言全绿），
  实况 `/api/model/resolve` 三条都回 2.6。
- **没动的部分（要动得单独拍）**：网关外还有 **19 个文件**写着 `"mimo-v2.5"` 字面量 —— 都是账本外的直连腿与临时脚本
  （`backend/services/{copy_generator:150, detail_image_agent:217, douyin_mall_clothing:354, prompt_reverser:36, tvc_agent:279+286}`、
  `pipeline/step1_mimo_parse.py`、`structural_replica_workflow.py`、`app.py`、`outputs/_*.py`、`_probe_*.py`）。
  其中 `prompt_reverser` / `step1_mimo_parse` 属 8090 那条"只到提示词"的边界文件，动它们=扩范围，不在这次里。
- **↑ 上面这条"没动的部分"已于 2026-10-13 作废**（用户当日拍板"mimo 2.5 要升 2.6"）：那 19 个文件里的**活代码全部升档完成**，
  含本条点名"不动"的 `prompt_reverser.py` 与 `step1_mimo_parse.py` —— 这句就是留这条记录的意义：**它覆盖了一个已登记的决定**，
  不是顺手改。改法：有模块级 `from config import settings` 的 10 个文件改成读 `settings.TEXT_MODEL_MIMO`（模型名只留一份），
  没有的 4 个（`pipeline/step1_mimo_parse.py` 的函数默认值、`douyin_mall_clothing.py`、`_probe_wan3_reverse.py`、`test_mimo_video.py`）
  写死 `"mimo-v2.6-flash"`；omni(8765) 的 `store.py`/`zexp.py`/`config.json` 三处 `mimo-v2.5-pro` → `mimo-v2.6-pro`；
  `tvc_agent.py` 里那句描述用的模型的散文一起改。**没改的（故意的）**：`video_runs/*/run.json` 的 `script_source`、
  `outputs/*.json`、三套闸里的 `"storyboard": "mimo-v2.5@..."` 夹具串、ADR/PRD 的历史节 —— 那些是"当时真用了什么"的台账，改=伪造历史。
- 2026-10-13 实测（换档后）：① 网关 `text.complete/mimo`(2.6-flash) 正常出词；② **视频腿的预算是真门槛** ——
  同一条 30 秒片子 `max_tokens=600` ⇒ `上游返回空内容`（3/3 次，回滚 0 扣），`4000` ⇒ 一次成、**实扣 111 分 / 11124 token**
  （预估只报 6 分 ⇒ 思考型模型的 `estimated` 只能当护栏，对账必须看 `charged`+`tokens`，这条纪律再次被验证）；
  ③ 但**图片分类腿给 256 不坏**（`douyin_mall_clothing` 那处，实测答 `'否'`、reasoning 仅 38 token）⇒ 没有证据就不去动别人的预算。
- 生效范围：**8090 必须重启**（那 8 条腿的字面量与函数默认值是 import 时定死的）；**8765** 的 `script_model` 走 `store.load_config()`
  现读 `config.json` ⇒ 新请求已经吃到 2.6-pro，只有 `zexp.py` 的函数默认参数要重启才换；**8100** 重启才对常驻进程看见新加的
  `text.complete/qwen38-ninfer`。重启前一律先 `hypit_bridge.py preflight`。回归：`scripts/check-mimo-lane-version.py` 新增 V 组
  （V1 活代码不许再出现 2.5 / V2 写死的字面量必须等于 config 的值 ⇒ config 再升档这里立刻红 / V3 覆盖对照 / V4-V5 双向与三向对照），
  现 **24/24**。

### 2026-10-13 新渠道：满血API（`manxueapi.com`）三条视频腿

- **接了什么**（唯一源 `gateway.ROUTES`，适配器 `gateway._manxue_video_run`，参数表 `gateway._MANXUE_VIDEO`）：

| purpose | 模型 | 上游价（已核实单位） | 参数依据 | 时长/分辨率 | 素材上限 |
|---|---|---|---|---|---|
| `video.generate/h3-manxue` | `1ren-minimax-h3` | 0.002 积分/runtime_second | ✅ 线上参数卡 | 5~15 秒 / 720p,480p | 9 图 / 3 视频 / 3 音频；**modes 仅 referenceImage** |
| `video.generate/seedance-2.0-manxue` | `1ren-dance-2-0` | 1.8 积分/call（一条封顶 30 秒） | ⚠️ **无线上卡**，用文档"客户端内置兜底" | 先按 4~15 秒封顶 | 9/3/3 |
| `video.generate/seedance-2.5-manxue` | `1ren-dance-2.5-2` | 6 积分/call（同上） | ✅ 线上参数卡（本日刚恢复并列出） | 离散 `5/10/15/30` / 只有 720p | **9 图 / 0 视频 / 0 音频** |

- 🔴 **上游计价单位核实了**：`GET /api/pricing` 的 `model_price` 单位就是**积分**，与对方 2026-10-13 公告逐条对上（2-0=1.8、2.5-2=6，都是"30 秒一条"）。一家三种单位混着用：`call`（整条）/ `second`（每秒）/ `runtime_second`（每运行秒）。⇒ `PRICE_ROUTES` 里这三条按【上游积分 1:1】登记（1/2/6），**绝对值别当钱**（这台机器的"1 元=几分"仍未定），但**相对比例是真的**（2.5 是 2.0 的 3.3 倍、H3 ≈ 免费）——比三条都填 60 诚实。
- **按次的那两条进了 `meter()` 的按次例外**（与 `wan3-prime`/两条 RPA 同档）：不随秒数乘。⚠️ 没进例外的 `h3-manxue` 走"每 10 秒一档"，而**时长分支不乘条数**（既有形状，别的时长型 lane 也一样）⇒ 批量要按条数自己算段数，批准那侧的段数由 `--count-from` 从盘上取，不受这个影响。
- **key**：用的是 `.env` 里那把 `MANXUE_H3_API_KEY`（sha8 `bf52820e`，51 位）——`GET /v1/models` 现读 **61 条**模型（文档只写 49），三条腿的模型名都在里面。用户同日另给的一把 `sk-` key（sha8 `70d6440e`，同 51 位、与盘上那把只有前 7 字符 `sk-wWGr` 相同）在**同一主机、同一请求形**下回 **401 `{"type":"new_api_error","message":"Invalid token"}`**，而盘上那把同刻回 200 ⇒ **没有覆盖盘上那把**（拿一把不认的 key 换掉能用的 key = 自己把渠道关掉）。要用它得先在对方控制台确认已激活/绑定。
- **契约**（照对方 Apifox 文档 + 0 消耗只读探测核对）：`POST /v1/videos`（扁平 body：`model/prompt/ratio/duration/resolution/images/videos/audios`）→ 任务号 `id`（兼容 `task_id`）；`GET /v1/videos/{task_id}`（建议 5s）`queued/in_progress → completed/failed`，失败读 `error.message`；取直链按文档给的七级优先级（`video.url` > `video_url` > `output.url`/`output[0].url` > `result.url` > `result_url` > `metadata.url` > 顶层 `url`）；兜底 `GET /v1/videos/{id}/content`（可匿名、透传 Range）。图片族走 `POST /v1/media/generate` + `GET /v1/media/status`（`data.is_final`）**未接**。
- 🔴 **三条红线**：① **没有重试环**（与 `wan3-prime` 相反）——这条渠道没有"失败不计次"的实测证据，一次调用只提交一次，失败由人决定；② 素材只吃公网 http(s) 外链，**本地路径在本地就拒**（0 分 0 请求，拒因指向 `gateway.py upload`）；③ 自带 `job_id` = 只捞不重投。任务号成功失败都落 `<成片目录>/manxue_job.json`（带 `route`、重投留 `prev_task_ids`）。
- 🔴 **新腿把上游返回体带进错误消息**（`提交回 HTTP 5xx，上游原文：…`）。原因是同日 wan3-prime 复盘发现 prime 那条只在 4xx 分支印 body、**5xx 只留三个字符**，导致每次事故都无法定性。prime 那侧**本次没改**（改动要配闸与变异对照，单独拍）。
- **未接的口子（要接说一句，别当成已覆盖）**：
  - `c-dance2.5-pro-1080` —— 唯一"能挂参考视频（10 条）+ 1080P"的 2.5（30 图/10 视频/10 音频、1.38 积分/秒）。`seedance-2.5-manxue` 的参考视频上限是 **0**，超级复刻那种"原片当参考视频"的形状**在这条上物理上做不到**，这是选腿时最该知道的一条。
  - `1ren-dance-2-0` 的 30 秒：公告说 30 秒 1.8 积分，但线上卡没有这条、文档兜底表只到 15 秒 ⇒ **先按 15 秒封顶**，等 `/api/model-capabilities` 列出它再放宽（不拿真提交去试探对方的档）。
  - `guanfang-seedance-2 / -2-5 / -fast / -mini`：价目 `price=0`、`price_unit=None`、无参数卡 ⇒ 接进来就是"面板上看着能选、一提交就撞"的假选项（同日 `wan3-dashscope` 那种形状）。
  - **文本腿一条没接**（用户裁定）。这家也卖 `gpt-6.1-sol`、`gpt-5.6-sol`、`deepseek-v4-pro`、`claude-opus-4-6/4-7`，与 nocannobb / zexapi 现有腿**同名不同源**；2026-10-04 那条"gpt-* 只认 `hub.nocannobb`"的规矩原样有效。
- **生效**：常驻 **8100 要重启**才看得见这三条（`/api/model/caps`·`board` 读进程内 `ROUTES`）；DSH 会话每次 `python … gateway.py call` 起新进程，立刻吃到。重启前先 `hypit_bridge.py preflight`。
- **回归**：本日跑过 `check-engine-single-source.py` 35/0、`check-runtime-bindings.py` 22/0、`check-spend-approval.py` 88/0（新腿默认要批、没进免批名单，所以 G1c 那条默认值断言照旧绿）。端到端用**肯定没条子的账户**探过：`approval_required`、`est=6`、`charged=0`、pending holds 21→21 不变、盘上零文件。⚠️ **这三条还没有一条真跑过** ⇒ 报价必须带"占位·未实测"，第一条要用来校真实扣费。


### omni 的首要推理脑 = `mimo-v2.5-pro`（2026-10-01 定，C `72c9096`）

`store.DEFAULT_CONFIG.script_model` + `config.json` 都指到它（agnes-3.0-flash 退居**看图 `video.analyze` + 兜底**）。接它的三条硬知识，以后动 video-ui 文本咽喉都要记得：
1. **video-ui 自己也要按模型名分流**：`zexp._chat_direct` 原来只认 `agnes*`、其余全打 zexapi ⇒ 直接把 `script_model` 设成 mimo 会把 mimo 模型名发给**没有这个模型的 zexapi**。现补 `_mimo_conf()` / `chat_backend("mimo")` / mimo 分支；走网关时按前缀映射 purpose（`mimo→mimo`、`gpt-→gpt61`、`gemini-3.7/3.8→gemini38`、`claude-opus-4-8→claude48`、`deepseek→deepseekv4`、`qwen→qwen38max`、未知→`zexapi`＝被屏蔽并明确拒绝），并把**模型名透传**进 `params.model`（不再依赖网关兜底模型名）。
2. **思考型模型要给足预算**：`_budget()` 对 `mimo/gpt-/deepseek/qwen/gemini-3.7+` 把 `max_tokens` 兜到 **6000**（实测 900~2500 会「HTTP 200 但正文空」）。
3. **`.env` 要 base+key 齐**：`.env` 原本只有 `MIMO_API_KEY`（base 靠 config.py 默认），而 video-ui 要求两者齐 ⇒ 已把 `MIMO_BASE_URL` 经 8100 管理接口写入 `.env`（不在 video-ui 复制常量，保持单一真相源）。

⚠️ 两点提醒：mimo 爱在正文后附"设计思路"，剧本/运镜的 prompt 要锁死"只出正文/严格 JSON"；生效需**重启 8765**（用户自己终端起的进程，别擅自 kill）。

## 超级复刻换脑：magpie 本地池三条 free 腿进网关（2026-10-20 落码 · 回归 `scripts/check-engine-single-source.py` L 组 8 条）

**为什么换**：小米 `token-plan-cn.xiaomimimo.com` 这把 key 整把 429 `quota exhausted`（2026-10-20 现读实测
`text.complete/mimo_pro` ⇒ 429、0 分）。超级复刻的**三条推理腿（分镜 / 编译 / 看图）全吃这一把** ⇒ 一起断。
用户裁定：接法照 8099（`D:\prompt_reverse_8099\backend\services\gpt_client.py:40-95`），但**模型要注册进网关、由网关调用**。

**同一天实测的死因分类**（别一起归因成"模型都不能用"）：`agnes` ✅ 通（1 分）；`grok46` ✅ 通但一条"只回一个字"
烧 2934 tok = 29 分（思考没关）；`deepseekv4` 🔴 403 欠费（zexapi ¥-0.001）；`gpt56`/`gpt-compile` 是我们自己
10-13 屏蔽的（零调用点），不是坏。

| capability/purpose | provider | 模型名唯一源 | 单价 | 状态（2026-10-20 真跑） |
|---|---|---|---|---|
| `text.complete/magpie` | magpie | **入口别名**（= 表头那条腿，现 deepseek） | 1 | ✅ 463 tok / 实扣 5 分，`lane` 记真答的那条腿 |
| `text.complete/magpie_deepseek` | magpie | `config.MAGPIE_DEEPSEEK_MODEL` | 1 | ✅ **推理主腿**（2026-10-20 用户点名） |
| `text.complete/magpie_mimo` | magpie | `…_MIMO_MODEL` | 1 | ✅ 3/3 合格，但墙钟 69.8/200.8/75.5s（deepseek 的 3–12 倍）且 🔴 图N 引用漂 |
| `text.complete/magpie_glm` | magpie | `…_GLM_MODEL` | 1 | 🔴 **0/3**：三次都把 8192 预算整段烧在 reasoning ⇒ 正文 0 字、`finish=length` ⇒ **已从两张腿表整条摘掉**，路由与价目留着（显式点名才走） |
| `video.analyze/magpie` | magpie | 同上入口 | 6 | ✅ **看图腿用它**（降采样后公平实测）：deepseek 20.9s / pt=1505、mimo 37.1s；比 agnes 的 68.7–90.3s 快 3 倍且**不脑补品类** |
| `video.analyze/default` | agnes | `AGNES_TEXT_MODEL` | 6 | ✅ **视频理解腿用它**：`video_tokens=240` 真吃视频（今天唯一能吃视频的）；看图这一格实测会把布料特写直接判成"灰色加绒卫衣" ⇒ 不用于看图 |

**看图腿的传输缩放（2026-10-20 同日补）**：`analyze_media` 原来把【原图字节】直接 base64 发出去
（一张客户产品图 7,608,526 字节 ≈ 10MB base64）⇒ magpie 侧观测到 220–380s、两图那次 420s 超时。
现在按 `ANALYZE_IMAGE_MAX_EDGE=1560` 缩放（口径照 8099 实测：8 张 19MB→6.6MB 后识别与融合都够用），
返回体带 `bytes_before/bytes_after/resized` 三格。
⚠️ **这条改动的价值不是"让 agnes 变快"** —— 公平对照（同题同图只变缩放）实测 agnes 68.7s→90.3s、
`image_tokens` 两边都是 1014 ⇒ 墙钟主导项是【生成】不是【上传】；我一度把"84s→6s"记成降采样的功劳，
那是我自己换了题和输出长度，对照不同条件 = 假结论（已在 `check-analyze-retry.py` T 组注释里写死这段，防止下次又照抄）。
真正的收益是：**magpie 从"会超时"变成可用**（220–420s → 20.9–37.1s），以及每条素材少占 10MB 内存。
🔴 视频一律不缩（那是分镜的像素事实）。回归 `check-analyze-retry.py` T1–T7（含"摘掉 resized 判断 ⇒ T4 立刻红"的反向对照）。

**五条规则只写在两处**：**腿序在 config**（`MAGPIE_TEXT_LANES="magpie_deepseek:3,magpie_mimo:2,magpie_glm:2"`、
`MAGPIE_VISION_LANES="magpie_deepseek:3,magpie_mimo:2"` ⇒ 换主腿只改这一行，不动码、不动路由），
执行规则在网关（`MAGPIE_MIN_BUDGET` / `_magpie_chat`），调用方只读结论：
1. **带图腿故意没有 glm**（8099 实测带 3 图 `finish=length` 拿不到合法 JSON ⇒ "没核验"比"核验失败"坏得多）；
   点名的腿不在表里 ⇒ 返回空表并出声；腿名拼错 ⇒ **整张表作废、一条都不发**（半张表会悄悄跑出一条没人指定过的腿）。
2. **预算下限 `MAGPIE_MIN_BUDGET=1800`**：实测 `max_tokens=60` 时 60 个 token 整段进 reasoning、正文 `None`
   （HTTP 200、usage 正常，看起来像"这模型不会说话"）。⇒ 夹上去，不是拒。
3. **它的 429 = 抖动不是额度**（实测隔 6–20 秒重打就通）⇒ 同一条腿退避重试，**不换腿**；非 429 才换下一条。
4. 第 2 次起带上一对关思考旋钮 `reasoning_effort:low` + `thinking:{type:disabled}`（实测 deepseek 只认前者、
   glm 只认后者、mimo 两个都不理 ⇒ 必须成对送）。
5. 单价**不登记 0**：`basis=usage` 按能力定，写 0 会让"预计总扣分"那一格说 0 分而实扣几百 ⇒ 报价谎报。
   口径照 agnes（同为免费上游）：登记 1/6 分、真扣按 token。

**🔴 边界（这条最容易日后写错）**：magpie 的桥**丢视频**。三条 free 腿带视频时 `prompt_tokens` 一字不涨
（291→291 / 481→480 / 345→345）、都答"没收到"，**尽管模型卡写着 `modalities.input` 含 video** —— 卡不算数，
桥的 chat part 白名单（`text/image_url/file`）才算数。⇒ 唯一一条必须吃视频的腿（Step1 分镜）**不在 magpie 范围内**，
由 `config.REPLICA_WAN3_STORYBOARD_ENGINE` 指到网关的 `video.analyze` 默认路由；那道 provider 守卫（默认路由被指成
magpie 就拒）由 L6 钉着，是真函数变异出来的，不是注释。

**强度边界（别当成"全接进账本了"）**：Step1 发请求的仍是 `pipeline/step1_mimo_parse.py`（BOUNDARY.md 整文件保护、
只调不改）⇒ 从网关读的是 base/key/model 三元组，**预扣/结算/台账并不会发生**，所以 `_mimo_cell()` 与 `_llm_stamp`
对它如实写"账本外·直连厂商 / ledger=off"，即使值来自网关路由。只有编译与看图两条真进了 `gateway.invoke`。

**两条容易踩的判据坑（都在这天真踩过）**：
① 测编译器腿**不许按"合法 JSON"判** —— Step1.5 的输出契约是【单条纯文本 Wan3.0 prompt】
   （`super_clone_wan3.py:1122` 那句"把 storyboard JSON 编译为单条 Wan3.0 prompt"，回来只剥 ``` 围栏），
   拿 JSON 当判据会把三条腿全判成 0/3（判据算错会把合格对象判红，和当年 PNG 忘滤波字节同一类）。
② 日志前缀不许挂厂商标：原来 `[Step1·MiMo]`、`[Step1.5·Grok编译器]` 在跑 agnes/deepseek 时照样打旧名字，
   agent 念给客户就是假话（同"报价里的上游与单价只许现读"一条规矩）⇒ 已改成中性的 `[Step1·分镜腿]` /
   `[Step1.5·编译器]`，要真名就现读 `_storyboard_lane()` 往正文里带。

**待办**：① ~~8090/8100 常驻进程仍持旧代码~~ 已做：8100 于 2026-10-20 重启两次核过
（PID 18320→20508→47892→23216，每次都读 `/api/model/resolve` 的真值当指纹，端口在不在不等于换没换人）；
**8090 仍是旧代码**（那条常驻口有自己的流水线，本条线不经过它，暂不动）。
② agnes 顶分镜**从没在 Step1 那份 400 行结构化合同上实测过**，且 Step1 现在还在用 MiMo 时代的
`fps=4.0 / media_resolution=max`（8099 用 agnes 是 `2.0 / default`）⇒ 下一条真单第一次跑要盯 `frames/` 逐秒核，
并考虑把这两个参数照 8099 降一档。



## 2026-10-04 新增三条非文本腿（都进同一预扣/结算/审计，不再各自硬编码厂商）

| capability/purpose | provider | 入口 | 计费 | 状态 |
|---|---|---|---|---|
| `audio.generate/seed-audio` | bytedance-audio | `POST /api/v3/tts/create`（**X-Api-Key 头**，不是 Bearer；401/403 才换 Bearer 重试一次） | 分/**千字**（不是分/条） | 已接，**未实跑** |
| `image.generate/doubao` | doubao-web | 子进程 `video-ui/doubao.py gen`（Playwright 驱动本机 Chrome） | 记 0（不花钱，烧登录态） | 已接·**有头才行**（无头被风控） |
| `image.generate/gflow` | gflow-cli | `gflow image t2i\|i2i --json` | 记 0（烧的是 Flow 配额，不是我方现金） | 已接·**上游 bot check 拦着**（`PUBLIC_ERROR_UNUSUAL_ACTIVITY`，未扣费） |
| `video.edit/dewatermark` | runninghub | AI App `2012077432525824001`：上传→`/openapi/v2/run/ai-app/<id>`→`/openapi/v2/query`→落盘 | 分/**10 秒档**（占位 20 分/档） | 已接，**未实跑** |

**三条硬规矩（都是踩出来的）：**
1. 🔴 **占位价必须标出来，且要能校准**。`video.edit/dewatermark` 的 20 分/档是照同一家 `image.edit/faceswap` 借的**占位数**（源码里写明"没有实测价"）——RunningHub 按 GPU 秒真扣钱，而"1 元=几分"未定。为此给 `runninghub_client.query_task()` 补了 **`usage` 透传**（`taskCostTime`/`consumeMoney`/`consumeCoins`；之前归一化时被丢掉，于是所有按 GPU 秒的腿都拿不到实测数）。跑通第一条后按这个数改 `model_prices.json`，不用动代码。
2. **网页/CLI 型腿没有密钥可验**，`test_route` 只能验"登录态文件在不在"（doubao）或 `auth status`（gflow），**不许把这种"reachable"当"真能出图"**——那两条都要实跑才算通。
3. **按条计费的腿一律强制 `output_path`**：上游链接 24 小时失效（RunningHub）或成功但拿不到东西（音频二进制体），不落盘 = 白花钱；参数校验全部排在**建预扣之前**，拒了就不该出现 hold 记录。

**闸**（默认 0 网络 0 花费，假传输层跑真代码）：`scripts/check-audio-generate.py` 23 条 · `scripts/check-web-image-lanes.py` 44 条 · `scripts/check-dewatermark-lane.py` 47 条。
🔴 假客户端**只许换传输层**（换 `httpx.AsyncClient`），把被测函数整块替掉 = 全绿也漏网（agnes 那条就这么骗过我一次）。

## 积分系统（账本适配层·对齐 PRD §6.x）
**原则：单一账本在 weeksir.com，网关只做"调用前预扣 / 成功提交 / 失败回滚"的适配，绝不在 DSH 另建用户/积分表。**（PRD:276/283）
接口（1.0 本地 sqlite 桩实现、3.0 换 weeksir.com API，**接口不变**）：
```
precheck(account)            -> {balance, ok}          # 余额够不够
preauth(account, cap, pur, p)-> {hold_id, cost}        # 按条预扣（PRD:346）
commit(hold_id, actual)      -> {charged, balance_after}
rollback(hold_id)            -> {refunded}             # 失败回滚该条
```
- **计价外置（2026-10-01 已落地）**：价目与"每能力默认路由"不再硬在代码里，而是 `backend/model_prices.json`：
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
- **✅ usage 回写扣费已落地（2026-10-01，D `838c870`）**：文本/看图**按上游返回的 `usage.total_tokens` 结算**，费率在 `model_prices.json` 的 `token_rate`（默认 **10 分/1k token**，即 1 分≈100 token）；生图/生视频仍按条计（`basis=flat`）。`settle_credits()` 规则：`charged = max(1, round(tokens/1000*rate))`，**护栏 = 单条封顶 `max(预估×8, 200)`**（防上游跑飞把客户积分一次掏空，超出标 `basis=usage-capped`）。~~`invoke` 用 `commit(hold_id, charged, tokens)` 落库~~，信封 `credits` 增加 `tokens/basis`，面板两张表显示 **预估 vs 实扣 vs token**，流水写 `settle … 实扣N/预估M tok=X`。
  - 🔴 **那句 commit 落库的承诺到 2026-10-13 才真正成立**（用户点名排查"实扣"时对账抓到）：`Ledger.commit()` 函数一直是好的，但 `invoke()` 的成功分支**从来没调它** —— 全仓 `ledger().commit(` **零调用点**（`preauth` 1 处、`rollback` 3 处）。⇒ 10-01 到 10-13 之间每次"按 usage 结算"只存在于**返回信封的数字**里：账本只按【预估】扣过、`holds` 停在 pending、面板那列"预估 vs 实扣"实际是同一个预估值的两遍。
  - 现场证据（不靠叙述）：`holds` 表**最后一笔 `done` 停在 2026-10-11 00:02**，之后所有成功调用全是 pending。最贵的一点不是少扣分，是**【成功未销账】与【进程被杀留下的泄漏预扣】在表里同形、分不开**——我上午就是这样把 wan3-prime 那笔 60 分判成"泄漏"的。
  - 修法（已落码）：`invoke()` 成功分支补 `done = ledger().commit(hold["hold_id"], charged, tokens or None)`，结果摊进信封 `credits.settled`；销不掉时**出声**（打原因 + `settled:false`），但业务成败不被结算失败改写。回归 = `scripts/check-spend-approval.py` 新增 **S 组 6 条**：S1 实扣与余额净减、S2 hold 落 `done` 且记下 tokens、S3 `settled` 可见、S4 不按 token 的腿也翻 done、**S5 反向对照（把 commit 摘掉必须抓红）**、S6 invoke 体内 `commit` 与 `rollback` 成对的顺序锚点。现 **94 条全绿**。
  - 生产实测（修复后）：CLI 一条 `text.complete/qwen38-ninfer` ⇒ `settled=True`、hold `status=done` `amount=1` `tokens=19`；**8100 自己的 `/api/model/call`** 同样落 done（新监听者 PID 49252@19:11:22，验证走的是常驻进程而不是新起进程）。
  - ⚠️ **历史债没修、也没法准确补**：修复前 `holds.tokens` 从没写入 ⇒ 当时该扣多少**已不可算**。`pending_holds` 里现在混着【成功未销账】与【真泄漏】两类，**绝不许批量回收**：拿 `holds-rollback` 退一笔"成功但没销账"的预扣 = 把真做掉的生成白送出去（那是另一种错账）。要么按 est 认定 done（承认少扣、只清状态不动余额），要么留着当历史债 —— 这条要单独拍。
  - 为什么必须这么做（实测）：同一句极简提问 `gpt-6.1-sol` 消耗 28 → 4396 token、`gpt-5.6-sol` 22366 ⇒ **整条单价只能当报价/护栏，不能当账单**。真跑对照：agnes 113 tok→扣 1（预估 1）、mimo-v2.5-pro 563 tok→**扣 6（预估只有 1）**。
  - 仍留的口子：`meter()` 的 `estimated` 还是拍脑袋的整条价（3.0 应改成 `token 预算 × 费率 + 上游单价表`）；上游不返 usage 的适配器会走 `flat`。
- **1.0 本机**：`ledger:"STUB"` 本地记账即可（PRD:458 本机阶段不接网站），但**接口就是上面四个**，3.0 平移到 weeksir.com 不改调用方。
- **与生成关口咬合**：omni/hypit 的"build 前预算批准"直接读 `resolve` 的 `credits.estimated`（[[omni-delivery-contract]] §9 预算护栏、[[super-clone-hypit-pipeline]] pricing 闸）。
- **上游真实价格·第一个实测硬证据（2026-10-02）**：`video.generate/omni-t2v`（zexapi `omni_flash-10s`）提交被拒，回 `insufficient_user_quota：用户剩余额度 ¥0.045460，需要预扣费额度 ¥0.722500` ⇒ **一次 omni 视频段上游要价 ≈ ¥0.72**，而我们内部记 **40 分** ⇒ 隐含锚 **1 元 ≈ 55 分**（此前"1 元=几分"完全未定，这是第一个来自上游账单口径的数据点，不是公开牌价）。⚠️ 只此一条、单次采样，**别当定价表用**；要定价得按各路由各时长多测几点。同时说明：**"扣积分"目前仍是本地记账，真金白银在上游账户里**（zexapi 没钱就出片失败，与客户余额无关）。

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
| video.analyze | default/mimo | agnes/mimo | 6 | ✅ 预估 6，**实测 8–37 分/次**（视频按 token 结算，见「看图看片的真实边界」节）|
| image.generate | default/generic | agnes/image2 | 4 | ✅default / ⏳generic |
| image.generate | detail | runninghub-ideogram | 8 | ⏳ |
| image.edit | faceswap | runninghub DreamID-V | 20 | ⏳ |
| video.generate | omni-t2v | omni(zexapi) | 40/10s | ✅ |
| video.generate | agnes-v | agnes-video | 30/10s | ⏳ |
| video.generate | motion（clone-r2v 与 wan3-dashscope 均已删除，见「假选项」注） | rh-motion | 50/10s | ⏳ |
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
python backend/gateway.py call <capability> <purpose> \
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
网关返 `result.path/url` → skill 写进 `productions/<task_id>/ASSETS.md`、更新 `LOOP.stage`、追加 `accounts/<acct>/SKILL_LOG.md`（框架已有这套）。产物本体留 backend/outputs，指针进 productions（同 [[omni-delivery-contract]] 档案层）。

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

## 素材上传口：把"拿公网 URL"收进网关（2026-10-09 落码 · 回归 `scripts/check-media-upload.py`）

**为什么要做**：一条会话为了"给满血复刻拿 6 个公网外链"卡住，把原因归成"RH token 失效"。实测三种死法都跟 key 好坏无关：

| 写法 | 真实返回 |
|---|---|
| `/openapi/v1/upload_file` | HTTP **200**，body 是 `{"code":404,"msg":"NOT_FOUND"}` —— 端点根本不存在，只看状态码会当成功 |
| host 换 `runninghub.cn` | `code=1 msg=API Key不存在` —— **key 按站点分**，WAN3 那把只在 `.ai` 有效 |
| 拿错 key（`PRIME_YZ_*` 是另一家） | 直接 error |

根因不是运气：**盘上 8 把 RH key、四条腿各用一套取法**（`wan3_client` 只认 WAN3；`runninghub_client` 是 ENTERPRISE→API_KEY→WAN22；`structural_replica_workflow` 用模块级 `H3_API_KEY`），"哪个域名 + 哪个端点 + 哪把 key"这件事**没有主人**。

**决策 = 网关的第二个入口，不塞进 `invoke()`**：

- `gateway.upload_media(file_path, provider="runninghub", key_hint="", account="local")` → `{ok, file_name, download_url, size, provider, key_hint, error}`。
  host / 端点 / key 优先级**只在这一个函数里**；`file_name` 优先返回（RH 的媒体节点要的是 `openapi/<hash>.png` 这种引用名，不是 `download_url`）。
- **0 计费、不建 hold、不进 `SPEND_APPROVAL_REQUIRED`**。理由：`invoke()` 的形状是"预扣→提交→结算"，而上传不花钱、没有 taskId、multipart 幂等 —— 为了收口 key 把扣费流水线拽进一个不计费的动作，是给未来埋坑（每次改结算都要多考虑一条腿）。
- **账还是要留**，但留在**自己的台账**（`GATEWAY_MEDIA_UPLOADS`，与批准条子同一套 JSON 文件形状），不往钱账本里塞 0 分流水：记 `ts/provider/key_hint/文件/大小/file_name/sha8(key)/ok/error`，**永不记 key 本体**。
  记账失败 ⇒ `ok:true` + `ledger_error`（登记炸了 ≠ 上传炸了，同 `check-motion-rpa-lane.py` E3 那条判据）。
- CLI：`python gateway.py upload <本地文件> [--provider runninghub] [--key-hint wan3] [--account <成员>]`。
- HTTP：`POST 127.0.0.1:8100/api/media/upload` —— **只听环回**（沿用 `model_admin` 已有的 `client_address` 判据），路径限工作区 `productions/` 内、文件名洗 ASCII 防穿越、有体积与张数上限，**响应里永不出现 key**。
- 两份重复实现（`wan3_client.upload_media`、`runninghub_client.upload_resource`）**这次不动它们的取 key 逻辑**，只由回归闸钉"三处的 host + 端点字面量必须一致"（漂移就判红）。
  两个理由：① **同一个客户端的上传与提交必须用同一把 key** —— RH 的 `fileName` 是账号内引用，上传走 A 把、提交走 B 把会直接"文件找不到"，把上传改成网关的默认档就等于埋这颗雷；② `wan3_client` 现在是 `override > RUNNINGHUB_WAN3_API_KEY`（取不到就 raise），网关的 `wan3` 档是 `WAN3 → ENTERPRISE → API_KEY` —— 收进来等于**悄悄放宽取 key 的顺序**，那是行为变更，要单独拍。
  各自的传输层（`requests` / `httpx` async）也保持不变：把 async 腿改成同步调用会阻塞事件循环，那是另一种自找的事故。

**强度到哪为止（别 over-claim）**：这一口收的是"端点/域名/key 的选择 + 留痕"，**不是鉴权**。8100 整体仍无鉴权 ⇒ 本机任何进程都能用它把文件传进我们的 RH 账号（不扣分，但占账号、素材进的是我们付费的存储）。发客户前必须和 `/api/hypit/cli` 那批一起做只读化 + origin 白名单 + 执行端点鉴权。
