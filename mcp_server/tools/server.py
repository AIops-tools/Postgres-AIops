"""Server-level PostgreSQL MCP tools (read-only): overview + catalog reads."""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from postgres_aiops.governance import governed_tool
from postgres_aiops.ops import overview as overview_ops
from postgres_aiops.ops import server as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def overview(target: Optional[str] = None) -> dict:
    """[READ] One-shot cluster health snapshot.

    Version + uptime, connections by state, idle-in-transaction count, the
    longest-running query, the worst dead-tuple table, and standby replay lag —
    each section captured defensively so one failing probe does not sink the rest.

    Args:
        target: Target name from config; omit for the default.
    """
    return overview_ops.snapshot(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def server_version(target: Optional[str] = None) -> dict:
    """[READ] Server version, uptime, recovery state, and data directory.

    Args:
        target: Target name from config; omit for the default.
    """
    return ops.server_version(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("list")
def show_settings(pattern: Optional[str] = None, target: Optional[str] = None) -> list:
    """[READ] Configuration parameters from pg_settings.

    Args:
        pattern: Optional case-insensitive substring to filter setting names
            (e.g. 'work_mem', 'autovacuum').
        target: Target name from config; omit for the default.
    """
    return ops.show_settings(_get_connection(target), pattern)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("list")
def list_extensions(target: Optional[str] = None) -> list:
    """[READ] Installed extensions and whether a newer version is available.

    Args:
        target: Target name from config; omit for the default.
    """
    return ops.list_extensions(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("list")
def list_databases(target: Optional[str] = None) -> list:
    """[READ] Databases with owner, encoding and on-disk size (largest first).

    Args:
        target: Target name from config; omit for the default.
    """
    return ops.list_databases(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("list")
def list_roles(target: Optional[str] = None) -> list:
    """[READ] Roles and their attributes (superuser/login/replication/…).

    Args:
        target: Target name from config; omit for the default.
    """
    return ops.list_roles(_get_connection(target))
