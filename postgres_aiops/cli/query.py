"""``postgres-aiops query`` — pg_stat_statements top-N, EXPLAIN, stats reset."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from postgres_aiops.cli._common import (
    DryRunOption,
    TargetOption,
    cli_errors,
    console,
    double_confirm,
    dry_run_print,
    get_connection,
    print_result,
)

query_app = typer.Typer(
    name="query",
    help="Query stats: top-N, EXPLAIN, reset.",
    no_args_is_help=True,
)


@query_app.command("top")
@cli_errors
def query_top(
    order_by: Annotated[
        str, typer.Option("--order-by", help="total_time|mean_time|calls|rows|io")
    ] = "total_time",
    limit: Annotated[int, typer.Option("--limit", help="Rows to return")] = 20,
    target: TargetOption = None,
) -> None:
    """Top statements from pg_stat_statements."""
    from postgres_aiops.ops import queries as ops

    conn, _ = get_connection(target)
    print_result(ops.top_queries(conn, order_by=order_by, limit=limit))


@query_app.command("explain")
@cli_errors
def query_explain(
    sql: Annotated[str, typer.Argument(help="A single SQL statement to EXPLAIN")],
    analyze: Annotated[
        bool, typer.Option("--analyze", help="Execute to gather real timing")
    ] = False,
    target: TargetOption = None,
) -> None:
    """Return the JSON execution plan for a statement."""
    from postgres_aiops.ops import queries as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.explain_query(conn, sql, analyze=analyze)))


@query_app.command("reset")
@cli_errors
def query_reset(target: TargetOption = None, dry_run: DryRunOption = False) -> None:
    """Reset pg_stat_statements accumulators (irreversible; dry-run + confirm).

    Real execution is delegated to the ``@governed_tool``-wrapped MCP function
    so the reset is audited on the same governance path as MCP calls.
    """
    from mcp_server.tools import queries as gov

    if dry_run:
        dry_run_print(operation="reset_query_stats", api_call="SELECT pg_stat_statements_reset()")
        return
    double_confirm("reset pg_stat_statements on", "this target")
    console.print_json(json.dumps(gov.reset_query_stats(target=target)))
