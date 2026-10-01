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

## §4 右栏挂载（native studio，非 D 控制台）
- pipeline-view 的「Hypit Studio」tab iframe 指 **`http://127.0.0.1:5179/`**（agent 起 `hypit studio --run X --port 5179` 后才有内容）。
- ⚠️ studio 是 **per-run、临时进程**：没在跑就白屏；Ctrl+C 停。它是"看某条成片"的窗口，**不是制作中仪表盘**，也**不是拖拽 NLE**（改内容靠改 SVML 文本）。
- presence 门控**暂关**（syncHypitTab 已 return）：没有像 video-ui 那样可廉价轮询的常驻 API；靠 agent 在 build 后起 studio、人手动开/刷新 tab 看。将来要做自动弹，需一个"studio 是否在 :5179"的轻量探测（studio 无 CORS，fetch 探不了，得另想信号）。

## §5 会话/任务绑定
- 一条工程 = `C:\dsh\hypit_runs` 下一个 run 目录；跨会话靠 `productions/<task>/`（BRIEF/ANALYSIS/TIMELINE/PROGRESS/DELIVERABLES）记当前 run，不靠 D、不靠 HTTP session 参数。
- 产物本体留 `hypit_runs`（`.hypit/`、`assets/`、`node_modules/` 已被 `.gitignore` 挡住，别 commit）；**工程文件与 provider 源码入库**（`hypit_runs/productions/`、`packages/`、`hypit.runtime.json`、`package*.json`——里面只有凭据**引用**，没有明文）。成片用 `hypit get` 导出到 `productions/<task>/`。

## §6 前置与坑
1. **build 要两样凭据/服务**：① HypiHub（`hypihub.default`，若用官方 Provider 才需要，OAuth、agent 不能替登）；② 我们的 `weeksir.gateway` apiKey（`hypit auth login weeksir.gateway --from`）。缺了 `hypit doctor` 报 `RUNTIME_CREDENTIAL_MISSING`，只能 check/plan。
2. **build 长且花钱**：可到 1h、按 Need 计费；pricing 未批绝不 build（铁律1）。
3. **studio 需已 build 的 .svrun**：没 build 打不开；端口固定 5179 便于右栏挂。
4. **runtime up 才出片**：只 init 不 up，build 无 Worker（`hypit runtime status` 实测停着时 `Worker stopped`）。
5. **D 的边界=只走 HTTP 契约**：生成腿经 `http://localhost:8100/api/hypit`（这**是**允许且要求的）；但 `:8090/:8000`、`hypit_workspace.py`、`D:\video-skill\hypit-workspace`、import D 的 Python 都不要再出现在本流程里（那是旧的 D 封装，已弃用）。
6. **上游 skill 会被 npx 覆盖**：authoring 语法参考在 `.dsh/skills/hypit/`（只读）；本驱动契约只在 `hypit-pipeline/`，别写进上游。
