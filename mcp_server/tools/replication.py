"""Replication PostgreSQL MCP tools (read-only)."""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from postgres_aiops.governance import governed_tool
from postgres_aiops.ops import replication as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def replication_status(target: Optional[str] = None) -> dict:
    """[READ] Connected standbys and their replay lag (pg_stat_replication).

    Args:
        target: Target name from config; omit for the default.
    """
    return ops.replication_status(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def replication_slots(target: Optional[str] = None) -> dict:
    """[READ] Replication slots, flagging inactive slots that retain WAL.

    Args:
        target: Target name from config; omit for the default.
    """
    return ops.replication_slots(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def wal_status(target: Optional[str] = None) -> dict:
    """[READ] WAL position, level, size settings and archiver health.

    Args:
        target: Target name from config; omit for the default.
    """
    return ops.wal_status(_get_connection(target))
