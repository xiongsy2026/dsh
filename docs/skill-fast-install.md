# Skill 快速创建安装方案（SOP）

状态：草案待拍板｜日期：2026-10-05｜适用：往 `C:\dsh` 新增一条 skill 的标准流程
> 一份流程四步走：**理解创建方法 → 拟名 → 接模型网关 → 装配（含首页滚动卡）**。
> 每装一条 skill 就照 §7 验收清单过一遍；有不清楚的点**集中在 §8 一次问完**，不边做边问。

---

## 1. 输入：你发来 skill 的创建方法

你发来的是"这条 skill 怎么做"（能力描述 / 工作流 / 调哪些脚本）。我的动作：

1. **理解后回一份「理解确认」**，只写五项：
   - **能力**：这条 skill 做什么、产物是什么（文案？图片？视频？文件落哪）
   - **输入**：客户要给什么（产品图 / 原片 / 卖点…），缺了我追问什么
   - **边界**：明确不做什么（相邻 skill 的分界，如"不做复刻、不读原片时间轴"）
   - **模型能力**：需要 text / image / video / video.analyze / 其它（audio、lipsync、video.edit…）各几次
   - **引擎或纯提示词**：带引擎（要登记意图、挂右栏）还是纯写作类（不登记、不挂台）
2. 不清楚的点**一次列成清单问完**（见 §8），问完即开工，不再中途反问。
3. 名字（§2）随理解确认一起给你过目，点头即定。

## 2. 拟名（一条 skill 三个名字，一次定齐）

| 名 | 规则 | 用在哪 |
|---|---|---|
| **id** | 英文 kebab-case，能力命名（`video-dewatermark`、`short-drama`），不用品牌词堆砌 | 目录名 `.dsh/skills/<id>/`、`/<id>` 调用、路由表 |
| **中文名** | ≤8 字，说清能力（"超级复刻""视频去水印"） | 首页 dock 卡片 `title` |
| **tagline** | ≤20 字，一句话卖点 | dock 卡片第二行 |

⚠️ 三个名字全局唯一：先查 `.dsh/skills/` 现有 35 条和 `SKILL_DOCK_META`，撞名就换。
id 一旦上架**不再改名**（客户 `/` 命令、SKILL_LOG、封面文件名都挂在上面）。

## 3. 接模型网关（唯一轨，不直连厂商）

唯一入口（凭证/选模型/计价全在网关，skill 只给"能力 + purpose + 参数"）：

```
python backend/gateway.py call <capability> [purpose] --account <LOOP.member> --json '<params>'
```

### 3.1 默认唯一档（新 skill 默认就用这两条，不给客户/不给 skill 作者选模型）

| 需要什么 | 能力 / purpose | 落到的模型 |
|---|---|---|
| **生图** | `image.generate` / `default` | agnes-image-2.5-flash |
| **生视频** | `video.generate` / `omni-t2v` | omni_flash-10s（10s 一段，长片自己切段拼接） |
| 文本（写文案/剧本/提示词） | `text.complete` / `agnes` | agnes-3.0-flash |
| 看图 / 看视频（语义质检） | `video.analyze` / `default` | agnes 多模态 |

**"唯一"就是规则**：一条 skill 里同一能力只走上面一条 route，不并存多家模型做 A/B。

### 3.2 什么情况必须先问（不许自选）

- 要用默认档以外的 route：h3 / seedance / clone-r2v（换产品换脸）/ motion（动作迁移）
- 要 `video.edit`（去水印、高清修复）、`image.edit`（换脸）、`lipsync`、`audio.generate`
- 任何按条计费能力的**批量**（段数 >3 或单次预估 >50 分）

### 3.3 网关铁律（继承 `AGENTS.md`，违者事故）

- 🔴 **花钱前报价并等同意 —— 2026-10-06 起这条有闸了，不是只靠自觉**：`gateway.py invoke()` 在**预扣之前**查"人批准条子"，
  要批的清单 = `video.generate` / `video.edit`(超分·去水印) / `image.generate` / `image.edit`；没条子 ⇒ `ok:false, approval_required`，
  **0 分不扣、任务不发起**。文本(`text.complete`)与看图(`video.analyze`)免批。
  - 拿到同意后由**人**签：`gateway.py approve <cap> [purpose] --count N --credits X --scope "<方案一句话>"`；`approvals` / `revoke <id>` 查撤。
  - 写 skill 时**不要**为了绕过闸自己 approve —— 那等于替用户点头；正确做法仍是先报价、等他回话。
  - 回归：`python scripts/check-spend-approval.py`。
- 🔴 **产物没落盘不算交付**：视频 = 目标 mp4 在盘 + 直链 + 抽帧贴图；图片 = 落盘 + 人眼看过。
- 失败按死因分类处理（抖动退避重试同 job_id、被拒不换参死磕）；同一原因连挂 3 次才停，停也要带证据。

## 4. 创建 SKILL.md（落在 `.dsh/skills/<id>/SKILL.md`）

frontmatter 契约（路由准不准的地基，`docs/PRD-skill-routing.md` §4）：

```markdown
---
name: <id>
description: >
  一句话"做什么"。
whenToUse: >
  触发场景 + 3-5 条用户原话例句（路由匹配语料）。
whenNot: >
  明确不接什么（相邻 skill 边界，防误路由）。
inputs: 客户要给什么
examples: 正例 / 反例
version: 0.1.0
---

# <中文名>
（正文：先决条件、步骤、调网关的命令模板、关口、失败处理）
```

硬规则：

1. 带引擎的 skill，正文第一步 = `python scripts/intent.py <skill> [任务名]`（登记意图才有右栏台）；纯提示词/知识库类**不登记**。
2. 命令契约写进正文（跨会话读文件即恢复，不靠 agent 记忆）。
3. 调模型一律 §3 网关命令模板，**禁止**写厂商 endpoint / key / 凭据。
4. 加 skill 时在 `docs/routing-eval.md` 补 ≥2 条该 skill 的测试说法（路由回归）。

## 5. 首页滚动图片卡（输入框下的 skill dock）

首页对话框底下已有**横滚技能卡**（`conversation.composer.dock` 槽，`plugins/super-clone-ui/lib/client.js`）。新 skill 上架**必须**登记进去，否则客户看不见：

1. **`SKILL_DOCK_META` 加一行**（唯一元数据源；顺序用 `order` 排）：

```js
"<id>": { title: "<中文名>", tagline: "<tagline>", order: 6,
          cover: "http://127.0.0.1:8100/api/film/file?task=_skill-covers&rel=cover-<id>-v4.png" },
```

2. **封面**（`productions/_skill-covers/cover-<id>-v4.png`）：
   - 规格 `472×264`（236×132 @2x）16:9，深炭底、橙只做一条光带、底部留 25% 叠字区。
   - 🔴 **逐张人眼确认才能上线**（`PRD-skill-showcase.md` §8.5 铁律）：出 2-3 版候选 → 你选定签字 → 才挂。提示词里不放任何字面量；出图后人眼过"有没有字/商标"。
   - 没确认封面就**不写 cover 字段**，卡片走"首字母 + 橙渐变"兜底（宁可素，不上未审图）。
   - 出图管线：`scripts/make-skill-covers-*.py`（同一视觉系统），**出图花钱先报价**（4 分/张量级）。
3. **同步闸**：`scripts/check-skill-dock.py` 的 `APPROVED` 数组加 `<id>`（白名单=元数据表键，漏改就 FAIL），然后跑：

```
python scripts/check-skill-dock.py     # 60 条：白名单、元数据齐、封面 200 且 >50KB、样式、点击行为
```

4. 改完 client.js **重启 DSH** 才生效（选择器是启动快照）。

## 6. 装配（框架四处登记，`AGENT-FRAMEWORK.md` §5.1）

| # | 动作 | 落点 |
|---|---|---|
| 1 | 建目录 + SKILL.md | `.dsh/skills/<id>/SKILL.md`（§4） |
| 2 | 路由表加一行 | `.dsh/skills/weeksir/SKILL.md` 的路由表 |
| 3 | AGENTS.md 加一句 | `C:\dsh\AGENTS.md`（若带引擎：写明 intent 登记档位 `omni/hypit/super-clone/product-video/film`） |
| 4 | dock 登记 | §5 |
| 5 | 路由评测语料 | `docs/routing-eval.md` 补 ≥2 条 |
| 6 | 新会话验证 | 新开 DSH 会话敲 `/<id>`，首页出卡、点击填输入框不自动发 |

> 只有**发客户包**才需要再跑 `weeksir-skills/sync_weeksir_skills.py` 提升到打包层；本地开发改 `.dsh/skills/` 即生效。

## 7. 验收清单（每条 skill 逐项打勾）

- [ ] 理解确认五项已过目（能力/输入/边界/模型能力/引擎与否）
- [ ] 三名已定且全局唯一（id / 中文名 / tagline）
- [ ] SKILL.md 齐：frontmatter 契约全 + 正文命令模板 + 第一步 intent（引擎类）
- [ ] 调模型只走 `gateway.py call`，默认唯一档（图=default、视频=omni-t2v）；例外 route 已问过用户
- [ ] 花钱已报价并获同意（路由/单价/次数/总扣分/余额齐全）
- [ ] 产物落盘可出示（路径 + 直链/贴图），不是"已提交"
- [ ] dock：`SKILL_DOCK_META` 有行 + 封面已人眼确认（或故意兜底）+ `check-skill-dock.py` 全绿
- [ ] weeksir 路由表、AGENTS.md、routing-eval 已登记
- [ ] 新会话验证 `/<id>` 可用、首页卡可点

## 8. 不清楚就问（一次问完，模板）

开工前把待定项集中问，格式固定：

```
这条 skill 待你拍板：
1. <能力边界问题，给 2-3 个选项>
2. <模型 route 问题：默认档 image=default / video=omni-t2v 是否OK，还是必须用 <例外 route>>
3. <封面：先出图（约 N 分，需报价）还是先首字母兜底>
```

## 9. 已定的默认（本方案自带，不用再问）

- 图片唯一档 = `image.generate`/`default`（agnes-image-2.5-flash）；视频唯一档 = `video.generate`/`omni-t2v`（omni_flash-10s）。
- 文本 = `text.complete`/`agnes`；看图 = `video.analyze`/`default`。
- 封面尺寸 472×264、深炭底 + 橙点缀、逐张确认。
- dock 一行 4 张（卡宽 228），order 从 6 起接在现有 5 条之后。
- 安装只落 `.dsh/skills/`（工作区层），不碰打包层、不改 DSH 内核、不建新插件。
