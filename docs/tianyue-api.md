# api.tianyue.xyz 视频渠道 · API 接入文档（实测 + 文档口径分栏）

> 整理时间：2026-10-16。标注来源：**[实测]**=我打过的口真回来的；**[文档]**=渠道方给的口径（未逐条验证）；**[未探明]**=没试出来。
> Key 不写进文档（sha8=`5242c17e`，51 位）；接网关时配进 `.env` 的 `TIANYUE_API_KEY`。
> ⚠️ 当前这把 key **余额 ¥0.000000**（403 `insufficient_user_quota`）⇒ 一条真单都建不了；充值/换 key 后才能补实测。

## 两条 wan3.0 模型的实测差异（2026-10-20 · 直接决定兜底怎么写）

| 项 | `ME-wan30-1080p-1055` | `PIX-Wan30-pro-1080p-1055-se` |
|---|---|---|
| `duration` 下限 | 🔴 **不校验**：`duration=0` 照样建单（已实测建上） | 校验：`必须是>=5秒` |
| 有参考视频时的时长 | 未探明（ME 不拦，未知它按什么口径出） | **5–15 秒**（原话"有参考视频必须是5-15秒"），30s 被拒 |
| `resolution=720p` | 接受，但**输出仍是 1080×1920**（实测两条 completed 成片同尺寸，只有体积 18.38MB vs 12.57MB）⇒ 更像码率档 | 未测 |
| 素材下载 | 未探明 | **建单后会真去下载素材**：坏链回 `参考素材下载失败，请检查素材链接或稍后重试` |
| `aspect_ratio` | 白名单存在（方向同本地 6 档） | `7:9` 回 `aspect_ratio is not supported by this model` |
| 成片直链 | `https://dw.tianyue.xyz/proxy/down_video?token=<~234字符>`：**带签名、会过期、下载不需要鉴权** | 同形 |
| 出片是否自带音轨 | **是**（aac，没给参考音频也有） | 未测（5 条都 failed） |

⇒ 对兜底的硬结论：**主腿能过的参数，兜底腿不一定能过**（ME 不查 duration、PIX 查 ≥5s）。
   所以换腿时 `TIANYUE_DURATION_MODE` 的口径必须跟着腿走，否则"兜底"表现为"两条都被拒"。

## 参考视频到底有没有被"读进时间轴"（2026-10-21 钉死 · 判据 `scripts/prove-reference-video.py`）

`completed` 不证明它照参考段走。判据用**整段运动能量时间序列的相关**（外观无关，只看什么时候在动、动多大），
并且必须带三个对照：**跨任务错配**（另一条片的参考段）、**半段错位**、**仪表自检**（参考段 vs 它自己的重编码，拿不到 ≥0.95 就是仪表坏，本轮不许输出结论）。

| 任务 | 参考段 | 同段相关 | 跨任务错配 | 半段错位 | 仪表自检 | 判定 |
|---|---|---|---|---|---|---|
| `clone-20261016`（13s，`task_WPBbZ8…`） | 720×960 / 27fps / **1.38MB** | **0.574** | 0.167 | 0.144 | 0.9997 ✅ | **参考视频进了时间轴**（不是只被下载） |
| `super-clone-20261020-1438`（15s，`task_pS2W3p…`） | 1080×1920 / 30fps / **19.77MB** | 0.124 | 0.119 | 0.196 | 0.9993 ✅ | **没跟上**：与错配持平 ⇒ 这一条不能说它按参考段走 |

🔴 走过的弯路要记下：**第一版判据用的是 SSIM，直接被证伪**——同刻 0.275 反而低于跨任务错配 0.419。
原因是复刻本来就把商品/脸/衣服/配色全换掉，像素结构必然不像 ⇒ 拿 SSIM 判"没读参考视频"是**判据算错**，
会把合格的对象判成红并送去重跑花钱的单。SSIM 现在只在报告里当旁证，不进判据。

**两条的差量假设（未证）**：都是 h264/aac、`pix_fmt=yuv420p`，但参考段体积差 **14 倍**（19.77MB vs 1.38MB）、
码率 ~10.7Mbps vs ~0.87Mbps。⇒ 最可疑的是**上游对参考视频有体积/码率上限，超了就悄悄不用**（它照样 completed）。
单变量复验办法：把 A 的参考段压到 B 那档（同分辨率、约 1–2Mbps 的 mp4）重交一次 ⇒ **要一条真单，得走批准**。
若证实 ⇒ 提交腿在上传前给参考段加一道归一（长边与码率上限），这条今天**管线里没有**。

## 事故留档：2026-10-20 探测误建 8 条真单（全部在网关账本外，无一行台账）

探测都打在 `POST /v1/videos` 的**裸 HTTP**上（没经 `gateway.invoke`）⇒ 上游真花钱、我方账本零行。
这是"绕过台账"那个形状，我自己犯了一次；记录在此防下次照抄"必然非法锚点"的想法——**ME 不校验 duration，
所以那个锚点不成立**。

| # | task_id | 模型 | 意图 | 终态 |
|---|---|---|---|---|
| 1 | `task_rIoIXogLG5Pc2JLfUByayAAE5hTw7e99` | ME | 不传图会不会拒 | completed（不传图**能建**⇒ 图非必填） |
| 2 | `task_OCuuSmc5Rc81g7WKENEhGYmIJzCGbNkt` | ME | 720p 收不收 | completed（1080×1920，体积更小） |
| 3 | `task_HtWSW5JqbyNzwlGD1hD0ZffqUz4FvmF7` | PIX | 图片上限 | failed |
| 4 | `task_eUlMo6dHmixMS1z6NrNIu7HcHPZZ3kgE` | PIX | 视频 3 个 | failed |
| 5 | `task_vc9DOed5EruQXPoNWOe0FIpWTiFOmeN2` | PIX | 音频 1 | failed |
| 6 | `task_usT2CiF1zTv3OOCafCdnHZGxiED85lUK` | PIX | 音频 3 | failed |
| 7 | `task_BRDNyJI7IPGdGJnQt3OKvWCgw5HOiWwW` | PIX | 图+视频+音频齐 | failed：参考素材下载失败（喂的是编出来的死链） |
| 8 | `task_YYaKpiSrKxnu2u8gGpAZzs4jj3xBHbAi` | ME | duration=0 该被拒 | **建上了**（ME 不校验） |

## 一分钟认识

- **这是"复刻形状"能用的口**：`image_urls`（参考图）+ `video_urls`（参考视频）+ `audio_urls`（参考音频）都在参数表里
  ⇒ 超级复刻那种"换产品复刻原片"的入参齐了（wanwan 那条只有 prompt/seconds/size，够不着）。
- 模型表 **146 条** [实测]，视频一族含 **`ME-wan30-1080p-1055`**（用户指定用这条）、`ME-wan30-720p-1055`、
  `ME-WAN3-prime-1080p-1055`、`S-Wan30-*`、`PIX-Wan30-pro-*`、`C-H3-*`、`C-SD2*`、`ME-SD2.5-1000-720p-30s` 等；
  另有文本（deepseek-v4-*、glm-5.3…）与生图（gemini-3*-image-preview-*）腿。
- 计费字段**模型表里一个都没有** [实测] ⇒ 单价只能标"无数据"（模型名尾缀 1055/1050/933/1000 猜不得）。
  [文档] 口径：`duration` 对**按次计费**模型固定传 `1`；对**按秒计费**模型要和 `video_duration` 传同一个值。

## 端点

| 方法 | 路径 | 来源 | 实测结论 |
|---|---|---|---|
| GET | `/v1/models` | [实测] | 200，146 条，字段 `id/object/created/owned_by/supported_endpoint_types`（**无价格**） |
| POST | `/v1/videos` | [文档]+[实测] | 建单（形状见下）；⚠️ 余额不足时**直接 403，不建单** |
| GET | `/v1/videos/{task_id}` | [文档] | 轮询（5 秒一跳建议）；状态 `queued / in_progress / completed / failed` |
| GET | `/v1/videos`（列表） | [实测] | **404** `Invalid URL` —— 没有列表口 |
| GET | `/v1/balance` | [实测] | **404** —— 没有余额口 |
| GET | `/v1/dashboard/billing/subscription` | [实测] | 200，但是 OpenAI 样板（额度都是 1e8 美元那种占位），**不能当余额看** |

鉴权：`Authorization: Bearer <key>`（我们请求都带 `User-Agent: Mozilla/5.0`）。
错误格式 [实测]：`{"code":"…","message":"…","data":null}`（如 `insufficient_user_quota`），
与 wanwan 那种 `{"error":{...}}` 不同——**解析器别混用**。

## POST /v1/videos 参数

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `model` | string | 是 | 如 `ME-wan30-1080p-1055`（用户指定的直连模型 id） |
| `prompt` | string | 是 | 视频提示词（我们的编译产物 4448 字符**未实测**是否超长） |
| `duration` | integer | 是 | [文档] 按次计费固定传 `1`；按秒计费传 `video_duration` 同值 |
| `video_duration` | integer | 否 | [文档] 真实时长；固定时长模型传固定值 |
| `aspect_ratio` | string | 否 | `16:9 / 9:16 / 4:3 / 3:4 / 1:1` |
| `resolution` | string | 是 | `480p / 720p / 1080p` |
| `image_urls` | string[] | **是** | 参考图，**只收公网 http/https** |
| `audio_urls` | string[] | 否 | 参考音频，公网 URL |
| `video_urls` | string[] | 否 | 参考视频，公网 URL |

轮询返回 [文档]：`id / task_id / object=video / model / status / progress / created_at / completed_at / seconds / url / video_url / metadata.url`；
完成后 `url`/`video_url` 给 mp4 直链。

## 我们这边已备好的一键入参（充值后直接发）

素材已用 `gateway.py upload --key-hint wan3`（0 分）传成公网直链，**无鉴权可达已验（200）**：

```json
{
  "model": "ME-wan30-1080p-1055",
  "prompt": "<outputs/step2_wan3/prompt.txt 全文，4448 字符>",
  "duration": 1,
  "video_duration": 12,
  "aspect_ratio": "9:16",
  "resolution": "1080p",
  "image_urls": [
    "https://rh-hk-images-switch.xiaoyaoyou.com/input/openapi/bb896ad7….png",
    "https://rh-hk-images-switch.xiaoyaoyou.com/input/openapi/d5e590ad….png"
  ],
  "video_urls": [
    "https://rh-hk-images-switch.xiaoyaoyou.com/input/openapi/6cd5e325….mp4"
  ]
}
```

（素材原片 = 泡脚液那条 11.61s 竖版 1080x1920；两条产品图 = 吸嘴袋 + 盒装。直链全名见任务目录 `scratch/` 或重跑 `gateway.py upload` 拿。）

## 实测记录

| 时间 | 事件 |
|---|---|
| 10-16 | GET /v1/models 200（146 模型，`ME-wan30-1080p-1055` 在表里） |
| 10-16 | GET /v1/videos、/v1/balance 404（没有列表/余额口）；/v1/dashboard/billing/subscription 200（占位样板，不可信） |
| 10-16 | POST /v1/videos（复刻形状：参考视频+2 产品图+编译 prompt）⇒ **403 `insufficient_user_quota`，剩余额度 ¥0.000000**，未建单、未扣费 |

## 未探明（别当已有）

- **真单一条都没跑成**（余额 0）⇒ 出片质量、时长是否照 `video_duration` 出、`duration=1` 在这个模型上到底是按次还是按秒、
  prompt 长度上限、参考视频上限（时长/体积）、失败重试语义、成片保留期：**全未验证**。
- 计费与单价：模型名尾缀（1055/1050/933/1000）是价还是版本号**不知道**；充值前先问渠道方"按次还是按秒、单价多少"。
- 多任务并发、排队语义（`queued` 队列有没有 `ahead` 之类的位次）未见。

## 接进网关要带的标注（照实说）

1. 余额是硬前置：¥0 时**连建单都不给**（403），不是"建了单失败"。
2. 只收**公网** media URL ⇒ 调用前必须先 `gateway.py upload`（0 分、记台账），本地路径/公司内网链都会被上游抓不到。
3. 计费口径未知 ⇒ 接网关先按 `flat`（按条）登记并标"占位·未实测"，跑通一条拿实扣再校准。
4. 错误信封与 wanwan 不同（`{code,message,data}` vs `{error:{…}}`），适配器要分别解析。
