# PRD：执行中进度分组（chat progress）—— 让 DSH 跑任务时像 Qoder 那样"一组逐行进度"

状态：设计定稿（未开工，代码已主动回退）｜日期：2026-10-05｜负责人：产品
关联：`docs/PRD-delivery-loop.md`（不停手）、全局行为层 `$DSH_HOME/AGENTS.md` §4/§5
任务清单：`docs/tasks-chat-progress.json`

## 1. 客户看到的差别（两张截图对出来的）

| 维度 | Qoder | DSH 现状 |
|---|---|---|
| 一轮执行 | **一个组**：组头「正在执行中 · 37s」，组内每步一行「图标 + 动作 + 目标 chip」，随进度**逐行追加** | 无组：旁白、工具卡、报错原文**平铺在正文流里**，一路往下糊 |
| 模型旁白 | ≈0（动作即界面） | 实测 51 条会话：**含正文的 assistant 消息 ÷ 步数 = 平均 0.42**（单条会话最高 0.5），正文平均 200~850 字，单会话最多 56 次「接下来我/我先…」式预告。⚠️ 更正：早前一版写"122 条=每步一条旁白"是**误读** —— `assistant/message` 多数只装 tool-call（每步一条是内核正常节奏），密度必须按 text 块算 |
| 失败 | 进卡片状态，不打断阅读 | `编辑 · Error: old_string was not found in "…local_compose.py"` 原文摊在正文里 |

## 2. 根因（实测 file:line，不是"没这功能"）

DSH **有**精简视图，而且默认就是开的：
- `@deepseek-ai/dsh-client-ui-chat/lib/client.js` → `DEFAULT_TRANSCRIPT_VIEW_MODE = "compact"`；界面入口 `:8344 transcriptView.setMode(mode)`；设置键 `ui-chat / transcriptView`。
- 但折叠的生效条件在 `:1581`：
  `processWindowReady = processSpec && processPresentation && **compactTranscript** && answerAnchorSeq !== null && turn === spec.turn && **turnClosed** && !historyIncomplete`
  ⇒ 🔴 **`turnClosed`：只有这一轮结束了才折**。正在跑的 10~20 分钟里全程平铺 —— 这就是"界面乱"发生的时间窗。

好消息：内核给了**语义钩子**，我们不需要碰哈希 class：
`data-chat-turn`（轮次分组天然存在）、`data-chat-flow-kind`（`assistant-step` / `reasoning` / `message` / `context` / `skill` / `plugin` / `user` / `steering` / `turn-process` / `turn-error` / `turn-tail` / `boundary` …）、`data-turn-process-member` / `-hidden` / `-answer`。

## 3. 目标 / 非目标

**目标**：正在跑的那一轮也收成一组 —— 组头「执行中 · N 步 · Xs」（可点开看全部步骤），组内每步一行，**最后一步始终露出来**当"它在动"的证据。

**非目标（🔴 越界即违规）—— 这 7 条与 `docs/tasks-chat-progress.json` 的 `hard_boundaries` 逐字一致，两份必须同步改**
1. 不做第二套折叠：官方那条 turn-process 折叠行【活着】（不带 data-turn-process-hidden）的轮次立刻让位
2. 不把当前动作藏掉 —— 最后一步必须始终可见（全收起来客户以为卡死）
3. 不搬 React 节点：只 setAttribute + 组首插一个头
4. 不依赖 CSS Modules 哈希 class（xzv4MW_/hWmORq_ 升级必漂）
5. 不改官方 compact 语义、不改 turnClosed 判据（要走路线 B 另立任务）
6. 不靠「让模型少说话」充当根治：那是配套 T5，不是本体
7. 动 plugins/super-clone-ui/lib/client.js 前先 git diff --numstat 对一遍，只用精确插入式编辑，禁止整文件覆盖与 git checkout --

## 4. 落点：我们自己的插件（不碰内核、Ctrl+R 生效、可逆）

宿主：`plugins/super-clone-ui/lib/client.js`（已是 UI 皮肤 + DOM 后处理的宿主，`hideCmdOnce` / `injectHeroTabs` 同一套路）。
- `turnFoldTick()`：按 `data-chat-turn` 分组（实测：一条流条目都带这个属性，82/82）→ 只处理**最后一组且官方折叠行没活着**的 → 组内非 KEEP、非答案的 kind 打 `data-ws-fold="1"`（CSS 隐藏）→ 组首插一个 `.ws-turn-head`（`执行中 · N 步 · Xs` + 展开/收起）→ 折叠时把"被我们自己折上的"仍算作可见（W9）。
- `KEEP = [user, steering, turn-tail, turn-error, turn-process, system-prompt, boundary]` —— **未知新 kind 默认收**（宁可少刷屏，也别让正文又糊满）。
- 触发：`MutationObserver(body, childList+subtree)` **同步**重挂（回调在微任务里跑，赶在浏览器画下一帧之前把头补回来）+ `ctx.setInterval(run, 1000)`（`inject` 要加 `"timer"`，否则 guard 直接拒）。⚠️ 早先那版用 `setTimeout(250)` 节流 ⇒ 内核每次重绘抹掉我们的头，浏览器会先画出"展开"再折回"收起"，整页高度来回跳（= 用户点名的"运行中上下闪缩"）。
- 稳态必须**零 DOM 写入**（W11）：只在值变了才写（`setIfDiff`/`rmIfHas`），"当前动作"那条在循环里就跳过、不能先打上再摘掉；否则我们自己的写入会点着 observer 转圈。
- 展开选择记在内存 `Map`（W12），不记在会被内核抹掉的头节点上 —— 否则用户点开"展开看过程"，下一次重绘就悄悄收回去了。
- 2026-10-05 已在活的内核页面（`127.0.0.1:43129`，另一条正在跑的会话）实测：组头「执行中 · 9 步 · 32s」、8 条收起 1 条露着、点开=展开全部且文案变「收起」、再点收回、秒数自己走。剩 T4 的肉眼截图要你在 DSH 窗口按一次 Ctrl+R（工作区插件重载即生效）。

## 5. 路线 B（后续可选，别先做）

把 `:1581` 的 `turnClosed` 放宽 ⇒ 官方行为，最正统。⚠️ **未验证**它为什么要等轮次结束（很可能为了答案锚点稳定：中途折叠会把正在生成的正文一起折掉）。要动就得：注册进 `rebuild_weeksir_desktop.py` 的 `PATCHES` + 过 `verify_kernel_consistency.py`，并接受内核升级漂移。

## 6. 配套：旁白源头（治正文密度 0.42）

全局层 `$DSH_HOME/AGENTS.md` 加 §6 输出纪律：正文只在**阶段完成 / 需要批准 / 判死**三种时刻说话；禁止"接下来我会…"式预告（这条同时掐掉"谎报完成"的言语来源）；禁止把工具报错原文抄进正文。
量具 `scripts/audit-output-style.mjs`（已跑，51 条会话基线：**正文块 ÷ 步数 = 0.42**、均字数 200~850、单会话预告最多 56 次）：统计正文块密度、均字数、预告句式、正文含 Error。
⚠️ 账本是**多帧 zstd 拼接**，`zlib.zstdDecompressSync` 一次只解第一帧 ⇒ 必须按魔数 `28 B5 2F FD` 切帧循环解（已实测：429 帧 / 1.56MB）。

## 7. 一条必须记住的风险：这个文件有并发写入者

2026-10-05 08:23，`plugins/super-clone-ui/lib/client.js` 被**另一个会话**改写（1320 → 1040 行），其中 `injectNativeAccountSwitcher`（原生账号切换器）整块被删（HEAD 里还在，工作区 0 命中）—— 不是我删的，也未恢复。
⇒ 动这个文件前必须先 `git diff --numstat` 对一遍，并且**只用精确插入式编辑**，禁止整文件覆盖、禁止 `git checkout --`（那会连带回滚别人的改动 + 抹掉昨天的 dock 活）。
✅ **已拍板（2026-10-05）**：账号切换器**接受移除** —— 账号绑定占位方案推翻，UI 还原标准模式选择；`session-bind` 类 skill 保留，只是不再占 UI 位。T7 关闭。

## 8. 验收（都要能自动判）

| # | 断言 | 怎么测 |
|---|---|---|
| W1 | 跑动中的一轮被收成一组，组头有 `N 步` 与秒数 | 起一条多步任务，截图 / DOM 断言 `.ws-turn-head` 存在且文本匹配 |
| W2 | **最后一步始终可见**（不许全收） | 同一时刻断言组内 `data-ws-fold` 未打在最后一条上 |
| W3 | 用户消息与报错不被收进组 | 构造含 `turn-error` 的一轮，断言其无 `data-ws-fold` |
| W4 | 官方折过的轮次我们不抢（幂等） | 让位信号 = 组内存在 kind=`turn-process` **且不带** `data-turn-process-hidden` 的行。⚠️ 那个属性的含义是 `controllerInactive`（内核源码 :1597）= 官方**没**折 —— 拿"有 hidden 属性"当"官方已折"是反的，2026-10-05 线上折叠永不生效就是这个判据 |
| W5 | 源码只用 `data-*` 选元素，零哈希 class | 闸扫源码（正则匹配 `xxxxxx_` 形式 class） |
| W6 | 不搬 React 节点 | 闸数 `insertBefore(` 出现次数 == 1（仅组首插头） |
| W7 | 内核契约漂移要红 | 闸断言 chat bundle 里 `data-chat-turn` / `data-chat-flow-kind` / `data-turn-process-hidden` / `turnClosed` / `DEFAULT_TRANSCRIPT_VIEW_MODE = "compact"` 仍在 |
| W8 | 旁白密度下降 | `node scripts/audit-output-style.mjs`：正文块 ÷ 步数 ≤ 0.3（基线 0.42，未达标） |
| W9 | **折叠不自我抖动**：折上的条目会被 CSS 藏掉，判据不能再拿"实时可见数"当依据 | 行为闸【逐跳】采样（连跳 5 次每跳都保持折着 + 恰好一个头）。⚠️ 只看终态会被奇偶蒙对——旧版就是这样假绿过 |
| W10 | 正文答案永远不收（`data-turn-process-answer` 排除） | 行为闸构造带答案的一轮，断言该条无 `data-ws-fold`、组头计数不含它 |
| W11 | **稳态那一跳零 DOM 写入**（运行中不上下闪缩） | 行为闸钉住时钟后数 DOM 写入数 == 0；"当前动作"那条必须在循环里跳过，不能先打上再摘掉（那样每跳写 2 次 ⇒ 真浏览器里点着自己的 observer 转圈） |
| W12 | **展开态跨内核重绘不丢**（执行中点开就能一直看着） | 行为闸模拟重绘（抹掉头 + 清掉标记）后 tick，断言头回来且仍处展开；实现上选择记在内存 Map，不记在会被抹掉的头节点上 |

## 9. 验收 ↔ 任务映射（改任何一边都要同步，闸会红）

机器可读版：`docs/tasks-chat-progress.json`（7 个任务，每个都带 `verify`）。

| 任务 | 内容 | 覆盖的验收 | 里程碑 |
|---|---|---|---|
| T1 | `turnFoldTick` 分组 + 组头 + 收起非当前步（已落码 `turn-fold-20261005d`） | W1 W2 W3 W4 W9 W10 W11 W12 | P0 |
| T2 | MutationObserver **同步**重挂（不再 setTimeout 节流）+ 1 秒计时 | W1（秒数走字） W11 W12 | P0 |
| T3 | `scripts/check-chat-progress.py` + `-dom.mjs`（源码纪律 + 行为真值表 + 内核契约漂移） | W5 W6 W7 W9 W10 W11 W12 | P0 |
| T4 | 真界面点一遍 + 截图（活的内核页面已验：组头「执行中 · 9 步 · 32s」/ 8 收 1 露 / 点开展开 / 秒数自走；只差 DSH 窗口 Ctrl+R 后那张肉眼截图） | W1 W2 W4 | P0 |
| T5 | 全局层 §6 输出纪律 + `audit-output-style.mjs` 密度量具 | W8 | P1 |
| T6 | 路线 B：放宽 `turnClosed`（先坐实为什么要等） | §5 结论 | P2 |
| T7 | 查 08:23 谁删了账号切换器并决定恢复/接受 | §7 | P1 |
