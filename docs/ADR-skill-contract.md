# ADR：skill 执行合同与回执（根治"agent 按自己理解跑"）

日期：2026-10-09 · 状态：**Accepted（用户指令：根治、全局、铁律、所有现有和新的 skill 都遵守）**
回归：`scripts/check-skill-contract.py`

## 问题（实测，不是猜）

一条 super-clone 会话在用户没被告知的情况下：① 绕开 `runner/run.py` 的 Step 1.5 标准迁移编译器，
自己手写了一份"简化 user 段"喂 GPT；② 违反 `SKILL.md` 硬规则 12（送 Wan 前必须按秒写死动作时间轴）。
用户结论：**"skill 在 agent 驱动的时候经常容易自己按自己理解的跑。"**

盘上查到的根因不是"模型不听话"，是**绕开合同零成本、零痕迹**：

| 事实 | 出处 |
|---|---|
| `runner/run.py` 只是薄封装，零校验（只校验 `product-name` 非空） | `.dsh/skills/super-clone/runner/run.py:60-94` |
| 合同里那"6 件事"在代码里只是**一个 prompt 字符串**，不是 6 个可校验阶段 | `backend/services/super_clone_wan3.py:283` |
| 编译产物落盘是裸文件，**没有任何版本/签名字段** | `super_clone_wan3.py:3898`（`prompt.txt`）、`:2757`（`h3_prompt_utf8.txt`） |
| 全工作区 40 个 skill，**0 个声明了流程合同** | 枚举 `.dsh/skills/*/SKILL.md` 的 frontmatter |

⇒ 事后没有任何东西能区分"编译器出的 prompt"和"agent 手写的 prompt"。靠提示词要求它别绕，
和 §7 那次一样：**只降漏检率，不降漏做率**。

## 决策：四层，判据只有一份

### 1. 合同声明（`SKILL.md` 的 `contract:` 段）—— 复用既有形状
行格式与 `form:` 同族（`字段|字段|…`），不发明新语法：

```yaml
contract:
  version: super-clone-wan3@1
  entry: python .dsh/skills/super-clone/runner/run.py        # 唯一合法入口
  spend: video.generate/clone-r2v                            # 哪一下花钱（第 4 层在这里拦）
  steps:
    - id=step1_5-compile | 证明=prompt.txt 由入口写出且六个模块齐 | 必需
```

### 2. 强制范围 = 已有那张唯一源，不新造分类
"哪些 skill 必须有合同" **不另立名单**，直接读 `hypit_bridge.KNOWN_SKILLS`（= `intent.py` 的 `LANES`，
两者已由 `check-intent-rule.py` 钉相等）。理由：这张表的语义已经正是"**带引擎的档**"——
带引擎 = 会花钱 = 有流程可跑偏。纯提示词/知识库类（13 个 `seedance-*`、`film-*` 写作类）本来就不在这张表上，
不欠回执，也不该被闸判红。
🔴 **新 skill 要挂引擎就得先进这张表 ⇒ 同时必须交合同**。这就是"新的也必须遵守"的落码方式。

### 3. 回执（`productions/<task>/.skill_receipt.json`）—— 由**入口**写，不由 agent 写
入口跑完自己落一份：`skill / contract_version / entry_argv_sha8 / session / ts /
inputs[{path,sha8,size}] / steps[{id, ok, evidence{path,sha8,字段}}] / deviations[{step, reason}]`。
`deliver_pack.py`（本来就是交付必跑项）读它，把**缺步/偏离原样印进交付消息** ⇒ 漂移从"肉眼发现"变成"交付里必现形"。

### 4. 提交口拒收无回执（硬拦）
花钱那一刀之前调 `skill_contract.verify_or_raise(skill, inputs…)`：**无回执 / 合同版本不符 /
回执里的输入指纹与这次要提交的文件不一致 ⇒ 抛错、不提交、0 分**。落点沿用今天已验证有效的形状：
`wan3_client._wan3_submit_guard()`（所有 wan3 腿的咽喉）与各条腿自己的守卫，**不是网关前门**。
🔴 边界与 RH 禁令同一条：**只断提交，不断查询/捞回** —— 已提交的任务不能因为没回执就重投（= 双花）。

## 豁免（不许静默）
`verify_or_raise` 只接受写在回执里的 `deviations[{step, reason}]`：reason 为空 ⇒ 判不合格。
即"人可以决定跳过某步"，但必须留下是谁、为什么，且第 3 层会把它印到交付消息里。
没有 `--force`、没有环境变量能一键关掉整套（那等于把铁律降级成建议）。

## 强度到哪为止（别 over-claim）
- 拦得住"忘了/图省事"，**拦不住蓄意伪造**：能改 `D:\video-skill` 源码的 agent 理论上能写一份假回执。
  代价是留下 `entry_argv_sha8` 与文件 mtime 的可核对痕迹，且回执由入口写、agent 手写一份要能凑齐输入指纹。
- 覆盖范围 = 已交合同的档。没交的档今天照样能跑 —— 由 `check-skill-contract.py` **判红**来逼进度，
  不靠文档呼吁。首批红名单 = `KNOWN_SKILLS` 里除 super-clone 之外的那几条。
- 第 4 层只在**花钱那一刀**生效：不花钱的中间步骤（看图、文本）没有强制点，只能靠第 3 层可见性。

## 落码顺序
B（声明 + 回执 + 交付打印）→ C（全局层 §8 铁律）→ A（提交口拒收，逐档接）。
A 逐档接是因为它要动 4000 行的管线文件，且并发会话正在改同一批文件（2026-10-09 实测 `super_clone_wan3.py` 一天内被三方动过）。


## 补：合同条款必须是【可执行形状】（2026-10-09 事故，用户定性"agent 自己改流程 = 最高等级铁律"）

**又发生了一次越界，而且这次能精确定位到合同自身的洞**：`super-clone/SKILL.md` §3「参考源清洗闸」写的是
"删除不希望继承的烧录字幕、花字、标记和水印" —— **纯结果条款，没写手段**；下一句"先清洗或换源"也没说怎么洗。
结果已知、手段未知的空隙，执行者必然拿"我想到一个能干活的办法"去填：那次是手搓 ffmpeg 去字幕，还差点编出"内置去字幕模型"。

所以根因不是"agent 不读 AGENTS.md"，而是**合同在那一步把决定权让渡给了执行者**。文本呼吁拦不住，
只有把"让渡"变成红才行。落码（`backend/skill_contract.py`）：

| 判据 | 形状 | 谁调 |
|---|---|---|
| `contract_shape_problems(contract)` | 每条 step 必须至少有 `证明=`（物证）或 `手段=`（白名单）之一；两者都没有 ⇒ 报"手段由执行者自由发挥" | `verify_or_raise()` 在**花钱之前**跑 ⇒ 合同有洞 = **拒绝提交**，不是提醒 |
| `impl_problems(contract, used_impl)` | 声明了 `手段=` 的步：实际手段不在白名单 ⇒ 拒；**没申报**也 ⇒ 拒（不许沉默通过） | 入口/管线在动手之前调 |
| `判据=`（内容判据，同日补） | `json-array-min` / `json-list-min` / `json-key` / `bind-sha8:<步>:<字段>`；判据不认识或读不出 ⇒ 一律按不通过算 | `check_receipt()` |

回归：`scripts/check-skill-contract.py` 的 **T18a/T18b/T18c/T19a/T19b/T19c/T20**（含"合同有洞必须拒"的端到端一条，
和"白名单内要放行"的正向对照 —— 防永远拒式假安全）。

### 强度到哪为止（这一层拦不住什么，必须写清）
- 合同核验发生在**管线/入口的代码路径**上。agent 在对话里**自己手敲一条 ffmpeg**，根本不经过任何被合同管住的函数
  ⇒ 第 4 层物理上看不见它。要拦这一类，强制点只能在**工具层**（内核 hook：`agent/turn-stopping` /
  `agent/inbox/inserted`，或工具调用白名单），那是宿主级改造，不在本合同范围内。
- 现在能做到的：① 让"合同有洞"直接卡住花钱；② 让"跳步/换料"在物证层被拒（`bind-sha8`）；
  ③ 让越界**在交付时可见**（回执打印缺步与 deviations）。**可见 ≠ 拦住**，别混着报。

### 待拍（我不替产品发明手段）
「参考源清洗」这一步的**授权手段**是什么？三个候选：A 只允许**换源**（合同写 `手段=换源`，禁止任何画面加工）；
B 允许**人工预处理**后重新登记素材（`手段=manual-preprocess`，产物要落 `source_clean.mp4` 当物证）；
C 管线里**真有内置清洗节点**（那我该去读码找到它，而不是问）。定了我就把它写进合同并接 `impl_problems`。

## 补：合同要【当人能核对】，所以前后各一张表（2026-10-09 用户："提交视频生成前，要总结一下每一次是谁完成的，做一个表格，以便我清楚流程没走错"）

判据仍然只在 `skill_contract.py` 一处，两张表只是它的两种渲染：

| 时机 | 表 | 来源 | 落点 |
|---|---|---|---|
| 报价那条消息 | **计划表** `plan_table(档)` | 合同 `contract:` 段（版本 / 花钱口 / 每步的 `必需`·`手段`·`证明`） | agent 贴进回复；人在同意前就能看出"要走的步对不对" |
| 提交那一刀之前 | **执行表** `who_table(回执)` | 回执逐行（会话 / 时间 / 手段 / 物证 / sha8 / 状态） | `verify_or_raise()` 返回 `table`，管线打进日志（前缀"合同核验"），agent 原样贴 |
| 交付 | 同一张执行表 | `summarize()` 现在直接渲染 `who_table` | `deliver_pack.py` 的 `receipt` 字段与 markdown |

一条命令出表（只读、0 分、不建预扣）：`python scripts/contract_tables.py <档> [段目录]`
—— 目录里没有回执 ⇒ 退出码 1 并明写"这份产物不是从声明的入口出来的"，**不给空表糊弄**。

### 🔴 执行表是【逐行署名】，不是"最后一个碰文件的人"

`write_receipt()` 会读上一份回执：某一行没变（证据路径 + sha8 + 手段 + 合格状态 + 必需标记全同，**且合同版本没动**）
就沿用上一次的会话与时间；变了的那一行才盖当前会话。
理由：一条片子常是几条会话接力做完的（甲出分镜、乙重跑编译）。整表盖同一个会话 = 台账当场作废，
事后查"这步是谁改的"查不到 —— 而这张表存在的全部意义就是查得到。
合同改版 ⇒ 旧署名不再继承（改版 = 旧事实不再算数），这一条由闸 T26b 钉，防"永远继承"式假账。

配套两条形状判据（否则表会骗人）：
- **没做的步不挂任何人的名字**：状态 ❌ 的行责任列写"—（这步没做）"、时间列留空（闸 T27）。
- **拿不到会话就明写"未知"**：常驻服务（8090）的进程环境里没有 `DSH_SESSION_ID` ⇒ 那一列是
  `未知（DSH_SESSION_ID 没传进这个进程）`，**绝不猜一个会话 id 填进去**（闸 T28b）。
  入口进程自己带着会话时（agent 跑 `runner/run.py`）才会署上真 id；服务端接力要在表里有名字，
  得由入口显式 `set_ctx(..., session=<会话id>)` 递进来 —— 目前没有哪条 HTTP 路在做这件事，这是**已知缺口**，不是"已经支持"。

### 强度到哪为止

这两张表保证【事前看得见要走哪几步 + 事后查得着每步是谁做的】，**不保证** agent 不走合同外那一步：
在对话里手敲一条命令、不经过任何被管函数，合同物理上看不见（见上一节同名条目）。
所以要贴给人看的动机在这里：表一贴出来，"跳步"和"换了手段"就从只有 agent 知道变成两个人都知道。

回归：`scripts/check-skill-contract.py` 的 T23–T31（含 T26 接力署名、T26b 改版不继承、T27 没做的不挂名、
T28/T28b 会话来源与"不许猜"、T30 打印在提交之前、T31 命令真跑）。


## 补：引擎只有一个 ⇒ 四方必须指同一条 lane（2026-10-10）

**事故形状**：客户在表单里锁定的引擎是 `yz-prime / wan3.0-video-prime`，合同 `spend` 写的是
`video.generate/clone-r2v`（**网关 ROUTES 里根本没有这条路由**），而入口实现
`wan3_client.run_one_segment` 直连 RunningHub。三个源各说一套 ⇒ agent 把"路由 RunningHub wan3.0
（参考视频迁移路线）"念给了客户，而被点名"网关不是删除了吗"。

**根因不是"文档写错"**：是【没有任何一处代码把这三方放在一起比过一次】。散文可以怎么写都行，
判据必须落在函数上。所以本ADR新增一条不变量与一支闸：

| 源 | 盘上位置 | 谁读它 |
|---|---|---|
| 表单锁定位 `engine_lock` / `resolution_lock` | `SKILL.md` 的 `form:` 段 | 8100 渲染给客户看 |
| 合同花钱口 `spend: <cap>/<purpose>` | `SKILL.md` 的 `contract:` 段 | `verify_or_raise` / `plan_table` |
| 网关路由与单价 | `gateway.py` 的 `ROUTES` / `PRICE_ROUTES`（+ 外置 `model_prices.json`） | `invoke` 预扣/结算/审计 |
| 真提交那一刀 | 提交腿函数体里的 `gw.invoke("<cap>","<purpose>")` | 花钱就发生在这里 |

判据（`scripts/check-engine-single-source.py`，全离线、0 消耗）：

- **E 组** 枚举盘上【同时有 `form:` 和 `contract.spend`】的每一条线（不点名 skill，加一档自动进闸）：
  spend 能在 ROUTES 解析、单价是**显式登记**（不许吃 `PRICE_BY_CAP` 兜底 —— 兜底值就是"凭记忆报价"的机器版本）、
  锁定位里的 provider == 路由 provider、有锁定位就不许再有引擎 `select`、三方判据合计 0 问题。
- **F 组** AST 读提交腿函数体，取出它 `invoke()` 的那一对 `(cap, purpose)`，必须**逐字等于**合同 spend；
  并断言入口里不再有直连厂商那条腿的调用点（`run_one_segment` 后不带 `_prime` 就算越界）。
- **G 组** 反向对照 + 变异对照：把当年那个错形状（`clone-r2v`）喂进**同一个纯判据** ⇒ 必须同时报
  "路由不存在"和"单价没登记"；再分别摘掉单价登记、把锁定位换成别的 provider、给表单加一个引擎 select ⇒ 各自必须红。
  并钉一条"正解输入仍然 0 问题"，防止变异对照把真假一起判红（那等于没闸）。
- **H 组** 报价时能不能【现读到真相】：SKILL.md 必须写出现读命令；真跑一次 `gateway.py prices`，
  输出里必须同时读得到这条 lane 的名字、**provider**、且退出码 0。
  为此 `prices` 每行末尾新增了 `[provider]`（原来只给"lane + 分数"，厂商标要另翻 ROUTES ⇒ 两处一比就记错）。

**顺带修掉的两处自伤**（都是这次比对暴露的）：

1. `skill_contract.parse_contract` 以前把行尾注释**当成值的一部分** ——
   `spend: video.generate/wan3-prime   # 唯一源=…` 解析出的值带尾巴，于是和 ROUTES 永远对不上、
   `plan_table` 打印出一长串注释。现在剥掉 ` #…`，注释回到自己的行（合同块里以 `#` 开头的行本来就被跳过）。
2. `start_8090.cmd` 的"活着没"守卫探的是 `/api/health/models` —— 那是 **8100** 的口，8090 上永不应答
   ⇒ 守卫永远判"没活"，会再去起一个实例并抢同一个日志文件（实测报"另一个程序正在使用此文件"）。
   改成探 `/`，并由 `check-8090-log-encoding.py` 的 **F2b** 钉住"守卫探的路径必须是 app.py 真注册过的路由"
   （钉规则，不钉 URL 字面量 —— 上一版就是钉了字面量才把我自己带沟里）。

**强度到哪为止**：这支闸管得住【走合同 + 走入口】的那条线。agent 在对话里手敲一条厂商 HTTP 请求，
它物理上看不见（同 ADR 前一节的限制）。它能保证的是：一旦四方有任意一方漂移，**花钱那一刀之前**就红，
而且红得指名是哪一方。

## 补：这三条判据不能靠"agent 记得跑"（2026-10-10，现网三条会话复盘）

同一晚对着盘上事实核了一条 12 环交互链（登记 → 分析素材 → 回填 → 客户补 → 提交 → 叫醒 → 回执 →
报价 → 点批准 → 自动跑 → 出片），断掉的地方**没有一处是判据写错了**，全都是
**"这一步由 agent 自觉执行"**。现网形状：

| 期望 | 盘上实际 | 根因 |
|---|---|---|
| 表弹出来时已经填好 | 三条会话只回填了 `voice_mode` 一格，原片躺在 `src/`，`inputs/` 空着 | 覆盖率没人算 ⇒ 归类活推回客户 |
| 报价那条消息带流程表 | 三条报价都只有价格 | 表要靠 `contract_tables.py`，那条命令可以跳过 |
| 批一次跑完整条 | 段数靠 agent 手填 `--count` | 段数没从盘上算（已由 `--count-from` 收口） |

三条都按同一张方子改：**判据落到服务端一份，绑到必然发生的动作上，跳不过去**。

1. **回填覆盖率 = 入口硬判据**。`model_admin.form_prefill_gap()` 一次算好三份
   （`unfilled_required` / `loose_materials` / `prefill_gap`）随门控下发；
   `runner/run.py: form_coverage_gap()` **只读结论**（不解析字段表 ⇒ 没有第二份判据），
   非空就在【导入引擎之前】拒 ⇒ 0 分、不落半成品。扫描根含 `<task>/src`，
   否则抓不到"原片下载了却留在 src"这一次；`outputs/`、`frames/` 故意不扫（派生物当素材会天天误拦）。
   两个必须写对的细节：① 隐藏格（`show=` 不命中）**不算缺** —— 拿它卡必填等于把人永久锁在入口外；
   ② 素材格的"有值"查的是 **param** 那份 media（回填按引擎参数名落盘），按 key 查就是"明明填了却报没填"。
2. **报价自带计划表**。`skill_contract.skill_for_spend(capability, purpose)` 从合同的 `spend:` 反查档位，
   `gateway.py approve` 打完报价紧接着打 `plan_table()`；反查不到就明说"这一档没合同"并且**不印表**。
   表由被调用方产生，而不是由调用方回忆。
3. **回填命令不许把"丢了什么"咽下去**。`intent.py prefill` 现在打 `files_skipped` /
   `unfilled_required` / `loose_materials` / `prefill_gap`，缺口非空时 `next` 换成"补齐再来"；
   同内容已在槽目录里就复用（重跑同一条回填撞 `PermissionError[13]` 是 8100 正握着那个文件预览）。

**回归**：`check-skill-form.py` D10–D15b（服务端三份 + 入口结论 + 两处变异对照：把 `form_prefill_gap`
摘成恒空、把 `form_coverage_gap` 摘成恒放行，各自必须让对应断言翻脸）；
`check-approval-count-from.py` K7–K10b（真跑 `approve`，临时账本 + `GATEWAY_APPROVAL_POPUP=0`
⇒ 人桌面上不多窗、线上 .gateway_spend_approvals.json 不动、核销码不进 stdout）。

**强度到哪为止**：这三条钉的是【走 8100 门控 + 走 runner + 走 approve】的路。
agent 绕开这三处自己拼 HTTP，判据物理上看不见；`prefill_gap` 只在**必填素材格**上工作，
客户手上真没有的素材留空是正当态，不会拦。所以它保证的是"料在手却没挂上"这一种形状当场失败，
不保证回填内容质量高 —— 质量靠「AI 已填 · 可改」+ 依据悬停那人眼通道。
