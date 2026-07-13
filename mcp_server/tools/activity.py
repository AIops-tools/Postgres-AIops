"""Activity PostgreSQL MCP tools (read-only): sessions, long queries, locks."""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from postgres_aiops.governance import governed_tool
from postgres_aiops.ops import activity as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def list_activity(
    state: Optional[str] = None,
    include_idle: bool = True,
    target: Optional[str] = None,
) -> dict:
    """[READ] Current sessions (pg_stat_activity) with per-state counts.

    Flags idle-in-transaction backends (open transactions holding resources).

    Args:
        state: Optional exact state filter (active, idle, 'idle in transaction').
        include_idle: Include plain idle backends (default True).
        target: Target name from config; omit for the default.
    """
    return ops.list_activity(_get_connection(target), state=state, include_idle=include_idle)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def long_running_queries(min_seconds: int = 60, target: Optional[str] = None) -> dict:
    """[READ] Active queries running at least ``min_seconds``, oldest first.

    Args:
        min_seconds: Minimum age in seconds (default 60).
        target: Target name from config; omit for the default.
    """
    return ops.long_running_queries(_get_connection(target), min_seconds=min_seconds)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def list_locks(target: Optional[str] = None) -> dict:
    """[READ] Held/awaited locks joined to their owning backend and object.

    Args:
        target: Target name from config; omit for the default.
    """
    return ops.list_locks(_get_connection(target))
