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
  get_user_orgs()   — returns distinct org names for the client switcher
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

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
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id               INTEGER NOT NULL REFERENCES runs(id),
                query                TEXT    NOT NULL,
                page_url             TEXT,
                perplexity_cited     INTEGER,
                chatgpt_cited        INTEGER,
                google_cited         INTEGER,
                perplexity_citations TEXT,
                chatgpt_citations    TEXT,
                google_citations     TEXT,
                readiness_score      INTEGER,
                verdict              TEXT,
                gaps                 TEXT,
                rewritten_section    TEXT,
                suggested_headings   TEXT,
                faq_schema           TEXT
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tracked_queries (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT    NOT NULL,
                org_name   TEXT    NOT NULL,
                query      TEXT    NOT NULL,
                page_url   TEXT,
                active     INTEGER NOT NULL DEFAULT 1,
                created_at TEXT    NOT NULL,
                UNIQUE(user_id, org_name, query)
            )
        """)
        for _col in ["perplexity_citations", "chatgpt_citations", "google_citations"]:
            try:
                conn.execute(f"ALTER TABLE query_results ADD COLUMN {_col} TEXT")
            except sqlite3.OperationalError:
                pass


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
                "run_id":                run_id,
                "query":                 r.get("query", ""),
                "page_url":              r.get("page_url", ""),
                "perplexity_cited":      r.get("perplexity_cited"),
                "chatgpt_cited":         r.get("chatgpt_cited"),
                "google_cited":          r.get("google_cited"),
                "perplexity_citations":  r.get("perplexity_citations") or [],
                "chatgpt_citations":     r.get("chatgpt_citations") or [],
                "google_citations":      r.get("google_citations") or [],
                "readiness_score":       r.get("readiness_score"),
                "verdict":               r.get("verdict", ""),
                "gaps":                  r.get("gaps") or [],
                "rewritten_section":     r.get("rewritten_section", ""),
                "suggested_headings":    r.get("suggested_headings") or [],
                "faq_schema":            r.get("faq_schema", ""),
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
                        perplexity_citations, chatgpt_citations, google_citations,
                        readiness_score, verdict, gaps, rewritten_section, suggested_headings, faq_schema)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        run_id,
                        r.get("query", ""),
                        r.get("page_url", ""),
                        int(bool(r.get("perplexity_cited"))) if r.get("perplexity_cited") is not None else None,
                        int(bool(r.get("chatgpt_cited"))) if r.get("chatgpt_cited") is not None else None,
                        int(bool(r.get("google_cited"))) if r.get("google_cited") is not None else None,
                        json.dumps(r.get("perplexity_citations") or []),
                        json.dumps(r.get("chatgpt_citations") or []),
                        json.dumps(r.get("google_citations") or []),
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
# CLIENTS — multi-client switcher
# ---------------------------------------------------------------------------
def get_stale_orgs(user_id: str, days: int = 14) -> list[dict]:
    """
    Return orgs for this user whose most recent run is older than `days` days.
    Each entry: {org_name, last_run (ISO string), days_ago (int)}.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    if _USE_SUPABASE:
        res = (
            _sb().table("runs")
            .select("org_name, created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        latest: dict[str, str] = {}
        for row in (res.data or []):
            name = row["org_name"]
            if name not in latest:
                latest[name] = row["created_at"]

        stale = []
        for org_name, last_run in latest.items():
            if last_run < cutoff:
                try:
                    last_dt  = datetime.fromisoformat(last_run.replace("Z", "+00:00"))
                    days_ago = (datetime.now(timezone.utc) - last_dt).days
                except Exception:
                    days_ago = days
                stale.append({"org_name": org_name, "last_run": last_run, "days_ago": days_ago})
        return stale

    else:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                """SELECT org_name, MAX(created_at) as last_run
                   FROM runs
                   WHERE (user_id = ? OR user_id IS NULL)
                   GROUP BY org_name
                   HAVING MAX(created_at) < ?
                   ORDER BY last_run DESC""",
                (user_id, cutoff),
            ).fetchall()
            result = []
            for org_name, last_run in rows:
                try:
                    last_dt  = datetime.fromisoformat(last_run)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    days_ago = (datetime.now(timezone.utc) - last_dt).days
                except Exception:
                    days_ago = days
                result.append({"org_name": org_name, "last_run": last_run, "days_ago": days_ago})
            return result


# ---------------------------------------------------------------------------
# TRACKED QUERIES — persistent query+page sets per org
# ---------------------------------------------------------------------------
def upsert_tracked_queries(user_id: str, org_name: str, rows: list[dict]) -> None:
    """
    Save or update the tracked query+page_url set for an org.
    Uses UPSERT so re-running the same audit doesn't duplicate rows.
    rows: list of {query, page_url}
    """
    now = datetime.now(timezone.utc).isoformat()
    if _USE_SUPABASE:
        sb = _sb()
        for row in rows:
            sb.table("tracked_queries").upsert(
                {
                    "user_id":  user_id,
                    "org_name": org_name,
                    "query":    row["query"],
                    "page_url": row.get("page_url", ""),
                    "active":   True,
                },
                on_conflict="user_id,org_name,query",
            ).execute()
    else:
        with sqlite3.connect(DB_PATH) as conn:
            for row in rows:
                conn.execute(
                    """INSERT INTO tracked_queries
                           (user_id, org_name, query, page_url, active, created_at)
                       VALUES (?, ?, ?, ?, 1, ?)
                       ON CONFLICT(user_id, org_name, query) DO UPDATE SET
                           page_url = excluded.page_url,
                           active   = 1""",
                    (user_id, org_name, row["query"], row.get("page_url", ""), now),
                )


def get_tracked_queries(user_id: str, org_name: str) -> list[dict]:
    """Return active tracked queries for an org, oldest-first (stable order)."""
    if _USE_SUPABASE:
        res = (
            _sb().table("tracked_queries")
            .select("query, page_url, created_at")
            .eq("user_id",  user_id)
            .eq("org_name", org_name)
            .eq("active",   True)
            .order("created_at", desc=False)
            .execute()
        )
        return res.data or []
    else:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT query, page_url, created_at FROM tracked_queries
                   WHERE user_id = ? AND org_name = ? AND active = 1
                   ORDER BY created_at ASC""",
                (user_id, org_name),
            ).fetchall()
            return [dict(r) for r in rows]


def get_query_citation_history(
    user_id: str,
    org_name: str,
    query: str,
    limit: int = 12,
) -> list[dict]:
    """
    Return per-engine citation results for a specific query across all runs,
    oldest-first — so callers can render a chronological trend table.
    Each row: {perplexity_cited, chatgpt_cited, google_cited, readiness_score, created_at}
    """
    if _USE_SUPABASE:
        # Collect run IDs belonging to this user+org
        runs_res = (
            _sb().table("runs")
            .select("id")
            .eq("user_id",  user_id)
            .eq("org_name", org_name)
            .execute()
        )
        run_ids = [r["id"] for r in (runs_res.data or [])]
        if not run_ids:
            return []
        res = (
            _sb().table("query_results")
            .select("perplexity_cited, chatgpt_cited, google_cited, readiness_score, created_at")
            .eq("query", query)
            .in_("run_id", run_ids)
            .order("created_at", desc=False)
            .limit(limit)
            .execute()
        )
        return res.data or []
    else:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT qr.perplexity_cited, qr.chatgpt_cited, qr.google_cited,
                          qr.readiness_score, r.created_at
                   FROM query_results qr
                   JOIN runs r ON qr.run_id = r.id
                   WHERE qr.query = ?
                     AND r.org_name = ?
                     AND (r.user_id = ? OR r.user_id IS NULL)
                   ORDER BY r.created_at ASC
                   LIMIT ?""",
                (query, org_name, user_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]


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


def get_all_orgs_summary(user_id: str) -> list[dict]:
    """
    Return one summary row per org: most recent run date, cited_count,
    query_count. Used by the client overview to show all clients at a glance.
    """
    if _USE_SUPABASE:
        res = (
            _sb().table("runs")
            .select("org_name, created_at, cited_count, query_count")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        seen: dict[str, dict] = {}
        for row in (res.data or []):
            name = row["org_name"]
            if name not in seen:
                seen[name] = {
                    "org_name":      name,
                    "last_run_date": row["created_at"],
                    "cited_count":   row["cited_count"],
                    "query_count":   row["query_count"],
                }
        return list(seen.values())
    else:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT org_name,
                          MAX(created_at)                          AS last_run_date,
                          cited_count,
                          query_count
                   FROM runs
                   WHERE user_id = ? OR user_id IS NULL
                   GROUP BY org_name
                   ORDER BY last_run_date DESC""",
                (user_id,),
            ).fetchall()
            return [dict(r) for r in rows]
