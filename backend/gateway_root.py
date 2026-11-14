r"""WEEKSIR 统一流水线网关（试点·暂存草稿）

目的：把 hypit 挂进单端口 :8000/hypit，验证 ADR-C 的四件事——
单端口 / 路径前缀 / 同源免 CORS / 前端 base-path 感知。
见 docs/ADR-pipeline-server.md「可行性验证」「迁移分阶段」。

⚠️ 本文件最终落位 D:\video-skill\gateway.py（不是这里）。
   必须从 D:\video-skill 目录启动，否则 `from config import settings` /
   `from backend.services...` 导不到（8090 也是这么起的）。
启动：
    cd /d/video-skill
    python -m uvicorn gateway:gw --host 127.0.0.1 --port 8000
验证：
    GET http://127.0.0.1:8000/hypit/hypit            → hypit 原生页（注意是双 hypit）
    GET http://127.0.0.1:8000/hypit/api/hypit/env    → 同源 JSON，无 CORS 头也拿得到
    （iframe 与 fetch 都在 :8000 这一 origin → 门控 fetch 天然通）

关键：hypit_router 自身路由是 `/hypit`(页) 与 `/api/hypit/*`；include_router(prefix="/hypit")
后变 `/hypit/hypit` 与 `/hypit/api/hypit/*`。前端 hypit.html 已用 resolveUrl() 按页目录推导 base，
故绝对路径 `/api/hypit/*` 会自动解析成 `/hypit/api/hypit/*`——同一份文件在 :8090 与网关都成立。
"""
from fastapi import FastAPI

from backend.services.hypit_workspace import router as hypit_router

gw = FastAPI(title="WeekSir Pipeline Gateway (pilot)")

# 试点第一条线：hypit（纯 FastAPI，不涉 a2wsgi）
gw.include_router(hypit_router, prefix="/hypit")

# 后续按 ADR 分期再加（video-ui 需 a2wsgi 挂 Flask，排在 hypit 跑通之后）：
# from a2wsgi import WSGIMiddleware
# from app_video_ui import app as video_ui_flask   # :8765 那个 Flask
# gw.mount("/omni", WSGIMiddleware(video_ui_flask))
# gw.include_router(super_clone_router, prefix="/clone")


@gw.get("/")
def _root():
    return {
        "service": "weeksir-pipeline-gateway",
        "pipelines": {"hypit": "/hypit  (page=/hypit/hypit, api=/hypit/api/hypit/*)"},
        "note": "pilot: hypit only; omni/clone mounts are staged per ADR",
    }
