"""Index reads: unused indexes, missing-index hints, bloat estimate, invalid/dupes.

Thresholds are bound parameters. The bloat figure is a transparent coarse
estimate (all inputs returned) — precise bloat needs the pgstattuple /
pgstatindex extension, which we deliberately do not assume is installed.
"""

from __future__ import annotations

import math
from typing import Any

from postgres_aiops.ops._util import human_bytes, s

_BLOCK_SIZE = 8192
# Coarse average bytes per index entry (key + tuple header/pointer overhead)
# used only for the estimated-ideal-size heuristic below.
_AVG_ENTRY_BYTES = 32

_UNUSED_SQL = """
SELECT s.schemaname AS schema,
       s.relname AS table,
       s.indexrelname AS index,
       s.idx_scan,
       pg_relation_size(s.indexrelid) AS size_bytes,
       i.indisunique AS is_unique,
       i.indisprimary AS is_primary
FROM pg_stat_user_indexes s
JOIN pg_index i ON i.indexrelid = s.indexrelid
WHERE s.idx_scan = 0
  AND i.indisprimary = false
  AND i.indisunique = false
  AND pg_relation_size(s.indexrelid) >= %(min_size_bytes)s
ORDER BY pg_relation_size(s.indexrelid) DESC
"""

_MISSING_SQL = """
SELECT schemaname AS schema,
       relname AS table,
       seq_scan,
       seq_tup_read,
       idx_scan,
       n_live_tup,
       CASE WHEN seq_scan > 0 THEN (seq_tup_read / seq_scan) ELSE 0 END AS avg_tuples_per_scan
FROM pg_stat_user_tables
WHERE seq_scan >= %(min_seq_scan)s
  AND n_live_tup > %(min_live_tup)s
  AND (idx_scan IS NULL OR seq_scan > idx_scan)
ORDER BY seq_tup_read DESC
"""

_BLOAT_SQL = """
SELECT n.nspname AS schema,
       ti.relname AS table,
       i.relname AS index,
       pg_relation_size(i.oid) AS size_bytes,
       i.relpages AS pages,
       i.reltuples::bigint AS tuples,
       COALESCE(s.idx_scan, 0) AS idx_scan
FROM pg_class i
JOIN pg_index x ON x.indexrelid = i.oid
JOIN pg_class ti ON ti.oid = x.indrelid
JOIN pg_namespace n ON n.oid = i.relnamespace
LEFT JOIN pg_stat_user_indexes s ON s.indexrelid = i.oid
WHERE i.relkind = 'i'
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
ORDER BY pg_relation_size(i.oid) DESC
LIMIT %(limit)s
"""

_INVALID_SQL = """
SELECT n.nspname AS schema, tc.relname AS table, c.relname AS index
FROM pg_index i
JOIN pg_class c ON c.oid = i.indexrelid
JOIN pg_class tc ON tc.oid = i.indrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE i.indisvalid = false
ORDER BY n.nspname, tc.relname
"""

_DUPLICATE_SQL = """
SELECT indrelid::regclass::text AS table,
       array_agg(indexrelid::regclass::text ORDER BY indexrelid) AS indexes,
       count(*) AS n
FROM pg_index
GROUP BY indrelid, indkey
HAVING count(*) > 1
"""


def unused_indexes(conn: Any, min_size_bytes: int = 0) -> dict:
    """[READ] Non-unique, non-primary indexes with zero scans (drop candidates)."""
    rows = conn.query(_UNUSED_SQL, {"min_size_bytes": int(min_size_bytes)})
    indexes = [
        {
            "schema": s(r.get("schema"), 128),
            "table": s(r.get("table"), 128),
            "index": s(r.get("index"), 128),
            "idxScan": r.get("idx_scan"),
            "sizeBytes": r.get("size_bytes"),
            "sizePretty": human_bytes(r.get("size_bytes")),
        }
        for r in rows
    ]
    total = sum(i["sizeBytes"] or 0 for i in indexes)
    return {
        "count": len(indexes),
        "reclaimableBytes": total,
        "reclaimablePretty": human_bytes(total),
        "indexes": indexes,
        "note": "idx_scan is cumulative since the last stats reset; confirm over a full cycle.",
    }


def missing_index_hints(
    conn: Any, min_seq_scan: int = 1000, min_live_tup: int = 10000
) -> dict:
    """[READ] Tables with heavy sequential scans and few index scans (index hints)."""
    rows = conn.query(
        _MISSING_SQL, {"min_seq_scan": int(min_seq_scan), "min_live_tup": int(min_live_tup)}
    )
    tables = [
        {
            "schema": s(r.get("schema"), 128),
            "table": s(r.get("table"), 128),
            "seqScan": r.get("seq_scan"),
            "seqTupRead": r.get("seq_tup_read"),
            "idxScan": r.get("idx_scan"),
            "liveTuples": r.get("n_live_tup"),
            "avgTuplesPerScan": r.get("avg_tuples_per_scan"),
        }
        for r in rows
    ]
    return {
        "count": len(tables),
        "thresholds": {"minSeqScan": int(min_seq_scan), "minLiveTup": int(min_live_tup)},
        "tables": tables,
        "note": (
            "Advisory: high seq_scan with many live tuples suggests a missing index; "
            "confirm with EXPLAIN on the hot query before creating one."
        ),
    }


def _bloat_row(r: dict) -> dict:
    tuples = r.get("tuples") or 0
    pages = r.get("pages") or 0
    size_bytes = r.get("size_bytes") or 0
    est_pages = math.ceil(max(tuples, 0) * _AVG_ENTRY_BYTES / _BLOCK_SIZE) + 1
    est_ideal_bytes = est_pages * _BLOCK_SIZE
    bloat_bytes = max(0, size_bytes - est_ideal_bytes)
    bloat_pct = round(100.0 * bloat_bytes / size_bytes, 1) if size_bytes else 0.0
    return {
        "schema": s(r.get("schema"), 128),
        "table": s(r.get("table"), 128),
        "index": s(r.get("index"), 128),
        "sizeBytes": size_bytes,
        "sizePretty": human_bytes(size_bytes),
        "pages": pages,
        "tuples": tuples,
        "idxScan": r.get("idx_scan"),
        "estIdealBytes": est_ideal_bytes,
        "estBloatBytes": bloat_bytes,
        "estBloatPct": bloat_pct,
    }


def index_bloat(conn: Any, limit: int = 50) -> dict:
    """[READ] Coarse index-bloat estimate (all inputs returned for transparency)."""
    rows = conn.query(_BLOAT_SQL, {"limit": max(1, min(int(limit), 500))})
    indexes = [_bloat_row(r) for r in rows]
    indexes.sort(key=lambda i: i["estBloatBytes"], reverse=True)
    return {
        "count": len(indexes),
        "indexes": indexes,
        "note": (
            "Coarse heuristic: estimated ideal size = ceil(tuples * "
            f"{_AVG_ENTRY_BYTES}B / {_BLOCK_SIZE}B pages). For precise bloat install "
            "pgstattuple and use pgstatindex()."
        ),
    }


def invalid_indexes(conn: Any) -> dict:
    """[READ] Invalid indexes (failed CONCURRENTLY builds) and duplicate indexes."""
    invalid = [
        {
            "schema": s(r.get("schema"), 128),
            "table": s(r.get("table"), 128),
            "index": s(r.get("index"), 128),
        }
        for r in conn.query(_INVALID_SQL)
    ]
    duplicates = [
        {
            "table": s(r.get("table"), 128),
            "indexes": [s(x, 128) for x in (r.get("indexes") or [])],
            "count": r.get("n"),
        }
        for r in conn.query(_DUPLICATE_SQL)
    ]
    return {
        "invalidCount": len(invalid),
        "invalid": invalid,
        "duplicateCount": len(duplicates),
        "duplicates": duplicates,
        "note": (
            "Invalid indexes should be dropped and rebuilt (REINDEX). Duplicate "
            "indexes cover the same column set — usually one can be dropped."
        ),
    }
