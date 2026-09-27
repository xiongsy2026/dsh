# PRD：超级复刻 Agent（DSH Desktop）

状态：立项草稿  
日期：2026-09-25  
工作区：`C:\dsh`  
运行时：DSH Desktop v0.9.2（基于 DeepSeek Harness）  
生成器：Wan 3.0（沿用 `D:\video-skill` 超级复刻管线）

---

## 1. 一句话

用 DSH Desktop 做 Agent 框架，把超级复刻合同和 Hypit 时间轴能力做成两个 skill，专门吃「多脸、短镜、要对齐原声」这类难单；日常只换产品仍走原 8090 页面。

## 2. 背景

`D:\video-skill` 超级复刻已经能跑通「原片 + 产品图 → MiMo 解析 → 迁移编译 → Wan3 直出」。默认只换产品；换脸 / 换背景 / 改口播可选。

难单会翻车。最近一次护肤软糖片：15.8 秒、20 个快切、脸按肤色 A→B→C（后加 D）、再换瓶/袋/糖。整段一次送 Wan，原脸被参考片锁死；后来 7 段 / 3 段 / 4 段方案在跑的时候改；脸 C 整段仍是原脸；军训第二镜脸 B 没换上；水印还在；裁切对不齐原声。

缺的不是再换一个生成模型，是 **Agent 回路**：先审切段表、并发生成、逐段抽帧、失败只重跑、按原声音轨贴回。

现有 `/hypit` 内置 Agent 已经是 skill + 工具批准，但工具箱只有 Hypit CLI，没有超级复刻步骤，导演 skill 还会把复刻拉成再创作。

## 3. 目标

1. 在 `C:\dsh` 单独立项，DSH Desktop 打开本工作区即可跑复刻 Agent。
2. 完整搬入两个 skill：`super-clone`（主合同）和 `hypit`（时间轴 / 抽帧 / 项目文件）。
3. 难单标准路径：分析 → 人审 TIMELINE → 并发 Wan → 抽帧验收 → 原声贴回。
4. 生成器锁 Wan 3.0。第一期用 shell 调 `D:\video-skill` 现有 Python，不重写管线。

## 4. 非目标（本期不做）

- 替换 8090 超级复刻客户表单
- 换 Agnes / 其他视频模型
- 把 Hypit 导演流程当成复刻主路径
- 每个 Wan 调用都弹一次批准
- 1 秒一条生成
- 一段里钉两张以上不同的脸

## 5. 用户与场景

| 角色 | 用法 |
| --- | --- |
| 制作同学 | DSH Desktop 开 `C:\dsh`，丢原片和产品图/脸图，走 Agent |
| 客户（简单单） | 继续 8090「只换产品」 |
| 制作同学（难单） | 多脸、短镜、要贴原声：必须走本 Agent |

首发验收片：富铁软糖 15.8s，脸 A/B/C/D + 三张产品图。切段以人审后的 TIMELINE 为准，参考方案：

| 段 | 原片时间 | 脸 | 说明 |
| --- | --- | --- | --- |
| 1 | 0.00–1.50 | 脸 A | 不足 2s，Wan 出 2s 再裁回 |
| 2 | 1.50–5.00 | 脸 B | |
| 3 | 5.00–10.00 | 脸 C | 产品特写并进同脸段 |
| 4 | 10.00–15.00 | 脸 D | 无人脸镜并进 |

## 6. 产品形态

```
DSH Desktop（Agent 壳：会话、skill、shell、批准、轨迹）
    ├── skill super-clone     最小迁移合同 + 切段规则 + Wan 调用约定
    ├── skill hypit           抽帧 / 裁切 / 原声贴回 / 项目文件
    ├── 工作区 C:\dsh         BRIEF / ANALYSIS / TIMELINE / PROGRESS
    └── shell → D:\video-skill  super_clone_wan3.run_pipeline（Wan 3.0）
```

Skill 是说明书，不是运行时代码。Wan 工具第一期走 shell；超时长命令走 DSH 后台 job。

## 7. Skill 合同摘要

### 7.1 super-clone（主）

完整合同见 `.dsh/skills/super-clone/references/`，从 `super_clone_wan3.py` 搬出。

硬规则：

- 最小迁移：换产品身份与可见外观；运镜、非商品动作、非商品口播锁死
- 成片默认无字幕、无花字、无账号水印；产品包装印刷按产品图保留
- 换脸 = 只换脸/五官/发型/肤色，忽略模特图衣服；服装跟原镜头或产品图
- 换背景 = 只接受文字，不传场景图
- Wan 单段 2–15 秒；>15s 按 15s 切；不足 2s 生成 2s 再裁回原窗口
- **一段只钉一张目标脸**（或无人脸）
- 2 秒窗口里出现两张以上不同脸：这一窗只换一张，换脸优先成功
- 口播：Wan 直出跟原声；引号内口播逐字保留（改口播是另一条链路）
- 图 N 由 media_manifest 固定；产品 1..K，模特紧随其后

管线步骤（与现系统对齐）：

0. 校验 + 可选裁剪 + 锁 `target_duration` + 固定图 N  
1. MiMo 解析 storyboard  
1.5 迁移编译器 → Wan prompt  
2. Layer 2 Linter（只修客观错误，宁漏勿改）  
3. Wan 3.0 执行  
4. 难单：按 TIMELINE 多段并发，再按原声音轨贴回

### 7.2 hypit（辅）

从 `D:\video-skill\.grok\skills\hypit` 完整搬运。本项目里只用于：

- 读参考片、抽帧、tiles
- 按源时间 `cut`
- 把各段裁到原窗口，贴回原片音频主时钟
- 把理解写进 ANALYSIS / TIMELINE

禁止用 Hypit 的「按新词新意图重做关系」覆盖超级复刻。

## 8. Agent 回路（难单）

```
收素材（原片、产品图 1-10、可选多张脸、可选背景文字、产品名）
  → /super-clone
  → 写 BRIEF.md
  → Step1 分析（MiMo 或抽帧），写 ANALYSIS.md
  → 按「同一张脸」提案切段，写 TIMELINE.md     ← 批准点 1
  → 人批之后并发 Wan（一段一张脸 + 该段产品图）
  → 每段抽帧 vs 原片 / 脸图                     ← 失败则批准点 2（只重跑该段）
  → 以原片音频为时钟，毫秒裁切贴回
  → 成片审水印、脸、产品、时长                   ← 批准点 3
  → 写 PROGRESS.md
```

简单单（只换产品、一张脸或无人脸、时长 ≤15s）：可一次过，不强制切段。

## 9. 工作区文件

`productions/<task-id>/`

| 文件 | 内容 |
| --- | --- |
| BRIEF.md | 产品名、要换什么、脸图清单、是否换背景、时长画幅 |
| ANALYSIS.md | 分镜、肤色阶梯、产品可见性、水印 |
| TIMELINE.md | 段起止、脸、产品图、Wan 时长、裁回时长 |
| PROGRESS.md | 各段状态：待批 / 生成中 / 抽帧过 / 失败原因 |

## 10. 成功标准

首发以富铁软糖难单为准：

- TIMELINE 先审后打，中途不再改切法
- 脸 A/B/C/D 在对应段抽帧可辨，不是原片那张脸
- 产品在可见镜换成富铁软糖瓶/袋/糖
- 成片无账号水印、无烧录字幕
- 成片时长等于原窗口（约 15s），口播对齐原声
- 脸 C 失败时只重跑第 3 段，A/B/D 留下

## 11. 分期

### P0（本立项已交付文档与 skill）

- 安装 DSH Desktop
- 建立 `C:\dsh` 工作区
- 搬入 hypit skill
- 写出 super-clone skill + 编译器 / Linter 合同
- 本 PRD

### P1

- DSH Desktop 打开 `C:\dsh`，`/super-clone` 能列出合同
- 对富铁软糖只跑到「写出 TIMELINE，等人批」，不打 Wan

### P2

- shell 调 `run_pipeline` 按段生成
- hypit 抽帧验收 + 原声贴回
- 失败段重跑

### P3

- 可选：把 analyze / generate / inspect / conform 收成 DSH plugin
- 8090 难单入口跳到本工作区（客户页仍简单单）

## 12. 风险

| 风险 | 处理 |
| --- | --- |
| Wan 仍绑不住脸 C | Agent 拆段、减弱原片参考、重试；不保证一次过 |
| 2s 生成裁回 1.5s 接口跳 | 原声对齐；接受小幅画面跳 |
| 两个 skill 抢语义 | AGENTS.md 路由：复刻先 super-clone |
| DSH 仍是开发者预览 | 合同放 skill 和 Python，少绑 DSH 内部 API |
| 沙箱拦 Wan 出网 | 工作区 `workspace-write` + 生成调用允许网络 |
| 长任务超时 | 依赖 DSH bash 转后台 job |

## 13. 打开清单

1. 启动 `DSH Desktop.exe`
2. 工作区选择 `C:\dsh`
3. 配模型 API（与现网 GPT / Grok 相同即可）
4. 新会话：`/super-clone`
5. 把原片放到 `references/`，图放到 `assets/`
