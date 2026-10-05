# PRD：Weeksir 全流程电商 Agent（DSH Desktop）

状态：总规划讨论稿  
日期：2026-09-26  
工作区：`C:\dsh`  
运行时：DSH Desktop（Harness；对话默认 Agnes 3.0 Flash）  
存量：`D:\video-skill`（8090 工作台 + Python 管线）+ 开源 GitHub skill 原料  

切片：难单复刻见 [`PRD.md`](PRD.md)。本文是平台总纲。

---

## 1. 一句话

客户用 **weeksir.com 同一套账号密码**登录 DSH Agent。每个授权店铺账号跑在隔离的指纹浏览器里，后面挂完整电商体系。Agent 按 skill 做内容策划、爆款采集、人工协同分析、选生成方案、出千川素材和自然流素材、对接千川投流、用数据回流策划层，再加无 AI 感自动客服。调用模型和 skill **走同一套积分账本**。除视频片型外，流程全员同一套；客户要的那种片子标准包盖不住时，才定制私有视频 skill。客户不选模型、不选工作流；Agent 判断哪条 skill 能完成，沟通同意后执行。

## 2. 闭环（要做成的操作系统）

```
weeksir.com 客户中心（账号密码 + 积分账本）── 同一身份登录 DSH
        │
        ▼
账号矩阵（指纹浏览器 + 网络隔离，一号一环境）
        │
        ▼
┌─────────────── 决策层 ───────────────┐
│  内容策划  ←── 投流/成交数据反馈      │
│  爆款采集 → 人工协同分析同行          │
│  选定视频生成方案（客户只听白话）      │
└───────────────┬─────────────────────┘
                │ 同意后
                ▼
┌─────────────── 生产层 ───────────────┐
│  Agent + Skill → 模型网关             │
│  文字 / 图片 / 视频（客户看不见品牌）  │
│  产出：千川投放素材 + 内置运营素材     │
└───────────────┬─────────────────────┘
                │
                ▼
┌─────────────── 投放与成交 ───────────┐
│  链千川后台：上传素材、起量、读报表    │
│  自动客服：无 AI 感回复，成交/售后     │
└───────────────┬─────────────────────┘
                │ 效果数据
                └──→ 回到决策层，准备下一批视频
```

这是一条运营闭环，不是「再做一个视频生成页」。

## 3. 原则

1. **客户不选模型、不选工作流。** 只描述目标和约束。模型写在网关策略里。
2. **同意的是方案。** Agent 说清：用哪条能力、要什么素材、风险、大概成本；点头后再调模型。
3. **一账号一套环境。** 指纹浏览器 + 独立网络/代理；Cookie、登录态、IP 不串号。只服务**授权店铺/授权账号**。
4. **工作流 = skill。** 8090 卡片、本系统管线、打磨过的开源仓库，都收成可安装 skill 包。
5. **商城管 skill 包。** 安装 / 启用 / 停用 / 删除。未启用的 skill，路由看不见。
6. **skill 调能力名，不调模型名。** 例如 `video.clone`，不写 Wan / Agnes / H3。
7. **数据必须回流。** 投放与客服结果写回策划知识，否则闭环是断的。
8. **对话模型可以固定。** Agnes 3.0 Flash 负责理解、路由、沟通；生成走网关。
9. **一套客户、一套积分。** DSH 与 weeksir.com 共用身份和积分账本，网站扣点、Agent 调模型扣的是同一余额。
10. **视频分共用工具和定制片型。** 抽帧、贴原声、Wan 调用、裂变管线等工具 skill 全员共用；客户要的那种成片结构标准合同盖不住时，再定制私有视频 skill。策划、投放、客服仍全员同一套。
11. **视频生成必须能批量。** 一条确认可以变成 N 条任务（多产品、多账号、多变体），排队进 `tool-jobs`，失败只重跑失败项，禁止一条条人工点。

## 4. 四层结构

```
① 客户与积分层     weeksir.com 同一套账号密码、同一积分账本
② 账号与环境层     指纹浏览器、网络隔离、店铺身份、千川登录态
③ 路由 Agent        只看「平台已启用 + 本客户私有」skill，征求同意
④ Skill 运行时      合同 + 批准点 + capability 调用
⑤ 模型网关          文字 / 图片 / 视频；扣积分后再调供应商
旁路：Weeksir Skill 商城（平台货架 + 客户私有货架）
旁路：效果仓库（投放/客服数据 → 策划检索）
```

### 4.1 账号与环境层

多账号矩阵的基础设施，本身也是 skill（`account-matrix`），不对客户露出「指纹」字样。

每个账号一份档案：

| 字段 | 含义 |
| --- | --- |
| shop_id / account_id | 授权店铺 |
| browser_profile | 指纹浏览器配置目录 |
| network | 独立代理 / 隔离网 |
| qianchuan_session | 千川后台登录态（凭证仓库，不进对话） |
| skill_enablement | 这个号启用哪些能力 |
| brand_kit | 产品图、卖点、禁用词、客服口吻 |

硬规则：

- 一个浏览器配置只绑一个账号。
- 打开千川、抖音创作者、客服后台，必须走该号环境，禁止系统默认 Chrome 混登。
- 网络隔离失败则拒绝开号，不带错号投放。
- 不做未授权号、买卖号、规避平台检测的黑产说明。产品边界是**已授权多店运营隔离**。

实现方向（已拍板）：**指纹浏览器自己开发，版权在 Weeksir。** 可以看 GitHub 上的实现思路（一号一环境、独立目录、独立出口），**不嵌入、不 fork、不分发对方产品**（比特、Geekez 等）。

自研范围：

- 账号档案、启停、与 DSH skill 的接口（打开某 `account_id` 的浏览器）
- 官方 Playwright Chromium + 独立 user-data-dir
- **每号一条 TUN（sing-box 或同类）**，该 Chromium 只走这块网卡（UDP/QUIC/WebRTC/DNS 进隧道）
- Playwright/CDP 与 cua-driver 只附着该号进程

不自研、也不主张拥有版权的：Chromium 内核（BSD）、sing-box/Xray（各自协议）。它们以独立进程/官方构建使用，不把对方闭源浏览器打进安装包。

只服务授权店；一 profile 一号。

IP 隔离（已拍板）：

- 每个 `account_id` 起一个 sing-box（或同等）**TUN**，该号 Chromium **只走这块虚拟网卡**。UDP / QUIC / WebRTC / DNS 进隧道，不靠浏览器里填 HTTP 代理。
- Windows：一号一份 Chromium 目录（独立 `chrome.exe` 路径或独立 user-data + 进程规则），路由按 `process_path` 进该 TUN；关 IPv6。
- 出口：一号一个稳定节点（住宅/ISP/独立 4G），不要多号共用。
- SOCKS 仅作调试兜底，正式路径是 TUN。
- 自动化和 cua-driver 只附着到该号已进 TUN 的 Chromium，不操作物理默认网卡上的浏览器。

#### 4.1.1 账号上下文记忆（切号即拉取）

切号 = 切身份 + 切环境 + 切记忆。客户在 DSH 内显式切到某个 `account_id` 时，系统必须自动拉取该号的上下文记忆，让 Agent 后续对话有该号历史可依。**这是必做项，不是 P2 加项。**

拉取范围（只读，禁止修改）：

| 记忆层 | 来源文件 | 拉什么 | 用途 |
| --- | --- | --- | --- |
| 账号档案 | `accounts/<id>/PROFILE.md` | 店名、平台、品牌 kit 索引、禁用词、口吻 | 让 Agent 知道"在跟哪个店说话" |
| 沉淀结论 | `accounts/<id>/MEMORY.md` | **全文加载到上下文** | 可复用结论直接指导本次出片/投放 |
| 最近任务 | `accounts/<id>/SKILL_LOG.md` | 末尾 N 条（默认 N=5） | 知道"上次做到哪、产出过什么" |
| 任务产出索引 | `accounts/<id>/SKILL_LOG.md` 里每条的 `project` 字段 | 各 `productions/<task>/` 的 LOOP/ASSETS/IMAGES/ADS 路径 | 点链接进任务，不展开正文 |
| 浏览器环境 | `accounts/<id>/browser.json` | `account_id`、`platforms`、`user_data_dir` | 起/切该号隔离浏览器（P2 account-matrix 上线后生效） |

硬规则：

- 切号瞬间把目标号的 PROFILE + MEMORY 全文 + SKILL_LOG 末 5 条注入当前会话上下文，**不是只读索引**。Agent 后续回答必须基于这套记忆。
- 切号只读本号，**禁止读 `accounts/` 下其它任何号的 MEMORY / SKILL_LOG 正文**（与 [AGENTS.md 账号记忆隔离](AGENTS.md) 一致）。
- 矩阵汇总 skill（`account-matrix`）只展示各号索引摘要，不把别号记忆正文拼进当前会话。
- 拉取的记忆写入 `productions/<task>/LOOP.md` 的 `context` 字段，作为本次任务的上下文基础。
- 记忆缺失（新号首次切换）只写空壳档案，不阻塞对话；下一步走 `session-bind` 补全。
- 拉取失败（文件不存在/损坏）只报错不阻塞；Agent 降级到"无记忆模式"继续对话，并在 SKILL_LOG 追加异常。

客户可见效果：

- 切到 shop-b 后，输入"再出一张主图"，Agent 知道这是 shop-b 的产品、用 shop-b 的卖点、避开 shop-b 的禁用词、延续 shop-b 上次套图的视觉风格。
- 不选账号时（标准模式）不拉任何记忆，对话从零开始；切到某号才拉。

实现归属：

- 拉取动作落在 `session-bind` skill 合同里（切号 = 重走 session-bind），不单独立 skill。
- 真正的隔离浏览器切换由 `account-matrix` skill 负责（P2 上线）；P1 阶段切号只切记忆和档案，浏览器环境用系统默认。
- ~~DSH 客户端 UI 上的账号切换按钮只负责调 `session-bind`~~ 🔴 **UI 载体已移除（2026-10-05 拍板）**：账号绑定占位方案推翻，`plugins/super-clone-ui` 里的原生账号切换器（`injectNativeAccountSwitcher`）整块撤掉，输入区还原成官方标准模式选择。
  **`session-bind` skill 本身保留**（切号 = 重走 session-bind 这条合同不变，见上两条），只是**不再在 UI 上占一个位**；本条其余纪律（不绕过 skill 边界、不自己读文件）继续生效。

### 4.2 路由 Agent（`weeksir`）

唯一总入口。只做：

1. 识别当前账号上下文（哪个店、哪个号）。
2. 听懂需求落在哪一环：策划 / 采集 / 分析 / 生产 / 投放 / 客服。
3. 只从**本账号已启用**的 skill 里提名。
4. 白话方案 + 缺素材 + 风险，等人同意。
5. 同意后加载目标 skill。

不确定就问，不瞎开视频生成。

### 4.3 Skill 包

| 文件 | 作用 |
| --- | --- |
| `SKILL.md` | name、description、何时用、硬规则、批准点 |
| `manifest.json` | id、版本、需要的网关能力、依赖 skill、是否客户可见 |
| `runner/`（可选） | 调本系统 Python / 开源 CLI 的薄封装 |

Skill 来源三路，都要**提取后打磨**，禁止原样堆进目录：

1. `D:\video-skill` 现有工作流（超级复刻、裂变、详情图……）
2. 本仓库已有定制合同（`super-clone`、`hypit`）
3. 开源 GitHub（客服、采集、投放报表等）——改成 DSH 头、去掉外站语法、补批准点、改走网关能力名

### 4.4 模型网关

| 能力类 | 用途 | 对内供应商举例 |
| --- | --- | --- |
| 文字 | 路由、策划文案、分镜、客服草稿 | Agnes 3.0 Flash |
| 图片 | 详情图、封面、关键帧 | Agnes Image 2.5 Flash |
| 视频 | 千川素材、自然流素材 | Wan 3.0、Agnes Video 2.5、H3 等 |

```
text.complete({ purpose, messages, images? })
image.generate({ purpose, prompt, refs[] })
video.generate({ purpose, duration, refs[] })
video.analyze({ purpose, file })
```

`purpose` 举例：`qianchuan-feed`、`organic-shop`、`clone-product-only`。客户对话不出现供应商名。

## 5. 运营 skill 地图（按环节，不按 8090 页面）

### 5.1 决策层

| skill id | 职责 | 人在哪停 |
| --- | --- | --- |
| `content-plan` | 本账号本周主题、人群、场、钩子、禁区 | 周计划确认 |
| `viral-collect` | 按品类/对手采集爆款（标题、封面、结构、评论） | 采集范围确认 |
| `competitor-review` | AI 先拆，人补判断：为什么爆、能否迁到本店 | 协同结论确认 |
| `scheme-select` | 根据结论提名生成方案（复刻 / 裂变 / 一句话 / TVC / 详情图） | 方案同意 → 才进生产 |

分析必须是 **AI 草案 + 人改**，不能全自动当决策。

### 5.2 生产层（8090 → skill）

| 现有入口 | skill id | 产出用途 |
| --- | --- | --- |
| 超级复刻 wan3 | `super-clone` | 千川 + 自然流成片 |
| Hypit | `hypit` | 抽帧、原声贴回、时间轴 |
| 爆款复刻 / wan3 replica | `viral-replica` | 结构复刻带货片 |
| 爆款裂变 | `viral-fission` | 多套素材变体（矩阵投放） |
| 一句话生视频 | `oneshot-video` | 快速测款素材 |
| 产品 TVC | `product-tvc` | 品牌/店铺广告 |
| 详情图 | `detail-image` | 主图/详情运营素材 |
| 视频翻译 | `video-translate` | 多语言投放 |
| 发布工作台 | `publish-douyin` | 成片后发布。**优先抖音/抖店开放平台上传与发布 API**；无接口再浏览器。不承诺 RPA 躲风控 |

生成器页（Agnes Image/Video、H3 Studio）**不**做客户 skill，收进网关。内部调试用 `lab-*`，商城对客户隐藏。

每条生产 skill 必须声明产出槽位：

- `asset.qianchuan` 投放素材（比例、时长、禁贴、封面）
- `asset.organic` 橱窗/直播切片/日常
- 两者可以同一条成片打两个槽，或按 skill 规则拆条

### 5.3 投放层

| skill id | 职责 | 人在哪停 |
| --- | --- | --- |
| `qianchuan-upload` | **折中：官方 API 上传素材/建计划草稿；** 指纹浏览器只做授权拿 token、以及接口没有的页面。计划上线前确认 |
| `qianchuan-optimize` | 读消耗、点击、转化、素材衰退 | 加预算 / 关计划确认 |
| `performance-feedback` | 把报表结构化写入效果仓库，供策划检索 | 自动；异常才问人 |

投流目标是「效果最大化」：新素材先小预算测，赢家加码，衰退下线；结论写回 `content-plan` / `viral-collect` 的检索源。没有这一笔，闭环不成立。

### 5.4 客服层

| skill id | 职责 | 硬规则 |
| --- | --- | --- |
| `cs-inbox` | 本阶段先接**抖音店铺消息**：优先开放平台接口，没有再走隔离浏览器 CU | 只走该账号环境 |
| `cs-reply` | 无 AI 感回复：短、像店员、用本店口吻和知识 | 禁止书面腔、禁止编物流/价格 |
| `cs-escalate` | 退款、发怒、法律、医疗功效 → 转人 | 强制 |

口吻、FAQ、禁提词来自该账号 `brand_kit`。开源客服仓库只当原料，必须按店铺打磨。

## 6. 插件商城

对外叫插件，对内是 Weeksir Skill 包。**不要**用 DSH 社区 `dsh-market` 当货架（cordis 插件会拖垮 Harness）。

| 操作 | 行为 |
| --- | --- |
| 浏览 | 上架包：名称、环节（策划/生产/投放/客服）、是否已装 |
| 安装 | 解到 `.dsh/skills` 或用户 skill 目录 |
| 启用/停用 | 可按**账号**启用；停用后路由不可见 |
| 删除 | 进行中任务则拒绝 |
| 可见性 | `customer` / `ops` / `lab` |

未安装不能假装会投流或会做客服。

商城分两层货架：

| 货架 | 谁能看见 | 谁能改 |
| --- | --- | --- |
| 平台 | 所有已购对应套餐的客户 | Weeksir 运营 |
| 私有 | 仅该 `customer_id` | 该客户 + 被授权的实施同学 |

安装目标必须带租户：平台包装进共享目录；私有包装进 `skills/tenants/<customer_id>/`。

## 6.1 客户管理系统（与 weeksir.com 同一套）

DSH Agent **不自建用户表**。身份、密码、积分、订单以 **weeksir.com 客户中心** 为唯一账本。

| 能力 | 约定 |
| --- | --- |
| 登录 | 账号 + 密码（及网站已有的短信/二次验证）。DSH 桌面端、以后的 Web Agent 都走同一登录接口。 |
| 会话 | 登录后拿网站签发的 token；过期回网站重新登。 |
| 客户档案 | 店铺、品牌 kit、已购套餐、启用中的 skill，都挂在同一 `customer_id` 下。 |
| 积分 | **同一余额、同一流水。** 网站生成扣点，DSH 里 skill 调文字/图片/视频也扣同一本账。禁止两套点数对不齐。 |
| 计价 | skill 的 `manifest` 声明预估积分（或按网关实际消耗回写）。调用前校验余额，不足则停并提示去 weeksir.com 充值。 |
| 流水 | 每笔：customer_id、skill_id、capability、消耗、任务 id、时间。网站个人中心和 DSH 都能查。 |

登录失败、积分不足、账号被停，Agent 只提示去 weeksir.com 处理，不在 DSH 里另开充值。

客户 : 店铺账号 = 1 : N。积分在**客户**上扣，不在单个抖音号上扣。

## 6.2 视频 skill：共用工具 + 按需定制片型

视频这一环分两层，不要混成「所有视频都私有」或「所有视频都一套合同」。

**A. 共用工具 skill（平台，全员同一套）**

抽帧、裁切、原声贴回、分镜分析、Wan/网关调用、裂变批量、任务排队。对应现有 `hypit`、管线 runner、网关 `video.*`。各客户都走这些工具，不为每家店再写一份。

**B. 片型合同 skill（标准包 + 必要时私有）**

写的是「这种片子怎么拍」：时长、比例、切段、换脸/换产品、口播、槽位（千川/自然流）、禁区。

- 通用片型放平台：`super-clone`、`viral-fission`、`oneshot-video`、`product-tvc`…
- 标准合同盖不住时，才给该客户定制一条私有片型 skill（女装走动、保健品分段、只要前 3 秒钩子等）

私有片型 **调用 A 层共用工具**，不复制一份 Wan/抽帧实现。定制的是合同和参数，不是另做一套视频引擎。

```
skills/
  platform/
    hypit/                       共用工具：抽帧、贴原声、时间轴
    super-clone/                 标准片型：超级复刻
    viral-fission/               标准片型：裂变
    ...
  tenants/<customer_id>/
    <slug>/SKILL.md              仅该客户的片型合同，runner 指向平台工具
```

规则：

1. **发现范围**：路由加载平台已购视频 skill ∪ 该客户私有片型。A 的女装走动片不会出现在 B。
2. **何时定制**：成片结构/禁区/素材槽和标准片型不一致才开私有包。工具层永远不私有化。
3. **怎么定制**：改 SKILL.md + `purpose`；抽帧/生成仍调平台 runner。平台升级工具不覆盖 `tenants/`。
4. **商城**：客户看到「平台视频」+「我的片型」。装/停/删只作用于自己的片型包。
5. **积分**：共用工具消耗和定制片型都走 weeksir 同一账本；定制片型可另标单价。
6. **其它流程全员同一套。** 策划、投放、客服、登录积分、账号隔离不为每家店定制。

路由：客户说「还是按我们店那种上身走动来一条」→ 私有片型 → 内部仍调共用 `hypit`/Wan 工具。没私有片型则提名标准 `super-clone` 并说明差异。

### 6.3 视频批量（效率）

视频是闭环里最慢、最贵的一环，单条点选不够用。平台工具 skill 和私有片型都要按**一批**设计。

一批可以是：

- 同一原片 × N 套产品/脸（裂变）
- 同一片型 × N 个账号（矩阵出同一结构的千川素材）
- 同一产品 × 多时长/多比例（15s 千川 + 橱窗条）
- 选方案后一次同意，生成列表里的多条

落地约束（DSH）：

1. 同意一次覆盖整批，不要每条都问。难单切段表按「片型模板」批一次，不要 N 张表。
2. 用 `tool-jobs` 排队，并发上限走网关配额；列表里看每条状态。
3. 失败隔离：第 3 条挂了只重跑第 3 条，整批不作废。
4. 积分按条预扣，失败回滚该条。
5. 抽帧、贴原声、上传千川这些共用工具必须吃「任务列表」参数，私有片型只提供列表和合同，不自己写循环。
6. 产出打标仍按条：`asset.qianchuan` / `asset.organic`。

## 4.7 落地：只用 DSH 现有 Agent 框架

真正落地 = **不另写一套 Agent 运行时**。对话循环、skill 加载、批准、后台任务、工作区文件、轨迹，全部用 DSH Desktop 已经跑着的件。Weeksir 只补三样：**合同（SKILL.md）、薄 runner、网关/积分适配**。

当前 Harness 已具备（桌面端 web profile）：

| 落地需求 | DSH 现成件 | 用法 |
| --- | --- | --- |
| 多轮 Agent | `dsh-agent` + `dsh-agent-loop` | 客户说话 → 模型调工具 → 再说话 |
| 默认对话模型 | `agent-default-model` + `llm-pi-ai` | 已接到 Agnes 3.0 Flash |
| 加载合同 | `skill-filesystem` + `tool-skill` + `/命令` | `.dsh/skills/<id>/SKILL.md` |
| 工作区规矩 | `dsh-agent-instructions`（AGENTS.md） | 路由写在工作区，不写进模型系统提示硬编码 |
| 读写真源 | `tool-fs` | BRIEF / ANALYSIS / TIMELINE / 效果仓库 |
| 征求同意 | `user-questions` + 批准 UI | 方案、切段表、投放草稿、客服升级 |
| 长任务 | `tool-jobs` + `jobs-local` | Wan/Python 管线丢后台，对话不卡死 |
| 调本机管线 | `tool-bash` / `tool-pwsh` | `D:\video-skill` 现有入口，不重写 |
| 分席 | `tool-subagent` | 策划 / 生产 / 客服可开子 Agent，共用工作区 |
| 待办 | `tool-todo` + `goal` | 闭环步骤可视化 |
| 轨迹 | 桌面「轨迹」页 | 审计调了什么，不另做日志台 |
| 会话 | `dsh-session` | 一客户多会话；难单一个会话跟到底 |
| 凭证 | credentials | 以后放 weeksir token；模型 Key 不进对话 |

一次真实请求在 DSH 里的走法：

```
客户登录（P1 起：weeksir.com 同一账号）
  → 打开工作区 C:\dsh
  → Agent 读 AGENTS.md + 已启用 skill 目录
  → 提名 skill，user-questions 征求同意
  → tool-skill 加载该 SKILL.md
  → tool-fs 写 productions/<任务>/
  → 积分预扣（同一账本）
  → tool-jobs 跑 runner（现有 Python / 开源 CLI）
  → 网关按 purpose 选文字/图/视频模型
  → 结果落盘；投放/客服数据再写入效果仓库
  → 轨迹可回放
```

明确不造：

- 不自研聊天循环、不自研 tool host、不自研批准气泡
- 不为每个 8090 页面做一套 Agent
- 图/视频模型不塞进 `llm-pi-ai`（那是对话 LLM）。它们是 **job/runner 调网关**
- 指纹浏览器、千川后台是 **隔离环境里的 runner**，不是新的 Agent 内核

### 4.8 Computer Use（结合 Agnes 3.0 Flash）

DSH **没有**内置 Anthropic 式 `computer_use` 协议。现成件是 Agent Loop、function calling、Agnes 3.0 Flash（文本 + **图像 URL**）。官方文档没有单独的 Computer Use API。

可落地的做法：给 Agent 一套「电脑工具」skill，作用域限制在该账号指纹浏览器里。

```
Agnes 3.0 Flash 看截图、决定下一步
  → DSH bash / jobs
  → screenshot | click | type | scroll
  → 只操作该店铺隔离浏览器（千川、抖店、客服后台）
```

约束：已有稳定脚本/API 的（出片、现有 Playwright 发布）继续走脚本。Computer Use 只补没有接口的后台页。每步截图走积分；提交/开计划必须先批准；禁止点本机任意窗口。

定位：**平台基础能力，有需要的 skill 就调。引擎用 [cua-driver](https://github.com/trycua/cua)（Windows UIA + 虚拟光标，不抢用户鼠标）。** DSH 里用 bash/jobs 调 `cua-driver`，或以后再套 dsh-computer-use 插件。出片三条不走 CU。千川无接口页、抖音店铺消息没有开放 API 时再用，且只打该号隔离 Chromium。管理员窗口点不了（引擎边界）。有稳定脚本则优先脚本。

P0 验收（这才叫落地，而不是文档）：

1. DSH 打开 `C:\dsh`，`/weeksir` 能路由
2. `/super-clone` 能按合同问 TIMELINE，同意后 `jobs` 调现有 Wan 管线
3. 工作区留下 BRIEF / ANALYSIS / TIMELINE / PROGRESS
4. 商城能装/停/删 skill 包（改目录即可，Harness 热发现）

## 7. 客户可见主路径（举例）

```
店长：这个号要推新鞋，对标这条抖音
  → 路由：viral-collect + competitor-review（本号启用）
  → 人协同：这条能迁，建议超级复刻 + 两条裂变测千川
  → 同意
  → super-clone / viral-fission 出片（网关自选视频模型）
  → 槽位：2 条千川 + 1 条橱窗
  → qianchuan-upload 起小预算测试
  → 3 天数据进 performance-feedback
  → content-plan 下周只加「脚部特写+价格钩子」
  → 询单进 cs-reply，退款转人
```

全程无模型下拉。

批准粒度：

1. 方案（路由 / 选方案）
2. 难单切段表或投放计划草稿
3. 成片或客服升级

禁止每个 HTTP 调用都问一次。

## 8. 分期

### P0 骨架（钉在 DSH 现成件上）

- 工作区 `C:\dsh` + `AGENTS.md` 路由
- `.dsh/skills/weeksir` 路由
- 视频 skill 第一批上架（**三条都是共用工具**，片型合同以后再拆）：`super-clone`、`hypit`、`omni`
- 商城最小：拷贝/删除目录 = 安装/卸载（skill-filesystem 热发现）
- 对话模型：已接的 Agnes 3.0 Flash
- 生产：**全部 skill 模式，只跑在 DSH 内置环境**（Agent Loop / 批准 / jobs / 工作区）。客户不进 8090。第一批 `super-clone` / `hypit` / `omni` 由 jobs 调用 `D:\video-skill` 现成 Python，不重写 Wan/Omni/Hypit 引擎，也不做成 cordis 插件。
- 批准：只用 DSH `user-questions`；批量只问一次方案

### P1 本地全流程（无 weeksir.com 账号）

本阶段要在本机 DSH 上把闭环跑通：策划 → 采爆款 → 人机分析 → 选方案 → 视频生产 → 千川投放 → 数据回流 → 客服。不接网站登录/积分。

视频家族先打磨上架三条共用工具：`super-clone`、`hypit`、`omni`（jobs 调现有 Python）。其余环节同样立 skill，能跑就接本机环境。8090 仅内部调试。

### P2 账号矩阵 + 投放回流

- 指纹浏览器 profile 绑定、网络隔离
- `qianchuan-upload` / `optimize` / `performance-feedback`
- `content-plan`、`viral-collect`、`competitor-review`（人机协同）
- 效果仓库可被策划检索

### P3 客服 + 私有化 + 商业化

- `cs-reply` 无 AI 感 + 升级规则
- 租户私有**视频生成** skill（按片型定制）+ 商城「我的片型」
- 实验室 skill 对客户隐藏
- 8090 仅内网调试；网站与 DSH 积分流水对账。**P1 起客户只走 DSH，不打开 8090。**

## 9. 明确不做

- 未授权账号、养号黑产、教人绕过平台检测
- 客户对话里选模型
- 把 2000+ 外部角色卡一次性装进 DSH
- 8090 每个页面做成一个客户入口
- 用社区 dsh-market 当商业货架
- P0 自研指纹浏览器内核
- 全自动投放加预算、全自动承诺发货时效
- DSH 另建一套用户/积分（必须复用 weeksir.com）
- 把 A 客户私有 skill 默认同步给其它客户
- 另起一套聊天/工具运行时替代 DSH Agent Loop

## 10. 风险

| 风险 | 处理 |
| --- | --- |
| 串号 | 一 profile 一号；环境检查失败则停 |
| 千川接口/RPA 易碎 | skill 隔离；失败只停投放环，不影响策划 |
| 开源 skill 口吻不像本店 | 必须经 brand_kit 打磨才上架 |
| 数据不回流 | P2 验收：没有 feedback 写入则投放 skill 不算完成 |
| 商城装 cordis 插件 | 禁止；只装 skill 包 |
| 网站与 DSH 积分不一致 | 单一账本 API；调用前预扣、失败回滚 |
| 私有 skill 串租户 | 发现路径强制 `tenants/<customer_id>`；测试用两个客户对打 |

## 11. 待拍板

1. 指纹浏览器第一期对接哪家本地 API？
2. P1 是否仍是：超级复刻、详情图、一句话出片？（投放和客服放 P2/P3）
3. 千川素材默认视频模型：Wan 3.0 还是 Agnes Video 2.5？
4. 自动客服先接哪条会话通道（抖音店铺消息 / 千川线索 / 企业微信）？
5. weeksir.com 登录/积分是否已有正式 API 可给 DSH 对接，还是 P1 先做适配层？
6. Computer Use 是否 P2 上：仅指纹浏览器内操作千川/店后台，还是 P1 不做、继续纯 RPA 脚本？

拍板后 P0 先落 `weeksir` + 商城目录 + `super-clone` / `hypit` 包结构。
