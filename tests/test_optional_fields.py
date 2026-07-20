"""Absent fields come back as null, not as an empty string.

PostgreSQL catalogs are full of columns whose NULL carries meaning:
``last_autovacuum`` is NULL because the table was *never* autovacuumed,
``pg_settings.unit`` is NULL because the setting is not a numeric quantity,
``pg_replication_slots.database`` is NULL because the slot is physical. An empty
string reads as "this field exists and is empty" — a different fact. Collapsing
the two hides information from any consumer, and a smaller local model will
confidently invent the difference. These tests pin the contract end-to-end:
helper, ops layer, and the CLI rendering that has to cope with a null.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from postgres_aiops.governance import opt_str
from postgres_aiops.ops import activity, replication, server, tables
from tests.conftest import FakePg

runner = CliRunner()


# ── the helper itself ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_opt_str_distinguishes_absent_from_empty():
    assert opt_str(None) is None, "absent must stay absent"
    assert opt_str("") == "", "a genuinely empty value is not the same as absent"
    assert opt_str("public.orders", 64) == "public.orders"


@pytest.mark.unit
def test_opt_str_still_sanitizes_and_truncates():
    assert opt_str("a\x00b") == "ab"  # control character stripped
    # A cut announces itself: the ellipsis is the only signal a reader gets
    # that what they are looking at is not the whole value.
    assert opt_str("abcdef", 3) == "ab\u2026"
    assert opt_str("abc", 3) == "abc"  # exactly at the cap is not truncated


@pytest.mark.unit
def test_opt_str_accepts_non_string_values():
    assert opt_str(42) == "42"


@pytest.mark.unit
def test_ops_opt_helper_wraps_opt_str():
    from postgres_aiops.ops._util import opt, s

    assert opt(None) is None
    assert s(None) == "", "s() still folds NULL into '' for always-present columns"


# ── ops layer: real nullable catalog columns ─────────────────────────────────


@pytest.mark.unit
def test_never_autovacuumed_table_reports_null_not_empty_string():
    """The canonical case: last_autovacuum IS NULL means 'never', not ''."""
    conn = FakePg({"FROM pg_stat_user_tables": [
        {"schema": "public", "table": "orders", "n_live_tup": 10, "n_dead_tup": 500,
         "dead_pct": 98.0, "size_bytes": 4096},
    ]})
    row = tables.table_bloat(conn)["tables"][0]
    assert row["lastAutovacuum"] is None
    assert row["lastVacuum"] is None
    assert row["lastAnalyze"] is None


@pytest.mark.unit
def test_background_worker_backend_reports_null_user_and_database():
    """pg_stat_activity NULLs usename/datname/state for background workers."""
    conn = FakePg({"FROM pg_stat_activity": [
        {"pid": 7, "backend_type": "autovacuum launcher"},
    ]})
    row = activity.list_activity(conn)["sessions"][0]
    assert row["pid"] == 7
    assert row["username"] is None
    assert row["database"] is None
    assert row["state"] is None
    assert row["clientAddr"] is None, "a unix-socket backend has no client_addr"
    assert row["waitEvent"] is None, "a backend that is not waiting has no wait_event"


@pytest.mark.unit
def test_physical_replication_slot_reports_null_plugin_and_database():
    """A physical slot has no output plugin and no database — both NULL."""
    conn = FakePg({"FROM pg_replication_slots": [
        {"slot_name": "standby1", "slot_type": "physical", "active": True},
    ]})
    row = replication.replication_slots(conn)["slots"][0]
    assert row["slotName"] == "standby1"
    assert row["plugin"] is None
    assert row["database"] is None
    assert row["restartLsn"] is None


@pytest.mark.unit
def test_non_numeric_setting_reports_null_unit():
    """pg_settings.unit is NULL for a setting that is not a quantity."""
    conn = FakePg({"FROM pg_settings": [
        {"name": "wal_level", "setting": "replica", "context": "postmaster"},
    ]})
    row = server.show_settings(conn)[0]
    assert row["setting"] == "replica"
    assert row["unit"] is None
    assert row["description"] is None


@pytest.mark.unit
def test_empty_string_from_the_server_is_preserved_not_nulled():
    """An explicitly empty upstream value stays '' — it is not turned into null."""
    conn = FakePg({"FROM pg_stat_activity": [
        {"pid": 9, "application_name": "", "state": "idle"},
    ]})
    row = activity.list_activity(conn)["sessions"][0]
    assert row["applicationName"] == "", "'' is a real value, distinct from absent"
    assert row["state"] == "idle"


@pytest.mark.unit
def test_ops_never_drop_the_key_itself():
    """Keys are always present; only their value may be null.

    Omitting a key entirely is worse than a null — the consumer cannot tell the
    field was even considered.
    """
    conn = FakePg({"FROM pg_stat_activity": [{}]})
    row = activity.list_activity(conn)["sessions"][0]
    for key in ("pid", "username", "database", "clientAddr", "applicationName",
                "state", "waitEventType", "waitEvent", "backendType", "query"):
        assert key in row, f"{key} must be present even when the source omitted it"


# ── consumers that must survive a null ───────────────────────────────────────


@pytest.mark.unit
def test_state_grouping_survives_a_null_state():
    """byState buckets a NULL state under 'unknown' rather than raising."""
    conn = FakePg({"FROM pg_stat_activity": [
        {"pid": 1, "backend_type": "checkpointer"},
        {"pid": 2, "state": "active"},
    ]})
    out = activity.list_activity(conn)
    assert out["byState"]["unknown"] == 1
    assert out["byState"]["active"] == 1


@pytest.mark.unit
def test_vacuum_analysis_still_flags_a_never_autovacuumed_table():
    """The RCA reads lastAutovacuum; a null must still mean 'never'."""
    from postgres_aiops.ops import analysis

    conn = FakePg({"FROM pg_stat_user_tables": [
        {"schema": "public", "table": "orders", "n_live_tup": 10, "n_dead_tup": 5000,
         "dead_pct": 99.0, "size_bytes": 4096},
    ]})
    rows = tables.table_bloat(conn)["tables"]
    assert rows[0]["lastAutovacuum"] is None
    out = analysis.bloat_and_vacuum_analysis(rows)
    assert out["needsAttentionCount"] == 1
    rec = out["recommendations"][0]
    assert rec["lastAutovacuum"] is None
    assert any("never autovacuumed" in r for r in rec["reasons"])


@pytest.mark.unit
def test_extension_update_check_survives_a_null_default_version():
    """LEFT JOIN misses leave default_version NULL — updateAvailable must not raise."""
    conn = FakePg({"FROM pg_extension": [
        {"name": "pg_stat_statements", "installed_version": "1.10"},
    ]})
    row = server.list_extensions(conn)[0]
    assert row["defaultVersion"] is None
    assert row["updateAvailable"] is False


@pytest.mark.unit
def test_cli_renders_rows_with_null_fields(monkeypatch):
    """The CLI must emit valid JSON with nulls rather than crashing on render."""
    from postgres_aiops.cli import activity as cli_activity
    from postgres_aiops.cli import app

    conn = FakePg({"FROM pg_stat_activity": [{"pid": 4242}]})
    monkeypatch.setattr(
        cli_activity, "get_connection", lambda target=None: (conn, None)
    )

    result = runner.invoke(app, ["activity", "list"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    session = payload["sessions"][0]
    assert session["pid"] == 4242
    assert session["username"] is None, "null must survive JSON serialisation"
