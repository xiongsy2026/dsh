---
name: session-bind
description: >
  新会话或未绑号时使用。给当前 DSH 会话选定项目目录和账号浏览器，
  写下 LOOP.md，并确保该账号的 MEMORY / SKILL_LOG 存在且只属于本号。
---

你只做会话绑定，不出片、不调 Wan。

## 要问清的（没有就问，不要猜别的号）

1. **资源管理器**：项目目录。默认 `C:\dsh`。
2. **账号浏览器**：本店 `account_id`（短英文或数字，如 `shop-demo`）。一会话一个浏览器；该浏览器可登同一店的抖店/千川/飞鸽，不混店。
3. **任务名**：`productions/<task_id>/` 的文件夹名。没有就用日期+短词，如 `2026-09-26-clip`。

列出 `accounts/` 下已有目录（不要打开其它号的 MEMORY.md / SKILL_LOG.md）。用户选已有号或新建号。

## 落盘

若 `accounts/<account_id>/` 不存在则创建，只写空壳，禁止从其它号拷记忆：

- `PROFILE.md`：可先几乎为空（本阶段出片现问卖点）
- `MEMORY.md`：标题 `# MEMORY <account_id>`，下面空白
- `SKILL_LOG.md`：标题 `# SKILL_LOG <account_id>`
- `browser.json`：先占位

```json
{
  "account_id": "<account_id>",
  "user_data_dir": "accounts/<account_id>/chrome-profile",
  "tun": null,
  "platforms": []
}
```

创建 `productions/<task_id>/LOOP.md`：

```markdown
# LOOP
- member: <account_id>
- account: <account_id>
- workspace: C:\\dsh
- stage: produce
- skill: session-bind
- platforms: []
- approved: []
- batch: []
```

再写一行 `PROGRESS.md`：已绑定 account / task。

往 **本号** `SKILL_LOG.md` 追加：

```
- time: <ISO>
  project: productions/<task_id>
  skill: session-bind
  result: ok
```

## 硬规则

- 只读写 `accounts/<本 account_id>/` 与 `productions/<本 task_id>/`
- 不要读 `accounts/` 下其它文件夹里的 MEMORY 或 SKILL_LOG
- 绑完告诉用户：下一句可以说要超级复刻 / Hypit / Omni（后两条 skill 上架后才真正能跑）
