# 客服开源怎么整合进 DSH

不把整套开源客服软件嵌进 Harness。拆成：

1. **通道**（`cs-inbox`）：怎么从抖音店铺拿到消息、怎么发出去。
2. **大脑**（`cs-reply`）：怎么写成店员口吻、何时转人。对话模型仍是 Agnes 3.0 Flash。

## 建议收的仓库（原料）

| 仓库 | 收什么 |
| --- | --- |
| [ChatGPT-On-CS](https://github.com/cs-lazy-tools/ChatGPT-On-CS) | 抖店/飞鸽怎么收发（RPA/插件）。不收它的 SaaS 和自带模型循环。 |
| [RTY798/ai-customer-service-agent](https://github.com/RTY798/ai-customer-service-agent) | 意图分流、知识检索、升级工单的**流程**。不部署 LangGraph。 |
| [whichmen/dxl-commerce-agent](https://github.com/whichmen/dxl-commerce-agent) | 抖音飞鸽 Playwright worker 的页面路径参考。不引入 OpenClaw。 |

口吻和禁词只来自本店 `accounts/<id>/PROFILE.md`，不用来源仓库的默认人设。

## 本阶段

- skill 合同已在 `.dsh/skills/cs-reply`、`cs-inbox`。
- 通道实现：有官方接口再写 runner；没有再 CU。
- 不把上述仓库 `dsh plugin add` 进 web profile。
