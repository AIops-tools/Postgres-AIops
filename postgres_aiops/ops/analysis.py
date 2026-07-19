"""Flagship signature analyses over PostgreSQL telemetry (pure analysis).

The differentiators — transparent heuristics, every flag reported with its number
so a DBA can see *why* something was ranked, never a black-box verdict:

  1. ``slow_query_rca`` — take the worst pg_stat_statements entry (+ an optional
     EXPLAIN plan) and map it to a likely cause + concrete action.
  2. ``bloat_and_vacuum_analysis`` — combine dead-tuple ratio, table size and
     autovacuum recency into a per-table recommendation.
  3. ``blocking_lock_chain_rca`` — build the wait-for tree from blocking pairs,
     name the root blocker, and give the action.

All three are pure functions (no I/O): pass them telemetry (from the reads in the
other ops modules, or injected) and they return the analysis.
"""

from __future__ import annotations

from typing import Any

MAX_ROWS = 100


# ── 1. slow query RCA ───────────────────────────────────────────────────────
# Thresholds that flip a signal on (each reported with its measured number).
_SLOW_MEAN_MS = 100.0
_LOW_CACHE_HIT_PCT = 95.0
_HIGH_CALLS = 100_000


def _plan_node_types(plan: Any, found: set[str]) -> None:
    """Recursively collect ``Node Type`` values from an EXPLAIN JSON plan."""
    if isinstance(plan, dict):
        node = plan.get("Node Type")
        if isinstance(node, str):
            found.add(node)
        for value in plan.values():
            _plan_node_types(value, found)
    elif isinstance(plan, list):
        for item in plan:
            _plan_node_types(item, found)


def _slow_findings(worst: dict, node_types: set[str]) -> list[dict]:
    """Build the list of cited findings (cause + action) for the worst statement."""
    findings: list[dict] = []
    mean = float(worst.get("meanExecTimeMs") or 0)
    cache = worst.get("cacheHitRatioPct")
    temp_written = worst.get("tempBlksWritten") or 0
    calls = worst.get("calls") or 0

    if "Seq Scan" in node_types and mean >= _SLOW_MEAN_MS:
        findings.append({
            "signal": "sequential scan on a slow statement",
            "detail": f"plan contains Seq Scan; mean {mean}ms >= {_SLOW_MEAN_MS}ms",
            "cause": "A hot query scans a table with no usable index.",
            "action": "Add an index on the filter/join columns; confirm with EXPLAIN.",
        })
    if isinstance(cache, (int, float)) and cache < _LOW_CACHE_HIT_PCT:
        findings.append({
            "signal": "low shared-buffer cache hit ratio",
            "detail": f"cacheHitRatioPct {cache}% < {_LOW_CACHE_HIT_PCT}%",
            "cause": "Reads miss the buffer cache and hit disk repeatedly.",
            "action": "Add a covering index to cut blocks read, or raise shared_buffers.",
        })
    if temp_written and int(temp_written) > 0:
        findings.append({
            "signal": "temp blocks written (spill to disk)",
            "detail": f"tempBlksWritten={temp_written} — sorts/hashes spilled",
            "cause": "work_mem is too small for this query's sort/hash.",
            "action": "Raise work_mem for the session/role, or reduce the sorted set.",
        })
    if calls and int(calls) >= _HIGH_CALLS:
        findings.append({
            "signal": "very high call count",
            "detail": f"calls={calls} >= {_HIGH_CALLS}",
            "cause": "A cheap statement is executed enormously often (possible N+1).",
            "action": "Batch/cache at the application, or use a prepared/set-based query.",
        })
    if not findings:
        findings.append({
            "signal": "no dominant signal",
            "detail": f"mean {mean}ms, calls {calls}",
            "cause": "The statement is costly but shows no single clear driver.",
            "action": "EXPLAIN (ANALYZE, BUFFERS) it and inspect the most expensive node.",
        })
    return findings


def slow_query_rca(statements: list[dict], explain: dict | None = None) -> dict:
    """[READ] RCA for the worst pg_stat_statements entry (+ optional EXPLAIN plan).

    Picks the statement with the greatest total execution time, then maps its
    numbers (mean time, cache-hit ratio, temp spill, call count) — and any
    EXPLAIN plan node types supplied — to cited causes and concrete actions.
    """
    ranked = sorted(
        (s for s in (statements or []) if isinstance(s, dict)),
        key=lambda x: float(x.get("totalExecTimeMs") or 0),
        reverse=True,
    )
    if not ranked:
        return {"evaluated": 0, "worst": None, "findings": [], "note": "No statements supplied."}

    worst = ranked[0]
    node_types: set[str] = set()
    if explain:
        _plan_node_types(explain.get("plan", explain), node_types)
    findings = _slow_findings(worst, node_types)
    return {
        "evaluated": len(ranked),
        "worst": {
            "queryId": worst.get("queryId"),
            "query": worst.get("query"),
            "calls": worst.get("calls"),
            "totalExecTimeMs": worst.get("totalExecTimeMs"),
            "meanExecTimeMs": worst.get("meanExecTimeMs"),
            "cacheHitRatioPct": worst.get("cacheHitRatioPct"),
            "tempBlksWritten": worst.get("tempBlksWritten"),
        },
        "planNodeTypes": sorted(node_types),
        "findings": findings,
        "note": (
            "Advisory read-only heuristic over pg_stat_statements; every finding "
            "cites the measured number. Worst = greatest total execution time."
        ),
    }


# ── 2. bloat & vacuum analysis ──────────────────────────────────────────────
_DEAD_PCT_WARN = 20.0
_DEAD_TUP_WARN = 1000
_MOD_ANALYZE_WARN = 50_000


def _vacuum_recommendation(row: dict) -> dict | None:
    """Return a cited recommendation for one table, or None if it looks healthy."""
    dead_pct = float(row.get("deadPct") or 0)
    dead = int(row.get("deadTuples") or 0)
    last_autovac = row.get("lastAutovacuum")
    reasons: list[str] = []
    action = None
    if dead_pct >= _DEAD_PCT_WARN and dead >= _DEAD_TUP_WARN:
        reasons.append(f"deadPct {dead_pct}% >= {_DEAD_PCT_WARN}% ({dead} dead tuples)")
        action = "Run VACUUM (ANALYZE); if disk must be reclaimed, VACUUM FULL/pg_repack."
    if not last_autovac and dead >= _DEAD_TUP_WARN:
        reasons.append(f"never autovacuumed but has {dead} dead tuples")
        action = action or "Lower autovacuum_vacuum_scale_factor for this table."
    if not reasons:
        return None
    return {
        "schema": row.get("schema"),
        "table": row.get("table"),
        "deadPct": dead_pct,
        "deadTuples": dead,
        "sizePretty": row.get("sizePretty"),
        "lastAutovacuum": last_autovac,
        "reasons": reasons,
        "action": action,
    }


def bloat_and_vacuum_analysis(tables: list[dict]) -> dict:
    """[READ] Rank tables needing vacuum from dead-tuple ratio + autovacuum recency.

    Pure analysis over table-bloat rows ({schema, table, deadPct, deadTuples,
    sizePretty, lastAutovacuum}). Each recommendation cites the numbers that
    triggered it; healthy tables are omitted.
    """
    recs = [rec for rec in (_vacuum_recommendation(r) for r in (tables or [])) if rec]
    recs.sort(key=lambda r: r["deadPct"], reverse=True)
    return {
        "tablesEvaluated": len(tables or []),
        "needsAttentionCount": len(recs),
        "thresholds": {"deadPct": _DEAD_PCT_WARN, "deadTuples": _DEAD_TUP_WARN},
        "recommendations": recs[:MAX_ROWS],
        "returned": min(len(recs), MAX_ROWS),
        "limit": MAX_ROWS,
        "truncated": len(recs) > MAX_ROWS,
        "note": (
            "Advisory read-only heuristic: flags deadPct >= "
            f"{_DEAD_PCT_WARN}% with >= {_DEAD_TUP_WARN} dead tuples, or a table "
            "never autovacuumed that has accumulated dead tuples."
        ),
    }


# ── 3. blocking lock chain RCA ──────────────────────────────────────────────


def _descendants(root: int, children: dict[int, list[int]]) -> set[int]:
    """All pids transitively blocked by ``root`` (BFS, cycle-safe)."""
    seen: set[int] = set()
    stack = list(children.get(root, []))
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        stack.extend(children.get(pid, []))
    return seen


def blocking_lock_chain_rca(pairs: list[dict]) -> dict:
    """[READ] Build the wait-for tree from blocking pairs and name the root blocker.

    Pure analysis over pairs ({blockedPid, blockingPid, blockedQuery,
    blockingQuery, ...}). A root blocker blocks others but is itself blocked by
    none; the worst root is the one with the most transitively-blocked backends.
    A cycle (everyone blocked) is reported as a likely deadlock.
    """
    edges = [
        p for p in (pairs or [])
        if isinstance(p, dict) and p.get("blockedPid") and p.get("blockingPid")
    ]
    if not edges:
        return {"blockedBackends": 0, "roots": [], "note": "No blocking detected."}

    children: dict[int, list[int]] = {}
    blocking_query: dict[int, str] = {}
    blocked_pids: set[int] = set()
    blocking_pids: set[int] = set()
    for e in edges:
        b, g = e["blockedPid"], e["blockingPid"]
        children.setdefault(g, []).append(b)
        blocked_pids.add(b)
        blocking_pids.add(g)
        blocking_query.setdefault(g, e.get("blockingQuery") or "")

    root_pids = [pid for pid in blocking_pids if pid not in blocked_pids]
    if not root_pids:
        return {
            "blockedBackends": len(blocked_pids),
            "roots": [],
            "deadlockSuspected": True,
            "note": (
                "Every blocker is itself blocked — a cycle / possible deadlock. "
                "Inspect the transactions and terminate one to break the cycle."
            ),
        }

    roots = []
    for pid in root_pids:
        blocked = _descendants(pid, children)
        roots.append({
            "rootPid": pid,
            "blockedCount": len(blocked),
            "blockedPids": sorted(blocked),
            "rootQuery": blocking_query.get(pid, ""),
            "action": (
                f"Backend {pid} is the head of the chain — investigate its "
                "transaction; cancel_query(pid) to stop its statement, or "
                "terminate_backend(pid) to end the session and release the locks."
            ),
        })
    roots.sort(key=lambda r: r["blockedCount"], reverse=True)
    return {
        "blockedBackends": len(blocked_pids),
        "rootCount": len(roots),
        "worstRootPid": roots[0]["rootPid"],
        "roots": roots[:MAX_ROWS],
        "returned": min(len(roots), MAX_ROWS),
        "limit": MAX_ROWS,
        "truncated": len(roots) > MAX_ROWS,
        "note": (
            "Advisory read-only heuristic: a root blocker holds locks others wait "
            "on but waits on nobody; worst root blocks the most backends."
        ),
    }
