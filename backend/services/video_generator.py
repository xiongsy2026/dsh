"""
视频生成服务 - 使用 Grok 生成最终视频
"""
import os
import json
import httpx
from pathlib import Path
from typing import List, Dict, Any
from config import settings


class VideoGenerator:
    """视频生成器 - 使用 Grok API"""
    
    def __init__(self):
        self.base_url = settings.GROK_BASE_URL
        self.api_key = settings.GROK_API_KEY
        self.output_dir = Path(settings.OUTPUT_DIR)
    
    async def generate_video(
        self,
        storyboard_path: str,
        script_json: Dict[str, Any],
        output_filename: str = "final_video.mp4"
    ) -> str:
        """
        根据故事板和分镜脚本生成视频
        
        Args:
            storyboard_path: 故事板图片路径
            script_json: 分镜脚本 JSON
            output_filename: 输出文件名
            
        Returns:
            生成的视频文件路径
        """
        # 构建提示词
        prompt = self._build_video_prompt(script_json)
        
        # 读取故事板图片
        with open(storyboard_path, "rb") as f:
            storyboard_base64 = f.read().hex()
        
        # 调用 Grok API
        async with httpx.AsyncClient(timeout=300.0) as client:
            payload = {
                "model": "grok-2-image",
                "prompt": prompt,
                "image": storyboard_base64,
                "duration": 15,  # 15秒视频
                "quality": "high",
                "resolution": "1080p"
            }
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            response = await client.post(
                f"{self.base_url}/v1/videos/generations",
                headers=headers,
                json=payload
            )
            response.raise_for_status()
            
            result = response.json()
            
            # 保存视频
            output_path = self.output_dir / output_filename
            video_data = result.get("video_data")
            
            if video_data:
                with open(output_path, "wb") as f:
                    f.write(bytes.fromhex(video_data))
            
            return str(output_path)
    
    def _build_video_prompt(self, script_json: Dict[str, Any]) -> str:
        """
        构建视频生成提示词
        
        Args:
            script_json: 分镜脚本 JSON
            
        Returns:
            提示词字符串
        """
        shots = script_json.get("shots", [])
        
        prompt_parts = [
            "请严格按照故事板的分镜画面生成视频，保持产品一致性和模特一致性。",
            "不要出现字幕！严格按照分镜脚本的口播内容进行配音。",
            "",
            "画质要求：",
            "- 人物高清原生毛孔，皮下血管纹理",
            "- 自然鱼尾细纹，面部微汗光泽",
            "- 原生通透皮肤，关闭过度磨皮",
            "- 100%细节强度，完整保留发丝、皮肤与面料肌理",
            "- 柔和侧光塑造面部立体骨骼",
            "- Canon EF85mm f/1.2镜头，胶片摄影质感",
            "- 超写实真人肤质，无假面、无塑料皮肤",
            "- 边缘清晰无锯齿模糊，超清4K极致细节",
            "",
            "分镜脚本："
        ]
        
        for shot in shots:
            shot_desc = [
                f"Shot {shot.get('shot', 0)}:",
                f"  场景: {shot.get('scene', '')}",
                f"  人物: {shot.get('character', '')}",
                f"  产品: {shot.get('product', '')}",
                f"  动作: {shot.get('action', '')}",
                f"  镜头: {shot.get('camera', '')}",
                f"  口播: {shot.get('dialogue', '无')}",
                f"  情绪: {shot.get('emotion', '')}",
                ""
            ]
            prompt_parts.extend(shot_desc)
        
        return "\n".join(prompt_parts)
    
    async def generate_video_from_frames(
        self,
        frame_paths: List[str],
        script_json: Dict[str, Any],
        output_filename: str = "video_from_frames.mp4"
    ) -> str:
        """
        从分镜帧图片生成视频（备选方案）
        
        Args:
            frame_paths: 帧图片路径列表
            script_json: 分镜脚本 JSON
            output_filename: 输出文件名
            
        Returns:
            生成的视频文件路径
        """
        # 这里可以实现一个备选方案：
        # 1. 使用帧图片作为关键帧
        # 2. 使用 AI 生成中间帧
        # 3. 合成最终视频
        
        # 暂时返回占位实现
        raise NotImplementedError("从帧生成视频的功能待实现")


# 全局实例
video_generator = VideoGenerator()
