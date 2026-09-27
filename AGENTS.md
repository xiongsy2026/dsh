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
