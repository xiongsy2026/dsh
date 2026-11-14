"""
视频角色检测服务
====================
从视频中提取关键帧，使用人脸检测+聚类识别不同角色，
输出每个角色的人脸截图。

依赖：
  - opencv-python（抽帧+人脸检测）
  - scikit-learn（人脸特征聚类）
  - insightface 或 OpenCV DNN 人脸检测

优先使用 insightface，次选 OpenCV DNN SSD（避免 CascadeClassifier 依赖）。
"""

import os
import asyncio
import logging
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ── 人脸检测器 ──

_face_detector = None
# OpenCV DNN 人脸检测模型（res10_300x300）的下载 URL
# 使用 jsdelivr CDN 镜像 raw.githubusercontent.com，提升国内可用性
_DNN_MODEL_URL = "https://cdn.jsdelivr.net/gh/opencv/opencv_3rdparty@dnn_samples_face_detector_20170830/res10_300x300_ssd_iter_140000_fp16.caffemodel"
_DNN_MODEL_FILE = os.path.join("models", "face_detector.caffemodel")
_DNN_CONFIG_URL = "https://cdn.jsdelivr.net/gh/opencv/opencv_3rdparty@dnn_samples_face_detector_20170830/deploy.prototxt"
_DNN_CONFIG_FILE = os.path.join("models", "face_detector.prototxt")


def _download_dnn_model():
    """下载 OpenCV DNN 人脸检测模型"""
    os.makedirs("models", exist_ok=True)
    if not os.path.exists(_DNN_MODEL_FILE):
        import urllib.request
        logger.info("下载 DNN 人脸检测模型...")
        urllib.request.urlretrieve(_DNN_MODEL_URL, _DNN_MODEL_FILE)
    if not os.path.exists(_DNN_CONFIG_FILE):
        import urllib.request
        urllib.request.urlretrieve(_DNN_CONFIG_URL, _DNN_CONFIG_FILE)


def _get_face_detector():
    """获取人脸检测器（优先 insightface，退化为 OpenCV DNN）"""
    global _face_detector
    if _face_detector is not None:
        return _face_detector

    # 尝试 insightface
    try:
        import insightface
        from insightface.app import FaceAnalysis
        app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        app.prepare(ctx_id=0, det_size=(640, 640))
        _face_detector = ("insightface", app)
        logger.info("使用 insightface 人脸检测器")
        return _face_detector
    except Exception as e:
        logger.warning(f"insightface 加载失败: {e}，退化为 OpenCV DNN")

    # 退化为 OpenCV DNN SSD（不依赖 CascadeClassifier，避免 AttributeError）
    try:
        _download_dnn_model()
        net = cv2.dnn.readNetFromCaffe(_DNN_CONFIG_FILE, _DNN_MODEL_FILE)
        _face_detector = ("opencv_dnn", net)
        logger.info("使用 OpenCV DNN 人脸检测器")
        return _face_detector
    except Exception as e:
        logger.warning(f"OpenCV DNN 加载失败: {e}")

    raise RuntimeError("无人脸检测器可用，请安装 insightface 或检查 OpenCV 版本")


def _detect_faces_opencv(frame: np.ndarray, net) -> list[dict]:
    """使用 OpenCV DNN SSD 检测人脸"""
    h, w = frame.shape[:2]
    blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)), 1.0,
                                 (300, 300), (104.0, 177.0, 123.0))
    net.setInput(blob)
    detections = net.forward()
    results = []
    for i in range(detections.shape[2]):
        confidence = detections[0, 0, i, 2]
        if confidence < 0.5:
            continue
        box = detections[0, 0, i, 3:7] * np.array([w, h, w, h])
        (x1, y1, x2, y2) = box.astype("int")
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        results.append({
            "bbox": [int(x1), int(y1), int(x2 - x1), int(y2 - y1)],
            "embedding": None,
        })
    return results


def _detect_faces_insightface(frame: np.ndarray, app) -> list[dict]:
    """使用 insightface 检测人脸"""
    faces = app.get(frame)
    results = []
    for face in faces:
        bbox = face.bbox.astype(int).tolist()  # [x1, y1, x2, y2]
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        results.append({
            "bbox": [bbox[0], bbox[1], w, h],
            "embedding": face.embedding if hasattr(face, "embedding") else None,
        })
    return results


def _detect_faces(frame: np.ndarray) -> list[dict]:
    """统一人脸检测接口"""
    detector_type, detector = _get_face_detector()
    if detector_type == "insightface":
        return _detect_faces_insightface(frame, detector)
    else:
        return _detect_faces_opencv(frame, detector)


def _cluster_faces(embeddings: list[np.ndarray], threshold: float = 0.35) -> list[int]:
    """
    基于余弦相似度对人脸特征进行聚类
    返回每个特征对应的聚类标签

    insightface buffalo_l 的 embedding 余弦相似度：
    - 同一人在不同角度/光照下通常 > 0.4
    - 不同人通常 < 0.3
    阈值 0.35 偏松，更倾向合并同一人的不同帧

    使用正确的并查集（union-find），避免传递合并错误。
    """
    if not embeddings:
        return []

    n = len(embeddings)
    # 归一化 embedding，便于直接用点积作为余弦相似度
    normed = []
    for e in embeddings:
        v = np.asarray(e, dtype=np.float32).flatten()
        nrm = np.linalg.norm(v) + 1e-8
        normed.append(v / nrm)
    normed = np.stack(normed, axis=0)  # (n, d)

    # ── 并查集 ──
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # 路径压缩
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra  # 把 b 的根挂到 a 的根下

    # 计算 n×n 余弦相似度矩阵（点积，因为已归一化）
    sim_matrix = normed @ normed.T  # (n, n)

    for i in range(n):
        for j in range(i + 1, n):
            if sim_matrix[i, j] > threshold:
                union(i, j)

    # 收集根，重新编号
    roots = [find(i) for i in range(n)]
    unique = sorted(set(roots))
    mapping = {old: new for new, old in enumerate(unique)}
    return [mapping[r] for r in roots]


async def detect_characters_in_video(video_path: str, max_frames: int = 30) -> list[dict]:
    """
    从视频中检测角色

    流程：
    1. 均匀抽帧
    2. 每帧检测人脸
    3. 用人脸特征聚类区分不同角色
    4. 每个角色选最佳人脸截图保存

    Returns:
        [{"id": 0, "name": "角色1", "face_image_path": "..."}]
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频: {video_path}")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25

    # 均匀抽帧
    sample_count = min(max_frames, total_frames)
    step = max(1, total_frames // sample_count)

    all_faces = []  # [{frame_idx, bbox, embedding, face_img}]

    for i in range(sample_count):
        frame_idx = i * step
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret:
            continue

        faces = _detect_faces(frame)
        for f in faces:
            x, y, w, h = f["bbox"]
            # 裁剪人脸区域
            x1, y1 = max(0, x), max(0, y)
            x2, y2 = min(frame.shape[1], x + w), min(frame.shape[0], y + h)
            face_img = frame[y1:y2, x1:x2]

            if face_img.size == 0:
                continue

            all_faces.append({
                "frame_idx": frame_idx,
                "bbox": f["bbox"],
                "embedding": f["embedding"],
                "face_img": face_img,
                "area": w * h,
            })

    cap.release()

    if not all_faces:
        logger.warning("视频中未检测到人脸")
        return []

    # ── 聚类 ──
    embeddings = [f["embedding"] for f in all_faces if f["embedding"] is not None]

    if embeddings:
        labels = _cluster_faces(embeddings)
        # 对没有 embedding 的，按位置简单归类
        emb_idx = 0
        for f in all_faces:
            if f["embedding"] is not None:
                f["label"] = labels[emb_idx]
                emb_idx += 1
            else:
                f["label"] = -1  # 未归类
    else:
        # 无 embedding，用 bbox 中心位置聚类（粗略）
        for i, f in enumerate(all_faces):
            f["label"] = 0  # 全部视为同一人

    # ── 每个角色选最佳人脸 ──
    unique_labels = sorted(set(f["label"] for f in all_faces if f["label"] >= 0))

    # 输出目录
    output_dir = os.path.join("outputs", "character_detect", os.path.splitext(os.path.basename(video_path))[0])
    os.makedirs(output_dir, exist_ok=True)

    characters = []
    for char_idx, label in enumerate(unique_labels):
        char_faces = [f for f in all_faces if f["label"] == label]
        if not char_faces:
            continue

        # 选面积最大的人脸
        best = max(char_faces, key=lambda f: f["area"])
        face_path = os.path.join(output_dir, f"character_{char_idx + 1}.jpg")
        cv2.imwrite(face_path, best["face_img"])

        characters.append({
            "id": char_idx,
            "name": f"角色 {char_idx + 1}",
            "face_image_path": os.path.abspath(face_path),
        })

    logger.info(f"检测到 {len(characters)} 个角色")
    return characters
