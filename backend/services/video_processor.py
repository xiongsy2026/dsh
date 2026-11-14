"""
视频处理服务 - FFmpeg 抽帧 + PIL 拼图
"""
import os
import json
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from typing import List, Dict, Any
from config import settings


class VideoProcessor:
    """视频处理器"""
    
    def __init__(self):
        self.upload_dir = Path(settings.UPLOAD_DIR)
        self.output_dir = Path(settings.OUTPUT_DIR)
    
    def extract_frames(
        self,
        video_path: str,
        shots: List[Dict[str, Any]],
        output_dir: str = None
    ) -> List[str]:
        """
        根据分镜时间点提取帧
        
        Args:
            video_path: 视频文件路径
            shots: 分镜列表，每个包含 time 字段
            output_dir: 输出目录
            
        Returns:
            提取的帧图片路径列表
        """
        if output_dir is None:
            video_name = Path(video_path).stem
            output_dir = self.output_dir / f"{video_name}_frames"
        else:
            output_dir = Path(output_dir)
        
        output_dir.mkdir(parents=True, exist_ok=True)
        
        frame_paths = []
        
        for shot in shots:
            shot_num = shot.get("shot", 0)
            time_point = shot.get("time", 0)
            
            output_path = output_dir / f"shot_{shot_num:02d}.jpg"
            
            # FFmpeg 命令
            cmd = [
                "ffmpeg",
                "-y",  # 覆盖输出
                "-ss", str(time_point),  # 起始时间
                "-i", video_path,  # 输入视频
                "-vframes", "1",  # 只提取1帧
                "-q:v", "2",  # 质量
                str(output_path)
            ]
            
            try:
                subprocess.run(cmd, check=True, capture_output=True)
                frame_paths.append(str(output_path))
                print(f"✓ 提取 Shot {shot_num} @ {time_point}s")
            except subprocess.CalledProcessError as e:
                print(f"✗ 提取 Shot {shot_num} 失败: {e.stderr.decode()}")
        
        return frame_paths
    
    def create_storyboard(
        self,
        frame_paths: List[str],
        output_path: str = None,
        cols: int = 4
    ) -> str:
        """
        创建分镜拼图
        
        Args:
            frame_paths: 帧图片路径列表
            output_path: 输出路径
            cols: 列数
            
        Returns:
            拼图图片路径
        """
        if not frame_paths:
            raise ValueError("没有帧图片")
        
        # 计算布局
        n = len(frame_paths)
        rows = (n + cols - 1) // cols
        
        # 读取第一张图片获取尺寸
        first_frame = Image.open(frame_paths[0])
        w, h = first_frame.size
        first_frame.close()
        
        # 创建画布
        canvas = Image.new("RGB", (w * cols, h * rows), (255, 255, 255))
        draw = ImageDraw.Draw(canvas)
        
        # 尝试加载字体
        try:
            font = ImageFont.truetype("arial.ttf", 32)
        except:
            font = ImageFont.load_default()
        
        # 粘贴图片并添加标注
        for idx, frame_path in enumerate(frame_paths):
            img = Image.open(frame_path)
            x = (idx % cols) * w
            y = (idx // cols) * h
            
            canvas.paste(img, (x, y))
            
            # 添加 Shot 标注
            shot_num = idx + 1
            draw.rectangle(
                [x + 5, y + 5, x + 140, y + 40],
                fill="black"
            )
            draw.text(
                (x + 10, y + 10),
                f"Shot {shot_num:02d}",
                fill="white",
                font=font
            )
            
            img.close()
        
        # 保存
        if output_path is None:
            output_path = self.output_dir / "storyboard.jpg"
        else:
            output_path = Path(output_path)
        
        canvas.save(str(output_path), quality=95)
        print(f"✓ 分镜拼图已保存: {output_path}")
        
        return str(output_path)
    
    def remove_watermark(self, image_path: str) -> str:
        """
        去除水印（简单实现）
        
        Args:
            image_path: 图片路径
            
        Returns:
            处理后的图片路径
        """
        # TODO: 实现更复杂的水印去除算法
        # 目前只是复制原图
        img = Image.open(image_path)
        output_path = Path(image_path).with_suffix(".no_watermark.jpg")
        img.save(str(output_path), quality=95)
        return str(output_path)
    
    def get_video_info(self, video_path: str) -> Dict[str, Any]:
        """
        获取视频信息
        
        Args:
            video_path: 视频文件路径
            
        Returns:
            视频信息字典
        """
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=duration,width,height",
            "-of", "json",
            video_path
        ]
        
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            info = json.loads(result.stdout)
            
            streams = info.get("streams", [])
            if streams:
                stream = streams[0]
                return {
                    "duration": float(stream.get("duration", 0)),
                    "width": int(stream.get("width", 0)),
                    "height": int(stream.get("height", 0))
                }
        except Exception as e:
            print(f"获取视频信息失败: {e}")
        
        return {"duration": 0, "width": 0, "height": 0}


# 全局实例
video_processor = VideoProcessor()
