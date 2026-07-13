"""Query-statistics PostgreSQL MCP tools: top-N, EXPLAIN (read) + stats reset (write)."""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from postgres_aiops.governance import governed_tool
from postgres_aiops.ops import queries as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def top_queries(
    order_by: str = "total_time",
    limit: int = 20,
    target: Optional[str] = None,
) -> dict:
    """[READ] Top statements from pg_stat_statements by a whitelisted metric.

    Args:
        order_by: One of total_time, mean_time, calls, rows, io.
        limit: Number of statements to return (1..200, default 20).
        target: Target name from config; omit for the default.
    """
    return ops.top_queries(_get_connection(target), order_by=order_by, limit=limit)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def explain_query(sql: str, analyze: bool = False, target: Optional[str] = None) -> dict:
    """[READ] Return the JSON execution plan for ``sql`` (EXPLAIN).

    analyze=False (default) plans without executing; analyze=True runs the
    statement to collect real timing — only use it for read-only SQL.

    Args:
        sql: A single SQL statement to EXPLAIN.
        analyze: If True, execute the statement to gather real row counts/timing.
        target: Target name from config; omit for the default.
    """
    return ops.explain_query(_get_connection(target), sql, analyze=analyze)


@mcp.tool()
@governed_tool(risk_level="medium")
@tool_errors("dict")
def reset_query_stats(dry_run: bool = False, target: Optional[str] = None) -> dict:
    """[WRITE][risk=medium] Reset pg_stat_statements accumulators (irreversible).

    The counters cannot be restored, so no undo is recorded. Pass dry_run=True
    to preview.

    Args:
        dry_run: If True, preview without resetting.
        target: Target name from config; omit for the default.
    """
    if dry_run:
        return {"dryRun": True, "wouldReset": "pg_stat_statements"}
    return ops.reset_query_stats(_get_connection(target))
