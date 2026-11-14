---
name: video-enhance
description: >
  把一条本地视频升成高清版（4K / 1080p）。走 WeekSir 统一网关 video.edit/enhance
  目的，底层是 RunningHub AI App 1990029249488801793。输入本地 mp4，输出高清版本。
whenToUse: >
  用户给一条本地视频（多为 Wan3 / H3 / 实拍素材），要一版更清晰的高清（4K / 1080p）修复片。
  触发词：高清修复、视频升清、变清晰、upscale、把这条视频变清晰。
  例句："把这条 28s 复刻片升成 4K"、"这段 480p 太糊了，来个高清版"、"视频升清一下"。
whenNot: >
  去水印/清字幕/清台标走 /video-dewatermark；图片升清不走本 skill；
  换产品/换脸/换背景走 /super-clone；对口型/换声走 /lipsync。
inputs:
  - 本地视频文件（路径，公网 URL 会被本地拒，先落到 productions/<task>/inputs/）
  - 输出路径（本地落盘；上游结果链接 24h 失效，不落盘 = 白花钱）
  - 可选：instance_type（default/plus/ultra）、job_id（捞回孤儿任务）
examples:
  正例: ["把这条 28s 复刻片升成 4K", "这段 480p 太糊了，来个高清版"]
  反例: ["把 Sora 的水印去掉（去水印，走 video-dewatermark）", "把这张产品图变清晰（图片，不走本 skill）"]
version: 0.1.0
---

# 视频高清修复（video-enhance）

一条**基础工具 skill**：输入一个本地视频文件，输出一个高清版本。引擎不是本 skill —— 生成腿统一在**网关**里（`video.edit / enhance` 目的，RunningHub AI App `1990029249488801793`），本文件只写命令契约和关口。

```
python backend/gateway.py call video.edit enhance --account <LOOP.member> --json '<params>'
```

- 🔴 **不直连 RunningHub、不自己拼 curl**。预扣 / 结算 / 审计只有走网关才有。
- 与 `video-dewatermark` 同一套协议：`upload → run/ai-app(nodeInfoList) → query → 落盘`，同一个企业 key（`RUNNINGHUB_ENTERPRISE_API_KEY`），**节点号只住 config**（`.env` 可覆盖，代码里不留字面量兜底）。

## 参数（`--json`）

| 键 | 必填 | 说明 |
|---|---|---|
| `video_path` | ✅ | **本地文件**。给公网 URL 会被本地拒（先下到 `productions/<task>/inputs/`，ASCII 文件名） |
| `output_path` | ✅ | 本地落盘路径。上游结果链接 **24 小时失效**，不落盘 = 白花钱 |
| `instance_type` | ⬜ | `default`(24G) / `plus`(48G) / `ultra`(84G)，默认 `default`。4K 长片建议 `plus`/`ultra`（防 OOM） |
| `job_id` | ⬜ | **捞回孤儿任务**用：给了就只查询+下载，绝不重新提交（重提交=双花） |
| `poll_timeout` / `poll_interval` | ⬜ | 默认 2400s / 10s（高清比去水印烧 GPU 秒多，队列更久） |

## 节点配置（🔴 花钱前必须确认）

App `1990029249488801793` 的 `nodeInfoList` 可配置节点**公开 API 查不到**（元信息端点全 404/301，提交空 body 只回 1101「Node info error」）。当前 config 默认**只配了 video 节点**：

```
RUNNINGHUB_ENHANCE_VIDEO_NODE=""          # 待填：上传 fileName 进的那个 Load Video 节点号
```

**节点号为空 = 花钱前拦截**（`_enhance_payload` 直接拒，0 分损失）。所以第一次用这个 skill 必须：

1. 找**工作流作者**要可配置节点清单（网页版该 AI App 的「节点参数映射」面板，每个节点的 `nodeId` / `fieldName` / 可选值，截图或文字均可）；
2. 填进 `.env`：`RUNNINGHUB_ENHANCE_VIDEO_NODE=<上传视频文件的节点号>`（有输出分辨率/档位节点的也一并填上对应 config 字段）；
3. 再提交。若上游仍报 1101，按报错逐个补节点。

## 流程（完整交付链）

```
收片落本地 → ① 确认节点配置就绪（缺 = 拦在本地，不花钱）→ ② 报价等确认
→ ③ 跑网关（上传→提交→轮询→落盘）→ ④ 失败分流（只捞不重投）→ ⑤ 交付三件套 + 抽帧质检
```

**① 节点配置关。** `gateway.py resolve video.edit enhance` 看路由在不在；`RUNNINGHUB_ENHANCE_VIDEO_NODE` 为空就停在这一步，跟工作流作者要节点清单。**缺配置提交 = 白等一次 GPU 队列**。

**② 报价（花钱前铁律，数字从命令读，禁止凭记忆写价）：**

```
python backend/gateway.py resolve video.edit enhance
python backend/gateway.py quote  video.edit enhance --json '{"duration":<秒>}'
python backend/gateway.py credits
```

⚠️ `enhance` 单价 **60 分/10s 档是占位估算**（RunningHub 按 GPU 秒真扣钱，4K 升档比去水印烧得多；"1 元=几分"还没定）。报价时**明说是占位**，跑完第一条拿信封里的 `upstream_usage`（`task_cost_time` / `consume_money`）回来校准。

**③ 跑。** 一条命令走完（上传→提交→轮询→下载落盘）：

```
python backend/gateway.py call video.edit enhance --account local \
  --json '{"video_path":"C:\\dsh\\productions\\<task>\\in-1.mp4","output_path":"C:\\dsh\\productions\\<task>\\out\\enhanced.mp4","instance_type":"plus"}'
```

成功信封里有 `path`（本地文件）、`url`（24h 失效）、`job_id`。

**④ 失败分两类（同去水印）：**
- 回 `job_id`（提交成功，查询炸/超时/上游 FAILED）⇒ **等一会儿再 `--json '{"video_path":…,"output_path":…,"job_id":"<那个 id>"}'` 捞结果，0 二次花费**
- 没回 `job_id`（本地校验拒 / 提交失败 1101）⇒ 按 `error` 文案修（1101 = 节点清单没补全，回 ①）

**⑤ 交付三件套**（视频不能内联，只丢链接=没交付）：
- 本地路径：`productions/<task>/out/enhanced.mp4`（ASCII 名）
- 可点直链：`http://127.0.0.1:8100/api/film/file?task=<task>&rel=out/enhanced.mp4`
- 抽帧对比：`ffmpeg -ss <t> -i <out> -frames:v 1 check-hd-<n>.png` 和原片同时间戳帧并排贴进对话（人眼验"确实变清晰了"，不是"跑成功了"）

## 边界（不许越）

- 🔴 **节点配置未确认前，一次提交都不许发**（1101 报错烧的是 GPU 队列位，不是 0 分）。
- 上游是 GPU 队列，排队时间不可控；长片（>15s）排队更久，`poll_timeout` 默认给足 2400s，超时**只捞不重投**。
- 高清修复不承诺"原片内容不变"：升档工作流可能对边缘做平滑，抽帧时重点看文字/人脸是否糊掉或漂移。
- 不做图片升清（那是 `image.edit` 或 `image.generate` 的活）。
- 别人的品牌台标/水印不是高清修复要清的，叠加去水印需求先走 `video-dewatermark` 再升档（顺序不能反：先清后升，否则把水印一起升上 4K）。

## 改这条腿之前

默认 0 网络 0 花费（假传输层跑真代码，核端点/Bearer/落盘/只捞不重投）：

```
python C:\dsh\scripts\check-dewatermark-lane.py   # 47 条（含 _rh_video_edit_run 公共腿）
```

节点号/价格/路由改任何一处都要重跑。节点号只住 `backend/config.py`（`.env` 可覆盖），**代码里不留字面量兜底**。
