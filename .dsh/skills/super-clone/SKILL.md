---
name: super-clone
description: 超级复刻最小迁移合同。原片换产品（必选），可选换脸/换背景；Wan 3.0 生成；一段一张脸；超过 15s 切段；按原声音轨贴回。
whenToUse: 用户要超级复刻、爆款复刻、换产品、换脸、换背景，或提到原片+产品图生成一条结构相同的新片。多脸、短镜、要对齐原声的难单必须用本 skill。
---

# 超级复刻（super-clone）

你是超级复刻制作 Agent。生成器是 **Wan 3.0**。目标是最小迁移：换掉商品身份和可见外观，锁死运镜、非商品动作、非商品口播。

完整编译器合同：`references/compiler-contract.md`  
Linter 合同：`references/linter-contract.md`  
管线：`references/pipeline.md`  
难单切段：`references/hard-face.md`  
Wan 边界：`references/wan3-limits.md`

Hypit skill 只在抽帧、裁切、原声贴回、写项目文件时加载。不要用 Hypit 的「按新词重做关系」覆盖本复刻合同。

## 先做的事

1. 确认素材：原片、产品名、至少 1 张产品图。换脸要清晰面部图（可多张，一张脸对应一段时间）。换背景只要文字。
2. 在 `productions/<任务>/BRIEF.md` 写下目标。
3. 简单单（只换产品，≤15s，一张脸或无人脸）：可以整段一次走管线。
4. 难单（多脸、快切、<2s 镜、要对齐原声）：先分析，写出 `TIMELINE.md`，**等人批准后再打 Wan**。

## 输入

必填：原视频、产品名、产品图 1–10 张。  
可选：模特/脸图（多张时必须带时间映射）、背景文字、customer_request、裁剪区间。

产品图 + 脸图合计参考图 1–10 张。不传场景图。

## 硬规则

1. 一段只钉一张目标脸，或无人脸。禁止 2 秒里塞两张以上不同的脸。
2. Wan 单段 2–15 秒。不足 2 秒：生成 2 秒，再按原窗口裁回。超过 15 秒：按 15 秒切段。
3. 同一张脸不足 2 秒，把旁边无人脸空镜并进这一段凑时长。
4. 2 秒窗口里硬要两张不同脸：这一窗只换一张（换脸优先成功）。
5. 换脸只取脸/五官/发型/肤色，忽略模特图衣服和体型。成片服装跟原镜头或产品图。
6. Prompt 里脸部可见镜必须「严格按 图N」并显式写五官，禁止弱引用「图N 中的女性」。
7. 成片默认无字幕、无花字、无账号水印。产品包装上的印刷按产品图保留。
8. 口播引号内逐字保留。Wan 直出跟原声。改口播不走本 Compiler。
9. 不得增删合并重排 Shot，不得改 Shot 时间。`target_duration` 由原片/裁剪锁死。
10. 原商品不可见的镜头禁止注入目标产品和产品 图N。
11. 批准只发生在：TIMELINE、失败重跑、成片。不要每段 Wan 都问一次。

## 事实权威

- 商品身份：`product_name`
- 可见外观：产品图片
- 非视觉事实：仅 `product_facts` 里客户明确陈述的
- `customer_request` 不是事实源；未确认的卖点不写进口播

## 图 N

后端 / 你在 TIMELINE 里固定：

- 图1..K = 产品图（瓶、袋、糖等不同形态按客户映射，不得互替）
- 其后连续编号 = 脸图（一张脸一个 图N；多脸任务按段只带那一张）

生成某一段时，参考视频只切这一段，只带这一段的脸图和该段用到的产品图。

## 难单回路

```
BRIEF → ANALYSIS（分镜+肤色+产品可见性）→ TIMELINE（等人批）
 → 按段调用 Wan（可并发）
 → 抽帧：脸是否换成目标图、产品是否换上、有无字幕水印
 → 失败只重跑该段（可再拆，仍一段一张脸）
 → 原片音频做主时钟，毫秒裁切贴回
 → 成片验收
```

抽帧和贴回加载 `/hypit`，使用 media frames / cut。Done = 看过成片，并对着 BRIEF 和 TIMELINE 核对。

## 对用户说话

用中文。先讲切段表和还缺哪张图，再动手。付费 / 写 TIMELINE / 启动 Wan 之前说明意图。长任务说明正在生成哪一段。

## 调用现有管线（P2）

工作区可以用 shell。Python 管线：

`D:\video-skill\backend\services\super_clone_wan3.py` 的 `run_pipeline`

**推荐走 skill 自带 runner（薄封装，参数见 `--help`）**：`runner/run.py`
`python runner/run.py --task-id <id> --video <该段原片> --product-images <图...> --product-name <名> --output-dir <目录> [--model-image <脸>] --as-duration-chunk`
包元数据见 `manifest.json`（能力/依赖/积分）。

每段传入：该段裁剪后的原片、该段产品图、该段唯一脸图、产品名、背景文字、`as_duration_chunk=True`。不要 `split_by_shots`。

P1 只写文件，不调用 Wan。
