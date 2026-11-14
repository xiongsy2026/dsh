# wanwan.coolhs.com 视频渠道 · API 接入文档（只记实测）

> 整理时间：2026-10-16。**这一页只写打过的口和真回来的东西**，没验证的一律进「未探明」清单，不猜。
> 测试单：`d88659ed-4c55-4c9d-9478-b1c53830b532`（prompt=月光下的屋顶，30s，1080x1440 → 实出 1246×1662）。
> Key 不写进文档：现用那把由用户提供，sha8=`70d6440e` 类（记 sha8 不记原文）；接网关时配进 `.env` 的 `WANWAN_API_KEY`。

## 一分钟认识

- **是 OpenAI-video 风格的文生视频口**（`POST /v1/videos` → 轮询 → `…/content` 取片）。
- **时长 2–30 秒**：`seconds=31` 会被服务端**夹回 30 并照常建单**（不是报错）；30 秒实测出片成功。
- **prompt 会原样传到生成侧**（回包回显 + 网页卡片标题 + 成片画面三重印证：满月夜空/城市天际线/屋顶人物，完全贴题）。
- ⚠️ **尺寸会漂**：请求 `1080x1440`（3:4），实出 **1246×1662**（3:4 对、像素不照给）——别拿它当精确出图。
- ⚠️ **队列长**：实测排队 392 条 → 0 条走了约 **1 小时 40 分**；`wait.ahead` 是真实队列位。
- ⚠️ **额度浅**：一次建单后同 key 后续请求回 `insufficient_quota`（网页右上角 `0 次`）；要批量先确认额度。
- 只验证了**纯文生视频**（见「未探明」：参考图/参考视频没试出来）。

## 端点（全部实测过）

| 方法 | 路径 | 用途 | 实测 |
|---|---|---|---|
| GET | `/v1/models` | 模型表 | 200，4 个：`wan` / `wan-480p` / `wan-720p` / `wan-1080p` |
| POST | `/v1/videos` | 建单 | 200 回任务对象（`status=in_progress`） |
| GET | `/v1/videos/{id}` | 轮询 | 200，含 `progress` / `status` / `wait` |
| GET | `/v1/videos/{id}/content` | 取成片 | 200 `video/mp4`（54.4MB），原始二进制 |
| GET | `/v1/videos?limit=N` | 列表 | 200：`{object, data[], first_id, last_id, has_more}` |

鉴权：`Authorization: Bearer <key>`；请求都带了 `User-Agent: Mozilla/5.0`（**没试不带行不行**，照抄带上）。
错误格式：`{"error": {"message": "…", "type": "…", "param": null, "code": null}}`。

## POST /v1/videos 请求参数（实测行为）

| 字段 | 类型 | 实测行为 |
|---|---|---|
| `model` | string | `wan-1080p` 出 1080 档；不存在的模型回 400 `没有该模型`（type=`invalid_request_error`） |
| `prompt` | string | **原样进生成**：回包 `prompt` 字段原样回显；成片内容贴题（抽帧验证） |
| `seconds` | **string** | 示例用字符串 `"5"`；`"31"` → 服务端夹回 `"30"` 并建单成功；下限 2 未实测（发 `"1"` 撞的是配额错，见下） |
| `size` | string | `1080x1440` 收了但**出片 1246×1662**（比例 3:4 保、像素漂）；文档说也支持 `21:9 / 16:9 / 9:16 / 3:4`（**未实测**）；非法值 `123x456` 也没报参数错（撞的是配额错） |

⚠️ **错误会被配额错掩盖**：额度不够时，参数级问题（seconds 越界、size 非法）也回 **403 `配额不足`（type=`insufficient_quota`）**，
不回参数错 ⇒ 判参数问题要用**额度充足**的 key，别拿 403 反推参数错。

## 返回对象字段（`/v1/videos/{id}`，出片后）

```
id, object="video", model, prompt, seconds("30"), size("1080x1440"),
status("in_progress"|"completed"), progress(2→100),
created_at, completed_at, expires_at(未见值), error(null), remixed_from_video_id(null),
wait: { phase("queued"|"done"), label("已发车"|"已出片"), detail("前面还有 392 条"), ahead(392→0),
        eta_depart_at, eta_start_at }
```

`wait.ahead` 就是排队位；出片后 `phase=done / label=已出片 / ahead=0`。

## 可抄脚本（三条）

```bash
# ① 建单（30 秒竖版）
curl -sS -X POST https://wanwan.coolhs.com/v1/videos \
  -H 'Authorization: Bearer WAN-XXXX' -H 'User-Agent: Mozilla/5.0' \
  -H 'Content-Type: application/json' \
  -d '{"model":"wan-1080p","prompt":"你的提示词","seconds":"30","size":"1080x1440"}'

# ② 轮询（看 wait.ahead 估队列；status=completed 就去取片）
curl -sS https://wanwan.coolhs.com/v1/videos/<id> \
  -H 'Authorization: Bearer WAN-XXXX' -H 'User-Agent: Mozilla/5.0'

# ③ 取成片（原始 mp4）
curl -sS -o out.mp4 https://wanwan.coolhs.com/v1/videos/<id>/content \
  -H 'Authorization: Bearer WAN-XXXX' -H 'User-Agent: Mozilla/5.0'
```

## 实测记录（一条单的完整生命周期）

| 时间 | 事件 |
|---|---|
| 16:43 | 建单（seconds=31 → 被夹成 30，size=1080x1440）→ `in_progress / progress=2 / wait: 前面还有 392 条` |
| 17:0x–18:2x | 队列 392 → 312（中途约 0.8 条/分钟），一度像停住 |
| 18:2x | `completed / progress=100 / ahead=0` |
| 同时 | 下载 `/v1/videos/{id}/content` → 54,427,394 字节 |
| ffprobe | **时长 30.00s** ✓、**1246×1662**（请求 1080x1440）、音轨 **AAC 44.1kHz 立体声** ✓ |
| 抽帧 | 8 帧连贯（远景屋顶→推近侧脸），画面=「月光下的屋顶」，prompt 遵循 ✓ |

## 接进网关要带的标注（照实说，别美化）

1. **排队不可控**：`wait.ahead` 是事实来源，报价/交付时要报队列位，别承诺出片时间。
2. **额度浅**：一次建单就可能 `insufficient_quota`；批量化前先确认额度（网页 `0 次` 那个数）。
3. **尺寸漂移**：`size` 只当"比例请求"，不保证像素；要精确尺寸得后处理缩放。
4. **计费口径未知**（没拿到单价/余额口：`/v1/balance` 不存在，`/v1/dashboard/billing/subscription` 404）⇒ 接网关只能按 `flat`（按条）记账并标"未定价"。
5. **错误语义**：403 `insufficient_quota` ≠ 参数错；参数验证要在额度充足时做。

## 未探明（别当已有）

- **参考图 / 参考视频入参**：`POST /v1/videos` 文档只给了 prompt/seconds/size；`image` / `reference_video` 之类字段**没试出来**（发探针会真建单烧额度，等你点头再试）⇒ **复刻形状（超级复刻）现在够不着这条腿**。
- 画幅枚举（21:9 / 16:9 / 9:16 / 3:4）只在文档口径里，没逐个实测。
- `seconds` 下限 2 未验证（被配额错挡了）；多单并发、prompt 长度上限、成片保留期（`expires_at` 一直为 null）都没测。
- 计费与额度规则（`0 次` 什么时候恢复、单价多少）：**无余额口，问渠道方**。
