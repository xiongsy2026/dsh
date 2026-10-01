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
> ⚠️ 2026-10-01 静态核实推翻了原"试点 video-ui 先行"：**video-ui 才是最重的一个**（Flask 要 a2wsgi + 前端 37 处绝对路径），**hypit 最轻**（纯 FastAPI + 仅 11 处），故改为 **hypit 先行试点**。证据见下「可行性验证」。
1. **立约定**（本 ADR）：网关 :8000 + 每线一前缀 + 前端 base-path 感知。
2. **试点 hypit（改此）**：给 D:\video-skill 建最小网关 `gateway.py`(:8000)，`include_router(hypit_router, prefix="/hypit")` → 改 `frontend/hypit.html` 绝对路径为 base-path 感知（`resolveUrl()`）→ smoke test 页面/静态/api/CORS(应同源免)。已 TestClient 预验（见「可行性验证」），暂存件在 `C:\dsh\scratch\gateway-pilot\`（待 D: 腾空间落回，见 `APPLY.md`）。
3. **plugin + skill 对齐**：pipeline-view 的 Hypit tab 指 `:8000/hypit/hypit`、门控 fetch `:8000/hypit/api/hypit/projects`；hypit-pipeline monitor-contract 端点前缀同步。
4. **再迁 video-ui(omni)**：`a2wsgi` 挂 Flask + 改 app.js 35 处 + templates 2 处 → `:8000/omni`（这次才碰 a2wsgi 那个未验项）。
5. **迁 clone + admin**：给 D:\video-skill 设 root_path → `:8000/clone`、`:8000/admin`（可选）。
6. **下线旧端口**：8765/8090 停独立暴露，只经 :8000。新流水线按约定直接加一个 mount。

## 可行性验证（2026-10-01·静态核实·未跑起来）
针对"能否拿 hypit 当第一试点"，查 `D:\video-skill` 得证：
- **`hypit_router` 对主 app 零反向依赖**：`backend/services/hypit_workspace.py` 只 `from config import settings`（:19），不 `import app`、不碰 `lifespan`/`OUTPUT_DIR`（grep 无命中）→ 可单独 import 进新网关、**不拖 app.py 那 11848 行 super-clone**。
- `WORKSPACE` 文件相对路径（`hypit_workspace.py:31`），换挂载点无影响；密钥 `hypit_agent.py:44-45` import 期从 `settings` 读，网关同仓库直接可用，**不新增密钥面**。
- **改动面 = `frontend/hypit.html` 仅 11 处 `/api/hypit/{env,productions,upload,file,projects,studio,agent}` 绝对路径**（无外链 JS/CSS）。⚠️ `<base href>` 对前导 `/` 的绝对路径无效 → 须真去掉前导斜杠改相对（或注 root_path），不能用 `<base>` 偷懒。
- **挂载形态**：`include_router(hypit_router, prefix="/hypit")` 后原生页落在 `:8000/hypit/hypit`（双 hypit，丑但可跑），iframe 与门控 fetch 全**同源 → CORS 免**。
- **✅ 已实测（2026-10-01·TestClient·未写 D:·未占空间）**：环境 fastapi 0.139.2 / starlette 1.3.1（include 的路由包成 `_IncludedRouter`，不平铺）。`include_router(hypit_router, prefix="/hypit")` 装配后：`GET /hypit/hypit` → **200 HTML**；`GET /hypit/api/hypit/projects` → **200 JSON**。证明 ① router 独立可导（16 路由齐）② 前缀生效、真实路径 `/hypit/hypit` + `/hypit/api/hypit/*` ③ 与 staged `resolveUrl()` base 推导吻合。**唯余**：真 uvicorn 起服务（vs TestClient 进程内）这一小步未跑；`projects` 现无 `session` 字段（presence 门控仍需 8090 侧补，见契约 §7）。
- **剩余 2 个只有跑起来才知道的风险**（已被 TestClient 缩小）：① ~~prefix 是否生效~~ 已验；改判为"真 uvicorn/Windows 下 hypit `runtime` 未 ready 时各端点行为"待 smoke；② 网关独立进程 import `hypit_workspace` 得到**自己那份** `_jobs`/`_studio` 内存态，与 8090 那份**互不相通**——试点期别两边混用同一 job/studio。
- 过渡态：**网关管 hypit、omni 暂留 :8765**、super-clone 暂留 :8090 —— 比"再给 8090 补一套裸 CORS 碎片"干净。

## 影响
- **pipeline-view**：不再每线硬编码端口，读一份"线→前缀"表即可（和 [[dsh-skill-showcase]] 的 dock 共用一份清单）。当前已加的 Hypit tab，其 iframe/门控基址随试点从 `:8090` 改 `:8000/hypit`。
- **agent 驱动**：REST 基址逐步从 `:8765`/`:8090` 改 `:8000/<线>`；skill 里的 monitor-contract 端点前缀要同步（hypit-pipeline 契约先行，omni 待 video-ui 迁）。
- **3.0 对齐**：网关即未来服务端后端的雏形——3.0 把 agent/密钥也搬上来后，这个 :8000 网关就是瘦客户端连的那一个 origin。

## 风险 / 回退
- 🔴 **最高危（商业化红线）**：这些控制台**无鉴权、且 `cli/build` 类端点服务端起 subprocess**（`hypit_workspace.py` `_run_hypit`、video-ui 生成端点）。**任何"为让客户看到 UI 就把端口开到 LAN/公网"的做法 = 把『任意人执行命令 + 无限烧付费生成』送出去**，且密钥在 .env 明文随包即泄（见 [[dsh-client-release-shaping]] hide≠remove）。网关只把攻击面从 N 端口收敛成 1 origin，**收敛≠安全**——单 origin 裸暴露同样致命。
- 风险：base-path 改造有漏网绝对路径 → 某功能 404。缓解：hypit 试点先跑通（只 11 处）、smoke 后再迁其余。
- 单进程 = 单点：一个 app 崩可能带累网关。缓解：mount 保持各 app 隔离、异常局部化；关键线可临时回退独立端口（约定兼容两种部署）。
- 回退：网关失败可退回"各端口 + D 的清单/launcher"，不丢功能（hypit 试点失败 → 8090 原样还在）。

## 从本机自用到上客户·强制安全门禁（Gate 0·不满足不得交付客户）
网关/流水线现在默认 **127.0.0.1 + ops 自用**。要面向客户，**三条全绿前只能发"客户 UI 外壳"、不能发/暴露任何生成栈**：
1. **只绑 + 白名单 origin**：网关只 `--host 127.0.0.1`（本机）；若必须跨机，走反代加 **origin 白名单 / mTLS / 内网**，绝不 `0.0.0.0` 裸暴露（对齐 model_admin「只绑 127.0.0.1、绝不上 LAN」旧规）。
2. **执行型端点鉴权 + 只读化**：`/api/hypit/cli`(subprocess)、`build/pricing`、video-ui 生成端点 → 服务端**鉴权 + 限额**；给客户看的视图要么**只读**（隐藏所有生成/执行按钮，批准仍走对话，同 [[omni-delivery-contract]] "UI 只读非输入口"），要么根本不给、只回传产物。
3. **生成/密钥/计费在服务端（= 3.0）**：客户产品最终形态 = weeksir 后端跑生成栈、瘦客户端连**单个鉴权 origin**、**按积分服务端计量扣费**（客户端可篡改，计费不能放端上）。此即 [[dsh-product-roadmap]] 3.0 与 [[model-gateway-direction]]，本 ADR 的网关是它的本机雏形。
> 一句话：**"端口多不多"是运维问题（网关解决）；"能不能商业化"是"生成/密钥/计费在哪跑、谁能碰"的问题（服务端鉴权+只读解决）**。别把本机无鉴权控制台当交付物直接摊给客户。

## 待确认
- [x] ~~a2wsgi 挂 Flask + FastAPI root_path 是否顺畅~~ → 拆两步：**hypit 先（纯 FastAPI，不涉 a2wsgi）**，video-ui 后（届时才验 a2wsgi）。
- [ ] Windows/uvicorn 下 FastAPI `mount` 子 app 的 `root_path` 转发实测（hypit 试点首验）。
- [ ] 网关进程与 8090 各自独立 `_jobs`/`_studio` 内存态——是否要收敛为"网关期只跑一份 hypit"。
- [x] ~~video-ui 的 `/files` 静态跨端口~~ → 合并同源后省 CORS（hypit 试点即验证）。
- [ ] 网关进程纳入 launcher / 发版（见 `docs/RELEASE.md`）。

## 实施时机
**决策更新（2026-10-01）**：不再"整体先不动"，改为 **hypit 试点先行**（成本最低、能顺带验证 mount/root_path/同源免CORS 三件事）。动手前提本 ADR 已把方案与风险写清；实施 = 新建 `D:\video-skill\gateway.py` + 改 `frontend/hypit.html` 11 处 + 重启 + pipeline-view/skill 对齐。**video-ui 的 a2wsgi 重改造仍排在其后单独做。**
