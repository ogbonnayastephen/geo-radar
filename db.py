"""
GEO Radar — SQLite persistence layer.

Stores audit runs so citation rates can be tracked over time.
The DB file lives next to app.py (geo_radar.db).
"""

import json
import sqlite3
from datetime import datetime, timezone

DB_PATH = "geo_radar.db"


def init() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                org_name    TEXT    NOT NULL,
                created_at  TEXT    NOT NULL,
                query_count INTEGER NOT NULL,
                cited_count INTEGER NOT NULL,
                synthesis   TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS query_results (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id           INTEGER NOT NULL REFERENCES runs(id),
                query            TEXT    NOT NULL,
                page_url         TEXT,
                perplexity_cited INTEGER,
                chatgpt_cited    INTEGER,
                readiness_score  INTEGER,
                verdict          TEXT,
                gaps             TEXT
            )
        """)


def save_run(org_name: str, results: list[dict], synthesis: dict | None = None) -> int:
    cited_count = sum(
        1 for r in results
        if r.get("perplexity_cited") or r.get("chatgpt_cited")
    )
    synthesis_json = json.dumps(synthesis) if synthesis else None

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """INSERT INTO runs (org_name, created_at, query_count, cited_count, synthesis)
               VALUES (?, ?, ?, ?, ?)""",
            (
                org_name,
                datetime.now(timezone.utc).isoformat(),
                len(results),
                cited_count,
                synthesis_json,
            ),
        )
        run_id = cur.lastrowid
        for r in results:
            conn.execute(
                """INSERT INTO query_results
                   (run_id, query, page_url, perplexity_cited, chatgpt_cited,
                    readiness_score, verdict, gaps)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    r.get("query", ""),
                    r.get("page_url", ""),
                    int(bool(r.get("perplexity_cited"))),
                    int(bool(r.get("chatgpt_cited"))),
                    r.get("readiness_score"),
                    r.get("verdict", ""),
                    json.dumps(r.get("gaps") or []),
                ),
            )
        return run_id


def get_history(org_name: str, limit: int = 10) -> list[dict]:
    """Return the last `limit` runs for an org, newest first."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT id, created_at, query_count, cited_count, synthesis
               FROM runs
               WHERE org_name = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (org_name, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_run_queries(run_id: int) -> list[dict]:
    """Return all query rows for a specific run."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM query_results WHERE run_id = ?", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]
