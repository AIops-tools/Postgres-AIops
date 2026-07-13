"""``postgres-aiops index`` — index-health reads."""

from __future__ import annotations

import json

import typer

from postgres_aiops.cli._common import TargetOption, cli_errors, console, get_connection

index_app = typer.Typer(
    name="index",
    help="Index health: unused, missing, bloat, invalid.",
    no_args_is_help=True,
)


@index_app.command("unused")
@cli_errors
def index_unused(target: TargetOption = None) -> None:
    """Non-unique, non-primary indexes with zero scans."""
    from postgres_aiops.ops import indexes as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.unused_indexes(conn)))


@index_app.command("missing")
@cli_errors
def index_missing(target: TargetOption = None) -> None:
    """Tables with heavy sequential scans (missing-index hints)."""
    from postgres_aiops.ops import indexes as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.missing_index_hints(conn)))


@index_app.command("bloat")
@cli_errors
def index_bloat(target: TargetOption = None) -> None:
    """Coarse index-bloat estimate."""
    from postgres_aiops.ops import indexes as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.index_bloat(conn)))


@index_app.command("invalid")
@cli_errors
def index_invalid(target: TargetOption = None) -> None:
    """Invalid and duplicate indexes."""
    from postgres_aiops.ops import indexes as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.invalid_indexes(conn)))
