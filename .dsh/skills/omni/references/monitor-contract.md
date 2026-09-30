# omni 出片 · 监控+优化契约草案（v0，讨论稿）

> 状态：**草案，未落地**。这是 step③ 的主体：规定 agent 如何驱动 video-ui、看什么、判什么、怎么自修、何时找人。**先评审再改写 SKILL.md 并实现。**
>
> 所有事实断言都对着代码里存在的信号/端点/字段（引用 `file:line`）；未实测的地方明确标 ⚠️ 待验。

---

## 0. 边界（分工的一句话）
- **video-ui（Flask, `127.0.0.1:8765`）= 唯一执行引擎**：所有生图/生视频/技术体检/闸门/落盘都在它里面。
- **omni skill = 指挥层**：agent 依此契约，通过 HTTP 驱动它、看它的中间产物、做**语义质检 + 失败自愈 + 请示**，其他什么都不做。
- 客户只在 DSH 对话里给输入和批准，**永远不点 video-ui 的按钮**。

**边界规则**（防止越界）：
- agent **不直连** zexapi/gflow/omni 模型 API，也不读 `.env` 里的 key；所有模型调用都通过 video-ui 的 API 触发。
- agent **不修改** video-ui 的 config.json / prompts.py / app.py；配置由 video-ui 端页面或人手动设。
- agent **不重复做技术体检**（时长/分辨率/codec/黑帧/卡帧/音量/水印/keyleak），video-ui `do_selfcheck` 已全自动（app.py:1322）。

---

## 1. 驱动约定（HTTP 端点·agent 只用这些）

**同步**（一次请求返回结果）：
- `POST /api/run` form: title/duration/mode；可选 multipart 字段 `product`。→ 建 run。
- `POST /api/run/<n>/product` 字段 `product`。→ 换/补主图。
- `POST /api/run/<n>/assets` 字段 `asset`，form: kind (product|character|prop)/name。→ 加细节图/角色图。
- `POST /api/run/<n>/country` JSON `country`。→ 目标国家/口播语言。
- `POST /api/run/<n>/script` JSON `text`（完整 script.json 或含围栏原文，`extract_json` 兼容）。
- `POST /api/run/<n>/gate` JSON `gate`(script|frames)/`approve`(bool)。
- `POST /api/run/<n>/frame_review` JSON `idx`/`verdict`(approved|rejected|pending)/`note`。
- `POST /api/run/<n>/camera` JSON `segment`/`text`（人肉运镜词）。
- `GET /api/run/<n>` → `{state, script, beats}`（**主轮询端点**）。
- `GET /api/bootstrap` → `{runs, config, caps, dry_run, key_present, gflow_exe_present, eyes_checks, doubao}`。
- `GET /files/<n>/<rel>` → 取产物字节。

**异步**（返回 `{ok, msg}`；worker 后台跑，agent 必须轮询 `state.job`）：
- `POST /api/run/<n>/product_card` JSON `notes`（Stage0，产品模式）。
- `POST /api/run/<n>/reason_brief` JSON `hint/country/platform`。
- `POST /api/run/<n>/ai_script` JSON `product_name/selling_points/structure/platform/country/extra/story_draft`。
- `POST /api/run/<n>/translate_script`（补中文对照）。
- `POST /api/run/<n>/story_outline`（story 模式大纲）。
- `POST /api/run/<n>/frames` JSON `backend`(gflow|zexapi|doubao)/`ref_mode`(skill|prev_only|anchor_only)/`targets`(list|all|null=未完成)。
- `POST /api/run/<n>/ai_camera` JSON `segments`(list|all|null=全部)。
- `POST /api/run/<n>/segments` JSON `targets/model/size`。
- `POST /api/run/<n>/finalize`。
- `POST /api/run/<n>/selfcheck`。

**幂等铁律**（app.py:39-80, 单 `_QUEUE` + 单 worker）：
- 每个 run 同时只允许 1 个 active job；`job.active=true` 时再 POST 异步端点 → 返回 `ok:False, msg:"有任务在跑"`。
- **提交前必读** `GET /api/run/<n>` → `state.job.active`。false 才允许提交。
- 提交后按 5s 间隔轮 `state.job`，直到 `active=false`；同时看 `state.job.current` 与 `state.job.lines`（最近 60 行日志）判断阶段与错误。
- **全服务器跨 run 串行**：一个 run 的 job 完成前，别的 run 只能排队。**批量并发需要多进程 video-ui**。⚠️ 待验：单 worker 是否真的把不同 run 的 job 排一个队列（代码显示共享 `_QUEUE`，实测再确认）。

---

## 2. 阶段流水线（agent 视角）

```
Stage0  intake        POST /api/run + /product + /assets + /country
Stage0  product_card  POST /product_card (async) [产品模式；story 见 §6 bug]
Stage0.5 reason_brief POST /reason_brief (async) → 推理「卖点 + 3 故事方向」写 brief.json
        选方向+定语言(触点) 贴 3 个 story_options 让人挑/改一个；同时确认 目标市场 + 台词语言 + 口播(语言/性别/开关) + 画面文字/字幕约定 → 方向作 selling_points、语言经 /country(+/settings·voiceover_language) 落定，喂 Stage1
Stage1  script        POST /ai_script (async，用所选方向) 或 POST /script (sync 手工)
        [可选] translate_script (async)
Gate1.5 script        POST /gate {gate:"script", approve:true}     ← 关口
Stage2  frames        POST /frames {backend, ref_mode:"skill"}     (async)
Stage3  语义 QA       见 §3 判 EYES_CHECKS；单帧 POST /frame_review
Gate3   frames        POST /gate {gate:"frames", approve:true}     ← 关口
Stage4  camera        POST /ai_camera (async) 或 /camera (sync)
Stage5  segments      POST /segments (async)
Stage6  finalize      POST /finalize (async)
Stage6  selfcheck     POST /selfcheck (async)  [成片验收]
        成片批准       回对话请示人                                  ← 关口
```

> **创意方向归人**：`reason_brief` 出的 3 个故事方向是给人挑的，agent **不擅自定方向直接 ai_script**。这是"方案"之前的一个轻触点（选方向 + 方案可合并成一次请示：先给方向让你选、再给据此生成的剧本方案）。

> **语言先钉死再生成（并入上面那个触点，不新增关口）**：台词语言由 `/country`→`COUNTRY_LANG` 推（`prompts.py:535` 强制 dialogue 用目标语言、`keyframe_prompt` 仍英文）；口播走 `effective_voiceover`（优先级 国家 > run `voiceover_language` > 全局，`app.py:158`）。**语言/市场没在人这边确认前不跑 `ai_script`**——语言错则剧本台词 + 口播全返工，最贵。画面内出现的所有文字/字幕用什么语言也在这一触点跟人说清（video-ui 不自动管这块，纯 agent 请示项；之前品牌字 NGLED/NGLEED 漂移就属这类无人复核的文字）。

闸门由服务端强制：`/frames` 要求 `review.script_approved`；`/ai_camera` `/camera` `/segments` 要求 `review.frames_approved`（app.py:816/842/871/989）。agent 不能跳。

### 触发链（关口批准 = 自动起跑信号）
**每拿到一个人工批准，agent 就自动串到下一个关口为止，中途不再逐步请示。** 三个"起跑键"：
- **方案批准** → agent `POST /gate{script}` → **自动**跑 Stage2 全部关键帧 → 轮询到 `job.active=false` → 逐帧语义 QA → 能自愈的先修 → **停在"审帧"关口**贴图给人。
- **审帧批准** → agent `POST /frame_review`+`/gate{frames}` → **自动**跑 Stage4 运镜（全段）→ Stage5 分段出片（轮询、降级/审核拦截按 §4 自愈）→ Stage6 `finalize`+`selfcheck` → **停在"成片"关口** present 给人。
- **成片批准** → agent 落 `DELIVERABLES.md` 指针 + 收尾。

规则：
- 关口之间**全自动**，agent 自己 poll-wait（帧/段要几分钟，循环读 `job.active`，别提交完就回话）。
- 只在**关口**、**预算护栏用尽**（单 run 重跑>8 / 单段>5）、**需要产品方事实**（品牌拼法、卖点真伪）这三类情况才停下来找人。
- 失败不静默：卡在哪一帧/哪一段、试了几次、为什么，如实汇报给人，让人决定继续还是改方案。

---

## 3. 监控闭环（每阶段一次）

**监控信号**（读 `GET /api/run/<n>` 的 `state`）：
- `state.stage` · `state.flags` {has_script/script_approved/frames_done/frames_total/segments_done/segments_total/final}。
- `state.job` {active, current, lines}。
- `state.frames["NN"]` {status, backend, error, file, thumb, refs, prompt, seconds}。
- `state.segment_status["N"]` {status, file, task_id, attempt, probe}。
- `state.selfcheck` 每条 verdict pass|warn|fail|eyes（app.py:1322-1435）。
- `state.eyes_checks` 7 项清单（app.py:1298-1315）。

**看什么**（agent 语义质检的具体动作）：
1. Stage2 完成后，逐帧取 `frames/thumb/frame_NN.jpg`（同机路径 `<RUNS_ROOT>/<run>/frames/thumb/…`；跨源用 `GET /files/<run>/frames/thumb/frame_NN.jpg`）→ **用 agent 原生多模态 Read 看图**。⚠️ 待验：`VIDEO_UI_RUNS` 目录对本 agent 文件系统是否可见；若不可见则退回 HTTP `/files`。
2. 对每帧按下列 7 条 EYES_CHECKS 判：
   - **product_identity**：产品外观（部件/颜色/材质/印刷文字）与 `state.product_card.summary_en` 一致；
   - **no_leak**：剧本里 `product_presence=not_present` 的帧，画面里不许出现产品；
   - **faces/cast_match**（story 模式）：出现的人/道具必须在名册里，同一人跨帧脸/发/衣一致；
   - **no_artifact**：无乱码文字/多余水印/畸形手指/凭空物体；
   - **product_scale / product_complete**：产品尺寸合理、没被裁边；
   - **frame_order / bridge**：段内动作连续、跨段过渡合理。
3. Stage6 selfcheck 完成后，把它的 `verdict:"eyes"` 那几项由 agent 用 Read 补判（其他 pass/warn/fail 直接采信）。
4. 整片 VLM 判断只在两种场景调 `gateway.call("video.analyze", path=<final_clean.mp4>)`（gateway.py:171-216）：① selfcheck 里 freeze/black/watermark diff 有 warn 时；② 人问"这版到底行不行"要一份客观描述。**默认不调**（6 积分桩 + 240s 往返，gateway.py:90/209）。

---

## 4. 优化决策表（不合格怎么办）

| 症状 | 归属 | 动作 | 上限 |
|---|---|---|---|
| `frames.NN.status=failed`, error 含 `timeout/Generation timed out/video_generation_failed` | 传输层 | 直接单帧重发 `/frames targets=[NN]` | ≤2 次 |
| `frames.NN.status=failed`, error 含 `auth/401/404/project/No profiles` | 后端配置 | 换后端 `backend=gflow↔zexapi` 单帧重发；仍挂就回对话请示人 | 换后端 1 次 |
| 语义不合格（EYES_CHECKS 挑出问题）：串脸/换衣/换道具/畸形手 | prompt 层 | 改 `ref_mode`（默认→`anchor_only` 或 `prev_only`）或补强 `subject_bible`；单帧重发 | ≤2 次 |
| 语义不合格：产品外观漂了/部件错/印刷乱 | 参考图层 | 让 video-ui 的 `product_card` 重跑一遍（`/product_card` 更新基准）+ 单帧重发 | 1 次 |
| 语义不合格：not_present 帧泄露了产品 | 剧本层 | 校对剧本 `product_presence` 字段；必要时改结构、重跑 script gate | 1 次 |
| 段生成 `segment_status.N.status=failed` 且已走完降级链 | 审核拦截（omni moderation） | **改运镜 prompt**（软化措辞/去掉公众人物/去掉冲突场景）——不改参数（product-video-pipeline SKILL.md §失败语义） | ≤3 次 |
| `selfcheck.watermark=fail`（region_diff 未生效）| 后期层 | 重发 `/finalize`；再挂就请示人 | 1 次 |
| `selfcheck.audio=fail` (> -55dB 静音) | 生成层 | 回 Stage4 改运镜 prompt 里口播部分；Stage5 单段重跑；Stage6 | ≤2 次 |

**每次自愈前先读 `job.active`**；单帧/单段"重跑"都是异步提交，按 §1 幂等协议。

**预算护栏**：
- 单个 run 总重跑次数上限 8（跨阶段合计），超了就停手、把当前状态打包给人。
- 视频段生成每段上限 5 次尝试（含 video-ui 内部降级链）。

---

## 5. 请示人（只在 3 个关口）

**审核点的图/片怎么交付进对话——已实测（2026-09-30）：**
- **主通道 = markdown 内联 http 图**：agent 在对话正文写 `![帧N](http://127.0.0.1:8765/files/<run>/frames/thumb/frame_NN.jpg)`，DSH 桌面端**直接渲染、永远可见、无需展开**（实测缩略 jpg + 原图 png 都显示；`/files` HTTP 200 已验）。审帧就贴这一排缩略（640px 看构图/脸/多余文字够用），产品 `focus` 帧**额外贴一张原图 png**（看印刷字）。
- **要"点开放大"再叠 `read_image`**：agent 调 `read_image` 工具读原图 → 对话里出**画廊缩略 + 灯箱全尺寸**（`read-image.ts:192` → `ToolRow.tsx:253-269`）；⚠️ 该图在工具卡里**默认折叠**，客户点展开才见。所以放大用 read_image，日常总览用 markdown http，两者互补。
- **成片 = `present(final_clean.mp4)`**：对话尾挂交付物卡片，客户点卡片 → 右栏 **video 预览**播放（mp4/webm 等补丁已装）。present 不内联、不自动开面板。
- ❌ 死路：markdown 本地绝对路径 / `file:///` / `C:\` / `dsh-app://…/api/file` 在桌面端**一律不渲染**；`data:` URI 被 sanitize。**别用这些**。

**三个关口模板：**
1. **方案 + 分镜脚本**(Stage1 后)：**必须把整张逐拍分镜表摊开给人审，不是只给摘要**。markdown 表格列：拍号/所属段 · beat_function · 景别与运镜动机(shot_motivation) · 画面(story_beat) · 台词(dialogue，目标语言) · 时长 · keyframe 画面要点。表头之上再给一句 global_arc / 情感核心 / 产品第几拍入场。回"通过 / 改第 N 拍(说改哪)"。人点头才 `POST /gate{script}`。
2. **审帧**(Stage2 后，Gate3 前)：贴每段 6 帧的 markdown http 缩略 + focus 帧原图；不合格的**先由 agent 按 §4 自己修到全合格**再请示，请示时说明"已自修 N 帧"。客户可"通过" / "重拍第__帧(说问题)" / "要放大看第__帧"(→ read_image)。
3. **成片验收**(Stage6 selfcheck 后)：`present(final_clean.mp4)` + selfcheck 摘要 + agent 的 EYES 结论 + 已知风险。客户点了播放看完再批。

闸门由 agent 通过 `POST /gate` 打，但打之前**必须先拿到人在对话里的"通过"**。不要每段每帧每 chat 问一次。

> 反馈闭环：客户说"帧4重拍/段2运镜放缓"→ agent 映射 `POST /frames {targets:[4]}` / 改 `camera[2]` 再 `/segments`。只回看改动那一帧（delta 复审），不重发整排。

---

## 6. 已知坑与不确定的（落地前必须先处理）

⚠️ **story 模式 Stage0 有真 bug**：`do_asset_card`（app.py:443/453/463）调 `entry.set_current(...)`，但 `entry` 是 dict、没有该方法 → 静默 AttributeError、每拍角色/道具特征卡其实没写成功。**产品模式不受影响**（走 `do_product_card`）。落地前先修这个（或绕开：story 用 `product_card` 那路的替身）。

⚠️ **Read 能否直读 run 目录**：run 目录在 `VIDEO_UI_RUNS`（本机 = `C:\dsh\video_runs`）。同机理论上 Read 能读；跨源或盘权限变了就要走 `/files` HTTP。**动手前先实测一张帧缩略能不能被 Read 直接看见**。

⚠️ **gflow_project 归属**：video-ui `genimg.gflow_generate` 必须传 `--project`；且**必须是本账号自有的**项目 id（0f474d52-… 是外账号的 → 404 → rc=23）。config 已改 `75cd0b95-…`。以后换 Google 账号一定要同步换 config 里的 project id。

⚠️ **agent 不能并发驱动多个 run**：video-ui 单 worker 串行；如果客户提批量，agent 只能排队做，或者由 video-ui 那边扩成多 worker（改代码，本契约不主张）。

---

## 7. 与既有 SKILL 条款的关系（不重复造）

`product-video-pipeline` SKILL.md 里那些 VUI **不编码**的规则继续由 agent 履行（不改）：
- 叙事结构选型（悬念揭秘/痛点/情感/UGC/质感/对比反转）；
- 故事优先三铁律（首 1.2s 不出现产品 · 剧情需要时才入场 · 情绪高点归人）；
- 产品保真 3 层（参考图/文字 parts 清单/角度 ≤45° · 尺寸比例 · 材质锁）；
- 审核软化经验（公众人物假阳性→遮脸优先，不轻易降 veo）；
- 批准礼仪 · 账号记忆 · 成本/平台 · DELIVERABLES 档案层。

VUI **已经编码**的（prompts.py 里的 SCRIPT_SCHEMA / CONSISTENCY_CLAUSE / NOT_PRESENT_CLAUSE / CAST_LOCK_CLAUSE / reference_plan 铁律 / camera_sanity / selfcheck）**不要**在 skill 再抄一遍——只留一句"由 video-ui 保证"。

---

## 8. 交付档案层落点
每跑一条 run，agent 在 `productions/<task>/DELIVERABLES.md` 追加一条指针，字段按 SKILL.md 的 §交付档案层。**产物本体（png/mp4）留在 video-ui run 目录，不搬进 productions**（几百 MB 会炸目录）。

---

## 9. 取向定稿（2026-09-30 拍板）

1. **审帧措辞 = agent 先自修到合格，再给人看**。✅ 定：agent 把能自己判/自己修的（串脸、漏产品、畸形手、后端 auth、传输 timeout）先重跑修好，**只把"改无可改"或"需要产品方事实判断"（品牌正确拼法、卖点真伪）摆给人**。理由：人做判断不做体力；代价是可能"过滤过头"，故请示时**必须附 agent 已修 N 帧的清单**，人可要求看原图回退。
2. **看图通道 = 原生 Read 直读 run 目录（本机 `C:\dsh\video_runs`）为主**。✅ 定：agent 语义质检用原生 Read 看 png/jpg（免费、无往返）；给人看走 markdown `http://…/files/…`（已实测桌面端渲染）。`video.analyze` 只兜底看整片 mp4。
3. **段级 VLM 二次意见 = 默认关，仅 selfcheck 出现 warn/fail 且 Read 也判不准时才开**。✅ 定：省 6 积分桩 + 240s 往返；日常语义判断靠 agent 原生 Read 看图，VLM 是最后兜底，不是每帧调。
4. **批量并发 = 本阶段承认单 worker 串行，多 run 排队，不给 video-ui 加多 worker**。✅ 定：并发是引擎级改造、风险高，1.0 不做；要提速走"多起 video-ui 实例（各自端口+RUNS_ROOT）"，不改代码。
5. **story Stage0 特征卡 bug = 已修**（app.py:443/449/463 `entry.set_current`→`entry["current"]`、去掉非法 `log=`），实测主角 char1 能写出完整特征卡。✅ 关闭。

---

**下一步（等你 review 完 §9）**：
- 定 §9 五个点 → 我把契约正式并进 `omni/SKILL.md` 的"驱动 video-ui"节 + 顺手修 story bug + 做一次端到端实测（Read 帧缩略 / gflow 单帧 / selfcheck eyes 补判）验证 §3/§6。
