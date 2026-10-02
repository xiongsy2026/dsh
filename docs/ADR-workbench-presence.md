# ADR：右栏工作台归属与 presence 契约

状态：待批准
日期：2026-10-02
影响面：`plugins/ops-gateway/lib/client.js`、`D:\video-skill\backend\hypit_bridge.py`、`D:\video-skill\backend\model_admin.py`、`.dsh/skills/*/references/monitor-contract.md`
不动：video-ui(:8765)、hypit 引擎本身、DSH 内核

## 背景 / 问题

右栏现在挂着三个工作台：omni 流水线（video-ui :8765）、Hypit Studio（:5179，经 8100 代探）、模型网关（:8100）。
**同一个症状已经第三次复发**：在 omni 会话里弹出了 Hypit 工作台。

本次实测证据（2026-10-02）：

| 证据 | 内容 |
|---|---|
| 登记文件 `D:\video-skill\.gateway_studio.json` | `{"sessions":["session-e82b8456…","session-0d0607eb…"],"session":"…0d0607eb","run":"hypit-e2e","port":5179,"ts":1790908335}` |
| `GET 127.0.0.1:8100/api/hypit/studio`（不带 session=运维视角）的 `seen[]` | `session-e82b8456…` **3 秒前还在轮询**，而它同时就在 `owners` 里 |
| `hypit_bridge.studio_claim()` | **累积语义**（`sessions` 只增不减），`studio_release()` 只在停 studio 时调 |
| `client.js:142-161` | 只有上升沿 `openTab`，**代码里不存在任何 close 路径** |
| `client.js:125` | 「模型网关」启动即无条件 `openTab(MGKEY)`，完全不看会话 |

⇒ 服务端判"它是主人"是**按规则正确执行**的。错的是规则本身。

## 根因

**"归属"没有唯一事实源，三条流水线各判各的**：

- omni 判的是**资源带主人** —— `run.json` 上有 `session` 标，会话换了自然就不匹配 ⇒ 会自愈。
- hypit 判的是**服务被登记过** —— claim 文件只增不减、无过期 ⇒ **一条会话只要跑过一次 hypit，就永久拥有那个页签**，哪怕它现在在写 omni。
- 模型网关**不判** —— 无条件开。

再叠加**只开不关**，于是每次修一个局部症状都会在下一次以新形态复发。

### 三次错误的共同形状（写下来是为了不再第四次）

1. 第①次：拿"端口活着"当跨会话 presence 信号。→ 修法是加主人登记。
2. 第②次：登记对象选错了层——登记了**会话↔服务**，而服务是单实例无会话概念的；应该登记**会话↔资源**。→ 修法是加多主人。
3. 第③次：多主人 + 只开不关 = 永久产权。

⇒ 三次都只在补症状，**没有人先定义"一条会话当前属于哪条流水线"这个概念**。这次先定义，再写代码。

## 决策：一条契约 + 一张表 + 一个对账循环

### 契约

```
show(workbench, sid) = 归属 ∧ 存活 ∧ 关口      // 三者缺一即 false
```

| 项 | 定义 | 唯一事实源 |
|---|---|---|
| **归属** | 这条会话**当前**在做这条流水线 | **资源上的 session 标**，不是服务登记。omni=`run.json.session`；hypit=studio 正在服务的那个 run 的登记项；super-clone=task.session |
| **存活** | 该引擎的工作台现在真能打开 | omni=8765 有本会话的 run；hypit=登记端口探活 up **且 studio 自报的 run == 登记的 run** |
| **关口** | 值得占右栏了 | omni=`flags.script_approved`（方案批准）；hypit=agent 起了 studio（=明确要给人看）；super-clone=TIMELINE 批准 |

`ops` 类工作台（模型网关）**不参与归属判定**，标 `scope:"ops"` 全局可见，但**必须走同一张表**——不许再写 `openTab` 旁路。

### 三条硬规矩（缺一条不许合）

1. **归属必须落在资源上，且带 TTL**。无 TTL 的登记 = 永久产权 = 这次的 bug。心跳就是右栏每 4 秒的探测，TTL 定 30 分钟。
2. **presence 必须双向对账**。真→假要 `closeTab(sid, key)`，假→真才 `openTab`。DSH dock 侧能力已核实：`@deepseek-ai/dsh-client-ui-sidebar-right` 暴露 `closeTab(sessionId, tabId)`、`focusTab`、`openTabIn`，且**页签状态本来就按 sessionId 存**（`dsh-web-frontend` 布局 store 的 action 集：openTab/insertTab/closeTab/moveTab/reorderTab/focusTab/focusPane/split/merge）。注册表里不提供 close 路径的工作台直接报错。
3. **presence 口必须要求 `session` 参数**。不带 session 一律只回运维视图（`reason:"ops-view"`），**永不用于弹页签**。这条专门用来杜绝"端口活着"再被当信号。

### 单一实现

`client.js` 里一张表，presence 循环遍历它，open/close 统一在一处：

```js
const WORKBENCHES = [
  { key: KEY,   scope: "session", probe: probeOmni  },
  { key: HKEY,  scope: "session", probe: probeHypit },
  { key: MGKEY, scope: "ops",     probe: probeOps   },
];
```

以后加 super-clone = 加一行。**"各写一套"在结构上不再可能**，这是比"下次注意"更硬的防线。

## 服务端改动

`hypit_bridge.py`：

- 登记结构从 `{sessions:[…], session, run, port}` 改成 **`{runs: {"<run>": {session, port, ts}}}`** —— run 才是被拥有的资源，一台 studio 同一时刻只服务一个 run。
- `studio_claim(session, run, port)` 写 `runs[run]`；`studio_status(session)` 判定改为"**该 studio 当前服务的 run 的登记项里 session == 你** 且 ts 未过期"。
- **"当前服务的 run" 不信任登记文件，问 studio**：实测 `GET /__studio/session` 回 `{"revision":9,"source":{"path":"productions\\hypit-e2e\\assemble.svml", …}}` —— studio 自己报告它在服务哪个源文件。桥取 `source.path` 参与判定（**只取 path，不回传 `source.text`**，那是完整源码）。这样 agent 换 run 重启 studio 而忘记重新 claim 时，presence 会自然变假，不会错弹。
- 加过期回收：每次读顺手删 `ts` 超 TTL 的项。
- **迁移策略：旧格式（有 `sessions` 没有 `runs`）一律判无主**，不做猜测式兼容。宁可少弹一次，不可错弹一次。

`model_admin.py`：`/api/hypit/studio` 缺 `session` 参数时不再回 `up`，改回 `{up:false, reason:"ops-view"}`，运维字段（`owners`/`seen`）保留。

## 验收（可跑，不靠肉眼）

新增 `scripts/check-workbench-presence.py`，用假 session id 打 presence 口，断言四条：

1. 未 claim 的会话 → `false`
2. claim 过、但 studio 现在服务的是**别的 run** → `false` ← **这次的 bug 就是这一条**
3. TTL 过期 → `false`
4. 主人会话 + run 匹配 + 端口活 → `true`

约定挡不住复发，**断言能**。这个脚本之后是改 presence 逻辑的回归门。

## 不做

- **不**合并成"一个统一工作台页签、内部路由"——已定过"UI 还是之前的 UI"，合并必然改样。
- **不**给 hypit studio 加鉴权、**不**改 hypit 引擎——不是这层的问题。
- **不**在本期做"每会话独立 studio 端口池"：共用端口现在只会让两条**都跑 hypit** 的会话互相看见，归属修好后至少不会串到 omni。列为 P2。
- **不**顺手改 omni 的 presence 语义（它是对的），只是把它那段逻辑搬进统一表。

## 分期

| 期 | 内容 | 触碰 |
|---|---|---|
| **P0** | presence 契约 + 双向对账 + claim 改 run 归属 + TTL + 验收脚本 | `client.js`、`hypit_bridge.py`、`model_admin.py`、新增 `scripts/check-workbench-presence.py`；8100 重启一次（**已查 `.gateway_jobs.sqlite` 无 queued/running，最近 5 条全 succeeded ⇒ 当前是安全窗口**） |
| P1 | super-clone 接同一张表（task 带 session + TIMELINE 关口） | 同上 + super-clone skill 契约 |
| P2 | 每会话 studio 端口租约（`5179+n`），presence 口回 `port`，iframe URL 由它拼 | `hypit_bridge.py`、`client.js` |

## 相关

- 上游决策：`docs/AGENT-FRAMEWORK.md`（UI 只展示+批准）、`docs/ADR-pipeline-server.md` Gate 0（origin 白名单 + 执行端点鉴权）
- 记忆条目：`dsh-stage-ui-architecture`（两条 presence 铁律）、`super-clone-hypit-pipeline`
