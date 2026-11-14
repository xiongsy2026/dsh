"""
GPT-5.5 API 客户端
"""
import httpx
import asyncio
import json
from typing import Optional, Dict, Any
from config import settings


class GPT55Client:
    """GPT-5.5 API 客户端"""
    
    def __init__(self, base_url: str = "", api_key: str = ""):
        # 允许覆盖 base/key（如 kb 蒸馏切到 qn-einnp/qwen）；不传则用 GPT55 默认
        self.base_url = base_url or settings.GPT55_BASE_URL
        self.api_key = api_key or settings.GPT55_API_KEY
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
    
    async def chat_completion(
        self,
        messages: list,
        model: str = "gpt-5.6-sol",
        temperature: float = 0.7,
        max_tokens: int = 4000,
        max_attempts: int = 5,
        fallback_models: tuple = ("gpt-6.1-sol",),
    ) -> Dict[str, Any]:
        """聊天完成（带 429/502/504/连接错误自动重试 + 备用模型 fallback）

        - 主模型重试 max_attempts 次
        - 全部失败后按 fallback_models 顺序尝试，每个模型也重试
        - 4xx 错误不重试直接抛出（429 限流除外：退避重试，限流窗口会过去）
        """
        models_to_try = [model] + list(fallback_models)
        last_err = None
        for model_idx, current_model in enumerate(models_to_try):
            attempts = max_attempts if model_idx == 0 else max(2, max_attempts // 2)
            for attempt in range(1, attempts + 1):
                try:
                    async with httpx.AsyncClient(timeout=300.0) as client:
                        payload = {
                            "model": current_model,
                            "messages": messages,
                            "temperature": temperature,
                            "max_tokens": max_tokens
                        }
                        response = await client.post(
                            f"{self.base_url}/chat/completions",
                            headers=self.headers,
                            json=payload
                        )
                        response.raise_for_status()
                        return response.json()
                except (httpx.HTTPStatusError, httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
                    last_err = e
                    status = getattr(getattr(e, "response", None), "status_code", None)
                    # 4xx 不重试
                    if status and 400 <= status < 500 and status not in (429,):
                        raise
                    # 429 若是额度耗尽/认证类，重试无用 → 立即抛，让上层 fallback 降档
                    if status == 429:
                        try:
                            _body = (e.response.text or "")
                        except Exception:
                            _body = ""
                        if any(k in _body for k in ("QUOTA_EXHAUSTED", "INVALID_API_KEY", "insufficient",
                                                     "quota", "额度", "credit", "disabled")):
                            raise
                    if status in (429, 502, 504, 524) or isinstance(e, (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError)):
                        if attempt < attempts:
                            # 429 限流退避更久：限流窗口通常比 5xx 抖动长，退避太短会连撞
                            wait_s = (15 * attempt) if status == 429 else 5 * attempt
                            print(f"  [GPT] {current_model} 第{attempt}/{attempts}次失败({status or type(e).__name__})，{wait_s}s后重试", flush=True)
                            await asyncio.sleep(wait_s)
                            continue
                        # 当前模型全部失败，切到下一个备用模型
                        if model_idx < len(models_to_try) - 1:
                            print(f"  [GPT] {current_model} 全部重试失败，切换备用模型 {models_to_try[model_idx + 1]}", flush=True)
                        break
                    raise
        raise last_err

    async def chat_completion_stream(
        self,
        messages: list,
        model: str = "gpt-5.6-sol",
        temperature: float = 0.7,
        max_tokens: int = 8000,
        max_attempts: int = 3,
    ) -> Dict[str, Any]:
        """流式聊天完成（SSE），避免 Cloudflare 524 网关超时。

        持续接收 SSE 数据流，网关不会判定 100s 超时。
        返回格式与 chat_completion 一致：{"choices": [{"message": {"content": "..."}}]}
        """
        for attempt in range(1, max_attempts + 1):
            try:
                payload = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": True,
                }
                collected = []
                async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0)) as client:
                    async with client.stream(
                        "POST",
                        f"{self.base_url}/chat/completions",
                        headers=self.headers,
                        json=payload,
                    ) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                                delta = chunk.get("choices", [{}])[0].get("delta", {})
                                text = delta.get("content", "")
                                if text:
                                    collected.append(text)
                            except (json.JSONDecodeError, IndexError, KeyError):
                                continue
                content = "".join(collected)
                if content:
                    return {"choices": [{"message": {"content": content}}]}
                _log_msg = f"  [GPT-stream] {model} 第{attempt}/{max_attempts}次返回空内容"
                print(_log_msg, flush=True)
            except (httpx.HTTPStatusError, httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status and 400 <= status < 500 and status != 429:
                    raise
                if attempt < max_attempts:
                    wait_s = 5 * attempt
                    print(f"  [GPT-stream] {model} 第{attempt}/{max_attempts}次失败({status or type(e).__name__})，{wait_s}s后重试", flush=True)
                    await asyncio.sleep(wait_s)
                    continue
                raise
        raise RuntimeError(f"GPT stream 调用失败（{max_attempts}次重试后仍无输出）")

    async def extract_product_info(self, image_base64: str, product_type: str) -> str:
        """提取产品信息"""
        messages = [
            {
                "role": "system",
                "content": "你是一个专业的产品分析师，擅长从图片中提取产品细节信息。"
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"请分析这张{product_type}图片，提取以下信息：\n1. 产品外观特征（颜色、形状、材质）\n2. 产品细节（纹理、图案、装饰）\n3. 产品状态（新旧程度、使用痕迹）\n4. 适合的拍摄角度和光线\n\n请用JSON格式返回。"
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_base64}"
                        }
                    }
                ]
            }
        ]
        
        result = await self.chat_completion(messages)
        return result["choices"][0]["message"]["content"]
    
    async def parse_video_script(self, video_description: str) -> Dict[str, Any]:
        """解析视频脚本，生成分镜"""
        messages = [
            {
                "role": "system",
                "content": """你是工业级视频结构解析引擎，负责视频还原和分镜脚本生成。

输出格式要求（JSON）：
{
  "shots": [
    {
      "shot": 1,
      "start_time": 0.0,
      "end_time": 2.5,
      "scene": "场景描述",
      "character": "人物描述",
      "product": "产品描述",
      "action": "动作描述",
      "camera": "镜头运动",
      "dialogue": "对白内容",
      "emotion": "情绪表达"
    }
  ]
}"""
            },
            {
                "role": "user",
                "content": f"请分析以下视频描述，生成分镜脚本：\n\n{video_description}"
            }
        ]
        
        result = await self.chat_completion(messages)
        content = result["choices"][0]["message"]["content"]
        
        # 尝试解析 JSON
        import json
        try:
            return json.loads(content)
        except:
            # 如果不是纯 JSON，尝试提取
            import re
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                return json.loads(json_match.group())
            return {"raw_response": content}


# 全局实例
gpt_client = GPT55Client()
