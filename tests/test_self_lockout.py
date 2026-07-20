"""Refuse writes that destroy their own reversibility.

Two shapes of the same bug, both reachable from an ordinary agent turn:

1. ``terminate_backend`` / ``cancel_query`` on the tool's OWN pid. The read path
   has always hidden it (``activity.py`` filters ``WHERE pid <>
   pg_backend_pid()`` in three separate queries) — the detection primitive
   existed, it simply never crossed to the writes. A terminate has no undo, so
   aiming it at the calling backend kills the statement issuing it and drops the
   session the audit row is written from.

2. ``update_setting`` on a connection-affecting postmaster setting.
   ``_SETTING_NAME_RE`` is a shape check only, so ``ALTER SYSTEM SET
   max_connections = 1`` was reachable. Nothing breaks at the moment of the call
   — this tool never reloads (there is no ``pg_reload_conf`` call anywhere in the
   package) — which is precisely what makes it insidious: the write looks clean,
   the undo sits in the store looking replayable, and the operator's next restart
   strands it.

The pid guard has a fail-open case (the probe can fail) and MUST fail open:
unknown identity may never read as "it is me". The setting denylist is static,
so it has no fail-open case — but both must be EXACT, or ordinary remediation
stops working.
"""

from __future__ import annotations

import pytest

from postgres_aiops.ops import remediation as ops
from postgres_aiops.ops.remediation import SelfLockout
from tests.conftest import FakePg

_OWN_PID = 4242
_ACTIVITY = "FROM pg_stat_activity"


def _conn(own_pid: int | None = _OWN_PID):
    """A fake whose pg_backend_pid() answers ``own_pid`` (None = probe returns nothing)."""
    return FakePg(
        {_ACTIVITY: [{"pid": 99, "username": "app", "query": "SELECT 1"}]},
        scalars={"pg_backend_pid()": own_pid, "pg_terminate_backend": True,
                 "pg_cancel_backend": True},
    )


# ── 1. terminating your own backend ─────────────────────────────────────────


@pytest.mark.unit
def test_terminate_backend_refuses_this_connections_own_pid():
    with pytest.raises(SelfLockout, match="calling through"):
        ops.terminate_backend(_conn(), _OWN_PID)


@pytest.mark.unit
def test_cancel_query_refuses_this_connections_own_pid():
    with pytest.raises(SelfLockout, match="calling through"):
        ops.cancel_query(_conn(), _OWN_PID)


@pytest.mark.unit
def test_the_refusal_names_the_action_and_the_way_out():
    with pytest.raises(SelfLockout) as ei:
        ops.terminate_backend(_conn(), _OWN_PID)
    msg = str(ei.value)
    assert "terminate_backend" in msg, "must name the operation being refused"
    assert "list_activity" in msg, "must offer the route that does work"


@pytest.mark.unit
def test_nothing_reaches_the_wire_when_the_terminate_is_refused():
    conn = _conn()
    with pytest.raises(SelfLockout):
        ops.terminate_backend(conn, _OWN_PID)
    called = [sql for sql, _ in conn.queried]
    assert not any("pg_terminate_backend" in sql for sql in called)


# ── exactness: a different backend is still terminable ──────────────────────


@pytest.mark.unit
def test_a_different_backend_is_still_terminated():
    conn = _conn()
    out = ops.terminate_backend(conn, 99)
    assert out["action"] == "terminate_backend" and out["terminated"] is True
    assert ("SELECT pg_terminate_backend(%(pid)s)", {"pid": 99}) in conn.queried


@pytest.mark.unit
def test_a_different_backend_can_still_have_its_query_cancelled():
    conn = _conn()
    out = ops.cancel_query(conn, 99)
    assert out["cancelled"] is True
    assert ("SELECT pg_cancel_backend(%(pid)s)", {"pid": 99}) in conn.queried


# ── fail open: unknown identity is never read as "it is me" ─────────────────


@pytest.mark.unit
def test_terminate_proceeds_when_the_pid_probe_returns_nothing():
    """Unknown identity must not block a legitimate terminate."""
    conn = _conn(own_pid=None)
    out = ops.terminate_backend(conn, _OWN_PID)
    assert out["terminated"] is True, "an undeterminable pid must fail OPEN, not closed"


@pytest.mark.unit
def test_terminate_proceeds_when_the_pid_probe_raises():
    class Exploding(FakePg):
        def scalar(self, sql, params=None):
            if "pg_backend_pid" in sql:
                raise RuntimeError("probe unavailable")
            return True

    conn = Exploding({_ACTIVITY: [{"pid": _OWN_PID}]})
    out = ops.terminate_backend(conn, _OWN_PID)
    assert out["terminated"] is True, "a failed probe must fail OPEN"


@pytest.mark.unit
def test_the_pid_guard_is_reachable_without_performing_the_terminate():
    """The MCP wrapper calls this ahead of its dry_run return."""
    conn = _conn()
    ops.guard_terminate_backend(conn, 99)  # a non-self target is silently allowed
    with pytest.raises(SelfLockout):
        ops.guard_terminate_backend(conn, _OWN_PID)
    assert not any("pg_terminate_backend" in sql for sql, _ in conn.queried)


# ── 2. update_setting denylist ──────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "setting",
    ["listen_addresses", "port", "max_connections",
     "superuser_reserved_connections", "ssl", "hba_file"],
)
def test_every_connection_affecting_setting_is_refused(setting):
    with pytest.raises(SelfLockout):
        ops.update_setting(_conn(), setting, "1")


@pytest.mark.unit
def test_the_setting_refusal_explains_the_delayed_damage():
    """The honest part of this bug is that nothing breaks NOW."""
    with pytest.raises(SelfLockout) as ei:
        ops.update_setting(_conn(), "max_connections", "1")
    msg = str(ei.value)
    assert "restart" in msg, "must say when the damage lands"
    assert "postgresql.auto.conf" in msg, "must name where the value goes"
    assert "postgresql.conf" in msg, "must offer the route that does work"


@pytest.mark.unit
def test_no_alter_system_is_issued_for_a_refused_setting():
    conn = _conn()
    with pytest.raises(SelfLockout):
        ops.update_setting(conn, "listen_addresses", "127.0.0.1")
    assert conn.executed == [], "no ALTER SYSTEM may reach the wire"


@pytest.mark.unit
def test_the_denylist_cannot_be_side_stepped_by_case_or_padding():
    for spelling in ("MAX_CONNECTIONS", "  max_connections  ", "Max_Connections"):
        with pytest.raises(SelfLockout):
            ops.update_setting(_conn(), spelling, "1")


@pytest.mark.unit
def test_an_ordinary_setting_is_still_updatable():
    """Exactness: the denylist must not swallow real tuning work."""
    conn = FakePg({"FROM pg_settings": [
        {"setting": "4MB", "unit": "kB", "context": "user", "pending_restart": False},
    ]})
    out = ops.update_setting(conn, "work_mem", "64MB")
    assert out["priorState"]["value"] == "4MB"
    assert conn.executed[0][0] == "ALTER SYSTEM SET work_mem = '64MB'"


@pytest.mark.unit
def test_the_setting_guard_is_reachable_without_any_io():
    """The MCP wrapper calls this ahead of its dry_run return; it takes no conn."""
    ops.guard_update_setting("work_mem")
    with pytest.raises(SelfLockout):
        ops.guard_update_setting("max_connections")


@pytest.mark.unit
def test_self_lockout_is_a_valueerror():
    """CLI/MCP error handling keys off ValueError; keep it in that family."""
    assert issubclass(SelfLockout, ValueError)
