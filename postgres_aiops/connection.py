"""Connection management for PostgreSQL via psycopg 3.

A thin wrapper over a live libpq connection with per-target session reuse:

  * Non-secret connection details (host / port / dbname / user / sslmode) come
    from ``config.yaml``; the **password** is read from the encrypted secret
    store (``~/.postgres-aiops/secrets.enc``) at connect time, never from disk in
    plaintext.
  * Reads run parameterised SQL against the system catalogs and ``pg_stat_*``
    views; the connection is opened ``autocommit=True`` so maintenance commands
    that cannot run inside a transaction block (``VACUUM``, ``CREATE INDEX
    CONCURRENTLY``, ``REINDEX CONCURRENTLY``) work directly.
  * Rows come back as dicts (``dict_row`` row factory), so the ops layer never
    has to index columns positionally.

All ``psycopg`` errors are translated centrally into ``PgError`` with a teaching
message rather than leaking a raw traceback to an agent.

The underlying connection is injectable for tests: pass ``connection=`` to
``PgConnection`` to substitute a fake that implements ``cursor()`` / ``close()``
— **no live database is required** to exercise the ops layer.
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row

from postgres_aiops.config import AppConfig, TargetConfig, load_config

_CONNECT_TIMEOUT = 10


class PgError(Exception):
    """A PostgreSQL operation failed; carries a teaching message + sqlstate."""

    def __init__(self, message: str, *, sqlstate: str | None = None) -> None:
        self.sqlstate = sqlstate
        super().__init__(message)


class PgConnectionLostError(PgError):
    """An ESTABLISHED connection dropped while a statement was running.

    Distinct from an ordinary failure because the outcome is genuinely
    undetermined: the statement may have committed before the link died.
    PostgreSQL rolls back on backend termination, so usually nothing landed —
    but a COMMIT whose acknowledgement was lost did land, and from here the two
    are indistinguishable. The MCP layer maps this to ``status=unknown`` rather
    than asserting a failure it cannot vouch for.

    Note the discriminator is WHERE it was raised, not the class or sqlstate:
    psycopg reports both "could not connect" and "server closed the connection
    unexpectedly" as ``OperationalError``, and a client-side detection of a
    dropped link carries no sqlstate at all — so position is the only reliable
    signal.
    """


def _teaching_message(exc: psycopg.Error, target: TargetConfig) -> str:
    """Map a psycopg error to an actionable, teaching message."""
    sqlstate = getattr(exc, "sqlstate", None)
    detail = str(exc).strip().splitlines()[0][:200] if str(exc) else ""
    if isinstance(exc, psycopg.OperationalError):
        return (
            f"Could not connect to PostgreSQL at {target.host}:{target.port}/"
            f"{target.dbname} as '{target.user}'. Check the host/port are reachable, "
            f"the role/password are correct, and pg_hba.conf permits this client "
            f"(sslmode={target.sslmode}). {detail}"
        )
    if sqlstate == "42P01":  # undefined_table
        return (
            f"Relation not found ({sqlstate}). A required catalog/view is missing — "
            f"pg_stat_statements must be installed (CREATE EXTENSION pg_stat_statements) "
            f"for query stats. {detail}"
        )
    if sqlstate == "42501":  # insufficient_privilege
        return (
            f"Insufficient privilege ({sqlstate}). This role lacks rights for the "
            f"operation; a monitoring role needs pg_monitor, and maintenance "
            f"commands need ownership. {detail}"
        )
    prefix = f" [{sqlstate}]" if sqlstate else ""
    return f"PostgreSQL error{prefix} on {target.name}: {detail}"


class PgConnection:
    """A single authenticated session against one PostgreSQL target."""

    def __init__(self, target: TargetConfig, connection: Any | None = None) -> None:
        self._target = target
        self._conn = connection if connection is not None else self._open(target)

    @staticmethod
    def _open(target: TargetConfig) -> Any:
        try:
            return psycopg.connect(
                **target.conn_kwargs,
                row_factory=dict_row,
                autocommit=True,
                connect_timeout=_CONNECT_TIMEOUT,
            )
        except psycopg.Error as exc:
            raise PgError(
                _teaching_message(exc, target), sqlstate=getattr(exc, "sqlstate", None)
            ) from exc

    @property
    def target(self) -> TargetConfig:
        return self._target

    def query(self, sql: str, params: Any | None = None) -> list[dict]:
        """Run a read query and return rows as a list of dicts."""
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
        except psycopg.Error as exc:
            raise PgError(
                _teaching_message(exc, self._target),
                sqlstate=getattr(exc, "sqlstate", None),
            ) from exc

    def query_one(self, sql: str, params: Any | None = None) -> dict | None:
        """Run a read query expected to return at most one row."""
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Any | None = None) -> Any:
        """Run a read query and return the first column of the first row (or None)."""
        row = self.query_one(sql, params)
        if not row:
            return None
        return next(iter(row.values()), None)

    def execute(self, sql: str, params: Any | None = None) -> str:
        """Run a write/DDL/maintenance statement; return the libpq status message."""
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
                return getattr(cur, "statusmessage", "") or "OK"
        except psycopg.Error as exc:
            # Reached only on an established connection, so an OperationalError
            # here means the link died mid-statement — not that we failed to
            # reach the server.
            cls = PgConnectionLostError if isinstance(exc, psycopg.OperationalError) else PgError
            raise cls(
                _teaching_message(exc, self._target),
                sqlstate=getattr(exc, "sqlstate", None),
            ) from exc

    def close(self) -> None:
        try:
            self._conn.close()
        except psycopg.Error:
            pass


class ConnectionManager:
    """Manages connections to multiple PostgreSQL targets with session reuse."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._connections: dict[str, PgConnection] = {}

    @classmethod
    def from_config(cls, config: AppConfig | None = None) -> ConnectionManager:
        cfg = config or load_config()
        return cls(cfg)

    def connect(self, target_name: str | None = None) -> PgConnection:
        """Connect to a target by name, or the default target."""
        target = (
            self._config.get_target(target_name)
            if target_name
            else self._config.default_target
        )
        cached = self._connections.get(target.name)
        if cached is not None:
            return cached
        conn = PgConnection(target)
        self._connections[target.name] = conn
        return conn

    def disconnect(self, target_name: str) -> None:
        conn = self._connections.pop(target_name, None)
        if conn is not None:
            conn.close()

    def disconnect_all(self) -> None:
        for name in list(self._connections):
            self.disconnect(name)

    def list_targets(self) -> list[str]:
        return [t.name for t in self._config.targets]

    def list_connected(self) -> list[str]:
        return list(self._connections.keys())
