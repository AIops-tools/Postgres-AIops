"""Read-path ops tests (server / activity / queries / indexes / tables / replication).

Uses the ``FakePg`` double (substring-matched canned rows) so normalisation,
rollups, and summaries are exercised without a live PostgreSQL cluster.
"""

from __future__ import annotations

import pytest

from postgres_aiops.ops import (
    activity,
    indexes,
    overview,
    queries,
    replication,
    server,
    tables,
)
from tests.conftest import FakePg


@pytest.mark.unit
def test_server_version_shapes():
    conn = FakePg({"version()": [{
        "version": "PostgreSQL 16.2 on x86_64", "server_version": "16.2",
        "server_version_num": "160002", "start_time": "2026-07-01",
        "uptime": "12 days", "in_recovery": False, "database": "app",
        "data_directory": "/var/lib/pg",
    }]})
    out = server.server_version(conn)
    assert out["serverVersion"] == "16.2"
    assert out["inRecovery"] is False


@pytest.mark.unit
def test_list_databases_sorted_with_pretty_size():
    conn = FakePg({"FROM pg_database": [
        {"name": "big", "owner": "postgres", "encoding": "UTF8",
         "collate": "C", "ctype": "C", "size_bytes": 1048576, "allow_conn": True},
    ]})
    out = server.list_databases(conn)
    assert out[0]["sizePretty"] == "1.0 MB"


@pytest.mark.unit
def test_list_activity_counts_states_and_idle_in_txn():
    conn = FakePg({"FROM pg_stat_activity": [
        {"pid": 1, "state": "active", "query": "SELECT 1"},
        {"pid": 2, "state": "idle in transaction", "query": "BEGIN"},
        {"pid": 3, "state": "active", "query": "SELECT 2"},
    ]})
    out = activity.list_activity(conn)
    assert out["total"] == 3
    assert out["byState"]["active"] == 2
    assert out["idleInTransactionCount"] == 1


@pytest.mark.unit
def test_long_running_passes_threshold_param():
    conn = FakePg({"FROM pg_stat_activity": [
        {"pid": 9, "duration_seconds": 120, "query": "SELECT pg_sleep(200)"},
    ]})
    out = activity.long_running_queries(conn, min_seconds=90)
    assert out["thresholdSeconds"] == 90 and out["count"] == 1
    # threshold is bound as a parameter, never string-formatted into the SQL
    _, params = conn.queried[0]
    assert params == {"min_seconds": 90}


@pytest.mark.unit
def test_list_locks_flags_waiting():
    conn = FakePg({"FROM pg_locks": [
        {"pid": 1, "mode": "AccessShareLock", "granted": True, "locktype": "relation"},
        {"pid": 2, "mode": "AccessExclusiveLock", "granted": False, "locktype": "relation"},
    ]})
    out = activity.list_locks(conn)
    assert out["total"] == 2 and out["waitingCount"] == 1


@pytest.mark.unit
def test_top_queries_orders_by_whitelist_column_and_computes_cache_ratio():
    conn = FakePg({"FROM pg_stat_statements": [
        {"queryid": 1, "query": "SELECT 1", "calls": 10, "total_exec_time_ms": 500,
         "mean_exec_time_ms": 50, "stddev_exec_time_ms": 1, "rows": 10,
         "shared_blks_hit": 90, "shared_blks_read": 10, "shared_blks_written": 0,
         "temp_blks_read": 0, "temp_blks_written": 0},
    ]})
    out = queries.top_queries(conn, order_by="io", limit=5)
    assert out["statements"][0]["cacheHitRatioPct"] == 90.0
    # the whitelisted column must appear in the emitted SQL, not the raw choice
    sql, params = conn.queried[0]
    assert "shared_blks_read DESC" in sql
    # limit + 1 is fetched so `truncated` is measured, not inferred from length
    assert params == {"limit": 6}
    assert out["returned"] == 1 and out["limit"] == 5 and out["truncated"] is False


@pytest.mark.unit
def test_top_queries_rejects_unknown_order_by():
    conn = FakePg()
    with pytest.raises(ValueError, match="order_by"):
        queries.top_queries(conn, order_by="; DROP TABLE users")


@pytest.mark.unit
def test_explain_rejects_multi_statement():
    conn = FakePg()
    with pytest.raises(ValueError, match="single statement"):
        queries.explain_query(conn, "SELECT 1; DROP TABLE t")


@pytest.mark.unit
def test_explain_wraps_statement():
    conn = FakePg({"EXPLAIN": [{"QUERY PLAN": [{"Plan": {"Node Type": "Seq Scan"}}]}]})
    out = queries.explain_query(conn, "SELECT * FROM t")
    assert out["analyze"] is False
    sql, _ = conn.queried[0]
    assert sql.startswith("EXPLAIN (FORMAT JSON")


@pytest.mark.unit
def test_unused_indexes_totals_reclaimable():
    conn = FakePg({"FROM pg_stat_user_indexes": [
        {"schema": "public", "table": "t", "index": "idx_a", "idx_scan": 0,
         "size_bytes": 2048, "is_unique": False, "is_primary": False},
    ]})
    out = indexes.unused_indexes(conn)
    assert out["count"] == 1 and out["reclaimableBytes"] == 2048


@pytest.mark.unit
def test_index_bloat_estimate_transparent():
    conn = FakePg({"FROM pg_class i": [
        {"schema": "public", "table": "t", "index": "idx_a",
         "size_bytes": 8192 * 100, "pages": 100, "tuples": 10, "idx_scan": 5},
    ]})
    out = indexes.index_bloat(conn)
    row = out["indexes"][0]
    assert row["estBloatBytes"] > 0 and 0 <= row["estBloatPct"] <= 100


@pytest.mark.unit
def test_table_bloat_dead_pct():
    conn = FakePg({"FROM pg_stat_user_tables": [
        {"schema": "public", "table": "t", "n_live_tup": 80, "n_dead_tup": 20,
         "dead_pct": 20.0, "size_bytes": 4096},
    ]})
    out = tables.table_bloat(conn)
    assert out["tables"][0]["deadPct"] == 20.0


@pytest.mark.unit
def test_replication_status_pretty_lag():
    conn = FakePg({"FROM pg_stat_replication": [
        {"pid": 5, "application_name": "standby1", "state": "streaming",
         "sync_state": "async", "replay_lag_bytes": 1024},
    ]})
    out = replication.replication_status(conn)
    assert out["count"] == 1
    assert out["replicas"][0]["replayLagPretty"] == "1.0 kB"


@pytest.mark.unit
def test_replication_slots_flags_inactive():
    conn = FakePg({"FROM pg_replication_slots": [
        {"slot_name": "s1", "active": True, "retained_bytes": 0, "slot_type": "physical"},
        {"slot_name": "s2", "active": False, "retained_bytes": 999, "slot_type": "physical"},
    ]})
    out = replication.replication_slots(conn)
    assert out["inactiveCount"] == 1 and out["inactive"][0]["slotName"] == "s2"


@pytest.mark.unit
def test_overview_resilient_to_partial_failure():
    # No canned rows for most queries → server_version returns {} but snapshot
    # must still assemble a dict, never raise.
    conn = FakePg({"FROM pg_stat_activity": [{"pid": 1, "state": "active", "query": "x"}]})
    out = overview.snapshot(conn)
    assert isinstance(out, dict)
    assert out["totalConnections"] == 1
