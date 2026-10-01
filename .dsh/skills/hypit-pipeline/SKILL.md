---
name: hypit-pipeline
description: >
  Hypit「video-as-code」工程化出片（WeekSir 指挥层）。引擎 = hypit 原生 npm CLI（全局），
  项目/工作区固定在 C:\dsh\hypit_runs，**与 D:\video-skill 零关联、不走网关、不用 D 的控制台**。
  本 skill 是薄编排层：Agent 直接在 C:\dsh\hypit_runs 里 authoring SVML/SVRun、跑 hypit CLI
  （check/plan/pricing/build/studio），只在关口请示人。右栏 = hypit 自带 studio（per-run 检视器）。
whenToUse: >
  用户要工程化复刻一条参考视频（语义重建/不限时长/video-as-code）、或用 hypit 做片。
  单段换脸/换产品超级复刻走 super-clone；产品图生竖屏广告走 omni。本 skill 是独立引擎，不依附 super-clone。
---

# Hypit 工程化出片（驱动 hypit 原生 CLI）

你是 Hypit 工程化出片 Agent。**引擎 = `hypit` 命令行（全局 npm 装的 @hypit/hypit），工作区固定在 `C:\dsh\hypit_runs`。不碰 D:\video-skill、不调任何 HTTP 服务。**
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
2. **HypiHub 凭据已配**（build 必需）：`hypit auth login hypihub.default --runtime C:\dsh\hypit_runs\hypit.runtime.json`。
   凭据存机器 home `AppData\Local\Hypit`，**不进项目文件、不进 D**。没配只能 check/plan，不能 build。
3. **Worker 起**：`hypit runtime up --workspace C:\dsh\hypit_runs`（`hypit runtime status` 看）。
4. 查就绪：`hypit doctor --workspace C:\dsh\hypit_runs`（缺凭据会报 RUNTIME_CREDENTIAL_MISSING）。

## 驱动序列（细节见 monitor-contract §2）
```
0 doctor 就绪 → 1 在 hypit_runs 里 authoring .svml/.svrun（看参考片→写结构）
   → 2 hypit check <src>（lint）→ 3 hypit plan <src>（看结构·不花钱）
   → 关口A【方案+预算批准】hypit pricing <src> 出成本，build 花钱前把 plan+pricing 摆给人，人批才继续
   → 4 hypit build <src> --follow（异步·花钱·长）→ hypit status/builds/logs 看进度
   → 关口B【抽帧 QA】hypit studio --run <x>.svrun --port 5179（右栏挂这个）或 hypit media frames 出图核对
   → 5 成片：hypit get <build-id> --output <name> --to <路径> 导出
   → 关口C【成片验收】成片在对话交付，人验收
```

## 审核点交付
- 关口全在 **DSH 对话闭环**（客户只走对话、UI 只读非输入口）。
- 抽帧 QA：右栏挂 native studio（`:5179`），或 `hypit media frames`/`capture screenshot` 出本地图后用 `read_image` 看（DSH 对话里本地路径不内联，走 read_image 或 studio）。
- 成片：`hypit get` 导出到 `productions/<task>/` 后 present。

## 铁律
1. **build 前必过 pricing 预算批准**：hypit 按 Need 付费、build 一旦跑就花钱且耗时长（可到 1 小时）。pricing 成本 + plan 结构未经人批，绝不 build。
2. **一切在 C:\dsh\hypit_runs**：工程文件、产物、`.hypit` 状态都在这；**绝不调 D:\video-skill、不走 :8090/:8000**。引擎是全局 CLI、凭据在机器 home。
3. **右栏 = native studio，不是 D 控制台**：studio 是 per-run 临时进程（agent 起 `hypit studio --run X --port 5179`，Ctrl+C 停）；不是常驻仪表盘，别当剪映 NLE。
4. **会话绑定**：一条工程对应一个 `C:\dsh\hypit_runs` 下的 run 目录；在 `productions/<task>/` 记当前 run（跨会话靠档案，不靠 D）。
5. **档案层**：每条工程在 `productions/<task>/` 落 `BRIEF/ANALYSIS/TIMELINE/PROGRESS/DELIVERABLES`；产物本体留 hypit_runs，指针进 productions。
6. **解耦**：hypit 是独立工程化生产引擎，不做换脸超级复刻（super-clone）、不做 omni 竖屏广告。
7. **别 commit hypit_runs**：state/产物体积大，已在 .gitignore；只 commit skill 文档与代码。
