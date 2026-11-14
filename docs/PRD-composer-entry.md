# PRD：对话框里加 /omni —— composer 常驻技能入口（skill chips）

状态：设计定稿（未开工）｜日期：2026-10-05｜负责人：产品
关联：`docs/PRD-chat-progress.md`（同一宿主文件的并发纪律）、全局行为层 `$DSH_HOME/AGENTS.md`
任务清单：`docs/tasks-composer-entry.json`（4 个任务，每个都带 `verify`）
回归闸：`scripts/check-composer-entry.py`（本 PRD 的 T3 建，跑不过就别收工）

## 1. bug 现象（用户原话）

用户在首页点了 skill dock 的 /omni 卡片，然后问：

> 我点了这个 /omni，可以在对话框里面加一个 /omni 吗

同一个 /omni，两个界面待遇不同：

| 场景 | /omni 在哪 | 一步能点到吗 |
|---|---|---|
| 新会话首页 | skill dock 的 Omni 卡片（`SKILL_DOCK_META` omni，点击 = 填 `/omni ` 进输入框） | ✅ 一击 |
| 对话中（composer） | 只剩"技能"下拉：点按钮 → 弹菜单 → 选"Omni 出片" | ❌ 两跳；卡片整块撤掉 |

⇒ 诉求很朴素：**首页点得着的 /omni，对话框里也要一击点得着。**

## 2. 根因（实测 file:line，不是"没这功能"）

全部在 `plugins/super-clone-ui/lib/client.js`：

- `:778` `isHeroSession` —— skill dock 只在新会话首页出卡，**进对话整块撤掉**（":772 只在【新会话首页】出卡，进入对话就整块撤掉"是文件里的原话）。所以对话里没有 /omni 的直达位。
- `:222-227` `SKILLS` 下拉表硬编码 3 条（omni / super-clone / hypit），两处与实况脱节：
  - 🔴 **`hypit` 不是真命令**：`.dsh/skills/hypit-pipeline/SKILL.md` 的 `name: hypit-pipeline`，全目录没有叫 `hypit` 的技能 ⇒ 这条点下去填的 `/hypit ` 是空炮（表头注释自己写了"列表来源：SKILL.md 的 name 字段"，表却没照做）。
  - 首页 dock 宣传的 **hypit-pipeline / short-drama / video-dewatermark** 都不在下拉里——`SKILL_DOCK_META`（`:755-767`）有 5 条客户可见技能（`SKILL_DOCK_ALLOW` `:769` 就这 5 条），下拉只认 3 条。
- 现成的复用点都有，缺的只是"常驻入口"这一块：
  - `:230` `setSkillPrompt(prompt)` —— 填命令的标准动作（清旧 `/技能` 前缀 → 命令置前 → 描述在后）。
  - `:263` `buildNativeSkillPicker` + `:570` `mountNativeSkillPicker` —— 工具栏挂载套路（克隆原生按钮 className、`data-ws-skill-*` 标记、幂等判据 `[data-ws-skill="1"]`）。
  - `:612` `injectNativeSkillPicker` —— 300ms 节流 MutationObserver，新挂载器搭它的车即可，不新开 observer。

## 3. 目标 / 非目标

**目标**：对话中的 composer 工具栏加一排**常驻技能 chips**（/omni 打头），点一下 = `setSkillPrompt` 填命令，不经过下拉；顺手把 `SKILLS` 表对齐技能目录实况。

**非目标（🔴 越界即违规）—— 这 7 条与 `docs/tasks-composer-entry.json` 的 `hard_boundaries` 逐字一致，两份必须同步改**
1. 不做「敲 / 弹候选」的命令面板（要接管键盘事件与光标定位，另立任务）
2. 不把首页 skill dock 整块搬进对话里常驻 —— 卡片式 dock 仍只在首页，对话里只加轻量 chips
3. 不新增技能、不改任何 SKILL.md —— 只对齐 SKILLS 表与实况（客户可见面 = `SKILL_DOCK_ALLOW` 这一张表；2026-10-05 用户拍板加第 6 条 `prompt-reverse` ⇒ 名单以 `check-skill-dock.py` 的 APPROVED 为准，两处必须同改）
4. 不动 DSH 内核（@deepseek-ai/dsh-*）与官方 composer DOM 结构 —— 只插自己的节点
5. 不依赖 CSS Modules 哈希 class（升级必漂），只克隆原生按钮 className + data-ws-* 标记
6. 不改 setSkillPrompt 的命令置前语义（清旧 /技能 前缀、命令置前、描述在后）
7. 动 plugins/super-clone-ui/lib/client.js 前先 git diff --numstat 对一遍，只用精确插入式编辑，禁止整文件覆盖与 git checkout --

## 4. 落点与实现要点（给执行 AI：照这个干，别"先研究再说"）

宿主：`plugins/super-clone-ui/lib/client.js`（不动内核、Ctrl+R 生效、可逆）。

- **T1 常驻 chips**：新增 `buildSkillChips(templateBtn)` + `mountSkillChips()`，照抄 `buildNativeSkillPicker`（`:263`）的套路 —— 克隆 templateBtn 的 className 定外观、`data-ws-skill-chip` 标记、幂等判据（host 里已有 chip 容器就 return）、**禁止 cloneNode(true)**（保留 React state 引用会触发原下拉，`:284` 的老坑）。挂载位：`findComposerActionsHost`（`:562-565`）给出的发送按钮之前。chips 逐个点击 = `setSkillPrompt(s.prompt)`。
- **T2 表对齐**：`SKILLS`（`:222`）改成与实况一致的 5 条：omni、super-clone、`hypit` → **hypit-pipeline**（prompt `/hypit-pipeline `）、short-drama、video-dewatermark；顺序照 `SKILL_DOCK_META` 的 order。chips 与下拉**同源自这一张表**，不留两份名单。
- **T3 回归闸**：`scripts/check-composer-entry.py` —— ①源码纪律（零哈希 class、零 cloneNode(true)、`setSkillPrompt` 被 chips 复用）②锚点漂移（`:222` SKILLS、`:230` setSkillPrompt、`:263` buildNativeSkillPicker、`:778` isHeroSession 的符号仍在）③`git diff --name-only` 只允许 client.js + 闸本身。
- **T4 肉眼**：真界面点一遍 + 截图（DSH 窗口 Ctrl+R 后）。

## 5. 一条必须记住的风险：这个文件有并发写入者

2026-10-05 08:23，`plugins/super-clone-ui/lib/client.js` 被**另一个会话**整块改写（1320 → 1040 行），`injectNativeAccountSwitcher` 整块被删。⇒ 动这个文件前必须先 `git diff --numstat` 对一遍，**只用精确插入式编辑**，禁止整文件覆盖、禁止 `git checkout --`（会连带回滚别人的改动）。

## 6. 验收（都要能自动判）

| # | 断言 | 怎么测 |
|---|---|---|
| X1 | 名单只有一个源且对齐实况：`SKILLS` 由 `SKILL_DOCK_META` 派生，每条 id 都能在 `.dsh/skills/<id>/SKILL.md` 找到同名 `name:`（不再有 `/hypit ` 这种空炮） | 闸解析派生语句 + 逐条扫盘比对（A1~A6） |
| X2 | 技能选择只有一个入口：下拉仍在挂载（首挂 + 两处节流重挂 = 3 个调用点），且源码里不留 chips 残留/死代码 | 闸数调用点 + 扫 chips 标识（B1/B2） |
| X3 | 点技能真能写进输入框：框里已有文字时也生效；命令置前、清旧 `/技能` 前缀、描述在后三条语义不变 | 闸钉写入路径（不许再用 `execCommand('selectAll')`，改显式 Range）+ 语义行（B3/B3b/B4/B5）；空框与有字两种起点在真界面各点一次 |
| X4 | 改动不越界：内核包零改动、`plugins/` 下只许 super-clone-ui | 闸跑 `git diff --name-only` 比对（C1~C3）。⚠️ 不拿"SKILL.md 不许出现在 diff 里"当判据 —— 本仓库有并发写入者，那批在途改动会让它永远红（假保险） |

## 7. 验收 ↔ 任务映射（改任何一边都要同步，闸会红）

机器可读版：`docs/tasks-composer-entry.json`（4 个任务，每个都带 `verify`）。

| 任务 | 内容 | 覆盖的验收 | 里程碑 |
|---|---|---|---|
| T2 | `SKILLS` 表对齐 `.dsh/skills` 实况（hypit → hypit-pipeline，补 short-drama、video-dewatermark）+ setSkillPrompt 写入路径 | X1 X3 | P0 |
| T1 | ~~composer 常驻技能 chips~~ ⇒ 已撤销，并把「撤销」本身钉成闸 | X2 | 撤销 |
| T3 | `scripts/check-composer-entry.py` 回归闸（名单单源 + 下拉仍在 + 写入路径纪律 + 改动范围） | X1 X2 X3 X4 | P0 |
| T4 | ~~真界面点一遍 + 截图~~ ⇒ 随 T1 撤销；撤销状态由 X2 守着，不再需要截图 | X2 | 撤销 |

## 8. 定案（2026-10-05 复盘，用户拍板）

**chips 撤销，技能选择恢复成"只有一个下拉"。** 落地后用户原话："真服了，之前这个技能选择框都没问题" —— 5 个 chip 与下拉并排 = 同一份名单两个入口，且工具栏被挤爆（第一版还克隆了原生按钮的胶囊样式，5 个等宽大胶囊更乱）。撤销方式：删掉 chips 整块（含 CSS 与挂载器，不留死代码）+ 把 `mountNativeSkillPicker()` 的 3 个调用点原样还回去。

因此原来那四条验收（常驻 chip 存在、chip 点击语义、chip 幂等、chip 截图）**全部作废并从 §6 表里删除**；§6 现在只有 X1~X4，全部描述现存行为，其中 X2 专门钉「只有一个入口、chips 不许复活」。

保留下来的只有两处**不改布局**的修复：
1. `SKILLS` 由 `SKILL_DOCK_META` 派生（X3）—— 下拉内容从 3 条变 5 条，且不再有指向不存在命令的 `/hypit `；标签取自 dock 那张表（"Hypit 时间轴" → "Hypit 视频裂变"）。
2. `setSkillPrompt` 的写入路径：`execCommand("selectAll")` 在这个编辑器里选不中已有内容 ⇒ 输入框有字时点任何技能都没反应（空框才生效）。改成显式 Range 覆盖整个输入区再 `insertText`，命令置前/清旧前缀/描述在后三条语义一字未动。

⚠️ 若你要求连第 1 条也退回（下拉回到旧 3 条），代价是恢复那条点了没反应的 `/hypit ` 空炮命令 —— 一句话我就改，同样只动那一处。
