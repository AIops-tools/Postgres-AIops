"""Smoke + harness tests for postgres-aiops.

Proves: every module imports, the CLI Typer app builds and --help works, the MCP
server exposes the expected tools, EVERY MCP tool carries the harness marker
``_is_governed_tool``, the write tools have the correct risk tiers and dry-run
gating, and undo is recorded through the harness from the REAL fetched
before-state. No live PostgreSQL is needed — the connection is faked.
"""

import asyncio
import importlib

import pytest
from typer.testing import CliRunner

from tests.conftest import FakePg

EXPECTED_TOOLS = {
    # server
    "overview", "server_version", "show_settings", "list_extensions",
    "list_databases", "list_roles",
    # activity
    "list_activity", "long_running_queries", "list_locks",
    # queries
    "top_queries", "explain_query", "reset_query_stats",
    # indexes
    "unused_indexes", "missing_index_hints", "index_bloat", "invalid_indexes",
    # tables
    "table_sizes", "table_bloat", "autovacuum_status",
    # replication
    "replication_status", "replication_slots", "wal_status",
    # flagship
    "slow_query_rca", "bloat_and_vacuum_analysis", "blocking_lock_chain_rca",
    # writes
    "reset_query_stats", "terminate_backend", "cancel_query", "run_vacuum",
    "run_analyze", "create_index", "drop_index", "reindex", "update_setting",
    # undo executor
    "undo_list", "undo_apply",
}

WRITE_RISK = {
    "terminate_backend": "high",
    "cancel_query": "high",
    "drop_index": "high",
    "run_vacuum": "medium",
    "run_analyze": "medium",
    "create_index": "medium",
    "reindex": "medium",
    "update_setting": "medium",
    "reset_query_stats": "medium",
}


@pytest.mark.unit
def test_all_modules_import():
    for name in (
        "postgres_aiops", "postgres_aiops.config", "postgres_aiops.connection",
        "postgres_aiops.doctor", "postgres_aiops.secretstore",
        "postgres_aiops.ops.server", "postgres_aiops.ops.activity",
        "postgres_aiops.ops.queries", "postgres_aiops.ops.indexes",
        "postgres_aiops.ops.tables", "postgres_aiops.ops.replication",
        "postgres_aiops.ops.analysis", "postgres_aiops.ops.remediation",
        "postgres_aiops.ops.overview",
        "postgres_aiops.cli", "postgres_aiops.cli._root", "postgres_aiops.cli._common",
        "postgres_aiops.cli.init", "postgres_aiops.cli.secret",
        "mcp_server.server", "mcp_server._shared",
        "mcp_server.tools.server", "mcp_server.tools.activity",
        "mcp_server.tools.queries", "mcp_server.tools.indexes",
        "postgres_aiops.cli.undo", "mcp_server.tools.undo",
        "mcp_server.tools.tables", "mcp_server.tools.replication",
        "mcp_server.tools.analysis", "mcp_server.tools.remediation",
    ):
        importlib.import_module(name)


@pytest.mark.unit
def test_version_matches_pyproject():
    """__version__ is single-sourced from package metadata; it must track
    pyproject.toml so a release bump can never ship a stale self-report."""
    import tomllib
    from pathlib import Path

    import postgres_aiops

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    expected = tomllib.loads(pyproject.read_text("utf-8"))["project"]["version"]
    assert postgres_aiops.__version__ == expected


@pytest.mark.unit
def test_cli_app_builds_and_help_works():
    from postgres_aiops.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for sub in ("server", "activity", "query", "index", "table", "repl",
                "analyze", "remediate", "secret", "init", "overview", "doctor", "mcp"):
        assert sub in result.output


@pytest.mark.unit
def test_cli_leaf_help_triggers_lazy_imports():
    from postgres_aiops.cli import app

    runner = CliRunner()
    for cmd in (
        ["server", "--help"], ["activity", "--help"], ["query", "--help"],
        ["index", "--help"], ["table", "--help"], ["repl", "--help"],
        ["analyze", "--help"], ["remediate", "--help"], ["secret", "--help"],
        ["server", "version", "--help"], ["activity", "long", "--help"],
        ["query", "top", "--help"], ["query", "explain", "--help"],
        ["index", "unused", "--help"], ["table", "bloat", "--help"],
        ["analyze", "slow-query", "--help"], ["remediate", "vacuum", "--help"],
        ["remediate", "drop-index", "--help"], ["remediate", "set", "--help"],
        ["overview", "--help"], ["init", "--help"],
    ):
        result = runner.invoke(app, cmd)
        assert result.exit_code == 0, f"{cmd} failed: {result.output}"


@pytest.mark.unit
def test_mcp_list_tools_exposes_expected_tools():
    from mcp_server.server import mcp

    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert EXPECTED_TOOLS <= names, f"missing: {EXPECTED_TOOLS - names}"


@pytest.mark.unit
def test_every_mcp_tool_is_governed_by_harness():
    from mcp_server import _shared

    tool_objs = _shared.mcp._tool_manager._tools
    assert len(tool_objs) == 35, (
        "tool count changed — update README/SKILL/server.json too"
    )
    assert EXPECTED_TOOLS <= set(tool_objs), "tool registry incomplete"
    for name, tool in tool_objs.items():
        fn = getattr(tool, "fn", None)
        assert fn is not None, f"{name} has no fn"
        assert getattr(fn, "_is_governed_tool", False), (
            f"{name} is not wrapped with @governed_tool (harness marker missing)"
        )


@pytest.mark.unit
def test_write_tools_have_correct_risk_tiers():
    from mcp_server.tools import queries as q
    from mcp_server.tools import remediation as rem

    assert q.reset_query_stats._risk_level == "medium"
    for tool_name, expected in WRITE_RISK.items():
        if tool_name == "reset_query_stats":
            continue
        assert getattr(rem, tool_name)._risk_level == expected, tool_name


@pytest.mark.unit
def test_drop_index_records_undo_from_captured_indexdef(monkeypatch):
    """drop_index through the harness records an inverse recreate from pg_get_indexdef."""
    import postgres_aiops.governance.undo as undo_mod
    from mcp_server.tools import remediation as rem

    indexdef = "CREATE INDEX idx_orders_cid ON public.orders USING btree (customer_id)"
    conn = FakePg({}, {"pg_get_indexdef": indexdef})
    monkeypatch.setattr(rem, "_get_connection", lambda target=None: conn)

    recorded = {}

    class _Store:
        def record(self, *, skill, tool, undo_descriptor, orig_params, effect_verified=True):
            recorded["descriptor"] = undo_descriptor
            return "undo-1"

    monkeypatch.setattr(undo_mod, "get_undo_store", lambda: _Store())

    result = rem.drop_index(name="idx_orders_cid")
    assert "error" not in result
    assert recorded["descriptor"]["tool"] == "create_index"
    assert recorded["descriptor"]["params"]["definition"] == indexdef  # the captured prior def
    assert result.get("_undo_id") == "undo-1"


@pytest.mark.unit
def test_create_index_undo_drops_created_name(monkeypatch):
    """create_index through the harness records an inverse that drops the new index."""
    import postgres_aiops.governance.undo as undo_mod
    from mcp_server.tools import remediation as rem

    conn = FakePg()
    monkeypatch.setattr(rem, "_get_connection", lambda target=None: conn)

    recorded = {}

    class _Store:
        def record(self, *, skill, tool, undo_descriptor, orig_params, effect_verified=True):
            recorded["descriptor"] = undo_descriptor
            return "undo-2"

    monkeypatch.setattr(undo_mod, "get_undo_store", lambda: _Store())

    result = rem.create_index(table="public.orders", columns=["customer_id"], name="idx_new")
    assert "error" not in result
    assert recorded["descriptor"]["tool"] == "drop_index"
    assert recorded["descriptor"]["params"]["name"] == "idx_new"


@pytest.mark.unit
def test_dry_run_gates_destructive_cli():
    """remediate drop-index --dry-run must not touch the connection."""
    from postgres_aiops.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["remediate", "drop-index", "idx_x", "--dry-run"])
    assert result.exit_code == 0
    assert "DRY-RUN" in result.output


@pytest.mark.unit
def test_dry_run_mcp_write_does_not_execute(monkeypatch):
    from mcp_server.tools import remediation as rem

    conn = FakePg()
    monkeypatch.setattr(rem, "_get_connection", lambda target=None: conn)
    out = rem.run_vacuum(table="public.orders", dry_run=True)
    assert out.get("dryRun") is True
    assert conn.executed == []
