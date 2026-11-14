---
name: film-lira-assets
description: Generate production-ready image prompts and asset briefs from a final screenplay or user-supplied character, location, and prop materials when the router selects stage 06-assets.
---

# 图像资产阶段

读取 `references/source.md` 作为图像提示词方法来源。用户讨论资产修改、要求优化现有资产或从完整作者剧本直接进入 Lira 时，再读取 `references/asset-change-control.md`。

输入可以是 `00_project/artifact-registry.md` 登记的最新已确认剧本，也可以是用户本轮明确指定的最终剧本、角色卡、场景资料、道具资料或参考图。允许从 Lira 直接开始，不要求前序阶段全部存在。只有 `00_project/reference-memory.md` 中已经确认、状态为“启用”、使用范围为“图像资产（06）”或“全流程（01-08）”的方法可以作为可选补充；没有适用参考时不得主动检索。

如果用户只有一份标准作者剧本，没有独立角色状态表、场次制片表和资产需求表，不得直接凭整部剧本猜一套粗资产。先按 `ai-film-router/references/screenplay-intake.md` 生成接管检查报告并停止；用户确认“保持作者剧本不变，只补生产资料”后，先生成 `02_script/production-breakdown-vNNN.md`，再开始资产输出。

全流程项目从 05 进入 Lira 时，如果最终剧本已经确认但 `02_script/production-breakdown-vNNN.md` 不存在，也必须先从最终剧本和 01-04 已确认文件生成该生产拆解。生产拆解独立保存，不把专业剧本正文改写成提示词格式。

标准资产输出为 `03_assets/assets-vNNN.md`。文件内按“角色 / 场景 / 道具”分组，并列出每条资产的稳定 ID、状态 ID、来源场次、参考依据与中文生成提示词。同一人物的不同年代、服装、伤势或变身必须归在同一角色 ID 下，不能拆成多个角色。修改单个资产时，在成果登记中写明受影响的资产 ID 与场次；只有真实引用该 ID 的 ACTING、镜头、关键帧和提示词进入过期状态，其他资产与场次继续有效。

禁止：未经用户授权改写剧本、重设角色弧光、擅自决定整体美术方向、输出表演段落或完整视频提示词。发现剧本问题时停止并建议总控切回剧本阶段，不在资产文件中偷偷修剧本。

用户说“你觉得怎么改、帮我分析、给修改建议”时，只进入咨询模式：查看实际资产后给 2 至 3 个候选方向，不修改文件。只有用户明确选择方向并确认资产修改单后，才生成新版本；旧资产不得覆盖。

完成后标记资产版本和“待人工确认”，更新 `00_project/artifact-registry.md` 的当前草稿并停止；用户确认后才登记为最新确认资产版本。
