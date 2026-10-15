"""Flagship analysis tests (pure functions, no I/O)."""

from __future__ import annotations

import pytest

from postgres_aiops.ops import analysis


@pytest.mark.unit
def test_slow_query_rca_picks_worst_and_flags_seq_scan():
    statements = [
        {"queryId": 1, "query": "SELECT small", "totalExecTimeMs": 10, "meanExecTimeMs": 1,
         "calls": 10, "cacheHitRatioPct": 99, "tempBlksWritten": 0},
        {"queryId": 2, "query": "SELECT big", "totalExecTimeMs": 9000, "meanExecTimeMs": 300,
         "calls": 30, "cacheHitRatioPct": 80, "tempBlksWritten": 1200},
    ]
    explain = {"plan": [{"Plan": {"Node Type": "Seq Scan"}}]}
    out = analysis.slow_query_rca(statements, explain=explain)
    assert out["worst"]["queryId"] == 2
    signals = {f["signal"] for f in out["findings"]}
    assert "sequential scan on a slow statement" in signals
    assert "low shared-buffer cache hit ratio" in signals
    assert "temp blocks written (spill to disk)" in signals


@pytest.mark.unit
def test_slow_query_rca_empty():
    out = analysis.slow_query_rca([])
    assert out["evaluated"] == 0 and out["worst"] is None


@pytest.mark.unit
def test_bloat_and_vacuum_flags_high_dead_pct():
    tables = [
        {"schema": "public", "table": "hot", "deadPct": 40.0, "deadTuples": 5000,
         "sizePretty": "10 MB", "lastAutovacuum": "2026-07-01"},
        {"schema": "public", "table": "cold", "deadPct": 1.0, "deadTuples": 5,
         "sizePretty": "1 MB", "lastAutovacuum": "2026-07-10"},
    ]
    out = analysis.bloat_and_vacuum_analysis(tables)
    assert out["needsAttentionCount"] == 1
    assert out["recommendations"][0]["table"] == "hot"
    assert "VACUUM" in out["recommendations"][0]["action"]


@pytest.mark.unit
def test_bloat_flags_never_autovacuumed():
    tables = [
        {"schema": "public", "table": "t", "deadPct": 5.0, "deadTuples": 2000,
         "sizePretty": "5 MB", "lastAutovacuum": None},
    ]
    out = analysis.bloat_and_vacuum_analysis(tables)
    assert out["needsAttentionCount"] == 1
    assert "never autovacuumed" in out["recommendations"][0]["reasons"][0]


@pytest.mark.unit
def test_blocking_chain_names_root_blocker():
    # 100 blocks 200; 200 blocks 300 → root is 100, blocking 2 backends.
    pairs = [
        {"blockedPid": 200, "blockingPid": 100, "blockingQuery": "UPDATE a"},
        {"blockedPid": 300, "blockingPid": 200, "blockingQuery": "UPDATE b"},
    ]
    out = analysis.blocking_lock_chain_rca(pairs)
    assert out["worstRootPid"] == 100
    assert out["roots"][0]["blockedCount"] == 2
    assert "terminate_backend" in out["roots"][0]["action"]


@pytest.mark.unit
def test_blocking_chain_detects_cycle():
    pairs = [
        {"blockedPid": 1, "blockingPid": 2},
        {"blockedPid": 2, "blockingPid": 1},
    ]
    out = analysis.blocking_lock_chain_rca(pairs)
    assert out.get("deadlockSuspected") is True


@pytest.mark.unit
def test_blocking_chain_no_blocking():
    out = analysis.blocking_lock_chain_rca([])
    assert out["blockedBackends"] == 0


# ── lock-chain root that is idle inside an open transaction ──────────────────


def _pg_pairs(state, query="UPDATE t SET v=1 WHERE id=1"):
    return [{"blockedPid": 201, "blockingPid": 100, "blockedQuery": "DELETE FROM t",
             "blockingQuery": query, "blockingState": state}]


def test_idle_in_transaction_root_is_named_and_cancel_is_ruled_out():
    from postgres_aiops.ops import analysis as a

    (root,) = a.blocking_lock_chain_rca(_pg_pairs("idle in transaction"))["roots"]
    assert root["rootState"] == "idle in transaction"
    assert root["rootIdle"] is True
    # The query is the LAST statement it ran, not a running one.
    assert "last statement" in root["action"]
    assert "terminate_backend(100)" in root["action"]
    assert "nothing to cancel" in root["action"]


def test_active_root_offers_cancel_first():
    from postgres_aiops.ops import analysis as a

    (root,) = a.blocking_lock_chain_rca(_pg_pairs("active"))["roots"]
    assert root["rootIdle"] is False
    assert "cancel_query(100)" in root["action"]


def test_missing_root_query_stays_null():
    from postgres_aiops.ops import analysis as a

    (root,) = a.blocking_lock_chain_rca(_pg_pairs(None, query=None))["roots"]
    assert root["rootQuery"] is None
    assert root["rootState"] is None
    assert root["rootIdle"] is None  # state unknown


def test_plain_idle_root_with_session_locks_is_not_offered_cancel():
    from postgres_aiops.ops import analysis as a

    (root,) = a.blocking_lock_chain_rca(_pg_pairs("idle"))["roots"]
    assert root["rootIdle"] is True
    assert "terminate_backend(100)" in root["action"]
