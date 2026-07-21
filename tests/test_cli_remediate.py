"""CLI ``remediate`` sub-commands — dry-run previews and error translation.

Every ``--dry-run`` routes its preview through the ``@governed_tool``-wrapped
twin in ``mcp_server.tools``, so the guards the real write would hit run against
the real target and an audit row lands for the preview itself. A preview of a
call that will be refused must report the refusal rather than a green banner:
otherwise the caller reads the later refusal as transient and retries.

The surviving invariant is **a dry_run MAY read; it must never write**. For a
SQL tool the mutating call is a statement that mutates, not an HTTP verb — DDL
and maintenance go through ``execute``, while the two activity-control writes
mutate through ``scalar("SELECT pg_terminate_backend(…)")``. Both channels are
asserted, so a preview cannot slip a write through either one.

These tests drive every remediate command with ``--dry-run`` and assert the
preview text; the confirmed (governed) write path is covered end-to-end in
test_cli_writes.py.
"""

from __future__ import annotations

import sqlite3

import pytest
from typer.testing import CliRunner

import postgres_aiops.governance.audit as audit_mod
import postgres_aiops.governance.policy as policy_mod
import postgres_aiops.governance.undo as undo_mod
from tests.conftest import FakePg

runner = CliRunner()

_OWN_PID = 1234

# SQL functions that mutate while travelling the read channel (``scalar``).
# Everything else that mutates goes through ``execute``.
_MUTATING_VIA_SCALAR = ("pg_terminate_backend", "pg_cancel_backend")


@pytest.fixture(autouse=True)
def gov_home(tmp_path, monkeypatch):
    """Bind audit/undo state to a throwaway home.

    Previews are governed calls now, so they persist audit rows — without this
    the suite would write into the developer's real ~/.postgres-aiops.
    """
    monkeypatch.setenv("POSTGRES_AIOPS_HOME", str(tmp_path))
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()
    yield tmp_path
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()


@pytest.fixture
def guarded_conn(monkeypatch):
    """Wire the governed remediation tools to a fake whose own backend pid is known."""
    import mcp_server.tools.remediation as gov

    fake = FakePg(
        {"FROM pg_stat_activity": [{"pid": 42, "username": "app"}],
         "FROM pg_settings": [
             {"setting": "4MB", "unit": "kB", "context": "user", "pending_restart": False},
         ]},
        scalars={"pg_backend_pid()": _OWN_PID, "pg_terminate_backend": True,
                 "pg_cancel_backend": True},
    )
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: fake)
    return fake


def _assert_no_mutation(fake: FakePg) -> None:
    """A dry_run MAY read; it must never write."""
    assert fake.executed == [], f"a dry-run must never execute a statement: {fake.executed}"
    mutating = [
        sql for sql, _ in fake.queried
        if any(fn in sql for fn in _MUTATING_VIA_SCALAR)
    ]
    assert mutating == [], f"a dry-run must never call a mutating function: {mutating}"


def _audit_tools(gov_home) -> list[str]:
    conn = sqlite3.connect(gov_home / "audit.db")
    try:
        return [r[0] for r in conn.execute("SELECT tool FROM audit_log ORDER BY id")]
    finally:
        conn.close()


def _dry(args: list[str]):
    from postgres_aiops.cli import app

    result = runner.invoke(app, [*args, "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "DRY-RUN" in result.output
    return result.output


@pytest.mark.unit
def test_terminate_dry_run_previews_pid(guarded_conn, gov_home):
    out = _dry(["remediate", "terminate", "42"])
    assert "terminate_backend" in out and "pid = 42" in out
    _assert_no_mutation(guarded_conn)
    assert _audit_tools(gov_home) == ["terminate_backend"]


@pytest.mark.unit
def test_terminate_dry_run_reports_a_self_targeted_refusal(guarded_conn):
    """The preview must not show a green banner for a call that will be refused."""
    from postgres_aiops.cli import app

    result = runner.invoke(app, ["remediate", "terminate", str(_OWN_PID), "--dry-run"])
    assert result.exit_code == 1, result.output
    assert "DRY-RUN" not in result.output
    assert "calling through" in result.output
    _assert_no_mutation(guarded_conn)


@pytest.mark.unit
def test_cancel_dry_run_previews_pid(guarded_conn, gov_home):
    out = _dry(["remediate", "cancel", "7"])
    assert "cancel_query" in out and "pid = 7" in out
    _assert_no_mutation(guarded_conn)
    assert _audit_tools(gov_home) == ["cancel_query"]


@pytest.mark.unit
def test_vacuum_dry_run_previews_flags(guarded_conn, gov_home):
    out = _dry(["remediate", "vacuum", "public.orders", "--full", "--analyze"])
    assert "run_vacuum" in out and "VACUUM public.orders" in out
    assert "full = True" in out and "analyze = True" in out
    _assert_no_mutation(guarded_conn)
    assert _audit_tools(gov_home) == ["run_vacuum"]


@pytest.mark.unit
def test_analyze_table_dry_run(guarded_conn, gov_home):
    out = _dry(["remediate", "analyze-table", "orders"])
    assert "run_analyze" in out and "ANALYZE orders" in out
    _assert_no_mutation(guarded_conn)
    assert _audit_tools(gov_home) == ["run_analyze"]


@pytest.mark.unit
def test_create_index_dry_run_previews_columns(guarded_conn, gov_home):
    out = _dry(["remediate", "create-index", "orders", "customer_id", "--unique"])
    assert "create_index" in out and "customer_id" in out and "unique = True" in out
    _assert_no_mutation(guarded_conn)
    assert _audit_tools(gov_home) == ["create_index"]


@pytest.mark.unit
def test_drop_index_dry_run(guarded_conn, gov_home):
    out = _dry(["remediate", "drop-index", "idx_orders_cid"])
    assert "drop_index" in out and "DROP INDEX idx_orders_cid" in out
    _assert_no_mutation(guarded_conn)
    assert _audit_tools(gov_home) == ["drop_index"]


@pytest.mark.unit
def test_drop_index_dry_run_reports_an_unreachable_target(monkeypatch):
    """A preview that cannot even reach the server must say so, not print a banner.

    Before the reroute this printed a confident green DRY-RUN for a target the
    CLI had never contacted — the exact green-preview-then-refusal trap.
    """
    import mcp_server.tools.remediation as gov
    from postgres_aiops.cli import app
    from postgres_aiops.connection import PgError

    def _boom(target=None):
        raise PgError("could not connect to server: Connection refused")

    monkeypatch.setattr(gov, "_get_connection", _boom)
    result = runner.invoke(app, ["remediate", "drop-index", "idx_x", "--dry-run"])
    assert result.exit_code == 1, result.output
    assert "DRY-RUN" not in result.output
    assert "Connection refused" in result.output


@pytest.mark.unit
def test_reindex_dry_run(guarded_conn, gov_home):
    out = _dry(["remediate", "reindex", "public.orders", "--kind", "TABLE"])
    assert "reindex" in out and "REINDEX TABLE public.orders" in out
    _assert_no_mutation(guarded_conn)
    assert _audit_tools(gov_home) == ["reindex"]


@pytest.mark.unit
def test_set_dry_run_previews_value(guarded_conn, gov_home):
    out = _dry(["remediate", "set", "work_mem", "64MB"])
    assert "update_setting" in out and "ALTER SYSTEM SET work_mem" in out
    assert "value = 64MB" in out
    _assert_no_mutation(guarded_conn)
    assert _audit_tools(gov_home) == ["update_setting"]


@pytest.mark.unit
def test_set_dry_run_reports_a_denylisted_setting(guarded_conn):
    """max_connections=1 strands the undo at the operator's next restart."""
    from postgres_aiops.cli import app

    result = runner.invoke(app, ["remediate", "set", "max_connections", "1", "--dry-run"])
    assert result.exit_code == 1, result.output
    assert "DRY-RUN" not in result.output
    assert "postgresql.conf" in result.output
    _assert_no_mutation(guarded_conn)


@pytest.mark.unit
def test_remediate_aborts_without_double_confirm():
    """Answering 'n' to the second confirm aborts with a non-zero exit and
    never reaches the governed twin."""
    from postgres_aiops.cli import app

    result = runner.invoke(app, ["remediate", "terminate", "42"], input="y\nn\n")
    assert result.exit_code != 0
