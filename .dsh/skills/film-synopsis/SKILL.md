---
name: film-synopsis
description: Create or revise only an AI film synopsis and concept anchor. Use only when the project router selects stage 01-synopsis.
---

# 故事概念阶段

读取 `references/source.md` 作为方法来源。

从零进入本阶段时，第一条创作回复只能逐字显示 `references/source.md` 第一部分的洋子项目定位 A/B/C/D 菜单，然后停止等待。不得改写成风格、来源、故事规模、主题等另一套问卷，不得直接生成概念或大纲。用户选择 B、C 或 D 后，必须继续执行来源文件中的对应子菜单并再次停止等待；定位和必要子选项确认前不得创作。

输入限于：`00_project/brief.md`、用户明确提出的故事要求，以及 `00_project/reference-memory.md` 中已经由用户确认、状态为“启用”、使用范围包含“故事创作（01-05）”的可选参考方法。

第 10 个本地知识桥默认未调用。若没有已确认参考，照常创作，不得显示知识库菜单、扫描目录或写入参考记忆。若存在已确认参考，只使用其中的抽象方法与来源提示，禁止带入参考作品的人名、剧情链、对白或独创设定；读取已有参考不等于重新检索。

输出仅限：`01_story/synopsis-vNNN.md`。每次完整修改先扫描已有文件取得下一个三位版本号，保存为草稿并登记；用户确认后由总控登记为最新确认概念锚定。

禁止：生成角色圣经、世界观、分场、剧本、图像提示词、表演或视频提示词；禁止假设下游资产。

完成后在文件顶部写明“待人工确认”，并停止。

