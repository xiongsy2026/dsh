"""RunningHub RPA 客户端：通过 Playwright 控制 ComfyUI 操作工作流

核心流程：
1. 打开工作流页面（已登录的 profile）
2. 通过 ComfyUI graph API 上传图片到 299 节点、视频到 275 节点
3. 点击主页面的"运行"按钮
4. 轮询 285 节点的 videopreview.params.cos_url（save_output=True 的完整视频节点；502 是 PreviewOnly 1 秒预览，不可用）
5. 下载视频到本地

节点链路设计（动作迁移weeksir_带坏节点.json）：
- 节点 285：VHS_VideoCombine，filename_prefix='h264 yuv420p'，save_output=True —— 真正的完整视频输出节点
- 节点 502：VHS_VideoCombine，filename_prefix='PreviewOnly'，save_output=False —— 1 秒/30 帧预览，仅供 ComfyUI 画布预览，不可下载
- 节点 600：FailedTrigger —— 在 285 生成完整视频后主动触发任务标 FAILED，绕过 RunningHub 服务端的视频时长/质量校验
- 因此任务状态最终为 FAILED 是设计内的预期行为，调用方必须 try 住 RuntimeError 后继续从 285 抓取视频
- 这正是 RPA 链路存在的意义：API 链路（/openapi/v2）遇 FAILED 直接报错拿不到视频，RPA 链路可以从 ComfyUI 画布的 videopreview widget 直接读 cos_url 抓取完整视频
"""
import asyncio
import time
from pathlib import Path
from typing import Optional
import httpx
from playwright.async_api import async_playwright, BrowserContext, Page, Frame

from config import settings


WORKFLOW_URL = "https://www.runninghub.cn/workflow/2087348783733628930?source=workspace"
# profile 目录与之前的调试脚本保持一致
_PROFILE_DIR = str(Path(__file__).resolve().parent.parent.parent / "runninghub_profile")
PROFILE_DIR = getattr(settings, "RUNNINGHUB_PROFILE_DIR", _PROFILE_DIR)


class RunningHubRPA:
    """RunningHub 网页 RPA 客户端"""

    def __init__(self, headless: bool = False):
        self.headless = headless
        self._playwright = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._comfy_frame: Optional[Frame] = None

    async def connect(self):
        """启动浏览器并打开工作流页面"""
        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=self.headless,
            viewport={"width": 1440, "height": 1000},
            locale="zh-CN",
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()

    async def close(self):
        """关闭浏览器"""
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()

    async def open_workflow(self, workflow_url: str = WORKFLOW_URL):
        """打开工作流页面并等待加载"""
        print(f"[RPA] 打开工作流: {workflow_url}")
        # RunningHub 页面有持续 polling，networkidle 经常等不到 → try/except 兜底
        # 不用 domcontentloaded - 太早，iframe 还没注入
        # 不用 load 单独 - onload 时 iframe 也可能还没挂载
        # 策略：先 try networkidle 30s（少数情况能等到，iframe 已就绪）；超时就 fallback 到 domcontentloaded + 等待循环
        try:
            await self._page.goto(workflow_url, wait_until="networkidle", timeout=30000)
        except Exception as _e:
            print(f"  [warn] networkidle 30s 超时，回退到 domcontentloaded: {str(_e)[:80]}")
            try:
                await self._page.goto(workflow_url, wait_until="domcontentloaded", timeout=30000)
            except Exception as _e2:
                print(f"  [warn] domcontentloaded 也超时: {str(_e2)[:80]}")
        await self._page.wait_for_timeout(5000)

        # 检查登录
        login_btn = self._page.locator("button:has-text('登 录')").first
        try:
            if await login_btn.is_visible(timeout=2000):
                raise RuntimeError(
                    "RunningHub 未登录！请先运行 python rh_auto_login3.py 完成登录（保存到 profile）"
                )
        except RuntimeError:
            raise
        except Exception:
            pass  # 按钮不可见 = 已登录

        # 等 ComfyUI iframe
        print("[RPA] 等待 ComfyUI iframe...")
        last_dbg = -10
        for i in range(30):
            await self._page.wait_for_timeout(2000)
            for f in self._page.frames:
                if "comfyUI" in f.url or "comfyui" in f.url.lower():
                    self._comfy_frame = f
                    break
            if self._comfy_frame:
                print(f"  [{(i+1)*2}s] iframe 就绪")
                break
            # 每 10s 打印一次 frame 列表，便于诊断
            if (i+1)*2 - last_dbg >= 10:
                frame_urls = [f.url[:80] for f in self._page.frames if f.url and f.url != 'about:blank']
                print(f"  [{(i+1)*2}s] 暂无 ComfyUI iframe，当前 {len(frame_urls)} 个 frame: {frame_urls}")
                last_dbg = (i+1)*2

        if not self._comfy_frame:
            frame_urls = [f.url[:80] for f in self._page.frames if f.url and f.url != 'about:blank']
            raise RuntimeError(f"未找到 ComfyUI iframe (当前 {len(frame_urls)} 个 frame: {frame_urls})")

        # 等 graph 加载
        print("[RPA] 等待 graph 加载...")
        for i in range(60):
            await self._page.wait_for_timeout(2000)
            count = await self._comfy_frame.evaluate(
                "() => (typeof app !== 'undefined' && app.graph) ? app.graph._nodes.length : -1"
            )
            if count > 0:
                print(f"  [{(i+1)*2}s] graph 加载完成（{count} 个节点）")
                return
        raise RuntimeError("工作流 graph 加载超时")

    async def upload_to_node(self, node_id: int, file_path: str, widget_name: str) -> str:
        """上传文件到 ComfyUI 节点

        通过 ComfyUI 的 fetchApi 直接调用 /api/upload/image 端点上传文件，
        然后设置节点的 widget 值。

        Args:
            node_id: 节点 ID
            file_path: 本地文件路径
            widget_name: 要设置的 widget 名（如 'image' 或 'video'）

        Returns:
            上传后的文件名
        """
        abs_path = str(Path(file_path).resolve())
        filename_only = Path(abs_path).name
        print(f"[RPA] 上传到节点 {node_id}: {abs_path}")

        # 读取文件转 base64，在 iframe 里用 fetchApi 上传
        import base64
        file_bytes = Path(abs_path).read_bytes()
        file_b64 = base64.b64encode(file_bytes).decode('ascii')
        # 根据扩展名判断 mime type
        ext = Path(abs_path).suffix.lower().lstrip('.')
        mime = {
            'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
            'webp': 'image/webp', 'mp4': 'video/mp4', 'webm': 'video/webm',
            'mov': 'video/quicktime',
        }.get(ext, 'application/octet-stream')

        # 在 ComfyUI iframe 内用 api.apiURL + fetchApi 上传
        # api.apiURL('/upload/image') 会自动拼接 Rh-Comfy-Auth 和 Rh-Identify token
        upload_result = await self._comfy_frame.evaluate(
            """async ([b64, filename, mime]) => {
                // base64 → blob
                const byteChars = atob(b64);
                const byteNums = new Array(byteChars.length);
                for (let i = 0; i < byteChars.length; i++) {
                    byteNums[i] = byteChars.charCodeAt(i);
                }
                const byteArray = new Uint8Array(byteNums);
                const blob = new Blob([byteArray], {type: mime});
                const formData = new FormData();
                formData.append('image', blob, filename);
                formData.append('subfolder', '');
                formData.append('type', 'input');

                // 用 api.apiURL 构造带 token 的 URL
                const uploadUrl = api.apiURL('/upload/image');
                const resp = await fetch(uploadUrl, {
                    method: 'POST',
                    body: formData,
                });
                const text = await resp.text();
                let data;
                try { data = JSON.parse(text); } catch(e) { data = {raw: text}; }
                return {status: resp.status, data: data};
            }""",
            [file_b64, filename_only, mime],
        )

        if upload_result.get('status') != 200:
            raise RuntimeError(f"上传失败 status={upload_result.get('status')}: {upload_result.get('data')}")

        uploaded = upload_result['data']
        # 打印完整响应，便于排查 RunningHub 服务端问题
        print(f"  [debug] 上传响应 status={upload_result.get('status')} data={uploaded}")

        # RunningHub 会重命名文件为哈希值，响应字段是 "name"
        filename = uploaded.get('name') or uploaded.get('filename')
        if not filename:
            raise RuntimeError(f"上传响应无 filename: {uploaded}")

        # 校验返回的 name 是否和本地 hash 一致（RunningHub 用文件内容 sha256 作文件名）
        local_hash = __import__('hashlib').sha256(file_bytes).hexdigest().upper()
        print(f"  [debug] 本地 sha256={local_hash}")
        print(f"  [debug] RunningHub 返回 name={filename}")
        if local_hash in filename.upper():
            print(f"  [ok] hash 一致，文件内容已正确上传")
        else:
            print(f"  [warn] hash 不一致！RunningHub 可能没收到完整文件内容")

        print(f"  上传成功: {filename}")

        # 设置节点的 widget 值
        await self._comfy_frame.evaluate(
            """([nodeId, widgetName, filename]) => {
                const node = app.graph._nodes_by_id[nodeId];
                if (!node) throw new Error('节点不存在: ' + nodeId);
                const widget = node.widgets.find(w => w.name === widgetName);
                if (!widget) throw new Error('widget 不存在: ' + widgetName);
                widget.value = filename;
                if (widget.callback) widget.callback(filename);
            }""",
            [node_id, widget_name, filename],
        )
        print(f"  节点 {node_id} widget '{widget_name}' = {filename}")

        return filename

    async def _list_run_buttons(self) -> list:
        """识别主页面所有运行按钮，返回其索引/文本/class/位置，便于按 engine 选择

        RunningHub 工作流页面通常有 3 个运行按钮（lite / standard / ultra），
        按钮文本可能为 "运行"、"RUN" 或带前缀如 "Lite 运行"、"Standard 运行"、"Ultra 运行"。
        本方法把它们都抓出来，方便调用方按文本/class 精确选择。
        """
        try:
            buttons_info = await self._page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button.run-btn, button[class*="run-btn"], button[class*="beveled-btn"]'));
                return btns.map((b, i) => {
                    const rect = b.getBoundingClientRect();
                    return {
                        index: i,
                        text: (b.innerText || b.textContent || '').trim().substring(0, 80),
                        className: b.className,
                        x: Math.round(rect.x),
                        y: Math.round(rect.y),
                        width: Math.round(rect.width),
                        height: Math.round(rect.height),
                        visible: rect.width > 0 && rect.height > 0,
                    };
                });
            }""")
            return buttons_info or []
        except Exception as e:
            print(f"  [warn] 识别运行按钮失败: {e}")
            return []

    def _select_button_by_engine(self, buttons: list, engine: str) -> int:
        """根据 engine 在已识别按钮列表中选目标索引

        实测 RunningHub 工作流页面有 3 个运行按钮（按 x 坐标从左到右）：
        - 左侧 idx=0：class='beveled-btn-right'，文本='运行\nLite/Standard'
        - 中间 idx=1：class='beveled-btn-left'，文本='运行'  ← plus 默认选这个
        - 右侧 idx=2：class='beveled-btn-ultra'，文本='运行'  ← Ultra 模式

        用户语义：前端 "plus" = 中间按钮（beveled-btn-left）
                  前端 "lite" = 左侧按钮（文本含 Lite/Standard 或最左侧）

        优先级：
        1. plus → class 含 'beveled-btn-left'；lite → 文本含 'lite' 或 class 含 'beveled-btn-right'
        2. 兜底：lite → 最左；plus → 中间一个；其它 → 最右
        """
        engine = (engine or "plus").lower()
        # 仅看可见按钮
        visible = [b for b in buttons if b.get("visible")]
        if not visible:
            return -1

        # 1) plus 优先匹配 beveled-btn-left；lite 优先匹配文本含 'lite' 或 class 含 'beveled-btn-right'
        if engine == "plus":
            for b in visible:
                if "beveled-btn-left" in (b.get("className") or "").lower():
                    return b.get("index", -1)
        elif engine == "lite":
            # 文本含 lite 的（实测左侧按钮文本是 '运行\nLite/Standard'）
            for b in visible:
                if "lite" in (b.get("text") or "").lower():
                    return b.get("index", -1)
            # 或 class 含 beveled-btn-right
            for b in visible:
                if "beveled-btn-right" in (b.get("className") or "").lower():
                    return b.get("index", -1)

        # 2) 通用关键字匹配（兼容未来 engine 值）
        for b in visible:
            cls = (b.get("className") or "").lower()
            if engine in cls:
                return b.get("index", -1)
        for b in visible:
            txt = (b.get("text") or "").lower()
            if engine in txt:
                return b.get("index", -1)

        # 3) 位置兜底（按 x 坐标排序）
        sorted_btns = sorted(visible, key=lambda b: b.get("x", 0))
        if engine == "lite":
            return sorted_btns[0].get("index", -1)
        if engine == "plus":
            # 中间一个（3 个按钮时 idx=1）
            mid = len(sorted_btns) // 2
            return sorted_btns[mid].get("index", -1) if sorted_btns else -1
        # 其它 → 最右
        return sorted_btns[-1].get("index", -1) if sorted_btns else -1

    async def click_run(self, engine: str = "plus"):
        """点击主页面的运行按钮，按 engine 识别并选择对应按钮

        Args:
            engine: plus/lite/standard —— 对应 RunningHub 主页面的 Ultra/Standard/Lite 按钮
        """
        engine = (engine or "plus").lower()
        print(f"[RPA] 点击运行按钮（engine={engine}）...")
        # 记录运行前的旧视频 filename 和任务列表，用于后续判断新视频生成
        self._old_preview_filename = await self._get_preview_filename(285)
        if self._old_preview_filename:
            print(f"  旧视频: {self._old_preview_filename}")

        # 记录运行前的任务 ID 列表，用于识别新任务
        self._old_task_ids = await self._get_running_task_ids()
        if self._old_task_ids:
            print(f"  旧任务数: {len(self._old_task_ids)}")

        # 先识别页面上所有运行按钮
        buttons = await self._list_run_buttons()
        if buttons:
            print(f"  识别到 {len(buttons)} 个运行按钮:")
            for b in buttons:
                vis = "✓" if b.get("visible") else "✗"
                print(f"    [{vis}] idx={b.get('index')} text={b.get('text')!r} class={b.get('className')!r} pos=({b.get('x')},{b.get('y')})")

        target_idx = self._select_button_by_engine(buttons, engine)
        if target_idx < 0:
            # 兜底：用 has-text('运行') 的最后一个
            run_btns = self._page.locator("button.run-btn:has-text('运行')")
            count = await run_btns.count()
            if count == 0:
                raise RuntimeError(f"未找到运行按钮（识别到 {len(buttons)} 个按钮，无匹配 engine={engine}）")
            run_btn = run_btns.nth(count - 1)
            print(f"  [兜底] engine={engine} 未精确匹配，用第 {count} 个运行按钮")
        else:
            # 通过 index 选择对应按钮
            run_btn = self._page.locator("button.run-btn, button[class*='beveled-btn']").nth(target_idx)
            matched = next((b for b in buttons if b.get("index") == target_idx), None)
            if matched:
                print(f"  匹配 engine={engine}: idx={target_idx} text={matched.get('text')!r} class={matched.get('className')!r}")

        await run_btn.scroll_into_view_if_needed()
        await run_btn.click()
        print(f"[RPA] 已点击运行（engine={engine}）")

    async def _get_running_task_ids(self) -> set:
        """获取当前运行中/排队中的任务 ID 集合"""
        try:
            result = await self._page.evaluate("""async () => {
                try {
                    const r = await fetch('/api/user/task/list', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({size: 50, current: 1, taskStatus: ['RUNNING', 'QUEUED'], taskType: ['WORKFLOW', 'WEBAPP']})
                    });
                    const j = await r.json();
                    const records = j?.data?.records || [];
                    return records.map(r => String(r.id));
                } catch(e) { return []; }
            }""")
            return set(result or [])
        except Exception:
            return set()

    async def _get_new_task_status(self, old_ids: set) -> dict:
        """获取新提交的任务状态（不在 old_ids 中的任务）"""
        try:
            result = await self._page.evaluate("""async (oldIds) => {
                try {
                    const r = await fetch('/api/user/task/list', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({size: 50, current: 1, taskStatus: ['RUNNING', 'QUEUED', 'COMPLETED', 'FAILED'], taskType: ['WORKFLOW', 'WEBAPP']})
                    });
                    const j = await r.json();
                    const records = j?.data?.records || [];
                    // 找新任务
                    const newTasks = records.filter(r => !oldIds.includes(String(r.id)));
                    return {
                        total: records.length,
                        newTasks: newTasks.map(r => ({
                            id: r.id,
                            status: r.taskStatus,
                            name: (r.taskName || '').substring(0, 50),
                            error: (r.errorMessage || '').substring(0, 300),
                            outputs: r.taskOutputs ? JSON.stringify(r.taskOutputs).substring(0, 500) : '',
                        })),
                    };
                } catch(e) { return {err: e.message}; }
            }""", list(old_ids))
            return result
        except Exception as e:
            return {'err': str(e)}

    async def _get_preview_filename(self, node_id: int) -> Optional[str]:
        """获取节点当前的预览视频 filename"""
        if not self._comfy_frame:
            return None
        try:
            result = await self._comfy_frame.evaluate(
                """(nodeId) => {
                    const node = app.graph._nodes_by_id[nodeId];
                    if (!node) return null;
                    const widget = node.widgets.find(w => w.name === 'videopreview');
                    if (!widget || !widget.value) return null;
                    const params = widget.value.params || {};
                    return params.filename || '';
                }""",
                node_id,
            )
            return result or None
        except Exception:
            return None

    async def _wait_for_new_task_completion(self, timeout: int) -> dict:
        """等待本次点击运行创建的任务完成，避免抓取中间 PreviewOnly 文件。

        设计说明：当前工作流（动作迁移weeksir_带坏节点）包含节点 600 (FailedTrigger)，
        它会在 285 节点生成完整视频后触发任务标 FAILED。这是设计内的预期失败。
        调用方（run_motion_transfer_rpa）必须用 try/except RuntimeError 包住此方法，
        在 FAILED 时继续调用 wait_for_preview_video(285) 抓取完整视频。
        本方法仅负责「等待任务跑完」这一个职责，不负责判断 285 是否有视频。
        """
        old_ids = getattr(self, "_old_task_ids", set())
        start = time.time()
        last_report = 0
        while time.time() - start < timeout:
            task_info = await self._get_new_task_status(old_ids)
            if task_info.get("err"):
                elapsed = int(time.time() - start)
                if elapsed - last_report >= 30:
                    print(f"  [task-wait {elapsed}s] 查询失败: {task_info.get('err')}")
                    last_report = elapsed
                await self._page.wait_for_timeout(5000)
                continue
            new_tasks = task_info.get("newTasks", [])
            elapsed = int(time.time() - start)
            if new_tasks:
                task = new_tasks[0]
                status = str(task.get("status") or "").upper()
                if elapsed - last_report >= 30 or status in ("COMPLETED", "SUCCESS", "FAILED", "CANCELED", "CANCELLED"):
                    print(f"  [task-wait {elapsed}s] total={task_info.get('total',0)} new={len(new_tasks)} status={status} name={task.get('name','')[:40]} err={task.get('error','')[:80]}")
                    last_report = elapsed
                if status in ("COMPLETED", "SUCCESS", "FAILED", "CANCELED", "CANCELLED"):
                    if status not in ("COMPLETED", "SUCCESS"):
                        raise RuntimeError(f"RunningHub RPA 任务失败: {task.get('error') or status}")
                    return task
            else:
                if elapsed - last_report >= 30:
                    print(f"  [task-wait {elapsed}s] 暂无新任务（total={task_info.get('total',0)}, old_ids={len(old_ids)}）")
                    last_report = elapsed
            await self._page.wait_for_timeout(5000)
        raise TimeoutError(f"等待 RunningHub RPA 任务完成超时（{timeout}s）")

    async def wait_for_preview_video(
        self,
        output_node_id: int,
        timeout: int = 1800,
        poll_interval: int = 5,
    ) -> str:
        """等待预览视频生成并返回视频 URL

        通过对比 click_run 前后的 filename 变化判断新视频生成。

        Args:
            output_node_id: 输出节点 ID（如 502）
            timeout: 超时秒数
            poll_interval: 轮询间隔秒数

        Returns:
            视频 URL（cos_url）
        """
        old_filename = getattr(self, '_old_preview_filename', None) or ''
        #region debug-point rpa-preview-observation
        try:
            async with httpx.AsyncClient(timeout=2) as _dbg_client:
                await _dbg_client.post("http://127.0.0.1:7777/event", json={"sessionId":"rpa-video-one-second","runId":"pre","hypothesisId":"H1-H2","event":"wait_start","data":{"node_id":output_node_id,"old_filename":old_filename,"old_task_ids":list(getattr(self, "_old_task_ids", set()))}})
        except Exception:
            pass
        #endregion
        print(f"[RPA] 等待节点 {output_node_id} 生成新视频（旧: {old_filename or '无'}, 超时 {timeout}s）...")
        start = time.time()
        last_report = 0
        last_queue_check = 0

        while time.time() - start < timeout:
            preview = await self._comfy_frame.evaluate(
                """(nodeId) => {
                    const node = app.graph._nodes_by_id[nodeId];
                    if (!node) return null;
                    const widget = node.widgets.find(w => w.name === 'videopreview');
                    if (!widget || !widget.value) return null;
                    const params = widget.value.params || {};
                    return {
                        filename: params.filename || '',
                        cos_url: params.cos_url || '',
                        type: params.type || '',
                    };
                }""",
                output_node_id,
            )

            if preview and preview.get('cos_url'):
                cur_file = preview.get('filename')
                # 新视频：filename 与旧的不同
                if cur_file and cur_file != old_filename:
                    elapsed = int(time.time() - start)
                    #region debug-point rpa-preview-observation
                    try:
                        async with httpx.AsyncClient(timeout=2) as _dbg_client:
                            await _dbg_client.post("http://127.0.0.1:7777/event", json={"sessionId":"rpa-video-one-second","runId":"pre","hypothesisId":"H1-H2-H4","event":"preview_selected","data":{"filename":cur_file,"cos_url":preview.get("cos_url",""),"type":preview.get("type",""),"elapsed":elapsed}})
                    except Exception:
                        pass
                    #endregion
                    print(f"  [{elapsed}s] 新视频已生成: {cur_file}")
                    print(f"  URL: {preview['cos_url']}")
                    return preview['cos_url']

            # 每 30s 报告一次
            elapsed = int(time.time() - start)
            if elapsed - last_report >= 30:
                print(f"  [{elapsed}s] 等待中...")
                last_report = elapsed

            # 每 60s 检查 ComfyUI 队列状态
            if elapsed - last_queue_check >= 60:
                last_queue_check = elapsed
                try:
                    queue_info = await self._comfy_frame.evaluate(
                        """async () => {
                            try {
                                const r = await fetch(api.apiURL('/queue'));
                                const j = await r.json();
                                return {
                                    queue_running: j.queue_running ? j.queue_running.length : 0,
                                    queue_pending: j.queue_pending ? j.queue_pending.length : 0,
                                };
                            } catch(e) { return {err: e.message}; }
                        }"""
                    )
                    if queue_info and not queue_info.get('err'):
                        print(f"  [{elapsed}s] ComfyUI 队列: running={queue_info.get('queue_running', 0)}, pending={queue_info.get('queue_pending', 0)}")
                    elif queue_info and queue_info.get('err'):
                        print(f"  [{elapsed}s] 队列查询失败: {queue_info.get('err')}")
                except Exception as e:
                    print(f"  [{elapsed}s] 队列查询异常: {e}")

            await self._page.wait_for_timeout(poll_interval * 1000)

        # 超时前截图，便于诊断
        try:
            screenshot_path = f"rpa_timeout_screenshot_{int(time.time())}.png"
            await self._page.screenshot(path=screenshot_path, full_page=True)
            print(f"  超时截图: {screenshot_path}")
        except Exception:
            pass

        raise TimeoutError(f"等待节点 {output_node_id} 生成视频超时（{timeout}s）")

    async def download_video(self, video_url: str, output_path: str):
        """下载视频到本地"""
        print(f"[RPA] 下载: {video_url}")
        print(f"  → {output_path}")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
            resp = await client.get(video_url)
            resp.raise_for_status()
            Path(output_path).write_bytes(resp.content)
        size_kb = len(resp.content) // 1024
        #region debug-point rpa-video-observation
        try:
            async with httpx.AsyncClient(timeout=2) as _dbg_client:
                await _dbg_client.post("http://127.0.0.1:7777/event", json={"sessionId":"rpa-video-one-second","runId":"pre","hypothesisId":"H1-H2","event":"download_complete","data":{"size_bytes":len(resp.content),"output_path":output_path}})
        except Exception:
            pass
        #endregion
        print(f"  完成（{size_kb}KB）")


async def run_motion_transfer_rpa(
    image_path: str,
    video_path: str,
    output_path: str,
    timeout: int = 1800,
    workflow_url: Optional[str] = None,
    engine: str = "plus",
) -> str:
    """通过 RPA 运行动作迁移工作流

    Args:
        image_path: 参考图片本地路径
        video_path: 参考视频本地路径
        output_path: 输出视频本地路径
        timeout: 等待超时秒数
        workflow_url: 工作流 URL（可选，默认用本模块顶部 WORKFLOW_URL）
        engine: 运行引擎 plus/lite/standard，对应 RunningHub 主页面的 Ultra/Lite/Standard 按钮

    Returns:
        输出视频本地路径
    """
    rpa = RunningHubRPA(headless=False)
    try:
        await rpa.connect()
        await rpa.open_workflow(workflow_url or WORKFLOW_URL)
        # 上传图片到 299 节点（widget: image）
        await rpa.upload_to_node(299, image_path, "image")
        # 上传视频到 275 节点（widget: video）
        await rpa.upload_to_node(275, video_path, "video")
        # 点击运行（按 engine 选择对应按钮）
        await rpa.click_run(engine=engine)
        # 等本次 RunningHub 任务完成（避免抓取中间 PreviewOnly 文件）。
        # 注意：该工作流包含节点 600 (FailedTrigger)，任务最终会标 FAILED —— 这是设计内行为。
        # 节点 285 (save_output=True) 在任务报错前已生成完整视频，
        # 因此 FAILED 时不能直接抛错，必须继续从 285 预览抓取视频。
        task_failed_with_err = None
        try:
            await rpa._wait_for_new_task_completion(timeout=timeout)
        except RuntimeError as e:
            # 节点 600 FailedTrigger 触发的预期失败：285 已生成完整视频，继续往下走
            task_failed_with_err = str(e)
            print(f"[RPA] 任务状态 FAILED（预期内，节点 600 FailedTrigger）：{task_failed_with_err}")
            print(f"[RPA] 继续从节点 285 预览抓取完整视频...")

        # 从 285 节点（save_output=True）的 videopreview widget 抓取完整视频。
        # 502 是 PreviewOnly 1 秒预览，不可用；600 是 FailedTrigger 兜底节点，已通过上方 try 处理。
        video_url = await rpa.wait_for_preview_video(285, timeout=timeout)
        # 下载
        await rpa.download_video(video_url, output_path)
        return output_path
    finally:
        try:
            await rpa.close()
        except Exception as close_error:
            print(f"[RPA] 关闭浏览器失败: {close_error!r}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("用法: python runninghub_rpa_client.py <image_path> <video_path> [output_path]")
        print()
        print("示例:")
        print("  python runninghub_rpa_client.py uploads/test.png uploads/test.mp4 outputs/motion.mp4")
        sys.exit(1)

    img = sys.argv[1]
    vid = sys.argv[2]
    out = sys.argv[3] if len(sys.argv) > 3 else "outputs/motion_transfer_rpa.mp4"

    result = asyncio.run(run_motion_transfer_rpa(img, vid, out, timeout=1800))
    print(f"\n✓ 完成: {result}")
