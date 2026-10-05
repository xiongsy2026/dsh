---
name: video-dewatermark
description: "Remove the generation watermark/text-and-icon overlays from an AI-generated video (Sora text+icon, Seedance text+icon) by running one file through the WeekSir unified gateway's video.edit lane (RunningHub ComfyUI AI App). Takes a LOCAL video file, quotes credits before spending, downloads the result before the 24-hour upstream link expires. Use when the user says '去水印', '去掉水印', '清水印', '把 Sora/Seedance 的字去掉', or hands over a generated mp4 and asks for a clean version. Not for logo/overlay removal you own, not for burned-in third-party brand marks (compliance), not for images."
whenToUse: 用户给一条已生成的视频（多为 Sora / Seedance / 我们自己的 omni·hypit 产物），要一版没有平台文字和图标的干净片。图片去水印、台标遮挡、马赛克、字幕硬压制这些都不走本 skill。
---

# 视频去水印（video-dewatermark）

一条**基础工具 skill**：输入一个本地视频文件，输出一个干净版本。引擎不是本 skill —— 生成腿统一在**网关**里，本文件只写命令契约和关口。

```
python D:\video-skill\backend\gateway.py call video.edit dewatermark --account <LOOP.member> --json '<params>'
```

- `capability=purpose` = `video.edit` / `dewatermark`，上游 = RunningHub AI App `2012077432525824001`（清 Sora / Seedance 的文字与图标）。
- 🔴 **不直连厂商、不自己拼 curl**。预扣 / 结算 / 审计只有走网关才有；直连的那笔钱在账本上对不到人。
- 参数（`--json`）：

  | 键 | 必填 | 说明 |
  |---|---|---|
  | `video_path` | ✅ | **本地文件**。给公网 URL 会被本地拒（要先下载到 `productions/<task>/inputs/`） |
  | `output_path` | ✅ | 本地落盘路径。上游结果链接 **24 小时失效**，不落盘 = 白花钱 |
  | `duration` | ⬜ | 秒。不给就用 `ffprobe` 自己探；探不到会**拒**（不猜，猜了会花一次钱才知道） |
  | `fps` | ⬜ | 只认 `16` / `24`（上游文档），默认取 config 的 24 |
  | `instance_type` | ⬜ | `default`(24G) / `plus`(48G) / `ultra`(84G)，默认 `default` |
  | `prompt` | ⬜ | 清理提示词。不传=全清（config 默认那条 Sora+Seedance）；客户**挑着清**时用位置限定写法（位置+形态+保留区，见流程②） |
  | `job_id` | ⬜ | **捞回孤儿任务**用：给了就只查询+下载，绝不重新提交（重提交=双花） |
  | `poll_timeout` / `poll_interval` | ⬜ | 默认 1800s / 10s |

## 流程（完整交付链）

```
收片落本地 → ① mimo 找水印 → ② 列位置问客户选哪些 → ③ 报价（含清理提示词）等确认
→ ④ 带 prompt 跑网关 → ⑤ 失败分流（只捞不重投）→ ⑥ 交付三件套 + 质检
```

**0. 拿到本地文件。** 用户给的是链接就先下载；文件名用 ASCII（`in-<n>.mp4`），别把中文和空格带进路径。本地落 `productions/<task>/inputs/`。

**① 找水印：找视频里哪些位置有文字/图标，这步交给 mimo（video.analyze 的 mimo purpose，按 token 结算，不花 GPU 分）**：

```
python D:\video-skill\backend\gateway.py call video.analyze mimo --account <LOOP.member> \
  --json '{"path":"<in>","question":"逐帧扫这条片，找出所有出现文字或图标的位置：每处给 位置(左上/右上/左下/右下/中下…)、形态(平台 logo/生成标识/文字条/数字角标)、出现时段(全程/前 n 秒)。没有就明说没有。"}'
```

- mimo 报"没有文字和图标" ⇒ 这条没得清，直接回客户"原片没有平台水印"，**不进报价**，不花 GPU 分。
- 定位不准/含糊（只说"有些文字"不给位置）⇒ 抽 1–3 帧贴图自检，**只信眼睛**：`ffmpeg -ss <t> -i <in> -frames:v 1 spot-<n>.png` 贴进对话核对位置后再问。
- 这步是"找出有哪些"，不是承诺能清——清不清得掉由②的客户选择和④的结果决定。

**② 把位置清单回给客户，问他清哪些**（结合①的输出，按形态+位置列成可选项，一次问完）：

> 检测到：① 左下角 Sora 生成标识（全程）② 右上角 Seedance logo（前 2 秒）。要清哪条？全部 / 挑着来 / 一条都不清？

- 客户挑着清 ⇒ ③ 的清理提示词按选中位置写：`"Remove the watermark text and icon at the bottom-left corner only; keep everything else in the frame unchanged."`（位置+形态+保留区三要素；不挑=全部 ⇒ 不传 `prompt`，走 config 默认那条）。
- 客户说不清 ⇒ 默认全清（config 默认 prompt），明说一句即可。

**③ 报价并等确认**（AGENTS.md 铁律，按条计费能力一律要先报）。数字**从命令里读，禁止凭记忆写价**：

```
python D:\video-skill\backend\gateway.py resolve video.edit      # 路由：provider / base_url / AI App 号（不含密钥）
python D:\video-skill\backend\gateway.py quote  video.edit --json '{"duration":<秒>}'
python D:\video-skill\backend\gateway.py credits                 # 余额
```

`quote` **只算不花钱**（不建预扣、不提交），回 `unit_price`（分/10 秒档）与 `credits_estimate`（这条的预计总扣分）。
报价四件套 = 路由（RunningHub AI App）· 单价（分/10 秒档）· 时长档数 · 预计总扣分 + 当前余额，并列出②定下的**清理提示词**（客户改范围要重新报价）。
⚠️ **这条的单价是占位数**（`PRICE_ROUTES` 里标了「没有实测价」）：RunningHub 按 GPU 秒真扣钱，而"1 元=几分"还没定。报价时要**明说这是占位估算**，跑完第一条拿信封里的 `upstream_usage`（`task_cost_time` / `consume_money`）回来校准。
①的 mimo 调用按 token 结算、量小不用单报；单条预估 >50 分才先报价。

**④ 跑。** 调用内部会 上传→提交→轮询→下载，一条命令走完；成功信封里有 `path`（本地文件）、`url`（24 小时失效的上游链接）、`job_id`。带 `prompt` 覆盖默认清理提示词就写在 `--json` 里（见参数表）。

**⑤ 失败分两类，处理完全不同：**
- 回 `job_id`（提交成功了，查询炸/超时/上游 FAILED）⇒ **不要重跑整条**。等一会儿再 `--json '{"video_path":…,"output_path":…,"job_id":"<那个 id>"}'` 捞结果，0 二次花费。
- 没回 `job_id`（本地校验就拒了 / 提交就没成功）⇒ 按 `error` 文案修参数，改了再提交。
- 轮询里查询异常是**退避重试同一个 taskId**（连续 6 次才判死），所以偶发 SSL 掐线不需要你干预。

**⑥ 交付三件套**（DSH 视频不能内联，只丢链接=没交付）：
- 本地路径：`productions/<task>/outputs/dewatermarked-<n>.mp4`（ASCII 名）
- 可点直链：`http://127.0.0.1:8100/api/film/file?task=<task>&rel=outputs/dewatermarked-<n>.mp4`（8100 支持 Range，能拖进度条）
- 抽帧贴图：`ffmpeg -ss <t> -i <out> -frames:v 1 check-<n>.png` 把图贴进对话，让人不点开也能看

**⑦ 质检（花 token 不花钱）**：`gateway.py call video.analyze --json '{"path":"<out>","question":"画面里还剩哪些文字或图标？画面有没有被破坏？"}'`。清理不干净要**如实说**，别把"跑成功了"当"水印没了"。

## 边界（不许越）

- 只测过 **Sora / Seedance 的平台文字与图标**这一类。别的平台、别的形态（半透明台标、跑马灯、烧死在画面里的第三方品牌）不承诺效果——①mimo 报出来的位置如果不在 Sora/Seedance 形态里，先跟客户说清"这条没保证"再决定要不要花 GPU 分。
- 🔴 **别人的商标/署名/平台水印，去掉可能构成侵权**。要动这类之前先问用户拿到授权没有；本 skill 默认服务对象是"清掉我们自己生成的那条片上的平台标记"。
- 不做图片去水印（那是另一条腿，且这条工作流只吃视频）。
- 不承诺"一次过"：上游是 GPU 队列，排队时间不可控；超时只捞不重投。

## 没有右栏页签

本 skill **不登记意图、不挂台**（`scripts/intent.py` 不认它）：它是一次性工具，没有要常驻的工作流界面。装了它右栏什么都不该弹——弹了就是回归，跑 `python scripts/check-intent-rule.py`。

## 改这条腿之前

先过闸，**默认 0 网络 0 花费**（假传输层跑真代码，核端点/节点/Bearer/落盘/失败不留假成功）：

```
python C:\dsh\scripts\check-dewatermark-lane.py     # 47 条
```

四张表（`CAPABILITIES` / `ROUTES` / `DEFAULT_PURPOSE` / `PRICE_ROUTES`+`PRICE_BY_CAP` / `_ADAPTED`）改任何一张都要重跑它。节点号只住 `D:\video-skill\config.py`（`.env` 可覆盖），**代码里不留字面量兜底**。
