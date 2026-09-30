# BRIEF — 富铁软糖 20s 带货视频

- account: shop-demo
- task: 2026-09-26-铁软糖
- 产品: 富铁软糖（Iron-Rich Gummy），NGLED®，枣香浓郁 酸甜爽口，富铁含量 81.6mg/100g，净含量 60g
- 素材（产品图，不进 omni API，仅供 Agent 描述）:
  - 图1 红色小熊形软糖特写（glossy、湿亮、鲜艳红）
  - 图2 单片独立包装（白色锯齿口小袋，红色软糖隐约透出）
  - 图3 整瓶（深琥珀色玻璃瓶 + 红色螺旋盖 + 红色标签「富铁软糖 NGLED」）
- 客户请求: 20s 带货视频，9:16，明亮
- 规格: 总 20s = 2 × 10s（omni_flash-10s 单段上限），aspect_ratio=9:16，调性 bright high-key e-commerce
- 关键镜头:
  - 段1: 整瓶 dolly-in → 开红盖 → 手取出一颗红色小熊软糖举向镜头
  - 段2: 软糖特写慢转 → 拉远带出单片独立包装
- 禁忌: no text overlay, no logo burn-in, no subtitle（包装印刷保留）
- 口播: 本阶段不配（客户未要求），纯画面
- 拼接: ffmpeg -f concat
