"""``postgres-aiops analyze`` — the three flagship analyses (pull live)."""

from __future__ import annotations

from typing import Annotated

import typer

from postgres_aiops.cli._common import (
    TargetOption,
    cli_errors,
    get_connection,
    print_result,
)

analyze_app = typer.Typer(
    name="analyze",
    help="Flagship analyses: slow-query RCA, bloat/vacuum, blocking chains.",
    no_args_is_help=True,
)


@analyze_app.command("slow-query")
@cli_errors
def analyze_slow_query(
    explain_sql: Annotated[str | None, typer.Option("--explain", help="SQL to EXPLAIN")] = None,
    limit: Annotated[int, typer.Option("--limit", help="Statements to consider")] = 20,
    target: TargetOption = None,
) -> None:
    """Root-cause the worst pg_stat_statements entry."""
    from postgres_aiops.ops import analysis, queries

    conn, _ = get_connection(target)
    source = queries.top_queries(conn, order_by="total_time", limit=limit)
    explain = queries.explain_query(conn, explain_sql) if explain_sql else None
    result = analysis.slow_query_rca(source["statements"], explain=explain)
    result["sourceTruncated"] = source["truncated"]
    result["sourceLimit"] = source["limit"]
    print_result(result)


@analyze_app.command("bloat-vacuum")
@cli_errors
def analyze_bloat_vacuum(
    limit: Annotated[int, typer.Option("--limit", help="Tables to consider")] = 50,
    target: TargetOption = None,
) -> None:
    """Rank tables needing vacuum from dead-tuple ratio + autovacuum recency."""
    from postgres_aiops.ops import analysis, tables

    conn, _ = get_connection(target)
    source = tables.table_bloat(conn, limit=limit)
    result = analysis.bloat_and_vacuum_analysis(source["tables"])
    result["sourceTruncated"] = source["truncated"]
    result["sourceLimit"] = source["limit"]
    print_result(result)


@analyze_app.command("blocking")
@cli_errors
def analyze_blocking(target: TargetOption = None) -> None:
    """Build the blocking-lock chain and name the root blocker."""
    from postgres_aiops.ops import activity, analysis

    conn, _ = get_connection(target)
    pairs = activity.blocking_pairs(conn)
    print_result(analysis.blocking_lock_chain_rca(pairs))
