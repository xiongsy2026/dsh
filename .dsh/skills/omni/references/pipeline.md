# Omni 管线（基于 D:\video-skill 现成 Python）

源码：`D:\video-skill\backend\services\omni_generate.py`（按 AGENTS.md 约定，不打开 8090）  
API：`https://zexapi.com/v1` 的 `omni_flash-10s`

## 步骤

| 步 | 名称 | 做什么 |
| --- | --- | --- |
| 0 | 预处理 | 产品图存在、产品名非空、customer_request 非空、`target_duration` ∈ {5, 8, 10} |
| 1 | Prompt 编译 | Agent 把产品图描述 + 场景 + 运镜 + 调性 + 时长提示编成英文 prompt |
| 2 | Omni 调用 | HTTP POST `/v1/videos`，bearer token，model=`omni_flash-10s`，prompt，duration，aspect_ratio |
| 3 | 轮询 | 任务 ID 轮询 `/v1/videos/{id}` 直到 status=completed，下载 mp4 |
| 4 | 验收 | 看首帧 + 整片，对照 BRIEF 核对 |
| 后 | 多段 | 客户要更长：拆段独立生成，再 ffmpeg concat（不调 omni 多段 API） |

## 调用约定（P2）

工作区可以用 shell。直接调 Python，不打开 8090，不换对话模型（Agnes 3.0 Flash）。

```python
from backend.services.omni_generate import run_omni

result = run_omni(
    prompt="...",
    model="omni_flash-10s",
    duration=10,
    aspect_ratio="16:9",
    output_dir="productions/<task>/outputs/",
)
```

返回：`{"success": True, "video_path": "...", "task_id": "..."}`

## HTTP 调用形状（仅参考，Agent 不要直接打 HTTP）

```http
POST https://zexapi.com/v1/videos
Authorization: Bearer <ZEXAPI_API_KEY>
Content-Type: application/json

{
  "model": "omni_flash-10s",
  "prompt": "In a single continuous 10-second shot, ...",
  "duration": 10,
  "aspect_ratio": "16:9"
}
```

响应：`{"id": "task-xxx", "status": "pending"}`

轮询：`GET https://zexapi.com/v1/videos/task-xxx`  
完成时：`{"status": "completed", "video_url": "https://..."}`  
下载到 `output_dir` 后返回路径。

## 多段拼接

客户要 20s / 30s：

1. 拆成 N 段 10s prompt（每段独立镜头，不复刻上一段）
2. 各段独立调 `run_omni`
3. 用 ffmpeg concat：
   ```
   ffmpeg -f concat -safe 0 -i list.txt -c copy out.mp4
   ```
4. 不调 omni 多段 API（不支持）

## 失败重跑

- 状态 `failed`：改 prompt 重跑（不调参）
- 状态超时（>5 分钟）：换 ZEXAPI_API_KEY 重试一次，仍失败升级到客户
- 内容不符（产品图错、调性错）：改 prompt 重跑，不重试同 prompt
