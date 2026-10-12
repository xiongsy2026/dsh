# 超级复刻五步流程（交表 → 出片）—— 每一格由谁触发、拿什么证明、断了会怎样

用户 2026-10-12 定的口径：**流程本身是目标**，一条片子跑不跑得成是上游的事。
这份表把五步之间每一次"交接"钉到具体的码与盘上凭据 —— 判据不写"agent 应该会…"，
只写"这一格由谁必然执行、留什么痕、没做会红在哪条闸"。

| # | 步骤 | 谁触发（不需要人想起来） | 盘上凭据 | 断了会怎样 |
|---|---|---|---|---|
| 1 | 客户交表 | 右栏【提交这张表】→ 8100 `form_submit` | `.dsh/form_payload.json`（`params`+`files`+`wake.at`） | 载荷没落 ⇒ 页面直接报错，`check-skill-form` D 组 |
| 2 | 交表即点火进管线 | 8100 `kickoff_compile`（**服务端起**，`--stage compile` 常量写死、DETACHED 无窗口）**或 agent 自己跑同一条** | 任务目录 `.compile.pid` + `compile.log`（🔴 牌子**谁起谁写**：入口 `claim_compile_pid` 自挂，2026-10-12 前只有服务端写 ⇒ agent 那台在盘上等于不存在） | 同任务已有活的不许起第二台；**入口自己也拒**（`compile_collision`，闸 D31–D31d）；**跑到一半死了 ⇒ 第四份唤醒来源 `kind=compile_died`**（牌子还在、进程没了、落牌超 30 秒；销账只删报上来的那个 pid） |
| 3 | 编译完成 → 报价 | runner 跑完回调 `POST /api/skills/form/compiled` ⇒ 唤醒来源 `kind=compiled` ⇒ 宿主插件 `Agent.followup`（收不到就**出声**：以前 200 + `ok:false` 被整个丢掉） | 每段目录 `.skill_receipt.json`（**打钩单**：必需 6/6、每格带现读模型名）+ `material_analysis.json` 的 `auto_spent` | 回执缺格 ⇒ `verify_or_raise` 在花钱前拒；叫醒没领 ⇒ `check-form-wake` Z1（部署一致）红 |
| 4 | 报价 + 人点批准 | `gateway.py approve` 建 pending（段数/分数只许 `--count-from` 从盘上算）⇒ 置顶批准窗一次点击 | `.gateway_spend_approvals.json` 的 `attest=dsh-card-click` | 没回填过表 ⇒ approve 不建条子（`form_prefill_blockers`）；**免批只能登记这条腿自己的 purpose**，借别的线的 purpose 算绕开流程 |
| 5 | 提交 → 拿到视频 | `run.py --stage submit`；**提交成功那一刻任务号落盘**（`wan3_job.json`，网关 `_yz_prime_note_job`）；被打断就 `--stage wait` 只捞不投 | `wan3_job.json` + 成片 mp4 | 🔴 **有号没片 ⇒ `--stage submit` 直接拒**（`submitted_but_unpaid`，闸 D33）：判据只看盘上有没有号，不看它用什么姿势拼参数 ⇒ 拿 `_wan3_request.json`（**物证转储，不是输入口**）或自造子命令重投这条路物理封死；agent 收工前会被 `agent/turn-stopping` 问 `obligation_view`，有活没完就 `steer` 回去继续盯 |

## 这条流程的三个"物理约束"（写下来免得又被当成偷懒借口）

1. **DSH 前台命令 10 分钟被强杀**：`@deepseek-ai/dsh-pwsh-local` 的 `maxTimeoutMs=6e5`（默认 2 分钟，
   agent 自己传更大也夹到 10 分钟），Windows 下强杀只报 `[exit code: 1]`。一条 wan3-prime 生成实测
   **10–17 分钟** ⇒ "提交 + 等终态"写在同一条前台命令里必然被打断。正解是第 5 步那两件事：
   号当场落盘 + `--stage wait`；长命令一律 `run_in_background: true` + `job_output` 收。
2. **叫醒只有四个事件源**：表单提交 / 批准核销 / 编译跑完 / **编译半路死了**（第四份是**从盘上推出来的**，
   不是回调 —— 死掉的机器自己报不了，2026-10-12 现网那台 35196 就是这么静默没的）。
   "后台那条命令跑完了"**不是事件**，所以第 5 步不能靠"等会儿想起来再看"，只能靠
   `agent/turn-stopping` 反过来拦（有活没完 ⇒ 不许收工）。
3. **上游挂了不是流程的失败**：`prime` 生成端 503 时（证据：空 prompt 回 `400 PROMPT_REQUIRED` ⇒ 服务活着，
   合法形状回 `503 GENERATION_ERROR` ⇒ 生成侧不可用），流程的正确表现是**不丢工作、不双花、看得见状态**：
   编译产物与打钩单已在盘上、预扣失败会回滚、`gateway.py holds` 查得到、重试由网关适配器内部的
   对账环决定（"没建上"才重投，"已建上/已计费"只捞）。

## 现在还剩两格没闭环（都不是判据问题）

- **两半都要重启才生效，而且有先后**：① 先**完整重启 DSH**（不是 Ctrl+R）让宿主插件带上死牌子的文案与
  `ack{pid}` 那半（`check-form-wake` 的 Z1 恒红就是在指这件事）；② 再**重启 8100**
  （先 `hypit_bridge.py preflight` 看到 `safe_to_restart:true`）让 `wake_view` 开始发第四格。
  🔴 反过来的顺序会造出一段"半条链"：老插件收到 `compile_died` 会走**表单那条兜底文案**
  （它对未知 kind 就 fallthrough），把"编译死了"念成"客户刚交表"—— 内容大致能行动，但那是假话。
  没重启期间，第 3 步的 `compiled` 叫醒、死牌子的叫醒与第 5 步的收工拦截只活在源码里。
- **`wan3_job.json` 只在"号拿到之后"才有**：上游 503 到不了那一步时，第 5 步的重试由
  适配器内部环（4 次、退避 45/120/240s）+ 管线外层 attempt（3 次）覆盖约 20 分钟；
  超过这个窗口就要人再点一次提交 —— 这一格**故意没有做成"永远重试"**（防无限烧厂商调用），
  要延长就调 `prime_attempts` / `prime_backoff`，别手搓看门狗脚本。
