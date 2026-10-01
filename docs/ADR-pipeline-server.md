# ADR：统一流水线服务（单端口多流水线网关）

- 状态：已接受（Accepted）· 决策先行、实施分期
- 日期：2026-10-01
- 决策者：产品
- 相关：[[video-ui]] [[super-clone-hypit-pipeline]] [[omni-delivery-contract]] [[dsh-stage-ui-architecture]] `docs/RELEASE.md`

## 背景 / 问题
每条流水线各自一个 Web 端口：video-ui(omni)=8765、D:\video-skill(super-clone+hypit)=8090、model_admin=8100、hypit studio=5179。再加流水线就再+端口，导致：
- pipeline-view 右栏 iframe 要硬编码每个端口；
- 每加一条线要处理一次 CORS；
- 谁挂了/哪个端口是啥全靠人肉记；
- 端口/防火墙/启动管理随技能数线性变糟。

## 决策
**采用「单一 FastAPI 网关进程」承载所有流水线前端+API，一个端口一个 origin，各流水线挂成路径前缀模块。**（评估过的 D/B/A/C 里选 C）

```
:8000/omni/*    ← video-ui（Flask/WSGI）经 a2wsgi WSGIMiddleware 挂进来
:8000/clone/*   ← super-clone（原 8090 FastAPI，设 root_path）
:8000/hypit/*   ← hypit（原 8090）
:8000/admin/*   ← model_admin（可选，运维）
```
- 各 app **不合并代码**，保留各自框架，只被同一个 ASGI 进程 `app.mount()`。
- 一个端口、一个 origin → CORS 全免、iframe 只认 `:8000/<线>`、启动/监控统一到一个进程。

## 为什么是 C（对比）
- **D（清单+launcher，不合并端口）**：最省，但端口没减，只是"集中管理"。治标。
- **B（统一前端 hub 页，底下仍多端口）**：入口统一，端口/CORS 没解决。
- **A（反向代理 + 前缀，进程仍分开）**：单 origin，但要处理前缀重写；和 C 的**核心成本相同**（见下），C 还省了独立代理进程。
- **C（一个网关进程 mount 全部）**：真单端口单 origin、CORS 免、运维一个进程。选它。

## 强制约定（这是全部成本所在）
任何挂进网关的流水线前端**必须 base-path 感知**，不能再写绝对根路径：
- 前端资源/接口一律走**相对路径**或运行时注入的 `<base>` 前缀（`<base>/api`、`<base>/static`、`<base>/files`）。
- Flask 侧：设 `SCRIPT_NAME`（ProxyFix/`DispatcherMiddleware`）；FastAPI 侧：设 `root_path`。
- 后端返回给前端的 URL（如 video-ui 的 `/files/...`、`/api/...`）要带前缀或用相对。
> 现状 video-ui `app.js` 用 `/api /static /files` 绝对路径、D:\video-skill 前端同理——**这是迁移的主要工作量**，一次改对、以后新线沿用同一约定即一劳永逸。

## 迁移分阶段
1. **立约定**（本 ADR）：网关 :8000 + 每线一前缀 + 前端 base-path 感知。
2. **试点 video-ui**：改 `app.js`/模板为相对路径 → 用 `a2wsgi` 把它 mount 进一个最小 FastAPI 网关 `:8000/omni` → 验证 iframe/静态/API/CORS 全通。
3. **迁 clone/hypit**：给 D:\video-skill 设 `root_path`、前端改相对 → mount 进 `:8000/clone`、`:8000/hypit`。
4. **下线旧端口**：8765/8090 停独立暴露，只经 :8000。
5. **pipeline-view 插件**：iframe 指 `http://127.0.0.1:8000/<线>/`；presence 门控（本会话有该线 run/task 才弹）按线读 `<base>/api/...`。
6. 新流水线按约定直接加一个 mount。

## 影响
- **pipeline-view**：不再每线硬编码端口，读一份"线→前缀"表即可（和 [[dsh-skill-showcase]] 的 dock 共用一份清单）。
- **agent 驱动**：所有 REST 基址从 `:8765`/`:8090` 改 `:8000/<线>`；skill 里的 monitor-contract 端点前缀要同步。
- **3.0 对齐**：网关即未来服务端后端的雏形——3.0 把 agent/密钥也搬上来后，这个 :8000 网关就是瘦客户端连的那一个 origin。

## 风险 / 回退
- 风险：base-path 改造有漏网绝对路径 → 某功能 404。缓解：试点一条先跑通再迁其余；每线迁移后 smoke test。
- 单进程 = 单点：一个 app 崩可能带累网关。缓解：mount 保持各 app 隔离、异常局部化；关键线可临时回退独立端口（约定兼容两种部署）。
- 回退：网关失败可退回"各端口 + D 的清单/launcher"，不丢功能。

## 待确认
- [ ] `a2wsgi` 挂 Flask + FastAPI `root_path` 在 Windows/uvicorn 下是否顺畅（试点阶段实测）。
- [ ] video-ui 的 `/files` 静态、DSH 右栏 iframe 跨端口引用是否还需保留（合并同源后应可省 CORS）。
- [ ] 网关进程纳入 launcher / 发版（见 `docs/RELEASE.md`）。

## 实施时机
**先不动**（当前专注补 super-clone 基础 skill）。本 ADR 定架构；实施排在复刻 skill 跑顺之后，从"阶段 2 试点 video-ui"起。
