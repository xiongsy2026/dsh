# Agent 工作约定

本工作区给 DSH Desktop 用。先走电商闭环路由，再加载单环 skill。框架见 `docs/AGENT-FRAMEWORK.md`。

## 路由

新会话先 `/weeksir`。没有绑号时先 `/session-bind`。目录里没有的 skill 不要假装会做。

- 换产品、换脸、超级复刻 → `/super-clone`（上架后）
- 抽帧、原声时间轴、BRIEF/TIMELINE 落盘 → `/hypit`（上架后）
- Omni 视频生成 → `/omni`（上架后）
- 策划 / 采爆款 / 分析 / 千川 / 客服 → **本阶段不路由**；只处理视频制作三条

不要把 Hypit 当成复刻主合同。

## 批准粒度

只在这些地方停：

1. 方案（含批量任务列表）
2. TIMELINE 切段表或投放草稿
3. 成片验收或客服升级（退款/发怒转人）

不要每个生成 HTTP 问一次。

## 会话绑定

每个会话同时绑：

1. **资源管理器**：本项目目录（素材与 LOOP）
2. **账号浏览器**：一个自研指纹环境；该浏览器可登录同一店铺的多个平台（抖店 / 千川 / 飞鸽），不混店

用户说账号矩阵、有哪些号、切换浏览器 → `/account-matrix`（上架后）。

## 账号记忆（隔离）

本会话绑定的 `account` 才是记忆主人。

- 每调用一条 skill，追加写入 `accounts/<account>/SKILL_LOG.md`（项目路径、skill 名、结果）
- 可复用的结论写入 `accounts/<account>/MEMORY.md`
- **禁止读、写、引用其它账号的 MEMORY / SKILL_LOG**
- 矩阵汇总只用各号档案索引，不把记忆正文拼进当前会话

## 文件即记忆

每个任务在 `productions/<task>/`：

- `LOOP.md` 当前环（框架中枢）
- `BRIEF.md` `ANALYSIS.md` `SCHEME.md` `TIMELINE.md` `ASSETS.md` `ADS.md` `CS.md` `PROGRESS.md`

## 生成器

本阶段客户只在 DSH 对话。jobs 调用 `D:\video-skill` 现成 Python。不打开 8090。不换对话模型（Agnes 3.0 Flash）。

商品资料本阶段不建档：出片时在对话里现问产品图和卖点即可。

## 模型调用轨（所有 skill 调模型的唯一入口）

任何 skill 要调模型（文字 / 图 / 视频 / 看图），一律经 `tool-jobs` 跑统一网关，**不各自硬编码厂商 key/endpoint、不直连供应商**：

```
python D:\video-skill\backend\gateway.py call <capability> [purpose] --account <LOOP.member> --json '<params>'
```

- `capability`：`text.complete` / `image.generate` / `video.generate` / `video.analyze`；`purpose` 看 `gateway.py caps`。
- **唯一例外形态 = hypit**：它的 Provider 协议只认 HTTP，所以生成腿经自写 provider `@weeksir/provider-gateway` POST `http://localhost:8100/api/hypit`（`/uploads`、`/videos`、`/videos/{id}`、`/rates`），服务端 `hypit_bridge.py` 仍转 `gateway.invoke` ⇒ **同一套预扣/结算/审计**。不算第二条轨，只是同一轨换了入口；鉴权用 `.env` 的 `MODEL_GATEWAY_TOKEN`（Bearer）。详见 `.dsh/skills/hypit-pipeline/references/monitor-contract.md` §1.5。
- 返回归一 JSON：`{ok, result, credits:{estimated,charged,tokens,basis,balance_after}, error}`。
- **积分**：网关调用前自动 校验余额→预扣→成功提交 / 失败回滚；不足则 `ok:false` 提示去 weeksir.com 充值。`--account` 用 `LOOP.member`。
  - `basis`：`usage`=按真实 token 结算（文本/看图，费率 `model_prices.json.token_rate`，1 分≈100 token）、`flat`=按条计费(生图/生视频)、`usage-capped`=超单条封顶被截。
  - `estimated` 只是**粗略预估**：思考型模型同题消耗实测波动 28→4396 token，报价/护栏看 `estimated`，**对账看 `charged`+`tokens`**。
- **估价**（批准前给人看成本）：`gateway.py resolve <cap> [purpose]`，或读信封 `credits.estimated`。折进"方案批准"，不新增批准类。
- 为什么走 tool-jobs 而非 MCP/插件：DSH 不加载 MCP、插件不能给 Agent 注入工具（见 `docs/AGENT-FRAMEWORK.md:6`）。命令契约写在这里 + 各 skill 的 SKILL.md，**跨会话读文件即恢复、不靠 agent 记忆**。
- 完整设计：`docs/ADR-model-gateway.md`。
