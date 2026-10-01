# Weeksir Agent 框架（DSH 上如何跑通电商闭环）

本阶段：**本地全流程、无 weeksir.com 账号。** 先把框架钉死，再一条 skill 一条 skill 实现。  
总产品口径见 [`PRD-weeksir-platform.md`](PRD-weeksir-platform.md)。

运行时只用 DSH 已有件：Agent Loop、Agnes 3.0 Flash、`tool-skill`、`user-questions`、`tool-fs`、`tool-jobs`、`tool-bash`/`pwsh`、`tool-subagent`。不另写 Agent 内核，不打开 8090 给客户。

---

## 1. 框架在解决什么

闭环不是一个巨型 skill，是 **一条任务状态机 + 一组可替换 skill**。

```
客户说话
  → weeksir 路由（读 LOOP + 已启用 skill）
  → 提名下一环，user-questions 征求同意
  → 加载该环 SKILL.md
  → 写工作区文件 / 必要时 jobs 调 Python
  → 更新 LOOP 当前环、产出路径
  → 回到路由，直到客服或任务结束
```

任意一环没实现，路由只能说「这一环还没上架」，不能假装做完。这样可以 **一个个实现** 而不拆框架。

---

## 2. 工作区就是记忆

DSH 工作区固定 `C:\dsh`。

```
C:\dsh\
  AGENTS.md                          路由宪法（短）
  docs/AGENT-FRAMEWORK.md            本文件
  docs/PRD-weeksir-platform.md
  .dsh/skills/
    weeksir/SKILL.md                 总路由（始终先加载）
    super-clone/                     视频共用工具（下一批实现）
    hypit/
    omni/
    …其余环节按需加目录
  accounts/                          本机账号档案（无网站登录）
    <account_id>/                    一号一份，记忆互不串
      PROFILE.md                     店、品类、口吻、禁词
      browser.json                   user-data-dir、TUN、出口
      MEMORY.md                      该账号会员的长期记忆（只本号可读）
      SKILL_LOG.md                   哪些项目/会话调用过哪些 skill
  productions/<task_id>/
    LOOP.md                          状态机：当前环、账号、已同意项
    BRIEF.md
    ANALYSIS.md
    SCHEME.md                        选定的生成方案
    TIMELINE.md                      视频切段（若需要）
    ASSETS.md                        产出清单（千川 / 自然流路径）
    ADS.md                           投放计划与结果
    CS.md                            客服线程
    PROGRESS.md
  assets/                            产品图、脸图
  references/                        原片、对标片
```

**LOOP.md 是框架的中枢。** skill 不互相直接调用；做完一环只更新 LOOP，由 `weeksir` 决定下一环。

LOOP 最小字段：

```markdown
# LOOP
- member: <member_id>        # 会员（本阶段可与 account 相同；接网站后用 weeksir customer_id）
- account: <account_id>      # 本会话绑定的浏览器环境
- workspace: C:\dsh
- stage: plan | collect | review | scheme | produce | ads | feedback | cs | done
- skill: <当前或下一个 skill id>
- platforms: []
- approved: []
- batch: []
```

---

## 2.1 会话 = 项目目录 + 浏览器（矩阵预备）

DSH 默认按 **项目目录** 隔离会话（工作区文件、productions、skill）。电商还要再绑一层 **浏览器环境**：

| 一会话里选什么 | 是什么 |
| --- | --- |
| 资源管理器 | 本项目目录（`C:\dsh` 或以后的客户工作区），管素材、LOOP、产出 |
| 账号浏览器 | 自研指纹环境：一个 Chromium + 一条 TUN。给这个会话专用 |

约定：

1. **一个 DSH 会话 ↔ 一个项目目录配置 ↔ 一个浏览器环境。**
2. **一个浏览器里可登录多个平台账号**（同一店铺主体：抖店后台、千川、飞鸽客服等）。不要把不同店铺塞进同一浏览器。
3. 指纹浏览器本阶段是为后面的 **账号矩阵 skill** 打底，不是客户自己去点「创建环境」。
4. 新会话开始时，路由询问（或沿用 LOOP）：用哪套浏览器、哪套项目目录。
5. **项目调用过的 skill 写入该账号会员记忆，且账号之间隔离。**

### 记忆隔离

每个 `account_id`（接网站后挂在 `member_id` 下）有自己的记忆目录，**禁止跨号读写**。

会话里每加载或完成一条 skill，追加到本号：

`accounts/<account_id>/SKILL_LOG.md`

```
- time: …
  project: productions/<task_id>
  session: <dsh session id 若有>
  skill: super-clone
  result: ok | fail | skipped
```

可沉淀的结论（口吻、禁词、常客问题、爆款结构）写入 `MEMORY.md`，只服务该号以后的路由和客服。A 号的 MEMORY 不能出现在 B 号会话里；矩阵 skill 只做汇总索引（号列表、最后登录），不把各号记忆正文混开。

### 矩阵 skill（`account-matrix`）

归纳 **所有会话** 用过的账号，打开后类似比特的多窗口管理台（实现是我们自己的 skill/页，不嵌比特）：

- 列出每个浏览器环境：绑定过哪些会话、项目目录、TUN、已登录平台
- 看登录是否有效、一键打开某后台
- 点进某号 = 只读/管理该环境，不串到别的号

路由：用户说「账号矩阵、有哪些号、切换浏览器」→ `/account-matrix`。统计数据从各 `productions/*/LOOP.md` 的 `account` + `accounts/*/PROFILE.md` 汇总，写到 `accounts/MATRIX.md`。

---

## 3. 闭环各环（skill 插槽）

全员同一流程。只有 **视频片型** 以后才允许 `tenants/<customer_id>/` 私有包。本阶段全部放 `platform`（即 `.dsh/skills/`）。

| stage | skill id | 本阶段状态 | DSH 怎么跑 |
| --- | --- | --- | --- |
| （总入口） | `weeksir` | **先实现** | 读 LOOP，提名，批准；会话绑定浏览器+目录 |
| （矩阵） | `account-matrix` | **合同后写，浏览器层先留档案** | 汇总全部会话账号，管理多浏览器 |
| plan | `content-plan` | 后做 | 写 BRIEF / 周计划 |
| collect | `viral-collect` | 后做 | 采对标，落 references/ |
| review | `competitor-review` | 后做 | AI 草案 + 人改 ANALYSIS |
| scheme | `scheme-select` | 后做 | 写 SCHEME.md，同意后才能 produce |
| produce | `super-clone` `hypit` `omni` | **视频第一批，共用工具** | jobs 调 `D:\video-skill` Python |
| ads | `qianchuan-upload` `publish-douyin` | 后做 | **折中：官方 API 发布/上传；浏览器只拿授权、补缺口** |
| feedback | `performance-feedback` | 后做 | 写回策划可检索的效果仓库 |
| cs | `cs-inbox` `cs-reply` | **合同已立，通道后接** | 抖音店铺消息；接口优先，否则 CU。回复无 AI 感；开源只作原料 |

视频第一批三条都是 **共用工具**，片型合同以后再拆。生成代码不进 DSH 插件：`tool-jobs` 调现有 Python。

### 成片之后的发布（折中，已拍板）

制作完成后进入投放/自然流发布，**不把指纹浏览器 RPA 当官方认可路径**（后台模拟点击会被风控，也不能承诺「不会被风控」）。

| 步骤 | 走哪 |
| --- | --- |
| 千川素材入库、建计划草稿 | **巨量千川 Marketing API** |
| 抖店素材中心 | **抖店开放平台** 素材上传 |
| 抖音号自然流发视频 | **抖音开放平台** 上传/发布（权限以控制台为准） |
| 默认槽位 | **一次生产可打两个槽**：`asset.qianchuan` + `asset.organic`，发布 skill 按槽位调对应官方 API |
| 应用归属 | **Weeksir 做服务商应用，店铺来授权**；access_token 按 `account_id` 存在隔离档案 |
| 授权登录、接口没有的页面 | 该号自研浏览器（TUN 内）+ 必要时 cua-driver |
| 计划上线、加大预算 | 仍要人点头；能 API 就 API |

指纹浏览器的作用仍是：矩阵隔离、同店多平台登录、给官方 OAuth 一个干净会话。不是用 RPA 代替开放平台。

---

## 4. 一次请求在 DSH 里怎么走

1. 打开 DSH，工作区 `C:\dsh`，新会话 `/weeksir`（或 AGENTS.md 要求先走路由）。
2. 路由读 `accounts/` 与 `productions/*/LOOP.md`；没有任务就建一个。
3. 根据用户原话套 stage 表，**只提名已存在的 SKILL.md**。
4. `user-questions` 问一次方案（批量也只问一次）。
5. 同意后 `tool-skill` 加载该 skill。
6. 该 skill 只做本环：写文件，或 `jobs` 跑脚本。
7. 更新 LOOP.stage / PROGRESS。控制权回到路由（同一会话继续说即可）。

批准只留三类：方案、难单 TIMELINE 或投放草稿、成片/客服升级。禁止每个 HTTP 问一次。

---

## 4.1 模型调用轨（所有 skill 调模型的唯一入口）

skill 要调模型不各接厂商、不靠 agent 记忆串——统一走一条 `tool-jobs` 命令（对齐 §1 的"jobs 调 Python"、§6 的"不另写 Agent 内核"）：

```
python D:\video-skill\backend\gateway.py call <capability> [purpose] --account <LOOP.member> --json '<params>'
```

- **能力名**：`text.complete` / `image.generate` / `video.generate` / `video.analyze`（purpose 见 `gateway.py caps`）。凭证/选模型/计价全在 `gateway.py` 的 ROUTES 注册表，skill 只给"能力 + purpose + 参数"。
- **积分**：`gateway.invoke()` 内自动 `precheck→preauth→call→commit/rollback`（本地 sqlite 桩，接口 = 未来 weeksir.com 账本）。`--account` 传 `LOOP.member`，扣在客户身份、账号隔离。
- **成本进已有批准**：批准前 `gateway.py resolve` 出 `credits.estimated` 摆进"方案批准"话术，不新增批准类。
- **为什么不是 MCP/插件**：DSH 不加载 MCP、插件不能给 Agent 注入工具（`AGENT-FRAMEWORK.md:6`）。命令契约写进 `AGENTS.md` + 各 skill 的 SKILL.md，**新会话读文件即恢复**。
- 设计全文：`docs/ADR-model-gateway.md`。

---

## 5. 实现顺序（按依赖，已规划）

本阶段路由 **只提名视频三条**。其它环写在表里但不进 weeksir 路由。

1. **会话绑定**（`session-bind`，已立合同）：一会话选浏览器 + 项目目录；写 LOOP；调用 skill 记入该号 `SKILL_LOG` / `MEMORY`（号间隔离）。
2. **`hypit`**：抽帧、时间轴、BRIEF 落盘。超级复刻要靠它。
3. **`super-clone`**：jobs 调现有 Wan Python；难单 TIMELINE 批准。
4. **`omni`**：你已写好的 skill，稍后提供；**不是** 8090 `/omni` omni_flash 页。
5. **发布折中**（开放平台应用已定为 Weeksir 服务商、店授权）：`qianchuan-upload` + `publish-douyin`，一次生产两个槽。
6. **`account-matrix`**：汇总各会话账号。
7. 策划 / 客服等：skill 合同可后补，**暂不路由**。

## 5.1 一条新 skill 怎么加进框架（实现节奏）

不改 Agent 内核。只加目录：

1. `.dsh/skills/<id>/SKILL.md`（name、description、何时用、硬规则、写哪些文件、调用哪条 Python）。
2. 在 `weeksir/SKILL.md` 的路由表加一行。
3. 在 AGENTS.md 路由加一句。
4. 新开 DSH 会话验证 `/<id>`。

没有 SKILL.md 的环，路由明确说未上架。框架保持完整，实现可以断。

---

## 6. 本阶段明确后置

- 商品 PROFILE 建档（出片时对话里现问产品图和卖点）
- weeksir.com 登录与积分
- 私有片型 skill
- 8090 客户入口（仅内部排错）
- 商用 fork Geekez。账号层：Chromium user-data + **每号 TUN（sing-box）**，UDP/QUIC/WebRTC 进隧道
- 出片走 Computer Use。CU 引擎已定为 **cua-driver**；仅后台无接口时由 skill 调用，作用域限制在该号隔离浏览器。

---

## 7. 框架验收（实现 skill 之前先过）

1. DSH 打开 `C:\dsh`，`/weeksir` 能根据一句话指出该走哪一环。
2. 未上架的环会拒绝，不会空跑生成。
3. 新建任务会得到 `productions/<task>/LOOP.md`。
4. 之后每实现一条 skill，只增加第 5 节的四步，不改循环。
