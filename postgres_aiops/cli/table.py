"""``postgres-aiops table`` — table-health reads."""

from __future__ import annotations

import json

import typer

from postgres_aiops.cli._common import TargetOption, cli_errors, console, get_connection

table_app = typer.Typer(
    name="table",
    help="Table health: sizes, bloat, autovacuum.",
    no_args_is_help=True,
)


@table_app.command("sizes")
@cli_errors
def table_sizes(target: TargetOption = None) -> None:
    """Largest tables by total relation size."""
    from postgres_aiops.ops import tables as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.table_sizes(conn)))


@table_app.command("bloat")
@cli_errors
def table_bloat(target: TargetOption = None) -> None:
    """Dead-tuple bloat proxy per table."""
    from postgres_aiops.ops import tables as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.table_bloat(conn)))


@table_app.command("autovacuum")
@cli_errors
def table_autovacuum(target: TargetOption = None) -> None:
    """Per-table dead tuples and last (auto)vacuum times."""
    from postgres_aiops.ops import tables as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.autovacuum_status(conn)))
