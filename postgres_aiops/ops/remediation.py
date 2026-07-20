"""PostgreSQL maintenance writes (guarded).

Every reversible write reads the server's real current state **before** it changes
anything, so the harness records a faithful undo / audit trail (the before-state
is fetched, never guessed):

  * ``drop_index`` captures ``pg_get_indexdef`` first, so undo recreates it exactly.
  * ``create_index`` returns the created name, so undo drops that name.
  * ``update_setting`` captures the current value, so undo sets it back.

Irreversible ops (``terminate_backend``, ``cancel_query``, ``run_vacuum``,
``run_analyze``, ``reindex``, ``reset_query_stats``) capture prior stats for the
audit trail but declare no undo.

Two writes additionally refuse targets that would destroy their own
reversibility (:class:`SelfLockout`):

  * ``terminate_backend`` / ``cancel_query`` refuse this connection's own pid.
    ``ops/activity.py`` already hides it from every read (``WHERE pid <>
    pg_backend_pid()``); the writes have to honour the same boundary, or the one
    backend an agent can reach by guessing is the one it is calling through.
  * ``update_setting`` refuses the connection-affecting postmaster settings.
    ``ALTER SYSTEM`` only writes postgresql.auto.conf, so nothing breaks at the
    moment of the call — this tool never reloads. The damage lands on the
    operator's next restart, by which time the recorded undo is stranded.

Values (pids, setting values) are bound parameters. The few identifiers that
cannot be parameterised (table/index names, GUC names) are validated and quoted
via :mod:`postgres_aiops.ops._util` before the single-line interpolation site.
"""

from __future__ import annotations

import re
from typing import Any

from postgres_aiops.ops._util import opt, qualify, quote_ident, quote_literal, s

_INDEX_METHODS = {"btree", "hash", "gist", "gin", "brin", "spgist"}
_REINDEX_KINDS = {"INDEX", "TABLE", "SCHEMA"}
_SETTING_NAME_RE = re.compile(r"^[a-z_][a-z0-9_.]*$")

# Postmaster-context settings that decide whether a client can connect at all.
# Unlike MySQL's SET GLOBAL these do not bite immediately — ALTER SYSTEM writes
# postgresql.auto.conf and this tool never reloads. That is exactly what makes
# them insidious: the call looks clean, the undo sits in the store looking
# replayable, and the operator's next restart strands it. The list is STATIC
# (no runtime detection), so there is no fail-open case.
_SELF_AFFECTING_SETTINGS: dict[str, str] = {
    "listen_addresses": "it decides which interfaces accept connections at all",
    "port": "it moves the listener, so every later connection goes to the wrong place",
    "max_connections": "it can be set below the live connection count, refusing new backends",
    "superuser_reserved_connections": (
        "it can reserve every available slot, refusing ordinary logins"
    ),
    "ssl": "it changes whether this connection's transport is even offered",
    "hba_file": "it repoints host-based auth, this tool's own rule included",
}


class SelfLockout(ValueError):  # noqa: N818 — teaching error, reads as a statement
    """Refused: the operation would cut this tool off from the server it manages."""


# ── activity control (irreversible) ─────────────────────────────────────────


def _own_backend_pid(conn: Any) -> int | None:
    """This connection's own backend pid, or None when it cannot be determined.

    ``None`` means UNKNOWN and must never be read as "it is me" — callers fail
    open, because refusing a legitimate terminate on a failed probe would be a
    new bug, while the read path (``activity.py``) already filters the same pid.
    """
    try:
        own = conn.scalar("SELECT pg_backend_pid()")
        return int(own) if own is not None else None
    except Exception:  # noqa: BLE001 — unknown identity, never a false "it is me"
        return None


def guard_terminate_backend(conn: Any, pid: int, action: str = "terminate_backend") -> None:
    """Raise the :class:`SelfLockout` a self-targeted terminate would raise, without acting.

    Called by ``terminate_backend`` / ``cancel_query`` themselves *and* by the
    MCP wrappers ahead of their ``dry_run`` early return, so a preview of a
    self-terminate reports the refusal instead of a green ``wouldTerminate``.
    Both paths run this one function, so preview and real call cannot disagree.

    Fails open on an undeterminable pid: unknown is never treated as "it is me".
    """
    own = _own_backend_pid(conn)
    if own is None or int(pid) != own:
        return
    raise SelfLockout(
        f"Refusing {action} on pid {int(pid)}: that is the backend this tool is "
        f"calling through. Terminating it kills the very statement issuing the "
        f"call and drops the session the audit row is written from. "
        f"list_activity already excludes it — pick a pid from there, or use a "
        f"separate psql session if you really must terminate this one."
    )


def _capture_backend(conn: Any, pid: int) -> dict:
    row = conn.query_one(
        "SELECT pid, usename AS username, datname AS database, state, "
        "left(query, 500) AS query FROM pg_stat_activity WHERE pid = %(pid)s",
        {"pid": int(pid)},
    ) or {}
    return {
        "pid": row.get("pid"),
        "username": opt(row.get("username"), 128),
        "database": opt(row.get("database"), 128),
        "state": opt(row.get("state"), 64),
        "query": opt(row.get("query"), 500),
    }


def terminate_backend(conn: Any, pid: int) -> dict:
    """[WRITE] Terminate a backend (pg_terminate_backend). No safe inverse.

    **Refuses this connection's own pid** — a terminate has no undo, and aiming
    it at the caller's own backend destroys the statement issuing it. If the pid
    cannot be determined the call proceeds (unknown is never treated as "it is
    me").
    """
    guard_terminate_backend(conn, pid, "terminate_backend")
    prior = _capture_backend(conn, pid)
    terminated = conn.scalar("SELECT pg_terminate_backend(%(pid)s)", {"pid": int(pid)})
    return {
        "action": "terminate_backend",
        "pid": int(pid),
        "terminated": bool(terminated),
        "priorState": prior,
    }


def cancel_query(conn: Any, pid: int) -> dict:
    """[WRITE] Cancel a backend's running query (pg_cancel_backend). No inverse.

    **Refuses this connection's own pid** — the query it would cancel is this
    very call. If the pid cannot be determined the call proceeds.
    """
    guard_terminate_backend(conn, pid, "cancel_query")
    prior = _capture_backend(conn, pid)
    cancelled = conn.scalar("SELECT pg_cancel_backend(%(pid)s)", {"pid": int(pid)})
    return {
        "action": "cancel_query",
        "pid": int(pid),
        "cancelled": bool(cancelled),
        "priorState": prior,
    }


# ── vacuum / analyze (irreversible; capture prior stats) ────────────────────


def _capture_table_stats(conn: Any, table: str) -> dict:
    relname = table.split(".")[-1]
    row = conn.query_one(
        "SELECT n_dead_tup, n_live_tup, last_vacuum, last_autovacuum, "
        "last_analyze, last_autoanalyze FROM pg_stat_user_tables "
        "WHERE relname = %(t)s",
        {"t": relname},
    ) or {}
    return {
        "deadTuples": row.get("n_dead_tup"),
        "liveTuples": row.get("n_live_tup"),
        "lastVacuum": opt(row.get("last_vacuum"), 64),
        "lastAnalyze": opt(row.get("last_analyze"), 64),
    }


def run_vacuum(conn: Any, table: str, full: bool = False, analyze: bool = False) -> dict:
    """[WRITE] VACUUM a table (optionally FULL/ANALYZE). Records prior dead-tuple stats."""
    ident = qualify(table)
    prior = _capture_table_stats(conn, table)
    parts = []
    if full:
        parts.append("FULL")
    if analyze:
        parts.append("ANALYZE")
    options = f"({', '.join(parts)}) " if parts else ""
    sql = f"VACUUM {options}{ident}"  # nosec B608 — ident validated, options static
    conn.execute(sql)
    return {"action": "run_vacuum", "table": table, "full": full, "analyze": analyze,
            "priorState": prior}


def run_analyze(conn: Any, table: str) -> dict:
    """[WRITE] ANALYZE a table to refresh planner statistics. Records prior stats."""
    ident = qualify(table)
    prior = _capture_table_stats(conn, table)
    sql = f"ANALYZE {ident}"  # nosec B608 — ident validated
    conn.execute(sql)
    return {"action": "run_analyze", "table": table, "priorState": prior}


# ── index create/drop/reindex ───────────────────────────────────────────────


def _default_index_name(table: str, columns: list[str]) -> str:
    base = "idx_" + table.split(".")[-1] + "_" + "_".join(columns)
    return base[:63]


def create_index(
    conn: Any,
    table: str,
    columns: list[str],
    name: str | None = None,
    unique: bool = False,
    concurrently: bool = False,
    method: str | None = None,
) -> dict:
    """[WRITE] Create an index. Reversible: undo drops the created name.

    Supports CONCURRENTLY (non-blocking build). The index name is returned so the
    harness can record an undo that drops exactly this index.
    """
    cols = [str(c) for c in (columns or []) if str(c).strip()]
    if not cols:
        raise ValueError("create_index requires at least one column.")
    col_sql = ", ".join(quote_ident(c) for c in cols)
    ident_table = qualify(table)
    index_name = name or _default_index_name(table, cols)
    ident_index = quote_ident(index_name)
    using = ""
    if method:
        if method.lower() not in _INDEX_METHODS:
            raise ValueError(f"Unknown index method '{method}'. Allowed: {sorted(_INDEX_METHODS)}.")
        using = f"USING {method.lower()} "
    unique_kw = "UNIQUE " if unique else ""
    conc_kw = "CONCURRENTLY " if concurrently else ""
    sql = f"CREATE {unique_kw}INDEX {conc_kw}{ident_index} ON {ident_table} {using}({col_sql})"  # nosec B608
    conn.execute(sql)
    return {
        "action": "create_index",
        "index": index_name,
        "table": table,
        "columns": cols,
        "concurrently": concurrently,
    }


# Shape gate for replaying a captured pg_get_indexdef statement. Server-generated
# (never user-composed), but validated anyway: single statement, CREATE INDEX only.
_INDEXDEF_RE = re.compile(
    r"^CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?"
    r'("?[A-Za-z_][A-Za-z0-9_$]*"?)\s+ON\s+',
    re.IGNORECASE,
)


def create_index_from_definition(conn: Any, definition: str) -> dict:
    """[WRITE] Recreate an index from a captured ``pg_get_indexdef`` statement.

    This is the replay path for ``drop_index``'s undo descriptor: the exact
    definition captured before the drop is executed verbatim after a shape
    check (single statement, must be CREATE [UNIQUE] INDEX ... ON ...).
    """
    stmt = (definition or "").strip().rstrip(";").strip()
    if not stmt or ";" in stmt:
        raise ValueError("definition must be a single CREATE INDEX statement.")
    m = _INDEXDEF_RE.match(stmt)
    if not m:
        raise ValueError("definition must start with CREATE [UNIQUE] INDEX ... ON ...")
    conn.execute(stmt)  # nosec B608 — shape-validated pg_get_indexdef output
    return {
        "action": "create_index",
        "index": m.group(1).strip('"'),
        "fromDefinition": True,
    }


def drop_index(conn: Any, name: str, concurrently: bool = False) -> dict:
    """[WRITE] Drop an index. Reversible: captures pg_get_indexdef first so undo recreates it."""
    ident = qualify(name)
    indexdef = conn.scalar(
        "SELECT pg_get_indexdef(to_regclass(%(n)s))", {"n": name}
    )
    if not indexdef:
        raise ValueError(f"Index '{name}' not found (no definition to capture).")
    conc_kw = "CONCURRENTLY " if concurrently else ""
    sql = f"DROP INDEX {conc_kw}{ident}"  # nosec B608 — ident validated
    conn.execute(sql)
    return {
        "action": "drop_index",
        "index": name,
        "priorState": {"indexdef": s(indexdef, 2000)},
    }


def reindex(conn: Any, target: str, kind: str = "INDEX", concurrently: bool = False) -> dict:
    """[WRITE] REINDEX an index/table/schema. No undo (rebuild in place)."""
    kind_up = (kind or "INDEX").upper()
    if kind_up not in _REINDEX_KINDS:
        raise ValueError(f"Unknown REINDEX kind '{kind}'. Allowed: {sorted(_REINDEX_KINDS)}.")
    ident = qualify(target)
    conc_kw = "CONCURRENTLY " if concurrently else ""
    sql = f"REINDEX {conc_kw}{kind_up} {ident}"  # nosec B608 — ident validated, kind whitelisted
    conn.execute(sql)
    return {"action": "reindex", "kind": kind_up, "target": target, "concurrently": concurrently}


# ── server settings (reversible via ALTER SYSTEM) ───────────────────────────


def _validate_setting_name(name: str) -> str:
    if not isinstance(name, str) or not _SETTING_NAME_RE.match(name):
        raise ValueError(
            f"Invalid setting name {name!r} (lowercase letters, digits, '_' and '.' only)."
        )
    return name


def guard_update_setting(name: str) -> None:
    """Raise the :class:`SelfLockout` ``update_setting`` would raise, without any I/O.

    Called by ``update_setting`` itself *and* by the MCP wrapper ahead of its
    ``dry_run`` early return, so a preview of a denylisted setting reports the
    refusal instead of a green ``wouldSet``. The denylist is static, so the
    preview and the real call cannot diverge and the guard costs nothing.

    Normalises the name itself, so it cannot be side-stepped by case or padding
    on either path.
    """
    setting = str(name).strip().lower()
    lockout_reason = _SELF_AFFECTING_SETTINGS.get(setting)
    if lockout_reason is None:
        return
    raise SelfLockout(
        f"Refusing ALTER SYSTEM SET {setting}: {lockout_reason}. Nothing breaks "
        f"now — this tool never reloads — but the value is written to "
        f"postgresql.auto.conf, so your next restart applies it and the undo "
        f"recorded here can no longer connect to replay itself. Edit "
        f"postgresql.conf directly, where you have console access to recover."
    )


def update_setting(conn: Any, name: str, value: str) -> dict:
    """[WRITE] ALTER SYSTEM SET a parameter. Reversible: captures the prior value.

    Writes to postgresql.auto.conf; most parameters need ``SELECT pg_reload_conf()``
    (or a restart for ``pending_restart`` ones) to take effect — this is reported
    but NOT performed automatically.

    **Refuses the connection-affecting postmaster settings**
    (``listen_addresses``, ``port``, ``max_connections``,
    ``superuser_reserved_connections``, ``ssl``, ``hba_file``). Those are the
    ones whose delayed application strands the undo: the write looks clean, and
    the operator's next restart is what locks the tool out.
    """
    guard_update_setting(name)
    setting_name = _validate_setting_name(name)
    prior = conn.query_one(
        "SELECT setting, unit, context, pending_restart FROM pg_settings WHERE name = %(n)s",
        {"n": setting_name},
    ) or {}
    literal = quote_literal(str(value))
    sql = f"ALTER SYSTEM SET {setting_name} = {literal}"  # nosec B608 — name validated, value literal-quoted
    conn.execute(sql)
    needs_restart = bool(prior.get("pending_restart")) or prior.get("context") == "postmaster"
    return {
        "action": "update_setting",
        "setting": setting_name,
        "newValue": str(value),
        "priorState": {
            "value": s(prior.get("setting"), 256),
            "unit": opt(prior.get("unit"), 32),
            "context": s(prior.get("context"), 32),
        },
        "reloadRequired": True,
        "restartRequired": needs_restart,
        "note": (
            "Run SELECT pg_reload_conf() to apply (restart needed for "
            "postmaster-context settings)."
        ),
    }
