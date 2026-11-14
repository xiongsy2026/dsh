"""Hypit 内置 Agent：把 hypit skill 装进 LLM 系统提示，把 CLI / 文件 / Studio 操作暴露为可批准的工具。

设计：codex+skill 模式
  - LLM 接收完整 SKILL.md 作为系统提示，再叠加工作区上下文与当前工程。
  - LLM 可发起 tool_call（一次一个），前端弹出批准卡片，用户点击后才执行。
  - 工具执行结果回灌给 LLM，继续推理直到不再发起 tool_call。
  - 全程 SSE 流式：delta / tool_call / tool_result / error / done。

LLM 引擎：GPT-5.6 主，Grok-4.6 备（502/524/连接失败时切换）。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from config import settings

# ── 工作区 & skill 路径 ──
WORKSPACE = Path(__file__).resolve().parents[2] / "hypit-workspace"

SKILL_CANDIDATES = [
    Path.home() / ".agents" / "skills" / "hypit" / "SKILL.md",
    Path.home() / ".grok" / "skills" / "hypit" / "SKILL.md",
    Path.home() / ".claude" / "skills" / "hypit" / "SKILL.md",
    Path.home() / ".trae-cn" / "skills" / "hypit" / "SKILL.md",
    Path.home() / ".openclaw" / "skills" / "hypit" / "SKILL.md",
    Path(__file__).resolve().parents[2] / ".agents" / "skills" / "hypit" / "SKILL.md",
]

# 安全扩展名（与 hypit_workspace 一致）
WRITE_EXTS = {".md", ".json", ".svml", ".svs", ".svrun", ".txt"}
TEXT_EXTS = {".md", ".json", ".svml", ".svs", ".svrun", ".txt", ".html", ".css", ".js", ".ts"}
SKIP_DIRS = {".git", "node_modules", "runtimes", "__pycache__"}
MEDIA_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".mp3", ".wav", ".m4a", ".jpg", ".jpeg", ".png", ".webp", ".gif"}

# ── LLM 配置：GPT 主 + Grok 备 ──
PRIMARY = {"model": "gpt-5.6-sol", "base": settings.GPT55_BASE_URL, "key": settings.GPT55_API_KEY}
FALLBACK = {"model": settings.SR_GROK_MODEL, "base": settings.SR_GROK_BASE_URL, "key": settings.SR_GROK_API_KEY}


def _load_skill_text(limit: int = 30000) -> str:
    """读取 SKILL.md（截断到 limit 字符避免 prompt 过长）。"""
    for p in SKILL_CANDIDATES:
        if p.exists():
            try:
                return p.read_text(encoding="utf-8", errors="replace")[:limit]
            except OSError:
                continue
    return ""


def _safe_rel(rel: str) -> Path:
    """安全相对路径解析（不跳出工作区）。"""
    if not rel or rel.strip() in (".", "/"):
        raise ValueError("路径无效")
    raw = rel.replace("\\", "/").lstrip("/")
    if ".." in Path(raw).parts:
        raise ValueError("不允许跳出工作区")
    root = WORKSPACE.resolve()
    path = (root / raw).resolve()
    if path != root and root not in path.parents:
        raise ValueError("路径超出工作区")
    return path


def _rel_of(path: Path) -> str:
    return path.resolve().relative_to(WORKSPACE.resolve()).as_posix()


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


# ── 工具定义（OpenAI tools 格式） ──
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "hypit_cli",
            "description": (
                "执行 hypit CLI 命令。action 可选：version / paths / doctor / runtime-status / "
                "runtime-init / runtime-up / runtime-down / runtime-logs / builds / activity / "
                "auth-status / vocabulary / check / plan / pricing / build / status / inspect / logs / cancel。"
                "target 对应 .svrun 相对路径（check/plan/pricing/build/studio）或 build-id（status/inspect/logs/cancel）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "CLI 动作"},
                    "target": {"type": "string", "description": "可选目标：.svrun 路径或 build-id", "default": ""},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取 hypit 工作区内的文本文件（BRIEF.md / TREATMENT.md / .svs / .svrun 等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "工作区相对路径，如 productions/foo/BRIEF.md"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "向工作区写入文本文件（限 .md/.json/.svml/.svs/.svrun/.txt）。已有文件会被覆盖。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "工作区相对路径"},
                    "content": {"type": "string", "description": "完整文件内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_workspace",
            "description": "列出当前工作区文件树（最多 200 条），了解工程与产物布局。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_projects",
            "description": "列出所有 productions 与 references（含 brief 第一行、media 数、.svrun 数）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "studio_start",
            "description": "为指定 .svrun 启动 Hypit Studio（端口 5179）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "run": {"type": "string", "description": ".svrun 相对路径"},
                },
                "required": ["run"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "studio_stop",
            "description": "停止当前 Studio 进程。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def build_system_prompt(production: str = "") -> str:
    """组装 system prompt：skill 文本 + 工作区信息 + 当前工程。"""
    skill_text = _load_skill_text()
    parts = [
        "你是 Hypit 内置 Agent，运行在 /hypit 工作台内，已经获得 hypit skill 的完整指令。",
        "请严格遵守 skill 中的所有职责：Brief / Treatment / Analysis / Timeline / Progress 等项目文件的写法、",
        "工具调用方式、付费前先确认、Studio 评审、Done = Watched 等。",
        "",
        f"工作区根目录：{WORKSPACE}",
        f"当前选中工程：{production or '（尚未选择，可调用 list_projects 浏览）'}",
        "",
        "你可以调用以下工具完成实际工作。每次只能调用一个工具，调用前会征求用户批准。",
        "不要假装调用了工具——只有出现 tool_calls 才会被执行。所有路径都相对工作区根。",
        "",
        "回答风格：",
        "- 用中文，温暖具体，像一位导演在和制作方对话。",
        "- 大段方向先讲清楚「为什么这么定」，再讲「下一步要做什么」。",
        "- 涉及付费 build / 写文件 / 启动 Studio 时，先简述意图，再发起 tool_call。",
        "- 遇到 skill 中要求你写的文档（BRIEF/Treatment/Progress/Analysis/Timeline），用 write_file 工具直接落盘。",
        "- 用户批准工具调用后，结果会作为 tool 角色消息回灌，请基于结果继续推理。",
        "",
        "==== SKILL 文本开始 ====",
        skill_text,
        "==== SKILL 文本结束 ====",
    ]
    return "\n".join(parts)


# ── CLI 复用 hypit_workspace 的子进程封装 ──
_CLI_MAP = {
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


def _resolve_cli(action: str, target: str) -> tuple[list[str], int, bool, bool]:
    action = (action or "").strip().lower()
    target = (target or "").strip()
    if action in _CLI_MAP:
        return _CLI_MAP[action]
    if not target:
        raise ValueError(f"{action} 需要 target")
    rel = _safe_rel(target) if action in {"check", "plan", "pricing", "build"} else None
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
            raise ValueError("build-id 非法")
        return ([action, target], 60, True, True)
    raise ValueError(f"不支持的 action：{action}")


async def _run_hypit(args: list[str], timeout: int, json_mode: bool, workspace: bool) -> dict:
    """直接 spawn hypit CLI（与 hypit_workspace 共用同一进程契约）。"""
    bin_path = _find_hypit_bin()
    cmd = [bin_path, *args]
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
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        raise TimeoutError(f"hypit {' '.join(args)} 超时（{timeout}s）")
    except FileNotFoundError:
        raise RuntimeError("无法启动 hypit")
    out = (stdout or b"").decode("utf-8", errors="replace")
    err = (stderr or b"").decode("utf-8", errors="replace")
    payload: Any = None
    if json_mode:
        stripped = out.strip()
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
        "output": (out + ("\n" + err if err else ""))[-8000:],
        "data": payload,
    }


def _find_hypit_bin() -> str:
    import shutil
    path = shutil.which("hypit")
    if not path:
        raise RuntimeError(
            "未找到 hypit 可执行程序。请先执行 `npm install -g @hypit/hypit`。"
        )
    return path


# ── 工具执行器 ──
async def execute_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    name = (name or "").strip()
    args = args or {}
    if name == "hypit_cli":
        return await _exec_hypit_cli(args.get("action", ""), args.get("target", ""))
    if name == "read_file":
        return await _exec_read_file(args.get("path", ""))
    if name == "write_file":
        return await _exec_write_file(args.get("path", ""), args.get("content", ""))
    if name == "list_workspace":
        return await _exec_list_workspace()
    if name == "list_projects":
        return await _exec_list_projects()
    if name == "studio_start":
        return await _exec_studio_start(args.get("run", ""))
    if name == "studio_stop":
        return await _exec_studio_stop()
    raise ValueError(f"未知工具：{name}")


async def _exec_hypit_cli(action: str, target: str) -> Dict[str, Any]:
    try:
        args, timeout, json_mode, workspace = _resolve_cli(action, target)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    try:
        result = await _run_hypit(args, timeout=timeout, json_mode=json_mode, workspace=workspace)
    except (TimeoutError, RuntimeError) as e:
        return {"ok": False, "error": str(e)}
    return {
        "ok": result["ok"],
        "code": result["code"],
        "cmd": result["cmd"],
        "output": result["output"],
        "data": result.get("data"),
    }


async def _exec_read_file(path: str) -> Dict[str, Any]:
    if not path:
        return {"ok": False, "error": "path 必填"}
    try:
        target = _safe_rel(path)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if not target.exists() or not target.is_file():
        return {"ok": False, "error": "文件不存在"}
    if target.suffix.lower() in TEXT_EXTS:
        return {"ok": True, "path": _rel_of(target), "text": True, "content": _read_text(target)}
    return {
        "ok": True,
        "path": _rel_of(target),
        "text": False,
        "url": "/api/hypit/media?path=" + _rel_of(target),
    }


async def _exec_write_file(path: str, content: str) -> Dict[str, Any]:
    if not path:
        return {"ok": False, "error": "path 必填"}
    try:
        target = _safe_rel(path)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if target.suffix.lower() not in WRITE_EXTS:
        return {"ok": False, "error": f"不允许写入 {target.suffix or '无扩展名'} 文件"}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content or "", encoding="utf-8")
    return {"ok": True, "path": _rel_of(target), "bytes": target.stat().st_size}


def _dir_summary(kind: str) -> list[dict]:
    root = WORKSPACE / kind
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
                "updated_at": child.stat().st_mtime,
                "files": len(files),
                "media": media,
                "runs": runs,
            }
        )
    return items


def _tree(max_entries: int = 200) -> list[dict]:
    root = WORKSPACE
    if not root.exists():
        return []
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


async def _exec_list_workspace() -> Dict[str, Any]:
    return {"ok": True, "tree": _tree()}


async def _exec_list_projects() -> Dict[str, Any]:
    return {
        "ok": True,
        "productions": _dir_summary("productions"),
        "references": _dir_summary("references"),
    }


async def _exec_studio_start(run: str) -> Dict[str, Any]:
    """复用 hypit_workspace 的 Studio 启动器。"""
    from .hypit_workspace import _safe_rel as ws_safe_rel, _studio, _rel_of as ws_rel_of, STUDIO_PORT
    import subprocess

    try:
        run_path = ws_safe_rel(run)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if not run_path.exists():
        return {"ok": False, "error": f"Run 不存在：{run}"}
    if run_path.suffix.lower() != ".svrun":
        return {"ok": False, "error": "Studio 需要 .svrun"}
    # 先停旧的
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
    port = STUDIO_PORT
    bin_path = _find_hypit_bin()
    cmd = [
        bin_path,
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
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(WORKSPACE),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
        creationflags=creationflags,
    )
    from datetime import datetime
    _studio.update(
        {
            "proc": proc,
            "url": "",
            "run": ws_rel_of(run_path),
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "log": [],
        }
    )
    deadline = time.time() + 25
    url = f"http://127.0.0.1:{port}"
    while time.time() < deadline:
        if proc.returncode is not None:
            rest = b""
            if proc.stdout:
                rest = await proc.stdout.read()
            text = rest.decode("utf-8", errors="replace")
            _studio["log"].append(text)
            return {"ok": False, "error": "Studio 启动失败", "log": _studio["log"][-20:]}
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
    return {"ok": True, "url": _studio["url"], "run": _studio["run"], "log": _studio["log"][-20:]}


async def _exec_studio_stop() -> Dict[str, Any]:
    from .hypit_workspace import _studio
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
    return {"ok": True}


# ── LLM 调用（GPT 主 + Grok 备，SSE 流式 + tool_calls 支持） ──
async def _call_llm_stream(
    messages: List[Dict[str, Any]],
    *,
    primary_first: bool = True,
) -> AsyncIterator[Dict[str, Any]]:
    """流式调用 LLM，按 OpenAI SSE 协议解析。

    yield 的 event 形如：
      {"type": "delta", "text": "..."}                              # 文本增量
      {"type": "tool_call", "id": "...", "name": "...", "arguments": "..."}  # 工具调用片段
      {"type": "finish", "reason": "stop|tool_calls|length", "tool_calls": [...]}  # 结束
      {"type": "error", "message": "...", "engine": "..."}        # 错误

    先用 primary 模型尝试；如遇网络/5xx 错误切到 fallback。
    """
    engines = [PRIMARY, FALLBACK] if primary_first else [FALLBACK, PRIMARY]
    last_error: Optional[str] = None

    for idx, engine in enumerate(engines):
        is_primary = idx == 0
        try:
            async for ev in _stream_openai_compat(engine, messages, is_primary=is_primary):
                yield ev
            return
        except _Retryable as e:
            last_error = str(e)
            print(
                f"[HypitAgent] 引擎 {engine['model']} 失败：{last_error}；切换备用",
                flush=True,
            )
            continue
        except _HardFail as e:
            yield {"type": "error", "message": str(e), "engine": engine["model"]}
            return

    yield {"type": "error", "message": last_error or "所有引擎均失败", "engine": "all"}


class _Retryable(Exception):
    """可重试错误（502/524/连接/超时）。"""


class _HardFail(Exception):
    """硬失败（4xx 鉴权/请求格式）。"""


async def _stream_openai_compat(
    engine: Dict[str, Any],
    messages: List[Dict[str, Any]],
    *,
    is_primary: bool,
) -> AsyncIterator[Dict[str, Any]]:
    """对 OpenAI 兼容端点做 SSE 流式调用，解析 tool_calls 增量。"""
    payload = {
        "model": engine["model"],
        "messages": messages,
        "temperature": 0.6,
        "max_tokens": 4000,
        "stream": True,
        "tools": TOOLS,
        "tool_choice": "auto",
    }
    headers = {
        "Authorization": f"Bearer {engine['key']}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    url = f"{engine['base'].rstrip('/')}/chat/completions"

    # 累积器
    content_parts: List[str] = []
    tool_calls_acc: Dict[int, Dict[str, str]] = {}  # index → {id, name, arguments}
    finish_reason: Optional[str] = None

    timeout = httpx.Timeout(600.0, connect=30.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                if response.status_code >= 500 or response.status_code in (502, 503, 504, 524):
                    raise _Retryable(f"上游 {response.status_code}")
                if response.status_code >= 400:
                    body = await response.aread()
                    raise _HardFail(f"HTTP {response.status_code}: {body[:500].decode('utf-8', errors='replace')}")

                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(chunk, dict):
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    # 文本增量
                    if delta.get("content"):
                        text_piece = delta["content"]
                        content_parts.append(text_piece)
                        yield {"type": "delta", "text": text_piece}
                    # 工具调用增量
                    for piece in delta.get("tool_calls") or []:
                        idx = piece.get("index", 0)
                        slot = tool_calls_acc.setdefault(
                            idx, {"id": "", "name": "", "arguments": ""}
                        )
                        if piece.get("id"):
                            slot["id"] = piece["id"]
                        fn = piece.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]
                        yield {
                            "type": "tool_call_delta",
                            "index": idx,
                            "id": slot["id"],
                            "name": slot["name"],
                            "arguments": slot["arguments"],
                        }
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
    except httpx.ConnectError as e:
        raise _Retryable(f"连接失败：{e}")
    except httpx.ReadTimeout as e:
        raise _Retryable(f"读超时：{e}")
    except httpx.RemoteProtocolError as e:
        raise _Retryable(f"协议错误：{e}")
    except httpx.HTTPStatusError as e:
        code = e.response.status_code if e.response else 0
        if code in (502, 503, 504, 524) or 500 <= code < 600:
            raise _Retryable(f"HTTP {code}")
        raise _HardFail(f"HTTP {code}: {e}")

    # 流结束：汇总
    tool_calls_list = [
        {
            "id": v["id"] or f"call_{i}",
            "name": v["name"],
            "arguments": v["arguments"],
        }
        for i, v in sorted(tool_calls_acc.items())
    ]
    yield {
        "type": "finish",
        "reason": finish_reason or ("tool_calls" if tool_calls_list else "stop"),
        "content": "".join(content_parts),
        "tool_calls": tool_calls_list,
    }


def sse(event: str, data: Dict[str, Any]) -> str:
    """格式化 SSE 帧。"""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


async def run_agent_turn(
    messages: List[Dict[str, Any]],
    production: str = "",
    *,
    primary_first: bool = True,
) -> AsyncIterator[str]:
    """跑一轮 agent：调用 LLM 流式输出，遇到 tool_call 时停在 finish 事件等用户批准。

    输入 messages 已经包含：
      - system 提示（首次）
      - 历史对话
      - 用户最新一条
      - 如果是回灌：包含 assistant 的 tool_calls 消息 + tool 角色结果消息

    yield SSE 帧（event: delta|tool_call|tool_result|error|done）。
    """
    # 确保有 system 提示
    if not messages or messages[0].get("role") != "system":
        messages = [{"role": "system", "content": build_system_prompt(production)}, *messages]

    final_content = ""
    final_tool_calls: List[Dict[str, Any]] = []
    finish_reason = "stop"
    engine_used = ""

    async for ev in _call_llm_stream(messages, primary_first=primary_first):
        et = ev.get("type")
        if et == "delta":
            yield sse("delta", {"text": ev["text"]})
            final_content += ev["text"]
        elif et == "tool_call_delta":
            # 仅向前端发"实时累加"，让 UI 显示 args 增量
            yield sse(
                "tool_call_delta",
                {
                    "index": ev["index"],
                    "id": ev["id"],
                    "name": ev["name"],
                    "arguments": ev["arguments"],
                },
            )
        elif et == "finish":
            final_content = ev.get("content") or final_content
            final_tool_calls = ev.get("tool_calls") or []
            finish_reason = ev.get("reason") or "stop"
        elif et == "error":
            yield sse("error", {"message": ev.get("message", "未知错误"), "engine": ev.get("engine", "")})
            return

    # 把 assistant 这条消息回写：content + tool_calls（让前端在下次请求时把它放进 messages）
    assistant_msg: Dict[str, Any] = {"role": "assistant", "content": final_content or ""}
    if final_tool_calls:
        assistant_msg["tool_calls"] = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["name"], "arguments": tc["arguments"]},
            }
            for tc in final_tool_calls
        ]
    yield sse("assistant_message", assistant_msg)

    if final_tool_calls:
        # 第一个 tool_call 即用（一次一个的约定）。前端拿到后弹出批准卡。
        tc = final_tool_calls[0]
        try:
            args_obj = json.loads(tc["arguments"]) if tc["arguments"] else {}
        except json.JSONDecodeError:
            args_obj = {}
        yield sse(
            "tool_call",
            {"id": tc["id"], "name": tc["name"], "arguments": args_obj, "raw_arguments": tc["arguments"]},
        )
        yield sse("done", {"finish_reason": finish_reason, "needs_approval": True})
    else:
        yield sse("done", {"finish_reason": finish_reason, "needs_approval": False})


async def execute_approved_tool(
    tool_call_id: str,
    name: str,
    raw_arguments: str,
) -> Dict[str, Any]:
    """执行用户批准的工具调用。返回结构化结果（用于回灌 LLM）。"""
    try:
        args = json.loads(raw_arguments) if raw_arguments else {}
    except json.JSONDecodeError:
        args = {"_invalid_arguments": raw_arguments}
    try:
        result = await execute_tool(name, args)
    except Exception as e:
        result = {"ok": False, "error": f"执行异常：{e}"}
    result["tool_call_id"] = tool_call_id
    result["name"] = name
    return result
