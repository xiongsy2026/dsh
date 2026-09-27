# PRD：Stage 三栏 UI（DSH Desktop 右栏直播看板）

状态：已拍板（三项决策锁定）  
日期：2026-09-27  
工作区：`C:\dsh`  
运行时：DSH Desktop（Harness；`dsh-client-ui-layout` 三栏 AppFrame）  
关联：[`PRD.md`](PRD.md) 难单超级复刻 · [`PRD-weeksir-platform.md`](PRD-weeksir-platform.md) 总纲 · [`AGENT-FRAMEWORK.md`](AGENT-FRAMEWORK.md)

**本切片只扩 DSH 现成件，不新建 UI 框架、不新建事件总线、不新建状态库。**

---

## 1. 一句话

左栏会话、中栏 Agent 对话保持原样；**右栏常驻 Stage 直播看板**——出图看画布、出视频看工作流并在上面完成审核；重操作只在 Stage，中栏批准气泡只读并可跳转。

---

## 2. 已拍板决策（2026-09-27）

| # | 决策 | 含义 |
|---|---|---|
| **D1** | **一进任务就常驻** | 会话绑定任务（有 `productions/<task>/`）后右栏保持打开；不按「有事才弹」 |
| **D2** | **重操作只在 Stage** | 拖切段边界、画布点选、播放器 scrub、单格/单段重做 → 只在右栏；中栏批准气泡只读摘要 +「去 Stage 处理」 |
| **D3** | **E2 视频优先** | 先做 `waveform-segments` + `frame-grid` 接 super-clone 难单；画布 `canvas-layers` 后置 |

---

## 3. 目标 / 非目标

### 目标

1. 利用 DSH 现成三栏壳与 dockkit，把右栏从「文档预览」升为生产 Stage。
2. 审核三停点在 Stage 可操作；批准流仍走 DSH `approval` / `user-questions`，不另造批准系统。
3. 实时源只认两样：`tool-jobs` 状态、`productions/<task>/` 文件变更。
4. skill 用 SKILL.md 声明默认页与视图；插件按 view 类型渲染。

### 非目标（本期不做）

- 不重写 AppFrame / 聊天循环 / tool host / 批准气泡内核
- 不新建「Stage 框架」、路由、DB、WebSocket
- 不把 Stage 做成第二个 8090 表单页
- 不做 `canvas-layers` 图片直播画布（后置）
- 不做批量任务总表（后置 E5）
- 不在 Stage 选模型、不执行生成（只展示 + 批准结论落盘）

---

## 4. 三栏职责（锁定）

```
┌─────────────┬──────────────────────────┬─────────────────────────────┐
│  左 · 会话   │      中 · Agent 对话      │       右 · Stage（常驻）      │
├─────────────┼──────────────────────────┼─────────────────────────────┤
│ 会话 / 任务  │ 思考、工具轨迹、白话方案   │ 任务条 + 页签看板            │
│ 账号绑定     │ 批准摘要卡（只读）         │ Timeline / 帧格 / 工作流     │
│ skill 入口  │ 「去 Stage 处理」跳转      │ 成片预览 · 文件树            │
│             │                          │ ★ 重操作与验收都在这里       │
└─────────────┴──────────────────────────┴─────────────────────────────┘
```

| 栏 | 放 | 不放 |
|---|---|---|
| 左 | 会话列表、workspace、账号、skill 快捷 | 生成过程 |
| 中 | 推理、轨迹、轻量摘要、跳转 | 长审核 UI、拖拽改表 |
| 右 | 直播视图 + 重操作 + 验收 | 完整聊天、模型下拉 |

---

## 5. 扩展面（只挂现成件）

实现落在现有 Cordis UI 插件（`plugins/super-clone-ui` 或同级），**不新建插件运行时**。

| 能力 | 挂接点（已有） | 本切片用法 |
|---|---|---|
| 三栏几何 / 拖宽 | `dsh-client-ui-layout` AppFrame | 不改 |
| 右栏开合 / 全屏 | `ctx.layout.openRightbar / closeRightbar / selectPanel` | 任务激活时 `openRightbar(track)` 常驻；Review 可 `fullscreen` |
| 右栏页签 | `sidebar.right.pane.tab` + `sidebar.right.pane.tab.title` | 注册 timeline / frames / workflow / review / files |
| 会话任务条 | `rightbar.session` | 任务名 · LOOP.stage · 账号 · 总进度 |
| 批准增强 | `conversation.approval.detail` | 只读摘要 + 跳 Stage 按钮 |
| 轻批准 / 追问 | 现有 `dsh-client-ui-approval`、`user-questions` | 通过 / 打回 / 选失败段 |
| 后台任务 | `tool-jobs` / `jobs-local` | 订阅段状态，不新建队列 |
| 产出文件 | `deliverables` + `sidebar.right.tab.document` | files 页兜底 |
| 品牌 / 输入条 | 现有 `sidebar.brand.*`、`conversation.input.accessory` | 保持 super-clone-ui 现状 |

**明确不碰**：`root` 槽位结构、会话存储、skill 加载器、Agent Loop、批准协议。

---

## 6. 右栏信息架构

### 6.1 常驻行为（D1）

```
会话绑定任务（session-bind 写 LOOP.md）
  → ctx.layout.openRightbar(track)          # 打开并保持
  → rightbar.session 显示任务条
  → 按 LOOP.stage 自动选中默认页
  → 切任务 / 切会话时更新绑定；无任务时右栏显示占位（工作区文件树）
```

- 默认宽度：layout 现成偏好（首开约 45%），用户可拖
- 用户手动 `closeRightbar` 后：本任务内记住偏好；**下次进入任务仍默认打开**
- 全屏：仅 Review 页 / 成片 diff 使用

### 6.2 页签（D3 本期只做加粗三页）

| 页 id | view | 数据源 | 何时显示 | 操作 |
|---|---|---|---|---|
| **timeline** | `waveform-segments` | `ANALYSIS.md` + `TIMELINE.md` | after_step1 / 待批切段 | ★ 批准点 1：拖边界、批段、改意见 |
| **frames** | `frame-grid` | `step2/frames/`、`step3/frames/` | 抽帧 / 替换后 | 单格放大、单格重做（写 PROGRESS） |
| **workflow** | `segment-graph` | `PROGRESS.md` + jobs | 并发生成中 | ★ 批准点 2：只重跑失败段 / 跳过 |
| review | `player-diff` | 成片 + 原片 | produced | ★ 批准点 3：通过 / 打回单镜 |
| files | `file-tree`（deliverables） | `productions/<task>/` | 始终可切 | 打开目录 / 下载 |

> E2 交付 **timeline + frames**；workflow 的段状态条可先以只读列表挂在 timeline 旁，重跑按钮进 E3。

### 6.3 任务条（`rightbar.session`）

```
[任务名]  stage: produce · TIMELINE 待批   账号: shop-a   [全屏] [文件]
```

点击 stage 跳到对应页；账号只读展示（隔离规则不变）。

---

## 7. 审核与批准（D2）

### 7.1 停点映射（仍只 3 类）

| 停点 | Stage（重操作） | 中栏（轻量） | 结论落盘 |
|---|---|---|---|
| ① 方案 / TIMELINE | timeline 页拖边界、勾段、备注 | `approval.detail`：段数、总时长、「去 Stage 调切段」 | `TIMELINE.md` + LOOP.approved |
| ② 失败段 | workflow 页红段：重跑该段 / 换图 / 跳过 | 摘要卡 +「去 Stage 处理」 | `PROGRESS.md` |
| ③ 成片 | review 页播放 / diff / 打回单镜 | 封面帧 + 时长 +「去 Stage 验收」 | `PROGRESS.md` / 出口 |

### 7.2 规则（锁定）

1. **重操作只在 Stage**：拖拽、scrub、画布点选、单格/单段改图 —— 中栏禁止。
2. **中栏批准气泡只读**：可「通过 / 打回」等轻选择，以及「去 Stage 处理」跳转；不可拖表。
3. **不逐 HTTP 请示**：批量一次盖一批；2s 窗多脸等合同规则仍由 skill 约束。
4. **批准 API 不改**：仍走 DSH `approval` / `user-questions`；Stage 只负责展示与收集结论，结论写入任务文件后由 skill/Agent 读取继续。
5. **误触防护**：中栏不出现「改切段」类控件。

---

## 8. 实时数据流（无新总线）

```
tool-jobs / jobs-local 事件 ──► workflow 段状态、进度条
productions/<task>/ 文件    ──► timeline / frames / files 刷新
deliverables                ──► 成片、导出可点
LOOP.md stage               ──► 右栏默认页切换
```

- 图片格：目录 watch，新 `shot_XX.png` 上格，失败标红
- 时间轴：`TIMELINE.md` 变更重渲染（提交后整表刷新即可，不做 CRDT）
- 不引入前端全局 store 服务；组件内订阅文件 / jobs 即可

---

## 9. Skill 契约扩展（仍是说明书）

`SKILL.md` 可选段，插件读它决定默认页与视图：

```yaml
stage:
  default_page: timeline
  pages:
    - id: timeline
      when: after_step1
      view: waveform-segments
      data: TIMELINE.md
      approve: true          # 批准点 1
    - id: frames
      when: after_step2
      view: frame-grid
      data: step3/frames/
      actions: [redo_shot]
    - id: workflow
      when: generating
      view: segment-graph
      data: PROGRESS.md
      actions: [retry_segment]
    - id: review
      when: produced
      view: player-diff
      data: final.mp4
      approve: true          # 批准点 3
```

- 无 `stage` 段 → 右栏仅 `files`
- view 未实现 → 降级 `file-tree`
- **不为单个 skill 做独立 UI 插件**（避免 cordis 插件拖垮 Harness）

---

## 10. 视图组件表（插件内 views 映射，非框架）

| view 字符串 | 组件职责 | E 期 |
|---|---|---|
| `file-tree` | 复用 deliverables | E0 |
| `waveform-segments` | 音频波形 + 段色条 + 脸/产品钉 + 拖边界 | **E2** |
| `frame-grid` | 分镜宫格、对比原帧/替换帧、单格放大与重做入口 | **E2** |
| `segment-graph` | 段状态列（待批/生成中/过/败）+ 失败重跑 | E3 |
| `player-diff` | 双播放器 / 对比 + 验收按钮 | E3 |
| `canvas-layers` | 图片生成直播画布 | 后置 |

全部为 React 叶子组件，`ctx.slots.register` 挂到右栏页签内容区。

---

## 11. 与既有约定的咬合

| 约定 | 本切片 |
|---|---|
| LOOP.md 中枢 | 默认页 = `LOOP.stage`；skill 不互相调 |
| 批准只 3 停点 | 不新增停点；只把重操作挪到 Stage |
| 账号记忆隔离 | 右栏只读当前会话 `productions/` |
| jobs 调 `D:\video-skill` | Stage 只展示与批准结论，不执行 |
| 文件即记忆 | 结论写 `TIMELINE.md` / `PROGRESS.md` |
| 不改生成引擎 | 纯展示 + 交互层 |

---

## 12. 分期（决策 D3：视频优先）

| 期 | 交付 | 验收 |
|---|---|---|
| **E0** | 右栏常驻策略 + `rightbar.session` 任务条 + `files` 页 | 进任务右栏保持打开；无任务占位 |
| **E1** | `conversation.approval.detail` 增强：只读摘要 + 跳 Stage | 中栏无拖拽控件；跳转进对应页 |
| **E2** | **`waveform-segments` + `frame-grid`** + super-clone `stage` 契约 | 富铁软糖：TIMELINE 在右栏拖边界并批准；帧格可看替换结果 |
| **E3** | `segment-graph` 失败重跑 + `player-diff` 验收 | 脸 C 失败只重跑第 3 段；成片在右栏验收 |
| **E4** | `canvas-layers` 图片直播 | 详情图/多角度逐格上图 |
| **E5** | 批量任务列表页 | 一次同意盖一批 |

**首发验收以 [`PRD.md`](PRD.md) 富铁软糖难单为准**（与 E2/E3 对齐）：

- TIMELINE 先审后打；边界在右栏调整，不改切法中途乱跳
- 脸 A/B/C/D 在 frames 页可辨
- 失败只动失败段
- 时长对齐原窗、原声贴回后 review 页可听可看

---

## 13. 风险

| 风险 | 处理 |
|---|---|
| 文件 watch 抖动 / 半截写 | 防抖 + 按 mtime 取完整文件；失败降级手动刷新 |
| dockkit 页签与会话切换错乱 | 页状态跟 `rightbar.session` 绑定会话 id |
| 中栏仍长出重操作 | E1 验收标准：中栏禁止 scrub/拖表 |
| 右栏常驻显拥挤 | 可拖宽 / Review 全屏；files 收进页签不占主视图 |
| 与 approval 协议双写 | 只写任务文件，批准仍走 DSH API；文档注明单一真相源=文件 |
| 插件体积 | 组件进现有 super-clone-ui（已 slim），不新开 cordis 大包 |

---

## 14. 打开清单（实现前）

1. 确认 DSH 桌面端 slot 行为与 0.1.5-rc.2 一致（`rightbar` / `rightbar.session` / `approval.detail`）
2. 在 `super-clone-ui` 内建 `views/` 与 `stage` 契约读取（仅 SKILL.md 字段）
3. 先 E0 常驻右栏 → E1 批准增强 → E2 视频两视图
4. 每期用富铁软糖或等价难单回归

---

## 15. 决策记录

| 日期 | 决策 | 备注 |
|---|---|---|
| 2026-09-27 | D1 右栏一进任务常驻 | 用户确认 |
| 2026-09-27 | D2 重操作只在 Stage，中栏只读+跳转 | 用户确认 |
| 2026-09-27 | D3 优先 E2 视频（timeline + frames） | 用户确认；画布后置 |
