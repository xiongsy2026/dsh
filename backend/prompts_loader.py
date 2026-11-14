"""提示词集中管理模块

从 prompts.json 读取提示词，运行时可通过 API 动态修改。
各步骤通过 get_prompt(step_key, prompt_key) 获取。
"""
import json
from pathlib import Path
from typing import Dict

_PROMPTS_FILE = Path(__file__).parent / "prompts.json"
_cache: Dict = {}


def _load() -> Dict:
    """加载 prompts.json（带缓存，但每次 reload 都会重读）"""
    global _cache
    if not _cache:
        with open(_PROMPTS_FILE, "r", encoding="utf-8") as f:
            _cache = json.load(f)
    return _cache


def reload():
    """重新从文件加载（API 更新后调用）"""
    global _cache
    _cache = {}
    return _load()


def get_all() -> Dict:
    """返回完整提示词配置"""
    return _load()


def get_step(step_key: str) -> Dict:
    """返回某个步骤的全部提示词"""
    return _load().get(step_key, {})


def get_prompt(step_key: str, prompt_key: str) -> str:
    """返回具体某个提示词的值"""
    step = _load().get(step_key, {})
    return step.get("prompts", {}).get(prompt_key, {}).get("value", "")


def save(new_data: Dict) -> Dict:
    """保存新提示词到文件"""
    global _cache
    with open(_PROMPTS_FILE, "w", encoding="utf-8") as f:
        json.dump(new_data, f, ensure_ascii=False, indent=2)
    _cache = new_data
    return _cache


def update_prompt(step_key: str, prompt_key: str, value: str) -> bool:
    """更新单个提示词"""
    data = _load()
    if step_key not in data:
        return False
    if prompt_key not in data[step_key].get("prompts", {}):
        return False
    data[step_key]["prompts"][prompt_key]["value"] = value
    save(data)
    return True
