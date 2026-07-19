"""CLI read commands — drive each Typer command with a stubbed connection.

Every read command resolves its connection through ``get_connection`` (imported
into each sub-module's namespace). We monkeypatch that single seam to hand back a
``FakePg`` with canned catalog rows, then assert the command exits 0 and its JSON
carries the normalised shape the ops layer produced — so the thin CLI glue and
its ``cli_errors`` wrapper are exercised without a live cluster.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from tests.conftest import FakePg

runner = CliRunner()


def _patch_conn(monkeypatch, module, conn) -> None:
    """Replace the ``get_connection`` seam a CLI sub-module imported."""
    monkeypatch.setattr(module, "get_connection", lambda target=None: (conn, None))


def _out_json(result) -> object:
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


# ── server ───────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cli_server_version(monkeypatch):
    from postgres_aiops.cli import app
    from postgres_aiops.cli import server as cli_server

    conn = FakePg({"version()": [{"server_version": "16.2", "in_recovery": False}]})
    _patch_conn(monkeypatch, cli_server, conn)
    out = _out_json(runner.invoke(app, ["server", "version"]))
    assert out["serverVersion"] == "16.2"


@pytest.mark.unit
def test_cli_server_settings_passes_pattern(monkeypatch):
    from postgres_aiops.cli import app
    from postgres_aiops.cli import server as cli_server

    conn = FakePg({"FROM pg_settings": [{"name": "work_mem", "setting": "4MB"}]})
    _patch_conn(monkeypatch, cli_server, conn)
    out = _out_json(runner.invoke(app, ["server", "settings", "work"]))
    assert out[0]["name"] == "work_mem"
    # the argument is turned into an ILIKE %pattern% bound param, never inlined
    sql, params = conn.queried[0]
    assert params == {"pattern": "%work%"}


@pytest.mark.unit
def test_cli_server_extensions_databases_roles(monkeypatch):
    from postgres_aiops.cli import app
    from postgres_aiops.cli import server as cli_server

    conn = FakePg({
        "FROM pg_extension": [{"name": "pg_stat_statements",
                               "installed_version": "1.10", "default_version": "1.11"}],
        "FROM pg_database": [{"name": "app", "owner": "postgres", "encoding": "UTF8",
                              "size_bytes": 2048, "allow_conn": True}],
        "FROM pg_roles": [{"name": "postgres", "superuser": True, "can_login": True}],
    })
    _patch_conn(monkeypatch, cli_server, conn)
    ext = _out_json(runner.invoke(app, ["server", "extensions"]))
    assert ext[0]["updateAvailable"] is True
    dbs = _out_json(runner.invoke(app, ["server", "databases"]))
    assert dbs[0]["sizePretty"] == "2.0 kB"
    roles = _out_json(runner.invoke(app, ["server", "roles"]))
    assert roles[0]["superuser"] is True


# ── activity ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cli_activity_list_long_locks(monkeypatch):
    from postgres_aiops.cli import activity as cli_activity
    from postgres_aiops.cli import app

    conn = FakePg({
        "FROM pg_stat_activity": [{"pid": 1, "state": "active", "query": "SELECT 1",
                                   "duration_seconds": 120}],
        "FROM pg_locks": [{"pid": 1, "mode": "AccessShareLock", "granted": True}],
    })
    _patch_conn(monkeypatch, cli_activity, conn)
    lst = _out_json(runner.invoke(app, ["activity", "list", "--state", "active"]))
    assert lst["total"] == 1
    lng = _out_json(runner.invoke(app, ["activity", "long", "--min-seconds", "90"]))
    assert lng["thresholdSeconds"] == 90
    locks = _out_json(runner.invoke(app, ["activity", "locks"]))
    assert locks["total"] == 1


# ── query ────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cli_query_top_and_explain(monkeypatch):
    from postgres_aiops.cli import app
    from postgres_aiops.cli import query as cli_query

    conn = FakePg({
        "FROM pg_stat_statements": [{"queryid": 1, "query": "SELECT 1", "calls": 5,
                                     "total_exec_time_ms": 10, "mean_exec_time_ms": 2,
                                     "shared_blks_hit": 9, "shared_blks_read": 1}],
        "EXPLAIN": [{"QUERY PLAN": [{"Plan": {"Node Type": "Seq Scan"}}]}],
    })
    _patch_conn(monkeypatch, cli_query, conn)
    top = _out_json(runner.invoke(app, ["query", "top", "--order-by", "calls", "--limit", "5"]))
    assert top["statements"][0]["cacheHitRatioPct"] == 90.0
    plan = _out_json(runner.invoke(app, ["query", "explain", "SELECT * FROM t"]))
    assert plan["analyze"] is False


@pytest.mark.unit
def test_cli_query_top_bad_order_by_is_teaching_error(monkeypatch):
    from postgres_aiops.cli import app
    from postgres_aiops.cli import query as cli_query

    _patch_conn(monkeypatch, cli_query, FakePg())
    result = runner.invoke(app, ["query", "top", "--order-by", "; DROP TABLE t"])
    assert result.exit_code == 1
    assert "Error:" in result.output and "order_by" in result.output


# ── index ────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cli_index_all_four(monkeypatch):
    from postgres_aiops.cli import app
    from postgres_aiops.cli import index as cli_index

    conn = FakePg({
        "FROM pg_stat_user_indexes": [{"schema": "public", "table": "t", "index": "i",
                                       "idx_scan": 0, "size_bytes": 4096,
                                       "is_unique": False, "is_primary": False}],
        "FROM pg_stat_user_tables": [{"schema": "public", "table": "t", "seq_scan": 5000,
                                      "seq_tup_read": 10, "n_live_tup": 20000}],
        "FROM pg_class i": [{"schema": "public", "table": "t", "index": "i",
                             "size_bytes": 8192, "pages": 1, "tuples": 1, "idx_scan": 0}],
        "WHERE i.indisvalid = false": [{"schema": "public", "table": "t", "index": "bad"}],
        "GROUP BY indrelid": [],
    })
    _patch_conn(monkeypatch, cli_index, conn)
    assert _out_json(runner.invoke(app, ["index", "unused"]))["count"] == 1
    assert _out_json(runner.invoke(app, ["index", "missing"]))["count"] == 1
    assert _out_json(runner.invoke(app, ["index", "bloat"]))["returned"] == 1
    assert _out_json(runner.invoke(app, ["index", "invalid"]))["invalidCount"] == 1


# ── table ────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cli_table_sizes_bloat_autovacuum(monkeypatch):
    from postgres_aiops.cli import app
    from postgres_aiops.cli import table as cli_table

    conn = FakePg({
        "FROM pg_class c": [{"schema": "public", "table": "t", "total_bytes": 1024,
                             "table_bytes": 512, "index_bytes": 512, "toast_bytes": 0,
                             "est_rows": 10}],
        "FROM pg_stat_user_tables": [{"schema": "public", "table": "t", "n_live_tup": 80,
                                      "n_dead_tup": 20, "dead_pct": 20.0, "size_bytes": 4096,
                                      "last_autovacuum": None}],
    })
    _patch_conn(monkeypatch, cli_table, conn)
    assert _out_json(runner.invoke(app, ["table", "sizes"]))["returned"] == 1
    assert _out_json(runner.invoke(app, ["table", "bloat"]))["tables"][0]["deadPct"] == 20.0
    av = _out_json(runner.invoke(app, ["table", "autovacuum"]))
    assert av["neverAutovacuumedWithDead"] == ["t"]


# ── replication ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cli_repl_status_slots_wal(monkeypatch):
    from postgres_aiops.cli import app
    from postgres_aiops.cli import replication as cli_repl

    conn = FakePg({
        "FROM pg_stat_replication": [{"pid": 5, "application_name": "s1", "state": "streaming",
                                      "replay_lag_bytes": 1024}],
        "FROM pg_replication_slots": [{"slot_name": "s2", "active": False, "retained_bytes": 99,
                                       "slot_type": "physical"}],
        "pg_is_in_recovery() AS in_recovery": [{"in_recovery": False, "wal_level": "replica"}],
        "FROM pg_stat_archiver": [{"archived_count": 3, "failed_count": 0}],
    })
    _patch_conn(monkeypatch, cli_repl, conn)
    assert _out_json(runner.invoke(app, ["repl", "status"]))["count"] == 1
    assert _out_json(runner.invoke(app, ["repl", "slots"]))["inactiveCount"] == 1
    wal = _out_json(runner.invoke(app, ["repl", "wal"]))
    assert wal["walLevel"] == "replica" and wal["archiver"]["archivedCount"] == 3


# ── analyze (flagship RCA CLIs) ──────────────────────────────────────────────


@pytest.mark.unit
def test_cli_analyze_slow_query_with_explain(monkeypatch):
    from postgres_aiops.cli import analyze as cli_analyze
    from postgres_aiops.cli import app

    conn = FakePg({
        "FROM pg_stat_statements": [{"queryid": 2, "query": "SELECT big", "calls": 30,
                                     "total_exec_time_ms": 9000, "mean_exec_time_ms": 300,
                                     "shared_blks_hit": 80, "shared_blks_read": 20,
                                     "temp_blks_written": 100}],
        "EXPLAIN": [{"QUERY PLAN": [{"Plan": {"Node Type": "Seq Scan"}}]}],
    })
    _patch_conn(monkeypatch, cli_analyze, conn)
    out = _out_json(runner.invoke(app, ["analyze", "slow-query", "--explain", "SELECT * FROM t"]))
    assert out["worst"]["queryId"] == 2


@pytest.mark.unit
def test_cli_analyze_bloat_and_blocking(monkeypatch):
    from postgres_aiops.cli import analyze as cli_analyze
    from postgres_aiops.cli import app

    conn = FakePg({
        "FROM pg_stat_user_tables": [{"schema": "public", "table": "hot", "n_live_tup": 6000,
                                      "n_dead_tup": 4000, "dead_pct": 40.0, "size_bytes": 8192,
                                      "last_autovacuum": None}],
        "pg_blocking_pids": [{"blocked_pid": 200, "blocking_pid": 100,
                              "blocking_query": "UPDATE a"}],
    })
    _patch_conn(monkeypatch, cli_analyze, conn)
    bloat = _out_json(runner.invoke(app, ["analyze", "bloat-vacuum"]))
    assert bloat["needsAttentionCount"] == 1
    blocking = _out_json(runner.invoke(app, ["analyze", "blocking"]))
    assert blocking["worstRootPid"] == 100


# ── overview ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cli_overview(monkeypatch):
    from postgres_aiops.cli import app
    from postgres_aiops.cli import overview as cli_overview

    conn = FakePg({"FROM pg_stat_activity": [{"pid": 1, "state": "active", "query": "x"}]})
    _patch_conn(monkeypatch, cli_overview, conn)
    out = _out_json(runner.invoke(app, ["overview"]))
    assert out["totalConnections"] == 1
