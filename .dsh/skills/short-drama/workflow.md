# 短剧带货总工作流（short-drama）执行手册

本文件是 `/short-drama` 的**可执行工作流定义**。它把 11 个 AI STUDIO 洋子 Skill 归纳成一条带货短剧推理链，内置 RAG 本地知识库检索。

## 触发

- 用户显式点 `/short-drama`，或说"做一部 XX 产品的剧情带货短剧"且明确要走剧情设计（不是口播/轮播）。
- 口播/轮播/卖点直给带货 → 不走本 Skill，走 `/omni`。
- 复刻爆款片 → 走 `/super-clone`。

## 调用首步：意图登记（像 /omni 一样可调用）

用户点 `/short-drama` 后，**第一步先登记意图**，再初始化项目：

```
python scripts/intent.py film <任务名>
```

- 登记 `film` 档（与 `ai-film-router` 同档）：排他挡掉 omni / hypit 右栏台（对方回 `intent-is-film`）。
- film 档不弹专门右栏台：进度看每阶段在对话里的完整展示（00_project/session-checkpoint.md 是机器可读的状态源，
  以后要加右栏台读它就行），08 提示词用 `present()` 交付。
- 登记完之后才初始化项目文件夹（按 `project-template/` 复制进 `productions/<task>/`）。

## 角色分工（本 Skill 是编排层，不重复专业 Skill 内容）

| 层 | 负责 | 不负责 |
|---|---|---|
| **short-drama（你）** | 判入口、串 01→08、RAG 检索契约、版本登记、关口呈现 | 生成各阶段正文（交专业 Skill） |
| **film-synopsis/characters/worldbuilding/treatment/screenplay/lira/acting/cinedance** | 各自阶段成果 | 跨阶段、调 RAG、出片 |
| **film-knowledge-bridge** | RAG 检索 5403 份剧本/方法书 | 不写创作成果 |
| **film-expert-bridge** | 专家增强（打戏/大招/镜头语言工具箱） | 不替换 9 主 Skill |

## 项目初始化

每个带货短剧任务在 `productions/<task_id>/` 建（模板在 `C:\dsh\.dsh\skills\ai-film-router\references\project-template\`）：

```
00_inbox/                     用户素材、参考图、产品图（本轮指定为准的）
00_project/
  brief.md                    产品 / 卖点 / 目标时长 / 平台
  project-status.md           入口模式、RAG 选择、专家增强状态、视频模型选择
  routing.md                  本阶段允许读取
  artifact-registry.md        版本登记（草稿/已确认-当前/已过期-禁止使用）
  artifact-state.json        机器状态（防版本冲突写入锁）
  reference-memory.md        RAG 确认的参考方法（001、002...）
  session-checkpoint.md      阶段进度检查点
01_story/
02_script/
03_assets/
04_performance/
05_shots/<场次ID>/
  scene-registry.md
  scene-timing-vNNN.md
  shot-plan-vNNN.md
  scene-space-map-vNNN.md
  continuity-ledger-vNNN.md
  reference-map-vNNN.md
  visual-continuity-audit-vNNN.md
06_prompts/<场次ID>/
  seedance-2.5-vNNN.md
  seedance-2.0-vNNN.md
  minimax-h3-vNNN.md
```

## 0-步骤：入口门禁（只问一次）

```
A 从零（只有产品+卖点）  B 已有完整剧本  C 部分资料  D 高手镜头优化
```

带货场景通常 A 或 C。入口判完，**只问一次 RAG 选择**：

```
要不要先查本地剧本知识库对标结构？
A 不调用  B 智能匹配 3-5 部爆款短剧+方法书  C 只查方法书
```

选 B/C：`film-knowledge-bridge` 检索 → 候选卡 → 确认 1-3 部 → 定向精读 → 写 `reference-memory.md`。
选 A：保存"已选择 A-关闭"，原 9 Skill 照旧，**不检索**。

## 依次推进 01→08

每个阶段四步循环（agent 履行）：

```
a. 加载专业 Skill（film-synopsis → film-characters → ... → film-cinedance）
b. 按 Skill 规则生成当前阶段成果
   （只读上一阶段"已确认-当前"文件 + 用户本轮要求 + reference-memory 中匹配范围的参考方法）
c. 在聊天完整展示成果（不要只给文件路径）
d. 等用户拍板：
   "确认本阶段" → 登记"已确认-当前" → 推进下一阶段
   "修改本阶段" → 保留旧版，生成 vNNN+1
   "回到 0X 改第 Y 场" → 切回 0X，改完把下游 06/07/08 受影响场次标记"已过期-禁止使用"
```

**专业 Skill 的最小输入与标准输出**（由 `ai-film-router` 调度）：

| 阶段 | 专业 Skill | 最小输入 | 标准输出 |
|---|---|---|---|
| 01 概念锚定 | `film-synopsis` | 创作需求或一句话想法 | `01_story/synopsis-vNNN.md` |
| 02 角色圣经 | `film-characters` | 大纲，或已有故事与角色方向 | `01_story/characters-vNNN.md` |
| 03 世界观（可选） | `film-worldbuilding` | 复杂世界规则需求 | `01_story/worldbuilding-vNNN.md` |
| 04 分场大纲 | `film-treatment` | 大纲/剧本梗概与目标时长 | `01_story/treatment-vNNN.md` |
| 05 剧本深度融合 | `film-screenplay` | 用户已有剧本资产，或已确认的大纲+角色+分场 | `02_script/screenplay-vNNN.md` |
| 06 Lira 图像资产 | `film-lira-assets` | 最终剧本 + 生产拆解，或明确的角色/场景/道具资料 | `03_assets/assets-vNNN.md` |
| 07 ACTING 表演 | `film-acting` | 当前场景/镜头、角色信息；资产参考按需 | `04_performance/<场次ID>-acting-vNNN.md` |
| 08 镜头规划+提示词 | `film-cinedance` | 整场戏或当前镜头 + 资产与表演资料 | `05_shots/*` + `06_prompts/*` |

带货短剧通常跳过 03（现实题材不建世界观）、07 可选（对白少时不单独做表演）。

## RAG 知识库检索契约（01 或任意阶段需要对标时）

**只读不写**，检索走 `film-knowledge-bridge/scripts/retrieve_stage_sources.py`：

```
1. 写检索画像 JSON（参考 film-knowledge-bridge/references/profile-example.json）
   - stage: 当前阶段（01-synopsis / 04-treatment / 08-cinedance...）
   - problem: 一句话"本轮要解决的创作问题"（不复制完整剧本）
   - queries: 2-3 组（中文词 + 英文词 + lane 路 + path_contains 分类目录）
   - lane_limits: 每条 lane 最多几条候选
2. 跑检索：
   python C:\dsh\.dsh\skills\film-knowledge-bridge\scripts\retrieve_stage_sources.py --profile <画像.json>
3. 出候选卡（00_project/knowledge-cards/<阶段>-candidates.md）：
   - 每条候选写"可迁移方法" + "禁止借用的具体内容"（人物身份组合、核心诡计、完整事件链、标志场面、长段对白）
   - 展示在聊天，让用户选 1-3 部精读
4. 用户确认后写第二张画像（selected_documents + deep_read_selected=true），再跑一次检索做定向精读
5. 精读证据卡（00_project/knowledge-cards/<阶段>-reference.md）展示后，用户确认方法
6. 写进 00_project/reference-memory.md：
   - 编号：参考方法001、002...
   - 使用范围（默认"故事创作 01-05"；资产 06；表演 07；镜头 08；全流程 01-08）
   - 预期落点（具体写到哪一场/哪一镜）
   - 禁止借用内容
```

**知识库物理位置（已配置好）**：
- 根：`C:\dsh\knowledge\剧本知识库\全球热门与获奖影视剧本知识库`
- FTS5 主库：`99-索引与检索规则\curated-screenplay-methods.sqlite`（5403 文档 / 12.8 万片段，trigram + BM25）
- 配置：`C:\dsh\.dsh\skills\film-knowledge-bridge\references\knowledge-base.json`
- 增量索引：`python .../update_knowledge_index.py`（新资料放 `90-隔离与待审/新加入待分类` 后触发）

## 08 CINEDANCE 的三道内部门（带货短剧核心推理）

`film-cinedance` 内部**不拆 08A/08B/08C**，按顺序跑：

1. **增强选择门**（首次进入 08）
   检测剧情特征：普通带货文戏 → 推荐 00 镜头语言工具箱 + 08-A 导演顾问；打戏 → 08-H；玄幻大招 → 08-N。
   带货短剧通常选 C"照原流程"。选择写入 `00_project/expert-memory.md`，后续场次继承。

2. **拍法与镜头确认门**
   输入整场戏时：`scene-registry.md` + `scene-timing-vNNN.md` + `shot-plan-vNNN.md` + `scene-space-map-vNNN.md` + `continuity-ledger-vNNN.md`。
   完整展示给用户，让用户增删/换景别/换机位/改运镜，**确认后才登记"已确认-当前"，禁止在此之前出批量提示词**。

3. **分镜图绑定与视觉质检门**
   每个正式镜头编译前必须绑定一张实际可读的分镜图或首帧图（`reference-map-vNNN.md`），并生成 `visual-continuity-audit-vNNN.md`。
   换机位时重新生成新机位首帧，不把旧角度尾帧硬塞。

三道门过后才出**模型提示词**（按场次、按模型单独保存）：

```
06_prompts/<场次ID>/seedance-2.5-vNNN.md     Seedance 2.5（最长 30s、多参考、精细适配可选）
06_prompts/<场次ID>/seedance-2.0-vNNN.md     Seedance 2.0（保留原结构）
06_prompts/<场次ID>/minimax-h3-vNNN.md       MiniMax H3（保留 H3 结构）
```

每个提示词必须写明：所依据的导演方案版本、镜头清单版本、空间图版本、连续性版本、参考图路径、视觉质检版本、资产与 ACTING 版本、"重新编译 / 继承"。

**禁止**：用"上一镜头、继续、同上、仍然、保持原样"等上下文依赖词。视频模型不知道聊天里的前镜头，必须写出实际物理连续状态。

## 出片交接

`short-drama` 工作流到 08 出提示词就绪即结束。**不自动调生成平台**，提示词由用户喂给：

- Higgsfield（Seedance 默认假设的环境）
- 即梦 / Seedance 平台
- 或接 DSH 现有 `seedance-*` 12 个片型 Skill 做提示词模板交叉质检（不自动打通，需用户点名）

## 关键铁律（专业 Skill 已写好，这里只列最高优先级）

1. 一次只跑一个专业 Skill，不串味。
2. 跨阶段只读"已确认-当前"。草稿、未确认想法、聊天示例不得带入下一阶段。
3. RAG 只迁移方法，不抄剧情。
4. 知识库方法按使用范围自动加载：故事默认 01-05；06/07/08 只读各自范围。
5. 每阶段成果版本化（v001、v002...），修改不覆盖。
6. 镜头清单确认后旧提示词立即作废，只重做受影响镜头。
7. 不自动调生成平台。
8. 08 出提示词前必须过三道门，不许跳过。
