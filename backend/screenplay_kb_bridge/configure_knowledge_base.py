from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any


DATABASE_NAME = "curated-screenplay-methods.sqlite"
CATALOG_NAME = "全球剧本知识库索引.csv"
FESTIVAL_CATALOG_NAME = "电影节剧本标签索引.csv"


def find_one(root: Path, filename: str, required: bool) -> Path | None:
    direct_candidates = [
        root / filename,
        root / "99-索引与检索规则" / filename,
    ]
    for candidate in direct_candidates:
        if candidate.is_file():
            return candidate.resolve()

    matches = [path.resolve() for path in root.rglob(filename) if path.is_file()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        preview = "\n".join(f"- {path}" for path in matches[:10])
        raise RuntimeError(f"发现多个 {filename}，请直接选择正确的数据库文件：\n{preview}")
    if required:
        raise FileNotFoundError(
            f"没有在所选位置找到 {filename}。请选择包含 99-索引与检索规则 的知识库根目录，"
            "或直接选择该 SQLite 文件。"
        )
    return None


def inspect_database(database: Path) -> tuple[int, int]:
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
        if not quick_check or quick_check[0] != "ok":
            raise RuntimeError(f"SQLite 完整性检查失败：{quick_check}")
        documents = int(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
        chunks = int(connection.execute("SELECT COUNT(*) FROM chunks_content").fetchone()[0])
        return documents, chunks
    finally:
        connection.close()


def default_output() -> Path:
    parent = Path(__file__).resolve().parent.parent
    if parent.name == "film-knowledge-bridge":
        return parent / "references" / "knowledge-base.json"
    return parent / "knowledge-base.json"


def build_config(selected: Path) -> dict[str, Any]:
    selected = selected.expanduser().resolve()
    if not selected.exists():
        raise FileNotFoundError(f"所选路径不存在：{selected}")

    if selected.is_file():
        if selected.name != DATABASE_NAME:
            raise ValueError(f"请选择知识库根目录或 {DATABASE_NAME}，当前文件不正确：{selected.name}")
        database = selected
        root = selected.parent.parent if selected.parent.name == "99-索引与检索规则" else selected.parent
    else:
        root = selected
        database = find_one(root, DATABASE_NAME, True)
        if database is None:
            raise FileNotFoundError(DATABASE_NAME)

    catalog = find_one(root, CATALOG_NAME, False)
    festival_catalog = find_one(root, FESTIVAL_CATALOG_NAME, False)
    documents, chunks = inspect_database(database)
    return {
        "name": "AI STUDIO 洋子本地专业知识库",
        "root": str(root),
        "database": str(database),
        "catalog": str(catalog) if catalog else "",
        "festival_catalog": str(festival_catalog) if festival_catalog else "",
        "indexed_documents": documents,
        "indexed_chunks": chunks,
        "generated_at": date.today().isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="配置 AI STUDIO 洋子本地专业知识库")
    parser.add_argument("root", help="知识库根目录，或 curated-screenplay-methods.sqlite 文件")
    parser.add_argument("--output", help="knowledge-base.json 输出位置")
    args = parser.parse_args()

    config = build_config(Path(args.root))
    output = Path(args.output).expanduser() if args.output else default_output()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print("本地知识库配置完成")
    print(f"配置文件：{output.resolve()}")
    print(f"知识库根目录：{config['root']}")
    print(f"正式索引文档：{config['indexed_documents']}")
    print(f"全文检索片段：{config['indexed_chunks']}")


if __name__ == "__main__":
    main()
