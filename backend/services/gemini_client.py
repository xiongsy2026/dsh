"""
Gemini 3 RPA 客户端。

主流程复用 run_gemini_workflow.py 中更稳定的浏览器自动化实现。
"""
from run_gemini_workflow import analyze_video as run_gemini_analyze_video


class GeminiClient:
    """Gemini 3 RPA 客户端"""

    async def analyze_video(self, video_path: str, product_info: str = "") -> dict:
        return await run_gemini_analyze_video(video_path, product_info, headless=False)


# 全局实例
gemini_client = GeminiClient()
