"""zexapi.com 网关客户端：提交/轮询/下载。Key 只从环境变量或本次会话内存取，绝不落盘。"""

import base64
import mimetypes
import os
import time

import requests

BASE = os.environ.get("ZEXAPI_BASE", "https://zexapi.com")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_session_key = None


_env_cache = None


def _skill_env():
    """一次性解析 D:\\video-skill\\.env（VIDEO_SKILL_HOME 可覆盖），给 zexapi/agnes 兜底取凭证。"""
    global _env_cache
    if _env_cache is None:
        _env_cache = {}
        home = os.environ.get("VIDEO_SKILL_HOME", r"D:\video-skill")
        path = os.path.join(home, ".env")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    _env_cache[k.strip()] = v.strip().strip('"').strip("'")
    return _env_cache


def _agnes_conf():
    """agnes 走 D:\\video-skill\\.env（或同名环境变量），与 zexapi 分开；模型名 agnes* 时用它。"""
    base = os.environ.get("AGNES_BASE_URL")
    key = os.environ.get("AGNES_API_KEY")
    if not (base and key):
        home = os.environ.get("VIDEO_SKILL_HOME", r"D:\video-skill")
        envp = os.path.join(home, ".env")
        if os.path.exists(envp):
            with open(envp, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k == "AGNES_BASE_URL" and not base:
                        base = v
                    elif k == "AGNES_API_KEY" and not key:
                        key = v
    return base, key


def set_session_key(key):
    global _session_key
    _session_key = (key or "").strip() or None


def has_key():
    return bool(api_key())


def chat_backend(model):
    return "agnes" if str(model or "").lower().startswith("agnes") else "zexapi"


def chat_ready(model):
    """chat 能不能跑：agnes 通道看 AGNES 配置，其余看 zexapi key。"""
    if chat_backend(model) == "agnes":
        base, key = _agnes_conf()
        return bool(base and key)
    return has_key()


def api_key():
    env = _skill_env()
    return (os.environ.get("ZEXAPI_API_KEY") or _session_key
            or env.get("ZEXAPI_API_KEY") or env.get("OMNI_API_KEY"))


class ZexError(RuntimeError):
    pass


def _headers():
    key = api_key()
    if not key:
        raise ZexError("缺少 ZEXAPI_API_KEY（在系统环境变量里设，或在页面顶部临时填入）")
    return {"Authorization": "Bearer %s" % key}


def _post(path, payload, timeout=180):
    last = None
    for attempt in range(3):
        try:
            r = requests.post(BASE + path, headers=_headers(), json=payload, timeout=timeout)
        except requests.RequestException as exc:
            last = "网络异常: %s" % exc
            time.sleep(3 * (attempt + 1))
            continue
        if r.status_code == 524 or r.status_code >= 500:
            last = "HTTP %d %s" % (r.status_code, r.text[:200])
            time.sleep(4 * (attempt + 1))
            continue
        if r.status_code >= 400:
            raise ZexError("HTTP %d: %s" % (r.status_code, r.text[:400]))
        return r.json()
    raise ZexError("提交失败（重试 3 次）: %s" % last)


def _get(path, timeout=60):
    r = requests.get(BASE + path, headers=_headers(), timeout=timeout)
    if r.status_code >= 400:
        raise ZexError("HTTP %d: %s" % (r.status_code, r.text[:300]))
    return r.json()


def chat(messages, model="gemini-3.5-flash", temperature=0.85, max_tokens=8000):
    """统一入口：默认走原直连；VIDEO_UI_VIA_GATEWAY=1 时改走模型网关（积分/审计/去抖都在网关侧）。
    契约不变（返回文本），调用方无感。默认关 = 与现在一模一样，零风险。"""
    if os.getenv("VIDEO_UI_VIA_GATEWAY") == "1":
        return _chat_via_gateway(messages, model, temperature, max_tokens)
    return _chat_direct(messages, model, temperature, max_tokens)


def _chat_via_gateway(messages, model, temperature, max_tokens):
    gw = os.getenv("MODEL_GATEWAY_URL", "http://127.0.0.1:8100")
    purpose = "agnes" if str(model).lower().startswith("agnes") else "zexapi"
    r = requests.post(gw + "/api/model/call", timeout=420, json={
        "capability": "text.complete", "purpose": purpose,
        "account": os.getenv("VIDEO_UI_MEMBER", "omni"),
        "params": {"messages": messages, "temperature": temperature, "max_tokens": max_tokens},
    })
    j = r.json()
    if not j.get("ok"):
        raise ZexError("网关失败: %s" % str(j.get("error"))[:200])
    return (j.get("result") or {}).get("text")


def _chat_direct(messages, model="gemini-3.5-flash", temperature=0.85, max_tokens=8000):
    """Stage 1 剧本 / Stage 4 运镜用。524 与 5xx 重试（实测网关会偶发 524）。
    模型名以 agnes 开头时打到 agnes 端点（key/base 来自 D:\\video-skill\\.env）。"""
    payload = {"model": model, "messages": messages, "temperature": temperature,
               "max_tokens": max_tokens, "stream": False}
    if str(model).lower().startswith("agnes"):
        base, key = _agnes_conf()
        if not (base and key):
            raise ZexError("agnes 通道缺 AGNES_BASE_URL/AGNES_API_KEY（检查 D:\\video-skill\\.env）")
        url = base.rstrip("/") + "/chat/completions"
        headers = {"Authorization": "Bearer %s" % key}
    else:
        url = BASE + "/v1/chat/completions"
        headers = _headers()
    last = None
    for attempt in range(4):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=420)
        except requests.RequestException as exc:
            last = "网络异常: %s" % exc
            time.sleep(4 * (attempt + 1))
            continue
        if r.status_code in (524, 502, 503, 504) or r.status_code >= 500:
            last = "HTTP %d %s" % (r.status_code, r.text[:260])
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code >= 400:
            raise ZexError("HTTP %d: %s" % (r.status_code, r.text[:400]))
        data = r.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            raise ZexError("chat 返回结构异常: %s" % str(data)[:300])
    raise ZexError("chat 重试 4 次仍失败: %s" % last)


def submit_video(prompt, images, model=None, size=None):
    cfg_model = model or os.environ.get("OMNI_MODEL", "omni_flash-hd-10s")
    payload = {"model": cfg_model, "prompt": prompt, "messages": "", "images": images}
    if size:
        payload["size"] = size
    data = _post("/v1/videos", payload)
    task_id = data.get("task_id") or data.get("id")
    if not task_id:
        raise ZexError("提交未返回 task_id: %s" % str(data)[:300])
    return task_id, data


def submit_image(prompt, images=None, model="nano_banana_pro-2K", aspect="9:16", image_size="1K"):
    payload = {"model": model, "prompt": prompt, "messages": "", "aspect_ratio": aspect}
    if image_size:
        payload["image_size"] = image_size
    if images:
        payload["images"] = images
    data = _post("/v1/videos", payload)
    task_id = data.get("task_id") or data.get("id")
    if not task_id:
        raise ZexError("提交未返回 task_id: %s" % str(data)[:300])
    return task_id, data


def fetch(task_id):
    return _get("/v1/videos/%s" % task_id)


def asset_url(data):
    for key in ("url", "image_url", "video_url"):
        val = data.get(key)
        if val:
            return val
    return None


def poll(task_id, interval=10, timeout=900, on_update=None):
    """终态只认 completed/succeeded/failed。图片可能是 succeeded + image_url。"""
    start = time.time()
    while time.time() - start < timeout:
        try:
            data = fetch(task_id)
        except ZexError as exc:
            data = None
            last_err = str(exc)
        if data:
            status = str(data.get("status", "")).lower()
            progress = data.get("progress")
            if on_update:
                on_update(status, progress)
            if status in ("completed", "succeeded"):
                url = asset_url(data)
                if not url:
                    raise ZexError("任务 %s 完成但没有 url/image_url/video_url" % task_id)
                return url, data
            if status == "failed":
                err = data.get("error") or {}
                raise ZexError("任务 %s 失败: %s (%s)" % (task_id, err.get("message"), err.get("code")))
        time.sleep(interval)
    raise ZexError("轮询超时 %ds（任务 %s）" % (timeout, task_id))


def download(url, dest):
    """OSS 直链需要浏览器 UA，否则 403。"""
    r = requests.get(url, headers={"User-Agent": UA}, stream=True, timeout=300)
    if r.status_code >= 400:
        raise ZexError("下载失败 HTTP %d: %s" % (r.status_code, url[:120]))
    tmp = str(dest) + ".part"
    with open(tmp, "wb") as fh:
        for chunk in r.iter_content(1 << 16):
            if chunk:
                fh.write(chunk)
    os.replace(tmp, str(dest))
    return dest


def data_uri(path, max_edge=None):
    """本地文件 -> data URI（必须带 `data:` 前缀，否则网关按裸 base64 解，报 illegal base64）。
    视频提交用 640px 缩样以免 body 过大。"""
    from PIL import Image
    import io
    path = str(path)
    mime = mimetypes.guess_type(path)[0] or "image/png"
    if max_edge:
        img = Image.open(path).convert("RGB")
        img.thumbnail((max_edge, max_edge), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    with open(path, "rb") as fh:
        return "data:%s;base64,%s" % (mime, base64.b64encode(fh.read()).decode())
