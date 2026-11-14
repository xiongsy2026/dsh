---
name: super-clone
description: 超级复刻稳定生产流程。原片换产品（必选），可选换脸/换衣服/换背景/换音色；生成引擎唯一：Wan 3.0（网关 lane video.generate/wan3-prime · provider yz30）；按复杂度切段；模块化提示词；多重零成本检查；按声音模式决定原声贴回或目标音色生成。
whenToUse: 用户要超级复刻、爆款复刻、换产品、换脸、换背景，或提到原片+产品图生成一条结构相同的新片。多脸、短镜、要对齐原声的难单必须用本 skill。
intent: super-clone
form: |
  video|video|原片（要复刻的那条）|yes|1|video|200MB|mark=必填|group=素材|hint=把视频拖进来，或点击选（1 个上限）
  product_images|images|产品图 1–10 张|no|1-10|product_images|mark=选填|group=素材|hint=上传您的产品图（多角度）
  model_image|images|换脸|no|1-10|model_image|mark=选填 · 换人时必给|group=素材|hint=一段一张脸；多脸请一段一段给
  scene_image|images|背景参考图|no|1|scene_image|mark=选填 · 换背景时建议给|group=素材|hint=有目标背景图直接传这里：它作为场景 图N 进 wan3.0，背景定调以图为事实源（2026-10-31 定）
  scene_text|textarea|场景替换描述|no| |scene_text|mark=换背景时必填|group=素材|hint=有背景图时这一格只补图里没有的细节（冲突以图为准）；没有图就靠这段文字定背景
  product_name|text|产品名|yes| |product_name|mark=必填|group=这次要换什么
  customer_request|textarea|请详细描述您的替换需求|yes| |customer_request|mark=必填|group=这次要换什么|hint=写"换什么 / 不换什么"就行，我按原片逐镜编译；背景写在上面那一格，这里不重复
  engine_lock|locked|生成引擎|no|yz30 / Wan 3.0 Prime 1080P（模型名与单价由网关现读）|-|mark=合同锁定|group=引擎与规格|note=1080P · 参考视频迁移 · 按秒计费
  voice_mode|select|声音替换|yes|preserve_original 原声保留;timbre_swap 只换音色;rewrite_text 改口播·音色不变;rewrite_voice 换人声+改口播|voice_mode|mark=必填|group=引擎与规格|hint=改口播＝把您念好的新词音频换到成片音轨上（画面不动，所以嘴形还是原片的）；想让嘴形也对上得再跑一道对齐，那条腿效果没把握，到时会单独问您一次
  voice_audio|audio|新的声音（音频）|yes|1|voice_audio|20MB|mark=必传|group=引擎与规格|show=voice_mode=timbre_swap,rewrite_text,rewrite_voice|hint=只换音色：传一段目标音色样例；改口播：传一段【已经念好新文案】的音频（单段 1–15 秒）
  voice_script|textarea|新口播文案|yes| |voice_script|mark=必填|group=引擎与规格|show=voice_mode=rewrite_text,rewrite_voice|hint=一行一句，句数要和成片口播对得上；不改的那句照原话抄一行
  aspect_ratio|select|画面比例|yes|adaptive 自适应（跟原片）;16:9 16:9;9:16 9:16;1:1 1:1;4:3 4:3;3:4 3:4;21:9 21:9|aspect_ratio|mark=必填|group=引擎与规格
  resolution_lock|locked|分辨率|no|1080P|-|group=引擎与规格|note=这条引擎只出 1080P
  seg_lock|locked|段数 / 切段|no|自动按镜头切|-|group=引擎与规格
---

# 超级复刻（super-clone）

你是超级复刻制作 Agent。生成器是 **Wan 3.0**。目标是最小迁移：换掉商品身份和可见外观，锁死运镜、非商品动作、非商品口播。

完整编译器合同：`references/compiler-contract.md`  
Linter 合同：`references/linter-contract.md`  
管线：`references/pipeline.md`  
难单切段：`references/hard-face.md`  
Wan 边界：`references/wan3-limits.md`

Hypit skill 只在抽帧、裁切、原声贴回、写项目文件时加载。不要用 Hypit 的「按新词重做关系」覆盖本复刻合同。

## 先做的事（顺序是死的，2026-10-11 重排：agent 是衔接层，不是分析层）

🔴 **第一条命令就是交接，不是分析**。现网形状（session-71b9a32d）：客户 20:17 点单，agent 先抽帧、
看图、写 BRIEF/ANALYSIS，44 分钟后表才出现在客户眼前（还是他自己把素材传完才出现的）——
那 44 分钟没有一分钟在为他干活。所以顺序倒过来：

1. `python scripts/handoff.py super-clone [任务名]` —— 一条命令做完：登记意图、建任务目录、
   把手里**无歧义**的素材归位到对应槽、把右栏那张表弹给客户。**一个模型都不调**。
   拿不准的格子一律留空让客户自己选（🔴 不许猜一个值填进去 —— 填了就等于替客户做决定，
   而且错了没人知道）。需要理解才能定的两格（产品名、需求描述）按客户原话补，跑
   `python scripts/intent.py fields` 拿 key，别凭记忆。
2. 等客户在右栏点【提交这张表】（会被叫醒，不要催、不要代发）。**交完表就没有任何东西需要猜**：
   原片与产品图是服务端存的绝对路径，声音档/比例是客户按下的。
3. 🔴 **不要自己去跑 `video.analyze`**（2026-10-12 把执行人锁进管线）：合同 `material-analysis` 那一步
   由**管线**经 `gateway.invoke("video.analyze")` 自动补跑 —— 会预扣、会结算、会记台账，
   实扣写进 `material_analysis.json` 的 `auto_spent` 并计入这次报价。熔断默认 120 分（`SUPER_CLONE_ANALYZE_CAP`）。
   你在这条路上只做一件事：**需要换背景时，把客户给的背景图经 `--scene-image` 传进引擎**（图为背景事实源，2026-10-31 定），图里看不到的细节写成文字进 `--scene-text`。
4. 🔴 跑 `python .dsh/skills/super-clone/runner/run.py --from-form --stage compile` **必须 `run_in_background: true`**
   （**不提交、不进执行器**）出分镜与提示词，对着 `frames/` 逐秒核；分镜/动作标签失真就补 `--extra-note` 重跑同一条。
   为什么这条也要后台：一条编译实测 **7–12 分钟**（裁片 + ASR/demucs + MiMo 分镜 + 编译器），前台到 10 分钟
   就被工具强杀 ⇒ 产物没落、牌子变死牌、又得整条重跑（2026-10-12 现网 18:27 起连死两台就是这个形状）。
   🔴 这一档就是"重编译不烧 Wan"那句散文的合法出口 —— 不许再绕开入口手搓 driver。
5. 认可后去掉 `--stage`（= `--stage submit`）⇒ 报价（五格现读）⇒ 等人在批准窗点一次 ⇒ 提交 ⇒ 盯到终态交付。
6. 🔴 **提交那一下必须"后台跑"，等结果用 `--stage wait`（2026-10-12 补，现网 session-5282eabf）**：
   DSH 的前台命令到 **10 分钟会被工具强杀**（`dsh-pwsh-local` 的 `maxTimeoutMs=6e5`，自己传更大也夹回来），
   而一条 wan3-prime 生成实测 **10–17 分钟** ⇒ "提交 + 等终态"写在同一条前台命令里必然被打断。
   跑法：`run_in_background: true` 起 `--stage submit`，用 `job_output` 收；被打断/没等到也不要紧——
   **提交成功那一刻任务号就落在成片旁边（`wan3_job.json`）**，之后任何一条
   `python .dsh/skills/super-clone/runner/run.py --stage wait` 都能按号把片子捞回来（只 GET，一个提交请求都不发）。
   🔴 三条禁令：① 没等到 ≠ 重投（重投=双花，平台按次计费）；② 不许绕开入口自己拼 HTTP；
   ③ 盘上没有 `wan3_job.json` 却说"已提交"= 谎报 —— 那一刻只是**我们预扣了**，上游有没有任务，
   以号为准（`gateway.py holds` / 号在不在盘上）。没到终态就收工会被宿主插件拦回来（`agent/turn-stopping`）。

素材与事实的口径照旧：原片、产品名、≥1 张产品图是花钱前必须真拿到的；换脸要清晰面部图（一张脸对应
一段时间）；换背景 = **背景图 + 文字**（客户给参考图就经 `--scene-image` 送进 wan3.0 作场景 图N，背景定调以图为事实源；文字只补图里没有的细节，客户没图时才纯文字）。
简单单（只换产品、≤15s、一张脸或无人脸）可以整段一次走管线；难单（多脸、快切、<2s 镜、要对齐原声）
先分析写出 `TIMELINE.md`，**等人批准后再打 Wan**。

## 输入

必填：原视频、产品名、产品图 1–10 张。  
可选：模特/脸图（多张时必须带时间映射）、**背景参考图 `--scene-image`（换背景时建议给：本地路径 → 场景 图N → 随素材上传进 wan3.0，背景定调以图为事实源）**、**场景替换描述 `--scene-text`（有图时只补图里没有的细节；没图时是唯一背景输入口）**、音色参考音频（`--voice-audio`，换人时必给，目标说话人干净人声 1-15s）、customer_request、裁剪区间。🔴 **引擎不可选**：本 skill 只有 wan3.0 一条路线（表单 `engine_lock` 锁死 yz30 / 1080P，2026-10-30 换芯，之前是 tianyue）；`seedance_params` 是已废的历史字段，只读不生效。 

### 生成引擎不由 agent 挑：一条片子只有一条模型（2026-10-30 用户定"wan3.0固定换"，接着 10-22 那条"不许失败换档"）

- **wan3.0 = 网关 lane `video.generate/wan3-prime`（provider `yz30` = youzan666 的 Prime 独立工作台）**，
  同一个 purpose 下**只接 `wan3.0-video-prime`（按【秒】计费）一条**：短段与尾巴也走它。
  🔴 2026-10-30 之前这条指 `provider tianyue / ME-WAN3-prime-1080p-1055`（按次）—— 已换芯，
  旧那条现在只剩 `video.generate/wan30-studio`（直出工作台，另一会话的活）还在用，**别把两条当一条**。
  10-22 那句"一条片子里两段用两个模型、两种计费口径 ⇒ 画面与账一起漂"照旧成立；
  换芯之后【更严】：新这家按秒计价，所以一段的秒数直接就是价格，`duration` 不许为了省钱或省事改。
  唯一源 = `.env` 的 `WAN3_PRIME_YZ_BASE_URL` / `WAN3_PRIME_YZ_API_KEY` / `WAN3_PRIME_YZ_MODEL`，
  适配器 = `gateway._yz30_video_run`（`POST /v1/assets` 送素材 → `POST /v1/tasks` 带 **Idempotency-Key**
  → `GET /v1/tasks/{id}` 盯到终态 → 落盘后**必须 ffprobe 读得出视频流**才算到手）。
  入参合法域（从它自己的参数卡与文档读，不是猜）：duration 2—30 真实秒、resolution 只 720P/1080P、
  ratio 七档、素材 图≤10 / 视频≤5 / 音频≤5 / 总≤20，数组顺序=引用顺序。
  提交腿在 `backend/services/wan3_client.py:run_one_segment_prime`，经 `gateway.invoke` ⇒ 预扣/结算/审计齐全。
  🔴 幂等 key 由【这次提交的参数】派生（`gateway._yz30_idem_key`）⇒ 同参数重发平台按编号恢复、不重复预扣；
  但上层纪律照旧是"**先盘上的号，只捞不重投**"—— 平台能给幂等不等于可以拿重发当恢复手段。
- 🔴 **不许失败换档，也不许自己把 PIX 填回那一行**：要接回按秒那条 = 回来重新报价（换计费口径就是"批的不是跑的"）。
  40 秒 = 15+15+10 ⇒ **三条都按次**；不许为省事出 30/40 秒单段。
- 🔴 **duration 的口径跟着模型名走**（2026-10-22 撤掉全局 `TIANYUE_DURATION_MODE` 那一键）：尾巴 `-se` = 按秒
  （`duration` 与 `video_duration` 都传真实秒数），无尾巴 = 按次（`duration` 固定传 1）。
  判据唯一源 `gateway.wan30_is_per_second()`，本页与报价都不许再自己写一种口径。
- 🔴 **不许在方案或报价里提这两条之外的引擎**（Seedance 2.5 / RunningHub wan3 都不行）：前者不是这条线的入口实现，
  后者按 2026-10-09 禁令默认断开（`config.WAN3_RH_ENABLED=False`，走入口提交不出去）。
  要换路线=换 skill、重新报价，不是在这里替客户选。
- 报价里"路由"那一栏的**上游与单价必须现读**：`python backend/gateway.py prices` 与
  ROUTES 的 provider；**不许凭记忆写、不许写"RunningHub"**（2026-10-10 就是这么被读错的）。
- 表单里 `engine_lock` / `resolution_lock` 是**只读🔒**。🔴 引擎那格的值**由服务端现读网关**
  （`model_admin.engine_lock_view` → `.env` 两键 + `WAN30_FULL_SECONDS`），字段表里第 5 列只是"读不到时
  的回落串"、故意不写模型名 —— 写死一条就会漂成"表单一个引擎、提交另一个"（2026-10-22 现网：那格还挂着
  改名前的 `ME-wan30-1080p-1055`，而这个名字在网关的**不接名单**里）。客户能选的只有素材、比例、声音档这些真入参。

**换人换声联动**：换了说话人身份（男↔女/换演员）⇒ 必须同时给 `--voice-audio`，口播音色按参考音频合成，原声不贴回；只换产品/背景不换人 ⇒ 不给，原声照常贴回。

产品图 + 脸图 + 背景图合计参考图 1–10 张。换背景 = **图优先、文字补充**：客户给的目标背景图经 `--scene-image` 直接进引擎（场景 图N，只控制背景家具/墙面/地板/光线，图中人物/商品/文字一律不得继承）；转写出来的背景细节文字要标「AI 已填 · 可改」并给依据（哪张图）。

## 表单（右栏输入口，唯一源 = 本文件顶部 `form:` 段）

挂点 = 8100 的 `/ui/super-clone`，出现条件 = 这条会话登记过 `intent.py super-clone`。
字段表只有那一份，渲染器/插件里不许再抄。三条不显然的口径写在这里：

1. **`product_images` 在表单上是选填，在引擎上是必填** —— 这不是矛盾，是两件事：
   表单不拿它卡住客户提交（客户可能随后在对话里发图）；但**花钱那一刀之前 agent 必须真的拿到 ≥1 张**，
   拿不到就停下来要图，绝不拿原片里的旧商品当"已经换好了"。入口这条硬判据现在在
   `runner/run.py:missing_required`（合并完表单值之后还空着 ⇒ 点名拒，0 分）——
   以前它是 argparse 的 `required=True`，那等于强制"由人敲命令行"，把 agent 钉成表单到引擎之间的
   人肉转录层；改成表单能覆盖之后，**缺格照样拒，只是不必再抄参数**。
2. **`voice_script` 不进 wan3 的入参，但它是【后处理腿的输入】**（2026-10-13 改口径）—— 只有改文案的两档有意义：
   `rewrite_text` 已接（`voiceover_replace.run_narration_swap`，见『声音模式与换音色』），`rewrite_voice` 未接、入口直接拒。
   所以 agent 读到非空 `voice_script` 时：按后处理腿报价（**每改一句 = 一次 RunningHub 克隆**，
   不在网关账本上 ⇒ 按余额 + instanceType 口径说、跑完回报 usage），等批准，再决定走哪条流程。
   🔴 不许把它塞进 `--customer-request` 蒙过去 —— wan3 不会照着它念，塞进去的结果是"客户以为改了、交回去还是原话"。
3. **`voice_mode` 与 `voice_audio` 必须自洽** —— 判据在 `runner/run.py:voice_mode_problems`（要换音色却没给人声 = 拒；
   要原声却给了人声 = 拒）。表单不做这个校验，入口做，所以绕过表单直接敲命令也拦得住。
4. **表没出现过 ⇒ 入口不进引擎** —— 判据在 `runner/run.py:form_gate_verdict`，落在【导入引擎之前】：
   这条会话登记了 super-clone，但既没回填（`defaults`/`default_files` 全空）也没被客户提交 ⇒ 直接 `SystemExit`，0 分、不动盘。
   🔴 但"弹表"【不再等回填】（2026-10-11 再改判，推翻 10-10 那版）：登记 + 配了字段表就弹、空表也弹 ——
   10-10 版把"agent 已回填"当弹表前置，实测代价是客户对着空右栏等几十分钟、中间看不见 agent 卡在哪。
   压力没取消，只是换了位置：**空表可以弹，但空表换不来一次扣费**（`gateway.py approve` 先问回填覆盖率，
   没回填 ⇒ 不建待批条子、0 额度）。
   回填走 `python scripts/intent.py prefill --set @模板.json`，key 先跑 `python scripts/intent.py fields` 拿
   （🔴 猜 key 会被入口退码 6 拒、盘上一条都不写：2026-10-11 现网猜错之后退出码是 0，agent 以为填好了，
   客户第二天把已经在手的素材自己又传了一遍）。素材归位不用手写命令，`handoff.py` 那条会做无歧义的部分。
   ⚠️ 强度边界：这道门槛管得住"走入口"，agent 自己拼脚本直接 import 引擎函数仍然绕得开 —— 那一条靠
   合同步骤的 `手段=` 白名单拦（`storyboard-fact-check` 已登记 `runner:--extra-note`、
   `material-analysis` 已登记 `pipeline:invoke:video.analyze`（2026-10-12 起执行人是管线，不是 agent）；花钱那一刀还额外认回执，见 `docs/ADR-skill-contract.md`）。

## 硬规则

1. 一段只钉一张目标脸，或无人脸。禁止 2 秒里塞两张以上不同的脸。
2. Wan 单段 2–15 秒。不足 2 秒：生成 2 秒，再按原窗口裁回。超过 15 秒：按 15 秒切段。
3. 同一张脸不足 2 秒，把旁边无人脸空镜并进这一段凑时长。
4. 2 秒窗口里硬要两张不同脸：这一窗只换一张（换脸优先成功）。
5. 换脸只取脸/五官/发型/肤色，忽略模特图衣服和体型。成片服装跟原镜头或产品图。
6. Prompt 里脸部可见镜必须「严格按 图N」并显式写五官，禁止弱引用「图N 中的女性」。
7. 成片默认无字幕、无花字、无账号水印。产品包装上的印刷按产品图保留。🔴 **这句只保证"模型不额外生成"，管不了参考视频里已经烧进画面的字幕**（见『参考视频是强事实源』那条，2026-10-14 实测推翻过一次的 over-promise）。
8. 口播引号内逐字保留。Wan 直出跟原声。改口播不走本 Compiler——走 `voice_mode=rewrite_text` 的后处理腿（见『声音模式与换音色』）；🔴 不许只在 prompt 里写新词就当"口播已改"，那条 lane 不会照着念。
9. 不得增删合并重排 Shot，不得改 Shot 时间。`target_duration` 由原片/裁剪锁死。
10. 原商品不可见的镜头禁止注入目标产品和产品 图N。
11. 批准只发生在：TIMELINE、失败重跑、成片。不要每段 Wan 都问一次。
12. **动作/节奏是硬约束**：MiMo 解析出的"手部律动/产品动作"字段常被压缩成静态循环标签（如"反复拉扯"），与对镜的抓-撸-停节奏不符。**送 Wan 前必须对原片核过该段动作标签**（见"编译产物自查"）；不符则在 extra-note 里按秒写死动作时间轴压过解析标签，不得让压缩标签直接进 Wan。
13. **换人必须换声**：换了说话人身份（男↔女、换演员）⇒ 必须同时给音色参考（`--voice-audio`，目标说话人的干净人声音频 1-15s），Wan 生成时按参考音色合成口播，**原片原声不贴回**（贴回原声 = 男脸女声，直接穿帮）。只换产品/换背景不换人 ⇒ 不给 `--voice-audio`，原声照常贴回。给不出参考音频时明说"该单无法换声，保留原声"，不许默认用原声糊过去。

## 稳定生产总流程

复杂复刻必须按以下顺序执行，不得跳过前置闸：

```text
素材登记 → 意图合同 → 复杂度分级 → 参考源确认（只看不动画面）
→ 原片分析 → TIMELINE 提案/论证/人审 → 冻结切段
→ 模块化 Prompt 编译 → 参考源/结构/意图/动作/声音五重零成本检查
→ 报价与批准 → 按段生成 → 候选质检 → 段间接口检查
→ 音频策略执行 → 成片终检 → 交付（跑 scripts/deliver_pack.py，不手写三件套）
```

### 1. 意图合同：先把用户的话变成可验收事实

在 `BRIEF.md` 写五换矩阵，并把每项标为 `LOCK`（必须保持）、`SWAP`（明确替换）或 `FORBID`（出现即失败）：

| 模块 | 必须登记 |
|---|---|
| 产品 | 目标产品、产品图、出现时间、原商品是否全部替换 |
| 脸 | 脸图、时间绑定、只换脸/发型/肤色还是换演员 |
| 衣服 | 换哪些衣物；袜、鞋、配饰哪些保留 |
| 背景 | 背景图为事实源（`--scene-image`），文字补细节；全程统一还是随原片变化 |
| 动作 | 严格参考原视频，还是明确允许改动 |
| 口播 | 原文逐字、改稿或删除；时间和断句 |
| 声音 | 原声保留、只换音色，或换人声/换文案 |
| 禁止项 | 字幕、水印、原商品、错误衣服、错误背景、皮肤瑕疵等 |

未写进合同的内容不自行推断；合同一旦批准，后续只允许变更明确的 `SWAP` 项。

### 2. 复杂度分级

按视觉替换、时间结构、动作表演、音频要求分别评估：

- **L1**：只换产品，单脸/无人脸，无快切，无裸露皮肤和换声。
- **L2**：换脸/背景/衣服，或有裸露皮肤、明显手部动作、单个硬切。
- **L3**：多脸、短镜头、快切、复杂商品交互、换音色或口型要求。
- **L4**：多项同时变化，且要求动作、口播、背景、产品和音色全部严格对齐。

L2 以上必须写 `ANALYSIS.md` 和 `TIMELINE.md`；L3/L4 必须保留候选结果和逐段评分，不以第一条 SUCCESS 为交付。

### 3. 参考源确认闸（零成本 · 🔴 不做任何像素处理）

Wan 的参考视频是强事实源，权重大于普通文字。提交前必须确认参考视频：

- 画面保留原动作、节奏、机位和口播时间关系；
- 不希望继承的烧录字幕、花字、标记和水印：🔴 **负向锁定只能让模型"不额外生成"，擦不掉参考片里已有的字**（2026-10-14 实测：`prompt.txt` 里「不得出现字幕」在位、合同 `negative-lock` 那一格照常打钩，成片每 2 秒取一帧仍 **8/8 带原片字幕**，而且是**重新渲染**不是像素拷贝——源片"新上架了一套保暖套装"被写成"新上架了一辈好暖夸装"）；
  合同 `negative-lock` 的物证与判据（手段在 `super_clone_wan3.py:275` 后端常量 + `:669` 编译器禁令，判据 `text-has=`）**只查 prompt 文本、不查像素** ⇒ 它不是交付质量证据；字幕这一项的验收口是**出片抽帧贴给人看**（本文件已有的抽帧质检要求）；
  🔴 **不许对原片做像素级清洗，更不许手搓 ffmpeg/自造去字幕工具**——那不在合同授权的手段名单里（2026-10-09 事故根因）。要消掉已烧录的字幕只有两条正解：**换无字幕源**，或把 `video.edit/dewatermark`（单价现读 `gateway.py prices`、默认要批）**先登记成合同步**再走；
- 原片本身就不合格（烧录字幕占比大、水印压在产品上、镜头混了别的场景）⇒ **换源**，或在提交前由人确认换哪条源；
  🔴 **`voice_mode=rewrite_text`（改口播）的单子这条是硬前提**：音轨换成新稿、画面还留着原片字幕 ⇒ 看到的字与听到的话是两套词，那是穿帮不是瑕疵；
- 🔴 **要改口播的单子，源片还得避开「正脸说话」镜头**（2026-10-14 两条实测，别再试第三次）：口型只能靠重画型腿（LatentSync / InfiniteTalk 这类），
  而人自己手跑的那条 15s 已经证明它会把画面改坏 —— 正脸段脸被改糊、**本来没有脸的段（手/产品特写）头整个消失**、
  9/9 帧的烧录字幕被重画成乱码、每帧左上角多一个 `RunningHub AI` 水印（样本 `D:\Backup\Downloads\InfiniteTalk_*.mp4`，720×1280 / 17.1s）。
  ⇒ 选源硬条件：**口播走画外音**（说话人不正脸出镜）+ **画面无烧录字幕**；
  两条做不到就**不许承诺改口播**，改成"只换音频、口型不对"并在交付里明写（不许拿"腿能调通"当"效果已验"）；
- 场景与目标背景意图不冲突；
- 没有会被误认为目标外观的旧产品/错误服装；
- 只切当前段，不带前后镜头。

参考源不合格时**换源**或按上面的授权手段处理，不靠追加形容词补救，不得直接花 Wan 生成费。

### 4. 模块化 Prompt：禁止散文式全量重写

最终提示词固定按以下模块编译，并保存基准版与本次 diff：

```text
[GLOBAL_CONTRACT] 时长/比例/分辨率/默认无字幕水印
[ACTION_LOCK] 动作/节奏/机位/运镜严格参考原视频
[PRODUCT_SWAP] 产品图、可见时机、原商品全替换规则
[FACE_SWAP] 脸图绑定、只取脸五官发型肤色
[CLOTHING_SWAP] 换衣服与保留的袜鞋配饰
[BACKGROUND_SWAP] 背景图为事实源、文字补细节、背景连续性
[VOICE_LOCK] 文案、节奏、音色来源、原声处理策略
[NEGATIVE_LOCK] 字幕/水印/错误产品/错误衣服/皮肤瑕疵
[TIMELINE] Shot 时间、脸绑定、产品可见性、口播范围
```

只修改一个模块；编译后必须对比其余模块无漂移。720P、换参考源或改负向词不得重新运行 MiMo 改写已批准的 Shot 表。

### 5. 五重零成本放行闸

送 Wan 前逐段通过：

1. **参考源闸**：参考视频无不应继承的文字/水印/场景污染，动作仍完整。
2. **结构闸**：Shot 数、时间、时长、脸绑定、产品可见性与批准版一致。
3. **意图闸**：产品/脸/衣服/背景/保留项/禁止项逐块匹配 BRIEF。
4. **动作闸**：逐秒对原片核动作方向、次数、抓-拉-抚-停顿；解析标签失真才按秒补充，不得凭空发明动作。
5. **声音闸**：口播逐字、音频模式、音色参考 URL、原声贴回策略互相一致。

任一闸失败只重编译或清洗，不提交 Wan。

## 逐段统一参数合同

每段都必须使用同一份 `BRIEF.md`、同一份批准后的 `TIMELINE.md` 和同一份验收清单，避免接力时图意漂移。

### Wan 3.0 参数包（唯一引擎）

```yaml
engine: wan3-prime
lane: video.generate/wan3-prime
provider: yz30
reference_video: 当前段干净原片（托管成 HTTPS 外链后送进去）
reference_images: 当前段产品图/脸图/背景图（--scene-image 给定时作为场景 图N 一起进引擎）
voice_reference: 目标音色参考（仅 timbre_swap）
resolution: 1080P        # 这条 lane 只出 1080P
ratio: adaptive | 16:9 | 4:3 | 1:1 | 3:4 | 9:16
prompt: 模块化复刻 Prompt
```

### 视频传参硬规则

1. `reference_video` 必须只包含当前生成段，不带前后镜头；
2. 参考视频承担动作、节奏、机位和口播时间事实，不承担目标产品、目标脸、目标衣服、目标背景事实；
3. 参考图按模块传入：产品、脸、衣服不得混淆，且每段只传当前段实际需要的图；背景图（`--scene-image`）作为场景 图N 进参考图，只控制背景（家具/墙面/地板/光线），图中人物/商品/文字不得继承；
4. 只换音色时仍必须传原视频逐字口播和目标音色参考，不得把目标音色音频当作文案来源；
5. 禁止把旧字幕、水印、旧产品和错误背景作为参考事实源（不生成 = 靠 `negative-lock` 那段负向词，不是靠清洗画面）；
6. 这条 lane 不支持的参考类型**不得静默丢弃**：要么在提交前明说"这一档这条引擎做不到"，要么换 skill 重新报价；
7. `LOCK/SWAP/VOICE/NEGATIVE/TIMELINE` 语义在任何一次重编译里必须保持一致，只做语法适配；
8. 换参考源或改负向词不能触发重新切段、重新理解用户意图。

### 唯一路线的放行判据

- **wan3.0（唯一引擎）放行**：动作复刻、口播时间轴、单脸绑定和参考视频迁移是第一优先级。
- **不确定时**：不直接生成；先做参数兼容性检查（比例/时长/参考类型/声音档这条 lane 收不收），列不出来就不提交。

## 声音模式与换音色

声音必须单独建模，不能用一句“换音色”覆盖所有情况：

```text
preserve_original：原文/原节奏，audio_urls 为空。实测这条 lane 会把参考视频的音轨带出来
  （2026-10-13 对账：成片音轨与原片音轨做差 -33.3dB，等于同一条），所以这一档**不需要**我们再做贴回。
  ⚠️ 这是引擎行为、不是我们写的 mux ⇒ 交付前按"做差 dB"验一次再说"原声保留了"，不许当契约。

timbre_swap：原文/原节奏不变，audio_urls=目标音色参考（送成 reference_audio）。
  🔴 参数发得出去，但【上游到底换不换音色从未实测】—— 盘上 20 份 _wan3_request.json 的 audio_urls 全空。
  没实测过就不许在报价里写"音色已换成某人"。

rewrite_text：改口播 ⇒ **必须给【新口播音频】**（2026-10-14 用户改口径，同一天实测推翻旧假定）
  旧规则写的是"这一档禁给参考音频、新词全靠后处理换轨"，理由是"音色取自成片自己的原声"。
  🔴 今天打掉它的是一条量出来的事实：**wan3-prime 会把参考视频的原声整条搬进成片**
  （成片音轨与 0-15s 源音轨相关系数 0.884；对成片跑本地 FunASR 听到的是「老铁，现在就去抢…」= 原片词），
  而 prompt 里的台词位即便逐字是新词，模型也【没有照着念】⇒ "只改文案不给声音"= 口播根本没改。
  所以这一档现在的入参是两件：`voice_script`（新文案）+ `voice_audio`（一段已经念好新词的音频），
  后者作为 `reference_audio` 送进 wan3 —— 上游参数卡（`GET /v1/models` 现读）明写
  `media_types` 含 `reference_audio`、`audio: boolean`（默认 true）⇒ **渠道支持传音频，且 `audio` 这个开关以前被我们丢了，现在真送**。
  🔴 仍未实测的是【语义】：上游拿 reference_audio 是"照这条音频念"还是"只取音色"，参数卡没写
  ⇒ 出片后必须跑一次本地 ASR 复验（`verify_narration_audio`，0 分、不发请求），
  复验不过就在交付里明写"口播没改"，绝不允许只凭"参数发出去了"写"口播已改"。
  编译前那根线仍在（`apply_customer_narration` + 物证 `voice_script_binding.json`，双向覆盖 ≥0.999 才放行）
  ⇒ 它保证的是【台词位是新词】，不是【观众听到新词】，两件事别混。
  三条代价必须写在报价和交付里，不许藏：
    ① 口型对齐是【一条单独的腿】（合同 `lip-sync` ⇒ 网关 `lipsync/default`，RunningHub LatentSync）：
       片子到手后 runner 会提这一刀，但它【默认要批】、20 分/次且【未实测】（这条腿在账本里 0 次调用）
       ⇒ 没人点头就不做，片子照常交，只是嘴形和新词对不上（正脸近景最明显）；
       目标音频 = 成片自己的音轨（本地 ffmpeg 抽，0 分），不再引一次 TTS/克隆；
       🔴 如果 wan3 真按新音频发声了，嘴形是同一个模型画的 ⇒ 这一刀可能不需要，先 ASR 复验再决定花不花；
    ② 兜底仍是后处理换轨 `backend/services/voiceover_replace.py:run_narration_swap`（逐句一次 RunningHub
       声音克隆、IndexTTS，🔴 不在网关账本上 ⇒ 报价按 RH 余额口径说、跑完把 usage 报回）；
       有直出物证在 ⇒ runner 自动跳过换轨（一条片子里两条声 + 重复花钱，比不改更坏）；
       ⚠️ 现网 2026-10-14：克隆与口型两条腿都回 `errorCode 605「您的账户余额不足」` ⇒ 这两条都要先充值；
    ③ 时长压不回去就停：atempo 只做 0.5–2.0 倍，再超就不是"说得快"而是变调 ⇒ 点名拒那一句
       （对账阶段先按"字数/秒数 > 6"提醒，别等钱花完才说）。
  句数必须与成片口播逐句对齐（一行一句，不改的那句照原话抄一行）；对不上就拒，不许猜哪句配哪句。

rewrite_voice：文案与音色都换 ⇒ **今天没有实现路径**（要先定"新音色参考取哪一段音频"和"要不要口型同步"），
  入口直接拒并说明可选项（改口播不换人走 rewrite_text；只换音色走 timbre_swap）。
  🔴 不许把它降级成 timbre_swap 蒙过去 —— 那正是"客户以为改了口播、交回去还是原话"的静默冒充形状。
```

### `timbre_swap` 的 Prompt 模块

```text
[VOICE_LOCK]
口播文字严格逐字继承原视频，不改写、不增词、不删词、不换句序；
语速、停顿、重音、情绪和呼吸节奏严格参考原视频；
说话音色使用上传的目标音色参考音频；
目标音色只改变声音身份，不改变口播文案和表达节奏；
禁止使用原视频说话人的原始音色；禁止将原视频原声贴回最终音轨。
```

同时满足：

- 目标音频 1–15 秒、单一说话人、尽量无音乐/混响/多人重叠；
- `audio_urls` 必须是 Wan 可访问 URL，不能把本地路径冒充 URL；
- 换人身份时必须 `timbre_swap` 或独立换声，不能“换脸后继续贴原声”；
- `audio_urls` 非空时，后处理不得执行原声贴回；
- `timbre_swap` 的口播文案仍以原视频转写为唯一事实源。

### 声音生成后检查

逐段检查：台词逐字、句序、语速、停顿、音色、原声是否误混、口型同步、段间断句。音频失败不能用原片原声静默替代，必须报告为声音闸失败。

## 事实权威

- 商品身份：`product_name`
- 可见外观：产品图片
- 非视觉事实：仅 `product_facts` 里客户明确陈述的
- 口播文字：原视频转写；换音色不改变文字
- 目标音色：上传的音色参考音频
- `customer_request` 不是事实源；未确认的卖点不写进口播

## 图 N

后端 / 你在 TIMELINE 里固定：

- 图1..K = 产品图（瓶、袋、糖等不同形态按客户映射，不得互替）
- 其后连续编号 = 脸图（一张脸一个 图N；多脸任务按段只带那一张）

生成某一段时，参考视频只切这一段，只带这一段的脸图和该段用到的产品图。

## 难单回路

```
BRIEF → ANALYSIS（分镜+肤色+产品可见性）
 → 切段三步（2026-10-05 定）：
    3a. MiMo 提案切点：逐镜识别原片硬切点，给帧号/秒数证据
    3b. agent 论证：切点是否压硬切（不闪脸）/ 段长≤15s / 脸绑定段（一段一张脸）
        / 段间背景连续性 → 写 TIMELINE.md（提案+论证+风险点）
    3c. 人审 TIMELINE（批准关口，照旧；批准后才裁剪视频）
 → 按段调用 Wan（可并发）
 → 抽帧：脸是否换成目标图、产品是否换上、有无字幕水印、动作节奏是否对原片
 → 失败只重跑该段（可再拆，仍一段一张脸；重跑=花钱，先报价等确认）
 → 原片音频做主时钟，毫秒裁切贴回
 → 成片验收
```

抽帧和贴回加载 `/hypit`，使用 media frames / cut。Done = 看过成片，并对着 BRIEF 和 TIMELINE 核对。

**切段职责分工（2026-10-05 用户定）**：切点由 **MiMo 提案**（它逐帧看原片，硬切证据在视野里），
由 **agent 论证**（它核全局事实：段长≤15s、切点压硬切、脸绑定、段间背景连续），
由 **人审 TIMELINE** 拍板。agent 不再自己拍切点（13.67s 切在镜头中间的教训）。

**质检与花钱边界（2026-10-05 用户定）**：
- 送 Wan 前自查（编译产物对原片核动作）= 0 分，不需要人同意，但结果要可见；
- 出片抽帧质检（对边界清单核脸/产品/字幕水印/动作节奏）= 0 分，**必须把帧贴给人看**，
  「上游回成功」不算交付；
- 任何**重跑**（Wan 段、去水印、高清修复）= 花钱，必须**先报价、等人明确同意**才执行，
  不自动扩范围。质检不合格 ⇒ 报证据+报价，等确认，不是直接重跑。

## 编译产物自查（送 Wan 前必做，2026-10-05 补）

管线 MiMo 解析会把"对镜手抓袜口下撸"这类动作压成静态循环标签（"反复拉扯"），
Grok 编译照此写进 prompt，Wan 就出机械拉扯——**解析侧信息损失，且默认无质检闸直接流到生成**。
每个段在调 `run_pipeline` 之后、等出片之前，对 `outputs/<段>/step1_5_*` 的编译产物做一次自查：

1. 读该段编译出的 prompt / storyboard，把"手部律动""产品动作""画面动作"字段摘出来。
2. 对着**该段原片**逐秒核：动作方向、次数、节奏（抓-撸-停）、停顿点是否一致。
3. 不符 ⇒ 在 extra-note 里按秒写死动作时间轴（如"0-2s 右手抓左腿袜口向下撸一次后松开；
   2.5-4.5s 双手抓右腿袜口向下撸一次；每次方向一致、有停顿、不得来回拉扯"），压过解析标签再重编译。
4. 符合 ⇒ 放行。

这是"编译前质检"：把动作对不对的判断从"出片后抽帧"前移到"送 Wan 前"，省一次 50 分重跑。
抽帧那步保留，作为出片后的第二道闸（核 Wan 有没有执行到位）。

## 意图匹配闸（编译后、送 Wan 前，2026-10-05 用户定）

GPT 编译（Step 1.5）+ Lint（Step 2）查的是**形式**（图N 越界、时间被改、物理文本错误），
**不查"编译出的画面和客户意图匹配不匹配"**。这是送 Wan 前的最后一道语义闸，agent 执行，0 分：

1. 读该段编译出的最终 prompt（`outputs/<段>/step2_wan3/_wan3_request.json` 的 prompt 字段）。
2. 对着 BRIEF 边界清单逐项核语义：
   - **换产品**：产品名/产品图元素在不在、该镜原商品可见性对不对（不可见镜有没有被注入产品）；
   - **换脸**：脸绑定段的五官描述是否严格按图N、无脸段有没有被注入脸；
   - **换背景**：客户意图的背景定调（含客户参考图识别成的文字）是否写进每个相关镜、
     有没有被 GPT 压缩掉关键元素、有没有和某镜原场景描述冲突；
   - **保留项**：袜/鞋/口播/运镜是否原样；
   - **负向**：字幕/花字/红箭头/水印/品牌装饰的清除指令在不在。
3. 不符 ⇒ 在 extra-note 里补对应条目**重编译**（重编译 0 分，不烧 Wan），再回 1 直到匹配。
4. 符合 ⇒ 放行送 Wan。

这道闸核的是**语义**（意图有没有进 prompt），编译产物自查核的是**动作**（解析标签有没有失真），
出片抽帧核的是**执行**（Wan 有没有照 prompt 做）——三道不重复，各管一段。

## 段间接口自检（多段拼成前必做，2026-10-05 补）

段内自检（1.6/1.7/3.5）管的是"每段自己对不对"；**段间接口**是另一个问题域——
两段拼接时，接口处没有专门自检项，不合格就闪切/跳场景/断句。多段单拼成前必核三件事：

| 接口项 | 核什么 | 方法 |
|---|---|---|
| 背景连续 | 段A 结尾帧 与 段B 开头帧，背景家具/墙面/地板/光线是否同套（不能段A 米白沙发、段B 突然换风格） | 抽段A 末帧 + 段B 首帧并排贴出，人眼核 |
| 口播一致 | 两段口播拼起来与原片口播一致：不重说、不漏、不截半句（口播只要求与原片逐字对齐，不要求段间动作/口播"衔接"） | 读两段 storyboard 口播字段，拼接后与原片口播逐字核 |
| 时间戳对齐 | 段A 结束时刻 = 段B 开始时刻，无重叠无间隙（原声贴回时主时钟对齐） | merge 脚本保证 + ffprobe 核总时长 |

**动作不做段间衔接校验**：动作只要求"复刻原视频"（1.6 逐秒核原片），段间接口处不额外要求"接得上"——
原片本身在这个位置是硬切（俯拍→坐姿），段间接口保留原片的硬切即可。

**接口不合格 ⇒ 回切段表改切点**（让接口压在原片硬切上），不是重跑 Wan 段。
切点选错（压在镜头中间）是接口闪切的根因，先改切点再跑。

## 对用户说话

用中文。先讲切段表和还缺哪张图，再动手。付费 / 写 TIMELINE / 启动 Wan 之前说明意图。长任务说明正在生成哪一段。

## 调用现有管线（P2）

工作区可以用 shell。Python 管线：

`backend/services\super_clone_wan3.py` 的 `run_pipeline`

**推荐走 skill 自带 runner（薄封装，参数见 `--help`）**：`runner/run.py`
`python runner/run.py --task-id <id> --video <该段原片> --product-images <图...> --product-name <名> --output-dir <目录> [--model-image <脸>] --as-duration-chunk`
包元数据见 `manifest.json`（能力/依赖/积分）。

每段传入：该段裁剪后的原片、该段产品图、该段唯一脸图、产品名、背景文字、背景图（如给了 `--scene-image`）、`as_duration_chunk=True`。不要 `split_by_shots`。

P1 只写文件，不调用 Wan。

## 流程合同（机器判据 · 不是建议）

这一节是**给代码读的**：入口跑完写回执，花钱那一刀之前核验回执，交付时把缺步印出来。
判据唯一源 = `backend/skill_contract.py`，设计见 `docs/ADR-skill-contract.md`。
🔴 绕开 `entry` 自己手写 prompt 再提交 ⇒ 没有回执 ⇒ 提交口直接拒（不是"提醒你别这样"）。

🔴 **两条提交路径共用同一个物证写者** `_contract_evidence`（`super_clone_wan3.py`）：按分镜切的多段、
以及一条 ≤15s 的单段，都必须落齐 `prompt.txt` / `storyboard.json` / `prompt_meta.json` / `.skill_receipt.json`
再核验。原来这段代码只写在多段循环里 ⇒ **单段复刻既不写提示词也不写回执**，客户问"回执呢、提示词呢"
时答案是"那个运行形状物理上就没有"（2026-10-10 实测）。闸 T34f 钉的就是"两处各调一次"，少一处就红。
`prompt_meta.json` 现在另带 `model` / `provider` / `ledger` 三键（编译器自己写下它用的那一个模型），
执行表因此多出「模型/路由」一列；`ledger=off` 是**如实**标——这条编译腿直连厂商、不在网关账本上。

🔴 **回执的写法 = 打钩单**（2026-10-12 用户定："这个回执还是要，回执的写法是每一次进行打钩，有模型名"）：
开工先 `open_receipt()` 落一张**全 ⬜** 的表（合同里每一步都先在表上占一行，含输入指纹），
之后**每落一件物证当场 `tick()` 一个钩**——不是跑到提交前一次性补一份总结。
每一格**必须带模型名**，且只许现读：网关腿读 `ROUTES`（`_lane_cell`）、Step1 分镜腿读真函数签名的默认值
（`_mimo_cell`）、编译腿用 `_llm_stamp`，确实不经模型的本地步写 `不用模型`。
🔴 **没给模型名 ⇒ 那一格打不上钩**（`tick` 直接拒），必需步没打钩 ⇒ `verify_or_raise` 在花钱那一刀之前拒、0 分。
所以中途崩了或被打断，盘上留下的就是"跑到第几格、哪一格卡住"，而不是一片空白或上一次那份旧的。

contract:
  # @12 = 加了 narration-file（客户自己给念好的音频 => 整条替换，0 分本地，见 run_narration_audio）。
  # @11 = 加了 narration-direct（改口播【直出】：客户改稿在编译前灌进各镜口播位，wan3 一次出片就念新词）。
  # @10 = 加了 lip-sync（口型对齐·后处理腿）。@9 那批在途回执要在提交前重跑一次 --stage compile（0 分）才继续，不是拿旧版本号蒙。
  # @9 = 加了 narration-swap（改口播·音色不变的后处理腿）。
  # 🔴 值行不写行尾注释（规矩：解析会剥，但别依赖它）—— 这句说明单独占一行。
  version: super-clone-wan3@12
  entry: python .dsh/skills/super-clone/runner/run.py --from-form   # 值全部从这条会话【已提交】的右栏表单读，参数只作覆盖；老写法逐格给照样能跑
  # 花钱口三方唯一源：表单 engine_lock(yz30) == 本行 == 网关 ROUTES/价目；clone-r2v 是已断开的 RunningHub 腿的批准词，不再是这条线的花钱口
  spend: video.generate/wan3-prime
  steps:
    - id=material-analysis | 手段=pipeline:invoke:video.analyze | 证明=material_analysis.json（该段每个素材的内容 sha8 + 缺口清单 + executor/auto_spent：这条腿由【管线】经 gateway.invoke 跑，台账与实扣由网关记，不是 agent 自述、也不是 agent 手搓脚本） | 判据=json-list-min:materials=1 | required=1
    - id=storyboard | 证明=该段目录里有 storyboard.json（Step1 MiMo 解析 + Checkpoint A 规范化后【喂给编译器的那一份】） | 判据=json-array-min=1 | required=1
    # 🔴 storyboard-fact-check（2026-10-11 补）：MiMo 的分镜表是【上游解析结果】不是事实（现网 5.1s 单镜头
    #    被解析成 2 镜 + 假"硬切/换装"）。SKILL.md 一直要求"标签不符就按秒写死时间轴压过它"，但这一步
    #    以前【既没物证也没授权手段】⇒ 改一次就要自己论证一次合法性，论证不出来就绕开入口手搓 driver。
    #    现在手段只认 runner 的 --extra-note，物证由管线落盘（镜数/时长/分镜指纹 + 改动的全文与 sha8）。
    #    ⚠️ 强度边界：它证明"对账发生过、改的是哪一版"，不证明"改得对"——对不对由人在审帧那一刀看。
    - id=storyboard-fact-check | 手段=runner:--extra-note | 证明=storyboard_check.json（喂进编译的镜数/时长 + storyboard_sha8 + extra_note 的 chars/sha8；有改动时全文另落 extra_note.txt） | 判据=json-nonempty | required=1
    - id=compile-prompt | 证明=prompt.txt 由 Step1.5 编译器写出且非空 | 判据=json-nonempty | required=1
    - id=compile-bound | 证明=prompt_meta.json（编译器写下：来源 + 它实际吃进去的 storyboard 指纹） | 判据=bind-sha8:storyboard:storyboard_sha8,json-key:source=step1_5_gpt_compiler | required=1
    - id=negative-lock | 手段=compiler-negative-lock | 证明=prompt.txt 里真的含后端负向锁定段（字幕/花字/水印不生成）——手段在 super_clone_wan3.py:275（后端常量）与 :669（编译器禁令），不是像素级清洗、更不是手搓 ffmpeg | 判据=text-has=不得出现字幕;水印 | required=1
    - id=source-fit | 证明=该段目录里有裁剪/贴合后的原片(trimmed_source.mp4 或 source.mp4) | required=0
    - id=submit-package | 证明=提交参数包 _wan3_request.json 落盘（提交之后才有，交付时核） | required=0
    #   narration-direct（@11）= 改口播【直出】：客户改稿在 Step1 的 FunASR 写回【之后】灌进各镜「口播」位，
    #   Step 1.5 编译器把它当台词写进 prompt，末尾 force_verbatim_speech 逐字钉回去 ⇒ wan3 一次出片就念新词。
    #   🔴 这根线以前是断的：apply_voiceover_script 全仓零调用点 ⇒ 进 prompt 的永远是原片旧词（那才是"只能后处理"的由来）。
    #   硬判据：新稿落位覆盖率 + 台词位残留（两个方向都 ≥0.999），不满即 0 分拒提交；
    #   ⚠️ 它只证"新词进了台词位"，【不证】念得准、也不证嘴形对 ⇒ 口型仍归 lip-sync 那一格与审帧那一刀。
    #   ⚠️ 一段一条稿：整条自动切多段时带着整稿会被点名拒（每段各灌一遍=每段都念整稿），逐段跑或走 narration-swap。
    - id=narration-direct | 手段=pipeline:apply_customer_narration | 证明=voice_script_binding.json（逐镜 原口播/新口播 + coverage/stale + 新稿全文 + problems + executor） | required=0
    #   narration-file（@12）= 客户【自己给一条念好的音频】⇒ 整条替换口播：本地 atempo 适配片长 +
    #   抹掉原人声 + 垫回 demucs 伴奏 ⇒ 0 分、不调上游、不重跑画面。
    #   🔴 为什么必须登记成合法手段：不登记就没有出口，只能被手搓 ffmpeg（合同外的手段）——那正是禁令要堵的形状。
    #   ⚠️ 与 narration-swap 互斥（那一条逐句克隆、音色跟原主播）；这条的音色 = 给的那个人。
    - id=narration-file | 手段=backend:invoke:voiceover_replace.run_narration_audio | 证明=narration_audio.json（成片/音频两条时长 + fitted 时长 + 增益与是否夹住 + vocals/new 响度 + spend=0 + 输出路径） | required=0
    #   narration-swap 只在 voice_mode=rewrite_text（改口播·音色不变）这一档产生 ⇒ required=0。
    #   🔴 这条腿每改一句花一次 RunningHub 克隆，且【不在网关账本上】⇒ 物证里的 usage 是"实扣多少"的唯一出处。
    - id=narration-swap | 手段=backend:invoke:voiceover_replace.run_narration_swap | 证明=narration_swap.json（逐句动作单 + 每句 task_id/usage + 时长比 tempo + 响度补偿与 clamp + 是否 dry_run） | required=0
    #   lip-sync（@10）= 改完口播之后把嘴形对齐到新词。🔴 它必须是【一条独立的腿】：
    #   wan3-prime 的入参名单（gateway._PRIME_YZ_MEDIA）只有 reference_image/video/audio/file，
    #   payload 里零个 lip/mouth/sync 字段 ⇒ "让 wan3 顺便对口型"这条物理上不存在；
    #   而上一步 run_narration_swap 自己的文档串就写着"不做口型同步"。
    #   对齐的目标音频 = 换轨成片【自己的音轨】（runner 本地 ffmpeg 抽，0 分、不发请求），
    #   所以这一条不许再引一次 TTS/克隆，也不许拿原片原声当目标。
    #   ⚠️ 20 分/次是价目登记值，账本里 lipsync 的调用数=0 ⇒ 未实测（耗时/成功率无数据，报价要带这两个字）。
    #   ⚠️ 走网关 ⇒ 默认要批：没有 lipsync/default 的条子 ⇒ 0 分未扣、原样念拒因，绝不"顺手提交"。
    - id=lip-sync | 手段=gateway:invoke:lipsync | 证明=lip_sync.json（输入视频/目标音频/说话段数 + capability + 现读单价 + estimated/charged + mode + 输出路径 + error） | required=0
    - id=reference-clean | 证明=需要无产品帧的那段有 product_absent.jpg | required=0
    #   ⚠️ 名字容易读错：这一步【不是"清理参考素材"】，跟字幕/水印无关。
    #      它登记的物证是"这一段确实不出现目标产品时，引擎要的占位帧"（首帧抽取），
    #      因为入口要求 product_image_paths ≥1；字幕/水印走 negative-lock 那条，绝不动像素。

contract-human:
  # 人工稿那条流程的合同（2026-10-24 用户定：提示词可以自己在界面上写/改，再点「生成视频」）。
  # 🔴 为什么是【第二份形状】，而不是把上面那份的 storyboard / compile-bound 改成 required=0：
  #    那两格的判据（json-array-min、bind-sha8 + json-key:source=step1_5_gpt_compiler）人工稿
  #    物理上永远满足不了 ⇒ 要么 0 分拒提交，要么去伪造 prompt_meta 的指纹（= 台账说谎）。
  #    而把格子降级会连【编译档】的判据一起拆掉。所以不动 @12，另立一份只管"人工稿确实发生了什么"。
  # 这一份仍然要看过图、仍然要负向锁定在正文里；它只是不假装有个编译器。
  version: super-clone-wan3-human@1
  entry: 界面「我自己写 / 用我改的这版」⇒ PUT /api/super-clone-wan3/prompt/{task_id} ⇒ 点「生成视频」
  spend: video.generate/wan3-prime
  steps:
    - id=material-analysis | 手段=pipeline:invoke:video.analyze | 证明=material_analysis.json（每个素材的内容 sha8 + 缺口清单 + executor/auto_spent：这条腿由【管线】经 gateway.invoke 跑，台账与实扣由网关记，不是 agent 自述、也不是手搓脚本） | 判据=json-list-min:materials=1 | required=1
    # human-prompt = 人在界面上敲的那一版。两件套物证：prompt.txt（正文本体）+ _human_prompt.json
    #   （prompt_sha8 / chars / saved_at / manifest_ids）。bind-sha8 钉的是"标记里那份指纹
    #   就是现在要发出去的正文"——存完被人再动过一个字 ⇒ 绑不上，0 分拒提交。
    - id=human-prompt | 手段=human:ui | 证明=_human_prompt.json（prompt_sha8 + chars + saved_at + manifest_ids） | 判据=json-nonempty,bind-sha8:negative-lock:prompt_sha8 | required=1
    # negative-lock 与默认合同【同一句硬判据】：人工稿也必须带后端那份负向锁定（不许字幕/花字/水印）。
    #   手段在这里申报成 human:ui（字是人打的），但内容不许靠自觉：text-has 这条判据照跑，
    #   而真正逐字相等的那一刀在代码里（super_clone_wan3.raise_prompt_preflight，提交前、0 分）。
    - id=negative-lock | 手段=human:ui | 证明=prompt.txt（最后一行逐字 = super_clone_wan3.STEP1_5_GLOBAL_CONSTRAINT） | 判据=text-has=不得出现字幕;水印 | required=1
    - id=source-fit | 证明=该段目录里有裁剪/贴合后的原片(trimmed_source.mp4 或 source.mp4) | required=0
    - id=submit-package | 证明=提交参数包 _wan3_request.json 落盘（提交之后才有，交付时核） | required=0
