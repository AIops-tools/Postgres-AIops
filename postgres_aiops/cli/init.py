"""``postgres-aiops init`` — a friendly, interactive onboarding wizard.

Walks a new user through connecting their first PostgreSQL target: collects the
non-secret connection details into ``config.yaml`` and the role password into the
*encrypted* store (never plaintext on disk). Designed to be run on a terminal;
everything it needs is prompted with sensible defaults.
"""

from __future__ import annotations

import getpass

import typer
import yaml

from postgres_aiops.cli._common import cli_errors, console
from postgres_aiops.config import (
    CONFIG_DIR,
    CONFIG_FILE,
    DEFAULT_DBNAME,
    DEFAULT_PORT,
    DEFAULT_SSLMODE,
    DEFAULT_USER,
)
from postgres_aiops.secretstore import SecretStore, resolve_master_password


def _load_existing_targets() -> list[dict]:
    if not CONFIG_FILE.exists():
        return []
    raw = yaml.safe_load(CONFIG_FILE.read_text("utf-8")) or {}
    return list(raw.get("targets", []))


def _write_targets(targets: list[dict]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        CONFIG_DIR.chmod(0o700)
    except OSError:
        pass
    CONFIG_FILE.write_text(yaml.safe_dump({"targets": targets}, sort_keys=False), "utf-8")


@cli_errors
def init_cmd() -> None:
    """Interactively set up your first PostgreSQL connection."""
    console.print("[bold cyan]Postgres AIops — setup wizard[/]")
    console.print(
        "This collects connection details (saved to config.yaml) and your "
        "PostgreSQL password (saved [bold]encrypted[/] to secrets.enc).\n"
    )

    console.print("[bold]Step 1 — master password[/]")
    console.print(
        "[dim]Encrypts secrets.enc. You'll set it via the "
        "POSTGRES_AIOPS_MASTER_PASSWORD env var for non-interactive/MCP use.[/]"
    )
    password = resolve_master_password(confirm_if_new=True)
    store = SecretStore.unlock(password)

    targets = _load_existing_targets()
    existing_names = {t.get("name") for t in targets}

    while True:
        console.print("\n[bold]Step 2 — add a target[/]")
        name = typer.prompt("Target name (e.g. primary)").strip()
        if name in existing_names:
            if not typer.confirm(f"'{name}' already exists — overwrite?", default=False):
                continue
            targets = [t for t in targets if t.get("name") != name]

        host = typer.prompt("Host (IP or FQDN of the PostgreSQL server)").strip()
        port = typer.prompt("Port", default=DEFAULT_PORT, type=int)
        dbname = typer.prompt("Database name", default=DEFAULT_DBNAME).strip()
        user = typer.prompt("Role/user", default=DEFAULT_USER).strip()
        sslmode = typer.prompt(
            "sslmode (disable/allow/prefer/require/verify-ca/verify-full)",
            default=DEFAULT_SSLMODE,
        ).strip()

        console.print(
            "[dim]The role should have pg_monitor for read visibility. "
            "Enter its password below (input hidden).[/]"
        )
        secret = getpass.getpass(f"Password for '{user}'@'{name}' (hidden): ")
        store = store.set(name, secret)

        entry = {
            "name": name,
            "host": host,
            "port": port,
            "dbname": dbname,
            "user": user,
            "sslmode": sslmode,
        }
        targets.append(entry)
        existing_names.add(name)
        _write_targets(targets)
        console.print(f"[green]✓ Saved target '{name}' (password stored encrypted).[/]")

        if not typer.confirm("\nAdd another target?", default=False):
            break

    console.print(f"\n[green]✓ Setup complete.[/] Config: {CONFIG_FILE}")
    console.print(
        "[dim]Tip: export POSTGRES_AIOPS_MASTER_PASSWORD=... in your shell profile "
        "so the MCP server and CLI can unlock secrets non-interactively.[/]"
    )
    if typer.confirm("Run a connectivity check now (postgres-aiops doctor)?", default=True):
        from postgres_aiops.doctor import run_doctor

        raise typer.Exit(run_doctor())
