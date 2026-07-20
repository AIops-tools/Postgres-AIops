"""A statement whose connection died is not a statement that failed.

The engine usually rolls back when the backend dies, so most of the time
nothing landed — but a COMMIT whose acknowledgement was lost DID land, and from
the client the two are indistinguishable. Reporting that as a definite failure
asserts something the tool cannot vouch for, so it is classified 'unknown'.

The discriminator is WHERE the error is raised, not its class or code: the
driver reports "could not connect" and "the link died mid-statement" the same
way, and only the statement-executing path knows a connection was established.
"""

from __future__ import annotations

import psycopg
import pytest

from mcp_server._shared import _UNDETERMINED_ERRORS
from postgres_aiops.config import TargetConfig
from postgres_aiops.connection import (
    PgConnection,
    PgConnectionLostError,
    PgError,
)


class _RaisingCursor:
    def __init__(self, exc): self._exc = exc
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, *a, **k): raise self._exc


class _RaisingConn:
    def __init__(self, exc): self._exc = exc
    def cursor(self, *a, **k): return _RaisingCursor(self._exc)


def _conn_raising(exc):
    """A real PgConnection whose driver raises on execute()."""
    target = TargetConfig(name="t", host="h", user="u", dbname="d")
    return PgConnection(target, connection=_RaisingConn(exc))


@pytest.mark.unit
def test_connection_lost_is_classified_undetermined():
    assert issubclass(PgConnectionLostError, _UNDETERMINED_ERRORS)


@pytest.mark.unit
def test_an_ordinary_failure_is_not_classified_undetermined():
    """The distinction has to be narrow, or every unreachable server cries wolf."""
    assert not issubclass(PgError, _UNDETERMINED_ERRORS)


@pytest.mark.unit
def test_a_lost_link_mid_statement_raises_the_dedicated_class():
    conn = _conn_raising(psycopg.OperationalError('server closed the connection unexpectedly'))
    with pytest.raises(PgConnectionLostError):
        conn.execute("UPDATE t SET c = 1")


@pytest.mark.unit
def test_a_server_side_error_stays_an_ordinary_failure():
    """The server answered, so the outcome is known — not undetermined."""
    conn = _conn_raising(psycopg.errors.UndefinedTable('relation "x" does not exist'))
    with pytest.raises(PgError) as caught:
        conn.execute("UPDATE t SET c = 1")
    assert not isinstance(caught.value, PgConnectionLostError)
