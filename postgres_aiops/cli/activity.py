"""``postgres-aiops activity`` — sessions, long-running queries, locks."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from postgres_aiops.cli._common import TargetOption, cli_errors, console, get_connection

activity_app = typer.Typer(
    name="activity",
    help="Activity: sessions, long-running queries, locks.",
    no_args_is_help=True,
)


@activity_app.command("list")
@cli_errors
def activity_list(
    state: Annotated[str | None, typer.Option("--state", help="Filter by state")] = None,
    target: TargetOption = None,
) -> None:
    """List current sessions (pg_stat_activity) with per-state counts."""
    from postgres_aiops.ops import activity as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.list_activity(conn, state=state)))


@activity_app.command("long")
@cli_errors
def activity_long(
    min_seconds: Annotated[int, typer.Option("--min-seconds", help="Minimum age")] = 60,
    target: TargetOption = None,
) -> None:
    """List active queries running at least --min-seconds."""
    from postgres_aiops.ops import activity as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.long_running_queries(conn, min_seconds=min_seconds)))


@activity_app.command("locks")
@cli_errors
def activity_locks(target: TargetOption = None) -> None:
    """List held/awaited locks."""
    from postgres_aiops.ops import activity as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.list_locks(conn)))
