"""钩子知识库检索服务

SQLite WHERE 粗筛 + chromadb 向量语义检索混合方案。

入口函数：
- retrieve(user_input: dict) -> dict
  完整执行 Step 2 粗筛 + Step 3 向量检索，返回 top-5 候选。

依赖：
- SQLite（hooks.db 在 backend/hooks_db/）
- chromadb（向量索引在 backend/hooks_db/chroma/）
- sentence-transformers（默认 bge-small-zh-v1.5）
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

# 项目根目录（与 config.py 同级）
BASE_DIR = Path(__file__).resolve().parents[2]
HOOKS_DB_PATH = BASE_DIR / "backend" / "hooks_db" / "hooks.db"
CHROMA_PATH = BASE_DIR / "backend" / "hooks_db" / "chroma"
COLLECTION_NAME = "blockbuster_hooks"
EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"

# 短剧带货维度 → 好莱坞大片标签映射（详见 SKILL references/matching-rules.md）
CATEGORY_MAP = {
    "美妆": ["美妆", "通用"],
    "服饰": ["服饰", "通用"],
    "食品": ["食品", "通用"],
    "数码": ["数码", "通用"],
    "母婴": ["母婴", "通用"],
    "家居": ["家居", "通用"],
    "日用": ["日用", "通用"],
    "通用": ["通用"],
}

AUDIENCE_MAP = {
    "都市女白领": ["都市女白领", "通用"],
    "学生": ["学生", "通用"],
    "宝妈": ["宝妈", "通用"],
    "中年男": ["中年男", "通用"],
    "银发族": ["银发族", "通用"],
    "通用": ["通用"],
}

SCENE_MAP = {
    "公司": ["公司", "通用"],
    "通勤": ["通勤", "通用"],
    "家居": ["家居", "通用"],
    "户外": ["户外", "通用"],
    "健身": ["健身", "通用"],
    "商场": ["商场", "通用"],
    "通用": ["通用"],
}

# 情绪基调映射：用户输入 → 大片标签（取两个相近）
TONE_MAP = {
    "紧张": ["紧张", "悬疑"],
    "温馨": ["温馨", "治愈"],
    "震撼": ["震撼", "冲击"],
    "好奇": ["好奇", "悬疑"],
    "冲击": ["冲击", "震撼"],
    "治愈": ["治愈", "温馨"],
    "反差": ["反差", "好奇"],
    "悬疑": ["悬疑", "好奇"],
}


def _build_like_clause(tags: list[str]) -> str:
    """构造 LIKE OR 子句。tags 已为安全短词。"""
    parts = " OR ".join(
        [f"product_category_fit LIKE '%{t}%'" for t in tags]
    )
    return f"({parts})"


def rough_filter(user_input: dict, limit: int = 30) -> list[dict]:
    """Step 2 SKILL 规则粗筛：纯 SQLite WHERE 过滤。

    Args:
        user_input: 包含 product_category/target_audience/scene_type/emotional_tone 的字典
        limit: 返回候选数量上限

    Returns:
        候选钩子列表，每条含 id/title/hook_pattern/opening_structure/first_frame/first_3s/标签字段
    """
    product = user_input.get("product_category", "通用")
    audience = user_input.get("target_audience", "通用")
    scene = user_input.get("scene_type", "通用")
    tone = user_input.get("emotional_tone", "好奇")

    cat_tags = CATEGORY_MAP.get(product, ["通用"])
    aud_tags = AUDIENCE_MAP.get(audience, ["通用"])
    scn_tags = SCENE_MAP.get(scene, ["通用"])
    tone_tags = TONE_MAP.get(tone, ["好奇", "悬疑"])

    cat_clause = " OR ".join([f"product_category_fit LIKE '%{t}%'" for t in cat_tags])
    aud_clause = " OR ".join([f"audience_fit LIKE '%{t}%'" for t in aud_tags])
    scn_clause = " OR ".join([f"scene_type_fit LIKE '%{t}%'" for t in scn_tags])
    tone_clause = " OR ".join([f"emotional_tone LIKE '%{t}%'" for t in tone_tags])

    sql = f"""
    SELECT id, title, hook_pattern, opening_structure, first_frame, first_3s,
           product_category_fit, audience_fit, scene_type_fit, emotional_tone,
           prompt_template
    FROM hooks
    WHERE ({cat_clause}) AND ({aud_clause}) AND ({scn_clause}) AND ({tone_clause})
    ORDER BY
        CASE WHEN product_category_fit LIKE '%{product}%' THEN 0 ELSE 1 END,
        CASE WHEN audience_fit LIKE '%{audience}%' THEN 0 ELSE 1 END,
        CASE WHEN scene_type_fit LIKE '%{scene}%' THEN 0 ELSE 1 END
    LIMIT {limit}
    """

    conn = sqlite3.connect(HOOKS_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _build_query_text(user_input: dict) -> str:
    """拼接用户完整需求为语义查询字符串。"""
    return (
        f"为{user_input.get('product_category', '通用')}产品设计前"
        f"{user_input.get('duration', 3)}秒{user_input.get('platform', '抖音')}短视频开场钩子。"
        f"目标人群{user_input.get('target_audience', '通用')}，"
        f"场景在{user_input.get('scene_type', '通用')}，"
        f"情绪基调{user_input.get('emotional_tone', '好奇')}。"
        f"{user_input.get('supplement', '')}"
    )


def vector_search(query_text: str, k: int = 5) -> list[dict]:
    """Step 3 向量语义检索：chromadb top-k。"""
    try:
        import chromadb
        from chromadb.utils import embedding_functions
    except ImportError as e:
        raise RuntimeError(
            "chromadb + sentence-transformers 未安装。"
            "请运行: pip install chromadb sentence-transformers"
        ) from e

    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL
    )
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )

    query_embedding = ef([query_text])
    results = collection.query(
        query_embeddings=query_embedding,
        n_results=k,
        include=["documents", "metadatas", "distances"]
    )

    candidates = []
    for i, doc_id in enumerate(results["ids"][0]):
        meta = results["metadatas"][0][i]
        dist = results["distances"][0][i]
        candidates.append({
            "id": doc_id,
            "title": meta.get("title", ""),
            "hook_pattern": meta.get("hook_pattern", ""),
            "opening_structure": meta.get("opening_structure", ""),
            "first_frame": meta.get("first_frame", ""),
            "first_3s": meta.get("first_3s", ""),
            "prompt_template": meta.get("prompt_template", ""),
            "distance": float(dist),
            "similarity": 1.0 - float(dist),
        })
    return candidates


def retrieve(user_input: dict, k: int = 5) -> dict:
    """完整执行 Step 2 + Step 3 检索管线。

    Args:
        user_input: 用户输入字典（4 维度必填 + 可选字段）
        k: 向量检索 top-k 数量

    Returns:
        {
            "user_input": {...},
            "rough_candidates": [...],   # Step 2 粗筛结果
            "vector_candidates": [...],  # Step 3 向量检索结果
            "merged": [...]              # 合并去重后的 top-k 候选
        }
    """
    # Step 2 粗筛
    rough = rough_filter(user_input, limit=30)

    # 若粗筛不足 5 条，降级（放 scene_type 为通用）
    if len(rough) < 5:
        degraded_input = dict(user_input)
        degraded_input["scene_type"] = "通用"
        rough = rough_filter(degraded_input, limit=30)

    # Step 3 向量检索
    query_text = _build_query_text(user_input)
    vector = vector_search(query_text, k=k)

    # 合并去重（按 id）
    merged_map: dict[str, dict] = {}
    for c in rough:
        merged_map[c["id"]] = {**c, "source": "rough"}
    for c in vector:
        if c["id"] in merged_map:
            merged_map[c["id"]].update({
                "distance": c["distance"],
                "similarity": c["similarity"],
                "source": "both"
            })
        else:
            merged_map[c["id"]] = {**c, "source": "vector"}

    # 按相似度排序（向量检索命中的优先）
    merged = sorted(
        merged_map.values(),
        key=lambda x: x.get("similarity", 0.0),
        reverse=True
    )[:k]

    return {
        "user_input": user_input,
        "rough_candidates": rough,
        "vector_candidates": vector,
        "merged": merged,
    }


def get_hook_by_id(hook_id: str) -> dict | None:
    """按 id 查询单条钩子（用于 LLM 精排时补充字段）。"""
    conn = sqlite3.connect(HOOKS_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM hooks WHERE id = ?", (hook_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


if __name__ == "__main__":
    # 命令行测试
    test_input = {
        "product_category": "美妆",
        "target_audience": "都市女白领",
        "scene_type": "公司",
        "emotional_tone": "紧张",
        "platform": "抖音",
        "duration": 3,
        "supplement": "突显加班熬夜的眼下细纹痛点",
    }
    result = retrieve(test_input, k=5)
    print(json.dumps(result, ensure_ascii=False, indent=2))
