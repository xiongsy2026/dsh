---
name: motion-transfer
description: >
  动作迁移：拿一条已经跑得好的模仿视频（动作/节奏/机位）+ 一张模特图 + 商品链接，
  跑出"同一个动作、换成你的模特和商品"的带货成片。引擎 = 本机 8091 五步工作室，
  第五步由它开浏览器去 RunningHub 的工作流里跑（账号池 + 串行锁）。
whenToUse: >
  用户点了 /motion-transfer，或明确说"用这条视频的动作换个模特/换个商品出片""动作迁移""把爆款视频的动作套到我的商品上"。
  例句：「用这条爆款视频的动作，模特换成这张图，商品是这条链接」「跑一条动作迁移」「上周那条动作迁移再跑一遍」。
whenNot: >
  - 只要提示词不要片子 ⇒ /prompt-reverse（它只反推提示词，明确不出片）。
  - 纯文生视频/一句话出片 ⇒ /one-shot-video；电商分镜方案出片 ⇒ /omni；video-as-code 工程化 ⇒ /hypit-pipeline。
  - 换脸/修图/去水印/高清修复 ⇒ /video-dewatermark、/video-enhance（那是 video.edit 系，不是这条腿）。
  - 要**发布到抖音** ⇒ 本 skill 只出片不发布；8091 有发布端点，但那是另一件事，需用户单独发起。
inputs: |
  product 模式（商品出片，三个都要，缺一不可）：
    1) 抖音商品链接 product_url
    2) 模仿视频链接 video_url（动作来源）
    3) 模特图 model_image_path —— 🔴 代码里是【必填】（`womenswear/router.py:_start_task`），
       nzdh 的 README/PRD 写"模特图可选"是错的，别照它劝用户。
  creator 模式（素材出片）：raw_product_path（模特图或商品图）+ source_video_path（模仿视频）
  可选：scene_image_path（背景来源；不给就抽模仿视频关键帧当背景）、engine（plus/lite/standard，默认 plus）
examples: |
  正例：「这条抖音爆款视频的动作，模特换成 D:\imgs\model.jpg，商品链接 https://…，出一条 15 秒的」
  正例：「上次那条动作迁移跑到 Step5 挂了，只补跑第五步」→ job_id/task_id 只捞不重投
  反例：「帮我想个带货钩子」（纯文案，不进引擎、不挂台）
  反例：「把这条视频的水印去掉」（video.edit/dewatermark）
version: 0.1.0
---

# 动作迁移（motion-transfer）

> 🔴 **这是一条按条计费的生成腿。** 发起前必须在对话里报价并等到用户明确同意（AGENTS.md 铁律）。
> 闸在**网关这一端**：agent 只许 `gateway call video.generate/motion-rpa`，没有覆盖本次请求的"人批准条子"
> ⇒ 网关拒绝，0 分不扣、任务不建；agent 不许为了跑通自己签条子（那是替用户点头）。
> ⚠️ **8091 工作台自己的三个口（`/run`、`/step5-rpa`、`/retry`）2026-10-10 起不再问条子** —— 那一下是人坐在
> 工作台前自己点的。**这条豁免不给 agent 用**：不许改成"直接 POST 8091"来绕开报价与批准（判据 = 
> `scripts/check-motion-rpa-lane.py` F 组，两半同时钉：8091 不问 + 网关仍要批）。

## 0. 第一步永远是登记意图（带引擎的 skill）

```
python scripts/intent.py motion-transfer <任务名>
```

- 不登记 ⇒ 右栏【零 UI】（新会话不猜意图那条铁律）。登记后弹第 7 档「动作迁移」台。
- 台里两格：**左 = RunningHub 工作流的实时画面**（8091 自己截的图），**右 = 8091 工作室页**，
  历史按 `?session=` 过滤 —— 谁点的谁看自己的，别人几个月前的单子不混进来。
  - 🔴 左边嵌的**不是** runninghub.cn 原页：那张页回 `X-Frame-Options: SAMEORIGIN`，在 DSH 里永远是空白；
    而"让它能嵌"= 开一个转发 RH 登录态的反向代理 = 把账号凭据经本机端口递出去，**不做**。
    所以这一格只有像素（截图），要交互就在台里点"在新窗口打开"。

## 1. 引擎与端口（先确认活着）

| 项 | 事实 |
|---|---|
| 服务 | `D:\nzdh` → `http://127.0.0.1:8091/`（uvicorn，`app.py:129` 用 `settings.HOST`） |
| 绑定 | **只听 127.0.0.1**（2026-10-06 收回；`config.py` 默认值与 `.env` 里那行都改了）。它能起 RPA 花真钱、能发抖音、管着一批 RH 账号，局域网点得动是不行的 |
| 生命周期 | 🔴 **手起，无看门狗、无计划任务**。起法：在 `D:\nzdh` 下 `pythonw app.py`（日志 `_svc_8091.out.log` / `_svc_8091.err.log`） |
| 判定 | 活没活由 8100 的 presence 真探（`GET /api/womenswear-studio/rpa-live/meta`，只读）。台里说"8091 工作室没起"时就是没起 |
| 会话主人 | 任务的 `extra.session`；由登记意图/请求体带进来 |

## 2. 五步在干什么（阶段名取自 pipeline 真实文案，不是 README）

| 步 | 干什么 | 花钱吗 |
|---|---|---|
| Step1 | 解析商品链接 / 下载商品原图（creator 模式直接用上传的模特图，跳过链接解析） | 抓站，不计费 |
| Step2 | Agnes 生**白底平铺图**（已有则跳过） | 生图腿（`image.generate`），要批 |
| Step3 | LLM 识别模特图 → 抽模仿视频关键帧当背景 → Agnes 融合出 **`showcase.png`（动作迁移参考图）** | 看图+生图，要批 |
| Step4 | 下载模仿视频 + 元信息（标题/作者/质量标签） | 抓站，不计费 |
| Step5 | 🔴 **浏览器 RPA**：Playwright 登录 RH 账号池 → 打开工作流 `2106377827540103169` → showcase 传节点 299、视频传节点 275 → 点运行（plus/lite/standard）→ 等节点 285 出完整视频（最长 30 分钟）→ 下 `final.mp4` | **`video.generate/motion-rpa` = 60 分/段（未实测·占位）** |

- Step5 有**全局串行锁**：同一时刻全机只有一条 RPA 在跑，别的任务会显示"等待 RPA 串行锁"。
- 节点 600 `FailedTrigger` 会在出片后**故意**把 RH 侧任务标成 FAILED（绕过对方时长/质量校验），
  所以"上游 FAILED"**不等于失败** —— 以 8091 任务状态和 `final.mp4` 落盘为准（`runninghub_rpa_client.py:10-15`）。

## 3. 怎么调（唯一轨 = 统一网关，不直连厂商）

### 3.1 先报价（数字必须从命令里读，禁止凭记忆）

```
python backend/gateway.py quote video.generate motion-rpa --json '{"count":<段数>}'
python backend/gateway.py credits
```

报价格式（一条消息里给全，然后**等用户回话**，不许同一轮顺手调用生成）：

```
路由：RunningHub 工作流（浏览器 RPA·8091 工作室）  模型档：plus
单价：60 分/段（⚠️ 占位·未实测，上游按 GPU 秒扣钱）
段数：N 段      预计总扣分：N×60 分
当前余额：<credits 里的数>
跑不跑？
```

用户同意后，由**人**在终端签条子（agent 只把这条命令原样呈给用户，不代签）：

```
python backend/gateway.py approve video.generate motion-rpa \
  --count <N> --credits <N*60> --account local --session <本会话id> --scope "<用户批准的那句方案原文>"
```

### 3.2 跑整条五步（`/run` 会自己跑到 Step5，**不要再补 step5-rpa**＝双花）

```
python backend/gateway.py call video.generate motion-rpa --account local --json '{
  "source_mode":"product","product_url":"<抖音商品链接>","video_url":"<模仿视频链接>",
  "model_image_path":"D:\\...\\model.jpg","session":"<会话id>",
  "output_path":"C:\\dsh\\productions\\<任务>\\outputs\\<ascii名>.mp4",
  "poll_timeout":2400,"poll_interval":10}'
```

### 3.3 只补跑第五步 / 只查不重投

```
# 前四步已备好（task_id 在 8091 盘上有 showcase.png + source_video.mp4）
... call video.generate motion-rpa --json '{"task_id":"<8091任务id>","session":"<会话id>","output_path":"..."}'

# 超时/客户端断了/进程被换 ⇒ 用 job_id 捞回结果，绝不重新提交（重投=双花，还占串行锁）
... call video.generate motion-rpa --json '{"job_id":"<8091任务id>","output_path":"..."}'
```

`output_path` 给不给都行：不给就报 8091 盘上那条（`D:\nzdh\outputs\womenswear_studio\<task_id>\final.mp4`）；
给了就顺手拷到 `productions/<任务>/outputs/`，交付三件套按 §5。

### 3.4 台里手点也要批准（不是只给 agent 拦）

8091 的 `/api/womenswear-studio/run`、`/step5-rpa`、`/retry` 三个口在**建任务/改状态之前**都会问 8100 的
`POST /api/spend/guard`（只听环回、只**消耗**批准、没有创建批准的端点）。没条子 ⇒ 423 + 报价原文。
判定口没回话同样 423 —— 闸门坏了不能当成"闸没拦"。

> 为什么网关对这条 purpose 不重复扣段：花钱动作发生在 8091 那个进程里，条子在源头消费一次；
> 两处各扣＝逼用户批两遍，是自造的假账（`gateway.py` 的 `SPEND_APPROVAL_AT_SOURCE`，配对关系由闸钉着）。

## 4. 三个关口（批准粒度：只在这些地方停）

1. **方案关口**：五步计划 + 上面那份报价，用户点头才动。
2. **Step3 审参考图**：`showcase.png` 出图后**贴图交审**再看 Step5 —— 参考图决定了货对不对板，
   跑完 Step5 才发现模特/商品不对就是白烧一段。
3. **成片验收**：按 §5 交付三件套贴图 + 路径 + 直链；用户点头才算完成。
4. 🔴 **发布永不自动**：8091 有 `/api/publish/*`（含抖音登录态、发布会话），本 skill **不调**。
   要发布由用户单独发起，且那是另一件花钱/担责的事。

## 5. 交付（目标没达成不许停）

成片 = 三件套齐：**盘上路径** + **可点直链** + **抽帧贴图**（DSH 视频不能内联）。

```
C:\dsh\productions\<任务>\outputs\<ascii名>.mp4
http://127.0.0.1:8091/outputs/womenswear_studio/<task_id>/final.mp4      # 8091 直链（文件名保持 ASCII）
抽帧贴图：ffmpeg -i <mp4> -ss <t> -frames:v 1 <ascii名>-frame.jpg 后用 markdown 内联贴出
```

- "已提交 / RUNNING / 上游回了状态"都**不算交付**；自己盯到终态。
- 一批里失败几段 ⇒ 只补跑那几段（用 `job_id` 捞或 `task_id` 单跑 Step5），不整批放弃。
- 同一失败原因连挂 3 次才停，停也要把 `task_id`、8091 的 error 原文、上游原因如实交回。

## 6. 失败分类与处置（按死因，不按症状）

| 死因 | 认法 | 处置 |
|---|---|---|
| 没批准条子 | HTTP 423，文案含 `approval_required` | 停下来报价，等用户点头；**不许**自己 approve |
| 判定口没回话 | 423 且文案含"没回话" | 8100 没起：在 `backend` 下 `pythonw backend\model_admin.py`；起来再重跑 |
| 8091 没起 | presence `up:false`、连接被拒 | `cd D:\nzdh; pythonw app.py`（手起服务没有看门狗是已知缺口） |
| RH 登录态过期 | error 含 `SessionStaleError` / "登录态已过期" | 8091 会自愈重登（`reset_session_and_relogin`）；Cookie 类账号要人工在弹出的浏览器里登一次 |
| 肖像/内容审核拦 | 上游文案（如"参考图未通过肖像保护"） | 🔴 上游文案二次不可信，以 8091 的嵌套 error 原文为准；换素材/换模特图再跑，不硬换提示词死磕 |
| 串行锁排队 | stage 含"等待 RPA 串行锁" | 正常等待，别开第二条腿；台里显示的就是排队 |
| 查询抖动 | SSL/URLError、连接被拒 | 网关侧退避重试**同一个** task_id，连续 6 次才判死；判死只报 id，不重提交 |
| 客户端超时 | 我们这边 HTTP 超时但 8091 还在跑 | `job_id` 只捞不重投；`poll_timeout` 要 ≥1800（Step5 上游最长 30 分钟） |
| D 盘满 | `ENOSPC` | 清盘再补跑那几段（历史踩过，别当引擎坏） |
| 重启误判 | 进程 StartTime 晚于任务 created、状态停在 RUNNING | 属"被我们打断"，不计入失败率；用 `task_id` 续跑 |

## 7. 验证与回归（改这块必须先过）

```
python scripts/check-motion-rpa-lane.py        # 动作迁移腿（价目/真值表/双花/两端闸配对/右栏契约）
python scripts/check-spend-approval.py         # 花钱批准闸本体
python scripts/check-workbench-presence.py     # 判定口（含第 7 档）
python scripts/check-workbench-client.mjs      # 右栏 WORKBENCHES（加档必改闸名单）
python scripts/check-skill-dock.py             # 首页 dock
node --check plugins/ops-gateway/lib/client.js
```

改 8091 端点、改价、改批准闸任何一处，上面五套都得跑。闸只读不写线上账本（scratch 开局清扫，
jobs 库指到临时文件）。

## 8. 已知缺口（别对用户说成"已具备"）

- **60 分/段是占位、没实测**：上游 RunningHub 按 GPU 秒扣钱，"1 元=几分"未定；跑通第一条后要拿上游消耗校准并改 `model_prices.json`。
- Step2/Step3 的生图/看图腿**仍在 8091 内部直连厂商**（`image2_client` / Agnes），没进网关账本 ⇒ 那部分积分不体现在信封里。
- 8091 **手起、无看门狗**；`D:\nzdh` 不是 git 仓库（改错没版本可回，动前已做 `_bak-20261006/` 备份）。
- `D:\nzdh\config.py` 默认值里带着明文 API key，且同一把 key 在 `.env` 和 `dist/exe-build/payload.zip` 里都出现过 ⇒ **建议轮换**（2026-10-06 我在排查时把含 key 的片段打印进了对话）。
- 真人脸/肖像保护是随机拦（历史战绩：整批 12 成 / 5 败，其中 5 条是重启误判，真实 ≈71%），别承诺必出。
