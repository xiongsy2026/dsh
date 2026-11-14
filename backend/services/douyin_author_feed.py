"""抖音账号主页作品列表：账号主页链接 / 分享文案 → 该账号本人的作品清单。

上游实测结论（2026-10-12，拿作者本人账号实测，判据钉在 scripts/check_douyin_batch.py）：
- 作品接口是 `/aweme/v1/web/aweme/post/`；社区流传的 `user/posts` 裸调返回 404
  `Unsupported path(Janus)`，签名只由浏览器自己完成，所以列表腿必须起浏览器。
- 登录态下页面会【同时】请求 `/aweme/v1/web/aweme/favorite/`，那是别人家的视频
  （实测 77 条里 20 条非本人）。不按 author.sec_uid 逐条过滤，批量就会下错人的视频。
- 匿名只能拿到首页 21 条，翻页必须有登录态，所以这个模块假定有 cookie，没有就大声拒绝。
"""
import asyncio
import json
import re
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import httpx

from config import settings

POST_PATH = "/aweme/v1/web/aweme/post/"
PROFILE_PATH = "/aweme/v1/web/user/profile/other/"
FAVORITE_PATH = "/aweme/v1/web/aweme/favorite/"

COOKIE_STATE_PATH = Path(settings.UPLOAD_DIR) / "douyin_storage_state.json"

ANONYMOUS_FIRST_PAGE = 21

_URL_RE = re.compile(r"https?://[^\s，。`'\"<>]+")
_SEC_UID_RE = re.compile(r"^MS4wLjAB[A-Za-z0-9_-]{20,}$")

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)

STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
window.chrome = { runtime: {} };
"""

# 2026-10-12 实测：主页作品区是【内层 overflow 容器】。window.scrollTo、打在背景上的
# mouse.wheel、按 End 四种滚法全部停在 57 条（页面根本不发第 4 个请求）；只有找到真正
# 可滚的那个容器并滚它，才会连续翻到 560+ 条。
SCROLL_JS = """() => {
    const all = [document.scrollingElement, ...document.querySelectorAll('div,main,section')];
    let best = null, bestGap = 0;
    for (const el of all) {
        const oy = getComputedStyle(el).overflowY;
        if (oy !== 'auto' && oy !== 'scroll') continue;
        const gap = el.scrollHeight - el.clientHeight;
        if (gap > bestGap) { bestGap = gap; best = el; }
    }
    if (best) best.scrollTop += Math.max(600, best.clientHeight * 0.9);
    window.scrollBy(0, 1400);
}"""

_feed_lock = asyncio.Lock()

DEFAULT_TARGET_DIR = Path("C:/视频下载")
_UNSAFE_RE = re.compile(r'[\\/:*?"<>|\r\n\t]')


def safe_text(text: str, limit: int = 24) -> str:
    """Windows 文件名安全化：非法字符全替换，超长截断（标题里带 #话题 空格 表情都常见）"""
    cleaned = _UNSAFE_RE.sub("_", (text or "").strip())
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned[:limit].strip("._") or ""


def run_dir(target_dir, nickname: str, sec_uid: str, task_id: str) -> Path:
    """每次下载任务一个独立目录：<目标>/<昵称>_<task_id>。

    同账号跑两次不混片，而且各带自己那份清单 —— 按账号共目录时，后一次的 清单.csv
    会把前一次那份盖掉，台账就没了。
    """
    base = Path(str(target_dir) or DEFAULT_TARGET_DIR)
    label = safe_text(nickname, 40) or f"author_{(sec_uid or 'unknown')[:8]}"
    return base / f"{label}_{(task_id or 'no-task')[:12]}"


def item_filename(index: int, total: int, aweme_id: str, title: str) -> str:
    """主页顺序进文件名，两位起跳，保证资源管理器里排序=账号里的排序"""
    width = max(2, len(str(total)))
    stem = safe_text(title, 24)
    tail = f"_{stem}" if stem else ""
    return f"{str(index).zfill(width)}_{aweme_id}{tail}.mp4"


def list_ledger(items: list[dict]) -> list[list[str]]:
    """交付台账行：谁下的、哪条腿、多大、多久、成没成 —— 逐行留痕，不整表盖一个结论"""
    header = ["序号", "aweme_id", "标题", "时长(s)", "发布日", "状态", "文件", "大小(MB)", "下载腿", "失败/跳过原因"]
    rows = [header]
    for it in items:
        rows.append([
            str(it.get("index", "")),
            str(it.get("aweme_id", "")),
            (it.get("title") or "")[:60],
            f"{(it.get('duration_ms') or 0) / 1000:.1f}",
            str(it.get("publish_date") or ""),
            str(it.get("dl_status") or "pending"),
            str(it.get("file") or ""),
            f"{(it.get('size_mb') or 0):.2f}" if it.get("size_mb") else "",
            (it.get("method") or "没记") if it.get("dl_status") == "done" else "",
            str(it.get("error") or it.get("skip_reason") or ""),
        ])
    return rows


def extract_share_url(text: str) -> str:
    """从整段分享文案里取出链接（抖音文案带反引号、引号、@ 噪声）"""
    if not text:
        return ""
    m = _URL_RE.search(text)
    return m.group(0).rstrip("/`'\"<>") if m else ""


def parse_cookie_header(raw: str) -> list[dict]:
    """`name=value; name2=value2`（document.cookie 或请求头 Cookie）→ playwright cookie 列表。

    只保留 name/value，domain 统一给 .douyin.com；过期时间设成会话级（-1），
    因为 document.cookie 本来就读不到 expires。
    """
    cookies = []
    seen = set()
    for chunk in (raw or "").replace("\n", " ").replace("\r", " ").split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        name, value = chunk.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"')
        if not name or name in seen:
            continue
        seen.add(name)
        cookies.append({
            "name": name,
            "value": value,
            "domain": ".douyin.com",
            "path": "/",
            "expires": -1,
            "httpOnly": False,
            "secure": True,
            "sameSite": "Lax",
        })
    return cookies


def read_state_cookies() -> dict:
    """storage_state 里的 cookie 按 name 索引（只在本机进程内使用，绝不外传/打印值）"""
    if not COOKIE_STATE_PATH.exists():
        return {}
    try:
        data = json.loads(COOKIE_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {c.get("name"): c.get("value") for c in data.get("cookies", []) if c.get("name")}


def save_cookie_string(raw: str) -> dict:
    """粘贴 cookie → 落盘 storage_state。返回摘要（只含名字/条数，绝不含值）"""
    cookies = parse_cookie_header(raw)
    if not cookies:
        raise ValueError("没解析出任何 cookie（格式应为 name=value; name2=value2）")
    names = {c["name"] for c in cookies}
    COOKIE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_STATE_PATH.write_text(
        json.dumps({"cookies": cookies, "origins": []}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "cookie_count": len(cookies),
        "has_sessionid": bool({"sessionid", "sessionid_ss", "sid_tt"} & names),
        "has_ttwid": "ttwid" in names,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "names": sorted(names)[:60],
    }


def login_state() -> dict:
    """只读登录态摘要 —— 供 UI 显示"有没有、缺不缺关键 cookie" """
    if not COOKIE_STATE_PATH.exists():
        return {"exists": False, "cookie_count": 0, "has_sessionid": False,
                "has_ttwid": False, "path": str(COOKIE_STATE_PATH)}
    names = set(read_state_cookies().keys())
    return {
        "exists": True,
        "cookie_count": len(names),
        "has_sessionid": bool({"sessionid", "sessionid_ss", "sid_tt"} & names),
        "has_ttwid": "ttwid" in names,
        "mtime_str": time.strftime("%Y-%m-%d %H:%M:%S",
                                   time.localtime(COOKIE_STATE_PATH.stat().st_mtime)),
        "path": str(COOKIE_STATE_PATH),
    }


def filter_own_posts(items: list[dict], sec_uid: str) -> tuple[list[dict], list[dict]]:
    """逐条按 author.sec_uid 判归属：作品留、别人的（收藏流混进来的）扔。

    归属拿不准（没有 author.sec_uid）的一律当外人 —— 宁可少下，不能下错人的视频。
    """
    own, foreign = [], []
    for it in items:
        author = it.get("author") or {}
        if author.get("sec_uid") == sec_uid and sec_uid:
            own.append(it)
        else:
            foreign.append(it)
    return own, foreign


def normalize_item(raw: dict) -> dict:
    """抖音原始 aweme 对象 → 本工作台的条目契约"""
    video = raw.get("video") or {}
    author = raw.get("author") or {}
    play_urls = video.get("play_addr", {}).get("url_list") or []
    aweme_id = str(raw.get("aweme_id") or raw.get("id") or "")
    create_time = raw.get("create_time") or 0
    images = raw.get("images") or []
    is_image_post = bool(images) and not play_urls
    return {
        "aweme_id": aweme_id,
        "share_url": f"https://www.douyin.com/video/{aweme_id}",
        "title": (raw.get("desc") or "").strip(),
        "author_nickname": author.get("nickname") or "",
        "duration_ms": video.get("duration") or 0,
        "create_time": create_time,
        "publish_date": time.strftime("%Y-%m-%d", time.localtime(create_time))
        if create_time else "",
        "playable": bool(play_urls),
        "downloadable": bool(play_urls) and not is_image_post,
        "skip_reason": "图文合集（没有视频流）" if is_image_post else
                       ("没有 play_addr" if not play_urls else ""),
        "cover": (video.get("cover", {}).get("url_list") or [""])[0],
    }


def dedupe_keep_order(items: list[dict]) -> list[dict]:
    """按 aweme_id 去重，保留首次出现顺序（主页顺序=置顶在前，不按时间重排）"""
    seen, out = set(), []
    for it in items:
        aid = it.get("aweme_id")
        if not aid or aid in seen:
            continue
        seen.add(aid)
        out.append(it)
    return out


async def _author_from_video_id(vid: str) -> str:
    """用户粘的其实是单条视频链接时，反查作者 sec_uid（分享页 SSR，需要 ttwid）"""
    ttwid = read_state_cookies().get("ttwid")
    if not ttwid:
        raise RuntimeError(
            f"粘进来的是单条视频链接（{vid}），要反查它的主页需要先有登录态 cookie 里的 ttwid。"
            "请先在上方粘贴抖音 cookie。")
    async with httpx.AsyncClient(follow_redirects=True, timeout=20.0,
                                 headers={"User-Agent": MOBILE_UA}) as client:
        client.cookies.set("ttwid", ttwid)
        resp = await client.get(f"https://www.iesdouyin.com/share/video/{vid}/")
    m = re.search(r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", resp.text, re.DOTALL)
    if not m:
        raise RuntimeError(f"视频链接反查作者失败：分享页没返回 SSR 数据（status={resp.status_code}）")
    data = json.loads(m.group(1).strip())
    for node in (data.get("loaderData") or {}).values():
        if isinstance(node, dict) and node.get("videoInfoRes"):
            items = node["videoInfoRes"].get("item_list") or []
            if items:
                sec_uid = (items[0].get("author") or {}).get("sec_uid") or ""
                if sec_uid:
                    return sec_uid
    raise RuntimeError("视频链接反查作者失败：SSR 里没有 item_list（cookie 可能已失效）")


async def resolve_author(user_input: str) -> dict:
    """账号主页链接 / 视频链接 / 裸 sec_uid → {sec_uid, homepage_url, source_kind, nickname_hint}"""
    text = (user_input or "").strip()
    if not text:
        raise ValueError("请输入抖音账号主页链接（或整段分享文案）")

    bare = text.split()[0] if " " not in text else ""
    if _SEC_UID_RE.match(bare):
        sec_uid, kind = bare, "sec_uid"
    else:
        url = extract_share_url(text)
        if not url:
            raise ValueError(f"没在输入里找到抖音链接：{text[:60]}")
        async with httpx.AsyncClient(follow_redirects=True, timeout=20.0,
                                     headers={"User-Agent": DESKTOP_UA}) as client:
            resp = await client.get(url)
        final = str(resp.url)
        m = re.search(r"/user/([A-Za-z0-9_-]{20,})", final)
        if m:
            sec_uid, kind = m.group(1), "profile"
        else:
            q = urlparse(final).query
            vm = re.search(r"[?&]sec_uid=([A-Za-z0-9_-]{20,})", final)
            if vm:
                sec_uid, kind = vm.group(1), "query"
            elif re.search(r"/video/(\d{15,})", final):
                vid = re.search(r"/video/(\d{15,})", final).group(1)
                sec_uid, kind = await _author_from_video_id(vid), "video_reversed"
            else:
                raise RuntimeError(
                    f"这条链接解析不出账号主页。跳转终点：{final[:120]}。"
                    "请复制「账号主页 → 分享 → 复制链接」得到的地址。")
    return {
        "sec_uid": sec_uid,
        "homepage_url": f"https://www.douyin.com/user/{sec_uid}",
        "source_kind": kind,
    }


def feed_verdict(stopped_reason: str, has_more, got: int, want: int = 0) -> dict:
    """「翻到底没有」这件事的唯一裁定处。

    规则：只有 has_more=0（或明确 stopped=end）才算到底；其余一律算没翻完。
    这条之所以要独立存在：2026-10-12 曾经把 stalled 在 57 条当成翻完了报给用户。
    """
    if stopped_reason == "enough":
        return {"reached_end": has_more == 0, "truncated": False, "maybe_anonymous": False}
    reached_end = stopped_reason == "end" or has_more == 0
    return {
        "reached_end": reached_end,
        "truncated": not reached_end,
        "maybe_anonymous": stopped_reason == "stalled" and got <= ANONYMOUS_FIRST_PAGE,
    }


async def list_author_videos(
    sec_uid: str,
    want: int = 0,
    status_callback: Optional[Callable] = None,
    max_scrolls: int = 400,
    pause_ms: int = 2600,
    stall_limit: int = 8,
) -> dict:
    """翻页取该账号本人作品。want<=0 表示「全部」，翻到 has_more=0 为止。"""
    if not sec_uid:
        raise ValueError("sec_uid 为空")
    if not COOKIE_STATE_PATH.exists():
        raise RuntimeError("还没有抖音登录态：请先粘贴 cookie 再取列表。")

    from playwright.async_api import async_playwright

    async def _say(msg):
        if status_callback:
            await status_callback(msg)

    raw_items: list[dict] = []
    page_sizes: list[int] = []
    favorite_hits = 0
    declared_count = None
    nickname = ""
    has_more: Optional[int] = None

    async with _feed_lock:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            try:
                context = await browser.new_context(
                    user_agent=DESKTOP_UA,
                    viewport={"width": 1366, "height": 900},
                    locale="zh-CN",
                    storage_state=str(COOKIE_STATE_PATH),
                )
                await context.add_init_script(STEALTH_SCRIPT)
                page = await context.new_page()

                async def on_response(resp):
                    nonlocal favorite_hits, declared_count, nickname, has_more
                    path = urlparse(resp.url).path
                    if path not in (POST_PATH, FAVORITE_PATH, PROFILE_PATH):
                        return
                    try:
                        body = await resp.json()
                    except Exception:
                        return
                    if path == POST_PATH:
                        lst = body.get("aweme_list") or []
                        if isinstance(lst, list) and lst:
                            page_sizes.append(len(lst))
                            raw_items.extend(lst)
                        has_more = body.get("has_more")
                    elif path == FAVORITE_PATH:
                        favorite_hits += len(body.get("aweme_list") or [])
                    elif path == PROFILE_PATH:
                        user = body.get("user") or {}
                        declared_count = user.get("aweme_count")
                        nickname = user.get("nickname") or nickname

                page.on("response", on_response)
                await _say(f"打开账号主页 {sec_uid[:16]}…（headless，不弹窗）")
                await page.goto(f"https://www.douyin.com/user/{sec_uid}",
                                wait_until="domcontentloaded", timeout=45_000)
                await page.wait_for_timeout(6000)

                seen_ids = set()
                stalled_rounds = 0
                stopped_reason = "unknown"
                for i in range(max_scrolls):
                    own_now, _ = filter_own_posts(raw_items, sec_uid)
                    ids = {str(it.get("aweme_id")) for it in own_now}
                    if len(ids) != len(seen_ids):
                        stalled_rounds = 0
                        seen_ids = ids
                        await _say(f"已翻到 {len(ids)} 条本人作品"
                                   + (f"（账号声明 {declared_count}）" if declared_count else ""))
                    else:
                        stalled_rounds += 1
                    if want and want > 0 and len(seen_ids) >= want:
                        stopped_reason = "enough"
                        break
                    if has_more == 0:
                        stopped_reason = "end"
                        break
                    if stalled_rounds >= stall_limit:
                        stopped_reason = "stalled"
                        break
                    await page.evaluate(SCROLL_JS)
                    await page.wait_for_timeout(pause_ms)
                else:
                    stopped_reason = "max_scrolls"
            finally:
                await browser.close()

    own, foreign = filter_own_posts(raw_items, sec_uid)
    items = dedupe_keep_order([normalize_item(it) for it in own])
    for idx, it in enumerate(items, start=1):
        it["index"] = idx
    if want and want > 0:
        items = items[:want]

    verdict = feed_verdict(stopped_reason, has_more, len(items), want)
    return {
        "sec_uid": sec_uid,
        "nickname": nickname or (items[0]["author_nickname"] if items else ""),
        "declared_count": declared_count,
        "items": items,
        "got": len(items),
        "own_total": len({it["aweme_id"] for it in own}),
        "foreign_dropped": len({str(it.get("aweme_id")) for it in foreign}),
        "favorite_hits": favorite_hits,
        "page_sizes": page_sizes,
        "has_more": has_more,
        "stopped_reason": stopped_reason,
        "reached_end": verdict["reached_end"],
        "maybe_anonymous": verdict["maybe_anonymous"],
        "truncated": verdict["truncated"],
    }
