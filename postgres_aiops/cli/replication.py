"""``postgres-aiops repl`` — replication reads."""

from __future__ import annotations

import json

import typer

from postgres_aiops.cli._common import TargetOption, cli_errors, console, get_connection

repl_app = typer.Typer(
    name="repl",
    help="Replication: status, slots, WAL.",
    no_args_is_help=True,
)


@repl_app.command("status")
@cli_errors
def repl_status(target: TargetOption = None) -> None:
    """Connected standbys and replay lag."""
    from postgres_aiops.ops import replication as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.replication_status(conn)))


@repl_app.command("slots")
@cli_errors
def repl_slots(target: TargetOption = None) -> None:
    """Replication slots (inactive slots flagged)."""
    from postgres_aiops.ops import replication as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.replication_slots(conn)))


@repl_app.command("wal")
@cli_errors
def repl_wal(target: TargetOption = None) -> None:
    """WAL position, level and archiver health."""
    from postgres_aiops.ops import replication as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.wal_status(conn)))
