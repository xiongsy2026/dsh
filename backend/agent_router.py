"""AGENT 自动路由端点（Weeksirs）。

一个 OpenAI 兼容的 /v1/chat/completions 服务：DSH 下拉里只暴露 **一个** provider/model
（"agent"），换模型发生在本服务内部——按请求里到底有没有图/视频，自动选 agnes 子模型：

    纯文本  -> settings.AGNES_TEXT_MODEL        (agnes-3.0-flash)
    含图片  -> settings.AGNES_IMAGE_MODEL       (agnes-image-2.5-flash)
    含视频  -> settings.AGNES_VIDEO_FLASH_MODEL (agnes-video-2.5-flash)

三者共用同一 agnes base_url + key，只换 model 字符串（agnes 是 openai-completions）。
响应头 X-Routed-Via 标明这次实际走了哪个模型。

安全：只绑 127.0.0.1、无鉴权。别暴露到局域网/公网。
依赖：纯标准库（urllib + http.server），不引入 httpx，避免 .venv 缺包。

    python backend/agent_router.py            # 默认 127.0.0.1:8123
    AGENT_ROUTER_PORT=8130 python backend/agent_router.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 向上找到含 config.py 的仓库根并加入 sys.path，使本文件可从任意位置运行。
_d = os.path.dirname(os.path.abspath(__file__))
while _d and not os.path.exists(os.path.join(_d, "config.py")):
    _d = os.path.dirname(_d)
if _d and _d not in sys.path:
    sys.path.insert(0, _d)

from config import settings  # noqa: E402

HOST = os.environ.get("AGENT_ROUTER_HOST", "127.0.0.1")
PORT = int(os.environ.get("AGENT_ROUTER_PORT", "8123"))

# DSH 侧看到的唯一 model id。
SERVED_MODEL = "agent"

# 模态 -> 上游路由。默认全走 agnes 集合；若某端点(如 agnes 海外代理)不稳，
# 可把对应条目的 base_url/api_key/model 换成国内直连的 mimo（见 config 里 MIMO_*）。
DISPATCH = {
    "text":  {"base_url": settings.AGNES_BASE_URL, "api_key": settings.AGNES_API_KEY, "model": settings.AGNES_TEXT_MODEL},
    "image": {"base_url": settings.AGNES_BASE_URL, "api_key": settings.AGNES_API_KEY, "model": settings.AGNES_IMAGE_MODEL},
    "video": {"base_url": settings.AGNES_BASE_URL, "api_key": settings.AGNES_API_KEY, "model": settings.AGNES_VIDEO_FLASH_MODEL},
}

# 判定 image_url 里的“视频”线索：data URL 的 video MIME，或常见视频后缀。
_VIDEO_HINTS = ("data:video", "video/", ".mp4", ".mov", ".webm", ".mkv", ".avi")


def sniff_modality(messages: list) -> str:
    """扫描 messages 的多模态 content parts，返回 'video' > 'image' > 'text'。"""
    has_video = has_image = False
    for m in messages or []:
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype == "video_url":
                has_video = True
            elif ptype == "image_url":
                url = ((part.get("image_url") or {}).get("url") or "").lower()
                if any(h in url for h in _VIDEO_HINTS):
                    has_video = True
                else:
                    has_image = True
    if has_video:
        return "video"
    if has_image:
        return "image"
    return "text"


def _upstream_request(route: dict, payload: dict) -> urllib.request.Request:
    body = json.dumps(payload).encode("utf-8")
    return urllib.request.Request(
        route["base_url"].rstrip("/") + "/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {route['api_key']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )


def forward(route: dict, payload: dict) -> dict:
    """非流式转发，返回上游 JSON。"""
    with urllib.request.urlopen(_upstream_request(route, payload), timeout=300) as resp:
        return json.loads(resp.read().decode("utf-8"))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send_json(self, code: int, obj, extra_headers: dict | None = None):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        path = self.path.rstrip("/")
        if path.endswith("/models"):
            self._send_json(200, {
                "object": "list",
                "data": [{"id": SERVED_MODEL, "object": "model", "owned_by": "weeksir-agent"}],
            })
        else:
            self._send_json(200, {
                "service": "weeksir-agent-router",
                "served_model": SERVED_MODEL,
                "dispatch": {k: v["model"] for k, v in DISPATCH.items()},
            })

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return self._send_json(400, {"error": {"message": "invalid JSON body"}})

        messages = req.get("messages", [])
        modality = sniff_modality(messages)
        route = DISPATCH[modality]
        via = f"agent/{modality}->{route['model']}"

        # 用选定的 agnes 子模型覆盖 model；其余参数原样透传。
        payload = dict(req)
        payload["model"] = route["model"]

        if req.get("stream"):
            return self._stream(route, payload, via)

        try:
            out = forward(route, payload)
            self._send_json(200, out, {"X-Routed-Via": via})
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            self._send_json(e.code, {"error": {"message": f"upstream {route['model']} {e.code}: {detail}"}})
        except Exception as e:  # 连接/超时等
            self._send_json(502, {"error": {"message": f"{type(e).__name__}: {e}"}})

    def _stream(self, route: dict, payload: dict, via: str):
        """流式透传：把上游 SSE 原样转发给客户端。"""
        try:
            resp = urllib.request.urlopen(_upstream_request(route, payload), timeout=300)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            return self._send_json(e.code, {"error": {"message": f"upstream {route['model']} {e.code}: {detail}"}})
        except Exception as e:
            return self._send_json(502, {"error": {"message": f"{type(e).__name__}: {e}"}})

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Routed-Via", via)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            for line in resp:
                self.wfile.write(line)
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")  # 终止 chunk
        except Exception:
            pass
        finally:
            resp.close()

    def log_message(self, *args):  # 静音默认 stderr 日志
        pass


def main():
    print(f"[agent-router] http://{HOST}:{PORT}/v1  served_model={SERVED_MODEL}")
    print(f"[agent-router] dispatch: " + ", ".join(f"{k}->{v['model']}" for k, v in DISPATCH.items()))
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
