"""CLI confirmed-write path — past dry-run, through governance, onto disk.

The CLI write commands delegate real execution to the ``@governed_tool``
functions in ``mcp_server.tools``. These tests drive a write command PAST the
dry-run branch and the double-confirm prompts and assert the call really went
through the governed path (audit row on disk) — the regression test for the
"CLI writes were unaudited" line-wide fix.

The dry-run branch now routes through that same governed twin, so it too lands
an audit row. The invariant a preview must hold is not "makes no call" but
**a dry_run MAY read; it must never write** — for this SQL tool, never a
statement that mutates.
"""

from __future__ import annotations

import sqlite3

import pytest
from typer.testing import CliRunner

import postgres_aiops.governance.audit as audit_mod
import postgres_aiops.governance.policy as policy_mod
import postgres_aiops.governance.undo as undo_mod


@pytest.fixture
def gov_home(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTGRES_AIOPS_HOME", str(tmp_path))
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()
    yield tmp_path
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()


def _audit_tools(db_path) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return [r[0] for r in conn.execute("SELECT tool FROM audit_log ORDER BY id")]
    finally:
        conn.close()


@pytest.mark.unit
def test_cli_query_reset_dry_run_never_writes_but_is_audited(gov_home, monkeypatch, fake_pg):
    """A dry_run MAY read; it must never write — and it IS audited.

    The preview routes through the governed twin, so it lands an audit row
    exactly as the MCP path has always done. What it must never do is mutate:
    for this SQL tool that means no statement that mutates, i.e. neither an
    ``execute`` nor a ``pg_stat_statements_reset()`` on the read channel.
    """
    from postgres_aiops.cli import app

    fake = fake_pg()
    import mcp_server.tools.queries as gov_queries

    monkeypatch.setattr(gov_queries, "_get_connection", lambda target=None: fake)
    result = CliRunner().invoke(app, ["query", "reset", "--dry-run"])
    assert result.exit_code == 0
    assert "DRY-RUN" in result.output
    assert fake.executed == [], f"a dry-run must never execute a statement: {fake.executed}"
    assert [sql for sql, _ in fake.queried if "pg_stat_statements_reset" in sql] == []
    assert _audit_tools(gov_home / "audit.db") == ["reset_query_stats"]


@pytest.mark.unit
def test_cli_query_reset_confirmed_goes_through_governance(gov_home, monkeypatch, fake_pg):
    """Confirmed CLI write must execute via the governed twin: the SQL runs
    AND an audit row lands in audit.db (this is what the reroute fix bought)."""
    from postgres_aiops.cli import app

    fake = fake_pg(responses={"pg_stat_statements_reset": [{"reset": True}]})
    import mcp_server.tools.queries as gov_queries

    monkeypatch.setattr(gov_queries, "_get_connection", lambda target=None: fake)
    result = CliRunner().invoke(app, ["query", "reset"], input="y\ny\n")
    assert result.exit_code == 0, result.output
    assert _audit_tools(gov_home / "audit.db") == ["reset_query_stats"]


@pytest.mark.unit
def test_cli_query_reset_aborts_without_double_confirm(gov_home, monkeypatch, fake_pg):
    from postgres_aiops.cli import app

    fake = fake_pg()
    import mcp_server.tools.queries as gov_queries

    monkeypatch.setattr(gov_queries, "_get_connection", lambda target=None: fake)
    result = CliRunner().invoke(app, ["query", "reset"], input="y\nn\n")
    assert result.exit_code != 0
    assert fake.executed == [] and fake.queried == []
    assert not (gov_home / "audit.db").exists()


# ── refusals must teach, not traceback ────────────────────────────────────────
#
# ``PolicyDenied``/``BudgetExceeded`` are raised by ``@governed_tool`` OUTSIDE the
# tool body, so ``tool_errors`` never flattens them into ``{"error": ...}`` and
# ``dry_run_preview``'s dict check cannot see them. Before they were listed in
# ``_cli_error_types`` a refused preview reached the operator as a raw traceback:
# the teaching text was in there, buried under a stack dump. A weak model reads
# that as a crash and retries — the very loop the preview reroute exists to stop.


def test_cli_error_types_covers_governance_refusals() -> None:
    """A governance refusal must be translated, not dumped as a traceback."""
    from postgres_aiops.cli._common import _cli_error_types
    from postgres_aiops.governance import BudgetExceeded, PolicyDenied

    types = _cli_error_types()
    assert PolicyDenied in types
    assert BudgetExceeded in types
