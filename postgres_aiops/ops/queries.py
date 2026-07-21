"""Query-statistics reads: pg_stat_statements top-N, EXPLAIN, and stats reset.

``top_queries`` orders by a whitelisted column only (never raw caller text).
``explain_query`` must interpolate the statement to EXPLAIN — it cannot be a
bound parameter — so the statement is validated (single statement, no injected
terminator) and the one interpolation site is a single-line f-string.
"""

from __future__ import annotations

from typing import Any

from postgres_aiops.ops._util import opt, order_column

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
        "query": opt(r.get("query"), 400),
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

    Returns an envelope rather than a bare list::

        {"statements": [...], "returned": N, "limit": L, "truncated": true}

    so a truncated read announces itself. A bare list cannot say "there is
    more" — the consumer has to infer it from the length happening to equal the
    limit, and a smaller local model faced with a long result tends to report
    that nothing came back at all. One extra row is requested so ``truncated``
    is *measured* rather than guessed from a length coincidence.
    """
    col = order_column(order_by)  # validated → safe to interpolate below
    sql = _TOP_SQL.format(col=col)  # nosec B608 — col is whitelisted, not user text
    requested = max(1, min(int(limit), 200))
    rows = list(conn.query(sql, {"limit": requested + 1}))
    truncated = len(rows) > requested
    statements = [_statement_row(r) for r in rows[:requested]]
    return {
        "orderBy": order_by,
        "statements": statements,
        "returned": len(statements),
        "limit": requested,
        "truncated": truncated,
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


# First keyword of the statement forms EXPLAIN (ANALYZE) may safely execute:
# these do not modify data. WITH is excluded deliberately — a CTE can contain a
# data-modifying INSERT/UPDATE/DELETE, so it is not safe under ANALYZE.
_READ_ONLY_LEADERS = ("select", "table", "values", "show")


def _assert_read_only_for_analyze(statement: str) -> None:
    """Refuse EXPLAIN ANALYZE on anything that could modify data.

    ANALYZE *executes* the statement to collect real timings, so an
    ``EXPLAIN (ANALYZE) DELETE ...`` would run the DELETE. This tool guarantees
    ``explain_query`` is read-only — enforce it rather than trust the docstring.
    """
    leader = statement.lstrip("( \t\n").split(None, 1)[0].lower() if statement.strip() else ""
    if leader not in _READ_ONLY_LEADERS:
        raise ValueError(
            f"EXPLAIN ANALYZE executes the statement, so it is refused for a "
            f"'{leader.upper() or '?'}' statement, which could modify data. Use "
            f"analyze=False to see the plan without executing, or run a SELECT."
        )


def explain_query(conn: Any, sql: str, analyze: bool = False) -> dict:
    """[READ] Return the JSON execution plan for ``sql`` (EXPLAIN).

    ``analyze=False`` (default) plans without executing. ``analyze=True`` runs the
    statement to collect real row counts/timing, so it is **refused** for anything
    but a read-only statement (SELECT / TABLE / VALUES / SHOW) — this stays a
    genuine ``[READ]``. The statement is validated to be a single statement first.
    """
    statement = _validate_statement(sql)
    options = "FORMAT JSON, VERBOSE, BUFFERS"
    if not analyze:
        command = f"EXPLAIN ({options}) {statement}"  # nosec B608 — validated single statement
        row = conn.query_one(command) or {}
    else:
        # ANALYZE *executes* the statement. The leader check rejects the obvious
        # DML/DDL, but a SELECT can still create a table (SELECT ... INTO),
        # advance state (SELECT nextval(...)), or take locks (FOR UPDATE). Run it
        # inside a transaction that is ROLLED BACK so no side effect can persist —
        # this is what keeps explain_query a genuine [READ]. All three statements
        # share the connection's transaction (a fresh cursor per call does not end
        # it), and ROLLBACK runs even if the EXPLAIN raises.
        _assert_read_only_for_analyze(statement)
        command = f"EXPLAIN (ANALYZE, {options}) {statement}"  # nosec B608 — validated
        conn.execute("BEGIN")
        try:
            row = conn.query_one(command) or {}
        finally:
            conn.execute("ROLLBACK")
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
