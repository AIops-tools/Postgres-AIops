"""MCP server wrapping postgres-aiops operations (stdio transport).

Thin adapter layer: each ``@mcp.tool()`` function (in ``mcp_server/tools/``)
delegates to the ``postgres_aiops`` ops package and is wrapped with the
postgres-aiops ``@governed_tool`` harness (audit / budget / undo / risk-tier).

Standalone, self-governed PostgreSQL DBA operations (preview).
For PostgreSQL servers/clusters via psycopg 3.

Source: https://github.com/AIops-tools/Postgres-AIops
License: MIT
"""

import logging

from mcp_server._shared import _safe_error, mcp, tool_errors

# Importing the tool modules registers every @mcp.tool() onto the shared
# `mcp` instance. Order does not matter; each module is self-contained.
from mcp_server.tools import (  # noqa: F401 — side effects
    activity,
    analysis,
    indexes,
    queries,
    remediation,
    replication,
    server,
    tables,
    undo,
)

__all__ = ["mcp", "main", "_safe_error", "tool_errors"]


def main() -> None:
    """Run the MCP server over stdio."""
    logging.basicConfig(level=logging.INFO)
    mcp.run(transport="stdio")
