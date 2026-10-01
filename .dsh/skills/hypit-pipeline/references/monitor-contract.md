# Hypit 工程化出片 · CLI 驱动契约（monitor-contract）

WeekSir 薄编排层驱动 **hypit 原生 CLI**（全局 `@hypit/hypit` v0.2.9），工作区固定 `C:\dsh\hypit_runs`。
**与 D:\video-skill 零关联**：不调 :8090/:8000、不用 D 的 `hypit_workspace.py`/控制台/网关。命令形态均对 `hypit --help` 实测。

引擎三层（只有工作区在 C:\dsh）：
- **CLI 本体** = 全局 `AppData\Roaming\npm\node_modules\@hypit\hypit`（`hypit paths` 的 Distribution）
- **工作区/project state** = `C:\dsh\hypit_runs`（Project state `C:\dsh\hypit_runs\.hypit`，Profile `hypit.runtime.json`）
- **凭据/host state** = `AppData\Local\Hypit`（机器 home，**不进项目、不进 D**）

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

## §2 驱动时序

```
0 hypit doctor → 就绪？否→ auth login / runtime up
1 在 C:\dsh\hypit_runs 里 authoring：看参考片→写 .svml/.svrun（结构、素材槽、时间轴）
2 hypit check <src>            过 lint
3 hypit plan <src>             看结构（免费）
── 关口A 方案+预算批准 ──  hypit pricing <src> 出成本；plan+pricing 摆给人，批了才 build（铁律1）
4 hypit build <src> --follow   花钱、长；轮询 hypit status <id> / builds / logs
── 关口B 抽帧 QA ──  hypit studio --run <x>.svrun --port 5179（右栏）或 media frames 出图核对
5 hypit get <id> --output <name> --to productions/<task>/   导出成片
── 关口C 成片验收 ──  对话里 present，人点头
```

## §3 关口（比 omni 多一道钱闸）
- **A 方案+预算批准**：build 花钱前必过（hypit 按 Need 付费）。pricing 是唯一成本预估口。
- **B 抽帧 QA**：native studio 或 media frames。
- **C 成片验收**。
- 商用合规/反 AI 幼稚腔审核线沿用 [[omni-delivery-contract]] §5.5/§5.6（创意/分镜/口播通用）。

## §4 右栏挂载（native studio，非 D 控制台）
- pipeline-view 的「Hypit Studio」tab iframe 指 **`http://127.0.0.1:5179/`**（agent 起 `hypit studio --run X --port 5179` 后才有内容）。
- ⚠️ studio 是 **per-run、临时进程**：没在跑就白屏；Ctrl+C 停。它是"看某条成片"的窗口，**不是制作中仪表盘**，也**不是拖拽 NLE**（改内容靠改 SVML 文本）。
- presence 门控**暂关**（syncHypitTab 已 return）：没有像 video-ui 那样可廉价轮询的常驻 API；靠 agent 在 build 后起 studio、人手动开/刷新 tab 看。将来要做自动弹，需一个"studio 是否在 :5179"的轻量探测（studio 无 CORS，fetch 探不了，得另想信号）。

## §5 会话/任务绑定
- 一条工程 = `C:\dsh\hypit_runs` 下一个 run 目录；跨会话靠 `productions/<task>/`（BRIEF/ANALYSIS/TIMELINE/PROGRESS/DELIVERABLES）记当前 run，不靠 D、不靠 HTTP session 参数。
- 产物本体留 hypit_runs（已在 .gitignore，别 commit）；只把成片 `hypit get` 导出到 `productions/<task>/`。

## §6 前置与坑
1. **没 auth 不能 build**：`RUNTIME_CREDENTIAL_MISSING` = 未配 HypiHub；只能 check/plan。auth 要人做（账号凭据）。
2. **build 长且花钱**：可到 1h、按 Need 计费；pricing 未批绝不 build（铁律1）。
3. **studio 需已 build 的 .svrun**：没 build 打不开；端口固定 5179 便于右栏挂。
4. **runtime up 才出片**：只 init 不 up，build 无 Worker。
5. **别引 D**：任何 `:8090/:8000/hypit_workspace.py/D:\video-skill\hypit-workspace` 都不该再出现在本流程里（那是旧的 D 封装，已弃用）。
6. **上游 skill 会被 npx 覆盖**：authoring 语法参考在 `.dsh/skills/hypit/`（只读）；本驱动契约只在 `hypit-pipeline/`，别写进上游。
