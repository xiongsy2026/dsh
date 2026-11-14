"""
爆款复刻工作流 - SQLite 历史记录数据库
"""
import json
import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).resolve().parent / "outputs" / "replica.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化数据库表"""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workflow_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            run_dir TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            video_path TEXT,
            storyboard_path TEXT,
            poster_path TEXT,
            shot_count INTEGER DEFAULT 0,
            error TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prompt_reverse_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def save_result(task_id: str, run_dir: str = "", status: str = "completed",
                video_path: str = "", storyboard_path: str = "", poster_path: str = "",
                shot_count: int = 0, error: str = ""):
    """保存工作流结果（按 task_id upsert：存在则更新，不存在则插入）"""
    init_db()
    conn = _get_conn()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing = conn.execute(
        "SELECT id FROM workflow_history WHERE task_id = ?", (task_id,)
    ).fetchone()
    if existing:
        conn.execute(
            """UPDATE workflow_history SET
               run_dir = ?, status = ?, video_path = ?, storyboard_path = ?,
               poster_path = ?, shot_count = ?, error = ?, completed_at = ?
               WHERE task_id = ?""",
            (run_dir, status, video_path, storyboard_path, poster_path, shot_count, error, now if status != "running" else None, task_id),
        )
    else:
        conn.execute(
            """INSERT INTO workflow_history
               (task_id, run_dir, status, video_path, storyboard_path, poster_path, shot_count, error, created_at, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, run_dir, status, video_path, storyboard_path, poster_path, shot_count, error, now, now if status != "running" else None),
        )
    conn.commit()
    conn.close()


def get_history(limit: int = 50):
    """获取历史记录列表"""
    init_db()
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM workflow_history ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]



def save_prompt_reverse_history(batch_id: str, links: list, video_paths: list, items: list, status: str = "done"):
    init_db()
    conn = _get_conn()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = json.dumps({"links": links, "video_paths": video_paths, "items": items}, ensure_ascii=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prompt_reverse_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT UNIQUE NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        INSERT INTO prompt_reverse_history(batch_id,status,payload,created_at,updated_at)
        VALUES(?,?,?,?,?) ON CONFLICT(batch_id) DO UPDATE SET status=excluded.status,payload=excluded.payload,updated_at=excluded.updated_at
    """, (batch_id, status, payload, now, now))
    conn.commit()
    conn.close()


def migrate_prompt_reverse_history(base_dir: str):
    init_db()
    root = Path(base_dir)
    if not root.exists():
        return
    conn = _get_conn()
    for batch_dir in root.iterdir():
        if not batch_dir.is_dir():
            continue
        exists = conn.execute("SELECT 1 FROM prompt_reverse_history WHERE batch_id = ?", (batch_dir.name,)).fetchone()
        if exists:
            continue
        items = []
        for item_dir in sorted(batch_dir.glob("item_*")):
            raw = next(iter(item_dir.glob("reverse_result.json")), None)
            data = json.loads(raw.read_text(encoding="utf-8")) if raw else {}
            txt = next(iter(item_dir.glob("*.txt")), None)
            index_text = item_dir.name.replace("item_", "")
            index = int(index_text) if index_text.isdigit() else len(items)
            items.append({"index": index, "status": "done" if txt else "failed", "stage": "历史任务", "product_category": txt.stem.split("_", 1)[-1] if txt else "", "general_prompt": txt.read_text(encoding="utf-8") if txt else "", "prompt_file": str(txt) if txt else "", "prompt_url": f"/outputs/batch_prompts/{batch_dir.name}/{item_dir.name}/{txt.name}" if txt else "", "reverse_result": data, "voiceover_segments": data.get("voiceover_segments", []), "voiceover_text": data.get("voiceover_text", ""), "frames": data.get("frames", [])})
        if items:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            payload = json.dumps({"links": [], "video_paths": [], "items": items}, ensure_ascii=False)
            conn.execute("INSERT OR IGNORE INTO prompt_reverse_history(batch_id,status,payload,created_at,updated_at) VALUES(?,?,?,?,?)", (batch_dir.name, "done", payload, now, now))
    conn.commit(); conn.close()


def list_prompt_reverse_history(limit: int = 50):
    init_db()
    conn = _get_conn()
    conn.execute("CREATE TABLE IF NOT EXISTS prompt_reverse_history (id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT UNIQUE NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    rows = conn.execute("SELECT id,batch_id,status,created_at,updated_at,payload FROM prompt_reverse_history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.commit(); conn.close()
    result = []
    for row in rows:
        data = json.loads(row["payload"])
        items = data.get("items", [])
        result.append({"id": row["id"], "batch_id": row["batch_id"], "status": row["status"], "created_at": row["created_at"], "updated_at": row["updated_at"], "total": len(items), "items": [{"index": i.get("index", n), "product_category": i.get("product_category", ""), "status": i.get("status", "") } for n, i in enumerate(items)]})
    return result


def update_prompt_reverse_item(batch_id: str, index: int, updates: dict):
    history = get_prompt_reverse_history(batch_id)
    if not history:
        return False
    items = history.get("items", [])
    for item in items:
        if item.get("index") == index:
            item.update(updates)
            break
    else:
        return False
    save_prompt_reverse_history(batch_id, history.get("links", []), history.get("video_paths", []), items, history.get("status", "done"))
    return True


def get_prompt_reverse_history(batch_id: str):
    init_db()
    conn = _get_conn()
    row = conn.execute("SELECT * FROM prompt_reverse_history WHERE batch_id = ?", (batch_id,)).fetchone()
    conn.close()
    if not row:
        return None
    data = json.loads(row["payload"])
    data.update({"id": row["id"], "batch_id": row["batch_id"], "status": row["status"], "created_at": row["created_at"], "updated_at": row["updated_at"]})
    return data


def delete_record(record_id: int):
    """删除一条记录"""
    init_db()
    conn = _get_conn()
    conn.execute("DELETE FROM workflow_history WHERE id = ?", (record_id,))
    conn.commit()
    conn.close()
