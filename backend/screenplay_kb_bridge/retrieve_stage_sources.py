from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_config(explicit: str | None) -> tuple[Path, dict[str, Any]]:
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
        if candidate.exists():
            return candidate, json.loads(candidate.read_text(encoding="utf-8"))
    raise FileNotFoundError("没有找到 knowledge-base.json。请先选择知识库文件夹并运行配置脚本。")


def build_match_query(text: str, mode: str) -> str:
    terms = [term.strip() for term in text.split() if term.strip()]
    if not terms:
        raise ValueError("检索词不能为空。")
    if len(terms) == 1:
        return terms[0]
    operator = " AND " if mode == "all" else " OR "
    escaped = [term.replace('"', '""') for term in terms]
    return operator.join(f'"{term}"' for term in escaped)


def split_terms(text: str) -> list[str]:
    terms = [term.strip() for term in text.split() if term.strip()]
    if not terms:
        raise ValueError("检索词不能为空。")
    return terms


def supports_trigram(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='chunks'"
    ).fetchone()
    if not row or "trigram" not in str(row[0] or "").casefold():
        return False
    try:
        connection.execute(
            "SELECT rowid FROM chunks WHERE chunks MATCH ? LIMIT 0",
            ("runtimeprobe",),
        ).fetchall()
    except sqlite3.OperationalError:
        return False
    return True


def normalize_filters(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def make_snippet(text: str, terms: list[str], limit: int = 240) -> str:
    compact = " ".join(str(text).split())
    lowered = compact.casefold()
    positions = [lowered.find(term.casefold()) for term in terms]
    positions = [position for position in positions if position >= 0]
    start = max((min(positions) if positions else 0) - 60, 0)
    snippet = compact[start : start + limit]
    if start > 0:
        snippet = "…" + snippet
    if start + limit < len(compact):
        snippet += "…"
    return snippet


def search_query(
    connection: sqlite3.Connection,
    text: str,
    path_contains: str | list[str] | None,
    path_excludes: str | list[str] | None,
    allowed_paths: set[str] | None,
    mode: str,
    limit: int,
    fts_available: bool,
) -> list[tuple[Any, ...]]:
    terms = split_terms(text)
    use_like = any(len(term) < 3 for term in terms) or not fts_available
    if use_like:
        operator = " AND " if mode == "all" else " OR "
        patterns = [f"%{term}%" for term in terms]
        predicate = "(" + operator.join("(raw.c5 LIKE ? OR documents.name LIKE ?)" for _ in terms) + ")"
        text_scores = " + ".join("CASE WHEN raw.c5 LIKE ? THEN 10 ELSE 0 END" for _ in terms)
        title_scores = " + ".join("CASE WHEN documents.name LIKE ? THEN 30 ELSE 0 END" for _ in terms)
        score_expression = f"-(({text_scores}) + ({title_scores}))"
        parameters: list[Any] = patterns + patterns
        for pattern in patterns:
            parameters.extend((pattern, pattern))
        sql = (
            "SELECT documents.name, documents.path, raw.c3, raw.c4, raw.c5, "
            f"{score_expression} AS rank_score "
            "FROM chunks_content AS raw JOIN documents ON documents.id = raw.c0 "
            f"WHERE {predicate}"
        )
    else:
        predicate = "chunks MATCH ?"
        parameters = [build_match_query(text, mode)]
        score_expression = "bm25(chunks, 0.0, 0.0, 8.0, 0.0, 0.0, 1.0)"
        sql = (
            "SELECT chunks.name, documents.path, chunks.page, chunks.chunk_no, "
            f"snippet(chunks, 5, '[', ']', '…', 30), {score_expression} AS rank_score "
            "FROM chunks JOIN documents ON documents.id = chunks.document_id "
            f"WHERE {predicate}"
        )
    include_paths = normalize_filters(path_contains)
    if include_paths:
        sql += " AND (" + " OR ".join("documents.path LIKE ?" for _ in include_paths) + ")"
        parameters.extend(f"%{item}%" for item in include_paths)
    excluded_paths = normalize_filters(path_excludes)
    for excluded_path in excluded_paths:
        sql += " AND documents.path NOT LIKE ?"
        parameters.append(f"%{excluded_path}%")
    if allowed_paths is not None:
        if not allowed_paths:
            return []
        placeholders = ",".join("?" for _ in allowed_paths)
        sql += f" AND documents.path IN ({placeholders})"
        parameters.extend(sorted(allowed_paths))
    sql += " ORDER BY rank_score ASC, documents.name ASC"
    sql += " LIMIT ?"
    parameters.append(limit)
    rows = connection.execute(sql, parameters).fetchall()
    if use_like:
        title_predicate = "(" + operator.join("documents.name LIKE ?" for _ in terms) + ")"
        title_score = " + ".join("CASE WHEN documents.name LIKE ? THEN 30 ELSE 0 END" for _ in terms)
        title_sql = (
            "SELECT documents.name, documents.path, '未提取', 0, '', "
            f"-({title_score}) AS rank_score FROM documents "
            f"WHERE {title_predicate} "
            "AND NOT EXISTS (SELECT 1 FROM chunks_content AS title_raw "
            "WHERE title_raw.c0 = documents.id LIMIT 1)"
        )
        title_parameters: list[Any] = patterns + patterns
        if include_paths:
            title_sql += " AND (" + " OR ".join("documents.path LIKE ?" for _ in include_paths) + ")"
            title_parameters.extend(f"%{item}%" for item in include_paths)
        for excluded_path in excluded_paths:
            title_sql += " AND documents.path NOT LIKE ?"
            title_parameters.append(f"%{excluded_path}%")
        if allowed_paths is not None:
            placeholders = ",".join("?" for _ in allowed_paths)
            title_sql += f" AND documents.path IN ({placeholders})"
            title_parameters.extend(sorted(allowed_paths))
        title_sql += " ORDER BY rank_score ASC, documents.name ASC LIMIT ?"
        title_parameters.append(limit)
        rows = sorted(
            list(rows) + connection.execute(title_sql, title_parameters).fetchall(),
            key=lambda row: (float(row[5]), str(row[0])),
        )[:limit]
        results: list[tuple[Any, ...]] = []
        for row in rows:
            raw_text = str(row[4] or "").strip()
            page = row[2] if raw_text else "未提取"
            snippet = (
                make_snippet(raw_text, terms)
                if raw_text
                else "仅文件名命中；正文没有可检索文字，需要先做 OCR 或换用文字版。"
            )
            results.append((row[0], row[1], page, row[3], snippet, row[5]))
        return results
    return rows


def load_festival_paths(config: dict[str, Any], festival: str | None, database: Path) -> set[str] | None:
    if not festival:
        return None
    catalog = Path(str(config.get("festival_catalog", database.parent / "电影节剧本标签索引.csv")))
    if not catalog.exists():
        return set()
    paths: set[str] = set()
    with catalog.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("电影节") == festival and row.get("本地完整路径"):
                paths.add(str(row["本地完整路径"]))
    return paths


def resolve_source_path(stored_path: str, config: dict[str, Any]) -> tuple[str, bool]:
    original = Path(stored_path).expanduser()
    if original.is_file():
        return str(original), True

    root_value = str(config.get("root", "")).strip()
    if not root_value or root_value.startswith("__"):
        return stored_path, False
    root = Path(root_value).expanduser()
    parts = [part for part in re.split(r"[\\/]+", stored_path) if part]
    category_index = next(
        (index for index, part in enumerate(parts) if re.match(r"^(0[1-9]|[1-9][0-9])-", part)),
        None,
    )
    if category_index is None:
        return stored_path, False
    candidate = root.joinpath(*parts[category_index:])
    return str(candidate), candidate.is_file()


def resolve_selected_document_paths(
    connection: sqlite3.Connection,
    config: dict[str, Any],
    selections: list[str],
) -> set[str]:
    rows = connection.execute("SELECT path, name FROM documents").fetchall()
    aliases: list[tuple[str, set[str]]] = []
    for stored_path, name in rows:
        resolved_path, _ = resolve_source_path(str(stored_path), config)
        values = {
            str(stored_path).replace("\\", "/").casefold(),
            str(resolved_path).replace("\\", "/").casefold(),
            str(name).casefold(),
        }
        aliases.append((str(stored_path), values))

    matched: set[str] = set()
    missing: list[str] = []
    for selection in selections:
        normalized = selection.replace("\\", "/").casefold()
        hits = [stored_path for stored_path, values in aliases if normalized in values]
        if not hits:
            missing.append(selection)
            continue
        matched.update(hits)
    if missing:
        raise ValueError(f"选定参考未进入索引或路径已失效：{'；'.join(missing)}")
    return matched


def retrieve(
    database: Path,
    config: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    queries = profile.get("queries")
    if not isinstance(queries, list) or not queries:
        raise ValueError("检索画像必须包含非空 queries 数组。")

    candidates: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    connection = sqlite3.connect(database)
    try:
        fts_available = supports_trigram(connection)
        selected_documents = normalize_filters(profile.get("selected_documents"))
        selected_paths = (
            resolve_selected_document_paths(connection, config, selected_documents)
            if selected_documents
            else None
        )
        deep_read_selected = bool(profile.get("deep_read_selected", False))
        if deep_read_selected and not selected_paths:
            raise ValueError("定向精读必须先提供用户已确认的 selected_documents。")
        for query in queries:
            lane = str(query.get("lane", "reference"))
            text = str(query.get("text", "")).strip()
            path_contains = query.get("path_contains")
            path_excludes = normalize_filters(query.get("path_excludes"))
            if selected_paths is None and (lane == "method" or "方法" in lane) and not bool(query.get("include_journals", False)):
                path_excludes.append("影视研究期刊")
            festival = str(query.get("festival", "")).strip() or None
            if selected_paths is not None:
                allowed_paths = selected_paths
                path_contains = None
                path_excludes = []
            else:
                allowed_paths = load_festival_paths(config, festival, database)
            mode = str(query.get("mode", "any"))
            weight = float(query.get("weight", 1.0))
            default_candidates = 1000 if deep_read_selected else 240
            maximum_candidates = 5000 if deep_read_selected else 1000
            candidate_chunks = min(
                max(int(query.get("candidate_chunks", default_candidates)), 30),
                maximum_candidates,
            )
            rows = search_query(
                connection,
                text,
                path_contains,
                path_excludes,
                allowed_paths,
                mode,
                candidate_chunks,
                fts_available,
            )
            seen_paths: set[str] = set()
            document_rank = 0
            for row in rows:
                stored_path = str(row[1])
                if stored_path in seen_paths:
                    continue
                seen_paths.add(stored_path)
                document_rank += 1
                fused_score = weight / (60.0 + document_rank)
                source_path, source_exists = resolve_source_path(stored_path, config)
                item = candidates[lane].setdefault(
                    stored_path,
                    {
                        "name": row[0],
                        "path": source_path,
                        "stored_path": stored_path,
                        "source_exists": source_exists,
                        "page": row[2],
                        "chunk": row[3],
                        "snippet": row[4],
                        "score": 0.0,
                        "queries": [],
                        "evidence": [],
                    },
                )
                item["score"] += fused_score
                item["queries"].append(text)
                evidence = {
                    "query": text,
                    "page": row[2],
                    "chunk": row[3],
                    "snippet": row[4],
                }
                if evidence not in item["evidence"]:
                    item["evidence"].append(evidence)
                if float(row[5]) < float(item.get("bm25", 0.0)):
                    item["bm25"] = row[5]
                    item["page"] = row[2]
                    item["chunk"] = row[3]
                    item["snippet"] = row[4]
    finally:
        connection.close()

    lane_limits = profile.get("lane_limits", {})
    output: dict[str, list[dict[str, Any]]] = {}
    for lane, lane_candidates in candidates.items():
        limit = int(lane_limits.get(lane, 5))
        ranked = sorted(lane_candidates.values(), key=lambda item: item["score"], reverse=True)
        for item in ranked:
            item["score"] = round(float(item["score"]), 6)
            item["queries"] = list(dict.fromkeys(item["queries"]))
        output[lane] = ranked[:limit]
    return output


def write_card(path: Path, profile: dict[str, Any], results: dict[str, list[dict[str, Any]]]) -> None:
    selected_documents = normalize_filters(profile.get("selected_documents"))
    card_name = "精读证据卡" if selected_documents else "候选卡"
    lines = [
        f"# 本地知识桥{card_name}｜{profile.get('stage', '未指定阶段')}",
        "",
        f"检索模式：{'已确认参考定向精读' if profile.get('selected_documents') else '影视资料库候选匹配'}",
        f"本轮问题：{profile.get('problem', '未填写')}",
        "状态：待模型复排与用户确认；不得直接当成创作成果",
        "",
    ]
    if selected_documents:
        lines.extend([f"限定文件：{'；'.join(selected_documents)}", ""])
    for lane, items in results.items():
        lines.extend([f"## {lane}", ""])
        if not items:
            lines.extend(["资料不足：该通道没有命中。", ""])
            continue
        for index, item in enumerate(items, start=1):
            lines.extend(
                [
                    f"### {index}. {item['name']}",
                    f"- 融合分：{item['score']}",
                    f"- 页码：{item['page']}",
                    f"- 路径：`{item['path']}`",
                    f"- 原文状态：{'可打开' if item.get('source_exists') else '未找到原文件；检索片段仍来自 SQLite'}",
                    f"- 命中查询：{'；'.join(item['queries'])}",
                    f"- 短片段：{item['snippet']}",
                    "- 匹配理由：待模型结合当前项目复排。",
                    "- 可迁移结论：待提炼；禁止复制具体人物、剧情链、独创设定和长段对白。",
                ]
            )
            evidence = item.get("evidence", [])
            if evidence:
                lines.append("- 定向证据：")
                for detail in evidence[:8]:
                    lines.append(
                        f"  - 查询“{detail['query']}”｜页码 {detail['page']}｜片段 {detail['chunk']}：{detail['snippet']}"
                    )
            lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--config")
    parser.add_argument("--output")
    args = parser.parse_args()

    profile_path = Path(args.profile)
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    config_path, config = load_config(args.config)
    database_value = str(config.get("database", "")).strip()
    if not database_value or database_value.startswith("__"):
        raise FileNotFoundError("本地知识库尚未配置。请先选择知识库文件夹并运行配置脚本。")
    database = Path(database_value).expanduser()
    if not database.exists():
        raise FileNotFoundError(f"SQLite 索引不存在：{database}")
    results = retrieve(database, config, profile)
    if args.output:
        write_card(Path(args.output), profile, results)
    payload = {
        "profile": str(profile_path),
        "config": str(config_path),
        "database": str(database),
        "results": results,
    }
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
