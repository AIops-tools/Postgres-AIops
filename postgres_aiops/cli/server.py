"""``postgres-aiops server`` — server-level reads."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from postgres_aiops.cli._common import TargetOption, cli_errors, console, get_connection

server_app = typer.Typer(
    name="server",
    help="Server reads: version, settings, extensions, databases, roles.",
    no_args_is_help=True,
)


@server_app.command("version")
@cli_errors
def server_version(target: TargetOption = None) -> None:
    """Server version, uptime and recovery state."""
    from postgres_aiops.ops import server as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.server_version(conn)))


@server_app.command("settings")
@cli_errors
def server_settings(
    pattern: Annotated[str | None, typer.Argument(help="Name substring filter")] = None,
    target: TargetOption = None,
) -> None:
    """Configuration parameters (pg_settings), optionally filtered by name."""
    from postgres_aiops.ops import server as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.show_settings(conn, pattern)))


@server_app.command("extensions")
@cli_errors
def server_extensions(target: TargetOption = None) -> None:
    """Installed extensions."""
    from postgres_aiops.ops import server as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.list_extensions(conn)))


@server_app.command("databases")
@cli_errors
def server_databases(target: TargetOption = None) -> None:
    """Databases with owner, encoding and size."""
    from postgres_aiops.ops import server as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.list_databases(conn)))


@server_app.command("roles")
@cli_errors
def server_roles(target: TargetOption = None) -> None:
    """Roles and their attributes."""
    from postgres_aiops.ops import server as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.list_roles(conn)))
