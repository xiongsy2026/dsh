# Weeksir 电商 Agent（DSH）

用 DSH Desktop 当 Agent 运行时。先定闭环框架，再一条 skill 一条实现。

- 框架：[`docs/AGENT-FRAMEWORK.md`](docs/AGENT-FRAMEWORK.md)
- 总 PRD：[`docs/PRD-weeksir-platform.md`](docs/PRD-weeksir-platform.md)
- 难单切片：[`docs/PRD.md`](docs/PRD.md)
- Stage 三栏 UI：[`docs/PRD-stage-ui.md`](docs/PRD-stage-ui.md)（右栏常驻看板 + 审核）
- 工作约定：[`AGENTS.md`](AGENTS.md)（路由 / 意图登记 / 网关契约 / 交付铁律）

## 新机器 clone-and-run

本仓库 = **工作区 + 后端全量代码**，三层架构：

```
DSH Desktop（Electron 客户端，单独安装）  ← 右栏 UI / 会话 / agent 沙箱
        ↕ 127.0.0.1:8100（model_admin：网关管理台、表单、批准、意图登记）
仓库 C:\dsh（本仓库）                        127.0.0.1:8090（app：流水线引擎）
  ├── backend/        ← 后端全部代码（8090 app.py + 8100 model_admin.py + gateway.py + services/ + pipeline/ + frontend/）
  ├── scripts/        ← agent 侧命令契约（intent.py / handoff.py / deliver_pack.py / 回归闸 check-*.py）
  ├── .dsh/skills/    ← skill 源码（form:/contract: 是判据唯一源）
  ├── plugins/        ← DSH 右栏 UI 插件
  └── productions/    ← 任务档案（运行时生成，媒体不入 git）
```

```powershell
git clone <私有库地址> C:\dsh
cd C:\dsh
.\setup.ps1            # Python 依赖 + backend\.env 模板 + ffmpeg/node/playwright 检查
# 把真实密钥填进 backend\.env（模板每条 key 都有注释）
.\start-all.ps1        # 起 8090 + 8100，并打开 DSH Desktop（工作区自动指到本仓库）
```

参数：`start-all.ps1 -NoBackend`（只开 DSH）/ `-NoDsh`（只起后端）/ `-PythonExe <解释器>`。
单独起后端：`backend\start-backend.ps1`（日志写 `backend\_svc_*.log`，Ctrl+C 停掉）。

**仍需单独装的外部件**：
1. DSH Desktop 客户端本体（`%APPDATA%\dsh-desktop\`）—— 本仓库不含 Electron 包
2. `backend/hyperframes/`（4.3GB Node 工具，不入 git）：从旧机器复制，或设置 `HYPERFRAMES_CLI` 指向其 `packages/cli/bin/hyperframes.mjs`
3. `backend/models/`（ProPainter 等权重，运行时按需下载）
4. ffmpeg / node / playwright chromium（setup.ps1 会检查）

## 打开（已有环境）

1. 启动 DSH Desktop，工作区指到本仓库根目录
2. 新会话 `/weeksir`
3. 未上架的环会被告知，不会空跑生成

## 目录

```
C:\dsh
  AGENTS.md               工作约定（判据唯一源之一）
  setup.ps1               新机器安装
  start-all.ps1           一键启动（后端 + DSH）
  backend/                后端：app.py(8090) model_admin.py(8100) gateway.py services/ pipeline/ frontend/
    start-backend.ps1     只起后端
    .env.example          密钥模板（复制为 .env 填真实值）
    requirements.txt
  scripts/                agent 命令契约 + 回归闸
  .dsh/skills/<skill>/    各 skill（SKILL.md 的 form:/contract: 段是唯一源）
  docs/                   PRD / ADR / FLOW
  productions/<task>/     任务档案 LOOP.md / BRIEF.md / ...
```
