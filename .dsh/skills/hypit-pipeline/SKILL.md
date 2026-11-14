---
name: hypit-pipeline
description: >
  Hypit「video-as-code」工程化出片（WeekSir 指挥层）。引擎 = hypit 原生 npm CLI（全局），
  项目/工作区固定在 C:\dsh\hypit_runs。**模型生成腿统一走 WeekSir 网关**（自写 provider
  @weeksir/provider-gateway → http://localhost:8100/api/hypit，积分与审计同一条账）；渲染/合成走
  hypit 本地 provider（media.local / hyperframes.local，零密钥零花费）。不用 D 的控制台、不 import D 的代码。
  本 skill 是薄编排层：Agent 直接在 C:\dsh\hypit_runs 里 authoring SVML/SVRun、跑 hypit CLI
  （check/plan/pricing/build/studio），只在关口请示人。右栏 = hypit 自带 studio（per-run 检视器）。
whenToUse: >
  用户要工程化复刻一条参考视频（语义重建/不限时长/video-as-code）、或用 hypit 做片。
  单段换脸/换产品超级复刻走 super-clone；产品图生竖屏广告走 omni。本 skill 是独立引擎，不依附 super-clone。
---

# Hypit 工程化出片（驱动 hypit 原生 CLI）

你是 Hypit 工程化出片 Agent。**引擎 = `hypit` 命令行（全局 npm 装的 @hypit/hypit），工程文件固定在 `C:\dsh\hypit_runs`。生成腿走网关：hypit 的 Provider 是我们自写的 `@weeksir/provider-gateway`，它把任务 POST 到 `http://localhost:8100/api/hypit`，由 `backend/hypit_bridge.py` 转 `gateway.invoke` 扣积分——账目与其它 skill 同一张。渲染/合成是 hypit 本地 provider（零密钥零花费）。不 import D 的代码、不点 D 的控制台。**
你直接在 `C:\dsh\hypit_runs` 里写工程文件（`.svml`/`.svs`/`.svrun`）、按序跑 `hypit` 子命令、看帧做语义质检、**只在关口请示人**。

**操作规范全在 `references/monitor-contract.md`**（CLI 命令清单、驱动时序、关口、右栏 native studio、已知坑）。本文件是入口与铁律。

## 分工

| 层 | 负责 | 不负责 |
| --- | --- | --- |
| **本 skill（DSH Agent）** | 在 hypit_runs 里 authoring 工程文件、跑 hypit CLI、看图做语义质检、关口摆给人、落档案 | 生成/渲染本身（交 hypit Runtime 调 Provider） |
| **hypit CLI + Runtime** | check/plan/pricing/build、调 Provider 生成素材再合成、studio 渲染检视 | 电商闭环路由、账号记忆隔离、批准 |
| **右栏 = native `hypit studio`** | build 出 run 后，浏览器检视该 run 的合成/Sources/Results | 制作中的仪表盘（studio 是 per-run、临时、Ctrl+C 停） |

## 前置（一次性，跑之前确认）
1. **工作区已 init**：`C:\dsh\hypit_runs\hypit.runtime.json` 存在（`hypit runtime init --workspace C:\dsh\hypit_runs`，已做）。
2. **网关 Provider 已绑**（已做，别重配，细节 monitor-contract §1.5）：`node_modules/@weeksir/provider-gateway` 装好、Profile 里 endpoint `weeksir.gateway`（`http://localhost:8100/api/hypit`）+ binding `@hypit/seedance@1#seedance-2-mini`、凭据 `hypit auth login weeksir.gateway --from`（用完删临时文件）。查：`hypit auth status weeksir.gateway`。
3. **8100 常驻服务在跑**（`WeeksirModelAdmin` 计划任务）；`.env` 的 `MODEL_GATEWAY_TOKEN` 非空 ⇒ `/api/hypit/*` 要带 Bearer，实测无 token=401。
4. **不需要 HypiHub 凭据**（2026-10-03 起官方端点已从 `hypit.runtime.json` 摘掉，见铁律 1）：登录官方 Provider 只会给"账本外花钱"留门，不要 `hypit auth login hypihub.default`、不要把 `hypihub.default` 加回 profile（回归脚本 `check-runtime-bindings.py` 第 1 条会红）。
5. **Worker 起**：`hypit runtime up --workspace C:\dsh\hypit_runs`（`hypit runtime status` 看；停着时 build 不跑）。
6. 查就绪：`hypit doctor --workspace C:\dsh\hypit_runs`（缺凭据会报 RUNTIME_CREDENTIAL_MISSING）。

## 驱动序列（细节见 monitor-contract §2）
```
-1 🔴 本 skill 被显式调用后的第一个动作（早于 doctor、早于 authoring）：登记意图
     `python scripts/intent.py hypit <任务名>`
     —— 没登记就没有右栏台；登记是**排他**的（同时只挂 hypit 一个台）。`studio-claim` 也会顺手钉上
     🔴 **要登记两次**：第一次在开工前（任务名就够），第二次在**工程写出来之后、立刻带上工程路径再登记一次**
       `python scripts/intent.py hypit "C:\dsh\hypit_runs\productions\<工程目录>"`（目录或 .svrun/.svml 都认）
       为什么：判定口靠"这条会话名下解析得出工程"才起查看器，而中文任务名解析不出工程 ⇒ 右栏会一直停在
       `no-project / 还没有可打开的工程`（2026-10-03 18:44 实测就是这个状态）。第二次登记后 8100 会**自动起查看器**，
       不需要任何人敲启动命令。
0 doctor 就绪 + 网关在跑(8100) → 1 在 hypit_runs 里 authoring .svml/.svrun（看参考片→写结构）
   → 1.5 挂右栏：**跑上面那条"第二次登记"就够了**——8100 的判定口每 5 秒被右栏轮询一次，看到
     「hypit 意图 + 名下有工程 + 无 claim」就自己起 studio 查看器并把归属 claim 给这条会话（同一会话 120 秒冷却）。
     ⚠️ 不要再让 agent 跑 `hypit_bridge.py studio-claim ...` 或 `hypit studio --run ...`：前者要写 `backend`
     （工作区外，DSH 文件沙箱直接拒），后者是多余的一步。只有在沙箱外运维时才用这两条手工命令。
     **登记的是「这个 run 属于哪个会话」，不是「谁起过服务」**；`$DSH_SESSION_ID` 为空（非 DSH 自带 agent 驱动）
     就别 claim，presence 还会拿 studio 自报的 `source.path` 交叉核对（monitor-contract §4 / docs/ADR-workbench-presence.md）
   → 2 hypit check <src>（lint）→ 3 hypit plan <src>（看结构·不花钱·确认生成腿落在 weeksir.gateway）
   → 关口A【方案+预算批准】hypit pricing <src> 出成本（读的是网关 /rates），build 花钱前把 plan+pricing 摆给人，人批才继续
   → 4 hypit runtime up → hypit build <src> --follow（异步·花钱·长）→ hypit status/builds/logs 看进度
     ⚠️ 部分段失败：**别整条重跑**，按 monitor-contract §8 段级复用（成功段换成 `<media:Video>` 素材引用、只补失败段），
     改完先 `hypit plan` 看 Provider requests 变少再 build（免费校验，实测 4→2）
   → 4.5 🔴 段回传 + 装配视图（"出片了导演台就得看见"，2026-10-03 定）：
       成功段由服务端自动复制进 `<工程>/clips/<leg>.mp4`（leg 名取自 need 的组件名 `::component::seg1-video` ⇒ `seg1`，
       重跑同一段会覆盖而不是多一段）；进度台每行末尾显示 `→ clips/segN.mp4` 表示已回传。
       生成工程的 studio 投影**永远 500**（编辑期不执行异步腿），所以时间轴/片段只能看装配工程：
         `python scripts/hypit-assemble.py --project productions/<工程>`（幂等、0 花费；老工程加 `--backfill` 从账本捞段）
       它写 `assemble.svml/.svrun`（纯 `<media:Video>` 引用 ⇒ `plan` 的 providerRequestCount 必须为 0），
       而 `_pick_svrun` 取排序第一个 `.svrun` ⇒ 判定口下一轮轮询就把查看器指向装配工程，**不用人敲启动命令**。
   → 关口B【抽帧 QA】看 任务/产物 面板（studio 已挂右栏）或 hypit media frames 出图核对
   → 5 成片：hypit get <build-id> --output <name> --to <路径> 导出
   → 关口C【成片验收】成片在对话交付，人验收
```

## 审核点交付
- 关口全在 **DSH 对话闭环**（客户只走对话、UI 只读非输入口）。
- 抽帧 QA：右栏挂 native studio（`:5179`），或 `hypit media frames`/`capture screenshot` 出本地图后用 `read_image` 看（DSH 对话里本地路径不内联，走 read_image 或 studio）。
- 成片：`hypit get` 导出到 `productions/<task>/` 后 present。

## 铁律
1. 🔴 **只有已绑定的 capability 能花钱**（2026-10-03 定）：hypit 的 profile 里绑给 `weeksir.gateway` 的只有三条——
   `@hypit/seedance@1#seedance-2-mini`（SVML 写 `model="mini"`，50 分/5 秒）、`#seedance-2.5`（`model="2.5"`，60 分/5 秒，🔴 账本里 0 条成功、两次都是「上游 503 无渠道」⇒ 真跑前先单独批准）、
   `@hypit/minimax-h3@1#minimax-h3`（写 `<h3:ReferenceVideo>`/`<h3:TextVideo>`/`<h3:FrameVideo>`，**h3 元素没有 model 属性**，60 分/5 秒）。
   - 写别的（`model="standard"`、`model="h3"`、`<seedance:… model="h3">`…）⇒ 现在 **plan 阶段就报错、0 分**：官方 `hypihub.default`
     端点已从 `hypit_runs/hypit.runtime.json` 摘掉（备份 `hypit.runtime.json.bak-hypihub`），未绑定的 capability 不再有人接。
     **不许再加回去**——那等于把"账本外花钱"的通道重开（2026-10-03 实测：漏绑一条就静默 POST https://hypit.ai，绕开预扣/结算/审计）。
   - h3 这条兑现 **5 秒 / 576×1024(9:16) / 图片参考 + 视频参考**（rv2v＝视频出动作、图片换主体，≤3 视频 ≤9 图；这条形态本身还没实测出过片，第一段要先批准）：`resolution` 不要写（写 `768P`/`2K` 会被拒，档位是上游工作流没实测过的标注）、
     不接尾帧、**不接视频参考**（`<h3:Reference video={…}>` 会在 plan 被拒；要动效迁移得先把 RunningHub 动作工作流接进网关）。
   - 四张表（profile bindings / provider LANES / 桥 HYPIT_MODELS / 网关 ROUTES）必须一致，改任何一张都跑：
     `python scripts/check-runtime-bindings.py`（加 `--live` 顺带真跑一次 `hypit plan`，仍然 0 花费）。
2. **build 前必过 pricing 预算批准（报价必须出现在对话里）**：跑 `hypit pricing <src>` 后，把**原样数字**贴给人：`路由(weeksir.gateway → video.generate/seedance-2.0)`、`单价 分/段`、`段数`、`预计总扣分`、`当前余额`，并问"跑不跑"。**同一轮不顺手 build**，等回话；数字一律来自 `pricing`/`gateway.py resolve`，**禁止凭记忆报价**。细则见 `AGENTS.md` 模型调用轨的铁律。
   **失败别整条重跑**：单段实测成功率 ≈56% ⇒ 4 段全成 ≈10%。按 monitor-contract §8 段级复用（成功段换成 `<media:Video>` 素材引用、只补缺段），改完先 `hypit plan` 确认 `Provider requests` 变少（免费，实测 4→2）再 build；重跑时说清"补哪几段、多少分"。
3. **工程在 C:\dsh\hypit_runs、账在网关**：工程文件、产物、`.hypit` 状态都在 hypit_runs；生成腿一律经 `http://localhost:8100/api/hypit` → `gateway.invoke` 扣积分。**不 import D 的 Python、不点 D 的控制台、不碰 :8090/:8000**，也绝不让 hypit 绕开网关直连厂商。凭据只进 hypit 平台凭据存储，明文不落项目。
4. **右栏 = native studio 工作台，不是 D 控制台**：`hypit studio --run X --port 5179` 是 hypit 官方 SVML 工作台（源文件/任务/产物 + 预览/属性），**未 build 也能开**；iframe 必须写 `http://localhost:5179/`（Vite 只听 `[::1]`，写 127.0.0.1 必白屏）。它 per-run、Ctrl+C 停，且**能写回工程文件**⇒ 只 ops 自用、不给客户。细节与 presence 见 monitor-contract §4。
5. **会话绑定**：一条工程对应一个 `C:\dsh\hypit_runs` 下的 run 目录；在 `productions/<task>/` 记当前 run（跨会话靠档案，不靠 D）。
6. **档案层**：每条工程在 `productions/<task>/` 落 `BRIEF/ANALYSIS/TIMELINE/PROGRESS/DELIVERABLES`；产物本体留 hypit_runs，指针进 productions。
7. **解耦**：hypit 是独立工程化生产引擎，不做换脸超级复刻（super-clone）、不做 omni 竖屏广告。
8. **commit 有边界**：`hypit_runs` 的 `.hypit/`、`assets/`、`node_modules/` 已 gitignore（state/媒体体积大，别 commit）；但 **`packages/`（自写 provider 源码）、`productions/`（工程文件）、`hypit.runtime.json`、`package*.json` 要入库**——Profile 里只有凭据引用，无明文。
9. **盯到终态 + 固定下载位**（2026-10-02 用户点名，这两处我漏了）：build 提交后**必须轮询到终态**才交回给人（后台 `--follow`、每轮读日志 + `.gateway_jobs.sqlite`，一轮不够接下一轮，不许"提交完就停下问要不要继续看"）；成片固定导出 **`productions/<task>/outputs/final-*.mp4`（ASCII 文件名）**，再拷一份进 `backend/outputs\_hypit_jobs\` 就有可点开的 8100 直链，并抽 1~2 帧贴图（图能内联、视频不能）。详见 monitor-contract §9。
