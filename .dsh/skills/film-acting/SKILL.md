---
name: film-acting
description: Write only the acting and performance block for a user-approved scene or shot from project files or uploaded materials when the router selects stage 07-acting.
---

# 表演阶段

读取 `references/source.md` 作为方法来源。

输入可以是项目角色、剧本和资产文件，也可以是用户本轮明确指定的场景、镜头、角色卡、资产参考或表演目标。允许直接进入 ACTING，不要求全流程前序齐全。只有 `00_project/reference-memory.md` 中已经确认、状态为“启用”、使用范围为“表演（07）”或“全流程（01-08）”的方法可以作为可选补充；没有适用参考时不得主动检索。

输出仅限：`04_performance/<场次ID>-acting-vNNN.md`。文件内只处理当前场次，输出表演目标、阻力、策略、节拍变化、潜台词和可执行行为。修改一场表演时只标记引用该场次与版本的镜头和提示词过期，不连带重做其他场次。

禁止：输出摄影机、光学镜头、完整 Seedance 提示词、图像提示词；不得未经用户授权重写剧本和角色圣经。发现上游问题时交回总控切换阶段。

完成后标记版本和“待人工确认”，更新 `00_project/artifact-registry.md` 的当前草稿并停止；用户确认后才登记为最新确认 ACTING 版本。
