"""Table reads: sizes, dead-tuple bloat proxy, and autovacuum status.

Table bloat here is an honest dead-tuple proxy from pg_stat_user_tables
(dead / (live + dead)) — it needs no extension and directly drives the
vacuum recommendation in the flagship bloat_and_vacuum_analysis.
"""

from __future__ import annotations

from typing import Any

from postgres_aiops.ops._util import human_bytes, s

_SIZES_SQL = """
SELECT n.nspname AS schema,
       c.relname AS table,
       pg_total_relation_size(c.oid) AS total_bytes,
       pg_relation_size(c.oid) AS table_bytes,
       pg_indexes_size(c.oid) AS index_bytes,
       (pg_total_relation_size(c.oid)
        - pg_relation_size(c.oid)
        - pg_indexes_size(c.oid)) AS toast_bytes,
       c.reltuples::bigint AS est_rows
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r'
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
ORDER BY pg_total_relation_size(c.oid) DESC
LIMIT %(limit)s
"""

_BLOAT_SQL = """
SELECT schemaname AS schema,
       relname AS table,
       n_live_tup,
       n_dead_tup,
       CASE WHEN (n_live_tup + n_dead_tup) > 0
            THEN round(100.0 * n_dead_tup / (n_live_tup + n_dead_tup), 2)
            ELSE 0 END AS dead_pct,
       pg_relation_size(relid) AS size_bytes,
       last_vacuum,
       last_autovacuum,
       last_analyze,
       last_autoanalyze
FROM pg_stat_user_tables
ORDER BY n_dead_tup DESC
LIMIT %(limit)s
"""

_AUTOVAC_SQL = """
SELECT schemaname AS schema,
       relname AS table,
       n_live_tup,
       n_dead_tup,
       n_mod_since_analyze,
       last_vacuum,
       last_autovacuum,
       last_analyze,
       last_autoanalyze,
       vacuum_count,
       autovacuum_count,
       analyze_count,
       autoanalyze_count
FROM pg_stat_user_tables
ORDER BY n_dead_tup DESC
LIMIT %(limit)s
"""


def table_sizes(conn: Any, limit: int = 20) -> dict:
    """[READ] Largest tables by total relation size (table + indexes + TOAST)."""
    rows = conn.query(_SIZES_SQL, {"limit": max(1, min(int(limit), 500))})
    tables = [
        {
            "schema": s(r.get("schema"), 128),
            "table": s(r.get("table"), 128),
            "totalBytes": r.get("total_bytes"),
            "totalPretty": human_bytes(r.get("total_bytes")),
            "tableBytes": r.get("table_bytes"),
            "indexBytes": r.get("index_bytes"),
            "toastBytes": r.get("toast_bytes"),
            "estRows": r.get("est_rows"),
        }
        for r in rows
    ]
    return {"count": len(tables), "tables": tables}


def _bloat_row(r: dict) -> dict:
    return {
        "schema": s(r.get("schema"), 128),
        "table": s(r.get("table"), 128),
        "liveTuples": r.get("n_live_tup"),
        "deadTuples": r.get("n_dead_tup"),
        "deadPct": float(r.get("dead_pct") or 0),
        "sizeBytes": r.get("size_bytes"),
        "sizePretty": human_bytes(r.get("size_bytes")),
        "lastVacuum": s(r.get("last_vacuum"), 64),
        "lastAutovacuum": s(r.get("last_autovacuum"), 64),
        "lastAnalyze": s(r.get("last_analyze"), 64),
        "lastAutoanalyze": s(r.get("last_autoanalyze"), 64),
    }


def table_bloat(conn: Any, limit: int = 50) -> dict:
    """[READ] Dead-tuple bloat proxy per table (dead / (live + dead)), worst first."""
    rows = conn.query(_BLOAT_SQL, {"limit": max(1, min(int(limit), 500))})
    tables = [_bloat_row(r) for r in rows]
    return {
        "count": len(tables),
        "tables": tables,
        "note": (
            "deadPct = dead / (live + dead) from pg_stat_user_tables — a vacuum "
            "proxy, not physical bloat. VACUUM makes dead space reusable; VACUUM "
            "FULL / pg_repack reclaims disk."
        ),
    }


def autovacuum_status(conn: Any, limit: int = 50) -> dict:
    """[READ] Per-table dead tuples, mods-since-analyze, and last (auto)vacuum times."""
    rows = conn.query(_AUTOVAC_SQL, {"limit": max(1, min(int(limit), 500))})
    tables = [
        {
            "schema": s(r.get("schema"), 128),
            "table": s(r.get("table"), 128),
            "liveTuples": r.get("n_live_tup"),
            "deadTuples": r.get("n_dead_tup"),
            "modSinceAnalyze": r.get("n_mod_since_analyze"),
            "lastVacuum": s(r.get("last_vacuum"), 64),
            "lastAutovacuum": s(r.get("last_autovacuum"), 64),
            "lastAnalyze": s(r.get("last_analyze"), 64),
            "lastAutoanalyze": s(r.get("last_autoanalyze"), 64),
            "vacuumCount": r.get("vacuum_count"),
            "autovacuumCount": r.get("autovacuum_count"),
            "analyzeCount": r.get("analyze_count"),
            "autoanalyzeCount": r.get("autoanalyze_count"),
        }
        for r in rows
    ]
    never_autovac = [t["table"] for t in tables if not t["lastAutovacuum"] and t["deadTuples"]]
    return {
        "count": len(tables),
        "neverAutovacuumedWithDead": never_autovac,
        "tables": tables,
    }
