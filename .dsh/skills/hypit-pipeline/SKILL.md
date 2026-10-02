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

你是 Hypit 工程化出片 Agent。**引擎 = `hypit` 命令行（全局 npm 装的 @hypit/hypit），工程文件固定在 `C:\dsh\hypit_runs`。生成腿走网关：hypit 的 Provider 是我们自写的 `@weeksir/provider-gateway`，它把任务 POST 到 `http://localhost:8100/api/hypit`，由 `D:\video-skill\backend\hypit_bridge.py` 转 `gateway.invoke` 扣积分——账目与其它 skill 同一张。渲染/合成是 hypit 本地 provider（零密钥零花费）。不 import D 的代码、不点 D 的控制台。**
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
4. **HypiHub 凭据**（只有用官方 Provider 才需要）：`hypit auth login hypihub.default --runtime C:\dsh\hypit_runs\hypit.runtime.json`。OAuth 要人自己做，agent 不能替登。
5. **Worker 起**：`hypit runtime up --workspace C:\dsh\hypit_runs`（`hypit runtime status` 看；停着时 build 不跑）。
6. 查就绪：`hypit doctor --workspace C:\dsh\hypit_runs`（缺凭据会报 RUNTIME_CREDENTIAL_MISSING）。

## 驱动序列（细节见 monitor-contract §2）
```
0 doctor 就绪 + 网关在跑(8100) → 1 在 hypit_runs 里 authoring .svml/.svrun（看参考片→写结构）
   → 1.5 挂右栏（长活建议）：先 `python D:\video-skill\backend\hypit_bridge.py studio-claim "$DSH_SESSION_ID" <run> 5179`
     再起 `hypit studio --run X --port 5179`；**不 claim 右栏不会弹**（presence 按会话主人过滤，monitor-contract §4）
   → 2 hypit check <src>（lint）→ 3 hypit plan <src>（看结构·不花钱·确认生成腿落在 weeksir.gateway）
   → 关口A【方案+预算批准】hypit pricing <src> 出成本（读的是网关 /rates），build 花钱前把 plan+pricing 摆给人，人批才继续
   → 4 hypit runtime up → hypit build <src> --follow（异步·花钱·长）→ hypit status/builds/logs 看进度
   → 关口B【抽帧 QA】看 任务/产物 面板（studio 已挂右栏）或 hypit media frames 出图核对
   → 5 成片：hypit get <build-id> --output <name> --to <路径> 导出
   → 关口C【成片验收】成片在对话交付，人验收
```

## 审核点交付
- 关口全在 **DSH 对话闭环**（客户只走对话、UI 只读非输入口）。
- 抽帧 QA：右栏挂 native studio（`:5179`），或 `hypit media frames`/`capture screenshot` 出本地图后用 `read_image` 看（DSH 对话里本地路径不内联，走 read_image 或 studio）。
- 成片：`hypit get` 导出到 `productions/<task>/` 后 present。

## 铁律
1. **build 前必过 pricing 预算批准**：hypit 按 Need 付费、build 一旦跑就花钱且耗时长（可到 1 小时）。pricing 成本 + plan 结构未经人批，绝不 build。**失败别盲目重跑整条多段 build**：单段实测成功率约 3/10 ⇒ 4 段全成 ≈1%，先按 monitor-contract §7 读 detail/jobs 判断，验链改用 1 段最小工程 `hypit_runs\productions\e2e-one\`。
2. **工程在 C:\dsh\hypit_runs、账在网关**：工程文件、产物、`.hypit` 状态都在 hypit_runs；生成腿一律经 `http://localhost:8100/api/hypit` → `gateway.invoke` 扣积分。**不 import D 的 Python、不点 D 的控制台、不碰 :8090/:8000**，也绝不让 hypit 绕开网关直连厂商。凭据只进 hypit 平台凭据存储，明文不落项目。
3. **右栏 = native studio 工作台，不是 D 控制台**：`hypit studio --run X --port 5179` 是 hypit 官方 SVML 工作台（源文件/任务/产物 + 预览/属性），**未 build 也能开**；iframe 必须写 `http://localhost:5179/`（Vite 只听 `[::1]`，写 127.0.0.1 必白屏）。它 per-run、Ctrl+C 停，且**能写回工程文件**⇒ 只 ops 自用、不给客户。细节与 presence 见 monitor-contract §4。
4. **会话绑定**：一条工程对应一个 `C:\dsh\hypit_runs` 下的 run 目录；在 `productions/<task>/` 记当前 run（跨会话靠档案，不靠 D）。
5. **档案层**：每条工程在 `productions/<task>/` 落 `BRIEF/ANALYSIS/TIMELINE/PROGRESS/DELIVERABLES`；产物本体留 hypit_runs，指针进 productions。
6. **解耦**：hypit 是独立工程化生产引擎，不做换脸超级复刻（super-clone）、不做 omni 竖屏广告。
7. **commit 有边界**：`hypit_runs` 的 `.hypit/`、`assets/`、`node_modules/` 已 gitignore（state/媒体体积大，别 commit）；但 **`packages/`（自写 provider 源码）、`productions/`（工程文件）、`hypit.runtime.json`、`package*.json` 要入库**——Profile 里只有凭据引用，无明文。
