# 视频模板（满意 run → 沉淀可复用配方）

**目的**：一条 omni run 出片、人验收满意后，把它的"拍法/节奏/调性"蒸馏成一个**结构化片型模板**，下次换产品一键复刻同款骨架，只喂新料（产品名/卖点/图）。

## 铁律：存"配方"、挖"料"
模板只存**跨产品不变**的东西；**产品专属事实一律挖成占位符**（换品时重填，且是合规雷区——品牌/价格/功效绝不能焊死进模板）。

| 进模板（配方） | 挖成占位符（料，换品重填） |
|---|---|
| mode / duration / segments / 每段拍数 | `{PRODUCT}` 产品名 |
| 叙事结构(structure) / 平台 / 国家 / 口播(语言·性别·开关) | `{SELLING_POINTS}` 卖点 |
| 生图后端 + 模型 + 拼接策略 | `{MAIN_IMAGE}` / `{HOST_IMAGE}` 图 |
| 每拍 spine：beat_function·product_presence·shot_motivation·emotion·时长 | 具体台词里的品牌/价格/功效 |
| keyframe_prompt 的**句式**（产品名词替换成 `{PRODUCT}`） | |
| 情绪弧线 / 口播节奏 / 踩坑清单 | |

## 存哪（账号隔离）
```
C:\dsh\accounts\<本会话绑定账号>\video_templates\<片型名>.json
```
- **挂账号下**（AGENTS.md 账号记忆隔离）：A 号的片型 B 号读不到、也不许写进别人目录。
- 一个片型 = 一个 json。**流程模板 + 通用踩坑**（跨任务、非账号数据）另进 skill playbook，不塞这里。
- 一个片型可同时存一份 `.md` 人话说明书（同目录同名），但 **`.json` 才是能跑的配方**——只有 md = 退化成笔记、享受不到一键套用。

## template.json schema
```json
{
  "name": "20s-对比反转-主播带货",
  "mode": "product", "duration": 20, "segments": 2, "beats_per_segment": 6,
  "structure": "对比反转", "platform": "抖音", "country": "中国",
  "voiceover": {"enabled": true, "language": "Chinese Mandarin", "gender": "female"},
  "backend": {"image": "zexapi", "model": "nano_banana_2", "splice": "硬切"},
  "spine": [
    {"seg": 1, "beat": 1, "beat_function": "hook", "product_presence": "not_present",
     "shot_motivation": "先给疲惫的脸，产品不出现", "emotion": "疲惫", "duration": 1.6,
     "keyframe_prompt_tmpl": "close-up of a tired woman, dull skin, {PRODUCT} not yet in frame, frontal"},
    {"seg": 2, "beat": 12, "beat_function": "cta", "product_presence": "focus",
     "shot_motivation": "整瓶促单收尾", "emotion": "促单", "duration": 1.8,
     "keyframe_prompt_tmpl": "woman holds {PRODUCT} frontal, face the camera, NO overlay text, package print matches reference"}
  ],
  "emotion_arc": "疲惫→好奇→惊喜→焕新→促单",
  "pacing": "约75字 / 3.5字每秒",
  "dialogue_tmpl": [
    {"seg": 1, "beat": 1, "text_tmpl": "最近熬夜多，气色看着特别累"}
  ],
  "pitfalls": [
    "CTA 拍默认带花字/箭头 → 去叠加层（违反成片无花字铁律）",
    "focus 拍产品印刷字会糊 → 锁印刷字与 product_card 一致",
    "脸太侧是高频返工点 → prompt 明确 frontal / face the camera",
    "首次建 run 别用 story 模式（narrative 结构校验会崩），带货走 product"
  ],
  "placeholders": ["{PRODUCT}", "{SELLING_POINTS}", "{MAIN_IMAGE}", "{HOST_IMAGE}"],
  "source_run": "20260930-…-富铁软糖-20s-…",
  "created_at": "2026-09-30 23:30:00", "account": "<acct>"
}
```
`spine` 必须**每拍一条**（段数×6），`keyframe_prompt_tmpl` 里产品名词用 `{PRODUCT}` 占位、其余句式照抄跑通版本；`dialogue_tmpl` 存口播句式骨架（具体卖点/品牌留 `{...}` 或换品时重写并过 §5.5 合规 + §5.6 质量红线）。

## 触发 ①：成片批准后自动追问（主用）
Stage6 成片**人验收通过后**，agent 固定追加一句：
> "这条满意的话，要不要存成视频模板？给个片型名（如 `20s-对比反转-主播带货`）。"

人回"存，叫 X" → 走下面的【存流程】。**不新增 UI 按钮、不碰 DSH composer、零跨源问题。** 人也可随时说"这条存成模板 X"手动触发。

## 存流程（agent 执行）
1. 读当前 run 目录：`script.json`（segments/beats）、`state.camera`、`config`、`product_card`、`brief`。
2. 按"配方/料"切法抽 `spine`：逐拍取 beat_function·product_presence·shot_motivation·emotion·duration；每条 `keyframe_prompt` 把产品名词/部件名替换成 `{PRODUCT}`（对照 `product_card` 的名词挖）。
3. 口播/台词抽成 `dialogue_tmpl`，具体卖点/品牌留占位。
4. 记本次踩过的坑进 `pitfalls`（可复用的那些）。
5. 写 `accounts/<acct>/video_templates/<名>.json`（+ 可选同名 .md 说明书）。
6. 回用户："模板已存：accounts/<acct>/video_templates/<名>.json，下次说『用『<名>』模板，产品是…』即可。"

## 触发 ②：用模板建 run
用户说"用『X』模板，产品是 Y、卖点 Z"（附新图）→ agent：
1. 读 `accounts/<acct>/video_templates/X.json`。
2. 拿 duration/segments/mode/backend/voiceover → `POST /api/run`（product 模式，用模板参数）+ `POST /country`（模板国家）+ 必要时 run 级 voiceover 覆写。
3. 喂新料：`POST /product`(主图) + `/assets`(细节/定妆图)。
4. 用 `spine` 把 `{PRODUCT}/{SELLING_POINTS}` 等填上新品 → 拼出完整 `script.json` → `POST /script`（会过 Stage1 校验）。
5. **正常走 3 关口**（方案/审帧/成片）——模板只是省了从零写剧本，不跳任何闸、不省任何审核（合规/质量红线照过）。

## 新任务立项时（可选主动）
每次起新 omni 任务，agent 可先列"你账号下有这些模板：…，要不要套一个？"——但**不强制**，用户没提就按常规立项三问走。

## 边界
- 模板**只省"从零搭骨架"**，不省生成、不省额度、不省 3 关口审核。
- 换品后 `keyframe_prompt_tmpl` 填出来的新 prompt **必须重过 §5.5 合规 + §5.6 质量红线**（占位符替换可能引入新品牌词/功效词）。
- 模板文件是**账号资产**，随 `accounts/<acct>/` 走记忆隔离；跨账号共享=另设"公共片型库"，本阶段不做。
