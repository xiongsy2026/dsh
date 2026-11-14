---
name: image-gen
description: >
  出一张静态图（产品图 / 场景底图 / 海报画面 / 带参考图的风格化图）。走 Weeksir 统一网关
  `image.generate / default`，模型 = `agnes-image-2.5-flash`（Agnes Image 2.5 Flash）。
whenToUse: >
  用户要的是**一张图**，不是一段视频。触发词：生图、出图、画一张、来一张、产品图、
  场景图、海报底图、按这张参考图出一张。
  例句："给这支按摩油出一张竖版场景图"、"按我这张产品图换背景"、"画一张客厅沙发摆拍图"。
whenNot: >
  要视频 / 动态成片走 /super-clone、/omni、/hypit-pipeline；把已有视频变清晰走 /video-enhance，
  去水印走 /video-dewatermark；换产品/换脸进视频是视频生成那条腿，不走本 skill；
  要文案 / 提示词本身不需要出图就别点这条。
inputs:
  - 一句话画面描述（必填）
  - 可选：尺寸 1K/2K/3K/4K、比例 1:1 / 3:4 / 4:3 / 16:9 / 9:16 / 2:3 / 3:2 / 21:9
  - 可选：参考图（**必须是公网 URL**；本地文件先 `gateway.py upload` 拿直链）
  - 输出落盘路径（必填 —— 上游回的是临时链接，不落盘 = 白花钱）
examples:
  正例: ["给这支琥珀瓶按摩油出一张 9:16 的摆拍图", "按这张产品图换到浴室场景", "画一张木纹桌面的沙发局部特写"]
  反例: ["把这条片子做成 15 秒视频（那是视频腿）", "把这张糊图变清晰（图片升清不走本 skill）"]
version: 0.1.0
---

# 生图（image-gen）

一条**基础工具 skill**：输入一段画面描述（可带参考图），输出一张落盘的图。引擎不在本 skill 里 ——
生成腿统一在**网关**（`image.generate / default`，模型 `agnes-image-2.5-flash`），本文件只写命令契约与关口。

```
python backend/gateway.py call image.generate default --account <LOOP.member> --json '<params>'
```

- 🔴 **不直连 Agnes、不自己拼 curl / httpx**。预扣、结算、审计只在 `gateway.invoke()` 里发生；
  绕过网关的出图 = 花了上游的钱而本机账本一行都没有（那是 10-12 现网点过名的形状）。
- 不挂右栏工作台、不登记意图（和 `video-enhance` / `voice-clone` 同一类：客户显式点了才干活）。

## 参数（`--json`）

| 键 | 必填 | 说明 |
|---|---|---|
| `prompt` | ✅ | 画面描述。空 prompt 在本地就被拒（不发请求、0 分） |
| `size` | ⬜ | `1K` / `2K` / `3K` / `4K`，默认 `1K`。名单外的值本地直接拒 |
| `ratio` | ⬜ | `1:1` `3:4` `4:3` `16:9` `9:16` `2:3` `3:2` `21:9`，默认 `1:1`（竖版带货图用 `9:16`） |
| `images` | ⬜ | 参考图数组。🔴 **只吃公网 URL** —— 本地文件先跑 `gateway.py upload <文件>` 拿直链（0 分、记台账） |
| `output_path` | ✅ | 本地落盘路径，**ASCII 文件名**，放 `productions/<task>/outputs/` 下。给了才下载，不给就只剩一条会失效的 URL |

🔴 **`--json` 里的 Windows 路径写正斜杠**（`"C:/dsh/productions/x/out.png"`）。
单反斜杠在 JSON 里是非法转义（`Invalid \escape`），双反斜杠经 shell 一层层吃掉后照样炸 —— 2026-10-27 实测：
`\\dsh\\` 那版直接 `JSONDecodeError`，改成正斜杠一次过。

## 渠道现状（2026-10-29 实测，别照旧文案写）

| purpose | 模型 | 探活 | 结论 |
|---|---|---|---|
| `gpt-image2.5` | `gpt-image-2.5-sunburst`（提速档 `-flare`） | **reachable** 781ms | ✅ **要 GPT Image 2.5 就用这条**。实测：出图墙钟 **29.9s**、实扣 **4 分**、`basis=flat`、落盘 1.04MB PNG |
| `default` | `agnes-image-2.5-flash` | **reachable** 88ms | ✅ 另一家的 2.5（不是 GPT），也在跑。实测 8.9s / 4 分 / 987KB |
| `generic` | 同 image2 端点，默认 `gpt-image-2` | 需代理（同上） | ⚠️ 只有 2.0，别当 2.5 用 |
| `nano` | zexapi `nano_banana_pro-2K` | unreachable | ❌ 别当兜底 |

🔴 **`image1.vibelearning.top` 这台机器直连会被 `WinError 10054` 强断**（DNS 通、TCP 443/80 通、TLS/HTTP 一发就断），
必须走本机代理：`.env` 的 `IMAGE2_PROXY_URL=http://127.0.0.1:10808`（与 `TIANYUE_PROXY_URL` 同一病因、同一形状）。
代理只注入三处：`Image2Client._client()`、网关落盘那一刀、`test_route` 探活 —— **别在第四个地方再拼一遍**。
清空 `IMAGE2_PROXY_URL` 就回到直连（那条腿会立刻报 unreachable，别误判成渠道死了）。

⚠️ 上游偶发 **500**（实测撞过一次、219s 才回）。同一形状**重发一次**就 200（15–24s）；只许重发一次 ——
出图属"结果未知"那类，无限重投就是双花。

单价现读，不许凭记忆写：`python backend/gateway.py prices`（`image.generate/*` 现在登记 **4 分/张**）。

## 关口

1. **生图免批、但不免账** —— `image.generate` 在 `SPEND_APPROVAL_EXEMPT` 第一条，直接出图不等点头；
   扣分、预扣、失败回滚照旧走账本。⚠️ 但**批量 >500 分**要先报价（AGENTS 那条通用口径）。
2. **失败不扣费**：上游 4xx/5xx、下载炸、prompt 空 ⇒ 退段；`credits.charged=0` 且 `hold` 回滚，
   别把"跑过"当"花了钱"。
3. **图上的文字不可信**：生成模型画包装文字必乱码（omni §10 实测）。要真实品牌字/规格数字，
   用实拍图贴回，或在提示词里明写"画面不出现任何文字"。

## 交付（图片这一路）

```
python scripts/deliver_pack.py <图片绝对路径> --json
```

- 它不抽帧、不要求 present，但会硬钉**直链那一格真回 `image/*`** —— 只看 200 不算数：
  markdown 图片加载失败会被回落成一行 alt 文字，客户看到的就"像没给图"。
- 交付那条消息里用 `![说明](http://127.0.0.1:8100/api/film/file?task=<task>&rel=outputs/<ascii名>.png)` **内联**；
  过程图 / 试跑图不要内联（只回路径），免得把对话刷成相册。
- 🔴 退出码非 0 = 没交付，不许收工。

## 边界（不许越）

- 只走 `image.generate / default` 这一条。**不许**因为"那条便宜"就切到 unreachable 的 `gpt-image2.5` / `nano` —— 换渠道等于换计费口径，批的价和花的价会对不上。
- 参考图只走 `images`（公网 URL）。塞本地路径进去，上游取不到图，报的错会被误读成"参数问题"。
- 本 skill 不出视频、不改视频里的画面；要动视频请走 /super-clone、/video-enhance、/video-dewatermark。

## 改这条腿之前

- 改模型名 / base_url / key：`gateway.py resolve image.generate default`（只看 model/provider/是否带 key，**别整份打印**）+ `gateway.py test_route image.generate default` 探活。
- 改单价 / 免批白名单：`python scripts/check-spend-approval.py`（G 组钉 `SPEND_APPROVAL_EXEMPT` 只有三条这个默认值）。
- 客户端原语在 `backend/services\agnes_image_client.py`（`IMAGE_SIZES` / `IMAGE_RATIOS` 是本地校验的唯一源）。
