"""``postgres-aiops analyze`` — the three flagship analyses (pull live)."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from postgres_aiops.cli._common import TargetOption, cli_errors, console, get_connection

analyze_app = typer.Typer(
    name="analyze",
    help="Flagship analyses: slow-query RCA, bloat/vacuum, blocking chains.",
    no_args_is_help=True,
)


@analyze_app.command("slow-query")
@cli_errors
def analyze_slow_query(
    explain_sql: Annotated[str | None, typer.Option("--explain", help="SQL to EXPLAIN")] = None,
    target: TargetOption = None,
) -> None:
    """Root-cause the worst pg_stat_statements entry."""
    from postgres_aiops.ops import analysis, queries

    conn, _ = get_connection(target)
    statements = queries.top_queries(conn, order_by="total_time")["statements"]
    explain = queries.explain_query(conn, explain_sql) if explain_sql else None
    console.print_json(json.dumps(analysis.slow_query_rca(statements, explain=explain)))


@analyze_app.command("bloat-vacuum")
@cli_errors
def analyze_bloat_vacuum(target: TargetOption = None) -> None:
    """Rank tables needing vacuum from dead-tuple ratio + autovacuum recency."""
    from postgres_aiops.ops import analysis, tables

    conn, _ = get_connection(target)
    rows = tables.table_bloat(conn)["tables"]
    console.print_json(json.dumps(analysis.bloat_and_vacuum_analysis(rows)))


@analyze_app.command("blocking")
@cli_errors
def analyze_blocking(target: TargetOption = None) -> None:
    """Build the blocking-lock chain and name the root blocker."""
    from postgres_aiops.ops import activity, analysis

    conn, _ = get_connection(target)
    pairs = activity.blocking_pairs(conn)
    console.print_json(json.dumps(analysis.blocking_lock_chain_rca(pairs)))
