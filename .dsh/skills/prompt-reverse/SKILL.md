---
name: prompt-reverse
description: >
  看一条爆款视频，反推出可直接复用的 H3 视频 Prompt，并通用化成 {{PRODUCT}}/{{SCENE}} 模板。
  产物是提示词文本（.txt/.json）+ 场景描述 + 口播原文，**不出片**。
whenToUse: >
  用户给一条视频（本地文件 / 抖音 / 小红书链接，可批量）并要"把这条视频的提示词反推出来 /
  拆成可复用模板 / 学它的镜头结构 / 换成我的产品重做一版提示词"。典型说法：
  "这条爆款视频的 prompt 是什么"、"把这个抖音视频反推成 H3 提示词"、"这条改成通用模板我以后换产品用"、
  "这几条链接批量反推"、"用我的商品图融合一版提示词"。
whenNot: >
  不做成片（要出 mp4 走 /omni、/hypit-pipeline、/super-clone）；不做原片换脸换背景的整片复刻（/super-clone）；
  不做去水印/高清修复（/video-dewatermark、/video-enhance）；不做字幕提取（/video-subtitle-reader，只要字幕走那条）；
  不写新剧本、不做剧情带货（/short-drama）。本 skill 到"提示词交人手可审"为止。
inputs: 一条或多条视频（本地路径 / 抖音 / 小红书链接）；可选裁剪区间、补充提示 extra_hint、引擎选择（mimo/grok/both）、融合腿的商品图
examples: |
  正例：反推这条抖音视频的提示词 → upload/prepare-link → submit → to-generic → 落盘交付
  正例：这 6 个链接批量反推成通用模板 → batch/start → pollBatch → 逐条 txt
  反例：把这条爆款换成我的产品拍一版 → 那是出片，交 /super-clone 或 /omni（本 skill 只交回融合 Prompt）
# 输入表单（2026-10-09，docs/PRD-input-form-single-port.md）：登记成 prompt-reverse 档后，
# 右栏挂「填参数」页签（页面由 8100 托管 /ui/prompt-reverse），客户填完点提交，agent 用
# `python scripts/intent.py payload` 读回。三条自律：
#   ① 字段表唯一源就是下面这段，渲染器/插件里不许出现第二份；第 6 列必须是 handler 真读的 param 名；
#   ② 表单不出现积分/价格数字；点头花钱仍只有 127.0.0.1:8100/approval 那一次点击；
#   ③ 🔴 裁剪区间 trim_start/trim_end 【不进表】：app.py:9140 只在两者【同时】给定时才裁，
#      只填一个会被静默忽略 = 表上放一个"改了也不生效的控件"，那种控件一律不许有（要裁剪请在对话里说）。
intent: prompt-reverse
form: |
  vid|video|要反推的爆款视频（本地文件，≤15MB）|yes|1|video_path|15MB
  extra_hint|textarea|额外提示（想重点拆镜头/口播/节奏哪一块）|no| |extra_hint
  engine|select|反推用哪个脑|no|mimo 默认(便宜);grok 备选;both 两个都跑对比|engine
version: 0.1.0
---

# 提示词反推（prompt-reverse）

把"别人家的爆款"还原成我们能改、能复用、能换产品的**提示词资产**。引擎是 8090 那个工作台，
本文件只写命令契约和关口；跨会话读这张纸就能接手，不靠记忆。

## 0. 先决条件（不满足就别往下走）

- **8090 在跑**：`app.py` 由 uvicorn 起（`python -m uvicorn app:app --host 0.0.0.0 --port 8090`，源码 `backend/app.py`）。
  探测：`curl -s -o NUL -w "%{http_code}" http://127.0.0.1:8090/prompt-reverse` 要 200。没起就说"8090 工作台没起"并给出上面这条启动命令，**不要自己去 kill 或重启常驻服务**。
- 🔴 **本 skill 的模型腿目前在网关账本外**（反推走 `prompt_reverser.py:570` 的 MiMo、`:694` 的 Grok，通用化/融合走 `gpt_client`）。
  在被接进网关之前，**只允许跑文本/看图腿**（视频理解 + 改写），**禁止调用任何按条计费的生成腿**
  （`/api/h3-studio/generate`、`fuse-product` 之后的出片、`publish-rpa`）。要出片请交回 `/omni`、`/hypit-pipeline`、`/super-clone`（那几条已接网关、会报价会扣分）。
- ✅ 2026-10-06 起这条禁令**有闸兜底**（不再只靠自觉）：`/api/h3-studio/generate` 入口调 `gateway.check_spend_approval("video.generate","h3",…)`，
  **没有人在终端签的批准条子就回 423、任务不发起**（实测 0 分未扣）。所以：① 别说"我禁了但能绕"；② 也别替用户签条子——
  要出片就走"报价 → 他回话 → 人在终端 `gateway.py approve`"这条路，或交回已接网关的那几条 skill。
- 因此**没有积分可报**：不要凭记忆编"这条扣几分"。如实说"这三条腿看的是 MiMo/GPT 的 key 用量，不在积分账本上"。

## 1. 第一步：登记意图（带引擎，不登记右栏不挂台）

```
python scripts/intent.py prompt-reverse <任务名>
```

会话 id 由 DSH 注入（PowerShell 写 `$env:DSH_SESSION_ID`，不是 bash 的 `$DSH_SESSION_ID`）；拿不到就加 `--session <会话id>`。
跑完会把判定口答复打出来（`intent/up/url`），当场就知道界面会不会出。任务名用**这条视频的别名**（如 `fu-tie-爆款01`），台里和 `productions/<任务名>/` 都靠它对上。

## 2. 端点契约（实测 file:line，别改写法）

基址 `http://127.0.0.1:8090`。所有路径都在 `backend/app.py`。

| 步骤 | 端点 | 请求 | 关键返回 |
|---|---|---|---|
| 传视频 | `POST /api/upload/video` (`:932`) | multipart，字段名 **`file`** | `{success, file_path}`；>15MB 自动压（crf30，仍超降到 720p） |
| 链接预处理 | `POST /api/prompt-reverse/prepare-link` (`:9418`) | `{text}`（整段分享文案都行） | 本地 `video_path` + 预览 URL + 时长。**不启动反推** |
| 单条反推 | `POST /api/prompt-reverse/submit` (`:9005`) | `{video_path, extra_hint?, engine?("mimo"默认/"grok"/"both"), output_subdir?, trim_start?, trim_end?}` | `{success, task_id, output_dir}` |
| 查状态 | `GET /api/prompt-reverse/status/<task_id>` (`:9065`) | — | `{status: INIT/RUNNING/SUCCESS/FAILED, error, result}` |
| 通用化 | `POST /api/prompt-reverse/to-generic` (`:9355`) | `{prompt}` | `{success, generic_prompt}`（已剥 markdown 围栏） |
| 融合商品 | `POST /api/prompt-reverse/fuse-product` (`:9091`) | `{mimo_prompt, reference_images[], product_name?, extra_hint?, target_duration_sec?, source_duration?}` | 融合版 Prompt 文本（**只是文本**；后面那个"生成案例视频"是生成腿，本 skill 不碰） |
| 批量 | `POST /api/batch-prompt-reverse/start` (`:9611`) | `{links:"一行一条", video_paths:[], trim_ranges:[{start,end}|null]}` | `{success, task_id, total}` |
| 批量状态 | `GET /api/batch-prompt-reverse/status/<task_id>` (`:9663`) | — | `{items:[{index,link,status,general_prompt,reverse_result,video_url,error,prompt_url}]}` |
| 批量重试 | `POST /api/batch-prompt-reverse/retry/<task_id>/<item_index>` (`:9733`) | 无 body | 只补跑那一条 |
| 历史 | `GET /api/batch-prompt-reverse/list` / `/history/<batch_id>` (`:9688`/`:9697`) | — | 服务重启后从历史恢复，别当"任务丢了"重跑 |

item 的 `status` 走 `pending → downloading → reversing → generalizing → saving → done/failed`。

## 3. 流程

**单条**：拿到本地路径（本地文件先 `/api/upload/video`；链接先 `prepare-link`）→ `submit` →
每 10 秒轮 `status` 到终态 → 拿 `result.h3_prompt` → `to-generic` 出通用版 → 落盘 + 贴图。

🔴 **HTTP 超时要给够：submit/to-generic 都按 ≥240 秒设**（2026-10-06 首次实测定标：
一条 6.6 秒素材，`submit` 到 SUCCESS 约 2 分钟【含 FunASR 口播识别 + demucs 人声分离】，`to-generic` 20 秒；
我第一版脚本按 60 秒设，把已经成功的 `to-generic` 掐断成 `TimeoutError`，**看起来像上游失败、其实是我方客户端太短**）。
`status` 轮询别用短超时：任务还在跑时那次请求本身就该等得到。

**批量**：粘链接 → `batch/start` → 轮 `status` 到全终态 → 每条把 `general_prompt` 落盘，失败的那几条**只补跑那几条**（`retry`）。

**融合商品（要换产品时用）**：`fuse-product` 传原 `mimo_prompt` + 商品图（先 `/api/upload/image`，字段名也是 `file`）→ 融合 Prompt 交用户**审核**（这一屏是关口，客户要挑"别继承原视频我家产品没有的部件"）。**到此为止**，出片不在本 skill（出片腿 `h3-studio/generate` 自己限 `reference_images` 0-3 张，那条本 skill 不调）。

### 3.1 首次实测（2026-10-06，素材 = `productions/2026-10-02-富铁软糖剧情带货短剧/05_shots/videos/h3-director/SEG-1-01-h3-dir.mp4`，856KB / 6.58s）

跑通链路：`submit(mimo)` → `SUCCESS`(~2min) → `h3_prompt` 1096 字 → `to-generic` 1094 字(20s) → 6 份产物落盘 + 抽帧。
**交付前必须知道的三件事**（都实测过，不是猜的）：

1. **`h3_prompt` 开头带内部字段名 `integrated_multimodal_description:`**，`to-generic` 也原样保留 ⇒
   交给人之前**要把它剥掉**（`p.split(':', 1)[1].strip()` 只剥第一处），否则客户拿到的是一段带机器标签的文本。
2. **通用化用的是裸中文替身词，不是 `{{PRODUCT}}/{{SCENE}}` 大括号占位符**（2026-10-06 实测纠正：
   我原先照抄了 `app.py:9355` 那段 system prompt 里的示例写法，没实测就写进文档，是错的）。
   真实形态：`身穿藏蓝色及膝大衣式连衣裙` → `身穿服装`、`正红色硬挺梯形手提包` → `产品`、`黑色高跟单鞋` → `鞋履`。
   ⇒ 交付时要说清"这是**去品牌化的可替换位**，不是模板变量"；客户若要 `{{PRODUCT}}` 那种可直接替换的变量格式，
   **得 agent 自己按替身词二次改写**，别承诺引擎会出。
   附带两个实测好习惯：通用版会**丢掉平台水印行**（`画面左上角始终显示水印文字<t>RunningHub AI</t>` 在通用版里没了 ⇒ 模板不该带别人水印，这是对的）；
   `<d>...</d>` 口播**逐字保留**（11.8s 素材里 h3 与通用版各 7 段，一字不差）。
3. **商品图角度建议现在拿不到真值**：结果里 `product_image_angles.summary` 实测写
   `解析失败：Grok HTTP 401，默认建议上传 1 张主图`（`SR_GROK_*` 那把 key 已停用）⇒
   融合腿要商品图时**直接问客户要**，别把这条降级默认值当成"分析结论"报给他。

## 4. 关口（只有这两个要停，别到处问）

1. **融合 Prompt / 通用化 Prompt 交审**：把正文贴回对话让用户过目（含 `{{PRODUCT}}` 占位是否合理、口播 `<d>...</d>` 是否逐字保留）。
2. **用户接着要出片**：不在本 skill 范围内 ⇒ 报价并引导点 `/omni`、`/super-clone`、`/hypit-pipeline`，**不擅自调生成腿**。

## 5. 交付（产物没落盘 = 没交付）

```
productions/<任务名>/prompt-reverse/
  reverse_result.json     # 引擎原始结果（scene_description / h3_prompt / duration / engine）
  h3-prompt.txt           # 反推版
  generic-prompt.txt      # 通用模板
  fused-prompt.txt        # 融合版（跑了才有）
  frames/                 # 抽帧图（grok/both 引擎会有）
```

- `submit` 传的 `output_subdir` 用 `pr_<任务名>`，产出的 `result.result_path` 就在那儿，**照抄路径**别猜。
- 交付消息里给：落盘绝对路径 + 提示词正文 + 一张抽帧图（证明看的是这条视频）。
- 🔴 不许把"已提交 / RUNNING / 上游回了 200"当结论收工；同一原因连挂 3 次才停，停也要带 `task_id` 与 `error` 原文。

## 6. 失败分类（照死因处理，不是一律重试）

| 死因 | 表现 | 动作 |
|---|---|---|
| 视频过大 | `视频过大（xxMB），MiMo-V2.5 限制 15MB` | 传 `trim_start/trim_end` 截一段，或压到 14MB 内再传 |
| 链接解析失败 | `prepare-link` 回非 200 / item `status=failed` 且 `error` 带下载字样 | 让用户直接给视频文件，别死磕链接 |
| 空 content | 日志"第 N 次返回空 content，重试" | 引擎内部已退避；连挂 3 次改 `engine=grok`（抽帧路线）再试 |
| 抽帧失败 | `抽帧失败，请检查 ffmpeg` | 环境缺 ffmpeg，停下报证据，不换参数硬试 |
| key 欠费/401 | `error` 带 401/403/Arrearage | 停下报上游原文。**不许**换成另一把 key 或直连厂商绕开 |
| 客户端超时太短 | `TimeoutError`，但 `status` 里任务已 `SUCCESS` | **不是上游失败**：先把超时提到 240s 再判（2026-10-06 我自己踩过，把成功的 `to-generic` 报成了失败）；已成功的结果直接读 `result_path` 复用，**别重跑**（重跑=重复消耗） |
| 服务重启 | `status` 回 404 任务不存在 | 先查 `/api/batch-prompt-reverse/list` 历史，能恢复就别重跑（重跑=重复消耗） |
