# PRD：交付闭环（delivery loop）—— 根治"视频没出来就停止运行"

状态：设计定稿（P0 待落）｜日期：2026-10-05｜负责人：产品
关联：`docs/ADR-model-gateway.md`（同一套预扣/结算）、`docs/ADR-workbench-presence.md`（判定口与右栏）、全局行为层 `$DSH_HOME/AGENTS.md` §4/§5

## 1. 问题与根因（实测，不是推测）

停手只由一行判据决定：

```js
const toolCalls = message.content.filter(b => b.type === 'tool-call')
if (toolCalls.length === 0) return { kind: 'completed' }   // _setup/dsh-harness/packages/core/agent-loop/src/agent.ts:486-487
```

⇒ **模型这一轮不调工具 = 内核判"完成"，没有任何东西检查产物在不在盘上。** 所以"说视频会出来但没出来"不是它故意撒谎，是判据缺项。

三个放大器（都已定位）：
1. **唤醒预算**：`packages/jobs/tool-jobs/src/index.ts:48-51` 默认 `maxConsecutiveWakes:3`，`:293-298` 预算花完就退化成 `owner.inject()`（进队列、不开新一轮）。多阶段链路每阶段烧一次 ⇒ 到"成片该播报"时预算早空。已 overlay 成 64（`_setup/patch_wake_budget.py`，闸 10 条），**未实测**。
2. **状态没主人**：整条链上没有任何地方记着"这个会话还欠几条产物"。jobs 表记已发生的任务，holds 表记预扣，**"期望"这张表不存在** ⇒ 谁都无从判断"能不能收工"。
3. **规则层放错**：交付铁律一直写在 `C:\dsh\AGENTS.md` = 工作区级（内核只沿 `ancestorChain(projectRoot,cwd)` 找文件，`dsh-agent-instructions/lib/index.js:578`）⇒ 客户机读不到。2026-10-05 已补全局层 `$DSH_HOME/AGENTS.md`（闸 14 条）—— 但那只是降概率。

## 2. 目标 / 非目标

**目标**：把"能不能收工"从模型手里拿走，交给一个有状态的主人；跑完的东西**必然**回到对话里（路径 + 直链 + 贴图）。

**非目标（🔴 越界即违规）—— 这 7 条与 `docs/tasks-delivery-loop.json` 的 `hard_boundaries` 逐字一致，两份必须同步改**
1. 不靠提示词根治：提示词只改模型倾向，判据才是根治点。
2. 不扩范围：加段数 / 换更贵路由 / 改片型一律回到原报价流程。
3. 不双花：已判死但上游可能仍在跑的任务不许重提交，用 `job_id` 捞回。
4. 不推翻三道批准关口（方案 / 切段表·投放草稿 / 成片验收）与花钱前报价；重启常驻服务前先 `preflight` 且要看到 `safe_to_restart:true`。
5. 同一失败原因连续 3 次不通必须停下报证据，防止"不许停"退化成无限烧钱。
6. 绝不往生产账本 / 状态库写假成功样本（要测就造真文件）。
7. 不新增批准类，不新增"要不要我继续"式提问。

## 3. 分层方案（R0 最便宜、R2 是地基、R1 才是判据）

| 层 | 做什么 | 落点 | 碰内核？ |
|---|---|---|---|
| **R0 少决定点** | 多阶段链路一次提交、引擎侧跑到终态，agent 只在终态被唤醒播报 | omni 的 pipeline 本来就是这种；改的是"agent 别在阶段间手调"，写进各 SKILL.md 的驱动契约 | 否 |
| **R2 义务台账**（地基） | 一张"期望清单"：这次任务欠哪些文件、几段、哪个会话 | `<工作区>\.dsh\delivery.json`（**必须在工作区内**，DSH 沙箱写不了工作区外）；8100 只开**读口**，写由各提交方直接落文件 | 否 |
| **R1 收工拦截**（判据） | `completed` 判据改成"无 tool-call **且** 义务清单为空"；非空则强制再来一轮 | 两条路见 §4 | 是（或半是） |
| **R3 唤醒预算** | 3 → 64 | 已落 | 否（overlay） |
| **R4 交付自动化** | 落盘后自动生成"路径 + 8100 直链 + 抽帧贴图"三件套，不靠 agent 记得 | `scripts/deliver_pack.py`（新） | 否 |

## 4. R1 的两条实现路线（P1 先探 A，A 不通才走 B）

- **A. 服务端模块挂 wake 通道**（不碰判据源码）：义务未清 + 会话空闲 ⇒ 由服务端模块触发一次 followup（复用 `completionDelivery:'wakeup'` 现成通道，只是触发者从"任务完成"换成"想收工"）。
  ⚠️ **待坐实**：我们自有的 Cordis 模块能不能注册成 **server 侧**（客户端插件没有往 agent turn 注入的权限）。探针：`packages/bundle/base/cordis.patch.yml` 的挂载能力 + `tool-jobs` 的 `owner.followup` 归属。
- **B. 直接 patch 编译产物里那两行判据**：加"义务清单非空 ⇒ 不许 completed"。风险=内核升级漂移 ⇒ 必须注册进 `rebuild_weeksir_desktop.py` 的 `PATCHES`，并过 `verify_kernel_consistency.py`。
- 🔴 两条都要有**兜底出口**：同一原因连续 3 次不通 ⇒ 停下报证据（taskId / 账本 error / 上游原文），防止"不许停"退化成无限循环烧钱。

## 5. R2 数据契约（谁写、谁销、谁读、什么时候作废）

```json
{ "session": "session-<uuid>", "task": "nose-cream", "skill": "omni",
  "expected": [{ "seq": 1, "path": "productions/nose-cream/outputs/seg1.mp4", "kind": "video" }],
  "budget_credits": 200, "created_at": 0, "ttl_sec": 86400, "closed_at": null }
```

- 🔴 **写入者必须是"必然发生的动作"，不能是可选项**（10-02 定案：可选步骤=不存在的步骤）：omni = 建 run 的唯一入口 `workflow.py:204 create_run_dir()`；hypit = `hypit_bridge.py:227`（studio-claim 已经在顺手钉意图，同一个位置加一条）；去水印 = `gateway.py call video.edit` 成功返回处。agent 不需要额外跑命令。
  ⚠️ **待坐实**：旧账上写的"omni 建 run 自动钉意图 = `store.create_run()`"这个函数名**全仓 grep 不到**（只有 `create_run_dir`）⇒ 落 T4 前先确认那处到底在哪、是否还在，别照文档实现。
- **销账者**：产物文件真的出现在 `expected[].path`（大小 > 0）⇒ 记 `delivered_at`；全部 delivered ⇒ 清单 closed。
- **读**：`GET 127.0.0.1:8100/api/delivery/pending?session=<id>` ⇒ 右栏台、R1 拦截、回归闸共用同一个口（照 presence 的先例，不再开第二个口）。
- **作废**：`ttl_sec` 到期自动失效（照意图 TTL 24h 的先例）。🔴 不许"跑过一次就永久欠着"—— 那正是 presence 四修砍掉痕迹兜底的原因。

## 6. 验收（每条都要能自动判，别靠自觉）

闸文件：`scripts/check-delivery-loop.py`（= 任务 T7）。🔴 所有"假传输层"只许替换 HTTP/进程边界，**不许把被测函数整块替掉**（agnes 那次 lane 全绿却漏了查询腿打错主机，就是这个错）。

| # | 断言 | 怎么测 | 落在哪个任务 |
|---|---|---|---|
| V1 | 提交生成后义务清单立刻出现，条数=段数 | 假传输层跑一次提交（0 分），读 `delivery.json` | T3 / T4 |
| V2 | 产物落盘 ⇒ 自动销账；全销 ⇒ closed | 造一个真文件（不写假记录进生产账本） | T3 |
| V3 | 有未销账时，`completed` 判据不许放行 | R1 落地后跑一条"中途故意断"的链路，看它是否自己续跑 | T9 |
| V4 | TTL 到期不再拦收工 | 把 ttl 设 1 秒重跑 V3 | T3 / T9 |
| V5 | 3 次同因失败必须停并报证据 | 桩里连返同一错误 | T9 |
| V6 | 交付三件套自动出（路径+直链+贴图） | R4 脚本对既有成片跑一遍，断言产物存在 | T6 |
| V7 | 不双花：判死任务不被重提交 | 账本里同一 job_id 只出现一次提交 | T4 / T7 |

## 7. 排期与任务清单

机器可读版：`docs/tasks-delivery-loop.json`（字段含 `verify` / `depends_on` / `status`，与全局层 §4"动手前先写验收"同一条纪律）。工时按 JSON 的 `est` 累加。

- **P0 ≈ 10.5h**（不碰内核，先把状态立起来）：T3 台账 + T4 三个写入锚点 + T5 8100 读口 + T6 交付三件套 + T7 闸 + **T11 实测唤醒 64**（T1 已打补丁，只差真跑一条，不被 P1 挡住）。
- **P1 ≈ 1~2d**：T8 路线 A 探针 → T9 收工拦截（判据）→ T13 实测拦截效果。
- **P2**：T10 R0 驱动契约改写（omni / hypit / 去水印三份 SKILL.md）+ T12 规则层收尾（摘 persona 与全局层 §5 的重复入口、装机脚本写 `$DSH_HOME/AGENTS.md`）。

## 8. 两份文档的映射（自查用，改任何一边都要同步）

| PRD | JSON 任务 |
|---|---|
| §1 放大器 1（唤醒预算） | T1（+T11 实测） |
| §1 放大器 3（规则层放错） | T2（+T12 收尾） |
| §3 R2 / §5 数据契约 | T3 + T4 + T5 |
| §3 R4 | T6 |
| §4 路线 A / B | T8 / T9 |
| §3 R0 | T10 |
| §6 V1~V7 | 见 §6 表最后一列，全部由 T7 汇总成闸 |
| §2 边界 7 条 | JSON `hard_boundaries`（逐字一致） |
