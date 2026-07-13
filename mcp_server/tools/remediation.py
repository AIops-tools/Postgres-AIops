"""PostgreSQL maintenance MCP tools (guarded writes).

The state-changing tools. Every one is wrapped with the governance harness
(audit + graduated approval tier) and takes a ``dry_run`` preview. Reversible
writes pass an ``undo=`` callback that turns the fetched before-state into an
inverse descriptor the harness records; irreversible ones record none.

Risk tiers:
  * terminate_backend / cancel_query / drop_index = high (destructive / irreversible)
  * run_vacuum / run_analyze / create_index / reindex / update_setting = medium
"""

from typing import Any, Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from postgres_aiops.governance import governed_tool
from postgres_aiops.ops import remediation as ops

# ── undo descriptors (built from the fetched before-state) ──────────────────


def _create_index_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    if not isinstance(result, dict) or not result.get("index"):
        return None
    return {
        "tool": "drop_index",
        "params": {"name": result["index"]},
        "skill": "postgres-aiops",
        "note": "Inverse of create_index: drop the index that was just created.",
    }


def _drop_index_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    if not isinstance(result, dict):
        return None
    indexdef = (result.get("priorState") or {}).get("indexdef")
    if not indexdef:
        return None
    return {
        "tool": "create_index",
        "params": {"definition": indexdef},
        "skill": "postgres-aiops",
        "note": (
            "Inverse of drop_index: recreate the index from its captured "
            "definition (replay this CREATE INDEX statement)."
        ),
    }


def _update_setting_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    if not isinstance(result, dict):
        return None
    prior = (result.get("priorState") or {}).get("value")
    if prior is None or prior == "":
        return None
    return {
        "tool": "update_setting",
        "params": {"name": params.get("name"), "value": prior},
        "skill": "postgres-aiops",
        "note": "Inverse of update_setting: ALTER SYSTEM SET back to the prior value.",
    }


# ── activity control (high; irreversible) ───────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="high")
@tool_errors("dict")
def terminate_backend(pid: int, dry_run: bool = False, target: Optional[str] = None) -> dict:
    """[WRITE][risk=high] Terminate a backend (pg_terminate_backend). No safe inverse.

    Captures the backend's pid + query for the audit trail; a terminate cannot be
    undone, so no undo is offered. Pass dry_run=True to preview.

    Args:
        pid: Backend process id (from list_activity).
        dry_run: If True, preview without terminating.
        target: Target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        return {"dryRun": True, "wouldTerminate": {"pid": pid}}
    return ops.terminate_backend(conn, pid)


@mcp.tool()
@governed_tool(risk_level="high")
@tool_errors("dict")
def cancel_query(pid: int, dry_run: bool = False, target: Optional[str] = None) -> dict:
    """[WRITE][risk=high] Cancel a backend's running query (pg_cancel_backend). No inverse.

    Captures the backend's pid + query for audit; a cancel has no undo. Pass
    dry_run=True to preview.

    Args:
        pid: Backend process id (from list_activity).
        dry_run: If True, preview without cancelling.
        target: Target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        return {"dryRun": True, "wouldCancel": {"pid": pid}}
    return ops.cancel_query(conn, pid)


# ── vacuum / analyze (medium; irreversible, record prior stats) ─────────────


@mcp.tool()
@governed_tool(risk_level="medium")
@tool_errors("dict")
def run_vacuum(
    table: str,
    full: bool = False,
    analyze: bool = False,
    dry_run: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=medium] VACUUM a table (optionally FULL/ANALYZE). Records prior stats.

    No undo (a vacuum has no inverse); the prior dead-tuple/last-vacuum stats are
    captured for the audit trail. VACUUM FULL takes an exclusive lock. Pass
    dry_run=True to preview.

    Args:
        table: Table name (optionally schema-qualified, e.g. public.orders).
        full: Run VACUUM FULL (rewrites the table, exclusive lock).
        analyze: Also refresh planner statistics (VACUUM ANALYZE).
        dry_run: If True, preview without running.
        target: Target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        return {"dryRun": True, "wouldVacuum": {"table": table, "full": full, "analyze": analyze}}
    return ops.run_vacuum(conn, table, full=full, analyze=analyze)


@mcp.tool()
@governed_tool(risk_level="medium")
@tool_errors("dict")
def run_analyze(table: str, dry_run: bool = False, target: Optional[str] = None) -> dict:
    """[WRITE][risk=medium] ANALYZE a table to refresh planner statistics.

    No undo; captures prior stats for audit. Pass dry_run=True to preview.

    Args:
        table: Table name (optionally schema-qualified).
        dry_run: If True, preview without running.
        target: Target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        return {"dryRun": True, "wouldAnalyze": {"table": table}}
    return ops.run_analyze(conn, table)


# ── index create/drop/reindex ───────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="medium", undo=_create_index_undo)
@tool_errors("dict")
def create_index(
    table: str,
    columns: list[str],
    name: Optional[str] = None,
    unique: bool = False,
    concurrently: bool = False,
    method: Optional[str] = None,
    dry_run: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=medium] Create an index. Reversible: undo drops the created name.

    Supports CONCURRENTLY (non-blocking build). The created name is returned so
    the harness records an undo that drops exactly this index. Pass dry_run=True
    to preview.

    Args:
        table: Table to index (optionally schema-qualified).
        columns: Column names to index.
        name: Index name (auto-generated from table+columns when omitted).
        unique: Create a UNIQUE index.
        concurrently: Build with CONCURRENTLY (no table lock).
        method: Index method — btree/hash/gist/gin/brin/spgist (default btree).
        dry_run: If True, preview without creating.
        target: Target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        return {"dryRun": True, "wouldCreate": {"table": table, "columns": columns, "name": name}}
    return ops.create_index(
        conn, table, columns, name=name, unique=unique,
        concurrently=concurrently, method=method,
    )


@mcp.tool()
@governed_tool(risk_level="high", undo=_drop_index_undo)
@tool_errors("dict")
def drop_index(
    name: str,
    concurrently: bool = False,
    dry_run: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=high] Drop an index. Reversible: captures pg_get_indexdef first.

    Before dropping, the exact index definition is captured so the harness records
    an undo that recreates it. Pass dry_run=True to preview.

    Args:
        name: Index name (optionally schema-qualified).
        concurrently: Drop with CONCURRENTLY (no table lock).
        dry_run: If True, preview without dropping.
        target: Target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        return {"dryRun": True, "wouldDrop": {"name": name}}
    return ops.drop_index(conn, name, concurrently=concurrently)


@mcp.tool()
@governed_tool(risk_level="medium")
@tool_errors("dict")
def reindex(
    target_name: str,
    kind: str = "INDEX",
    concurrently: bool = False,
    dry_run: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=medium] REINDEX an index/table/schema (rebuild in place, no undo).

    Rebuilds physical index storage; there is no inverse. Pass dry_run=True to
    preview.

    Args:
        target_name: The index/table/schema name to reindex.
        kind: INDEX, TABLE, or SCHEMA (default INDEX).
        concurrently: Rebuild with CONCURRENTLY.
        dry_run: If True, preview without rebuilding.
        target: Target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        return {"dryRun": True, "wouldReindex": {"kind": kind, "target": target_name}}
    return ops.reindex(conn, target_name, kind=kind, concurrently=concurrently)


# ── server settings (medium; reversible via ALTER SYSTEM) ───────────────────


@mcp.tool()
@governed_tool(risk_level="medium", undo=_update_setting_undo)
@tool_errors("dict")
def update_setting(
    name: str,
    value: str,
    dry_run: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=medium] ALTER SYSTEM SET a parameter. Reversible: captures prior value.

    Writes postgresql.auto.conf; most parameters need SELECT pg_reload_conf() (or
    a restart for postmaster-context settings) to take effect — reported but NOT
    performed automatically. The prior value is captured so the harness records an
    undo that sets it back. Pass dry_run=True to preview.

    Args:
        name: The configuration parameter name (e.g. work_mem).
        value: The new value (as a string).
        dry_run: If True, preview without changing.
        target: Target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        return {"dryRun": True, "wouldSet": {"name": name, "value": value}}
    return ops.update_setting(conn, name, value)
