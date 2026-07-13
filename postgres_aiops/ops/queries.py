"""Query-statistics reads: pg_stat_statements top-N, EXPLAIN, and stats reset.

``top_queries`` orders by a whitelisted column only (never raw caller text).
``explain_query`` must interpolate the statement to EXPLAIN — it cannot be a
bound parameter — so the statement is validated (single statement, no injected
terminator) and the one interpolation site is a single-line f-string.
"""

from __future__ import annotations

from typing import Any

from postgres_aiops.ops._util import order_column, s

_TOP_SQL = """
SELECT queryid,
       left(query, 400) AS query,
       calls,
       round(total_exec_time::numeric, 2) AS total_exec_time_ms,
       round(mean_exec_time::numeric, 2) AS mean_exec_time_ms,
       round(stddev_exec_time::numeric, 2) AS stddev_exec_time_ms,
       rows,
       shared_blks_hit,
       shared_blks_read,
       shared_blks_written,
       temp_blks_read,
       temp_blks_written
FROM pg_stat_statements
ORDER BY {col} DESC
LIMIT %(limit)s
"""

_MAX_STATEMENT_LEN = 100_000


def _statement_row(r: dict) -> dict:
    hit = r.get("shared_blks_hit") or 0
    read = r.get("shared_blks_read") or 0
    total_blks = hit + read
    cache_hit_ratio = round(100.0 * hit / total_blks, 2) if total_blks else None
    return {
        "queryId": r.get("queryid"),
        "query": s(r.get("query"), 400),
        "calls": r.get("calls"),
        "totalExecTimeMs": float(r.get("total_exec_time_ms") or 0),
        "meanExecTimeMs": float(r.get("mean_exec_time_ms") or 0),
        "stddevExecTimeMs": float(r.get("stddev_exec_time_ms") or 0),
        "rows": r.get("rows"),
        "sharedBlksHit": hit,
        "sharedBlksRead": read,
        "sharedBlksWritten": r.get("shared_blks_written"),
        "tempBlksRead": r.get("temp_blks_read"),
        "tempBlksWritten": r.get("temp_blks_written"),
        "cacheHitRatioPct": cache_hit_ratio,
    }


def top_queries(conn: Any, order_by: str = "total_time", limit: int = 20) -> dict:
    """[READ] Top statements from pg_stat_statements by a whitelisted metric.

    ``order_by`` is one of total_time, mean_time, calls, rows, io — mapped to a
    real column through a whitelist, so no caller text ever reaches the ORDER BY.
    """
    col = order_column(order_by)  # validated → safe to interpolate below
    sql = _TOP_SQL.format(col=col)  # nosec B608 — col is whitelisted, not user text
    rows = conn.query(sql, {"limit": max(1, min(int(limit), 200))})
    return {
        "orderBy": order_by,
        "count": len(rows),
        "statements": [_statement_row(r) for r in rows],
        "note": (
            "Requires the pg_stat_statements extension. Times are milliseconds; "
            "cacheHitRatioPct is shared_blks_hit / (hit + read)."
        ),
    }


def _validate_statement(sql: str) -> str:
    """Reject empty/multi-statement input so EXPLAIN interpolation is bounded."""
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        raise ValueError("No SQL statement supplied to EXPLAIN.")
    if len(text) > _MAX_STATEMENT_LEN:
        raise ValueError("Statement too long to EXPLAIN.")
    if ";" in text:
        raise ValueError(
            "Only a single statement may be EXPLAINed (embedded ';' rejected)."
        )
    return text


def explain_query(conn: Any, sql: str, analyze: bool = False) -> dict:
    """[READ] Return the JSON execution plan for ``sql`` (EXPLAIN).

    ``analyze=False`` (default) plans without executing. ``analyze=True`` runs the
    statement to collect real row counts/timing — only use it for read-only SQL,
    since ANALYZE executes side effects. The statement is validated to be a
    single statement before it is placed into the EXPLAIN command.
    """
    statement = _validate_statement(sql)
    options = "FORMAT JSON, VERBOSE, BUFFERS"
    if analyze:
        options = "ANALYZE, " + options
    command = f"EXPLAIN ({options}) {statement}"  # nosec B608 — validated single statement
    row = conn.query_one(command) or {}
    plan = next(iter(row.values()), None) if row else None
    return {
        "analyze": bool(analyze),
        "plan": plan,
        "note": (
            "EXPLAIN plan in JSON. With analyze=True the statement is executed; "
            "only use it for read-only SQL."
        ),
    }


def reset_query_stats(conn: Any) -> dict:
    """[WRITE] Reset pg_stat_statements accumulators (pg_stat_statements_reset()).

    Irreversible — the counters cannot be restored — so no undo is recorded.
    """
    conn.execute("SELECT pg_stat_statements_reset()")
    return {"action": "reset_query_stats", "reset": True}
