# PRD：执行中进度分组（chat progress）—— 让 DSH 跑任务时像 Qoder 那样"一组逐行进度"

状态：设计定稿（未开工，代码已主动回退）｜日期：2026-10-05｜负责人：产品
关联：`docs/PRD-delivery-loop.md`（不停手）、全局行为层 `$DSH_HOME/AGENTS.md` §4/§5
任务清单：`docs/tasks-chat-progress.json`

## 1. 客户看到的差别（两张截图对出来的）

| 维度 | Qoder | DSH 现状 |
|---|---|---|
| 一轮执行 | **一个组**：组头「正在执行中 · 37s」，组内每步一行「图标 + 动作 + 目标 chip」，随进度**逐行追加** | 无组：旁白、工具卡、报错原文**平铺在正文流里**，一路往下糊 |
| 模型旁白 | ≈0（动作即界面） | **每步一条**：实测一条会话 7 轮 / 123 步，`assistant/message` 有 **122 条**（`tool/call` 156）⇒ 1.28 : 1 |
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
1. 不做第二套折叠：官方已打 data-turn-process-hidden 的轮次立刻让位
2. 不把当前动作藏掉 —— 最后一步必须始终可见（全收起来客户以为卡死）
3. 不搬 React 节点：只 setAttribute + 组首插一个头
4. 不依赖 CSS Modules 哈希 class（xzv4MW_/hWmORq_ 升级必漂）
5. 不改官方 compact 语义、不改 turnClosed 判据（要走路线 B 另立任务）
6. 不靠「让模型少说话」充当根治：那是配套 T5，不是本体
7. 动 plugins/super-clone-ui/lib/client.js 前先 git diff --numstat 对一遍，只用精确插入式编辑，禁止整文件覆盖与 git checkout --

## 4. 落点：我们自己的插件（不碰内核、Ctrl+R 生效、可逆）

宿主：`plugins/super-clone-ui/lib/client.js`（已是 UI 皮肤 + DOM 后处理的宿主，`hideCmdOnce` / `injectHeroTabs` 同一套路）。
- `turnFoldTick()`：按 `data-chat-turn` 分组 → 只处理**最后一组且未被官方折叠**的 → 组内非 KEEP 的 kind 打 `data-ws-fold="1"`（CSS 隐藏）→ 组首插一个 `.ws-turn-head`（`执行中 · N 步 · Xs` + 展开/收起）。
- `KEEP = [user, steering, turn-tail, turn-error, turn-process, system-prompt, boundary]` —— **未知新 kind 默认收**（宁可少刷屏，也别让正文又糊满）。
- 触发：`MutationObserver(body, childList+subtree)` 节流 250ms + `ctx.setInterval(later, 1000)`（`inject` 要加 `"timer"`，否则 guard 直接拒）。
- 代码已写过一版（109 行）并**主动回退**，形态待你排期；回退原因：先出文档，且该文件当时正被另一个会话改（见 §7）。

## 5. 路线 B（后续可选，别先做）

把 `:1581` 的 `turnClosed` 放宽 ⇒ 官方行为，最正统。⚠️ **未验证**它为什么要等轮次结束（很可能为了答案锚点稳定：中途折叠会把正在生成的正文一起折掉）。要动就得：注册进 `rebuild_weeksir_desktop.py` 的 `PATCHES` + 过 `verify_kernel_consistency.py`，并接受内核升级漂移。

## 6. 配套：旁白源头（治 122 条 `assistant/message`）

全局层 `$DSH_HOME/AGENTS.md` 加 §6 输出纪律：正文只在**阶段完成 / 需要批准 / 判死**三种时刻说话；禁止"接下来我会…"式预告（这条同时掐掉"谎报完成"的言语来源）；禁止把工具报错原文抄进正文。
配闸 `scripts/check-output-style.py`：解会话账本统计 —— 每轮 `assistant/message` 条数 / 步数 比值（目标 ≤0.3）、正文是否含 `Error:` 原文、是否含预告句式。
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
| W4 | 官方折过的轮次我们不抢（幂等） | 已带 `data-turn-process-hidden` 的组：我们的头不存在、无残留标记 |
| W5 | 源码只用 `data-*` 选元素，零哈希 class | 闸扫源码（正则匹配 `xxxxxx_` 形式 class） |
| W6 | 不搬 React 节点 | 闸数 `insertBefore(` 出现次数 == 1（仅组首插头） |
| W7 | 内核契约漂移要红 | 闸断言 chat bundle 里 `data-chat-turn` / `data-chat-flow-kind` / `data-turn-process-hidden` / `turnClosed` / `DEFAULT_TRANSCRIPT_VIEW_MODE = "compact"` 仍在 |
| W8 | 旁白密度下降 | `check-output-style.py`：`assistant/message` ÷ `step/start` ≤ 0.3 |

## 9. 验收 ↔ 任务映射（改任何一边都要同步，闸会红）

机器可读版：`docs/tasks-chat-progress.json`（7 个任务，每个都带 `verify`）。

| 任务 | 内容 | 覆盖的验收 | 里程碑 |
|---|---|---|---|
| T1 | `turnFoldTick` 分组 + 组头 + 收起非当前步（已写过 109 行·主动回退） | W1 W2 W3 W4 | P0 |
| T2 | MutationObserver 节流 + 1 秒计时 | W1（秒数走字） | P0 |
| T3 | `scripts/check-chat-progress.py`（源码纪律 + 内核契约漂移） | W5 W6 W7 | P0 |
| T4 | 真界面点一遍 + 截图 | W1 W2 W4 | P0 |
| T5 | 全局层 §6 输出纪律 + `check-output-style.py` 密度闸 | W8 | P1 |
| T6 | 路线 B：放宽 `turnClosed`（先坐实为什么要等） | §5 结论 | P2 |
| T7 | 查 08:23 谁删了账号切换器并决定恢复/接受 | §7 | P1 |
