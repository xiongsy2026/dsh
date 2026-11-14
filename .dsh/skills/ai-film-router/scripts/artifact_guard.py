from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Optional


SCHEMA_VERSION = "3.3"


def project_path(value: str) -> Path:
    root = Path(value).expanduser().resolve()
    if not (root / "00_project").is_dir():
        raise ValueError(f"不是电影项目目录，缺少：{root / '00_project'}")
    return root


def state_path(root: Path) -> Path:
    return root / "00_project" / "artifact-state.json"


def registry_path(root: Path) -> Path:
    return root / "00_project" / "artifact-registry.md"


def normalize_artifact_path(root: Path, value: str) -> str:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as error:
        raise ValueError(f"成果文件必须位于电影项目内：{resolved}") from error


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", newline="\n", delete=False, dir=path.parent) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def load_state(root: Path) -> dict:
    path = state_path(root)
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "artifacts": [], "current": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("成果状态文件不是 V3.3，请先升级当前电影工程规则。")
    data.setdefault("artifacts", [])
    data.setdefault("current", {})
    return data


def save_state(root: Path, data: dict) -> None:
    atomic_write_text(state_path(root), json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    render_registry(root, data)


def artifact_key(kind: str, scope: str, model: str) -> str:
    return "::".join((kind.strip(), scope.strip(), model.strip()))


def record_for_path(data: dict, path: str) -> Optional[dict]:
    return next((item for item in data["artifacts"] if item["path"] == path), None)


def render_registry(root: Path, data: dict) -> None:
    lines = [
        "# 项目成果版本登记表",
        "",
        "登记表版本：V3.3",
        "",
        "本文件是人可读总表；`artifact-state.json` 是同一登记的机器状态。聊天只负责讨论，正式执行以这里登记的最新已确认为准。",
        "",
        "| 成果 | 范围 | 当前草稿 | 最新已确认 | 状态 | 直接依据 | 影响范围 |",
        "|---|---|---|---|---|---|---|",
    ]
    if not data["current"]:
        lines.append("| 无 | 全项目 | 无 | 无 | 未开始 | 无 | 无 |")
    else:
        for key in sorted(data["current"]):
            current = data["current"][key]
            kind, scope, model = key.split("::", 2)
            label = kind if not model else f"{kind} / {model}"
            draft = current.get("draft") or "无"
            confirmed = current.get("confirmed") or "无"
            selected = record_for_path(data, draft) or record_for_path(data, confirmed) or {}
            dependencies = "、".join(selected.get("depends_on", [])) or "无"
            affected = "、".join(selected.get("affected_shots", [])) or "无"
            lines.append(
                f"| {label} | {scope} | `{draft}` | `{confirmed}` | {current.get('status', '未开始')} | {dependencies} | {affected} |"
            )
    lines.extend(
        [
            "",
            "## 固定规则",
            "",
            "1. 状态只使用：`草稿待确认`、`已确认-当前`、`历史已替代`、`已过期-禁止使用`、`缺文件-阻断`。",
            "2. 同阶段继续修改时读取当前草稿与最新已确认；跨阶段只读取最新已确认。",
            "3. 新草稿确认前不影响旧确认稿。新版本确认后，依赖旧确认稿的下游自动变为“已过期-禁止使用”。",
            "4. 08 按场次登记镜头清单、空间图、分镜图绑定、视觉连续性质检与提示词；每镜正式提示词必须依赖实际参考图。",
            "5. 多个 Agent 不得同时写同一项目。写入前获取 `project-edit-lock.json`，完成后释放。",
            "6. 文件不存在、依赖过期或版本对不上时停止，不能用聊天印象补齐。",
            "",
        ]
    )
    atomic_write_text(registry_path(root), "\n".join(lines))


def command_next_version(args: argparse.Namespace) -> None:
    directory = Path(args.directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(rf"^{re.escape(args.stem)}-v(\d+)\.{re.escape(args.extension)}$")
    versions = [int(match.group(1)) for path in directory.iterdir() if path.is_file() and (match := pattern.match(path.name))]
    print(directory / f"{args.stem}-v{max(versions, default=0) + 1:03d}.{args.extension}")


def command_register_draft(args: argparse.Namespace) -> None:
    root = project_path(args.project)
    data = load_state(root)
    relative = normalize_artifact_path(root, args.path)
    if not (root / relative).is_file():
        raise FileNotFoundError(f"必须先成功写入草稿文件，再登记：{root / relative}")
    dependencies = [normalize_artifact_path(root, value) for value in args.depends_on]
    for dependency in dependencies:
        dependency_record = record_for_path(data, dependency)
        if dependency_record is None:
            raise ValueError(f"依赖尚未登记：{dependency}")
        if dependency_record.get("status") != "已确认-当前":
            raise ValueError(f"依赖不是当前确认版本：{dependency} / {dependency_record.get('status')}")
    key = artifact_key(args.kind, args.scope, args.model)
    record = record_for_path(data, relative)
    payload = {
        "kind": args.kind,
        "scope": args.scope,
        "model": args.model,
        "path": relative,
        "status": "草稿待确认",
        "depends_on": dependencies,
        "affected_shots": args.affected_shot,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    if record is None:
        data["artifacts"].append(payload)
    else:
        record.update(payload)
    current = data["current"].setdefault(key, {"draft": None, "confirmed": None, "status": "未开始"})
    current["draft"] = relative
    current["status"] = "草稿待确认"
    save_state(root, data)
    print(f"已登记草稿：{relative}")


def cascade_stale(data: dict, replaced_path: str) -> list[str]:
    queue = [replaced_path]
    stale_paths: list[str] = []
    while queue:
        dependency = queue.pop(0)
        for record in data["artifacts"]:
            if record["status"] in {"已过期-禁止使用", "历史已替代"}:
                continue
            if dependency in record.get("depends_on", []):
                record["status"] = "已过期-禁止使用"
                record["stale_reason"] = f"上游已替换：{dependency}"
                stale_paths.append(record["path"])
                queue.append(record["path"])
    for current in data["current"].values():
        if current.get("draft") in stale_paths or current.get("confirmed") in stale_paths:
            current["status"] = "已过期-禁止使用"
    return stale_paths


def command_confirm(args: argparse.Namespace) -> None:
    root = project_path(args.project)
    data = load_state(root)
    key = artifact_key(args.kind, args.scope, args.model)
    current = data["current"].get(key)
    if current is None:
        raise ValueError("没有找到对应草稿登记。")
    target = normalize_artifact_path(root, args.path) if args.path else current.get("draft")
    if not target:
        raise ValueError("当前没有待确认草稿。")
    record = record_for_path(data, target)
    if record is None or record.get("status") != "草稿待确认":
        raise ValueError(f"目标不是待确认草稿：{target}")
    for dependency in record.get("depends_on", []):
        dependency_record = record_for_path(data, dependency)
        if dependency_record is None or dependency_record.get("status") != "已确认-当前":
            status = dependency_record.get("status") if dependency_record else "未登记"
            raise ValueError(f"确认被阻断，依赖已变化：{dependency} / {status}")
    old_confirmed = current.get("confirmed")
    if old_confirmed and old_confirmed != target:
        old_record = record_for_path(data, old_confirmed)
        if old_record and old_record.get("status") != "已过期-禁止使用":
            old_record["status"] = "历史已替代"
    record["status"] = "已确认-当前"
    record["confirmed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    current["confirmed"] = target
    current["draft"] = None
    current["status"] = "已确认-当前"
    stale = cascade_stale(data, old_confirmed) if old_confirmed and old_confirmed != target else []
    save_state(root, data)
    print(f"已确认：{target}")
    if stale:
        print("已自动标记过期：")
        for path in stale:
            print(f"- {path}")


def lock_path(root: Path) -> Path:
    return root / "00_project" / "project-edit-lock.json"


def command_acquire_lock(args: argparse.Namespace) -> None:
    root = project_path(args.project)
    path = lock_path(root)
    payload = {
        "owner": args.owner,
        "scope": args.scope,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as error:
        existing = json.loads(path.read_text(encoding="utf-8"))
        raise RuntimeError(f"项目正由 {existing.get('owner', '其他 Agent')} 编辑：{existing.get('scope', '未注明范围')}") from error
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"已获取项目写入锁：{args.owner} / {args.scope}")


def command_release_lock(args: argparse.Namespace) -> None:
    root = project_path(args.project)
    path = lock_path(root)
    if not path.exists():
        print("项目当前没有写入锁。")
        return
    existing = json.loads(path.read_text(encoding="utf-8"))
    if existing.get("owner") != args.owner:
        raise RuntimeError(f"写入锁属于 {existing.get('owner')}，不能由 {args.owner} 释放。")
    path.unlink()
    print("已释放项目写入锁。")


def command_validate(args: argparse.Namespace) -> None:
    root = project_path(args.project)
    data = load_state(root)
    errors: list[str] = []
    for record in data["artifacts"]:
        artifact = root / record["path"]
        if not artifact.is_file():
            errors.append(f"成果文件不存在：{record['path']}")
        for dependency in record.get("depends_on", []):
            if not (root / dependency).is_file():
                errors.append(f"依赖文件不存在：{record['path']} -> {dependency}")
    for key, current in data["current"].items():
        confirmed = current.get("confirmed")
        if confirmed:
            record = record_for_path(data, confirmed)
            if record is None:
                errors.append(f"最新确认未登记：{key} -> {confirmed}")
            elif record.get("status") == "已过期-禁止使用":
                errors.append(f"最新确认已经过期：{key} -> {confirmed}")
    if errors:
        print("版本校验失败：")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(2)
    print(f"版本校验通过：{len(data['artifacts'])} 份成果记录。")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI STUDIO 洋子 V3.3 成果版本守卫")
    subparsers = parser.add_subparsers(dest="command", required=True)

    next_version = subparsers.add_parser("next-version")
    next_version.add_argument("--directory", required=True)
    next_version.add_argument("--stem", required=True)
    next_version.add_argument("--extension", default="md")
    next_version.set_defaults(func=command_next_version)

    register = subparsers.add_parser("register-draft")
    register.add_argument("--project", required=True)
    register.add_argument("--kind", required=True)
    register.add_argument("--scope", default="全项目")
    register.add_argument("--model", default="")
    register.add_argument("--path", required=True)
    register.add_argument("--depends-on", action="append", default=[])
    register.add_argument("--affected-shot", action="append", default=[])
    register.set_defaults(func=command_register_draft)

    confirm = subparsers.add_parser("confirm")
    confirm.add_argument("--project", required=True)
    confirm.add_argument("--kind", required=True)
    confirm.add_argument("--scope", default="全项目")
    confirm.add_argument("--model", default="")
    confirm.add_argument("--path")
    confirm.set_defaults(func=command_confirm)

    acquire = subparsers.add_parser("acquire-lock")
    acquire.add_argument("--project", required=True)
    acquire.add_argument("--owner", required=True)
    acquire.add_argument("--scope", required=True)
    acquire.set_defaults(func=command_acquire_lock)

    release = subparsers.add_parser("release-lock")
    release.add_argument("--project", required=True)
    release.add_argument("--owner", required=True)
    release.set_defaults(func=command_release_lock)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--project", required=True)
    validate.set_defaults(func=command_validate)
    return parser


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    try:
        args.func(args)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"错误：{error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
