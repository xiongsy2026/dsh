# deliverables 表草案（暂不落地·留档）

> 状态：**设计草案，当前不实现**。现阶段用「文件即记忆」：档案层 = `productions/<task>/DELIVERABLES.md`
> 的 run 指针，产物层 = video-ui `VIDEO_UI_RUNS/<run>/` 目录。出现下列任一需求才把指针迁进本表：
> 跨号搜索 / 积分计费对账 / 验收审计留痕 / 服务端多租户（3.0）。

DB = SQLite（DSH 内核已内置 node:sqlite；服务端多租户时换 Postgres，字段同形）。
原则：**表只存元数据+路径，视频本体永远留在产物层目录**，靠 `run` 名与 `final_path` 指向。

## 主表：deliverables
```sql
CREATE TABLE deliverables (
  id            INTEGER PRIMARY KEY,
  run           TEXT NOT NULL,            -- video-ui run 名，产物层目录名；与 run.json 对齐
  task          TEXT NOT NULL,            -- productions/<task>
  account       TEXT NOT NULL,            -- 绑定账号；查询/统计按号隔离（见 AGENTS.md 账号记忆）
  kind          TEXT NOT NULL,            -- product | story | clone(预留)
  mode          TEXT,                     -- 出片模式细节
  duration_s    REAL,
  segments      INTEGER,
  total_frames  INTEGER,
  status        TEXT NOT NULL,            -- running | awaiting_approval | approved | failed
  -- 产物层链接（只存路径，不存文件）
  final_path    TEXT,                     -- <VIDEO_UI_RUNS>/<run>/video/final.mp4
  frames_dir    TEXT,                     -- <VIDEO_UI_RUNS>/<run>/frames/
  product_card  TEXT,                     -- <VIDEO_UI_RUNS>/<run>/product_card.json
  thumb_path    TEXT,                     -- 档案层留的成片缩略（可入库，小图）
  -- 后端/模型留痕（当时用了哪条通道，便于成本核算与复现）
  chat_backend   TEXT,                    -- agnes-3.0-flash ...
  image_backend  TEXT,                    -- zexapi:nano_banana_2 | gflow
  video_backend  TEXT,                    -- omni_flash-hd-10s ...
  -- 三次批准（方案/审帧/成片）——审计与验收口径
  approved_scheme_at  TEXT,
  approved_frames_at  TEXT,
  approved_final_at   TEXT,
  approved_by         TEXT,               -- 批准人标识
  -- 计费（变现 3.0 才启用，先留桩）
  credits_cost  INTEGER,                  -- 按 PRICE 桩累计：text1/image4/video40/analyze6
  created_at    TEXT NOT NULL,            -- ISO8601
  updated_at    TEXT NOT NULL
);
CREATE INDEX idx_del_account ON deliverables(account, created_at);
CREATE INDEX idx_del_status  ON deliverables(status);
CREATE INDEX idx_del_run     ON deliverables(run);
```

## 可选附表：deliv_events（每步调用/重跑日志，给计费与排障）
```sql
CREATE TABLE deliv_events (
  id        INTEGER PRIMARY KEY,
  run       TEXT NOT NULL,               -- FK 逻辑外键 -> deliverables.run
  stage     TEXT NOT NULL,               -- script|frames|camera|segments|finalize|gate
  ts        TEXT NOT NULL,
  ok        INTEGER,                     -- 1/0
  detail    TEXT                         -- 错误摘要/重跑原因（如 "frame_12 timeout, retried"）
);
CREATE INDEX idx_evt_run ON deliv_events(run, ts);
```

## 迁移路径（真要做时）
1. 写一个一次性脚本扫 `VIDEO_UI_RUNS/*/run.json` + `productions/*/DELIVERABLES.md`，回填本表。
2. omni skill 改为**双写**（先写文件台账，稳定后再切只写 DB）。
3. `run` 保持与 video-ui 目录名一致，作为跨层主键；删 run 走 video-ui 的 `_trash`，DB 只标 `status=deleted`（软删，别硬删）。
