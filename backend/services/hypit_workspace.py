"""Hypit 工作台：视频项目、Runtime、Studio 与 CLI 封装。"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from config import settings

try:
    from . import hypit_agent  # noqa: F401  (内置 agent，按需调用)
except Exception as _agent_import_err:  # pragma: no cover
    hypit_agent = None  # type: ignore
    _AGENT_IMPORT_ERR = str(_agent_import_err)
else:
    _AGENT_IMPORT_ERR = None

router = APIRouter()

WORKSPACE = Path(__file__).resolve().parents[2] / "hypit-workspace"
FRONTEND = Path(settings.FRONTEND_DIR)
STUDIO_PORT = 5179
TEXT_EXTS = {".md", ".json", ".svml", ".svs", ".svrun", ".txt", ".html", ".css", ".js", ".ts"}
MEDIA_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".mp3", ".wav", ".m4a", ".jpg", ".jpeg", ".png", ".webp", ".gif"}
WRITE_EXTS = {".md", ".json", ".svml", ".svs", ".svrun", ".txt"}
SKIP_DIRS = {".git", "node_modules", "runtimes", "__pycache__"}

_jobs: dict[str, dict] = {}
_studio: dict[str, Any] = {"proc": None, "url": "", "run": "", "started_at": "", "log": []}


def _hypit_bin() -> str:
    path = shutil.which("hypit")
    if not path:
        raise HTTPException(500, "未找到 hypit 可执行程序。请先执行 npm install -g @hypit/hypit")
    return path


def _ensure_workspace() -> Path:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    for name in ("assets", "references", "productions", "packages"):
        (WORKSPACE / name).mkdir(exist_ok=True)
    pkg = WORKSPACE / "package.json"
    if not pkg.exists():
        pkg.write_text(
            json.dumps(
                {
                    "name": "hypit-workspace",
                    "version": "0.0.0",
                    "private": True,
                    "type": "module",
                    "description": "Hypit video project for AI Video Studio",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return WORKSPACE


def _safe_rel(rel: str) -> Path:
    if not rel or rel.strip() in (".", "/"):
        raise HTTPException(400, "路径无效")
    raw = rel.replace("\\", "/").lstrip("/")
    if ".." in Path(raw).parts:
        raise HTTPException(400, "不允许跳出工作区")
    root = _ensure_workspace().resolve()
    path = (root / raw).resolve()
    if path != root and root not in path.parents:
        raise HTTPException(400, "路径超出工作区")
    return path


def _rel_of(path: Path) -> str:
    return path.resolve().relative_to(_ensure_workspace().resolve()).as_posix()


def _slug(name: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", (name or "").strip(), flags=re.UNICODE)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return (s or "untitled")[:64]


def _read_text(path: Path, limit: int = 200_000) -> str:
    data = path.read_bytes()
    if len(data) > limit:
        data = data[:limit]
    return data.decode("utf-8", errors="replace")


def _first_line(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            text = line.strip().lstrip("#").strip()
            if text:
                return text
    except OSError:
        return ""
    return ""


def _skill_info() -> dict:
    repo_root = Path(__file__).resolve().parents[2]
    candidates = [
        Path.home() / ".grok" / "skills" / "hypit" / "SKILL.md",
        repo_root / ".grok" / "skills" / "hypit" / "SKILL.md",
        Path.home() / ".agents" / "skills" / "hypit" / "SKILL.md",
    ]
    seen = set()
    found = []
    for p in candidates:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        found.append({"path": key, "exists": p.exists()})
    return {"installed": any(x["exists"] for x in found), "locations": found}


async def _run_hypit(
    args: list[str],
    timeout: int = 60,
    json_mode: bool = False,
    workspace: bool = True,
) -> dict:
    _ensure_workspace()
    cmd = [_hypit_bin(), *args]
    if workspace:
        cmd += ["--workspace", str(WORKSPACE)]
    if json_mode and "--json" not in args:
        cmd.append("--json")
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(WORKSPACE),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        raise HTTPException(504, f"hypit {' '.join(args)} 超时（{timeout}s）")
    except FileNotFoundError:
        raise HTTPException(500, "无法启动 hypit")
    text = (stdout or b"").decode("utf-8", errors="replace")
    payload: Any = None
    if json_mode:
        stripped = text.strip()
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            try:
                payload = json.loads(stripped[start : end + 1])
            except json.JSONDecodeError:
                payload = None
    return {
        "ok": proc.returncode == 0,
        "code": proc.returncode,
        "cmd": cmd,
        "output": text,
        "data": payload,
    }


def _dir_summary(kind: str) -> list[dict]:
    root = _ensure_workspace() / kind
    if not root.exists():
        return []
    items = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        files = []
        media = []
        runs = []
        for p in child.rglob("*"):
            if not p.is_file():
                continue
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            rel = _rel_of(p)
            files.append(rel)
            if p.suffix.lower() in MEDIA_EXTS:
                media.append(rel)
            if p.suffix.lower() == ".svrun":
                runs.append(rel)
        brief = child / "BRIEF.md"
        analysis = child / "ANALYSIS.md"
        items.append(
            {
                "slug": child.name,
                "path": _rel_of(child),
                "title": _first_line(brief) or _first_line(analysis) or child.name,
                "brief": _first_line(brief),
                "updated_at": datetime.fromtimestamp(child.stat().st_mtime).isoformat(timespec="seconds"),
                "files": len(files),
                "media": media,
                "runs": runs,
            }
        )
    return items


def _tree(max_entries: int = 400) -> list[dict]:
    root = _ensure_workspace()
    entries = []
    for p in sorted(root.rglob("*"), key=lambda x: str(x).lower()):
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        if p.name.startswith(".") and p.is_dir() and p.name not in {".hypit"}:
            continue
        rel = _rel_of(p)
        if rel.startswith(".hypit/runtimes"):
            continue
        entries.append(
            {
                "path": rel,
                "name": p.name,
                "dir": p.is_dir(),
                "size": p.stat().st_size if p.is_file() else 0,
                "ext": p.suffix.lower() if p.is_file() else "",
            }
        )
        if len(entries) >= max_entries:
            break
    return entries


class ProductionCreate(BaseModel):
    name: str
    brief: str = ""
    reference_slug: str = ""


class FileWrite(BaseModel):
    path: str
    content: str


class CliRequest(BaseModel):
    action: str
    target: str = ""
    extra: list[str] = []


class StudioStart(BaseModel):
    run: str
    port: int = STUDIO_PORT


@router.get("/hypit", response_class=HTMLResponse)
async def hypit_page():
    html_path = FRONTEND / "hypit.html"
    if not html_path.exists():
        raise HTTPException(404, "Hypit 工作台页面未找到")
    return HTMLResponse(
        content=html_path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"},
    )


@router.get("/api/hypit/env")
async def hypit_env():
    _ensure_workspace()
    version = await _run_hypit(["version"], json_mode=True, workspace=False)
    paths = await _run_hypit(["paths"], json_mode=True)
    profile = WORKSPACE / "hypit.runtime.json"
    selection = WORKSPACE / ".hypit" / "runtime"
    return {
        "success": True,
        "workspace": str(WORKSPACE),
        "skill": _skill_info(),
        "version": version.get("data") or {"output": version["output"], "ok": version["ok"]},
        "paths": paths.get("data") or {"output": paths["output"], "ok": paths["ok"]},
        "runtime": {
            "profile": str(profile) if profile.exists() else "",
            "selected": selection.read_text(encoding="utf-8").strip() if selection.exists() else "",
            "ready": profile.exists() and selection.exists(),
        },
        "studio": {
            "running": bool(_studio.get("proc") and _studio["proc"].returncode is None),
            "url": _studio.get("url") or "",
            "run": _studio.get("run") or "",
        },
        "hint": (
            "在本仓库对 Grok 说：/hypit 用这个参考视频做一条新片：<参考路径>。"
            f" 项目在 {WORKSPACE}。"
        ),
    }


@router.get("/api/hypit/projects")
async def hypit_projects():
    _ensure_workspace()
    return {
        "success": True,
        "workspace": str(WORKSPACE),
        "productions": _dir_summary("productions"),
        "references": _dir_summary("references"),
        "tree": _tree(),
    }


@router.post("/api/hypit/productions")
async def hypit_create_production(req: ProductionCreate):
    slug = _slug(req.name)
    dest = _ensure_workspace() / "productions" / slug
    if dest.exists():
        raise HTTPException(409, f"工程已存在：{slug}")
    for sub in ("authors", "recipes", "runs", "assets", "drafts"):
        (dest / sub).mkdir(parents=True, exist_ok=True)
    brief = (req.brief or "").strip() or f"用 Hypit 制作「{req.name}」。"
    ref_line = ""
    if req.reference_slug:
        ref_path = WORKSPACE / "references" / _slug(req.reference_slug)
        if ref_path.exists():
            rel = _rel_of(ref_path)
            ref_line = f"\n- 参考档案：`{rel}`\n"
    dest.joinpath("BRIEF.md").write_text(
        f"# {req.name.strip() or slug}\n\n"
        f"## Goal\n{brief}\n"
        f"{ref_line}"
        "\n## Constraints\n- 待补充平台、时长、画幅与必须保留的事实。\n\n"
        "## Paid scope\n- 尚未约定。Hypit 框架免费；生成走已连接账号。\n",
        encoding="utf-8",
    )
    dest.joinpath("TREATMENT.md").write_text(
        f"# Treatment\n\n导演方案待 Agent 在理解参考片 / Brief 后写入。\n",
        encoding="utf-8",
    )
    dest.joinpath("PROGRESS.md").write_text(
        "# Progress\n\n"
        "- Live question: 等待参考理解或素材生成\n"
        "- Next: 在 Grok 中执行 `/hypit`，指向本工程与参考素材\n",
        encoding="utf-8",
    )
    return {"success": True, "slug": slug, "path": _rel_of(dest)}


@router.post("/api/hypit/upload")
async def hypit_upload(
    kind: str = Form(...),
    slug: str = Form(""),
    file: UploadFile = File(...),
):
    if not file.filename:
        raise HTTPException(400, "文件名为空")
    _ensure_workspace()
    kind = kind.strip().lower()
    stem = Path(file.filename).stem
    ext = Path(file.filename).suffix.lower() or ""
    if kind == "reference":
        name = _slug(slug or stem)
        folder = WORKSPACE / "references" / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "evidence").mkdir(exist_ok=True)
        dest = folder / f"source{ext or '.mp4'}"
        if not (folder / "ANALYSIS.md").exists():
            folder.joinpath("ANALYSIS.md").write_text(
                f"# Analysis\n\n参考片 `{file.filename}` 的整片理解待 Agent 写入。\n",
                encoding="utf-8",
            )
        if not (folder / "TIMELINE.md").exists():
            folder.joinpath("TIMELINE.md").write_text(
                "# Timeline\n\n带时间的细节与功能待 Agent 在看片后写入。\n",
                encoding="utf-8",
            )
    elif kind == "asset":
        dest = WORKSPACE / "assets" / Path(file.filename).name
    elif kind == "production-asset":
        name = _slug(slug)
        if not name:
            raise HTTPException(400, "production-asset 需要 slug")
        folder = WORKSPACE / "productions" / name / "assets"
        if not folder.parent.exists():
            raise HTTPException(404, f"工程不存在：{name}")
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / Path(file.filename).name
    else:
        raise HTTPException(400, "kind 必须是 reference / asset / production-asset")
    content = await file.read()
    dest.write_bytes(content)
    return {
        "success": True,
        "path": _rel_of(dest),
        "size": len(content),
        "filename": dest.name,
        "kind": kind,
        "slug": dest.parent.name if kind != "asset" else "",
    }


@router.get("/api/hypit/file")
async def hypit_read_file(path: str):
    target = _safe_rel(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "文件不存在")
    if target.suffix.lower() in TEXT_EXTS:
        return {"success": True, "path": _rel_of(target), "text": True, "content": _read_text(target)}
    return {"success": True, "path": _rel_of(target), "text": False, "url": "/api/hypit/media?path=" + _rel_of(target)}


@router.put("/api/hypit/file")
async def hypit_write_file(req: FileWrite):
    target = _safe_rel(req.path)
    if target.suffix.lower() not in WRITE_EXTS:
        raise HTTPException(400, f"不允许写入 {target.suffix or '无扩展名'} 文件")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(req.content, encoding="utf-8")
    return {"success": True, "path": _rel_of(target), "bytes": target.stat().st_size}


@router.get("/api/hypit/media")
async def hypit_media(path: str):
    target = _safe_rel(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(target)


def _cli_args(req: CliRequest) -> tuple[list[str], int, bool, bool]:
    action = (req.action or "").strip().lower()
    target = (req.target or "").strip()
    mapping = {
        "version": (["version"], 30, True, False),
        "paths": (["paths"], 30, True, True),
        "doctor": (["doctor"], 180, True, True),
        "runtime-status": (["runtime", "status"], 30, True, True),
        "runtime-init": (["runtime", "init"], 30, False, True),
        "runtime-up": (["runtime", "up"], 600, False, True),
        "runtime-down": (["runtime", "down"], 60, False, True),
        "runtime-logs": (["runtime", "logs"], 30, False, True),
        "builds": (["builds"], 30, True, True),
        "activity": (["activity"], 30, True, True),
        "auth-status": (["auth", "status"], 30, True, True),
        "vocabulary": (["vocabulary"], 30, False, False),
    }
    if action in mapping:
        return mapping[action]
    if not target:
        raise HTTPException(400, f"{action} 需要 target")
    rel = _safe_rel(target) if action in {"check", "plan", "pricing", "build", "studio"} else None
    if action == "check":
        return (["check", str(rel)], 60, False, True)
    if action == "plan":
        return (["plan", str(rel)], 120, False, True)
    if action == "pricing":
        return (["pricing", str(rel)], 120, False, True)
    if action == "build":
        return (["build", str(rel), "--follow"], 3600, False, True)
    if action in {"status", "inspect", "logs", "cancel"}:
        if not re.fullmatch(r"[A-Za-z0-9._:-]{4,128}", target):
            raise HTTPException(400, "build-id 非法")
        return ([action, target], 60, True, True)
    raise HTTPException(400, f"不支持的 action：{action}")


@router.post("/api/hypit/cli")
async def hypit_cli(req: CliRequest):
    args, timeout, json_mode, workspace = _cli_args(req)
    long_running = timeout >= 120
    if not long_running:
        result = await _run_hypit(args, timeout=timeout, json_mode=json_mode, workspace=workspace)
        return {"success": result["ok"], **result}

    job_id = uuid.uuid4().hex[:10]
    _jobs[job_id] = {
        "id": job_id,
        "action": req.action,
        "status": "running",
        "output": "",
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "code": None,
    }

    async def _worker():
        job = _jobs[job_id]
        try:
            result = await _run_hypit(args, timeout=timeout, json_mode=json_mode, workspace=workspace)
            job.update(
                {
                    "status": "done" if result["ok"] else "failed",
                    "output": result["output"],
                    "code": result["code"],
                    "data": result.get("data"),
                }
            )
        except HTTPException as e:
            job.update({"status": "failed", "output": str(e.detail), "code": e.status_code})
        except Exception as e:
            job.update({"status": "failed", "output": str(e), "code": 1})

    asyncio.create_task(_worker())
    return {"success": True, "job_id": job_id, "status": "running"}


@router.get("/api/hypit/jobs/{job_id}")
async def hypit_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return {"success": True, **job}


@router.post("/api/hypit/studio/start")
async def hypit_studio_start(req: StudioStart):
    run_path = _safe_rel(req.run)
    if not run_path.exists():
        raise HTTPException(404, f"Run 不存在：{req.run}")
    if run_path.suffix.lower() != ".svrun":
        raise HTTPException(400, "Studio 需要 .svrun")
    await hypit_studio_stop()
    port = int(req.port or STUDIO_PORT)
    cmd = [
        _hypit_bin(),
        "studio",
        "--run",
        str(run_path),
        "--workspace",
        str(WORKSPACE),
        "--port",
        str(port),
    ]
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess_no_window()
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(WORKSPACE),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
        creationflags=creationflags,
    )
    _studio.update({"proc": proc, "url": "", "run": _rel_of(run_path), "started_at": datetime.now().isoformat(timespec="seconds"), "log": []})
    deadline = time.time() + 25
    url = f"http://127.0.0.1:{port}"
    while time.time() < deadline:
        if proc.returncode is not None:
            rest = b""
            if proc.stdout:
                rest = await proc.stdout.read()
            text = rest.decode("utf-8", errors="replace")
            _studio["log"].append(text)
            raise HTTPException(500, "Studio 启动失败：\n" + text[-1500:])
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=0.4)
        except asyncio.TimeoutError:
            line = b""
        if line:
            text = line.decode("utf-8", errors="replace").rstrip()
            _studio["log"].append(text)
            match = re.search(r"https?://[^\s]+", text)
            if match:
                url = match.group(0).rstrip(".,)")
                _studio["url"] = url
                break
            if "Studio" in text and "http" in text:
                _studio["url"] = url
                break
        else:
            await asyncio.sleep(0.2)
    if not _studio.get("url"):
        _studio["url"] = url
    return {"success": True, "url": _studio["url"], "run": _studio["run"], "log": _studio["log"][-20:]}


def subprocess_no_window() -> int:
    return getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0)


@router.post("/api/hypit/studio/stop")
async def hypit_studio_stop():
    proc = _studio.get("proc")
    if proc and proc.returncode is None:
        try:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
        except Exception:
            pass
    _studio.update({"proc": None, "url": "", "run": "", "log": []})
    return {"success": True}


@router.get("/api/hypit/studio/status")
async def hypit_studio_status():
    proc = _studio.get("proc")
    running = bool(proc and proc.returncode is None)
    return {
        "success": True,
        "running": running,
        "url": _studio.get("url") or "",
        "run": _studio.get("run") or "",
        "log": _studio.get("log") or [],
    }


# ═══════════════════════════════════════
# 内置 Agent：codex+skill 模式
# LLM 加载 hypit skill 作为系统提示，发起 tool_call；前端批准后执行；结果回灌继续推理。
# ═══════════════════════════════════════


class AgentChatRequest(BaseModel):
    """请求体：用户发起新一轮对话。

    messages 必须包含完整历史（system / user / assistant / tool）。
    production 用于把当前工程名注入 system 提示（仅当 messages 不含 system 时生效）。
    """
    messages: list[dict]
    production: str = ""


class AgentExecuteRequest(BaseModel):
    """请求体：用户批准了一次 tool_call，要求执行并继续推理。

    messages 必须包含到 assistant tool_calls 消息为止的完整历史。
    """
    messages: list[dict]
    tool_call_id: str
    name: str
    arguments: str  # 原始 JSON 字符串，避免客户端序列化差异
    production: str = ""


def _ensure_agent() -> None:
    if hypit_agent is None:
        raise HTTPException(500, f"内置 agent 加载失败：{_AGENT_IMPORT_ERR}")
    if shutil.which("hypit") is None:
        raise HTTPException(500, "未找到 hypit 可执行程序。请先执行 `npm install -g @hypit/hypit`。")


@router.get("/api/hypit/agent/skill")
async def hypit_agent_skill():
    """返回当前加载的 SKILL.md 文本（调试 / 信息用）。"""
    _ensure_agent()
    text = hypit_agent._load_skill_text()
    return {
        "success": True,
        "length": len(text),
        "text": text,
        "workspace": str(WORKSPACE),
        "primary_engine": hypit_agent.PRIMARY,
        "fallback_engine": hypit_agent.FALLBACK,
    }


@router.post("/api/hypit/agent/chat")
async def hypit_agent_chat(req: AgentChatRequest):
    """SSE：跑一轮 agent。messages 是完整历史；返回 delta / tool_call / done 事件。"""
    _ensure_agent()
    messages = list(req.messages or [])
    production = (req.production or "").strip()

    async def stream():
        try:
            async for frame in hypit_agent.run_agent_turn(messages, production=production):
                yield frame
        except Exception as e:
            err = json.dumps({"message": str(e)[:1500]}, ensure_ascii=False)
            yield f"event: error\ndata: {err}\n\n"
            yield f"event: done\ndata: {json.dumps({'finish_reason': 'error', 'needs_approval': False}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/api/hypit/agent/execute")
async def hypit_agent_execute(req: AgentExecuteRequest):
    """SSE：执行用户批准的 tool_call，把结果回灌后继续 agent 推理。

    流程：
      1. 执行工具 → tool_result 事件
      2. 把 {role: tool, tool_call_id, content: json(result)} 追加到 messages
      3. 再跑一轮 agent（run_agent_turn），流式输出后续 delta / tool_call
    """
    _ensure_agent()
    messages = list(req.messages or [])
    # 1) 执行工具
    result = await hypit_agent.execute_approved_tool(
        req.tool_call_id,
        req.name,
        req.arguments,
    )
    # 2) 追加 tool 消息
    messages.append(
        {
            "role": "tool",
            "tool_call_id": req.tool_call_id,
            "content": json.dumps(result, ensure_ascii=False),
        }
    )

    async def stream():
        # 先发 tool_result
        yield (
            "event: tool_result\ndata: "
            + json.dumps(result, ensure_ascii=False)
            + "\n\n"
        )
        # 再流式跑下一轮
        try:
            async for frame in hypit_agent.run_agent_turn(messages, production=(req.production or "").strip()):
                yield frame
        except Exception as e:
            err = json.dumps({"message": str(e)[:1500]}, ensure_ascii=False)
            yield f"event: error\ndata: {err}\n\n"
            yield f"event: done\ndata: {json.dumps({'finish_reason': 'error', 'needs_approval': False}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
