# ADR：右栏工作台归属与 presence 契约

状态：P0 已落地（#31 服务端 / #32 插件 / #33 回归），P1/P2 未做
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
show(workbench, sid) = 意图                 // 只此一条
台里显示什么          = 引擎活着吗            // 死了就显示「引擎没在跑 + 怎么起」，不藏台
// （2026-10-02 定稿，取代原来的三条合取：把 存活/关口 当弹出条件，
//   直接后果就是用户实测的"打开会话什么都不弹"。它们只该决定台里的内容。）
```

| 项 | 定义 | 唯一事实源 |
|---|---|---|
| **归属** | 这条会话**当前**在做这条流水线 | **资源上的 session 标**，不是服务登记。omni=`run.json.session`；hypit=studio 正在服务的那个 run 的登记项；super-clone=task.session |
| **存活** | 该引擎的工作台现在真能打开 | omni=8765 有本会话的 run；hypit=登记端口探活 up **且 studio 自报的 run == 登记的 run** |
| **关口** | 值得占右栏了 | omni=`flags.script_approved`（方案批准）；hypit=agent 起了 studio（=明确要给人看）；super-clone=TIMELINE 批准 |

`ops` 类工作台（模型网关）**不参与归属判定**，标 `scope:"ops"` + `auto:false`：全局可【手动】开（右栏"+"里选得到），但【绝不自动弹】。**"全局可见"不等于"每条会话都弹一次"**——把内部控制台摆进每一条对话，在客户产品里不可接受（2026-10-02 第一版就踩了：照搬旧的"启动即 openTab"，你当场指出"全部对话都出现"）。`auto:false` 的表项完全不进对账循环（不弹也就不关）。

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
  { key: MGKEY, scope: "ops", auto: false, probe: probeOps },   // 不自动弹
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

## 实施记录（2026-10-02 · P0）

**跑法**：`PYTHONIOENCODING=utf-8 python scripts/check-workbench-presence.py`（22 条：A 函数层 15 + B 真 8100/真 studio 的 HTTP 层 7）、`node scripts/check-workbench-client.mjs`（9 条：假 ctx 驱动对账循环）。当前 **31/31 全绿**。

**两处与设计不同，都记下来：**

1. `closeIn(sessionId, tabId)` 确认存在（`SidebarRightController.closeIn`，注释写明"store 未 adopt 的会话什么都不做"），**但 tabId 是 dock store 生成的**：`placeTab → actions.openContent(sessionId,{kind,contentId:pageAddress(kind),…}, cb(tabId))`，插件面拿不到那个 id。⇒ 对账循环仍然**尽力调 `closeIn`**，但**正确性不依赖它**：页签体（`WorkbenchBody`）自己每 6 秒重算同一个谓词，判假就渲染**门禁**（显示 `not-owner`/`run-mismatch`/`studio-down` 等原因）而不是 iframe。所以"关不掉"最坏也只是留一个空页签，**绝不会把别人的引擎露在这个会话里**。
2. **`$DSH_SESSION_ID` 只有 DSH 自带 agent 执行时才由内核注入**（`_setup/dsh-harness/.agents/notes/archived/feature/2026-07-10-agent-session-identity-and-log-location.md:22`）。Qoder/ops 会话里 `env` 一个 DSH 变量都没有 ⇒ 契约改为：**拿不到就别 claim**（空 session 服务端直接拒），确实要让某个 DSH 会话看到，就从运维视图 `seen[]` 取它的 id 显式登记。omni 不受影响（总是 DSH agent 驱动），hypit 经常是 ops 会话驱动，所以只有 hypit 会踩。

3. **测试脚手架自己也会骗人**：第一版假 React 的 `createElement` 只生成描述符、不递归渲染函数组件 ⇒
   断言 8「body 未绑定渲染门禁」是**因为压根没进组件才通过**的假阳性。修成真递归渲染后补了反向断言
   10「拥有时 body 必须出 iframe、且端口来自 presence 口」——**单向断言能过的，双向都要过**。

**落地后的线上表现**：`.gateway_studio.json` 里那两个 9-30/10-02 留下的僵尸主人已随旧格式判无主而消失（release 到空表会整个删文件）；三条正在轮询的 DSH 会话全部拿到 `not-owner` ⇒ **此刻没有任何会话弹 Hypit 页签**，直到有人按新契约 claim。这是有意的 fail-closed。

**插件生效条件**：`client.js` 经符号链接进 DSH，**改完要重载 DSH 窗口（Ctrl+R）**；服务端改完要重启计划任务 `WeeksirModelAdmin`（重启前先查 `.gateway_jobs.sqlite` 有无 queued/running）。

## 已知代价（导出会打满本机）

`hypit build` 的渲染腿是 hyperframes 无头 Chrome（实测一次导出起 **49 个 node/chrome 进程、CPU 100%**，约 2~4 分钟）。期间 **8100 会忙到不回包**（实测 `curl` 2 秒即失败、listen backlog 溢出）⇒ 右栏 presence 会短暂拿 `probe-failed`。这是本地渲染的固有代价，不是接线坏；**给客户之前必须把渲染挪到服务端或限并发**。导出条上已经把状态文案写成"渲染合成中（本地·约 1~4 分钟）"来提示等待。


## 落地后当天回修（2026-10-02 续·用户实测"打开会话什么都不弹"）

三个都是**我把 fail-closed 做得过头**导致的，逐条纠正：

1. **归属粒度错了**：第一版把 claim 做成"一个 run 只能一个主人"（矫枉过正——要修的 bug 是"全局只增不减"，不是"多人共看"）。实测有 2~3 个 DSH 窗口同时轮询 ⇒ 改成**归属 = （run, 会话）对的集合**，`runs:{run:{sessions:[…],port,ts}}`；`claim` 追加观看者、`release <会话>` 只把自己从名单里划掉，名单空了才删那个 run。
2. **TTL 没接心跳**：第一版 TTL 从 **claim 时刻**起算 ⇒ 一条 30 分钟的 build 会半路自己关页签。改成**探测即心跳**（`studio_status(session)` 顺手把该会话名下 run 的 `ts` 顶到现在，30 秒内不重复落盘）。断言 A5b/A5c 锁住这两条。
3. **关口不该挡住"人主动要看"**：`script_approved` 这道闸是用户 2026-10-01 定的（commit 5234d90，避免立项/写剧本阶段占右栏），**保留不动**；但历史 run 没打会话标 ⇒ 自动判定永远认不出来。⇒ 门禁里加一个 **「我就是要看，直接显示」**：只翻本地 `forced`，用 `fallback` 地址出台，**不写任何归属登记**（导出条因此仍拿不到 `run`、按钮保持 disabled —— 看可以，烧钱不行）。

顺带纠正我本轮的一个错误结论：我说过"8 个 run 里 0 个有 `script_approved`"，那是**读了 run.json 顶层 `flags`**（不存在该字段）；真值在 `review.script_approved`，由 `stage_flags()` 现算。实际 8 个里 **4 个过了 Gate1.5**，但这 4 个都是打标机制之前的老 run（无会话标），而带标的 2 个还没过闸 ⇒ 交集为空，这才是"omni 什么都不弹"的真实原因（另加 8765 当时没在跑）。

**回归从 33 条涨到 73 条**（presence 28 / client 16 / export gates 29）。


## 定稿（2026-10-02 第二次修订·取代上面所有关于"何时弹"的表述）

**弹不弹只看意图，一条合取都没有。** 相应修订：

| 原规矩 | 定稿 |
|---|---|
| `show = 归属 ∧ 存活 ∧ 关口` | `show = 意图`；`up`（引擎活着）只决定台里显示什么 |
| 关口：omni=`script_approved`、hypit=studio 起了 | 降级为**台内信息**（omni 的 `ready` 字段），不参与弹出 |
| claim 带 30 分钟 TTL，过期撤销 | **意图持久**，超时只影响 `up`；登记封顶 20 个 run、只裁最旧 |
| 归属 = 会话拥有服务 | 不变：归属 = **(run, 会话) 对的集合**，同 run 可多人共看 |
| presence 口必须带 session | 不变（不带只回 `ops-view`） |

**判定合并成一个口**：`GET /api/workbench/presence?session=<sid>` 一次回 `{omni:{intent,up,url,run,ready}, hypit:{intent,up,port,run,reason,how_to_start}}`。
⇒ 顺带修掉一个藏了很久的真 bug：**omni 的意图原先由插件直连 `:8765/api/runs` 判，而 video-ui 不发 CORS 头 ⇒ 跨源 fetch 读不到响应 ⇒ `show` 恒 false**。现在意图由 8100 读盘上 `video_runs/*/run.json` 的 `session` 字段得出——意图来自资源，不来自"服务是否活着"。

**对账状态按 `(会话, 页签)` 记**，切会话**不清记忆**：若清掉，"切走 → 那边意图撤了 → 切回来"就等不到下降沿，那个台会永远赖在页签栏（断言 9b 锁这条）。

**新会话**：两条流水线都无意图 ⇒ 一条都不弹。不需要"猜意图"，这正是产品要的行为。
**老 run 没打会话标**（打标点机制之前那 4 条有进度的 run）⇒ 认不出意图；页签体给一个「我就是要看，直接显示」出口，只翻本地状态、不写登记，所以拿不到 run、导出条也不给。


## 🔴 铁律（第三次修订·最终版）：意图是一个显式动作

用户把目标定成铁律：**「新会话不要出现 UI；确认是 omni 还是 hypit 还是后面的别的 skill，明确意图后出现 UI。」**

之前两版都靠**痕迹**推断意图（hypit=claim 过的 run、omni=盘上带会话标的 run），两头都出错：
**该弹的不弹**（agent 从不执行那个"可选"的 claim）、**不该弹的乱弹**（一条会话同时留着两条流水线的痕迹）。
⇒ 意图必须是一个**有名字的命令**，而不是一个可选步骤：

```
python D:/video-skill/backend/hypit_bridge.py intent "$DSH_SESSION_ID" <skill> [任务名]
python ... intent-clear <会话id>      # 做完了收口
python ... intent-show                # 运维：现在谁挂着哪条流水线
```

判定优先级与排他性：

| 情况 | 结果 |
|---|---|
| 有显式登记 | **只**挂 `skill` 匹配的那一个台；其余回 `reason:"intent-is-<别的skill>"` |
| 无登记、有痕迹 | 退到佐证（hypit=claim 过的 run／omni=盘上带该会话标的 run） |
| 都没有 | 新会话 ⇒ `no-intent` ⇒ **零 UI** |

- **登记了但还没产出也照样挂台**（早期视图可见，不必等已建 run）。
- `studio-claim` 会顺手把该会话钉成 `hypit`（兜底），但契约要求显式登记。
- 状态文件 `D:/video-skill/.gateway_intent.json`（已进 .gitignore）；`ops` 视图回 `intents` 清单。
- 回归：`scripts/check-intent-rule.py`（13 条）。**线上实测**：nose 那条会话登记 `hypit` 后
  `omni=intent-is-hypit`、`hypit=intent:True, up:False, reason:not-claimed`；未登记会话两台全 `no-intent`。
- 提交 D `5d5e2f2` / C `56e1056`（`AGENTS.md` 新增「右栏 UI 铁律」节 + omni/hypit 两个 skill 契约同步）。

**教训（写下来防再犯）：标着"可选"的步骤等于不存在的步骤。** 铁律必须落成一条命令 + 一条断言。

## 🔴 第四次修订（最终·intent-only）：砍掉"退到佐证"那一档

三修留了个兜底："没登记就退到痕迹佐证（omni=盘上有带本会话标的 run／hypit=claim 过的 run）"。
**这个兜底本身就是猜意图，而且永不过期。**

2026-10-02 实测事故：用户装了个纯提示词 skill（GPT-5.8 生视频 Prompt 库，没有引擎、不该挂任何台），
切回那条会话却在弹 omni 工作台。查 presence 服务端：`intent:true, source:"run-tag",
run:"20261002-174602-SPARK烟花棒30s剧情带货-4362", idle_age:4061s` —
是 **67 分钟前**跑过的那条 omni run 带着该会话的标，被"佐证"档认成了主人。
run 文件不会自己过期 ⇒ 这条会话以后每次切回来都弹，跟它现在在做什么完全无关。

定稿规则（只剩两层，且"弹不弹"这一层只有一个来源）：

| 情况 | 结果 |
|---|---|
| 显式登记成某 skill | **只**挂那一个台；其余回 `intent-is-<别的skill>`；登记了但还没产出也照样挂（早期视图） |
| 没有显式登记 | **零 UI**。盘上的 run / 旧 claim 只是**显示用历史**，永不触发页签 |

- 痕迹仍然要可见：hypit 在 `no-intent` 时回 `stale_claim`（告诉你以前 claim 过什么），
  omni 登记后回 `run` / `ready` / `idle_age`。**可见 ≠ 拥有** — 这一档的边界就在这里。
- 引擎动作 `studio-claim` 会顺手 `set_intent(…, "hypit")`：它本身就是显式动作，不算猜，保留。
- `source` 字段只剩 `"explicit"`，`"run-tag"` 作废。
- **纯提示词 / 知识库类 skill 一律不登记意图**（`.dsh/skills` 里 seedance-* 那 13 个 prompt 库、film-* 写作类）。
  没有引擎 ⇒ 挂了就是空壳；就算误登记，两台也各回 `intent-is-<它>` 而不弹。
- 回归：`check-intent-rule.py` **15 条**（新增 5/5b/5c 直接断"清掉意图⇒痕迹不算数"，5d 断未知 skill 不挂台）
  + `check-workbench-presence.py` **40 条**（A15 整段重写：先断"有 run 没登记⇒不挂"，再登记断"挂"）。
  改 presence 逻辑必须先过这 55 条。

## 配套加固（同日·把"该弹的不弹"和"判定口单点"关掉）

砍掉佐证后剩下的风险不在判定层，在它的上下游。两条都落码并实测：

**① 意图登记绑到必然动作上（不再只靠 agent 记性）**
- 依据：线上登记簿至今 0 条是 agent 自己登记的 ⇒ 铁律写在散文里等于没写（跟"可选步骤=不存在的步骤"同一个坑）。
- omni：`store.create_run()` 建完 run 顺手 `set_intent(session, "omni", run名)`（`scratch/video-ui/video-ui/store.py`）。
  只认 `session` 非空；不带 session 的老式调用不凭空登记任何人；`set_intent` 抛异常**也绝不能让建 run 失败**（右栏台是附带品）。
- hypit：`studio_claim` 早就自登记，两个流水线现在都有锚。
- 端到端实测：隔离端口 8799 的临时实例 `POST /api/run` ⇒ 登记簿出现 `{"session-E2E-ANCHOR":{"skill":"omni","task":"<run名>"}}`，run.json 的 session 一致。

**② 判定口（8100）单点：三种 verdict 要分清，别把"没回话"当"没意图"**
- 旧行为：fetch 失败 → `j || {}` → `intent:false` → **8100 一抖，每条会话的台全被关掉**，恢复后再弹（闪断）。
- 新行为（`client.js` pv-20261002f）：`presence()` 回 `null` = 没回话；对账遇到 `null` 保持上一次判定不动，
  **只有哑过 90 秒才认输关掉**（仍然 fail-closed，不会永远挂着）；一次判定都没拿到时页签体显示"判定口没回话 + 怎么起 8100"，
  不再谎报"这条会话不属于这个台"。
- 常驻服务自愈：`WeeksirModelAdmin` / `WeeksirVideoUI` 各加 **1 分钟重复时间触发器**，启动器加端口守卫（已在监听就 `exit /b 0` ⇒ 触发器幂等），
  `ExecutionTimeLimit` 置 `PT0S`（原来 72 小时会把常驻进程掐掉）。
  ⚠️ **`RestartCount=99` 是假保险**：启动器是 `.cmd` 里直接拉 `pythonw`，杀掉 pythonw 后 cmd 以 0 退出 ⇒ 计划任务判定"成功结束"，压根不走失败重启。
  实测杀掉 8100：`RestartCount` 4 分钟没反应；换 1 分钟触发器后 **60 秒内自己回来**。8765 同样实测（重启 5 秒后自己回来）。

**回归总数 105 → 121**：`check-intent-rule.py` 15 · `check-workbench-presence.py` 40 · `check-workbench-client.mjs` 28（新 12b/12c/13/13d/14 全是哑掉场景）
· `check-intent-anchor.py` 9（新脚本）· `check-export-gates.py` 29。

⇒ 教训：**判定层的可靠性来自"谁必然执行它"，不来自"规则写得多严"**。规则再严，没人执行就是"什么都不弹"；
而"取不到判定"和"判定为否"必须分开，否则一个单点抖动会伪装成业务结论。

## 活会话审计（把"严格匹配"变成可复查的动作，不是针对某条会话调出来的）

`scripts/audit-presence-live.py`：枚举 DSH 真实会话目录（`%APPDATA%/dsh-desktop/harness/sessions/<工作区>/session-*`），
逐条打判定口，同时列出三份独立证据源（意图登记簿 / `video_runs` 的 run 标 / studio claim 名单），
按三条不变量核对：**弹台 == 登记**、**没登记 ⇒ 零台（痕迹不算）**、**任何会话最多一个台**。

2026-10-02 实测：29 条真会话全部一致（登记簿 1 条 = 复刻会话 ⇒ 只弹 Studio；4 个会话有 omni run 痕迹但无登记 ⇒ 全部不弹，
正是四修要的效果）。泛化由 `check-intent-rule.py` 的 ⑦ 矩阵锁住：`omni/hypit` 只弹各自那一个，
`super-clone / product-video / film / 纯提示词类`（还没台或本来不该有台）⇒ **零台**，连续切换不漏旧台。回归 27 条。

⚠️ 审计中发现（未定责）：`D:/video-skill/.gateway_studio.json` 整份不见了，里面原本挂着 2 个 assemble 的陈旧 claim。
可疑入口是 `studio_release(session, run)` 在两个参数都为空时**整条作废**（`hypit_bridge.py:196-201`），
裸跑 `hypit_bridge.py studio-release`（漏了会话 id）就会走到那一路。现下无实际影响（陈旧 claim 本来就不该弹台），
但这是个脚枪：待拍板的收口 = 无参数时必须显式带 `--all`，否则报错并提示怎么按会话释放。

## 相关

- 上游决策：`docs/AGENT-FRAMEWORK.md`（UI 只展示+批准）、`docs/ADR-pipeline-server.md` Gate 0（origin 白名单 + 执行端点鉴权）
- 记忆条目：`dsh-stage-ui-architecture`（两条 presence 铁律）、`super-clone-hypit-pipeline`
