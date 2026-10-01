# gateway-pilot 暂存件·落回清单

因 `D:` 盘 100% 满（0 空闲），hypit 试点网关的两处改动先暂存在 `C:\dsh\scratch\gateway-pilot\`。
**等 D: 腾出空间后**按此落回。当前 D: 未动，super-clone/hypit 仍跑在 :8090。

## 暂存物
- `gateway.py` → 落位 `D:\video-skill\gateway.py`（新建，网关入口）
- `hypit.html` → **覆盖** `D:\video-skill\frontend\hypit.html`（已加 `resolveUrl()` base-path 感知；在 :8090 与网关都成立）

## 落回步骤（D: 有空间后）
```bash
# 0) 确认 D: 有余量
df -h /d

# 1) 备份原前端（可回滚）
cp /d/video-skill/frontend/hypit.html /d/video-skill/frontend/hypit.html.bak

# 2) 落回两文件
cp /c/dsh/scratch/gateway-pilot/gateway.py /d/video-skill/gateway.py
cp /c/dsh/scratch/gateway-pilot/hypit.html /d/video-skill/frontend/hypit.html

# 3) 起网关（必须 cd 到 D:\video-skill，否则 import 不到 config/backend）
cd /d/video-skill
python -m uvicorn gateway:gw --host 127.0.0.1 --port 8000
```

## 验证（网关起来后）
- `curl http://127.0.0.1:8000/` → 返回网关 JSON（pipelines.hypit）
- 浏览器开 `http://127.0.0.1:8000/hypit/hypit` → hypit 原生页正常；F12 Network 里
  `env`/`projects` 等应打到 `/hypit/api/hypit/*` 且 **200 同源、无 CORS 报错**。
- 若页能打、但 `fetch` 404/仍打 `/api/hypit/*`（没带 /hypit 前缀）→ resolveUrl 没生效，查 `location.pathname`。

## 插件切换（网关验证通过后，改 C:\dsh 侧）
`plugins/ops-gateway/lib/client.js` 里把 hypit 两条常量从 :8090 改指网关：
```js
const HYPIT_ORIGIN   = "http://127.0.0.1:8000/hypit";     // 原 http://127.0.0.1:8090
const HYPIT_VIEW_URL = HYPIT_ORIGIN + "/hypit";           // → :8000/hypit/hypit
// 门控 fetch(HYPIT_ORIGIN + "/api/hypit/projects") 自动变同源
// ⚠️ 网关独立进程有自己那份 _jobs/_studio，别和 8090 那份混用同一 job/studio
```
改后 DSH 重载插件；建一个带 session 的 hypit 工程 → Hypit tab 才弹（还需 production 带 session 字段，见契约 §7 / 该端点走网关后同源可直读 projects，session 过滤仍待 8090 侧补 `session`）。

## 回滚
网关有问题 → `cp hypit.html.bak hypit.html` + 删 `gateway.py`；:8090 全程没动，功能不丢。
（对应 ADR「风险/回退」：回退不丢功能。）
