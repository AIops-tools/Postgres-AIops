"""Connection layer — teaching-error translation and the ConnectionManager.

Extends test_connection.py: every psycopg failure is mapped to a ``PgError`` with
an actionable message keyed on sqlstate (missing catalog, insufficient privilege,
generic), ``execute`` and ``close`` funnel through the same guard, and the manager
caches one session per target and disconnects cleanly.
"""

from __future__ import annotations

import psycopg
import pytest

from postgres_aiops.config import AppConfig, TargetConfig
from postgres_aiops.connection import ConnectionManager, PgConnection, PgError


class _SqlError(psycopg.Error):
    """A psycopg error carrying a chosen sqlstate (psycopg's is read-only)."""

    def __init__(self, message: str, sqlstate: str | None = None) -> None:
        self._sqlstate = sqlstate
        super().__init__(message)

    @property
    def sqlstate(self):  # type: ignore[override]
        return self._sqlstate


class _RaisingCursor:
    def __init__(self, exc):
        self._exc = exc

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        raise self._exc

    def fetchall(self):
        return []


class _RaisingConn:
    def __init__(self, exc):
        self._exc = exc

    def cursor(self):
        return _RaisingCursor(self._exc)

    def close(self):
        raise psycopg.OperationalError("close failed")


def _target():
    return TargetConfig(name="primary", host="db.local", port=5432, user="postgres")


@pytest.mark.unit
def test_undefined_table_sqlstate_teaches_extension_install():
    conn = PgConnection(_target(), connection=_RaisingConn(_SqlError("boom", "42P01")))
    with pytest.raises(PgError) as ei:
        conn.query("SELECT * FROM pg_stat_statements")
    assert "pg_stat_statements" in str(ei.value)
    assert ei.value.sqlstate == "42P01"


@pytest.mark.unit
def test_insufficient_privilege_sqlstate_teaches_pg_monitor():
    conn = PgConnection(_target(), connection=_RaisingConn(_SqlError("nope", "42501")))
    with pytest.raises(PgError) as ei:
        conn.query("SELECT 1")
    assert "pg_monitor" in str(ei.value)


@pytest.mark.unit
def test_generic_sqlstate_names_target_and_state():
    conn = PgConnection(_target(), connection=_RaisingConn(_SqlError("weird", "22012")))
    with pytest.raises(PgError) as ei:
        conn.query("SELECT 1")
    assert "[22012]" in str(ei.value) and "primary" in str(ei.value)


@pytest.mark.unit
def test_execute_translates_error_too():
    conn = PgConnection(_target(), connection=_RaisingConn(_SqlError("bad ddl", "42501")))
    with pytest.raises(PgError):
        conn.execute("VACUUM t")


@pytest.mark.unit
def test_close_swallows_psycopg_error():
    conn = PgConnection(_target(), connection=_RaisingConn(_SqlError("x")))
    conn.close()  # must not raise despite the underlying close() failing


@pytest.mark.unit
def test_target_property_exposed():
    tgt = _target()
    conn = PgConnection(tgt, connection=_RaisingConn(_SqlError("x")))
    assert conn.target is tgt


# ── ConnectionManager ────────────────────────────────────────────────────────


class _StubConn:
    def __init__(self, target, connection=None):
        self.target = target
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def _stub_pgconnection(monkeypatch):
    import postgres_aiops.connection as conn_mod

    monkeypatch.setattr(conn_mod, "PgConnection", _StubConn)


def _config():
    return AppConfig(targets=(
        TargetConfig(name="primary", host="a"),
        TargetConfig(name="replica", host="b"),
    ))


@pytest.mark.unit
def test_manager_connect_caches_one_session_per_target(_stub_pgconnection):
    mgr = ConnectionManager(_config())
    first = mgr.connect("primary")
    again = mgr.connect("primary")
    assert first is again  # cached, not reopened
    assert mgr.list_connected() == ["primary"]


@pytest.mark.unit
def test_manager_connect_default_target_when_unnamed(_stub_pgconnection):
    mgr = ConnectionManager(_config())
    conn = mgr.connect()
    assert conn.target.name == "primary"  # first target is the default


@pytest.mark.unit
def test_manager_lists_targets_and_disconnects(_stub_pgconnection):
    mgr = ConnectionManager(_config())
    assert mgr.list_targets() == ["primary", "replica"]
    c = mgr.connect("replica")
    mgr.disconnect("replica")
    assert c.closed is True and mgr.list_connected() == []
    mgr.disconnect("replica")  # idempotent: popping an absent target is a no-op


@pytest.mark.unit
def test_manager_disconnect_all(_stub_pgconnection):
    mgr = ConnectionManager(_config())
    mgr.connect("primary")
    mgr.connect("replica")
    mgr.disconnect_all()
    assert mgr.list_connected() == []


@pytest.mark.unit
def test_manager_from_config_uses_supplied_config(_stub_pgconnection):
    mgr = ConnectionManager.from_config(_config())
    assert mgr.list_targets() == ["primary", "replica"]
