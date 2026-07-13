"""``postgres-aiops remediate`` — guarded maintenance writes (dry-run + confirm)."""

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
)

remediate_app = typer.Typer(
    name="remediate",
    help="Guarded writes: terminate/cancel, vacuum/analyze, index ops, ALTER SYSTEM.",
    no_args_is_help=True,
)


@remediate_app.command("terminate")
@cli_errors
def remediate_terminate(
    pid: Annotated[int, typer.Argument(help="Backend pid to terminate")],
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Terminate a backend (no undo; dry-run + confirm)."""
    from postgres_aiops.ops import remediation as ops

    if dry_run:
        dry_run_print(operation="terminate_backend",
                      api_call="SELECT pg_terminate_backend(pid)", parameters={"pid": pid})
        return
    double_confirm("terminate backend", str(pid))
    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.terminate_backend(conn, pid)))


@remediate_app.command("cancel")
@cli_errors
def remediate_cancel(
    pid: Annotated[int, typer.Argument(help="Backend pid whose query to cancel")],
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Cancel a backend's running query (no undo; dry-run + confirm)."""
    from postgres_aiops.ops import remediation as ops

    if dry_run:
        dry_run_print(operation="cancel_query",
                      api_call="SELECT pg_cancel_backend(pid)", parameters={"pid": pid})
        return
    double_confirm("cancel query on backend", str(pid))
    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.cancel_query(conn, pid)))


@remediate_app.command("vacuum")
@cli_errors
def remediate_vacuum(
    table: Annotated[str, typer.Argument(help="Table (optionally schema-qualified)")],
    full: Annotated[bool, typer.Option("--full", help="VACUUM FULL (exclusive lock)")] = False,
    analyze: Annotated[bool, typer.Option("--analyze", help="Also ANALYZE")] = False,
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """VACUUM a table (dry-run + confirm)."""
    from postgres_aiops.ops import remediation as ops

    if dry_run:
        dry_run_print(operation="run_vacuum", api_call=f"VACUUM {table}",
                      parameters={"full": full, "analyze": analyze})
        return
    double_confirm("VACUUM", table)
    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.run_vacuum(conn, table, full=full, analyze=analyze)))


@remediate_app.command("analyze-table")
@cli_errors
def remediate_analyze(
    table: Annotated[str, typer.Argument(help="Table (optionally schema-qualified)")],
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """ANALYZE a table (dry-run + confirm)."""
    from postgres_aiops.ops import remediation as ops

    if dry_run:
        dry_run_print(operation="run_analyze", api_call=f"ANALYZE {table}")
        return
    double_confirm("ANALYZE", table)
    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.run_analyze(conn, table)))


@remediate_app.command("create-index")
@cli_errors
def remediate_create_index(
    table: Annotated[str, typer.Argument(help="Table to index")],
    columns: Annotated[list[str], typer.Argument(help="Column(s) to index")],
    name: Annotated[str | None, typer.Option("--name", help="Index name")] = None,
    unique: Annotated[bool, typer.Option("--unique", help="UNIQUE index")] = False,
    concurrently: Annotated[bool, typer.Option("--concurrently", help="CONCURRENTLY")] = False,
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Create an index (reversible; dry-run + confirm)."""
    from postgres_aiops.ops import remediation as ops

    if dry_run:
        dry_run_print(operation="create_index", api_call=f"CREATE INDEX ON {table}",
                      parameters={"columns": columns, "name": name, "unique": unique})
        return
    double_confirm("create index on", table)
    conn, _ = get_connection(target)
    result = ops.create_index(conn, table, columns, name=name, unique=unique,
                              concurrently=concurrently)
    console.print_json(json.dumps(result))


@remediate_app.command("drop-index")
@cli_errors
def remediate_drop_index(
    name: Annotated[str, typer.Argument(help="Index name to drop")],
    concurrently: Annotated[bool, typer.Option("--concurrently", help="CONCURRENTLY")] = False,
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Drop an index (reversible; captures the definition first; dry-run + confirm)."""
    from postgres_aiops.ops import remediation as ops

    if dry_run:
        dry_run_print(operation="drop_index", api_call=f"DROP INDEX {name}")
        return
    double_confirm("drop index", name)
    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.drop_index(conn, name, concurrently=concurrently)))


@remediate_app.command("reindex")
@cli_errors
def remediate_reindex(
    target_name: Annotated[str, typer.Argument(help="Index/table/schema name")],
    kind: Annotated[str, typer.Option("--kind", help="INDEX|TABLE|SCHEMA")] = "INDEX",
    concurrently: Annotated[bool, typer.Option("--concurrently", help="CONCURRENTLY")] = False,
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """REINDEX an index/table/schema (dry-run + confirm)."""
    from postgres_aiops.ops import remediation as ops

    if dry_run:
        dry_run_print(operation="reindex", api_call=f"REINDEX {kind} {target_name}")
        return
    double_confirm(f"REINDEX {kind}", target_name)
    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.reindex(conn, target_name, kind=kind,
                                              concurrently=concurrently)))


@remediate_app.command("set")
@cli_errors
def remediate_set(
    name: Annotated[str, typer.Argument(help="Parameter name (e.g. work_mem)")],
    value: Annotated[str, typer.Argument(help="New value")],
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """ALTER SYSTEM SET a parameter (reversible; dry-run + confirm)."""
    from postgres_aiops.ops import remediation as ops

    if dry_run:
        dry_run_print(operation="update_setting",
                      api_call=f"ALTER SYSTEM SET {name} = ...", parameters={"value": value})
        return
    double_confirm(f"ALTER SYSTEM SET {name} =", value)
    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.update_setting(conn, name, value)))
