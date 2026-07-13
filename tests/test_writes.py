"""Write-path ops tests: before-state capture, identifier safety, SQL shape.

No live database — ``FakePg`` records executed statements and serves canned
before-state rows, so the guarded writes are verified offline.
"""

from __future__ import annotations

import pytest

from postgres_aiops.ops import _util
from postgres_aiops.ops import remediation as ops
from tests.conftest import FakePg

# ── identifier safety ───────────────────────────────────────────────────────


@pytest.mark.unit
def test_qualify_quotes_each_part():
    assert _util.qualify("public.orders") == '"public"."orders"'
    assert _util.qualify("orders") == '"orders"'


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["orders; DROP TABLE users", "a.b.c", "o'rders", "1foo"])
def test_qualify_rejects_injection(bad):
    with pytest.raises(ValueError):
        _util.qualify(bad)


@pytest.mark.unit
def test_quote_literal_doubles_quotes():
    assert _util.quote_literal("64M") == "'64M'"
    assert _util.quote_literal("a'b") == "'a''b'"


# ── activity control captures before-state ──────────────────────────────────


@pytest.mark.unit
def test_terminate_backend_captures_prior_and_calls_terminate():
    conn = FakePg(
        {"FROM pg_stat_activity": [{"pid": 42, "username": "app", "database": "db",
                                    "state": "active", "query": "SELECT bad()"}]},
        {"pg_terminate_backend": True},
    )
    out = ops.terminate_backend(conn, 42)
    assert out["action"] == "terminate_backend"
    assert out["terminated"] is True
    assert out["priorState"]["query"] == "SELECT bad()"


@pytest.mark.unit
def test_cancel_query_captures_prior():
    conn = FakePg(
        {"FROM pg_stat_activity": [{"pid": 7, "query": "SELECT pg_sleep(999)"}]},
        {"pg_cancel_backend": True},
    )
    out = ops.cancel_query(conn, 7)
    assert out["action"] == "cancel_query" and out["cancelled"] is True


# ── vacuum / analyze ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_run_vacuum_builds_options_and_captures_stats():
    conn = FakePg({"FROM pg_stat_user_tables": [
        {"n_dead_tup": 100, "n_live_tup": 900, "last_vacuum": None},
    ]})
    out = ops.run_vacuum(conn, "public.orders", full=True, analyze=True)
    assert out["priorState"]["deadTuples"] == 100
    sql, _ = conn.executed[0]
    assert sql == 'VACUUM (FULL, ANALYZE) "public"."orders"'


@pytest.mark.unit
def test_run_analyze_quotes_identifier():
    conn = FakePg({"FROM pg_stat_user_tables": [{"last_analyze": None}]})
    ops.run_analyze(conn, "orders")
    sql, _ = conn.executed[0]
    assert sql == 'ANALYZE "orders"'


# ── index create/drop ───────────────────────────────────────────────────────


@pytest.mark.unit
def test_create_index_builds_statement_and_returns_name():
    conn = FakePg()
    out = ops.create_index(conn, "public.orders", ["customer_id", "created_at"],
                           concurrently=True)
    assert out["action"] == "create_index"
    sql, _ = conn.executed[0]
    assert sql.startswith("CREATE INDEX CONCURRENTLY ")
    assert '"public"."orders"' in sql
    assert '"customer_id", "created_at"' in sql


@pytest.mark.unit
def test_create_index_rejects_bad_column():
    conn = FakePg()
    with pytest.raises(ValueError):
        ops.create_index(conn, "orders", ["id); DROP TABLE t; --"])


@pytest.mark.unit
def test_create_index_rejects_unknown_method():
    conn = FakePg()
    with pytest.raises(ValueError, match="method"):
        ops.create_index(conn, "orders", ["id"], method="magic")


@pytest.mark.unit
def test_drop_index_captures_indexdef_before_dropping():
    indexdef = "CREATE INDEX idx_orders_cid ON public.orders USING btree (customer_id)"
    conn = FakePg({}, {"pg_get_indexdef": indexdef})
    out = ops.drop_index(conn, "idx_orders_cid")
    assert out["priorState"]["indexdef"] == indexdef
    sql, _ = conn.executed[0]
    assert sql == 'DROP INDEX "idx_orders_cid"'


@pytest.mark.unit
def test_drop_index_raises_when_not_found():
    conn = FakePg({}, {})  # scalar returns None → no definition
    with pytest.raises(ValueError, match="not found"):
        ops.drop_index(conn, "nope")


# ── reindex / settings ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_reindex_whitelists_kind():
    conn = FakePg()
    ops.reindex(conn, "public.orders", kind="table")
    sql, _ = conn.executed[0]
    assert sql == 'REINDEX TABLE "public"."orders"'
    with pytest.raises(ValueError, match="kind"):
        ops.reindex(conn, "orders", kind="EVERYTHING")


@pytest.mark.unit
def test_update_setting_captures_prior_and_quotes_value():
    conn = FakePg({"FROM pg_settings": [
        {"setting": "4MB", "unit": None, "context": "user", "pending_restart": False},
    ]})
    out = ops.update_setting(conn, "work_mem", "64MB")
    assert out["priorState"]["value"] == "4MB"
    sql, _ = conn.executed[0]
    assert sql == "ALTER SYSTEM SET work_mem = '64MB'"


@pytest.mark.unit
def test_update_setting_rejects_bad_name():
    conn = FakePg()
    with pytest.raises(ValueError, match="setting name"):
        ops.update_setting(conn, "work_mem; DROP", "1")
