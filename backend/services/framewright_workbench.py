"""
Framewright 工作台服务

按 skills/framewright 的使用方法驱动（SKILL.md Workflow）：
  1. Unified Director Intake（UNDERSTANDING / PRODUCTION READING / DECISIONS）
  2. Production Spine 确认（导演意图、阶段、目标模型）
  3. 每次只编译一个阶段（storyboard / keyframes / video_prompt）
  4. 保存 .txt 提示词工件 + 交付 handoff + 官方校验器校验

规范来源：skills/framewright/references/framewright.md（权威规范）
序列化适配器：references/runtime_profiles/*.md（视频）、references/keyframe_profiles/*.md（图片）
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
# 规范要求仓库布局 skill/framewright（校验器按 repository_root/skill/framewright 解析注册源）
SKILL_DIR = ROOT / "skill" / "framewright"
CORE_SPEC = SKILL_DIR / "references" / "framewright.md"
RUNTIME_DIR = SKILL_DIR / "references" / "runtime_profiles"
KEYFRAME_DIR = SKILL_DIR / "references" / "keyframe_profiles"
VALIDATOR = SKILL_DIR / "scripts" / "validate_framewright.py"
# 规范默认交付路径：Framewright/outputs/[project_slug]/[generation_unit_slug]/prompt_*.txt
OUTPUT_ROOT = ROOT / "Framewright" / "outputs"

# 注册路由（对应 adapter_registry.yaml）
VIDEO_TARGETS = {
    "seedance_2_0": {"label": "Seedance 2.0", "owner": "framewright_adapter_seedance_2_0", "profile": "seedance_2_0.md"},
    "seedance_2_5": {"label": "Seedance 2.5", "owner": "framewright_adapter_seedance_2_5", "profile": "seedance_2_5.md"},
    "minimax_h3": {"label": "MiniMax H3", "owner": "framewright_adapter_minimax_h3", "profile": "minimax_h3.md"},
}
IMAGE_TARGETS = {
    "midjourney_v8_2": {"label": "Midjourney V8.2", "create_profile": "midjourney_v8_2.md", "edit_profile": "gpt_image_2_5_edit.md"},
    "gpt_image_2_5": {"label": "GPT Image 2.5", "create_profile": "gpt_image_2_5.md", "edit_profile": "gpt_image_2_5_edit.md"},
}
STAGE_FILES = {
    "storyboard": "prompt_storyboard.txt",
    "keyframes": "prompt_keyframes.txt",
    "video_prompt": "prompt_video.txt",
}
DIRECTOR_MODES = ("auteur", "apprentice", "screenwriter")


# ---------------------------------------------------------------- 基础读取

def _front_matter_version() -> str:
    text = CORE_SPEC.read_text(encoding="utf-8")
    m = re.search(r'^version:\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else ""


def get_status() -> Dict[str, Any]:
    """工作台状态：skill 安装、规范版本、注册路由。"""
    installed = CORE_SPEC.exists()
    return {
        "installed": installed,
        "skill_dir": str(SKILL_DIR),
        "version": _front_matter_version() if installed else "",
        "core_spec": str(CORE_SPEC),
        "video_targets": [{"id": k, "label": v["label"]} for k, v in VIDEO_TARGETS.items()],
        "image_targets": [{"id": k, "label": v["label"]} for k, v in IMAGE_TARGETS.items()],
        "stages": [
            {"id": "storyboard", "label": "Storyboard 分镜板", "file": STAGE_FILES["storyboard"]},
            {"id": "keyframes", "label": "Keyframes 关键帧 / Shot Plate", "file": STAGE_FILES["keyframes"]},
            {"id": "video_prompt", "label": "Video Prompt 视频提示词", "file": STAGE_FILES["video_prompt"]},
        ],
        "director_modes": list(DIRECTOR_MODES),
    }


def _extract_sections(header_patterns: List[str]) -> str:
    """从权威规范中按章节标题提取治理段落（§3 Intake、§13 Video Prompt 等）。"""
    text = CORE_SPEC.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: List[str] = []
    for pattern in header_patterns:
        rx = re.compile(pattern)
        start = None
        for i, ln in enumerate(lines):
            if ln.startswith("#") and rx.search(ln):
                start = i
                break
        if start is None:
            continue
        level = len(lines[start]) - len(lines[start].lstrip("#"))
        end = len(lines)
        for j in range(start + 1, len(lines)):
            ln = lines[j]
            if ln.startswith("#") and (len(ln) - len(ln.lstrip("#"))) <= level:
                end = j
                break
        out.append("\n".join(lines[start:end]))
    return "\n\n".join(out)


def _load_profile(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _slugify(value: str) -> str:
    s = re.sub(r"[^\w\-]+", "-", value.strip().lower()).strip("-")
    return s or "unit"


# ---------------------------------------------------------------- LLM 调用

def _gpt():
    from backend.services.gpt_client import gpt_client
    return gpt_client


async def _ask(system: str, user: str, max_tokens: int = 12000) -> str:
    resp = await _gpt().chat_completion(
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.6,
        max_tokens=max_tokens,
    )
    return resp["choices"][0]["message"]["content"]


# ---------------------------------------------------------------- Intake

INTAKE_CONTRACT = """你正在执行 Framewright 的 Unified Director Intake（导演统一进场）。
按规范输出固定三段（英文标题，内容用中文）：

UNDERSTANDING
[场景、可见动作、预期结果的紧凑复述；保留用户有创作意图的原话。]

PRODUCTION READING
[临时解读：director mode、scene grammar、推断权限范围、生成单元形状、参考素材用途、
大概输出阶段、影响最大的未决区域。假设要标注。]

DECISIONS
[只问会实质改变下游契约的问题：先一个依赖性的高影响问题，或真正独立的不超过 5 个批量；
每个问题给 UNRESOLVED DECISION / WHY IT MATTERS / OPTIONS / RECOMMENDATION。
安全可推断的执行细节、装饰性细节不要问。]
"""


async def run_intake(brief: str, assets: Optional[List[str]] = None, extra: str = "") -> Dict[str, Any]:
    """Step 1-2：导演进场复述 + 制作解读 + 决策问题。"""
    if not CORE_SPEC.exists():
        raise RuntimeError("Framewright 规范未安装（skills/framewright 缺失）")
    core = _extract_sections([r"^## 3\. Unified Director Intake"])
    system = (
        "你是 Framewright v" + _front_matter_version() + "，导演主导、意图保持的电影化编译器。\n"
        "本次只做 Unified Director Intake，不产出任何提示词文件。\n\n"
        "=== 权威规范节选（必须遵守）===\n" + core + "\n\n"
        "=== Intake 输出契约 ===\n" + INTAKE_CONTRACT
    )
    user = "【素材/场景简述】\n" + brief.strip()
    if assets:
        user += "\n\n【参考素材】\n" + "\n".join(f"- {a}" for a in assets if a)
    if extra.strip():
        user += "\n\n【导演补充】\n" + extra.strip()
    text = await _ask(system, user, max_tokens=4000)
    return {"success": True, "review": text, "version": _front_matter_version()}


# ---------------------------------------------------------------- Compile

def _compile_system(stage: str, target: str) -> str:
    version = _front_matter_version()
    core_rules = _extract_sections([
        r"^## 14\. Runtime Cleanliness",
        r"^## 15\. File Output Workflow",
    ])
    if stage == "video_prompt":
        stage_spec = _extract_sections([r"^## 13\. Video Prompt Stage"])
        info = VIDEO_TARGETS.get(target)
        if not info:
            raise RuntimeError(f"未注册的视频目标模型: {target}（支持: {', '.join(VIDEO_TARGETS)}）")
        adapter = _load_profile(RUNTIME_DIR / info["profile"])
        header = (
            f"你是 Framewright v{version} 编译器。本次唯一激活阶段 = Video Prompt，"
            f"目标模型 = {info['label']}（target_model={target}，serialization_owner={info['owner']}）。\n"
            "目标模型决定方言：只加载并服从下方这一个 runtime profile 序列化，不得混入其他模型的规则。\n"
        )
    elif stage == "keyframes":
        info = IMAGE_TARGETS.get(target)
        if not info:
            raise RuntimeError(f"未注册的图片目标模型: {target}（支持: {', '.join(IMAGE_TARGETS)}）")
        stage_spec = _extract_sections([r"^## 12\. Keyframes Stage"])
        adapter = _load_profile(KEYFRAME_DIR / info["create_profile"])
        header = (
            f"你是 Framewright v{version} 编译器。本次唯一激活阶段 = Keyframes（含 Shot Plate），"
            f"图像创建目标 = {info['label']}。\n"
            "只服从下方注册的图像 adapter 序列化规则。\n"
        )
    else:  # storyboard
        info = IMAGE_TARGETS.get(target) or IMAGE_TARGETS["gpt_image_2_5"]
        stage_spec = _extract_sections([r"^## 11\. Storyboard Stage", r"^### 8\.2\.1 Storyboard Layout Contract"])
        adapter = _load_profile(KEYFRAME_DIR / info["create_profile"])
        header = (
            f"你是 Framewright v{version} 编译器。本次唯一激活阶段 = Storyboard，"
            f"图像创建目标 = {info['label']}。\n"
            "只服从下方注册的图像 adapter 序列化规则。\n"
        )
    return (
        header
        + "\n硬规则：只编译当前这一个阶段；不重开创作探索；保留导演锁定的意图与台词；"
        + "输出干净的模型面向提示词（不把 director mode、ownership 元数据写进提示词正文）。\n"
        + "\n=== 权威规范 · 阶段 ===\n" + stage_spec
        + "\n\n=== 权威规范 · 干净度与交付 ===\n" + core_rules
        + "\n\n=== 目标模型序列化 adapter（唯一序列化方）===\n" + adapter
    )


COMPILE_CONTRACT = """编译完成后输出一个 JSON 对象（不要输出其他任何内容）：
{
  "clean_prompt": "保存进 .txt 的完整干净提示词正文",
  "handoff": "assistant 面向的交付说明：active stage、director mode、生成单元状态、prompt 方言、参考素材责任表（如有 @标记）、假设、未决/残留风险、INTENT DELTA（DIRECTOR LOCKS / APPROVED DECISIONS / FRAMEWRIGHT INFERENCES / INTENTIONAL FREEDOM / UNRESOLVED）"
}
"""


async def run_compile(
    project_slug: str,
    unit_slug: str,
    stage: str,
    target: str,
    director_mode: str,
    materials: str,
    spine: str = "",
    answers: str = "",
) -> Dict[str, Any]:
    """Step 3-4：编译单个阶段 → 保存 .txt → 官方校验器校验 → 交付 handoff。"""
    if not CORE_SPEC.exists():
        raise RuntimeError("Framewright 规范未安装（skills/framewright 缺失）")
    if stage not in STAGE_FILES:
        raise RuntimeError(f"未知阶段: {stage}（支持: {', '.join(STAGE_FILES)}）")
    if director_mode not in DIRECTOR_MODES:
        raise RuntimeError(f"未知导演模式: {director_mode}（支持: {', '.join(DIRECTOR_MODES)}）")

    system = _compile_system(stage, target) + "\n\n=== 输出契约 ===\n" + COMPILE_CONTRACT
    user = (
        f"【已批准的 Production Spine / 制作素材】\n{materials.strip()}\n"
        + (f"\n\n【Production Spine 补充】\n{spine.strip()}\n" if spine.strip() else "")
        + (f"\n\n【导演对 DECISIONS 的回答】\n{answers.strip()}\n" if answers.strip() else "")
        + f"\n\n【编译参数】stage={stage} target={target} director_mode={director_mode}（director_mode 不得写入提示词正文）"
        + "\n请跨过 Approved Compile Boundary：直接编译，输出 JSON。"
    )
    text = await _ask(system, user, max_tokens=12000)

    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError("编译输出不是 JSON: " + text[:200])
    data = json.loads(m.group(0))
    clean_prompt = (data.get("clean_prompt") or "").strip()
    handoff = (data.get("handoff") or "").strip()
    if not clean_prompt:
        raise RuntimeError("编译输出缺少 clean_prompt")

    out_dir = OUTPUT_ROOT / _slugify(project_slug) / _slugify(unit_slug)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / STAGE_FILES[stage]
    out_path.write_text(clean_prompt, encoding="utf-8")

    validation = await asyncio.to_thread(_validate, stage, target, out_path)
    return {
        "success": True,
        "stage": stage,
        "target": target,
        "director_mode": director_mode,
        "file": str(out_path),
        "clean_prompt": clean_prompt,
        "handoff": handoff,
        "validation": validation,
        "version": _front_matter_version(),
    }


def _validate(stage: str, target: str, path: Path) -> Dict[str, Any]:
    """跑官方校验器（ownership-aware）。"""
    import subprocess
    import sys

    if not VALIDATOR.exists():
        return {"ok": False, "output": "validate_framewright.py 不存在"}
    cmd = [sys.executable, str(VALIDATOR)]
    if stage == "video_prompt":
        cmd += ["video-prompt", str(path), "--target-model", target, "--serialization-owner", VIDEO_TARGETS[target]["owner"]]
        cmd += ["--adapter-id", target, "--profile-contract", target]
        cmd += ["--compiler-source", "skill/framewright/SKILL.md", "--compiler-source", "skill/framewright/references/framewright.md"]
        profile_rel = f"skill/framewright/references/runtime_profiles/{VIDEO_TARGETS[target]['profile']}"
        cmd += ["--compiler-source", profile_rel]
    elif stage == "keyframes":
        cmd += ["keyframe-prompt", str(path), "--adapter-id", target, "--artifact-kind", "keyframe"]
    else:
        cmd += ["prompt", str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=str(ROOT))
        out = (proc.stdout or "") + (proc.stderr or "")
        return {"ok": proc.returncode == 0, "output": out.strip()[:4000]}
    except Exception as e:
        return {"ok": False, "output": f"校验执行失败: {e}"}
