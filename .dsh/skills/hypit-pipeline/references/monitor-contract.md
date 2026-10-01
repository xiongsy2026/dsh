# Hypit 工程化出片 · 驱动契约（monitor-contract）

WeekSir 薄编排层驱动 **hypit 原生引擎**（`D:\video-skill\backend\services\hypit_workspace.py`，8090 `/api/hypit/*`）。
本契约的每条端点/常量都标了 file:line，改动前先核。上游 authoring 逻辑在 `.dsh/skills/hypit/`（只读），
本 skill 只负责「建工程→传参考→拉起原生 Agent→关口请示→右栏挂载」。

引擎根路径常量：
- `WORKSPACE = D:\video-skill\hypit-workspace`（`hypit_workspace.py:31`，工程落 `productions/<slug>/`）
- `STUDIO_PORT = 5179`（`:33`，Studio 按 run 临时起）
- 原生页 HTML `hypit.html`（`:269`，由 `settings.FRONTEND_DIR` 提供）

---

## §1 端点清单（8090）

| 端点 | 方法 | file:line | 用途 | 关键返回 |
| --- | --- | --- | --- | --- |
| `/api/hypit/env` | GET | :278 | 查 workspace/版本/runtime 是否 ready、Studio 是否在跑 | `runtime.ready`、`studio.{running,url,run}` |
| `/api/hypit/projects` | GET | :308 | 列 productions/references 目录 | `productions`、`tree` |
| `/api/hypit/productions` | POST | :320 | **建工程**；body `{name,brief?,reference_slug?}` | 自动落 `BRIEF.md/TREATMENT.md/PROGRESS.md`(:335-352) |
| `/api/hypit/upload` | POST | :356 | multipart `kind=reference/asset/production-asset`+`slug`+`file` | reference 自动落 `ANALYSIS.md/TIMELINE.md`(:374-383) |
| `/api/hypit/file` | GET/PUT | :409/:419 | 读写工程内文本(SVML/SVS)；PUT body `{path,content}` | 非文本回 `media` url |
| `/api/hypit/media` | GET | :429 | 取二进制产物帧/片 | FileResponse |
| `/api/hypit/cli` | POST | :474 | 底层原语(排查用)，body `{action,target?,extra?}` | 见 §4 |
| `/api/hypit/jobs/{id}` | GET | :513 | 轮询长任务(build/plan/pricing 异步) | `status: running/done/failed`、`output`、`data` |
| `/api/hypit/studio/start` | POST | :521 | body `{run:"<.svrun 路径>",port?}` | `url`(默认 :5179)、抽帧 QA 用 |
| `/api/hypit/studio/stop`/`status` | POST/GET | :590/:606 | 收/查 Studio 进程 | |
| **`/api/hypit/agent/chat`** | POST(SSE) | :669 | **拉起 hypit 内置 Agent 跑一轮**；body `{messages:[...], production}` | SSE: `delta`/`tool_call`/`done`(`needs_approval`) |
| **`/api/hypit/agent/execute`** | POST(SSE) | :697 | **执行已批准的 tool_call 并续推**；body `{messages,tool_call_id,name,arguments,production}` | SSE: `tool_result`→续 `delta`/`tool_call`/`done` |
| `/api/hypit/agent/skill` | GET | :654 | 调试：返回内置 Agent 现加载的 SKILL 文本 | |
| `/hypit` | GET | :267 | **右栏挂载目标**=原生「Hypit 内置 Agent」控制台 | |

请求体（`:625/:635`）：
```
AgentChatRequest    = { messages: list[dict], production: str="" }
AgentExecuteRequest = { messages, tool_call_id, name, arguments: str(JSON原文), production }
```
`messages` 必须带完整历史（system/user/assistant/tool）；`arguments` 用原始 JSON 字符串避免序列化差异。

---

## §2 驱动时序（DSH Agent 的主路径）

```
0. GET /env → runtime.ready?  否→ cli runtime-init/runtime-up 或提示去 /hypit 点 Doctor
1. POST /productions {name, brief}          → 记 slug
2. POST /upload?kind=reference (multipart)  → 参考片落 references/<slug>/，自带 ANALYSIS/TIMELINE 骨架
3. 右栏 iframe src = /hypit（§5），固定展示原生控制台
4. POST /agent/chat (SSE) 逐轮驱动内置 Agent：
     它读参考→写 SVML/SVS→check→plan→pricing
5. 【关口A 方案+预算批准】内置 Agent 在 build 前发 tool_call(pricing→build)：
     - 收 done(needs_approval=true) 或 tool_call(build)
     - DSH Agent 把 plan(结构)+pricing(成本) 摆给**人**；人批 → 走 /agent/execute 触发
     - 未经人批，绝不 execute build（花钱闸，铁律1）
6. build 长任务：agent/execute 内部走 cli build→异步 job；
     轮询 GET /jobs/{id} 至 done
7. 【关口B 抽帧QA】POST /studio/start {run:".../<x>.svrun"} → 帧图贴对话核对 ANALYSIS/TIMELINE；studio/stop
8. 成片交付（对话 present→右栏 video 播）
9. 【关口C 成片验收】人点头 → 落档案
```

`hypit_agent.py:206` 内置 Agent 自己就规定「涉及付费 build / 写文件 / 启 Studio 先简述意图再发 tool_call」，
和关口A 对齐——DSH 侧顺势把这条 tool_call 拦下来交人批即可，不用另造审批。

---

## §3 原生 Agent 批准回路（SSE 协议）

- `agent/chat` 每轮：LLM 一次只发**一个** tool_call → 前端弹批准卡 → 用户点确认 → `agent/execute` 执行 → 结果回灌 → 续推，直到不再发 tool_call（`hypit_agent.py:5-7`）。
- 事件：`delta`(文本流) / `tool_call`{id,name,arguments} / `tool_result` / `error` / `done`{finish_reason, needs_approval}。
- **DSH Agent 的角色**：不是自己点卡，而是把 needs_approval 的 tool_call 语义翻译进 DSH 对话，等**人**在对话批准后再 POST `agent/execute` 回灌。批准闭环始终在 DSH 对话（见 omni 同范式）。

---

## §4 底层原语 cli（仅排查/兜底，主路径走原生 Agent）

`/api/hypit/cli` body `{action,target?,extra?}`；长任务(timeout≥120s)返回 `job_id`，走 `/jobs/{id}` 轮询。

| action | args | 超时 | 需 target |
| --- | --- | --- | --- |
| version/paths/builds/activity/auth-status/runtime-status | — | 30 | 否(:441-451) |
| doctor | — | 180 | 否(:443) |
| runtime-init/up/down/logs | — | 30/600/60/30 | 否(:445-448) |
| **check** | `<工程/文件>` | 60 | 是(:459) |
| **plan** | `<target>` | 120→job | 是(:461) |
| **pricing** | `<target>` | 120→job | 是(:463) — **build 前必查** |
| **build** | `<target>` `--follow` | 3600→job | 是(:465) |
| status/inspect/logs/cancel | `<build-id>` | 60 | 是(:467) |

target 走 `_safe_rel` 限定 workspace 内路径；build-id 校验 `[A-Za-z0-9._:-]{4,128}`(:468)。

---

## §5 右栏挂载（复用 pipeline-view 插件·已扩第二 tab）

- **pipeline-view 插件 `plugins/ops-gateway/lib/client.js` 现注册两个右栏 tab**：
  ① 「流水线」= iframe → video-ui `http://127.0.0.1:8765/?session=<sid>`（omni，gate=`/api/runs?session=` 里有 `script_approved` run）；
  ② 「Hypit」(`weeksir-hypit-view`) = iframe → 原生 `http://127.0.0.1:8090/hypit`（`hypit_workspace.py:267`）。
- **Hypit tab 的 presence 门控**（`syncHypitTab`）：`fetch(:8090/api/hypit/projects)` →
  `productions.some(p => p.session === 当前 sid)` 才 `openTab`。所以 **DSH Agent 建工程时必须把 `$DSH_SESSION_ID` 记进该 production 的 session**。
- ⚠️ **门控生效的前置（8090 侧·尚未做·需确认）**：8090 现在 **没给 `/api/*` 加 CORS**、`/projects` 的 production **没有 `session` 字段**。
  二者缺一 → `syncHypitTab` 的 fetch 被跨源挡 → Hypit tab 不弹（不崩、只是不出现）。补齐方案见 §7。
- **iframe 嵌 `/hypit` 本身不受 CORS 影响**（iframe 可加载跨源；`/hypit` 无 `X-Frame-Options`/CSP `frame-ancestors`，已核）。受影响、需要 CORS 的只是门控那条 `fetch(projects)`。
- 原生 `/hypit` 控制台是**全局的**（不认 `?session`/`?project=`）：tab 出现即代表"本会话有 hypit 工程"，控制台里靠人/agent 选中该 slug 的工程。deep-link 到具体 slug 待查原生页是否支持查询参数（未验证）。
- ⚠️ `/hypit` 是无鉴权操作台且含付费 build 按钮：现仅 ops 自用；给客户前须只读化或加闸（同 omni `/` 的 caveat）。

## §7 让 Hypit tab 门控生效所需的 8090 最小改动（待确认·生产引擎）

对照 video-ui 成熟先例，只两处：
1. **给 `/api/*` 加 CORS**：`app.py` 加 `CORSMiddleware(allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])`（或 router 级 after_request 给 `/api/` 设 `Access-Control-Allow-Origin: *`）。
2. **production 存 session + 在 `/projects` 回吐**：
   - `ProductionCreate`(:245) 加 `session: str = ""`；`POST /productions`(:320) 写 `productions/<slug>/.session` 侧车（或 PROGRESS.md 里一行 `session: <sid>`）。
   - `_dir_summary`(:179) 每个 item 读侧车 → 加 `"session": <sid>`。
   - DSH Agent 建工程：`POST /api/hypit/productions {name, brief, session:$DSH_SESSION_ID}`。
> 未补 1、2 前，Hypit tab 不会自动弹（回滚安全：插件 fetch 失败仅 no-op，omni tab 不受影响）。

---

## §6 前置与坑（file:line 佐证）

1. **内置 Agent 依赖 `hypit` 在 PATH**：`_ensure_agent` 找不到就 500「请先 `npm install -g @hypit/hypit`」(:623 附近)。当前版本 0.2.9。
2. **runtime 未 ready 一切皆空**：`/env` 的 `runtime.ready = hypit.runtime.json 存在 && .hypit/runtime 选中存在`(:291-294)；先 init/up。
3. **build 花钱、耗时长**：3600s 超时、按 Need 付费，pricing 未批绝不 build（铁律1）。
4. **Studio 按 run 临时起**：`.svrun` 才行(:526)，端口默认 5179，用完 stop；它不是 NLE 剪辑台，只是 per-run 检视器。
5. **上游 skill 会被覆盖**：`npx skills add hypit-ai/hypit -g` 整目录替换 `.dsh/skills/hypit/`；
   WeekSir 一切驱动/关口/路由只写在本 `hypit-pipeline/`，别写进上游 SKILL.md（其 `whenToUse` 也随时会被冲掉）。
6. **长任务结果在 `_jobs` 内存**：8090 重启丢历史 job（同 super-clone 内存态），别把它当持久队列。
