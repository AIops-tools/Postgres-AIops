"""Table-health PostgreSQL MCP tools (read-only)."""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from postgres_aiops.governance import governed_tool
from postgres_aiops.ops import tables as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def table_sizes(limit: int = 20, target: Optional[str] = None) -> dict:
    """[READ] Largest tables by total relation size (table + indexes + TOAST).

    Args:
        limit: Number of tables to return, largest first (default 20).
        target: Target name from config; omit for the default.
    """
    return ops.table_sizes(_get_connection(target), limit=limit)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def table_bloat(limit: int = 50, target: Optional[str] = None) -> dict:
    """[READ] Dead-tuple bloat proxy per table (dead / (live + dead)), worst first.

    Args:
        limit: Number of tables to inspect (default 50).
        target: Target name from config; omit for the default.
    """
    return ops.table_bloat(_get_connection(target), limit=limit)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def autovacuum_status(limit: int = 50, target: Optional[str] = None) -> dict:
    """[READ] Per-table dead tuples, mods-since-analyze, and last (auto)vacuum times.

    Args:
        limit: Number of tables to inspect (default 50).
        target: Target name from config; omit for the default.
    """
    return ops.autovacuum_status(_get_connection(target), limit=limit)
