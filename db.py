"""
GEO Radar — persistence layer.

Uses Supabase (Postgres) when SUPABASE_URL + SUPABASE_KEY are set in the
environment; falls back to local SQLite otherwise so local dev works without
a Supabase project.

Public API is backwards-compatible with the original SQLite-only version.
New additions:
  save_run()        — now accepts user_id; mutates result dicts to add query_result_id
  save_fix_attempt()— persists a proof-of-fix before/after comparison
  get_fix_attempts()— retrieves fix history for a specific query result
  get_user_orgs()   — returns distinct org names for the agency client switcher
"""

import json
import os
import sqlite3
from datetime import datetime, timezone

_SUPABASE_URL = os.getenv("SUPABASE_URL")
_SUPABASE_KEY = os.getenv("SUPABASE_KEY")
_USE_SUPABASE = bool(_SUPABASE_URL and _SUPABASE_KEY)

DB_PATH = "geo_radar.db"


def _sb():
    from supabase import create_client
    return create_client(_SUPABASE_URL, _SUPABASE_KEY)


# ---------------------------------------------------------------------------
# INIT — creates SQLite tables; Supabase tables are created via Dashboard SQL
# ---------------------------------------------------------------------------
def init() -> None:
    if _USE_SUPABASE:
        return
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT,
                org_name    TEXT    NOT NULL,
                created_at  TEXT    NOT NULL,
                query_count INTEGER NOT NULL,
                cited_count INTEGER NOT NULL,
                synthesis   TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS query_results (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id             INTEGER NOT NULL REFERENCES runs(id),
                query              TEXT    NOT NULL,
                page_url           TEXT,
                perplexity_cited   INTEGER,
                chatgpt_cited      INTEGER,
                google_cited       INTEGER,
                readiness_score    INTEGER,
                verdict            TEXT,
                gaps               TEXT,
                rewritten_section  TEXT,
                suggested_headings TEXT,
                faq_schema         TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fix_attempts (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                query_result_id   INTEGER NOT NULL REFERENCES query_results(id),
                before_score      INTEGER,
                after_score       INTEGER,
                before_perplexity INTEGER,
                after_perplexity  INTEGER,
                before_chatgpt    INTEGER,
                after_chatgpt     INTEGER,
                before_google     INTEGER,
                after_google      INTEGER,
                checked_at        TEXT    NOT NULL
            )
        """)


# ---------------------------------------------------------------------------
# SAVE RUN
# Side-effect: mutates each result dict to add "query_result_id" so the UI
# can offer a per-query re-check button tied to the persisted row.
# ---------------------------------------------------------------------------
def save_run(
    org_name: str,
    results: list[dict],
    synthesis: dict | None = None,
    user_id: str | None = None,
) -> int:
    cited_count = sum(
        1 for r in results
        if r.get("perplexity_cited") or r.get("chatgpt_cited") or r.get("google_cited")
    )

    if _USE_SUPABASE:
        sb = _sb()
        run_row = {
            "org_name":    org_name,
            "query_count": len(results),
            "cited_count": cited_count,
            "synthesis":   synthesis,
        }
        if user_id:
            run_row["user_id"] = user_id

        run_res = sb.table("runs").insert(run_row).execute()
        run_id  = run_res.data[0]["id"]

        for r in results:
            qr_row = {
                "run_id":             run_id,
                "query":              r.get("query", ""),
                "page_url":           r.get("page_url", ""),
                "perplexity_cited":   r.get("perplexity_cited"),
                "chatgpt_cited":      r.get("chatgpt_cited"),
                "google_cited":       r.get("google_cited"),
                "readiness_score":    r.get("readiness_score"),
                "verdict":            r.get("verdict", ""),
                "gaps":               r.get("gaps") or [],
                "rewritten_section":  r.get("rewritten_section", ""),
                "suggested_headings": r.get("suggested_headings") or [],
                "faq_schema":         r.get("faq_schema", ""),
            }
            qr_res = sb.table("query_results").insert(qr_row).execute()
            if qr_res.data:
                r["query_result_id"] = qr_res.data[0]["id"]
        return run_id

    else:
        synthesis_json = json.dumps(synthesis) if synthesis else None
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.execute(
                """INSERT INTO runs
                   (user_id, org_name, created_at, query_count, cited_count, synthesis)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    org_name,
                    datetime.now(timezone.utc).isoformat(),
                    len(results),
                    cited_count,
                    synthesis_json,
                ),
            )
            run_id = cur.lastrowid
            for r in results:
                qr = conn.execute(
                    """INSERT INTO query_results
                       (run_id, query, page_url, perplexity_cited, chatgpt_cited, google_cited,
                        readiness_score, verdict, gaps, rewritten_section, suggested_headings, faq_schema)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        run_id,
                        r.get("query", ""),
                        r.get("page_url", ""),
                        int(bool(r.get("perplexity_cited"))) if r.get("perplexity_cited") is not None else None,
                        int(bool(r.get("chatgpt_cited"))) if r.get("chatgpt_cited") is not None else None,
                        int(bool(r.get("google_cited"))) if r.get("google_cited") is not None else None,
                        r.get("readiness_score"),
                        r.get("verdict", ""),
                        json.dumps(r.get("gaps") or []),
                        r.get("rewritten_section", ""),
                        json.dumps(r.get("suggested_headings") or []),
                        r.get("faq_schema", ""),
                    ),
                )
                r["query_result_id"] = qr.lastrowid
            return run_id


# ---------------------------------------------------------------------------
# PROOF-OF-FIX — save a before/after recheck comparison
# ---------------------------------------------------------------------------
def save_fix_attempt(
    query_result_id: int,
    before: dict,
    after: dict,
) -> int:
    def _bool(v):
        return bool(v) if v is not None else None

    data = {
        "query_result_id":   query_result_id,
        "before_score":      before.get("readiness_score"),
        "after_score":       after.get("readiness_score"),
        "before_perplexity": _bool(before.get("perplexity_cited")),
        "after_perplexity":  _bool(after.get("perplexity_cited")),
        "before_chatgpt":    _bool(before.get("chatgpt_cited")),
        "after_chatgpt":     _bool(after.get("chatgpt_cited")),
        "before_google":     _bool(before.get("google_cited")),
        "after_google":      _bool(after.get("google_cited")),
    }

    if _USE_SUPABASE:
        res = _sb().table("fix_attempts").insert(data).execute()
        return res.data[0]["id"] if res.data else -1
    else:
        checked_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.execute(
                """INSERT INTO fix_attempts
                   (query_result_id, before_score, after_score,
                    before_perplexity, after_perplexity,
                    before_chatgpt, after_chatgpt,
                    before_google, after_google, checked_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    query_result_id,
                    data["before_score"],    data["after_score"],
                    int(data["before_perplexity"]) if data["before_perplexity"] is not None else None,
                    int(data["after_perplexity"])  if data["after_perplexity"]  is not None else None,
                    int(data["before_chatgpt"])    if data["before_chatgpt"]    is not None else None,
                    int(data["after_chatgpt"])     if data["after_chatgpt"]     is not None else None,
                    int(data["before_google"])     if data["before_google"]     is not None else None,
                    int(data["after_google"])      if data["after_google"]      is not None else None,
                    checked_at,
                ),
            )
            return cur.lastrowid


def get_fix_attempts(query_result_id: int) -> list[dict]:
    if _USE_SUPABASE:
        res = (
            _sb().table("fix_attempts")
            .select("*")
            .eq("query_result_id", query_result_id)
            .order("checked_at", desc=True)
            .execute()
        )
        return res.data or []
    else:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM fix_attempts WHERE query_result_id = ? ORDER BY checked_at DESC",
                (query_result_id,),
            ).fetchall()
            return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# HISTORY
# ---------------------------------------------------------------------------
def get_history(
    org_name: str,
    limit: int = 10,
    user_id: str | None = None,
) -> list[dict]:
    if _USE_SUPABASE:
        q = (
            _sb().table("runs")
            .select("id, created_at, query_count, cited_count, synthesis")
            .eq("org_name", org_name)
        )
        if user_id:
            q = q.eq("user_id", user_id)
        res = q.order("created_at", desc=True).limit(limit).execute()
        return res.data or []
    else:
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
    if _USE_SUPABASE:
        res = (
            _sb().table("query_results")
            .select("*")
            .eq("run_id", run_id)
            .execute()
        )
        return res.data or []
    else:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM query_results WHERE run_id = ?", (run_id,)
            ).fetchall()
            return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# AGENCY — client switcher
# ---------------------------------------------------------------------------
def get_user_orgs(user_id: str) -> list[str]:
    """Return distinct org names for a user, most recently used first."""
    if _USE_SUPABASE:
        res = (
            _sb().table("runs")
            .select("org_name")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        seen: set[str] = set()
        orgs: list[str] = []
        for row in (res.data or []):
            name = row["org_name"]
            if name not in seen:
                seen.add(name)
                orgs.append(name)
        return orgs
    else:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                """SELECT DISTINCT org_name FROM runs
                   WHERE user_id = ? OR user_id IS NULL
                   ORDER BY created_at DESC""",
                (user_id,),
            ).fetchall()
            return [r[0] for r in rows]
