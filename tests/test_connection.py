"""Tests for the psycopg-backed connection layer (no live database).

A fake connection/cursor stands in for a real psycopg connection so the row
mapping, scalar/execute helpers and error translation are exercised offline.
"""

from __future__ import annotations

import psycopg
import pytest

from postgres_aiops.config import TargetConfig
from postgres_aiops.connection import PgConnection, PgError


class FakeCursor:
    def __init__(self, rows, *, raise_exc=None, status="SELECT 1"):
        self._rows = rows
        self._raise = raise_exc
        self.statusmessage = status
        self.executed = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed = (sql, params)
        if self._raise is not None:
            raise self._raise

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, rows=None, *, raise_exc=None):
        self._rows = rows or []
        self._raise = raise_exc
        self.closed = False

    def cursor(self):
        return FakeCursor(self._rows, raise_exc=self._raise)

    def close(self):
        self.closed = True


def _target():
    return TargetConfig(name="primary", host="db.local", port=5432, user="postgres")


@pytest.mark.unit
def test_query_maps_rows_to_dicts():
    conn = PgConnection(_target(), connection=FakeConn([{"a": 1, "b": 2}]))
    rows = conn.query("SELECT a, b FROM t")
    assert rows == [{"a": 1, "b": 2}]


@pytest.mark.unit
def test_query_one_and_scalar():
    conn = PgConnection(_target(), connection=FakeConn([{"version": "PostgreSQL 16.2"}]))
    assert conn.query_one("SELECT version() AS version")["version"] == "PostgreSQL 16.2"
    assert conn.scalar("SELECT version() AS version") == "PostgreSQL 16.2"


@pytest.mark.unit
def test_scalar_none_when_empty():
    conn = PgConnection(_target(), connection=FakeConn([]))
    assert conn.scalar("SELECT 1") is None


@pytest.mark.unit
def test_execute_returns_status():
    conn = PgConnection(_target(), connection=FakeConn([]))
    assert conn.execute("VACUUM t") == "SELECT 1"


@pytest.mark.unit
def test_query_translates_operational_error_to_pgerror():
    boom = psycopg.OperationalError("connection refused")
    conn = PgConnection(_target(), connection=FakeConn(raise_exc=boom))
    with pytest.raises(PgError) as ei:
        conn.query("SELECT 1")
    assert "Could not connect" in str(ei.value) or "db.local" in str(ei.value)


@pytest.mark.unit
def test_conn_kwargs_include_password_from_legacy_env(monkeypatch):
    import postgres_aiops.config as cfg

    monkeypatch.setattr(cfg, "has_store", lambda: False)
    monkeypatch.setenv("PG_PRIMARY_PASSWORD", "s3cr3t")
    kwargs = _target().conn_kwargs
    assert kwargs["password"] == "s3cr3t"
    assert kwargs["host"] == "db.local"
    assert kwargs["application_name"] == "postgres-aiops"


@pytest.mark.unit
def test_dsn_redacted_hides_password():
    assert "***" in _target().dsn_redacted
    assert "s3cr3t" not in _target().dsn_redacted
