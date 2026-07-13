"""Activity reads: sessions, long-running queries, locks and blocking pairs.

Every query surfaced to an agent is truncated in SQL (``left(query, N)``) and
sanitised again via ``s`` — a running statement can contain arbitrary text.
Thresholds (seconds) are passed as bound parameters, never string-formatted.
"""

from __future__ import annotations

from typing import Any

from postgres_aiops.ops._util import s

_ACTIVITY_SQL = """
SELECT pid,
       usename AS username,
       datname AS database,
       client_addr::text AS client_addr,
       application_name,
       state,
       wait_event_type,
       wait_event,
       backend_type,
       xact_start,
       query_start,
       state_change,
       EXTRACT(EPOCH FROM (now() - query_start))::int AS query_age_seconds,
       EXTRACT(EPOCH FROM (now() - xact_start))::int AS xact_age_seconds,
       left(query, 500) AS query
FROM pg_stat_activity
WHERE pid <> pg_backend_pid()
  AND (%(state)s IS NULL OR state = %(state)s)
  AND (%(include_idle)s OR state IS DISTINCT FROM 'idle')
ORDER BY query_start NULLS LAST
"""

_LONG_RUNNING_SQL = """
SELECT pid, usename AS username, datname AS database, state,
       wait_event_type, wait_event,
       EXTRACT(EPOCH FROM (now() - query_start))::int AS duration_seconds,
       (now() - query_start) AS duration,
       left(query, 500) AS query
FROM pg_stat_activity
WHERE state <> 'idle'
  AND query_start IS NOT NULL
  AND (now() - query_start) >= make_interval(secs => %(min_seconds)s)
  AND pid <> pg_backend_pid()
ORDER BY query_start ASC
"""

_LOCKS_SQL = """
SELECT a.pid, a.usename AS username, a.datname AS database,
       l.locktype, l.mode, l.granted,
       COALESCE(c.relname, l.locktype) AS object,
       a.wait_event_type, a.wait_event,
       left(a.query, 300) AS query
FROM pg_locks l
JOIN pg_stat_activity a ON a.pid = l.pid
LEFT JOIN pg_class c ON c.oid = l.relation
WHERE a.pid <> pg_backend_pid()
ORDER BY l.granted, a.pid
"""

# One row per (blocked backend -> a backend blocking it). pg_blocking_pids gives
# the exact wait-for edges; unnesting produces the graph the flagship RCA walks.
_BLOCKING_PAIRS_SQL = """
SELECT blocked.pid AS blocked_pid,
       blocked.usename AS blocked_user,
       blocked.datname AS database,
       blocked.wait_event_type,
       blocked.wait_event,
       left(blocked.query, 300) AS blocked_query,
       blocking.pid AS blocking_pid,
       blocking.usename AS blocking_user,
       blocking.state AS blocking_state,
       left(blocking.query, 300) AS blocking_query
FROM pg_stat_activity blocked
JOIN LATERAL unnest(pg_blocking_pids(blocked.pid)) AS bp(pid) ON true
JOIN pg_stat_activity blocking ON blocking.pid = bp.pid
WHERE cardinality(pg_blocking_pids(blocked.pid)) > 0
"""


def _activity_row(r: dict) -> dict:
    return {
        "pid": r.get("pid"),
        "username": s(r.get("username"), 128),
        "database": s(r.get("database"), 128),
        "clientAddr": s(r.get("client_addr"), 64),
        "applicationName": s(r.get("application_name"), 128),
        "state": s(r.get("state"), 64),
        "waitEventType": s(r.get("wait_event_type"), 64),
        "waitEvent": s(r.get("wait_event"), 64),
        "backendType": s(r.get("backend_type"), 64),
        "queryAgeSeconds": r.get("query_age_seconds"),
        "xactAgeSeconds": r.get("xact_age_seconds"),
        "query": s(r.get("query"), 500),
    }


def list_activity(
    conn: Any, state: str | None = None, include_idle: bool = True
) -> dict:
    """[READ] Current sessions from pg_stat_activity, with per-state counts.

    Flags idle-in-transaction backends (an open transaction holding resources).
    """
    rows = conn.query(_ACTIVITY_SQL, {"state": state, "include_idle": include_idle})
    sessions = [_activity_row(r) for r in rows]
    by_state: dict[str, int] = {}
    for r in sessions:
        key = r["state"] or "unknown"
        by_state[key] = by_state.get(key, 0) + 1
    idle_in_txn = [
        r for r in sessions if r["state"] == "idle in transaction"
    ]
    return {
        "total": len(sessions),
        "byState": dict(sorted(by_state.items(), key=lambda kv: kv[1], reverse=True)),
        "idleInTransactionCount": len(idle_in_txn),
        "idleInTransaction": idle_in_txn,
        "sessions": sessions,
    }


def long_running_queries(conn: Any, min_seconds: int = 60) -> dict:
    """[READ] Active queries running at least ``min_seconds``, oldest first."""
    rows = conn.query(_LONG_RUNNING_SQL, {"min_seconds": int(min_seconds)})
    queries = [
        {
            "pid": r.get("pid"),
            "username": s(r.get("username"), 128),
            "database": s(r.get("database"), 128),
            "state": s(r.get("state"), 64),
            "durationSeconds": r.get("duration_seconds"),
            "duration": s(r.get("duration"), 64),
            "waitEventType": s(r.get("wait_event_type"), 64),
            "waitEvent": s(r.get("wait_event"), 64),
            "query": s(r.get("query"), 500),
        }
        for r in rows
    ]
    return {
        "thresholdSeconds": int(min_seconds),
        "count": len(queries),
        "queries": queries,
    }


def list_locks(conn: Any) -> dict:
    """[READ] Held/awaited locks joined to their owning backend and object."""
    rows = conn.query(_LOCKS_SQL)
    locks = [
        {
            "pid": r.get("pid"),
            "username": s(r.get("username"), 128),
            "database": s(r.get("database"), 128),
            "lockType": s(r.get("locktype"), 64),
            "mode": s(r.get("mode"), 64),
            "granted": bool(r.get("granted")),
            "object": s(r.get("object"), 128),
            "waitEventType": s(r.get("wait_event_type"), 64),
            "query": s(r.get("query"), 300),
        }
        for r in rows
    ]
    waiting = [lock for lock in locks if not lock["granted"]]
    return {
        "total": len(locks),
        "waitingCount": len(waiting),
        "waiting": waiting,
        "locks": locks,
    }


def blocking_pairs(conn: Any) -> list[dict]:
    """[READ] Wait-for edges (blocked pid -> blocking pid) from pg_blocking_pids."""
    rows = conn.query(_BLOCKING_PAIRS_SQL)
    return [
        {
            "blockedPid": r.get("blocked_pid"),
            "blockedUser": s(r.get("blocked_user"), 128),
            "database": s(r.get("database"), 128),
            "waitEventType": s(r.get("wait_event_type"), 64),
            "waitEvent": s(r.get("wait_event"), 64),
            "blockedQuery": s(r.get("blocked_query"), 300),
            "blockingPid": r.get("blocking_pid"),
            "blockingUser": s(r.get("blocking_user"), 128),
            "blockingState": s(r.get("blocking_state"), 64),
            "blockingQuery": s(r.get("blocking_query"), 300),
        }
        for r in rows
    ]
