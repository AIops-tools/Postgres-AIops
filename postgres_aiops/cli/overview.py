"""``postgres-aiops overview`` — one-shot cluster health snapshot."""

from __future__ import annotations

import json

from postgres_aiops.cli._common import TargetOption, cli_errors, console, get_connection


@cli_errors
def overview_cmd(target: TargetOption = None) -> None:
    """One-shot cluster health: version, connections, long queries, bloat, replication."""
    from postgres_aiops.ops import overview as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.snapshot(conn)))
