"""A truncated read announces itself, and the truncation is measured.

A bare list cannot say "there is more": the consumer has to infer it from the
length happening to equal the limit, which is a coincidence, not a fact. Worse,
a smaller local model faced with a long-but-cut-short result tends to report
that nothing came back at all. So every limited read returns an envelope —
``{"<items>": [...], "returned": N, "limit": L, "truncated": bool}`` — and
fetches one row past the limit so ``truncated`` is measured rather than guessed.
"""

from __future__ import annotations

import pytest

from postgres_aiops.ops import indexes, queries, tables
from tests.conftest import FakePg


def _rows(n: int, **extra) -> list[dict]:
    return [{"schema": "public", "table": f"t{i}", "index": f"i{i}", **extra} for i in range(n)]


# ── the extra row is actually requested ──────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    ("fn", "marker", "limit"),
    [
        (lambda c, n: tables.table_sizes(c, limit=n), "FROM pg_class c", 5),
        (lambda c, n: tables.table_bloat(c, limit=n), "FROM pg_stat_user_tables", 5),
        (lambda c, n: tables.autovacuum_status(c, limit=n), "FROM pg_stat_user_tables", 5),
        (lambda c, n: indexes.index_bloat(c, limit=n), "FROM pg_class i", 5),
    ],
)
def test_one_extra_row_is_fetched_so_truncation_is_measured(fn, marker, limit):
    conn = FakePg({marker: _rows(0)})
    fn(conn, limit)
    _, params = conn.queried[0]
    assert params == {"limit": limit + 1}, "must over-fetch by exactly one"


@pytest.mark.unit
def test_top_queries_also_over_fetches_by_one():
    conn = FakePg({"FROM pg_stat_statements": []})
    queries.top_queries(conn, limit=7)
    _, params = conn.queried[0]
    assert params == {"limit": 8}


# ── the envelope reports the truth ───────────────────────────────────────────


@pytest.mark.unit
def test_truncated_is_true_and_the_extra_row_is_not_returned():
    """limit+1 rows come back → report limit rows and truncated=True."""
    conn = FakePg({"FROM pg_stat_user_tables": _rows(4, n_dead_tup=1)})
    out = tables.table_bloat(conn, limit=3)
    assert out["truncated"] is True
    assert out["returned"] == 3
    assert out["limit"] == 3
    assert len(out["tables"]) == 3, "the probe row must never leak into the payload"


@pytest.mark.unit
def test_a_full_page_that_is_not_truncated_says_so():
    """Exactly `limit` rows exist — the old length-equals-limit heuristic would
    have called this truncated. Over-fetching gets it right."""
    conn = FakePg({"FROM pg_stat_user_tables": _rows(3, n_dead_tup=1)})
    out = tables.table_bloat(conn, limit=3)
    assert out["returned"] == 3
    assert out["truncated"] is False


@pytest.mark.unit
def test_empty_result_is_not_truncated():
    conn = FakePg({"FROM pg_stat_statements": []})
    out = queries.top_queries(conn, limit=10)
    assert out == {
        "orderBy": "total_time",
        "statements": [],
        "returned": 0,
        "limit": 10,
        "truncated": False,
        "note": out["note"],
    }


@pytest.mark.unit
def test_index_bloat_resorts_by_bloat_after_truncating_by_size():
    """The SQL takes the largest N by size; the probe row is dropped before the
    worst-bloat-first re-sort, so it cannot influence the returned ordering."""
    conn = FakePg({"FROM pg_class i": [
        {"schema": "public", "table": "t", "index": f"i{i}",
         "size_bytes": 10_000_000, "pages": 1, "tuples": 1, "idx_scan": 0}
        for i in range(3)
    ]})
    out = indexes.index_bloat(conn, limit=2)
    assert out["truncated"] is True
    assert out["returned"] == 2 and len(out["indexes"]) == 2


@pytest.mark.unit
def test_limit_clamp_is_reflected_in_the_reported_limit():
    """The envelope reports the limit actually applied, not the one asked for."""
    conn = FakePg({"FROM pg_stat_statements": []})
    out = queries.top_queries(conn, limit=99999)
    assert out["limit"] == 200, "the reported limit must be the enforced ceiling"


# ── analysis propagates its source's truncation ──────────────────────────────


@pytest.mark.unit
def test_analysis_reports_when_its_source_read_was_truncated(tmp_path, monkeypatch):
    """An RCA drawn from a cut-short top-N is a partial verdict — say so.

    The MCP tool pulls the top-N itself, so without this the truncation flag
    would be silently dropped on the way into the analysis.
    """
    monkeypatch.setenv("POSTGRES_AIOPS_HOME", str(tmp_path))
    from mcp_server.tools import analysis as gov_analysis

    conn = FakePg({"FROM pg_stat_statements": [
        {"queryid": i, "query": f"SELECT {i}", "calls": 1, "total_exec_time_ms": 100 - i,
         "mean_exec_time_ms": 1, "stddev_exec_time_ms": 0, "rows": 1,
         "shared_blks_hit": 1, "shared_blks_read": 0, "shared_blks_written": 0,
         "temp_blks_read": 0, "temp_blks_written": 0}
        for i in range(4)
    ]})
    monkeypatch.setattr(gov_analysis, "_get_connection", lambda target=None: conn)

    out = gov_analysis.slow_query_rca(limit=2)
    assert out["sourceTruncated"] is True, "a partial source must be flagged"
    assert out["sourceLimit"] == 2


@pytest.mark.unit
def test_analysis_omits_the_source_flag_when_rows_are_injected(tmp_path, monkeypatch):
    """Injected rows have no source read, so there is no truncation to report."""
    monkeypatch.setenv("POSTGRES_AIOPS_HOME", str(tmp_path))
    from mcp_server.tools import analysis as gov_analysis

    out = gov_analysis.slow_query_rca(statements=[{"totalExecTimeMs": 5, "calls": 1}])
    assert "sourceTruncated" not in out


@pytest.mark.unit
def test_in_memory_analysis_caps_also_announce_themselves():
    """bloat_and_vacuum_analysis caps at MAX_ROWS — that cut is reported too."""
    from postgres_aiops.ops import analysis

    rows = [
        {"schema": "public", "table": f"t{i}", "deadPct": 90.0, "deadTuples": 5000,
         "sizePretty": "1.0 MB", "lastAutovacuum": None}
        for i in range(analysis.MAX_ROWS + 5)
    ]
    out = analysis.bloat_and_vacuum_analysis(rows)
    assert out["truncated"] is True
    assert out["returned"] == analysis.MAX_ROWS
    assert out["limit"] == analysis.MAX_ROWS
    assert len(out["recommendations"]) == analysis.MAX_ROWS
