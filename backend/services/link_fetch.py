# -*- coding: utf-8 -*-
"""下载这条腿的【唯一入口】：分享链接 → 本地视频（解析 + 下载 + 量时长 + 断头片重下）。

为什么搬出 app.py：① 的入池、② 的 prepare-link、② 的反推管线**必须共用同一条腿**，
而它原来住在路由文件里 —— ② 的管线（backend/services/batch_prompt_pipeline.py）要复用就得
反向 import app.py（循环 import），所以它历史上自己抄了一份直接调 `download_douyin_video`，
于是同一件事有了两种行为：① 有两次重试 + 断头片判定，② 一次、且靠默认值吃策略。
现在这边六处调用方全从这里走：① 入池、② prepare-link、② 反推管线、女装 Step4 的模仿视频、
抖音账号批量下载、头条管线 Step1 ⇒ 全仓业务代码里直接调 `download_douyin_video` 的只剩这个
文件里那两处（分发抖音/小红书），另两处是下载器自己 `__main__` 里的自测。
⚠️ 这个项目（8090）里没有判据闸，"只剩一处"这件事在 8099 的 `check_pool_link_add` ⑫ 组里钉。
   两个项目各有一份副本：**函数体必须逐字相同**（8099 那份带闸，是权威副本），只有这段文件说明各写各的。

策略也只在这里定一次：
  · `analyze`：默认 False（只要文件）。要「够不够长」这道门槛的**显式**给 True ——
      这边三处：② 的反推管线、女装 Step4 的模仿视频、头条管线 Step1（原来它们是靠
      `download_douyin_video` 自己的默认 True 吃到的，收口后默认变了 ⇒ 必须写出来）。
  · `cb` 收三种形状：None / list（进度 append 进去）/ 同步或 async 可调用。认形状只在这一处，
      调用方不许自己适配（10-22 就是这里写死 `logs = cb`，传 async 回调当场 AttributeError）。
  · `allow_browser`：**两条都开**。别再当"批量会弹窗"关掉它 —— 10-15 在这台机器实测：
      方案0 SSR `item_list 为空` / yt-dlp `Fresh cookies are needed`（本机没有登录态文件）/ httpx 空，
      三条静默腿全拿不到片，唯一能拿到的是 Playwright（headless，实测 7.19MB · 不弹窗）。
      浏览器腿内部是 headless 先跑、headless 撞验证才降级有头 —— 正常一条链接走不到有头那步。
"""
import asyncio
import inspect
import time
import uuid
from pathlib import Path

from config import settings

UPLOAD_DIR = Path(settings.UPLOAD_DIR)


async def link_fetch_local(text: str, cb=None, out_dir=None,
                           analyze: bool = False, allow_browser: bool = True) -> dict:
    """分享链接 → 本地视频。拿不到就抛 RuntimeError（带原因），不返回半条数据。

    返回：file_path / preview_url / duration / title / size_mb / seconds / platform / logs，
    外加下载器自己报的 author / method / quality 三个字段原样透传（调用方要写台账的那几个数）。
    `cb` 可以是 None、一个 list（进度 append 进去）、或一个同步/async 可调用（每条进度送过去）。
    """
    text = (text or "").strip()
    if not text:
        raise RuntimeError("链接为空")

    from backend.services.douyin_downloader import is_douyin_link, download_douyin_video
    try:
        from backend.services.xhs_downloader import is_xhs_link, download_xhs_video
    except ImportError:
        def is_xhs_link(_t):
            return False
        download_xhs_video = None

    # 默认仍落 uploads/（老路），但入池那条腿会把任务的素材格传进来 —— 一行的片就住在自己那一格
    out_dir = Path(out_dir) if out_dir else (UPLOAD_DIR / "prompt_reverse_links" / uuid.uuid4().hex[:12])
    out_dir.mkdir(parents=True, exist_ok=True)
    # `cb` 收三种形状（调用方历史上三种都在用，分发只在这里做一次）：
    #   None ⇒ 漏斗自己攒一份；list ⇒ 老写法 `cb=logs`，每条进度原样 append 进去；
    #   可调用（同步或 async）⇒ 每条进度都送过去（批量的 stage、任务日志靠它）。
    # 🔴 2026-10-22 修的坑：原来这里写死 `logs = cb`，传进来一个 async 回调时 `logs.append`
    #    直接 AttributeError ⇒ 8090 的 ② 管线（`cb=_dl_cb`）一条链接都下不成，而报出来的话
    #    长得像"下载器坏了"。回调形状由这一处认，不许调用方自己适配。
    logs: list = []

    async def _cb(msg: str):
        s = str(msg)
        logs.append(s)
        if isinstance(cb, list):
            cb.append(s)
        elif callable(cb):
            got = cb(s)
            if inspect.isawaitable(got):
                await got

    t0 = time.time()

    async def _dl_once():
        if is_xhs_link(text) and download_xhs_video:
            return await download_xhs_video(text, str(out_dir), _cb)
        elif is_douyin_link(text):
            return await download_douyin_video(text, str(out_dir), _cb,
                                               analyze=analyze, allow_browser=allow_browser)
        raise RuntimeError("未识别到抖音或小红书链接")

    # 量时长这个函数在两个项目里住的地方不一样（8099 抽进了 `ffmpeg_utils`，8090 还在 `super_clone_wan3` 里）
    # ⇒ 兜着 import。别为了"两份副本逐字相同"把其中一个项目写成 ModuleNotFoundError（10-22 真撞上过：
    #    8090 那份从昨天抄过来就带着 `ffmpeg_utils`，那边根本没这个模块，一调就崩在 import 上）。
    try:
        from backend.services.ffmpeg_utils import _ffprobe_duration
    except ImportError:
        from backend.services.super_clone_wan3 import _ffprobe_duration
    video_path, duration, result = "", 0.0, {}
    for attempt in (1, 2):
        try:
            result = await _dl_once()
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"下载没成：{e}")

        video_path = (result or {}).get("video_path") or ""
        if not video_path or not Path(video_path).exists():
            raise RuntimeError("下载没成（说完成了但盘上没有）：" + " / ".join(str(x) for x in logs[-2:]))

        duration = float(_ffprobe_duration(video_path) or 0.0)
        if duration:
            break
        # 🔴 断头片：ffprobe 读不了 = 文件被截断（2026-10-17 实测：59 条一批里坏 25 条，
        #    mov/mp4 头残缺「no tfhd was found」，缩略图全黑 + 时长未知）。删坏片、缓一下、原地重下一次。
        await _cb(f"第 {attempt} 次下回来是坏片（ffprobe 量不出时长），重下一次")
        try:
            Path(video_path).unlink()
        except Exception:
            pass
        if attempt == 1:
            await asyncio.sleep(3)
    if not duration:
        raise RuntimeError("下载被截断（ffprobe 量不出时长，两次都没下完整）：" + " / ".join(str(x) for x in logs[-2:]))
    try:
        rel = Path(video_path).resolve().relative_to(UPLOAD_DIR.resolve())
        preview_url = f"/uploads/{rel.as_posix()}"
    except ValueError:
        preview_url = ""
    return {
        "file_path": video_path,
        "preview_url": preview_url,
        "duration": duration,
        "title": (result or {}).get("title") or Path(video_path).stem,
        "size_mb": round(Path(video_path).stat().st_size / 1024 / 1024, 2),
        "seconds": round(time.time() - t0, 1),
        "platform": "xiaohongshu" if is_xhs_link(text) else "douyin",
        # 下载器原样报上来的三个字段透传出去（批量下载要 method/quality 写台账、
        # 女装那条要 author）—— 收口不等于把调用方原来拿得到的信息吃掉。
        "author": (result or {}).get("author") or "",
        "method": (result or {}).get("method") or "",
        "quality": (result or {}).get("quality") or "",
        "logs": logs,
    }
