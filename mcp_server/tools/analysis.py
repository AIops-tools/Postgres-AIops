"""Flagship PostgreSQL analysis MCP tools (read-only)."""

from typing import Any, Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from postgres_aiops.governance import governed_tool
from postgres_aiops.ops import activity as activity_ops
from postgres_aiops.ops import analysis as ops
from postgres_aiops.ops import queries as query_ops
from postgres_aiops.ops import tables as table_ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def slow_query_rca(
    statements: Optional[list[dict[str, Any]]] = None,
    explain_sql: Optional[str] = None,
    limit: int = 20,
    target: Optional[str] = None,
) -> dict:
    """[READ] RCA for the worst pg_stat_statements entry, with cause + action.

    Picks the statement with the greatest total execution time and maps its
    numbers (mean time, cache-hit ratio, temp spill, calls) — plus an optional
    EXPLAIN plan — to cited causes and concrete actions. Pass 'statements' for
    pure/offline analysis, or omit to pull the top statements live.

    Args:
        statements: Injected pg_stat_statements rows (as from top_queries); if
            omitted, the worst statements are pulled live.
        explain_sql: Optional SQL to EXPLAIN so plan node types feed the RCA.
        limit: How many statements to pull when not injected (default 20).
        target: Target name from config; omit for the default.
    """
    conn = None
    source: dict = {}
    if statements is None:
        conn = _get_connection(target)
        source = query_ops.top_queries(conn, order_by="total_time", limit=limit)
        statements = source["statements"]
    explain = None
    if explain_sql:
        conn = conn or _get_connection(target)
        explain = query_ops.explain_query(conn, explain_sql, analyze=False)
    result = ops.slow_query_rca(statements, explain=explain)
    # The RCA reads a top-N; if that top-N was itself cut short the verdict is
    # drawn from a partial view. Say so rather than let it read as complete.
    if source:
        result["sourceTruncated"] = source["truncated"]
        result["sourceLimit"] = source["limit"]
    return result


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def bloat_and_vacuum_analysis(
    tables: Optional[list[dict[str, Any]]] = None,
    limit: int = 50,
    target: Optional[str] = None,
) -> dict:
    """[READ] Rank tables needing vacuum from dead-tuple ratio + autovacuum recency.

    Pass 'tables' (as from table_bloat) for pure/offline analysis, or omit to
    pull the worst dead-tuple tables live. Each recommendation cites its numbers.

    Args:
        tables: Injected table-bloat rows; if omitted, pulled live.
        limit: How many tables to pull when not injected (default 50).
        target: Target name from config; omit for the default.
    """
    source: dict = {}
    if tables is None:
        source = table_ops.table_bloat(_get_connection(target), limit=limit)
        tables = source["tables"]
    result = ops.bloat_and_vacuum_analysis(tables)
    if source:
        result["sourceTruncated"] = source["truncated"]
        result["sourceLimit"] = source["limit"]
    return result


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def blocking_lock_chain_rca(
    pairs: Optional[list[dict[str, Any]]] = None,
    target: Optional[str] = None,
) -> dict:
    """[READ] Build the wait-for tree from blocking pairs and name the root blocker.

    Pass 'pairs' (as from the live blocking-pairs read) for pure/offline analysis,
    or omit to pull the current blocking graph live.

    Args:
        pairs: Injected blocking pairs {blockedPid, blockingPid, ...}; if omitted,
            pulled live from pg_blocking_pids.
        target: Target name from config; omit for the default.
    """
    if pairs is None:
        pairs = activity_ops.blocking_pairs(_get_connection(target))
    return ops.blocking_lock_chain_rca(pairs)
