# Hypit 工程化出片 · CLI 驱动契约（monitor-contract）

WeekSir 薄编排层驱动 **hypit 原生 CLI**（全局 `@hypit/hypit` v0.2.9），工程文件固定 `C:\dsh\hypit_runs`。
**生成腿走统一模型网关**：自写 Provider `@weeksir/provider-gateway` 把任务 POST 到 `http://localhost:8100/api/hypit`（`D:\video-skill\backend\hypit_bridge.py` → `gateway.invoke`），积分/审计与 omni、super-clone 同一张账。**但不 import D 的代码、不用 D 的控制台、不碰 :8090/:8000**——只认这一条 HTTP 契约。渲染/合成是 hypit 本地 provider（`media.local` / `hyperframes.local`，零密钥零花费）。命令形态均对 `hypit --help` 与实跑核对。

引擎三层（只有工作区在 C:\dsh）：
- **CLI 本体** = 全局 `AppData\Roaming\npm\node_modules\@hypit\hypit`（`hypit paths` 的 Distribution）
- **工作区/project state** = `C:\dsh\hypit_runs`（Project state `C:\dsh\hypit_runs\.hypit`，Profile `hypit.runtime.json`）
- **凭据** = hypit 的 **平台凭据存储**（Profile 里 `credentials.platform.use = @hypit/credential-store-platform`；`hypit.runtime.json` 只存 `{store, key}` 引用，**明文 key 不落任何文件**）。实测家目录与 `AppData\Local\Hypit` 都没有凭据文件；`hypit auth status <endpoint>` 只报 `Configured 1/1`，不打印值。

---

## §1 CLI 命令清单（`--workspace C:\dsh\hypit_runs` 贯穿）

| 阶段 | 命令 | 说明 |
| --- | --- | --- |
| 就绪 | `hypit doctor` | 诊断；缺凭据报 `RUNTIME_CREDENTIAL_MISSING` |
| 就绪 | `hypit runtime init/use/up/status/logs/down` | init 已做；up 起本地 Build Worker |
| 凭据 | `hypit auth login hypihub.default --runtime <profile>` | build 必需；logout 撤 |
| 校验 | `hypit check <source>` | 验一个 Author/Run source（不花钱） |
| 结构 | `hypit plan <run-source>` | 显示将要做的活，不执行（不花钱） |
| 成本 | `hypit pricing <run-source>` | 读所选 Provider 当前定价 → **关口A 用** |
| 生成 | `hypit build <run-source> --follow` | 提交 Build，花钱、可长达 1h |
| 进度 | `hypit status <build-id> [--watch]` / `builds` / `logs <build-id>` / `activity` | 看阶段/结果 |
| 取消 | `hypit cancel <build-id>` | 撤回活动 Build |
| 检视 | `hypit studio --run <build.svrun> [--port N]` | **右栏挂这个**；per-run 浏览器检视合成/Sources/Results，Ctrl+C 停 |
| 导出 | `hypit get <build-id> --output <name> --to <path>` | 导出一条 Output |
| 抽帧 | `hypit media frames/tile/probe/cut/boundaries`、`hypit capture screenshot` | 出本地图做 QA（配合 read_image） |
| 词汇 | `hypit vocabulary` | 可编排元素清单（authoring 时查） |

工程文件（`.svml` 脚本 / `.svs` source / `.svrun` run）由 **Agent 直接在 hypit_runs 里写**（authoring 语法见上游 skill `.dsh/skills/hypit/references/production/{script-syntax,source-syntax,authoring,runs}.md`）。

## §1.5 网关 Provider 接线（已装好，日常别重配）

| 件 | 位置 | 事实 |
| --- | --- | --- |
| Provider 包 | `C:\dsh\hypit_runs\packages\provider-gateway\`（**入库**）；`hypit_runs/package.json` 用 `file:packages/provider-gateway` 装进 `node_modules/@weeksir/`（npm 在 Windows 建符号链接，`npm install` 需在 hypit_runs 里跑） | capability `@hypit/seedance@1#seedance-2-mini` |
| Profile | `hypit_runs/hypit.runtime.json`：endpoint `weeksir.gateway`（`baseUrl http://localhost:8100/api/hypit`、`pollIntervalMs 5000`）+ binding `"@hypit/seedance@1#seedance-2-mini": "weeksir.gateway"` | 实测 `hypit plan` 输出 `→ weeksir.gateway (@weeksir/provider-gateway), bound in the Profile` |
| 服务端 | `D:\video-skill\backend\hypit_bridge.py` 挂在常驻 8100（`model_admin.py` 的 `/api/hypit/*`），转 `gateway.invoke` 走积分账 | 实测 `/api/hypit/rates?model=seedance-2-mini` 返回我们的价目文档 |
| 凭据 | `hypit auth login weeksir.gateway --from <文件>`（**用完立刻删该文件**；`.gitignore` 已加 `.secret*` 兜底）。查：`hypit auth status weeksir.gateway` | 实测 `Configured 1/1` |
| 鉴权 | `.env` 里 `MODEL_GATEWAY_TOKEN` 非空 ⇒ `/api/hypit/*` 必须带 `Authorization: Bearer <token>`；实测无 token=401、带 token=200 | token 值不进 git、不进对话 |

三条**硬约束**（踩过的坑，改 provider 前先看）：
1. `baseUrl` 只能是 `https://…` 或**主机名写作 `localhost`** 的 http——实测填 `127.0.0.1` 被 Profile 校验拒，所以一律 `http://localhost:8100`（`localhost` 解析到 127.0.0.1；IPv6 `::1` 不通）。
2. `pricing.kind="page"` 要求 **https** 页面，本地 http 过不了 ⇒ 不声明 `pricing`，改用 `readPricing()` 拉 `/api/hypit/rates`（实测 `hypit pricing` 读到的就是我们的文档）。
3. 网关侧支持上限 = **720p、单段 ≤10s、只吃图参考**；超界或传视频/音频参考一律**明确拒绝**（`check_support`），不静默丢弃。

## §2 驱动时序

```
0 hypit doctor → 就绪？否→ auth login / runtime up
  （网关侧：8100 常驻 + MODEL_GATEWAY_TOKEN 已配 + weeksir.gateway 凭据已 login）
1 在 C:\dsh\hypit_runs 里 authoring：看参考片→写 .svml/.svrun（结构、素材槽、时间轴）
1.5 挂右栏（可选，做长活时建议）：studio-claim "$DSH_SESSION_ID" <run源文件路径> 5179 → hypit studio --run <x>.svrun --port 5179
    ⚠️ 三条：① `$DSH_SESSION_ID` 为空（=不是 DSH 自带 agent 在驱动）就**别 claim**，那种 studio 只给 ops 看；② run 要传**源文件路径**，presence 会拿 studio 自报的 `source.path` 交叉核对；③ 不用 release，claim 有 30 分钟 TTL（细则见 §4）
2 hypit check <src>            过 lint
3 hypit plan <src>             看结构（免费）；确认生成腿落到 weeksir.gateway
── 关口A 方案+预算批准 ──  hypit pricing <src> 出成本；plan+pricing 摆给人，批了才 build（铁律1）
4 hypit runtime up             Worker 不起 build 不跑（实测停着时报 stopped）
5 hypit build <src> --follow   花钱、长；轮询 hypit status <id> / builds / logs
── 关口B 抽帧 QA ──  hypit studio --run <x>.svrun --port 5179（右栏）或 media frames 出图核对
6 hypit get <id> --output <name> --to productions/<task>/   导出成片
── 关口C 成片验收 ──  对话里 present，人点头
```

命令不必 `cd` 到工作区：`--workspace C:\dsh\hypit_runs` 在任意 cwd 可用（实测 `runtime status`），但源文件路径要给绝对路径。

## §3 关口（比 omni 多一道钱闸）
- **A 方案+预算批准**：build 花钱前必过。走 `weeksir.gateway` 时钱=**网关积分**（`gateway.invoke` 预扣→失败回滚，账目在 `gateway.py credits`），不是付 HypiHub；pricing 是唯一成本预估口。
- **B 抽帧 QA**：native studio 或 media frames。
- **C 成片验收**。
- 商用合规/反 AI 幼稚腔审核线沿用 [[omni-delivery-contract]] §5.5/§5.6（创意/分镜/口播通用）。

## §4 右栏挂载 = hypit 官方 studio 工作台（不是 D 控制台，也不是"临时看片器"）
studio 是 hypit 自带的 SVML 工作台：**左库 源文件(Source：Run+Author 闭包、可写回) / 任务(Tasks：一卡一条 Build，All·In progress·Finished，活动进度取自 Runtime 只读 control 口) / 产物(Artifacts：项目媒体卡)**，右侧 预览 + 属性，另有 `#comments`。**未 build 的 `.svrun` 也能打开**（先看结构，Build 后看进度与成片），所以 agent 在 authoring 阶段就可以把它挂上右栏。

三条实测约束（改这里前必读）：
1. **地址只能写 `http://localhost:5179/`**：Vite dev server 只监听 **IPv6 回环 `[::1]:5179`**（netstat 实证），写 `127.0.0.1` iframe 必白屏。
2. **presence 信号走 8100 代探**：studio 自己的只读口 `GET /__studio/library?section=tasks` 虽然回 200，但 Vite 的 CORS **只回显白名单 origin**（实测给 `http://localhost:8765`、不给 `dsh-app://…`/`null`/外部 origin）⇒ DSH 渲染层 fetch 不到。改由 **8100 `GET /api/hypit/studio`**（服务端代探 5179，回 `{up,port,tasks,running,artifacts}`，带 `ACAO:*`、只读、不要 Bearer；端口不开放给查询参数，免得变成给本机网页用的端口探测器）。插件 `syncHypitTab` 每 4s 轮它，`up:true` 才 `openTab`。
3. **预览区那条 `编译失败 · Studio projection is unresolved: @hypit/seedance@1#seedance-2-mini` 是设计如此**：studio 编辑期只做确定性 Producer + Runtime 显式许可的 **transient** Need，而 transient 执行**只注册 immediate 端点、异步端点声明 transient 会直接抛错**（`runtime-local/src/config.ts:599`）⇒ 我们这条 `lifecycle:"asynchronous"` 的网关腿进不了预览执行。**别为此去 `runtime up` 或改 provider**；成片只能在 `hypit build` 后从 任务/产物 面板看。
- ⚠️ 安全边界：studio 是 **可写的本地无鉴权工作台**（`PUT /__studio/source` 直接改工程文件、`POST /__studio/mutation`）⇒ 只 ops 自用；给客户看只能给对话 + `hypit get` 导出的成片，或 `hypit snapshot --studio <url>` / `/__studio/visual.html` 的只读快照。
- **🔴 归属登记：登记的是「这个 run 属于哪个会话」，不是「谁起过服务」**（契约=`C:\dsh\docs\ADR-workbench-presence.md`；2026-10-01/10-02 三次复发的根因就是登记在了服务上、还只增不减）：
  ```
  python D:\video-skill\backend\hypit_bridge.py studio-claim "$DSH_SESSION_ID" productions/<工程>/<x>.svrun 5179
  hypit studio --run <x>.svrun --port 5179 &
  ```
  - **`$DSH_SESSION_ID` 只有 DSH 自带 agent 才有**（内核按每次 bash 调用注入，见 `_setup/dsh-harness/.agents/notes/archived/feature/2026-07-10-agent-session-identity-and-log-location.md:22`）。**在 Qoder/ops 会话里它是空的 ⇒ 别 claim**（空 session 会被直接拒），也别指望右栏弹：那种 studio 只给 ops 自己看。确实要让某个 DSH 会话看到，就从运维视图取它的 id 再显式 claim：`curl "http://127.0.0.1:8100/api/hypit/studio"` → `seen[]`。
  - **run 参数给源文件路径**（不是裸工程名）：presence 会拿 studio 自报的 `source.path` 做交叉核对，工程目录对不上直接判死。
  - **不需要 release**：claim 带 30 分钟 TTL，右栏每 4 秒的探测就是心跳，没人看就自动失效（旧版"只增不减"就是永久产权那个 bug）。要立刻收口就 `studio-release <会话id>`。
  - 🔴 **2026-10-02 定稿：弹不弹只看【意图】，一条合取都没有**。claim 就是意图登记、**持久不撤销**（旧版 30 分钟 TTL 会撤销 ⇒ 老会话回来什么都不弹，是错的）；studio 死了台照样在，里面显示「引擎没在跑 + 怎么起」。判定统一走 `GET /api/workbench/presence?session=`（一次回 omni + hypit 两台），别再单独打 `/api/hypit/studio`。登记封顶 20 个 run、只裁最旧。
  - 旧谓词 = **归属 ∧ 存活 ∧ 交叉核对**，缺一即不弹：`?session=` 不匹配→`not-owner`；studio 自报的 run 与登记不符→`run-mismatch`；studio 探不到→`studio-down`；**不带 session 的请求只回 `reason:"ops-view"`、永不回 `up`**（专门堵死"端口活着"再被当成跨会话信号）。查：`studio-status <会话id>`。
- 部署事实：插件 `client.js` 经 `AppData\Roaming\dsh-desktop\harness\profiles\web\node_modules\ops-gateway` **符号链接**指回 `C:\dsh\plugins\ops-gateway` ⇒ 改完**不用 pnpm install，重载 DSH 窗口即生效**；8100 改码则要重启计划任务 `WeeksirModelAdmin`（Stop-ScheduledTask → Stop-Process 旧 PID → Start-ScheduledTask）。

## §5 会话/任务绑定
- 一条工程 = `C:\dsh\hypit_runs` 下一个 run 目录；跨会话靠 `productions/<task>/`（BRIEF/ANALYSIS/TIMELINE/PROGRESS/DELIVERABLES）记当前 run，不靠 D、不靠 HTTP session 参数。
- 产物本体留 `hypit_runs`（`.hypit/`、`assets/`、`node_modules/` 已被 `.gitignore` 挡住，别 commit）；**工程文件与 provider 源码入库**（`hypit_runs/productions/`、`packages/`、`hypit.runtime.json`、`package*.json`——里面只有凭据**引用**，没有明文）。成片用 `hypit get` 导出到 `productions/<task>/`。

## §6 前置与坑
1. **build 要两样凭据/服务**：① HypiHub（`hypihub.default`，若用官方 Provider 才需要，OAuth、agent 不能替登）；② 我们的 `weeksir.gateway` apiKey（`hypit auth login weeksir.gateway --from`）。缺了 `hypit doctor` 报 `RUNTIME_CREDENTIAL_MISSING`，只能 check/plan。
2. **build 长且花钱**：可到 1h、按 Need 计费；pricing 未批绝不 build（铁律1）。
3. **studio 未 build 也能开**（看 源文件/任务/产物，只是 预览 空）；端口固定 5179 便于右栏挂。⚠️ 打开**含异步生成腿**的 run（如 `seedance:ReferenceVideo`）时，预览区会显示 `编译失败 · Studio projection is unresolved: @hypit/seedance@1#seedance-2-mini`，**并且 源文件 面板也是空的**（`GET /__studio/session` 回 500）——这是设计如此：studio 编辑期只做确定性 Producer + Runtime 许可的 transient Need，而 transient 执行**不注册异步端点**（异步端点声明 transient 会直接抛错，`runtime-local/src/config.ts:599`）。**已验证的解法**：把工程改成零生成腿（成功段用 `<media:Video>` 引用，见 §8），同一台 studio 打开后 `/__studio/session` 由 500 → **200**、源文件与预览都有内容。⇒ **想在 studio 里看片/看结构，就开"装配工程"（assemble），不要开"生成工程"（clone）**；也别为此去 `runtime up` 或改 provider。
4. **runtime up 才出片**：只 init 不 up，build 无 Worker（`hypit runtime status` 实测停着时 `Worker stopped`）。
5. **D 的边界=只走 HTTP 契约**：生成腿经 `http://localhost:8100/api/hypit`（这**是**允许且要求的）；但 `:8090/:8000`、`hypit_workspace.py`、`D:\video-skill\hypit-workspace`、import D 的 Python 都不要再出现在本流程里（那是旧的 D 封装，已弃用）。
6. **上游 skill 会被 npx 覆盖**：authoring 语法参考在 `.dsh/skills/hypit/`（只读）；本驱动契约只在 `hypit-pipeline/`，别写进上游。

## §7 e2e 实测结论（2026-10-02·已花钱·别当成"链路坏了"去重跑）

**✅ 全链已跑穿一次**：1 段最小工程 `hypit_runs\productions\e2e-one\e2e.svrun` → `hypit build` 6 步完成、出 `final.video`，付费段资产与 hypit 结果库文件**同大小同哈希**（2,392,271 字节 / sha1 `f5c49119a89a`，`result.json` 里输出名 `seg1-video.video`）。全程 ~30 分钟，其中 **~25 分钟只在等那一段付费生成** —— 排期要按这个数报给人。
**这一段是被重试救回来的**：账本 `preauth→rollback→preauth→rollback→preauth→done`（3 次尝试、`MAX_ATTEMPTS=3` 用满）。所以**上游抖的时候，一次失败≠链路坏**；先看 detail 的错误类别再决定重跑。

**链路是通的**，硬证据：网关侧资产 `D:\video-skill\outputs\_hypit_jobs\f5731b02….mp4`（1,870,530 字节 / sha1 `50520bfef489`）与 hypit 结果库 `.hypit\results\<日期>\<build>\files\file-0001.mp4` **同大小同哈希** ⇒ `gateway.invoke`→记账 50 分→`/api/hypit/assets`→**hypit collect**→Result Repository 无损耗。

真实数字（5s·720p·seedance-2-mini，走 `video.generate/seedance-2.0`=vinted.cam。**口径=单遍统计 27 条真实段，剔掉我自测的短 prompt 桩**）：
- **成功 15 / 已定论 26 ≈ 56%**；⚠️ 这是**段(含重试)**口径——一条 job 内部最多 3 次尝试，所以**单次尝试命中率更低**，别把 56% 当"发一次就中"。
- 上游侧失败 9 段：`生成超时（它自判、退积分）×4`、`503 参考图下载繁忙（原话"请稍后使用同样的请求重试"）×2`、`参考图 4xx×1`、`5xx(502)×1`、**`肖像保护×1`**。我方原因 3 段：形状 bug×1、我们轮询超时×1、我重启打断×1。
- 单段耗时 **305 / 359 / 373 / 405 秒**；上游约 400s 自判超时。我们轮询上限现为 **1800s**（旧 900s 会把快成的片误杀）。
- **重试分两类，别混**：`RETRYABLE=(502,503,504,service_unavailable,参考图,生成超时)` 自动重试（同样请求、退避 30/60s、最多 3 次）；`NON_RETRYABLE=(肖像,本人肖像,内容不合规,违规,敏感)` **先排除、不重试**（策略拒绝重试也不会变，实测那 1 段旧代码白跑 3 次≈15 分钟）；`轮询超时` 也不重试（我们等够≠上游没出片，重试=重复花费）。
- **人脸参考图不是硬墙**：含人脸的段成功过多次，肖像保护只有 1/27≈4%，但它是**非确定性**的（同图同词上次过、这次拒）。⇒ 真人片型的真解法是**段级复用**（成功段固化成素材、只补缺段），不是换模型也不是重掷整条 build。
- **算式**：一条 build 要 N 段**全成**才过 ⇒ 4 段 ≈ 0.56⁴ ≈ **10%**。所以"连跑 8 次全灭"= 上游稳定性 × 组合概率，**不是接线问题**。

操作规矩（省钱省时）：
1. **验链用 1 段最小工程**，别拿 4 段正片试：`hypit_runs\productions\e2e-one\e2e.svrun`（6 请求 / 5 个本地免费 / 只 1 段付费 ≈50 分）。
2. build 失败先看 `hypit status <build-id>` 的 detail + `D:\video-skill\.gateway_jobs.sqlite` 的 `error`（含上游任务号可回溯），**不要直接重跑整条多段 build**。
3. 上游成功一段后**积分才会扣**（失败=rolled_back，实测 0 扣费），所以反复重跑不产生账目，但产生等待时间与真实上游压力。
4. **重启常驻 8100 前先查有没有在跑的 job**（`SELECT id,state FROM jobs WHERE state IN ('queued','running')`）——我两次重启打断了用户自己的 build。
5. 未证实项（别当结论用）：同期 Google Flow 的"Image uploads are failing…"公告与我们 `参考图` 类失败**无证据同源**（不同上游，症状同名）；"换参考图托管源"的对照实验用户决定暂不跑。

## §8 段级复用（build 失败后的标准处置·已免费验证，别再整条重跑）

单段成功率 ≈56% ⇒ 4 段全成 ≈10%。**别重掷**：把已成功的段固化成素材，只补缺的那几段。

1. 找成功段：`D:\video-skill\.gateway_jobs.sqlite` 里 `state='succeeded'` 的行，`output` 指向 `D:\video-skill\outputs\_hypit_jobs\<jid>.mp4`；用 `request` 里的 prompt 前 20 字对应回 `segN`。
2. 拷进工程：`copy ... -> C:\dsh\hypit_runs\assets\segN.mp4`（`assets/` 已被 gitignore，别 commit）。
3. 改 SVML：把那段整块 `<seedance:ReferenceVideo id="segN-video" …>…</seedance:ReferenceVideo>` **换成一行**
   `<media:Video id="segN-file" src="../../assets/segN.mp4"/>`
   ⚠️ 两个坑：① `media:Video` 发布的是**裸 id**（不是 `segN-file.video`），所以下游要写 `pipeline:Normalize … source={segN-file}`；② `src` 按**声明它的源文件**位置解析（`../../assets/` = 相对 `productions/<task>/`）。
4. **免费校验**：`hypit check <src>` 过语法 → `hypit plan <src>` 看 `Provider requests` 有没有按预期减少（实测 4→2、`Preflight ready`）→ 才 build。
5. 已验证样例：`hypit_runs\productions\e2e-reuse\rerun.svml`（seg1/seg2 素材、seg3/seg4 生成 ⇒ plan=2）。
6. 段数与 5s 时长要一致（`media:Track Item for="5s"`），素材本身若不是 5s 就先 `hypit media probe` 看时长，别硬套。

## §9 交付与持续监控（2026-10-02 补：这两点曾漏，用户点名）

### 成片下载位（固定，别让人去翻 `.hypit`）
```
hypit get <build-id> --output final.video --to C:\dsh\productions\<task>\outputs\final-20s.mp4
```
- 目录 = `productions/<task>/outputs/`（已在 `.gitignore`，成片不入库）。
- **文件名用 ASCII**（`final-20s.mp4`）：中文名会踩 DSH 预览卡片的短名/路径坑。
- 交付时对话里给两条入口：① 本地路径（右栏文件预览能播，video 预览补丁已装）；② 可点开的 http 直链 —— 把文件拷进 `D:\video-skill\outputs\_hypit_jobs\` 就能由 8100 出片：`http://localhost:8100/api/hypit/assets/<名字>.mp4`（实测 200 / `video/mp4`）。
- 顺手抽 1~2 帧（`ffmpeg -ss 1 -i x.mp4 -frames:v 1 f.jpg`）贴进对话：**图能内联、视频不能**，别只丢链接。

### build 必须盯到终态（agent 硬规矩，不许"提交完就交回给人"）
- 发起 build 后要一直轮询到 `hypit status <build-id>` 进终态（complete / failed / cancelled）为止。
- 单次工具调用有 10 分钟上限 ⇒ 正确姿势：`hypit build <src> --follow > _build.log 2>&1 &` 后台跑，然后**每轮读日志尾部 + 查 `.gateway_jobs.sqlite` 的 job 状态**；一轮不够就接着下一轮，**中途不要停下来问"要不要继续看"**。
- 播报节奏：**状态变了才播**（第几段成功／在重试／失败原因），别每 4 秒刷屏；**超过 10 分钟无变化**要主动报"卡在哪个请求、上游任务号是多少"。
- 到终态立刻做上面的导出 + 抽帧交付，不要等人催。
- 失败按 §8 只补缺段（属同一次预算范围，但要说清"补第几段、多少分"）。

## §10 一键导出（2026-10-02 实装·右栏 Hypit 页签顶部那条，agent 也可以用同一条路）

导出 = `hypit build` + `hypit get --to`（官方 help 原话 "export one Build Output"）。**导演台里没有任何导出按钮**（`/__studio/*` 九个路由里没有 export/render，`studio/README.md:84` 明说浏览器播放不产生导出），所以这个口是我们自己在 8100 上开的，右栏按钮和 agent 走的都是它。

```
python D:\video-skill\backend\hypit_bridge.py export <run相对路径> [productions下的任务目录名] [文件名.mp4]
# 或 HTTP（右栏按钮就是这条）：
POST http://127.0.0.1:8100/api/hypit/export   {"run":…,"task":…,"name":…}   → {"id":…}
GET  http://127.0.0.1:8100/api/hypit/export?id=…  → state: queued|planning|building|fetching|done|failed
```

**四道闸（别绕，绕过去就是"没报价就烧钱"）**：
1. **免费闸**：`hypit plan --json` 的 `providerRequestCount` 必须 **== 0**。有生成腿的工程一律拒（错误里会点名几段付费）⇒ **要新镜头仍走 §关口A 报价+人批 + `hypit build`**，这个按钮只用于"装配工程出片/改结构重出"。`_export_run` 在真正 build 前还会再验一次。
2. **路径白名单**：`run` 只能是 `C:\dsh\hypit_runs` 下的相对 `.svrun`（拒 `..`／绝对路径／盘符）；`task` 只能是 `C:\dsh\productions` 下**已存在**的一层目录名；文件名只收裸 ASCII `.mp4`，否则回落 `export-<id>.mp4`。
3. **不走 shell**：`subprocess` 用 argv 列表 + `node <hypit.mjs>`（⚠️ 实测无 shell 直调 npm 的 `hypit` 垫片会 `WinError 2`；且必须 `encoding="utf-8"`，默认 cp936 会把 hypit 的 UTF-8 输出解码崩）。
4. **单飞互斥**：同时只允许一条导出（409 BUSY）。重启会打断在跑的导出 ⇒ 启动时 `export_reconcile()` 把遗留的判死。

**鉴权**：Bearer 对 ⇒ 放行；没带 token 时**只认本机 Origin**（按 host 放行：`127.0.0.1`/`localhost`/`[::1]`/`dsh-app`，**不按端口**，DSH 渲染层端口每次可能变）。插件里不能嵌 token，所以按钮用 `Content-Type: text/plain` 发（免预检）。外站 Origin 一律 403。

**⚠️ 代价**：渲染是本地无头 Chrome，一次导出实测 **49 个 node/chrome 进程、CPU 100%、2~4 分钟**，期间 **8100 忙到不回包**（右栏 presence 会短暂 `probe-failed`）。别当成服务挂了去重启它。

**验收**：`PYTHONIOENCODING=utf-8 python scripts/check-export-gates.py`（29 条：路径/文件名/origin/免费闸正反两向）。真跑过一次 HTTP 全链：`done`、build id 由 `hypit builds --json` 前后快照差集拿到、文件落进中文任务目录、直链 200 `video/mp4`、**余额不变（0 分）**。
