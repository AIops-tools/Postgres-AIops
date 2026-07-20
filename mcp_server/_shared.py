"""Shared MCP server primitives: the FastMCP instance, connection helper,
error sanitisation, and the ``@tool_errors`` decorator.

Tool modules under ``mcp_server/tools/`` import ``mcp`` from here and register
their ``@mcp.tool()`` functions onto it. ``mcp_server/server.py`` then imports
those modules and runs the server.

Keep ``Optional[X]`` (never PEP 604 ``X | None``) in any FastMCP-reflected
tool signature — on older mcp/pydantic the union eval'd to ``types.UnionType``
crashes FastMCP's ``issubclass`` check.
"""

import functools
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from postgres_aiops.config import load_config
from postgres_aiops.connection import ConnectionManager, PgConnectionLostError, PgError
from postgres_aiops.governance import mark_unknown, sanitize

logger = logging.getLogger(__name__)

_DOCTOR_HINT = "Run 'postgres-aiops doctor' to verify connectivity and credentials."


# Long enough to carry the remediation sentence. These messages teach the
# caller what to do instead, and that clause comes last — a 300-char cap cut
# it off silently on every refusal long enough to need one.
_ERROR_MAX = 800


# Failures that leave the statement's fate genuinely undetermined. Raised
# only from the statement-executing path, so it means an ESTABLISHED link
# died mid-statement — not that the server was unreachable. The driver gives
# both the same class, so the connection layer discriminates by position and
# raises a dedicated class; this layer only has to recognise it.
# PostgreSQL rolls back on backend termination, so usually nothing landed —
# but a COMMIT whose acknowledgement was lost did land.
_UNDETERMINED_ERRORS = (PgConnectionLostError,)


def _safe_error(exc: Exception, tool: str) -> str:
    """Return an agent-safe error string; log full detail server-side only."""
    logger.error("Tool %s failed", tool, exc_info=True)
    _passthrough = (
        ValueError,
        FileNotFoundError,
        KeyError,
        PermissionError,
        TimeoutError,
        ConnectionError,
        PgError,
    )
    if isinstance(exc, _passthrough):
        return sanitize(str(exc), _ERROR_MAX)
    return f"{type(exc).__name__}: operation failed."


def tool_errors(shape: str = "dict") -> Callable:
    """Wrap a tool body in the canonical try/except → ``_safe_error`` pattern.

    Place this *between* ``@governed_tool`` and the function so the audit
    decorator and FastMCP still see the original signature.
    """

    def decorator(func: Callable) -> Callable:
        name = func.__name__

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as e:  # noqa: BLE001 — sanitised below
                msg = _safe_error(e, name)
                if shape == "list":
                    return [{"error": msg, "hint": _DOCTOR_HINT}]
                if shape == "str":
                    return f"Error: {msg} {_DOCTOR_HINT}"
                payload = {"error": msg, "hint": _DOCTOR_HINT}
                # Flatten the exception into a dict and its type is gone
                # for good — so classify here, while it is still known,
                # whether the operation may nonetheless have taken effect.
                if isinstance(e, _UNDETERMINED_ERRORS):
                    return mark_unknown(payload)
                return payload

        return wrapper

    return decorator


mcp = FastMCP(
    "postgres-aiops",
    instructions=(
        "Governed PostgreSQL DBA operations: a one-shot cluster "
        "'overview'; server reads (version/settings/extensions/databases/roles); "
        "activity (sessions, long-running queries, locks); query stats "
        "(pg_stat_statements top-N, EXPLAIN); index and table health (unused / "
        "missing / bloat / autovacuum); replication (lag, slots, WAL); three "
        "flagship analyses — 'slow_query_rca', 'bloat_and_vacuum_analysis', and "
        "'blocking_lock_chain_rca'; and guarded writes (terminate/cancel, "
        "vacuum/analyze, create/drop index, reindex, ALTER SYSTEM). Every tool "
        "runs through the postgres-aiops governance harness (audit / budget / "
        "risk-tier / undo). Do NOT use for OT/industrial edge — see industrial-aiops."
    ),
)

_conn_mgr: Optional[ConnectionManager] = None


def _get_connection(target: Optional[str] = None) -> Any:
    """Return a PostgreSQL connection, lazily initialising the manager."""
    global _conn_mgr  # noqa: PLW0603
    if _conn_mgr is None:
        config_path_str = os.environ.get("POSTGRES_AIOPS_CONFIG")
        config_path = Path(config_path_str) if config_path_str else None
        _conn_mgr = ConnectionManager(load_config(config_path))
    return _conn_mgr.connect(target)
