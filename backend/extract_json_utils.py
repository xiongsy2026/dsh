"""
============================================================================
⚠️ 爆款复刻工作流 - 受保护文件（整文件保护）
参见 BOUNDARY.md。修改本文件必须经过显式确认：
  1. 运行 python scripts/check_replica_boundary.py 记录变更
  2. 运行 pytest tests/test_replica_boundary.py -v 验证行为
  3. 运行 python scripts/update_replica_boundary.py 更新签名基准
未经流程确认的修改将破坏工作流稳定性。
============================================================================
"""
import json
from typing import Any, Optional


SECTION_MARKERS = ["【一】", "【二】", "【三】", "【四】"]


def _balanced_json_from(text: str, start: int) -> Optional[str]:
    opener = text[start]
    closer = "]" if opener == "[" else "}"
    depth = 0
    in_string = False
    escape = False

    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start:idx + 1]
    return None


def _json_candidates(text: str):
    for idx, ch in enumerate(text):
        if ch not in "[{":
            continue
        raw = _balanced_json_from(text, idx)
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            continue


def _section_text(text: str, marker: str) -> str:
    start = text.rfind(marker)
    if start == -1:
        return ""

    end = len(text)
    for item in SECTION_MARKERS:
        if item == marker:
            continue
        pos = text.find(item, start + len(marker))
        if pos != -1:
            end = min(end, pos)
    return text[start:end]


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("shots", "timeline", "ffmpeg_timeline", "FFmpeg时间轴", "分镜脚本"):
            child = value.get(key)
            if isinstance(child, list):
                return child
    return []


def _looks_like_shots(value: Any) -> bool:
    items = _as_list(value)
    if not items or not all(isinstance(item, dict) for item in items):
        return False
    shot_fields = {"场景", "人物", "产品", "画面内容", "人物动作", "产品动作", "景别", "构图", "运镜", "口播"}
    return sum(1 for item in items if "shot" in item and shot_fields.intersection(item)) >= max(1, len(items) // 2)


def _looks_like_timeline(value: Any) -> bool:
    if isinstance(value, dict) and "shot" in value and "time" in value:
        return False
    items = _as_list(value)
    if not items or not all(isinstance(item, dict) for item in items):
        return False
    return all("shot" in item and "time" in item for item in items)


def _normalize_timeline(value: Any) -> list:
    timeline = []
    for item in _as_list(value):
        try:
            timeline.append({"shot": int(item["shot"]), "time": float(item["time"])})
        except (KeyError, TypeError, ValueError):
            continue
    return timeline


def _extract_first_matching(text: str, marker: str, matcher) -> list:
    section = _section_text(text, marker)
    if not section:
        return []
    for candidate in _json_candidates(section):
        if matcher(candidate):
            return _as_list(candidate)
    return []


def extract_json_blocks(text: str):
    shots = _extract_first_matching(text, "【二】", _looks_like_shots)
    timeline = _extract_first_matching(text, "【三】", _looks_like_timeline)

    if not shots or not timeline:
        for candidate in _json_candidates(text):
            if not shots and _looks_like_shots(candidate):
                shots = _as_list(candidate)
            elif not timeline and _looks_like_timeline(candidate):
                timeline = _normalize_timeline(candidate)
            if shots and timeline:
                break

    blocks = []
    if shots:
        blocks.append(shots)
    if timeline:
        blocks.append(_normalize_timeline(timeline))
    return blocks


def normalize_gemini_result(text: str) -> dict:
    blocks = extract_json_blocks(text)
    shots = blocks[0] if len(blocks) > 0 else []
    timeline = blocks[1] if len(blocks) > 1 else []
    return {
        "shots": shots,
        "timeline": timeline,
        "json_blocks": blocks,
        "shots_count": len(shots),
        "timeline_count": len(timeline),
    }
