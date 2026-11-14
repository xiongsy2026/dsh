from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree


SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md", ".docx"}
SKIP_MARKERS = {
    "99-索引与检索规则",
    "90-隔离与待审",
    "待精筛",
    "正版待导入",
    "下载记录",
}
class MissingPdfReader(RuntimeError):
    pass


def load_config(explicit: str | None) -> tuple[Path | None, dict[str, object]]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    environment = os.environ.get("AI_FILM_KNOWLEDGE_CONFIG")
    if environment:
        candidates.append(Path(environment))
    skill_root = Path(__file__).resolve().parent.parent
    candidates.extend(
        [
            Path.cwd() / "00_project" / "knowledge-base.json",
            skill_root / "knowledge-base.json",
            skill_root / "references" / "knowledge-base.json",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate, json.loads(candidate.read_text(encoding="utf-8-sig"))
    return None, {}


def find_bundled_python() -> Path | None:
    home = Path.home()
    candidates = [
        home
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "python"
        / "python.exe",
        home
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "python"
        / "bin"
        / "python3",
        home
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "python"
        / "bin"
        / "python",
    ]
    for candidate in candidates:
        if candidate.is_file() and candidate.resolve() != Path(sys.executable).resolve():
            return candidate
    return None


def has_pdf_reader() -> bool:
    return bool(importlib.util.find_spec("pypdf") or importlib.util.find_spec("pdfplumber"))


def maybe_relaunch_for_pdf(arguments: argparse.Namespace) -> None:
    if arguments.no_relaunch or has_pdf_reader():
        return
    bundled_python = find_bundled_python()
    if not bundled_python:
        return
    command = [str(bundled_python), "-X", "utf8", str(Path(__file__).resolve()), *sys.argv[1:], "--no-relaunch"]
    completed = subprocess.run(command, check=False)
    raise SystemExit(completed.returncode)


def should_skip(path: Path, root: Path, include_isolated: bool) -> bool:
    relative_parts = path.relative_to(root).parts[:-1]
    if any(part.startswith(".") for part in relative_parts):
        return True
    if include_isolated:
        return any(part == "99-索引与检索规则" for part in relative_parts)
    return any(any(marker in part for marker in SKIP_MARKERS) for part in relative_parts)


def scan_documents(root: Path, include_isolated: bool) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if should_skip(path, root, include_isolated):
            continue
        files.append(path)
    return sorted(files, key=lambda item: str(item).casefold())


def relative_key(path_value: str) -> str | None:
    parts = [part for part in re.split(r"[\\/]+", path_value) if part]
    category_index = next(
        (index for index, part in enumerate(parts) if re.match(r"^(0[1-9]|[1-9][0-9])-", part)),
        None,
    )
    if category_index is None:
        return None
    return "/".join(parts[category_index:]).casefold()


def file_signature(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        document = archive.read("word/document.xml")
    root = ElementTree.fromstring(document)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    for paragraph in root.iter(namespace + "p"):
        text = "".join(node.text or "" for node in paragraph.iter(namespace + "t"))
        if text.strip():
            paragraphs.append(text.strip())
    return "\n".join(paragraphs)


def read_pdf(path: Path) -> list[tuple[int, str]]:
    if importlib.util.find_spec("pypdf"):
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return [(index, page.extract_text() or "") for index, page in enumerate(reader.pages, start=1)]
    if importlib.util.find_spec("pdfplumber"):
        import pdfplumber

        with pdfplumber.open(str(path)) as document:
            return [(index, page.extract_text() or "") for index, page in enumerate(document.pages, start=1)]
    raise MissingPdfReader("缺少 PDF 文字解析组件")


def extract_pages(path: Path) -> list[tuple[int, str]]:
    extension = path.suffix.lower()
    if extension == ".pdf":
        return read_pdf(path)
    if extension == ".docx":
        return [(1, read_docx(path))]
    return [(1, read_text_file(path))]


def normalize_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_text(text: str, target: int = 1400, overlap: int = 180) -> list[str]:
    text = normalize_text(text)
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    minimum = max(target // 2, 400)
    while start < len(text):
        end = min(start + target, len(text))
        if end < len(text):
            window = text[start + minimum : end]
            boundaries = [window.rfind(mark) for mark in ("\n", "。", "！", "？", ". ", "! ", "? ")]
            boundary = max(boundaries)
            if boundary >= 0:
                end = start + minimum + boundary + 1
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        next_start = max(end - overlap, start + 1)
        start = next_start
    return chunks


def ensure_schema(connection: sqlite3.Connection) -> str:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS documents ("
        "id INTEGER PRIMARY KEY, path TEXT UNIQUE, name TEXT, extension TEXT, size INTEGER, "
        "modified_ns INTEGER, signature TEXT, pages INTEGER, status TEXT)"
    )
    existing = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='chunks'"
    ).fetchone()
    if existing:
        sql = str(existing[0] or "").casefold()
        return "trigram" if "trigram" in sql else "兼容模式"
    try:
        connection.execute(
            "CREATE VIRTUAL TABLE chunks USING fts5("
            "document_id UNINDEXED, path UNINDEXED, name, page UNINDEXED, "
            "chunk_no UNINDEXED, text, tokenize='trigram')"
        )
        return "trigram"
    except sqlite3.OperationalError:
        connection.execute(
            "CREATE VIRTUAL TABLE chunks USING fts5("
            "document_id UNINDEXED, path UNINDEXED, name, page UNINDEXED, "
            "chunk_no UNINDEXED, text)"
        )
        return "兼容模式"


def load_existing(connection: sqlite3.Connection) -> tuple[dict[str, tuple], dict[str, tuple]]:
    by_path: dict[str, tuple] = {}
    by_relative: dict[str, tuple] = {}
    for row in connection.execute(
        "SELECT id, path, name, extension, size, modified_ns, signature, pages, status FROM documents"
    ):
        by_path[str(row[1]).casefold()] = row
        key = relative_key(str(row[1]))
        if key and key not in by_relative:
            by_relative[key] = row
    return by_path, by_relative


def relocate_document(connection: sqlite3.Connection, document_id: int, path: Path) -> None:
    connection.execute(
        "UPDATE documents SET path=?, name=?, extension=?, size=?, modified_ns=? WHERE id=?",
        (str(path), path.name, path.suffix.lower(), path.stat().st_size, path.stat().st_mtime_ns, document_id),
    )
    connection.execute("UPDATE chunks SET path=?, name=? WHERE document_id=?", (str(path), path.name, document_id))


def upsert_document(
    connection: sqlite3.Connection,
    path: Path,
    existing: tuple | None,
    signature: str,
    pages: list[tuple[int, str]],
    status: str,
) -> tuple[int, int]:
    stat = path.stat()
    if existing:
        document_id = int(existing[0])
        connection.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
        connection.execute(
            "UPDATE documents SET path=?, name=?, extension=?, size=?, modified_ns=?, signature=?, pages=?, status=? "
            "WHERE id=?",
            (
                str(path),
                path.name,
                path.suffix.lower(),
                stat.st_size,
                stat.st_mtime_ns,
                signature,
                len(pages),
                status,
                document_id,
            ),
        )
    else:
        cursor = connection.execute(
            "INSERT INTO documents(path, name, extension, size, modified_ns, signature, pages, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(path),
                path.name,
                path.suffix.lower(),
                stat.st_size,
                stat.st_mtime_ns,
                signature,
                len(pages),
                status,
            ),
        )
        document_id = int(cursor.lastrowid)
    chunk_count = 0
    if status == "indexed":
        for page_number, page_text in pages:
            for chunk_number, text in enumerate(chunk_text(page_text), start=1):
                connection.execute(
                    "INSERT INTO chunks(document_id, path, name, page, chunk_no, text) VALUES (?, ?, ?, ?, ?, ?)",
                    (document_id, str(path), path.name, page_number, chunk_number, text),
                )
                chunk_count += 1
    return document_id, chunk_count


def update_config(config_path: Path | None, config: dict[str, object], root: Path, database: Path) -> None:
    if config_path is None:
        config_path = database.parent / "knowledge-base.json"
    with sqlite3.connect(database) as connection:
        documents = int(connection.execute("SELECT COUNT(*) FROM documents WHERE status='indexed'").fetchone()[0])
        chunks = int(connection.execute("SELECT COUNT(*) FROM chunks_content").fetchone()[0])
    updated = dict(config)
    updated.update(
        {
            "name": updated.get("name") or "AI STUDIO 洋子本地影视知识库",
            "root": str(root),
            "database": str(database),
            "indexed_documents": documents,
            "indexed_chunks": chunks,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")


def write_report(root: Path, summary: dict[str, object], details: Iterable[str]) -> Path:
    report_root = root / "99-索引与检索规则"
    report_root.mkdir(parents=True, exist_ok=True)
    report_path = report_root / f"增量索引报告-{datetime.now():%Y%m%d-%H%M%S-%f}.md"
    lines = [
        "# 本地知识库增量索引报告",
        "",
        f"时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
        f"扫描文件：{summary['scanned']}",
        f"新增或更新：{summary['updated']}",
        f"原路径自动迁移：{summary['relocated']}",
        f"未变化跳过：{summary['unchanged']}",
        f"无文字或失败：{summary['failed']}",
        f"新增片段：{summary['chunks']}",
        f"索引模式：{summary['tokenizer']}",
        "",
        "## 明细",
        "",
        *details,
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description="增量更新 AI STUDIO 洋子本地影视知识库")
    parser.add_argument("root", nargs="?", help="知识库根目录；省略时读取 knowledge-base.json")
    parser.add_argument("--config", help="knowledge-base.json 路径")
    parser.add_argument("--database", help="SQLite 数据库路径")
    parser.add_argument("--include-isolated", action="store_true", help="同时索引待精筛或隔离目录")
    parser.add_argument("--dry-run", action="store_true", help="只扫描，不写数据库")
    parser.add_argument("--no-relaunch", action="store_true", help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    maybe_relaunch_for_pdf(arguments)

    if arguments.root and not arguments.config:
        selected_root = Path(arguments.root).expanduser().resolve()
        selected_config = selected_root / "99-索引与检索规则" / "knowledge-base.json"
        if selected_config.is_file():
            config_path = selected_config
            config = json.loads(selected_config.read_text(encoding="utf-8-sig"))
        else:
            config_path = None
            config = {}
    else:
        config_path, config = load_config(arguments.config)
    root_value = arguments.root or str(config.get("root", "")).strip()
    if not root_value or root_value.startswith("__"):
        raise FileNotFoundError("尚未配置知识库根目录。请先选择知识库文件夹。")
    root = Path(root_value).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"知识库根目录不存在：{root}")
    database_value = arguments.database or str(config.get("database", "")).strip()
    database = (
        Path(database_value).expanduser()
        if database_value and not database_value.startswith("__")
        else root / "99-索引与检索规则" / "curated-screenplay-methods.sqlite"
    )
    database = database.resolve()
    database.parent.mkdir(parents=True, exist_ok=True)

    files = scan_documents(root, arguments.include_isolated)
    summary: dict[str, object] = {
        "scanned": len(files),
        "updated": 0,
        "relocated": 0,
        "unchanged": 0,
        "failed": 0,
        "chunks": 0,
        "tokenizer": "未创建",
    }
    details: list[str] = []
    if arguments.dry_run:
        print(json.dumps({"root": str(root), "database": str(database), "scanned": len(files)}, ensure_ascii=False))
        return

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA synchronous=NORMAL")
        summary["tokenizer"] = ensure_schema(connection)
        by_path, by_relative = load_existing(connection)
        for path in files:
            stat = path.stat()
            exact = by_path.get(str(path).casefold())
            relative = by_relative.get(relative_key(str(path)) or "")
            existing = exact or relative
            if existing and not exact and int(existing[4] or 0) == stat.st_size:
                relocate_document(connection, int(existing[0]), path)
                connection.commit()
                summary["relocated"] = int(summary["relocated"]) + 1
                summary["unchanged"] = int(summary["unchanged"]) + 1
                details.append(f"- 路径迁移：`{path}`")
                continue
            if existing and int(existing[4] or 0) == stat.st_size and int(existing[5] or 0) == stat.st_mtime_ns:
                summary["unchanged"] = int(summary["unchanged"]) + 1
                continue
            try:
                signature = file_signature(path)
                if existing and str(existing[6] or "") == signature and str(existing[1]) == str(path):
                    connection.execute(
                        "UPDATE documents SET modified_ns=?, size=? WHERE id=?",
                        (stat.st_mtime_ns, stat.st_size, int(existing[0])),
                    )
                    connection.commit()
                    summary["unchanged"] = int(summary["unchanged"]) + 1
                    continue
                pages = extract_pages(path)
                has_text = any(normalize_text(text) for _, text in pages)
                status = "indexed" if has_text else "no_text"
                _, chunks = upsert_document(connection, path, existing, signature, pages, status)
                connection.commit()
                if status == "indexed":
                    summary["updated"] = int(summary["updated"]) + 1
                    summary["chunks"] = int(summary["chunks"]) + chunks
                    details.append(f"- 已索引：`{path}`（{chunks} 个片段）")
                else:
                    summary["failed"] = int(summary["failed"]) + 1
                    details.append(f"- 未提取到文字，可能需要 OCR：`{path}`")
            except MissingPdfReader:
                summary["failed"] = int(summary["failed"]) + 1
                details.append(f"- 缺少 PDF 解析组件，暂未索引：`{path}`")
            except Exception as error:
                connection.rollback()
                summary["failed"] = int(summary["failed"]) + 1
                details.append(f"- 失败：`{path}`｜{type(error).__name__}: {error}")

    update_config(config_path, config, root, database)
    report = write_report(root, summary, details or ["- 没有需要更新的文件。"])
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print("本地知识库更新完成")
    print(f"扫描文件：{summary['scanned']}")
    print(f"新增或更新：{summary['updated']}")
    print(f"原路径自动迁移：{summary['relocated']}")
    print(f"未变化跳过：{summary['unchanged']}")
    print(f"无文字或失败：{summary['failed']}")
    print(f"新增片段：{summary['chunks']}")
    print(f"报告：{report}")
    if int(summary["failed"]) and not has_pdf_reader() and not find_bundled_python():
        print("部分 PDF 未索引：当前电脑缺少 pypdf。可安装后再次运行：python -m pip install pypdf")


if __name__ == "__main__":
    main()
