---
name: voice-clone
description: >
  声音克隆工作流：给一段参考音频，克隆音色后合成口播。走 RunningHub
  云端 ComfyUI 工作流（AI App 1965614643077070850），先上传参考音频，
  提交前与客户确认待说文本，产物 = 本地音频文件（mp3/wav）。
whenToUse: >
  用户给一段参考音频（真人原声 / 自己录的样音），要一条"用这个声音说这段话"的口播；
  或给文本 + 参考音频，要求声音克隆配音。
  触发词：声音克隆、音色克隆、克隆我的声音、用这个声音配音、口播配音、换声成同一音色。
  例句："用我这段样音把这段口播念出来"、"把这段文案用这位客户的声音克隆一遍"。
whenNot: >
  只做纯文本 TTS（无参考音频、不用克隆）不走本 skill；
  视频里的人对口型 / 口型同步（lipsync）不走本 skill；
  把 A 的声音换成 B 的音色（已有人声轨 + 参考音）也走本 skill，但产物是音频不是视频。
inputs:
  - 参考音频文件（本地路径，建议 10-60 秒清晰人声；先落到 productions/<task>/inputs/）
  - 待说文本（口播文案；缺了追问一次，一次问完）
  - 输出路径（本地落盘；上游结果链接 24h 失效，不落盘 = 白花钱）
  - 可选：instanceType（default/plus/ultra，默认 default）
examples:
  正例: ["用我这段样音把这段口播念出来", "克隆这位主播的声音念 30 秒带货词"]
  反例: ["帮我把这条视频的口型对上字幕（lipsync）", "随便念一段，不用克隆（纯 TTS）"]
version: 0.1.0
---

# 声音克隆工作流（voice-clone）

一条**基础工具 skill**：输入参考音频 + 待说文本，输出克隆音色的口播音频文件。
引擎 = RunningHub 云端 ComfyUI 工作流（AI App `1965614643077070850`），本文件只写命令契约和关口。

🔴 与 `video-dewatermark` / `video-enhance` 的区别：这条**不经 `gateway.py` 网关**（网关尚未接 RunningHub 声音克隆 purpose），
直接调 RunningHub OpenAPI，**没有网关的预扣/结算/审计**——报价靠 RunningHub 账户余额实扣，跑完看 `usage` 回报实扣。
所以纪律照旧：**花钱前报价、等客户明确同意，才提交**。

## 前置（一次性）

- **RunningHub API Key**：客户注册 RunningHub 并充值。Key 放 `backend/.env` 的
  `RUNNINGHUB_API_KEY`（32 位，自动生成，别外泄）。
- **可配置节点清单**：AI App `1965614643077070850` 的 `nodeInfoList` 映射**公开 API 查不到**，
  第一次用之前找**工作流作者**要：哪个 `nodeId` 吃参考音频、哪个吃文本、哪个出结果（`outputs_to_execute`）。
  拿到后写进 `.env`：
  ```
  RUNNINGHUB_VOICECLONE_REF_AUDIO_NODE=<参考音频上传节点号>
  RUNNINGHUB_VOICECLONE_TEXT_NODE=<文本输入节点号>
  RUNNINGHUB_VOICECLONE_OUTPUT_NODE=<结果输出节点号>   # 提交响应 promptTips 会回 outputs_to_execute，可校准
  ```
  节点号为空 = **本地拦截，不提交**（0 花费）。

## 参数

| 键 | 必填 | 说明 |
|---|---|---|
| `ref_audio_url` / 参考音频 | ✅ | 本地文件先传 RunningHub 上传口拿 URL；也可直接传公网 URL / Base64 Data URI |
| `text` | ✅ | 待说文本。不给 = 没这活，先追问 |
| `output_path` | ✅ | 本地落盘路径（ASCII 名）。上游结果链接 **24h 失效** |
| `instanceType` | ⬜ | `default`(24G) / `plus`(48G) / `ultra`(84G)，默认 `default` |
| `usePersonalQueue` | ⬜ | 是否用个人独占队列，默认 `false` |
| `webhookUrl` | ⬜ | 完成回调地址；本 skill 走轮询，不用 |
| `retainSeconds` | ⬜ | 仅企业共享 key 生效，10-180s，会额外计费，默认不传 |

## 命令契约（直连 RunningHub OpenAPI）

```
# ① 上传参考音频（拿 URL；链接有效期 1 天，提交要紧接着用）
curl -X POST 'https://www.runninghub.ai/openapi/v2/media/upload/binary' \
  -H "Authorization: Bearer <RUNNINGHUB_API_KEY>" \
  -F 'file=@<本地参考音频路径>'
# 响应 data.download_url = 该音频的 RunningHub URL（data.fileName 亦可当相对引用）

# ② 提交工作流（nodeInfoList 把参考音频 URL + 文本挂到对应节点）
curl -X POST 'https://www.runninghub.ai/openapi/v2/run/ai-app/1965614643077070850' \
  -H "Authorization: Bearer <RUNNINGHUB_API_KEY>" -H 'Content-Type: application/json' \
  -d '{
    "instanceType": "default",
    "usePersonalQueue": "false",
    "nodeInfoList": [
      { "nodeId": "<RUNNINGHUB_VOICECLONE_REF_AUDIO_NODE>", "value": {"<音频字段>": ["<data.download_url>"]} },
      { "nodeId": "<RUNNINGHUB_VOICECLONE_TEXT_NODE>",     "value": {"<文本字段>": ["<text>"]} }
    ]
  }'
# 响应 = taskId（状态 QUEUED/RUNNING…）；promptTips 里的 outputs_to_execute 可校准输出节点号

# ③ 轮询到终态（SUCCESS / FAILED），间隔 10s、超时 1800s
curl -X POST 'https://www.runninghub.ai/openapi/v2/query' \
  -H "Authorization: Bearer <RUNNINGHUB_API_KEY>" -H 'Content-Type: application/json' \
  -d '{ "taskId": "<taskId>" }'
# SUCCESS ⇒ results[].url（24h 失效）+ results[].nodeId + usage（consumeMoney/consumeCoins/taskCostTime）

# ④ 下载落盘
curl -o <output_path> '<results[].url>'
```

## 流程（完整交付链）

```
收参考音频落本地 → ① 节点配置关（缺 = 拦在本地）→ ② 定文本（缺了问一次）
→ ③ 报价（RunningHub 余额 + 预估）等客户同意 → ④ 上传→提交→轮询→落盘
→ ⑤ 失败分流（只捞不重投）→ ⑥ 交付：路径 + 试听链接 + 如实报音色
```

**① 节点配置关。** 三个 `RUNNINGHUB_VOICECLONE_*_NODE` 有一个空就停在这，跟工作流作者要清单，不提交。

**② 定文本。** 客户没给文案 ⇒ 追问一次问完（要哪段话、语气要求）；给了就用。

**③ 报价（铁律）。** RunningHub 按 GPU 秒真扣钱，没有分/档占位价可查——报价口径 =
`路由：RunningHub 云端 ComfyUI 工作流 1965614643077070850` + `instanceType`（默认 default）
+ `客户 RunningHub 余额`（客户自报 / 跑完看 usage 回报实扣）
+ `预计跑 1 次，失败重跑不另收客户费` + 明确问"跑不跑"。
🔴 客户同意**只覆盖这一次**；换文案、换实例档位 ⇒ 重新报价。

**④ 跑。** 一条链路走完（上传→提交→轮询→下载）。成功信封里有 `taskId`、`results[].url`、`usage`。

**⑤ 失败分流（只捞不重投）：**
- 回 `taskId`（提交成功了，查询炸 / 超时 / FAILED）⇒ 拿 `taskId` 只查询 + 下载，**绝不重新提交**（重提交 = 双花）。
- 没回 `taskId`（上传就拒 / 提交就 1101 节点错）⇒ 按 `errorMessage` 修参数（多半是节点号没填对），改了再提交。
- 轮询里查询异常按死因退避重试**同一个** taskId（连续 6 次才判死）。

**⑥ 交付。**
- 本地路径：`productions/<task>/outputs/voice-clone-<n>.mp3`（ASCII 名）
- 可点直链：`http://127.0.0.1:8100/api/film/file?task=<task>&rel=outputs/voice-clone-<n>.mp3`
- 🔴 **如实报效果**：克隆音色和原音色相似度由人耳定，跑完贴直链让客户听，**不写"已克隆完成"就当交付**；
  客户听着不像 ⇒ 记下来，属工作流侧调参问题，报给作者，不在本 skill 里死磕。

## 边界（不许越）

- 只出**音频**文件（mp3/wav 视工作流出口），不出视频、不对口型。
- 参考音频必须是**客户自己或有授权的声音**；克隆他人音色涉及肖像/声音权，提交前问一句授权。
- 不做纯文本 TTS（无克隆需求）；那条走别的腿，不走本 skill。
- 一次客户同意 = 一次提交；批量克隆（>1 段文案）要重新报价。

## 没有右栏页签

本 skill **不登记意图、不挂台**（`scripts/intent.py` 不认它）：一次性工具，没有常驻工作流界面。

## 改这条腿之前

- 直连 RunningHub 的这条腿**不在网关账本上**（无预扣/结算/审计），跑完务必把 `usage`
  （`consumeMoney`/`consumeCoins`/`taskCostTime`）报回，攒 3 条实测后评估是否接入网关。
- 节点号只住 `.env`，**代码里不留字面量兜底**；改节点映射先跑通 `promptTips.outputs_to_execute` 自检。
