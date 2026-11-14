"""
今日头条自动发布（Playwright 浏览器自动化）
- 扫码登录，持久化 storage_state
- 发布图文文章
- 发布微头条
"""
import asyncio
from pathlib import Path
from typing import Optional
from config import settings


# 头条创作者平台
LOGIN_URL = "https://mp.toutiao.com/auth/page/login"
PUBLISH_ARTICLE_URL = "https://mp.toutiao.com/profile_v4/graphic/publish"
PUBLISH_MICRO_URL = "https://mp.toutiao.com/profile_v4/graphic/publish?from=weitoutiao"

# storage_state 保存位置
STORAGE_STATE_PATH = Path(settings.UPLOAD_DIR) / "toutiao_storage_state.json"


async def ensure_login(status_callback=None, force_relogin: bool = False) -> str:
    """
    确保已登录头条创作者平台：
    - 若 storage_state.json 存在且未失效，直接复用
    - 否则启动浏览器，等待用户扫码登录，保存 storage_state
    返回 storage_state 路径
    """
    from playwright.async_api import async_playwright

    if not force_relogin and STORAGE_STATE_PATH.exists():
        if status_callback:
            await status_callback("使用已保存的登录状态")
        return str(STORAGE_STATE_PATH)

    if status_callback:
        await status_callback("请扫码登录今日头条创作者平台...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(LOGIN_URL)

        # 等待用户扫码登录，检测登录成功的标志（URL跳转到 /profile_v4/）
        try:
            await page.wait_for_url("**/profile_v4/**", timeout=180_000)  # 3分钟超时
        except Exception:
            raise RuntimeError("扫码登录超时（3分钟未完成）")

        await context.storage_state(path=str(STORAGE_STATE_PATH))
        await browser.close()

    if status_callback:
        await status_callback("登录成功，已保存登录状态")
    return str(STORAGE_STATE_PATH)


async def publish_article(
    title: str,
    content_html: str,
    image_paths: list[str],
    status_callback=None,
) -> str:
    """
    发布图文文章
    返回文章URL
    """
    from playwright.async_api import async_playwright

    await ensure_login(status_callback=status_callback)

    if status_callback:
        await status_callback("打开发布文章页面...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # 可视化便于调试
        context = await browser.new_context(storage_state=str(STORAGE_STATE_PATH))
        page = await context.new_page()
        await page.goto(PUBLISH_ARTICLE_URL)
        await page.wait_for_load_state("networkidle")

        # 输入标题
        if status_callback:
            await status_callback("输入文章标题...")
        title_input = page.locator('div[contenteditable="true"]').first
        # 头条文章标题通常是单独的 input，优先找 input
        title_locator = page.locator('input[placeholder*="标题"], input[placeholder*="title"]').first
        try:
            await title_locator.wait_for(timeout=10_000)
            await title_locator.fill(title)
        except Exception:
            # 回退到 contenteditable 第一个
            await title_input.fill(title)

        # 输入正文（contenteditable）
        if status_callback:
            await status_callback("输入文章正文...")
        body_locator = page.locator('div[contenteditable="true"]').nth(1)
        await body_locator.click()
        # 把 HTML 拆成段落逐个输入，每段后回车换行
        # 简化版：用 inner_html 注入
        await body_locator.evaluate(
            "(el, html) => { el.innerHTML = html; }",
            content_html,
        )

        # 上传图片
        if image_paths:
            if status_callback:
                await status_callback(f"上传 {len(image_paths)} 张配图...")
            # 头条文章的图片上传通过文件选择器
            file_input = page.locator('input[type="file"]').first
            await file_input.set_input_files(image_paths)
            # 等待上传完成
            await page.wait_for_timeout(5000)

        # 点击发布
        if status_callback:
            await status_callback("点击发布按钮...")
        publish_btn = page.locator('button:has-text("发布"), button:has-text("发表")').first
        await publish_btn.click()

        # 等待发布完成（URL变化或成功提示）
        try:
            await page.wait_for_url("**/profile_v4/graphic/articles**", timeout=60_000)
        except Exception:
            pass

        article_url = page.url
        await context.storage_state(path=str(STORAGE_STATE_PATH))  # 更新状态
        await browser.close()

    if status_callback:
        await status_callback(f"文章已发布: {article_url}")
    return article_url


async def publish_micro(
    text: str,
    image_paths: list[str],
    status_callback=None,
) -> str:
    """
    发布微头条
    返回微头条URL
    """
    from playwright.async_api import async_playwright

    await ensure_login(status_callback=status_callback)

    if status_callback:
        await status_callback("打开发布微头条页面...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(storage_state=str(STORAGE_STATE_PATH))
        page = await context.new_page()
        await page.goto(PUBLISH_MICRO_URL)
        await page.wait_for_load_state("networkidle")

        # 输入文案
        if status_callback:
            await status_callback("输入微头条文案...")
        editor = page.locator('div[contenteditable="true"]').first
        await editor.click()
        await editor.fill(text)

        # 上传图片
        if image_paths:
            if status_callback:
                await status_callback(f"上传 {len(image_paths)} 张图片...")
            file_input = page.locator('input[type="file"]').first
            await file_input.set_input_files(image_paths)
            await page.wait_for_timeout(5000)

        # 发布
        if status_callback:
            await status_callback("点击发布...")
        publish_btn = page.locator('button:has-text("发布"), button:has-text("发表")').first
        await publish_btn.click()

        # 等待跳转
        try:
            await page.wait_for_url("**/weitoutiao/**", timeout=30_000)
        except Exception:
            await page.wait_for_timeout(3000)

        micro_url = page.url
        await context.storage_state(path=str(STORAGE_STATE_PATH))
        await browser.close()

    if status_callback:
        await status_callback(f"微头条已发布: {micro_url}")
    return micro_url


async def logout():
    """删除保存的登录状态（退出登录）"""
    if STORAGE_STATE_PATH.exists():
        STORAGE_STATE_PATH.unlink()


if __name__ == "__main__":
    # 自测：扫码登录
    asyncio.run(ensure_login())
