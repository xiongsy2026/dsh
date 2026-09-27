# Weeksir 电商 Agent（DSH）

用 DSH Desktop 当 Agent 运行时。先定闭环框架，再一条 skill 一条实现。

- 工作区：`C:\dsh`
- 框架：[`docs/AGENT-FRAMEWORK.md`](docs/AGENT-FRAMEWORK.md)
- 总 PRD：[`docs/PRD-weeksir-platform.md`](docs/PRD-weeksir-platform.md)
- 难单切片：[`docs/PRD.md`](docs/PRD.md)
- Stage 三栏 UI：[`docs/PRD-stage-ui.md`](docs/PRD-stage-ui.md)（右栏常驻看板 + 审核）
- 生成管线仍在：`D:\video-skill`（jobs 调用，客户不进 8090）

## 打开

1. 启动 DSH Desktop，工作区指到 `C:\dsh`
2. 新会话 `/weeksir`
3. 未上架的环会被告知，不会空跑生成

## 目录

```
C:\dsh
  AGENTS.md
  docs/AGENT-FRAMEWORK.md
  .dsh/skills/weeksir/     总路由（已有）
  productions/<task>/LOOP.md
```

