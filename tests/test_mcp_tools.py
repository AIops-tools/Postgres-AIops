"""MCP tool wrappers — dry-run previews, live-pull branches, and undo builders.

These exercise the ``mcp_server/tools`` layer that the ops tests don't reach: the
governed-twin ``dry_run`` short-circuits, the "pull live when not injected"
branches (which resolve a connection through ``_get_connection``), the
argument-validation guards on ``create_index``, and the three pure undo-descriptor
factories that turn a captured before-state into an inverse call.

Each governed call is bound to a throwaway ``POSTGRES_AIOPS_HOME`` so audit/undo
rows land in a tmp dir, never the real ``~/.postgres-aiops``.
"""

from __future__ import annotations

import pytest

import postgres_aiops.governance.audit as audit_mod
import postgres_aiops.governance.policy as policy_mod
import postgres_aiops.governance.undo as undo_mod
from tests.conftest import FakePg


def _reset() -> None:
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()


@pytest.fixture
def gov_home(tmp_path, monkeypatch):
    """Isolate the harness to a tmp home; keep a synthetic approver so the
    high-risk dry-run previews are allowed to run."""
    monkeypatch.setenv("POSTGRES_AIOPS_HOME", str(tmp_path))
    monkeypatch.setenv("POSTGRES_AUDIT_APPROVED_BY", "pytest")
    _reset()
    yield tmp_path
    _reset()


# ── pure undo-descriptor factories ───────────────────────────────────────────


@pytest.mark.unit
def test_create_index_undo_inverts_to_drop():
    from mcp_server.tools.remediation import _create_index_undo

    desc = _create_index_undo({}, {"index": "idx_orders_cid"})
    assert desc["tool"] == "drop_index"
    assert desc["params"] == {"name": "idx_orders_cid"}
    assert desc["skill"] == "postgres-aiops"


@pytest.mark.unit
def test_create_index_undo_none_when_no_index():
    from mcp_server.tools.remediation import _create_index_undo

    assert _create_index_undo({}, {"index": ""}) is None
    assert _create_index_undo({}, "not-a-dict") is None


@pytest.mark.unit
def test_drop_index_undo_recreates_from_captured_definition():
    from mcp_server.tools.remediation import _drop_index_undo

    indexdef = "CREATE INDEX idx_t ON public.t USING btree (c)"
    desc = _drop_index_undo({}, {"priorState": {"indexdef": indexdef}})
    assert desc["tool"] == "create_index"
    assert desc["params"] == {"definition": indexdef}


@pytest.mark.unit
def test_drop_index_undo_none_without_definition():
    from mcp_server.tools.remediation import _drop_index_undo

    assert _drop_index_undo({}, {"priorState": {}}) is None
    assert _drop_index_undo({}, "nope") is None


@pytest.mark.unit
def test_update_setting_undo_sets_back_prior_value():
    from mcp_server.tools.remediation import _update_setting_undo

    desc = _update_setting_undo({"name": "work_mem"}, {"priorState": {"value": "4MB"}})
    assert desc["tool"] == "update_setting"
    assert desc["params"] == {"name": "work_mem", "value": "4MB"}


@pytest.mark.unit
@pytest.mark.parametrize("result", [{"priorState": {"value": ""}}, {"priorState": {}}, "x"])
def test_update_setting_undo_none_when_no_prior(result):
    from mcp_server.tools.remediation import _update_setting_undo

    assert _update_setting_undo({"name": "work_mem"}, result) is None


# ── remediation dry-run previews (no execution, no undo) ─────────────────────


@pytest.mark.unit
def test_terminate_and_cancel_dry_run(gov_home, monkeypatch):
    from mcp_server.tools import remediation as gov

    fake = FakePg()
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: fake)
    term = gov.terminate_backend(pid=42, dry_run=True)
    assert term["dryRun"] is True and term["wouldTerminate"] == {"pid": 42}
    cancel = gov.cancel_query(pid=7, dry_run=True)
    assert cancel["wouldCancel"] == {"pid": 7}
    # a preview must not execute any SQL
    assert fake.executed == []


@pytest.mark.unit
def test_vacuum_analyze_reindex_setting_dry_run(gov_home, monkeypatch):
    from mcp_server.tools import remediation as gov

    fake = FakePg()
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: fake)
    assert gov.run_vacuum(table="t", full=True, dry_run=True)["wouldVacuum"]["full"] is True
    assert gov.run_analyze(table="t", dry_run=True)["wouldAnalyze"] == {"table": "t"}
    reidx = gov.reindex(target_name="t", kind="TABLE", dry_run=True)
    assert reidx["wouldReindex"]["kind"] == "TABLE"
    setres = gov.update_setting(name="work_mem", value="64MB", dry_run=True)
    assert setres["wouldSet"]["value"] == "64MB"
    assert fake.executed == []


@pytest.mark.unit
def test_create_index_dry_run_both_shapes(gov_home, monkeypatch):
    from mcp_server.tools import remediation as gov

    monkeypatch.setattr(gov, "_get_connection", lambda target=None: FakePg())
    cols = gov.create_index(table="t", columns=["a"], dry_run=True)
    assert cols["wouldCreate"]["columns"] == ["a"]
    defn = gov.create_index(definition="CREATE INDEX i ON t (a)", dry_run=True)
    assert defn["wouldExecute"] == "CREATE INDEX i ON t (a)"


@pytest.mark.unit
def test_drop_index_dry_run(gov_home, monkeypatch):
    from mcp_server.tools import remediation as gov

    monkeypatch.setattr(gov, "_get_connection", lambda target=None: FakePg())
    assert gov.drop_index(name="i", dry_run=True)["wouldDrop"] == {"name": "i"}


# ── create_index argument validation (mutually-exclusive / required) ─────────


@pytest.mark.unit
def test_create_index_rejects_definition_and_table_together(gov_home, monkeypatch):
    from mcp_server.tools import remediation as gov

    monkeypatch.setattr(gov, "_get_connection", lambda target=None: FakePg())
    # @tool_errors sanitises the ValueError into an {"error": ...} envelope
    out = gov.create_index(table="t", columns=["a"], definition="CREATE INDEX i ON t (a)")
    assert "not both" in out["error"]


@pytest.mark.unit
def test_create_index_requires_table_or_definition(gov_home, monkeypatch):
    from mcp_server.tools import remediation as gov

    monkeypatch.setattr(gov, "_get_connection", lambda target=None: FakePg())
    out = gov.create_index()
    assert "requires table+columns" in out["error"]


@pytest.mark.unit
def test_create_index_from_definition_executes_verbatim(gov_home, monkeypatch):
    from mcp_server.tools import remediation as gov

    fake = FakePg()
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: fake)
    out = gov.create_index(definition="CREATE INDEX idx_t_a ON public.t (a)")
    assert out["fromDefinition"] is True and out["index"] == "idx_t_a"
    sql, _ = fake.executed[0]
    assert sql == "CREATE INDEX idx_t_a ON public.t (a)"


# ── analysis tools: live-pull branches (statements/tables/pairs omitted) ─────


@pytest.mark.unit
def test_slow_query_rca_pulls_live_and_uses_explain(gov_home, monkeypatch):
    from mcp_server.tools import analysis as gov

    fake = FakePg({
        "FROM pg_stat_statements": [{"queryid": 9, "query": "SELECT big", "calls": 40,
                                     "total_exec_time_ms": 9000, "mean_exec_time_ms": 225,
                                     "shared_blks_hit": 50, "shared_blks_read": 50,
                                     "temp_blks_written": 10}],
        "EXPLAIN": [{"QUERY PLAN": [{"Plan": {"Node Type": "Seq Scan"}}]}],
    })
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: fake)
    out = gov.slow_query_rca(explain_sql="SELECT * FROM t")
    assert out["worst"]["queryId"] == 9
    signals = {f["signal"] for f in out["findings"]}
    assert "sequential scan on a slow statement" in signals


@pytest.mark.unit
def test_bloat_analysis_pulls_live(gov_home, monkeypatch):
    from mcp_server.tools import analysis as gov

    fake = FakePg({"FROM pg_stat_user_tables": [
        {"schema": "public", "table": "hot", "n_live_tup": 5000, "n_dead_tup": 5000,
         "dead_pct": 50.0, "size_bytes": 8192, "last_autovacuum": None},
    ]})
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: fake)
    out = gov.bloat_and_vacuum_analysis()
    assert out["needsAttentionCount"] == 1


@pytest.mark.unit
def test_blocking_rca_pulls_live(gov_home, monkeypatch):
    from mcp_server.tools import analysis as gov

    fake = FakePg({"pg_blocking_pids": [
        {"blocked_pid": 200, "blocking_pid": 100, "blocking_query": "UPDATE a"},
    ]})
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: fake)
    out = gov.blocking_lock_chain_rca()
    assert out["worstRootPid"] == 100
