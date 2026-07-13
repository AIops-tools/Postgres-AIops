"""One-shot cluster health snapshot (read-only, resilient).

Folds a handful of cheap reads into a single summary a DBA/agent can call first:
version + uptime, connection counts by state, the longest-running query, the
worst dead-tuple table, and standby replay lag. Each section is captured
defensively — one failing probe becomes an ``error`` field, never a raised
traceback (a health probe must survive the thing it probes being unhealthy).
"""

from __future__ import annotations

from typing import Any

from postgres_aiops.ops import activity, replication, server, tables


def _safe(fn: Any, *args: Any) -> Any:
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": str(exc)[:200]}


def snapshot(conn: Any) -> dict:
    """[READ] One-shot cluster health snapshot across several subsystems."""
    version = _safe(server.server_version, conn)
    act = _safe(activity.list_activity, conn)
    long_running = _safe(activity.long_running_queries, conn, 60)
    bloat = _safe(tables.table_bloat, conn, 5)
    repl = _safe(replication.replication_status, conn)

    longest = None
    if isinstance(long_running, dict) and long_running.get("queries"):
        longest = long_running["queries"][0]
    worst_bloat = None
    if isinstance(bloat, dict) and bloat.get("tables"):
        worst_bloat = bloat["tables"][0]

    return {
        "version": version.get("serverVersion") if isinstance(version, dict) else None,
        "uptime": version.get("uptime") if isinstance(version, dict) else None,
        "inRecovery": version.get("inRecovery") if isinstance(version, dict) else None,
        "connections": act.get("byState") if isinstance(act, dict) else act,
        "totalConnections": act.get("total") if isinstance(act, dict) else None,
        "idleInTransaction": act.get("idleInTransactionCount") if isinstance(act, dict) else None,
        "longRunningCount": long_running.get("count") if isinstance(long_running, dict) else None,
        "longestQuery": longest,
        "worstBloatTable": worst_bloat,
        "replicaCount": repl.get("count") if isinstance(repl, dict) else None,
        "replicas": repl.get("replicas") if isinstance(repl, dict) else None,
    }
