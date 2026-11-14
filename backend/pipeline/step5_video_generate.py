"""
============================================================================
⚠️ 爆款复刻工作流 - 受保护文件（整文件保护）
参见 BOUNDARY.md。修改本文件必须经过显式确认：
  1. 运行 python scripts/check_replica_boundary.py 记录变更
  2. 运行 pytest tests/test_replica_boundary.py -v 验证行为
  3. 运行 python scripts/update_replica_boundary.py 更新签名基准
未经流程确认的修改将破坏工作流稳定性。
============================================================================

Step5: 视频生成（提交到 weeksir.com/video 页面）

页面结构（dev.weeksir.com/video，2026-07-26 调试结果）:
  Composer 区域 (.video-page__composer):
    1. textarea - 提示词输入（placeholder: "例如：一只白色运动鞋..."）
    2. file input - 上传参考图（accept: image/*，最多7张）
       - 两个 Tab: "参考"（上传参考图）和"首尾帧"
    3. 模型选择 select (.video-page__native-model-select)
       选项: seedance-2.0-renlian（不卡人脸）等
    4. 参数按钮:
       - 规格: 720p (.video-page__parameter-trigger)
       - 比例: 16:9 → 改为 9:16
       - 时长: 8s → 改为 15s
       - 数量: 生成1条
    5. 生成按钮: "开始生成" (.video-page__generate)

  交互流程:
    1. 填写提示词到 textarea
    2. 上传参考图（故事板、产品图等）
    3. 选择模型：不卡人脸 Seedance 2.0
    4. 设置参数：9:16、15s
    5. 点击"开始生成"
    6. 等待生成完成（页面出现视频播放器/下载按钮）
"""
import asyncio
import json
import re
import time
import httpx
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# ── 提示词（动态生成，允许通过 prompts.json 覆盖） ──
# 图片位置约定：
#   @图片1 = 故事板（分镜画面权威）
#   @图片2 ~ @图片N = 产品图（可多张，产品一致性）
#   @图片N+1 = 原视频分镜图shot1（仅参考光影效果和人物皮肤质感）

# prompts.json 覆盖（若提供则使用自定义完整提示词，跳过动态生成）
_CUSTOM_PROMPT_OVERRIDE = None
try:
    from prompts_loader import get_prompt
    _CUSTOM_PROMPT_OVERRIDE = get_prompt("step5_video", "PROMPT")
except Exception:
    pass


def _build_prompt(product_count: int, has_original_frame: bool) -> str:
    """根据实际产品图数量和是否有原视频分镜图，动态生成提示词。

    Args:
        product_count: 实际上传的产品图数量（位于 @图片2 ~ @图片N+1 之间，N = product_count + 1）
        has_original_frame: 是否上传了原视频分镜图shot1

    Returns:
        完整提示词字符串
    """
    # 产品图引用：@图片2 单张 / @图片2~@图片N+1 多张
    if product_count <= 0:
        product_ref = ""
    elif product_count == 1:
        product_ref = "@图片2"
    else:
        product_ref = f"@图片2~@图片{product_count + 1}"

    # 原视频分镜图shot1 引用编号（故事板 1 + 产品 N + 1 = N+2）
    original_idx = product_count + 2
    original_ref = f"@图片{original_idx}" if has_original_frame else ""

    parts = [
        "【全局禁令·最高优先级】严禁画面出现任何字幕、文字、标题、水印、贴纸、Logo标识、说明性文字、弹幕、字幕条、中英文字符、数字、符号！"
        "口播内容只能用语音播放，绝对不得作为字幕、文字、标题叠加在画面上！"
        "\n\n请严格按照@图片1 故事板的分镜画面生成，不可自己更改故事板分镜画面，"
        "进行推理保持产品的一致性保持模特一致性,"
        "严禁生成任何字幕、文字、标题、水印、贴纸、Logo标识、说明性文字！"
        "画面中不得出现任何可见文字或字符，包括但不限于中英文、数字、符号；"
        "严格按照故事板的口播文字内容，进行口播（仅语音播放，禁止画面显示字幕）！"
    ]

    if product_ref:
        parts.append(
            f"\n\n{product_ref} 为产品参考图，必须严格保持产品外观、颜色、材质、纹理、"
            f"工艺等细节的一致性，不可自行更改产品形态。"
        )

    if original_ref:
        parts.append(
            f"\n\n{original_ref} 仅用于参考原视频的光影效果（光照方向、色调氛围、明暗对比、阴影质感）"
            f"和人物皮肤质感（毛孔、肤质、肤色、面部光泽、皮下血管纹理），"
            f"禁止参考{original_ref} 的画面内容、人物动作、构图或产品形态；"
            f"光影风格必须与{original_ref} 保持一致，营造真实自然的光照氛围。"
        )

    parts.append(
        "\n\n"
        "人物高清原生毛孔，皮下血管纹理，自然鱼尾细纹，面部微汗光泽，原生通透皮肤，"
        "关闭过度磨皮，100%细节强度，完整保留发丝、皮肤与面料肌理，"
        "柔和侧光塑造面部立体骨骼，Canon EF85mm f/1.2镜头，胶片摄影质感，"
        "超写实真人肤质，无假面、无塑料皮肤，边缘清晰无锯齿模糊，超清4K极致细节"
    )

    # 字幕禁令作为结尾强约束（结尾权重高，模型更易遵守）
    parts.append(
        "\n\n【结尾最终禁令·必须执行】再次强调：严禁画面出现字幕、文字、水印、标题、贴纸、Logo、弹幕、字幕条等任何可见字符，"
        "画面保持纯净无文字！分镜脚本中的口播内容只能用语音播放，绝对不得以字幕、文字、标题、贴纸任何形式叠加在画面上！"
        "如果画面中出现任何字符，判定为生成失败！"
    )

    return "".join(parts)


def _format_storyboard_text(storyboard_json: list) -> str:
    """将分镜脚本格式化为可读文字，拼到提示词末尾。
    图片里的文字可能看不清，文字配合确保口播/画面内容准确传达。"""
    if not storyboard_json:
        return ""
    lines = ["", "====================分镜脚本（文字配合，避免图片文字看不清）===================="]
    for shot in storyboard_json:
        if not isinstance(shot, dict):
            continue
        num = shot.get("shot", "?")
        tr = shot.get("时间区间", {}) or {}
        start = tr.get("开始时间", "")
        end = tr.get("结束时间", "")
        dur = tr.get("镜头时长", "")
        env = shot.get("场景环境", {}) or {}
        scene = env.get("场景", "")
        act = shot.get("画面动作", {}) or {}
        # 防御性清洗：剥离"画面内容"中残留的原视频字幕描述（形如"...｜字幕：xxx"或"...|字幕：xxx"）
        _raw_visual = act.get("画面内容", "") or ""
        for _sep in ["｜字幕", "|字幕", "｜字幕：", "|字幕："]:
            if _sep in _raw_visual:
                _raw_visual = _raw_visual.split(_sep)[0].rstrip()
                break
        # 同时移除"无字幕"标记
        _raw_visual = _raw_visual.replace("｜无字幕", "").replace("|无字幕", "").rstrip()
        visual = _raw_visual
        person_act = act.get("人物动作", "")
        product_act = act.get("产品动作", "")
        pp = shot.get("人物与产品", {}) or {}
        person = pp.get("人物", "")
        product = pp.get("产品", "")
        voice = shot.get("口播", "")
        vt = shot.get("声音语气", {}) or {}
        speed = vt.get("语速", "")
        tone = vt.get("语调", "")
        emotion = vt.get("情绪", "")
        sfx = shot.get("音效", "")
        parts = [f"Shot {num} [{start}-{end}/{dur}]"]
        if scene:
            parts.append(f"场景：{scene}")
        if visual:
            parts.append(f"画面：{visual}")
        if person:
            parts.append(f"人物：{person}")
        if person_act:
            parts.append(f"动作：{person_act}")
        if product:
            parts.append(f"产品：{product}")
        if product_act:
            parts.append(f"产品动作：{product_act}")
        if voice:
            parts.append(f'口播（仅语音，禁止显示字幕）："{voice}"')
        if speed or tone or emotion:
            parts.append(f"语气：{speed}/{tone}/{emotion}")
        if sfx:
            parts.append(f"音效：{sfx}")
        lines.append("  ".join(str(p) for p in parts if p))
    lines.append("=" * 60)
    return "\n".join(lines)

# ── 网站配置 ──
WEEKSIR_URL = "https://weeksir.com/video/"
# 🔴 2026-10-08 明文凭据清零（BOUNDARY §三.1 bug 修复 + §三.2 用户明确要求）：
#    原来这两行是明文账号密码，且散进另外 13 个文件 ⇒ 改成只从 config/.env 读，取不到就大声报错，
#    不再拿空串去点登录按钮（那会表现为"登录失败"而不是"没配凭据"，最难查的那种）。
#    ⚠️ 旧密码已经进过对话与多个文件 ⇒ 只能作废轮换；轮换只改 .env，本文件不用再动。
try:  # 与文件既有取 settings 的方式一致：仓库根在 sys.path 上
    from config import settings as _settings
except Exception:  # pragma: no cover - 只有被独立脚本方式跑时才会走到
    _settings = None


def _weeksir_credentials() -> tuple:
    user = (getattr(_settings, "WEEKSIR_USERNAME", "") or "").strip()
    pwd = (getattr(_settings, "WEEKSIR_PASSWORD", "") or "").strip()
    if not user or not pwd:
        raise RuntimeError(
            "weeksir 登录凭据未配置：请在 D:\\video-skill\\.env 里设置 "
            "WEEKSIR_USERNAME / WEEKSIR_PASSWORD（源码不再内置账号密码）"
        )
    return user, pwd

# 浏览器 profile 目录（持久化登录态）
PROFILE_DIR = str(Path(__file__).parent.parent / "weeksir_profile")

# 目标模型值
MODEL_NO_FACE_LOCK = "seedance-2.0-renlian"
# Seedance2.5：支持最长 30s 单次生成
MODEL_SEEDANCE_2_5 = "seedance-2.5"


def _run_async(coro):
    """安全运行异步协程（兼容线程中无事件循环的情况）"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    else:
        return asyncio.run(coro)


async def _launch_context(headless: bool = False):
    """启动浏览器持久化上下文"""
    # 清理上次异常退出遗留的浏览器锁文件，避免 "已有另一个会话在运行" 错误
    _cleanup_browser_locks()

    playwright = await async_playwright().start()
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=PROFILE_DIR,
        headless=headless,
        viewport={"width": 1440, "height": 1000},
        locale="zh-CN",
        args=[
            "--disable-blink-features=AutomationControlled",
            "--start-maximized",
        ],
    )
    return playwright, context


def _cleanup_browser_locks():
    """清理 Chromium 持久化目录的锁文件和遗留进程，避免上次异常退出导致下次启动失败"""
    import subprocess
    profile_path = Path(PROFILE_DIR)
    if not profile_path.exists():
        return

    # 1. kill 掉所有使用 weeksir_profile 的 chrome.exe 遗留进程
    #    （上次任务异常退出时可能留下僵尸进程，导致新会话"已有另一个会话在运行"）
    try:
        # 查找使用该 profile 的 chrome 进程
        result = subprocess.run(
            ["wmic", "process", "where",
             "name='chrome.exe'",
             "get", "ProcessId,CommandLine"],
            capture_output=True, text=True, timeout=10,
        )
        killed_pids = []
        if result.returncode == 0 and result.stdout:
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line or "ProcessId" in line:
                    continue
                if str(PROFILE_DIR).lower() in line.lower():
                    # 提取最后一列的 PID
                    parts = line.split()
                    if parts:
                        try:
                            pid = int(parts[-1])
                            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                          capture_output=True, timeout=5)
                            killed_pids.append(pid)
                        except (ValueError, subprocess.SubprocessError):
                            pass
        if killed_pids:
            print(f"  → 清理遗留 Chrome 进程: PIDs {killed_pids}", flush=True)
            import time
            time.sleep(1)  # 等待进程完全退出
    except Exception as e:
        print(f"  ⚠ 清理 Chrome 进程时出错（可忽略）: {e}", flush=True)

    # 2. 清理 Chromium SingletonLock 系列文件
    lock_files = ["SingletonLock", "SingletonCookie", "SingletonSocket"]
    cleaned = []
    for name in lock_files:
        f = profile_path / name
        if f.exists():
            try:
                f.unlink()
                cleaned.append(name)
            except Exception:
                pass

    # 3. 清理 DevToolsActivePort 文件（影响新会话）
    port_file = profile_path / "DevToolsActivePort"
    if port_file.exists():
        try:
            port_file.unlink()
            cleaned.append("DevToolsActivePort")
        except Exception:
            pass

    if cleaned:
        print(f"  → 清理遗留浏览器锁文件: {', '.join(cleaned)}")


async def _do_login(page):
    """在登录页面执行登录"""
    print("  → 检测到未登录，正在登录...", flush=True)

    account_input = page.locator("input[placeholder='请输入账户']").first
    await account_input.wait_for(state="visible", timeout=10000)
    _wk_user, _wk_pwd = _weeksir_credentials()
    await account_input.fill(_wk_user)

    pwd_input = page.locator("input[placeholder='请输入密码']").first
    await pwd_input.fill(_wk_pwd)

    checkbox = page.locator("input[type='checkbox']").first
    if not await checkbox.is_checked():
        await checkbox.check()

    submit_btn = page.locator("button.submit-button").first
    await submit_btn.click()

    await page.wait_for_timeout(8000)
    print(f"  → 登录完成，URL: {page.url}", flush=True)


async def _navigate_and_login(page):
    """导航到视频生成页面并处理登录"""
    print(f"  → 导航到: {WEEKSIR_URL}", flush=True)
    await page.goto(WEEKSIR_URL, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(5000)

    if "/login" in page.url:
        await _do_login(page)
        await page.wait_for_timeout(5000)
        await page.goto(WEEKSIR_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(8000)

    print(f"  → 当前页面URL: {page.url}", flush=True)


async def _wait_for_page_ready(page):
    """等待 /video 页面 composer 区域渲染"""
    print("  → 等待页面元素渲染...", flush=True)

    try:
        await page.locator(".video-page__composer").first.wait_for(
            state="visible", timeout=15000
        )
        print("  ✓ composer 已渲染", flush=True)
    except PlaywrightTimeoutError:
        print("  ⚠ composer 未出现，继续尝试...", flush=True)

    await page.wait_for_timeout(2000)


async def _fill_prompt(page, prompt: str):
    """填写提示词到 textarea"""
    print(f"  → 填写提示词...", flush=True)

    textarea = page.locator("textarea").first
    await textarea.wait_for(state="visible", timeout=30000)
    await textarea.fill(prompt)
    print(f"  ✓ 提示词已填写（{len(prompt)}字）")


async def _upload_reference_images(page, image_paths: list):
    """上传参考图到 /video 页面

    页面有1个 file input，accept=image/*，最多7张
    需要确认在"参考" tab 下
    """
    print(f"  → 上传参考图（{len(image_paths)}张）...")

    # 确保在"参考"tab下
    ref_tab = page.locator(".video-page__composer-tab:has-text('参考')").first
    try:
        if await ref_tab.is_visible(timeout=3000):
            is_active = await ref_tab.evaluate("el => el.classList.contains('is-active')")
            if not is_active:
                await ref_tab.click()
                await page.wait_for_timeout(1000)
                print("  ✓ 已切换到'参考'tab", flush=True)
    except Exception:
        pass

    # 逐张上传
    file_input = page.locator("input[type='file'].video-page__file-input, input[type='file']").first

    for i, img_path in enumerate(image_paths):
        path = Path(img_path)
        if not path.exists():
            print(f"  ⚠ 图片不存在: {img_path}", flush=True)
            continue

        try:
            # 每次上传前重新定位 file input（DOM可能刷新）
            file_input = page.locator("input[type='file']").first
            await file_input.set_input_files(str(path))
            await page.wait_for_timeout(2000)
            print(f"  ✓ [{i+1}] {path.name} 上传成功", flush=True)
        except Exception as e:
            print(f"  ⚠ [{i+1}] {path.name} 上传失败: {e}", flush=True)

    # 检查上传结果
    thumb_count = await page.locator(".video-page__reference-thumbs img, .video-page__reference-thumbs [class*='thumb']").count()
    print(f"  → 参考图上传完成，当前缩略图数: {thumb_count}", flush=True)


async def _select_model(page, model_value: str = MODEL_NO_FACE_LOCK):
    """选择视频生成模型

    方式1: 通过隐藏的 select 元素设置值
    方式2: 通过 model-trigger 按钮打开面板选择
    """
    print(f"  → 选择模型: {model_value}", flush=True)

    # 方式1: 通过隐藏 select
    try:
        select = page.locator("select.video-page__native-model-select").first
        await select.wait_for(state="attached", timeout=10000)
        current_value = await select.evaluate("el => el.value")
        if current_value == model_value:
            print(f"  ✓ 模型已是目标值", flush=True)
            return True

        await select.select_option(value=model_value)
        await page.wait_for_timeout(1500)

        # 验证是否生效
        new_value = await select.evaluate("el => el.value")
        if new_value == model_value:
            print(f"  ✓ 模型已切换（select）", flush=True)
            return True
        else:
            print(f"  ⚠ select设置后值={new_value}，未生效", flush=True)
    except Exception as e:
        print(f"  ⚠ select方式失败: {e}", flush=True)

    # 方式2: 通过 model trigger 面板
    try:
        model_trigger = page.locator("button.video-page__model-trigger").first
        await model_trigger.click()
        await page.wait_for_timeout(1500)

        # 找到"不卡人脸"选项（通过文本内容匹配）
        options = page.locator("button.video-page__model-option")
        count = await options.count()
        for i in range(count):
            opt = options.nth(i)
            text = await opt.text_content()
            if text and "不卡人脸" in text:
                await opt.click()
                await page.wait_for_timeout(1500)
                print(f"  ✓ 模型已切换（trigger面板）", flush=True)
                return True
        print(f"  ⚠ trigger面板中未找到'不卡人脸'选项", flush=True)
        await page.keyboard.press("Escape")
    except Exception as e2:
        print(f"  ⚠ trigger方式也失败: {e2}", flush=True)
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass

    return False


async def _dismiss_dialogs(page):
    """关闭页面上可能存在的弹窗（base-dialog等）"""
    for _ in range(3):
        try:
            dialog = page.locator("div.base-dialog[aria-modal='true']").first
            if await dialog.is_visible(timeout=1000):
                close_btn = dialog.locator("button[aria-label='关闭弹窗']").first
                if await close_btn.is_visible(timeout=500):
                    await close_btn.click()
                    await page.wait_for_timeout(500)
                    print("  ✓ 已关闭弹窗", flush=True)
                    continue
        except Exception:
            pass
        try:
            # 备选：点击遮罩层关闭
            mask = page.locator("button.base-dialog__mask").first
            if await mask.is_visible(timeout=500):
                await mask.evaluate("el => el.click()")
                await page.wait_for_timeout(500)
                print("  ✓ 已通过遮罩关闭弹窗", flush=True)
                continue
        except Exception:
            pass
        break
    # 最终兜底：按 Escape
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(300)


async def _set_parameters(page, aspect_ratio: str = "9:16", duration: str = "15s", resolution: str = "720p"):
    """设置视频生成参数（比例、时长、分辨率）— 带重试和硬校验"""
    print(f"  → 设置参数: {resolution} · {duration} · {aspect_ratio}", flush=True)

    errors = []

    # 设置比例（最多重试3次）
    ratio_ok = False
    for attempt in range(3):
        try:
            # 先关闭可能存在的弹窗和面板
            await _dismiss_dialogs(page)
            await page.wait_for_timeout(500)

            ratio_btn = page.locator("button.video-page__parameter-trigger").filter(has_text="比例").first
            await ratio_btn.click()
            await page.wait_for_timeout(1500)

            # 精确匹配目标选项（优先精确匹配，再模糊匹配）
            target = page.locator("button.video-page__parameter-option").filter(has_text=aspect_ratio).first
            await target.wait_for(state="visible", timeout=5000)
            await target.click()
            await page.wait_for_timeout(1000)

            # 关闭面板
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)

            # 验证
            ratio_btn2 = page.locator("button.video-page__parameter-trigger").filter(has_text="比例").first
            btn_text = await ratio_btn2.text_content()
            if aspect_ratio in (btn_text or ""):
                print(f"  ✓ 比例已设置为 {aspect_ratio}（第{attempt+1}次尝试）", flush=True)
                ratio_ok = True
                break
            else:
                print(f"  ⚠ 比例设置后按钮文本: {btn_text}，重试...", flush=True)
        except Exception as e:
            print(f"  ⚠ 设置比例失败（第{attempt+1}次）: {e}", flush=True)
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass

    if not ratio_ok:
        errors.append(f"比例设置失败，未能设为{aspect_ratio}")

    # 设置时长（最多重试3次）
    duration_ok = False
    for attempt in range(3):
        try:
            await _dismiss_dialogs(page)
            await page.wait_for_timeout(500)

            duration_btn = page.locator("button.video-page__parameter-trigger").filter(has_text="时长").first
            await duration_btn.click()
            await page.wait_for_timeout(1500)

            target = page.locator("button.video-page__parameter-option").filter(has_text=duration).first
            await target.wait_for(state="visible", timeout=5000)
            await target.click()
            await page.wait_for_timeout(1000)

            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)

            duration_btn2 = page.locator("button.video-page__parameter-trigger").filter(has_text="时长").first
            btn_text = await duration_btn2.text_content()
            if duration in (btn_text or ""):
                print(f"  ✓ 时长已设置为 {duration}（第{attempt+1}次尝试）", flush=True)
                duration_ok = True
                break
            else:
                print(f"  ⚠ 时长设置后按钮文本: {btn_text}，重试...", flush=True)
        except Exception as e:
            print(f"  ⚠ 设置时长失败（第{attempt+1}次）: {e}", flush=True)
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass

    if not duration_ok:
        errors.append(f"时长设置失败，未能设为{duration}")

    # 设置分辨率/规格（最多重试3次）
    resolution_ok = False
    for attempt in range(3):
        try:
            await _dismiss_dialogs(page)
            await page.wait_for_timeout(500)

            res_btn = page.locator("button.video-page__parameter-trigger").filter(has_text="规格").first
            await res_btn.click()
            await page.wait_for_timeout(1500)

            target = page.locator("button.video-page__parameter-option").filter(has_text=resolution).first
            await target.wait_for(state="visible", timeout=5000)
            await target.click()
            await page.wait_for_timeout(1000)

            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)

            res_btn2 = page.locator("button.video-page__parameter-trigger").filter(has_text="规格").first
            btn_text = await res_btn2.text_content()
            if resolution in (btn_text or ""):
                print(f"  ✓ 规格已设置为 {resolution}（第{attempt+1}次尝试）", flush=True)
                resolution_ok = True
                break
            else:
                print(f"  ⚠ 规格设置后按钮文本: {btn_text}，重试...", flush=True)
        except Exception as e:
            print(f"  ⚠ 设置规格失败（第{attempt+1}次）: {e}", flush=True)
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass

    if not resolution_ok:
        errors.append(f"规格设置失败，未能设为{resolution}")

    # 最终硬校验：重新读取按钮文本确认
    await page.wait_for_timeout(1000)
    final_check = await page.evaluate("""() => {
        const triggers = document.querySelectorAll('button.video-page__parameter-trigger');
        const result = {};
        triggers.forEach(btn => {
            const text = btn.textContent?.trim() || '';
            if (text.includes('比例')) result.ratio = text;
            if (text.includes('时长')) result.duration = text;
            if (text.includes('规格')) result.resolution = text;
        });
        return result;
    }""")
    print(f"  → 最终参数校验: {final_check}", flush=True)

    if aspect_ratio not in final_check.get("ratio", ""):
        errors.append(f"比例硬校验失败: 期望含'{aspect_ratio}'，实际'{final_check.get('ratio', '')}'")
    if duration not in final_check.get("duration", ""):
        errors.append(f"时长硬校验失败: 期望含'{duration}'，实际'{final_check.get('duration', '')}'")
    if resolution not in final_check.get("resolution", ""):
        errors.append(f"规格硬校验失败: 期望含'{resolution}'，实际'{final_check.get('resolution', '')}'")

    if errors:
        error_msg = "；".join(errors)
        print(f"  ✗ 参数设置错误: {error_msg}", flush=True)
        raise ValueError(f"Step5 参数设置未通过校验: {error_msg}")


async def _click_generate(page):
    """点击"开始生成"按钮"""
    print("  → 点击'开始生成'按钮...", flush=True)

    # 等待按钮启用
    max_wait = 30
    for i in range(max_wait):
        btn = page.locator(".video-page__generate").first
        try:
            if await btn.is_visible(timeout=3000):
                is_disabled = await btn.evaluate("""el => {
                    return el.classList.contains('is-soft-disabled') || el.disabled;
                }""")
                if not is_disabled:
                    break
        except Exception:
            pass
        await page.wait_for_timeout(1000)

    try:
        btn = page.locator(".video-page__generate").first
        await btn.wait_for(state="visible", timeout=10000)

        # 尝试移除 is-soft-disabled
        await btn.evaluate("""el => {
            el.classList.remove('is-soft-disabled');
            el.removeAttribute('disabled');
        }""")

        await btn.click()
        print("  ✓ 已点击'开始生成'", flush=True)
    except Exception as e:
        print(f"  ⚠ 点击失败: {e}", flush=True)
        try:
            await btn.evaluate("el => el.click()")
            print("  ✓ 已通过JS点击'开始生成'", flush=True)
        except Exception as e2:
            print(f"  ✗ JS点击也失败: {e2}", flush=True)


def _setup_video_capture(page) -> list:
    """挂载网络响应监听，捕获所有视频相关的真实 URL。

    weeksir.com/video 的结果视频常以 blob: URL 暴露在 <video>.src，
    真正的 OSS MP4 URL 只在网络请求中出现。监听 response 事件是最可靠的回传方式。

    收集规则（任一命中即收集）：
      - URL 含 ".mp4"
      - URL 含 "weeksir-video"
      - content-type 以 "video/" 开头
    过滤规则（排除非用户生成结果）：
      - URL 含 "video-examples"（页面示例视频）
      - URL 含 "example"（示例资源）
      - URL 含 "/static/"（静态资源）
    返回捕获列表（供 _wait_for_video_result 轮询）。
    """
    captured: list = []

    async def _on_response(response):
        try:
            url = response.url or ""
            ctype = (response.headers or {}).get("content-type", "")
            if ".mp4" in url or "weeksir-video" in url or ctype.startswith("video/"):
                if any(kw in url for kw in (
                    "video-examples", "example", "/static/", "demo",
                    "reference_image", "video-assets",
                )):
                    return
                if url not in captured and not url.startswith("blob:"):
                    captured.append(url)
                    print(f"  [net] 捕获视频相关响应: {url[:120]}", flush=True)
        except Exception:
            pass

    page.on("response", _on_response)
    return captured


async def _wait_for_video_result(page, timeout: int = 900, captured_video_urls: Optional[list] = None):
    """等待视频生成完成"""
    print(f"  → 等待视频生成（最多 {timeout} 秒）...", flush=True)

    start = time.time()
    task_uuid = None

    while time.time() - start < timeout:
        try:
            # 检测 task_uuid
            current_url = page.url
            if "task_uuid=" in current_url and task_uuid is None:
                import urllib.parse
                parsed = urllib.parse.urlparse(current_url)
                params = urllib.parse.parse_qs(parsed.query)
                task_uuid = params.get("task_uuid", [None])[0]
                print(f"  → 检测到任务: task_uuid={task_uuid}", flush=True)

            # ① 优先：网络监听捕获到的视频 URL（最可靠，blob: 背后的真实 OSS 地址）
            # 如果捕获到 video-results 路径的 mp4（真实生成结果），即使没有 task_uuid 也直接使用
            # （weeksir.com 可能改版，URL 中不再包含 task_uuid 参数）
            status_now = ""
            try:
                status_now = await page.locator(".video-page__current-status").first.inner_text(timeout=500)
            except Exception:
                status_now = ""
            if any(k in (status_now or "") for k in ("排队", "队列", "生成中", "进行中")):
                elapsed_now = int(time.time() - start)
                if elapsed_now % 5 == 0 and elapsed_now > 0:
                    print(f"  → 等待本任务出片（{status_now.strip()[:40]}）... ({elapsed_now}s)", flush=True)
                await asyncio.sleep(3)
                continue

            if captured_video_urls:
                for url in reversed(captured_video_urls):
                    if ".mp4" in url and "thumb" not in url and "preview" not in url:
                        # video-results/public/ 路径是真实生成结果，直接使用
                        if "video-results" in url:
                            print(f"  ✓ 找到视频URL (net, video-results): {url[:100]}...")
                            if not task_uuid:
                                task_uuid = "detected_via_network"
                            return {"status": "ok", "video_url": url, "task_uuid": task_uuid}
                        # 有 task_uuid 时使用其他 URL
                        if task_uuid:
                            print(f"  ✓ 找到视频URL (net): {url[:100]}...")
                            return {"status": "ok", "video_url": url, "task_uuid": task_uuid}

            # ② DOM 扫描（放宽选择器 + data-url + source 标签）
            result_info = await page.evaluate("""() => {
                const body = document.body.innerText;
                const hasFailed = body.includes('生成失败') || body.includes('任务失败');
                const hasCompleted = body.includes('已完成') || body.includes('生成完成');

                // 结果视频播放器（放宽到所有 video）
                const resultVideos = document.querySelectorAll('video');
                let videoSrc = '';
                resultVideos.forEach(v => {
                    const s = v.src || v.currentSrc || '';
                    if (s && !s.startsWith('blob:') && !s.includes('example') && s.startsWith('http')) {
                        videoSrc = s;
                    }
                });
                // <video><source src="..."></video>
                if (!videoSrc) {
                    document.querySelectorAll('video source').forEach(s => {
                        const src = s.src || s.getAttribute('src') || '';
                        if (src && src.startsWith('http') && !src.includes('example') && src.includes('.mp4')) {
                            videoSrc = src;
                        }
                    });
                }

                // 下载链接（放宽：任意含 .mp4 的 a[href]，或 a[download]）
                const downloadLinks = Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.href)
                    .filter(h => h && (h.includes('.mp4') || h.includes('weeksir-video')));

                // data-url / data-video 等属性
                const dataUrls = [];
                document.querySelectorAll('[data-url], [data-video-url], [data-video], [data-src]').forEach(el => {
                    ['data-url','data-video-url','data-video','data-src'].forEach(attr => {
                        const v = el.getAttribute(attr);
                        if (v && v.includes('.mp4')) dataUrls.push(v);
                    });
                });

                // 检查进度/状态
                const statusEl = document.querySelector('.video-page__current-status');
                const statusText = statusEl ? statusEl.textContent?.substring(0, 100)?.trim() : '';

                return { hasFailed, hasCompleted, videoSrc, downloadLinks, dataUrls, statusText };
            }""")

            if result_info.get("hasFailed"):
                # 页面显示失败，但如果网络层已捕获到 video-results 的 mp4，可能是误判（旧提示残留）
                has_real_video = any("video-results" in u for u in (captured_video_urls or []))
                if has_real_video:
                    print("  ⚠ 页面显示失败，但网络层已捕获到视频，尝试使用捕获的 URL", flush=True)
                    for url in reversed(captured_video_urls):
                        if ".mp4" in url and "video-results" in url:
                            if not task_uuid:
                                task_uuid = "detected_via_network"
                            print(f"  ✓ 找到视频URL (net, fallback): {url[:100]}...")
                            return {"status": "ok", "video_url": url, "task_uuid": task_uuid}
                print("  ✗ 视频生成失败", flush=True)
                return {"status": "failed", "video_url": None, "task_uuid": task_uuid}

            # 所有检测路径都必须等 task_uuid 出现后才认为视频 URL 有效，
            # 避免误用页面残留的旧视频/示例视频
            if not task_uuid:
                elapsed_now = int(time.time() - start)
                if elapsed_now % 15 == 0 and elapsed_now > 0:
                    print(f"  → 等待任务提交（task_uuid）... ({elapsed_now}s)")
                await asyncio.sleep(2)
                continue

            # 找到视频URL
            if result_info.get("videoSrc"):
                print(f"  ✓ 找到视频URL (video): {result_info['videoSrc'][:100]}...")
                return {"status": "ok", "video_url": result_info["videoSrc"], "task_uuid": task_uuid}

            if result_info.get("downloadLinks"):
                url = result_info["downloadLinks"][0]
                print(f"  ✓ 找到视频URL (link): {url[:100]}...")
                return {"status": "ok", "video_url": url, "task_uuid": task_uuid}

            if result_info.get("dataUrls"):
                url = result_info["dataUrls"][0]
                print(f"  ✓ 找到视频URL (data-attr): {url[:100]}...")
                return {"status": "ok", "video_url": url, "task_uuid": task_uuid}

            # ③ HTML 正则匹配 OSS URL（保留，放宽到任意 .mp4）
            oss_url = await page.evaluate("""() => {
                const html = document.body.innerHTML;
                const matches = html.match(/https?:\\/\\/[^"'\\s<>]+\\.mp4[^"'\\s<>]*/g);
                return (matches || [])[0] || '';
            }""")
            if oss_url and "example" not in oss_url:
                print(f"  ✓ 找到视频URL (html): {oss_url[:100]}...")
                return {"status": "ok", "video_url": oss_url, "task_uuid": task_uuid}

            # ④ sessionStorage / localStorage 里可能存了任务结果 JSON
            if not result_info.get("videoSrc") and not result_info.get("downloadLinks"):
                store_url = await page.evaluate("""() => {
                    const sources = [];
                    for (let i = 0; i < sessionStorage.length; i++) {
                        sources.push(sessionStorage.getItem(sessionStorage.key(i)));
                    }
                    for (let i = 0; i < localStorage.length; i++) {
                        sources.push(localStorage.getItem(localStorage.key(i)));
                    }
                    const blob = sources.join('\\n');
                    const m = blob.match(/https?:\\/\\/[^"'\\s<>]+\\.mp4[^"'\\s<>]*/);
                    return m ? m[0] : '';
                }""")
                if store_url and "example" not in store_url:
                    print(f"  ✓ 找到视频URL (storage): {store_url[:100]}...")
                    return {"status": "ok", "video_url": store_url, "task_uuid": task_uuid}

        except Exception as e:
            if "page has been closed" in str(e).lower() or "context" in str(e).lower():
                print(f"  ⚠ 页面已关闭: {e}", flush=True)
                return {"status": "error", "video_url": None, "task_uuid": task_uuid}

        elapsed = int(time.time() - start)
        if elapsed % 10 == 0 and elapsed > 0:
            status = result_info.get("statusText", "")[:60] if 'result_info' in dir() else ""
            print(f"  → 生成中... ({elapsed}s) status={status}", flush=True)

        await page.wait_for_timeout(5000)

    print(f"  ⚠ 等待超时（{timeout}s）", flush=True)

    # 超时截图
    try:
        screenshot_path = Path("outputs") / "step5_timeout.png"
        await page.screenshot(path=str(screenshot_path), full_page=False)
        print(f"  → 超时截图: {screenshot_path}", flush=True)
    except Exception:
        pass

    return {"status": "timeout", "video_url": None, "task_uuid": task_uuid}


async def _async_run(
    video_path: str,
    product_image_path: Optional[str] = None,
    model_image_path: Optional[str] = None,
    scene_image_path: Optional[str] = None,
    storyboard_image_path: Optional[str] = None,
    replaced_storyboard_image_path: Optional[str] = None,
    original_frame_image_path: Optional[str] = None,
    product_name: str = "爆款产品",
    output_dir: str = ".",
    headless: bool = False,
    wait_for_result: bool = True,
    timeout: int = 600,
    aspect_ratio: str = "9:16",
    custom_prompt: Optional[str] = None,
    extra_images: Optional[list] = None,
    duration: str = "15s",
    resolution: str = "720p",
    storyboard_json: list = None,
    product_image_paths: Optional[list] = None,
    model_value: str = MODEL_NO_FACE_LOCK,
):
    """异步执行视频生成"""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    playwright, context = await _launch_context(headless=headless)
    page = context.pages[0] if context.pages else await context.new_page()

    # 挂载网络监听，捕获视频 OSS URL（blob: 无法直接下载，需从网络请求拿真实地址）
    captured_video_urls = _setup_video_capture(page)

    try:
        # ====== 1. 导航到视频生成页面（含自动登录） ======
        await _navigate_and_login(page)

        debug_shot = out_dir / "step5_01_page_loaded.png"
        await page.screenshot(path=str(debug_shot), full_page=False)
        print(f"  → 页面加载截图: {debug_shot}", flush=True)

        # ====== 2. 等待页面元素渲染 ======
        await _wait_for_page_ready(page)

        # ====== 3. 按固定角色顺序整理参考图 ======
        # 固定顺序：@图片1 故事板 → @图片2~N 产品图 → @图片N+1 原视频分镜图shot1
        image_candidates = [("storyboard", "故事板", storyboard_image_path)]

        # 添加产品图（2 到 N 位置）：先主产品图，再多张产品图
        product_paths = []
        if product_image_path:
            product_paths.append(("product_main", "主产品图", product_image_path))
        if product_image_paths:
            for i, path in enumerate(product_image_paths, 1):
                # 去重：避免与主产品图重复
                if path and path != product_image_path:
                    product_paths.append((f"product_{i}", f"产品图{i}", path))
        image_candidates.extend(product_paths)

        # 添加原视频分镜图shot1（N+1 位置）
        if original_frame_image_path:
            image_candidates.append(("original_frame", "原视频分镜图shot1", original_frame_image_path))

        # 额外图片（兜底，追加在原视频分镜图之后）
        if extra_images:
            for i, img_path in enumerate(extra_images):
                if img_path and Path(img_path).exists():
                    image_candidates.append((f"extra_{i+1}", f"额外图片{i+1}", img_path))

        ref_images = []
        image_refs = {}
        actual_product_count = 0
        has_original_frame = False
        for role, label, image_path in image_candidates:
            if image_path and Path(image_path).exists():
                ref_images.append(image_path)
                image_refs[role] = f"@图片{len(ref_images)}"
                if role.startswith("product"):
                    actual_product_count += 1
                if role == "original_frame":
                    has_original_frame = True
                print(f"  → {image_refs[role]} {label}: {image_path}", flush=True)

        required_roles = ("storyboard",)
        missing_roles = [role for role in required_roles if role not in image_refs]
        if missing_roles and not custom_prompt:
            print(f"  ⚠ 缺少参考图: {', '.join(missing_roles)}，降级使用已有图片")
            if "storyboard" not in image_refs:
                raise ValueError(f"Step5 至少需要故事板，当前缺失: {', '.join(missing_roles)}")

        # ====== 4. 加载/生成提示词（按实际图片数量动态生成 @图片N 引用） ======
        if custom_prompt:
            # 调用方提供的完整提示词，跳过动态生成
            full_prompt = custom_prompt
            print(f"  ✓ 使用自定义提示词（{len(full_prompt)}字）", flush=True)
        elif _CUSTOM_PROMPT_OVERRIDE:
            # prompts.json 提供的完整提示词，跳过动态生成
            full_prompt = _CUSTOM_PROMPT_OVERRIDE
            print(f"  ✓ 使用 prompts.json 提示词（{len(full_prompt)}字）", flush=True)
        else:
            # 动态生成：根据实际产品图数量和是否有原视频分镜图，生成 @图片N 引用
            full_prompt = _build_prompt(actual_product_count, has_original_frame)
            print(f"  ✓ 动态生成提示词（产品图{actual_product_count}张，"
                  f"原视频分镜图shot1={'有' if has_original_frame else '无'}，"
                  f"{len(full_prompt)}字）", flush=True)

        # ====== 5. 填写提示词并上传参考图 ======
        await _fill_prompt(page, full_prompt)
        await _upload_reference_images(page, ref_images)

        # ====== 6. 选择模型 ======
        await _select_model(page, model_value)

        # ====== 7. 设置参数 ======
        await _set_parameters(page, aspect_ratio=aspect_ratio, duration=duration, resolution=resolution)

        # ====== 8. 提交前截图 ======
        screenshot_path = out_dir / "step5_02_before_generate.png"
        await page.screenshot(path=str(screenshot_path), full_page=False)
        print(f"  → 提交前截图: {screenshot_path}", flush=True)

        # ====== 9. 点击"开始生成" ======
        n_old = len(captured_video_urls)
        captured_video_urls.clear()
        print(f"  → 已丢弃页面加载时捕获的 {n_old} 条历史视频 URL，只收本次生成结果", flush=True)
        await _click_generate(page)

        # ====== 10. 等待结果 ======
        result = {
            "status": "submitted",
            "page_url": page.url,
            "screenshot": str(screenshot_path),
            "prompt": full_prompt,
        }

        if wait_for_result:
            video_result = await _wait_for_video_result(page, timeout, captured_video_urls=captured_video_urls)
            result.update(video_result)

            # 立即下载视频到本地（OSS签名URL会过期）
            video_url = result.get("video_url")
            if video_url and video_url.startswith("http"):
                local_video = out_dir / "output_video.mp4"
                try:
                    print(f"  → 下载视频到本地...", flush=True)
                    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
                        resp = await client.get(video_url)
                        resp.raise_for_status()
                        local_video.write_bytes(resp.content)
                    result["local_video"] = str(local_video)
                    print(f"  ✓ 视频已保存: {local_video}", flush=True)
                except Exception as e:
                    print(f"  ⚠ 视频下载失败: {e}", flush=True)
                    result["local_video"] = None
        else:
            result["status"] = "submitted_no_wait"

        # 最终截图
        final_screenshot = out_dir / "step5_03_final.png"
        await page.screenshot(path=str(final_screenshot), full_page=False)
        result["final_screenshot"] = str(final_screenshot)

        # 保存结果
        result_path = out_dir / "step5_result.json"
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        print(f"\n  → Step5 完成: status={result['status']}", flush=True)
        return result

    finally:
        await context.close()
        await playwright.stop()


def run(
    video_path: str,
    product_image_path: Optional[str] = None,
    model_image_path: Optional[str] = None,
    scene_image_path: Optional[str] = None,
    storyboard_image_path: Optional[str] = None,
    replaced_storyboard_image_path: Optional[str] = None,
    original_frame_image_path: Optional[str] = None,
    product_name: str = "爆款产品",
    output_dir: str = ".",
    headless: bool = False,
    wait_for_result: bool = True,
    timeout: int = 600,
    aspect_ratio: str = "9:16",
    custom_prompt: Optional[str] = None,
    extra_images: Optional[list] = None,
    duration: str = "15s",
    resolution: str = "720p",
    storyboard_json: list = None,
    product_image_paths: Optional[list] = None,
    model_value: str = MODEL_NO_FACE_LOCK,
) -> dict:
    """Step5: 视频生成入口

    Args:
        video_path: 原视频文件路径（保留参数，/video页面不需要）
        product_image_path: 主产品图路径（兼容单图模式）
        model_image_path: 模特参考图路径（保留参数，不再上传）
        scene_image_path: 场景参考图路径（保留参数，不再上传）
        storyboard_image_path: 故事板参考图路径（@图片1，分镜画面权威，必传）
        replaced_storyboard_image_path: 保留参数，不再上传
        original_frame_image_path: 原视频分镜图第1张路径（@图片N+1，仅参考光影效果和人物皮肤质感）
        product_image_paths: 多张产品图路径列表（@图片2 ~ @图片N，产品一致性参考）
        product_name: 产品名称
        output_dir: 输出目录
        headless: 是否无头模式
        wait_for_result: 是否等待视频生成结果
        timeout: 等待超时（秒）
        aspect_ratio: 画面比例，如 "3:4" 或 "9:16"
        custom_prompt: 自定义完整提示词（跳过角色替换逻辑）
        extra_images: 额外参考图路径列表
        storyboard_json: 分镜脚本列表，拼接到提示词末尾（文字配合图片，避免图片文字看不清）
        duration: 视频时长，如 "15s" 或 "30s"
        resolution: 视频分辨率，如 "720p" 或 "480p"
        model_value: weeksir 模型值，如 MODEL_NO_FACE_LOCK 或 MODEL_SEEDANCE_2_5
    """
    print("=" * 60, flush=True)
    print("Step5: 视频生成（weeksir.com/video）", flush=True)
    print("=" * 60, flush=True)
    if storyboard_image_path:
        print(f"  → 故事板: {storyboard_image_path}", flush=True)
    if product_image_path:
        print(f"  → 产品图: {product_image_path}", flush=True)
    if replaced_storyboard_image_path:
        print(f"  → 替换分镜图: {replaced_storyboard_image_path}", flush=True)
    if original_frame_image_path:
        print(f"  → 原视频分镜图(参考光影和肤质): {original_frame_image_path}")
    if model_image_path:
        print(f"  → 模特图: {model_image_path}", flush=True)
    if scene_image_path:
        print(f"  → 场景图: {scene_image_path}", flush=True)
    if product_image_paths:
        print(f"  → 多张产品图: {len(product_image_paths)} 张")
    print(f"  → 模型: {model_value} | 比例: {aspect_ratio} | 时长: {duration} | 规格: {resolution}", flush=True)

    return _run_async(_async_run(
        video_path=video_path,
        product_image_path=product_image_path,
        model_image_path=model_image_path,
        scene_image_path=scene_image_path,
        storyboard_image_path=storyboard_image_path,
        replaced_storyboard_image_path=replaced_storyboard_image_path,
        original_frame_image_path=original_frame_image_path,
        product_name=product_name,
        output_dir=output_dir,
        headless=headless,
        wait_for_result=wait_for_result,
        timeout=timeout,
        aspect_ratio=aspect_ratio,
        custom_prompt=custom_prompt,
        extra_images=extra_images,
        duration=duration,
        resolution=resolution,
        storyboard_json=storyboard_json,
        product_image_paths=product_image_paths,
        model_value=model_value,
    ))
