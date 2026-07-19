"""``postgres-aiops table`` — table-health reads."""

from __future__ import annotations

from typing import Annotated

import typer

from postgres_aiops.cli._common import (
    TargetOption,
    cli_errors,
    get_connection,
    print_result,
)

table_app = typer.Typer(
    name="table",
    help="Table health: sizes, bloat, autovacuum.",
    no_args_is_help=True,
)


@table_app.command("sizes")
@cli_errors
def table_sizes(
    limit: Annotated[int, typer.Option("--limit", help="Tables to return")] = 20,
    target: TargetOption = None,
) -> None:
    """Largest tables by total relation size."""
    from postgres_aiops.ops import tables as ops

    conn, _ = get_connection(target)
    print_result(ops.table_sizes(conn, limit=limit))


@table_app.command("bloat")
@cli_errors
def table_bloat(
    limit: Annotated[int, typer.Option("--limit", help="Tables to inspect")] = 50,
    target: TargetOption = None,
) -> None:
    """Dead-tuple bloat proxy per table."""
    from postgres_aiops.ops import tables as ops

    conn, _ = get_connection(target)
    print_result(ops.table_bloat(conn, limit=limit))


@table_app.command("autovacuum")
@cli_errors
def table_autovacuum(
    limit: Annotated[int, typer.Option("--limit", help="Tables to inspect")] = 50,
    target: TargetOption = None,
) -> None:
    """Per-table dead tuples and last (auto)vacuum times."""
    from postgres_aiops.ops import tables as ops

    conn, _ = get_connection(target)
    print_result(ops.autovacuum_status(conn, limit=limit))
