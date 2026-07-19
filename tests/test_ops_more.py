"""Additional read-path ops coverage: server/tables/indexes/replication shapes.

Uses the ``FakePg`` double to reach the ops functions the first read tests skip
(settings, extensions, roles; table sizes and autovacuum; missing-index hints and
invalid/duplicate indexes; WAL/archiver status) — asserting the normalised keys
and the small classifications (updateAvailable, neverAutovacuumedWithDead).
"""

from __future__ import annotations

import pytest

from postgres_aiops.ops import indexes, replication, server, tables
from tests.conftest import FakePg

# ── server ───────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_show_settings_binds_ilike_pattern_and_shapes():
    conn = FakePg({"FROM pg_settings": [
        {"name": "work_mem", "setting": "4MB", "unit": "kB", "context": "user",
         "pending_restart": False, "short_desc": "memory for sorts"},
    ]})
    out = server.show_settings(conn, "mem")
    assert out[0]["name"] == "work_mem" and out[0]["description"] == "memory for sorts"
    _, params = conn.queried[0]
    assert params == {"pattern": "%mem%"}


@pytest.mark.unit
def test_show_settings_no_pattern_binds_none():
    conn = FakePg({"FROM pg_settings": []})
    server.show_settings(conn)
    _, params = conn.queried[0]
    assert params == {"pattern": None}


@pytest.mark.unit
def test_list_extensions_flags_update_available():
    conn = FakePg({"FROM pg_extension": [
        {"name": "pg_stat_statements", "installed_version": "1.9", "default_version": "1.10"},
        {"name": "hstore", "installed_version": "1.8", "default_version": "1.8"},
    ]})
    out = server.list_extensions(conn)
    assert out[0]["updateAvailable"] is True
    assert out[1]["updateAvailable"] is False


@pytest.mark.unit
def test_list_roles_shapes_attributes():
    conn = FakePg({"FROM pg_roles": [
        {"name": "app", "superuser": False, "can_login": True, "replication": False,
         "conn_limit": 100},
    ]})
    out = server.list_roles(conn)
    assert out[0]["canLogin"] is True and out[0]["connLimit"] == 100


# ── tables ───────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_table_sizes_clamps_limit_and_shapes():
    conn = FakePg({"FROM pg_class c": [
        {"schema": "public", "table": "orders", "total_bytes": 3072, "table_bytes": 2048,
         "index_bytes": 1024, "toast_bytes": 0, "est_rows": 500},
    ]})
    out = tables.table_sizes(conn, limit=9999)
    assert out["returned"] == 1 and out["tables"][0]["totalPretty"] == "3.0 kB"
    assert out["limit"] == 500 and out["truncated"] is False
    _, params = conn.queried[0]
    # clamped to the 500 ceiling, then +1 so truncation is measured not guessed
    assert params == {"limit": 501}


@pytest.mark.unit
def test_autovacuum_status_flags_never_autovacuumed_with_dead():
    conn = FakePg({"FROM pg_stat_user_tables": [
        {"schema": "public", "table": "hot", "n_live_tup": 10, "n_dead_tup": 200,
         "last_autovacuum": None, "vacuum_count": 0},
        {"schema": "public", "table": "clean", "n_live_tup": 10, "n_dead_tup": 0,
         "last_autovacuum": None},
    ]})
    out = tables.autovacuum_status(conn)
    assert out["neverAutovacuumedWithDead"] == ["hot"]


# ── indexes ──────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_missing_index_hints_binds_thresholds():
    conn = FakePg({"FROM pg_stat_user_tables": [
        {"schema": "public", "table": "t", "seq_scan": 5000, "seq_tup_read": 1_000_000,
         "idx_scan": 1, "n_live_tup": 50000, "avg_tuples_per_scan": 200},
    ]})
    out = indexes.missing_index_hints(conn, min_seq_scan=2000, min_live_tup=10000)
    assert out["count"] == 1
    assert out["thresholds"] == {"minSeqScan": 2000, "minLiveTup": 10000}
    _, params = conn.queried[0]
    assert params == {"min_seq_scan": 2000, "min_live_tup": 10000}


@pytest.mark.unit
def test_invalid_indexes_reports_invalid_and_duplicates():
    conn = FakePg({
        "WHERE i.indisvalid = false": [{"schema": "public", "table": "t", "index": "bad_idx"}],
        "GROUP BY indrelid": [
            {"table": "public.t", "indexes": ["idx_a", "idx_b"], "n": 2},
        ],
    })
    out = indexes.invalid_indexes(conn)
    assert out["invalidCount"] == 1 and out["invalid"][0]["index"] == "bad_idx"
    assert out["duplicateCount"] == 1
    assert out["duplicates"][0]["indexes"] == ["idx_a", "idx_b"]


# ── replication ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_wal_status_assembles_wal_and_archiver():
    conn = FakePg({
        "pg_is_in_recovery() AS in_recovery": [
            {"in_recovery": False, "current_lsn": "0/16B3740", "current_wal_file": "0000...",
             "wal_level": "replica", "max_wal_size": "1GB", "archive_mode": "on"},
        ],
        "FROM pg_stat_archiver": [
            {"archived_count": 42, "last_archived_wal": "0000...41", "failed_count": 0},
        ],
    })
    out = replication.wal_status(conn)
    assert out["inRecovery"] is False and out["walLevel"] == "replica"
    assert out["archiver"]["archivedCount"] == 42 and out["archiver"]["failedCount"] == 0
