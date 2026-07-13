"""Index-health PostgreSQL MCP tools (read-only)."""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from postgres_aiops.governance import governed_tool
from postgres_aiops.ops import indexes as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def unused_indexes(min_size_bytes: int = 0, target: Optional[str] = None) -> dict:
    """[READ] Non-unique, non-primary indexes with zero scans (drop candidates).

    Args:
        min_size_bytes: Only report indexes at least this large (default 0).
        target: Target name from config; omit for the default.
    """
    return ops.unused_indexes(_get_connection(target), min_size_bytes=min_size_bytes)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def missing_index_hints(
    min_seq_scan: int = 1000,
    min_live_tup: int = 10000,
    target: Optional[str] = None,
) -> dict:
    """[READ] Tables with heavy sequential scans and few index scans (index hints).

    Args:
        min_seq_scan: Minimum cumulative sequential scans to flag (default 1000).
        min_live_tup: Minimum live tuples for a table to qualify (default 10000).
        target: Target name from config; omit for the default.
    """
    return ops.missing_index_hints(
        _get_connection(target), min_seq_scan=min_seq_scan, min_live_tup=min_live_tup
    )


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def index_bloat(limit: int = 50, target: Optional[str] = None) -> dict:
    """[READ] Coarse index-bloat estimate (all inputs returned for transparency).

    Args:
        limit: Number of indexes to inspect, largest first (default 50).
        target: Target name from config; omit for the default.
    """
    return ops.index_bloat(_get_connection(target), limit=limit)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def invalid_indexes(target: Optional[str] = None) -> dict:
    """[READ] Invalid indexes (failed CONCURRENTLY builds) and duplicate indexes.

    Args:
        target: Target name from config; omit for the default.
    """
    return ops.invalid_indexes(_get_connection(target))
