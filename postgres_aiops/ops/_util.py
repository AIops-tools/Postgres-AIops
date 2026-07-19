"""Shared helpers for the PostgreSQL ops modules.

Two jobs:

  * ``s`` — pass catalog/query text through the governance ``sanitize`` (bounded
    length, control-character stripping) before it reaches an agent.
  * ``qualify`` / ``quote_ident`` — the ONLY sanctioned way to place an
    identifier (schema/table/index/column) into a statement that cannot be
    parameterised (DDL, ``VACUUM``, ``ANALYZE``, ``REINDEX``). Every part is
    validated against strict identifier rules and then double-quoted, so a value
    that is not a plain identifier is rejected rather than interpolated.

Values (pids, thresholds, limits, setting values) are ALWAYS passed as query
parameters — never string-formatted into SQL.
"""

from __future__ import annotations

import re
from typing import Any

from postgres_aiops.governance import opt_str, sanitize

# A PostgreSQL unquoted identifier component: letter/underscore then
# letters/digits/underscore/dollar. We deliberately reject everything else
# (spaces, quotes, semicolons, operators) so interpolation cannot inject SQL.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")

# Whitelist of ORDER-BY columns callers may choose from (maps a friendly name to
# a real column). Used so an ordering choice is never taken from raw user text.
STATEMENT_ORDER_COLUMNS = {
    "total_time": "total_exec_time",
    "mean_time": "mean_exec_time",
    "calls": "calls",
    "rows": "rows",
    "io": "shared_blks_read",
}


def s(value: Any, limit: int = 200) -> str:
    """Sanitize an arbitrary value to a bounded, injection-safe string.

    Folds SQL ``NULL`` into ``""``. Use only for columns that are genuinely
    always present; for a nullable column use :func:`opt` instead.
    """
    return sanitize(str(value if value is not None else ""), limit)


def opt(value: Any, limit: int = 200) -> str | None:
    """Sanitize a nullable column, preserving SQL ``NULL`` as ``None``.

    PostgreSQL catalogs are full of columns whose ``NULL`` carries meaning:
    ``pg_stat_user_tables.last_autovacuum`` is NULL because the table was *never*
    autovacuumed, ``pg_settings.unit`` is NULL because the setting is not a
    numeric quantity, ``pg_replication_slots.database`` is NULL because the slot
    is physical rather than logical. Rendering those as ``""`` throws the fact
    away — a consumer cannot tell "never vacuumed" from "vacuumed at the empty
    string", and a smaller local model will confidently invent the difference.

    So absence stays ``None`` (JSON ``null``) and only a genuinely empty value
    comes back as ``""``.
    """
    return opt_str(value, limit)


def quote_ident(part: str) -> str:
    """Validate a single identifier component and return it double-quoted.

    Raises ``ValueError`` for anything that is not a plain identifier — this is
    the boundary that makes identifier interpolation safe.
    """
    if not isinstance(part, str) or not _IDENT_RE.match(part):
        raise ValueError(
            f"Invalid SQL identifier {part!r}: only letters, digits, underscore "
            f"and '$' are allowed (must start with a letter/underscore)."
        )
    return '"' + part + '"'


def qualify(name: str) -> str:
    """Validate + quote a possibly schema-qualified name (``schema.table``).

    ``qualify('public.orders')`` → ``"public"."orders"``; ``qualify('orders')``
    → ``"orders"``. Each component is validated by :func:`quote_ident`.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Empty identifier is not allowed.")
    parts = name.split(".")
    if len(parts) > 2:
        raise ValueError(f"Too many name parts in {name!r} (expected schema.table).")
    return ".".join(quote_ident(p) for p in parts)


def quote_literal(value: str) -> str:
    """Quote a string as a SQL literal (single-quoted, doubling embedded quotes).

    Only used for ``ALTER SYSTEM SET`` where the value cannot be parameterised.
    """
    return "'" + str(value).replace("'", "''") + "'"


def order_column(choice: str) -> str:
    """Map a caller's order-by choice to a real column via the whitelist."""
    col = STATEMENT_ORDER_COLUMNS.get(choice)
    if col is None:
        allowed = ", ".join(sorted(STATEMENT_ORDER_COLUMNS))
        raise ValueError(f"Unknown order_by '{choice}'. Allowed: {allowed}.")
    return col


def human_bytes(n: Any) -> str:
    """Render a byte count as a human string (e.g. 1536 -> '1.5 kB')."""
    try:
        size = float(n)
    except (TypeError, ValueError):
        return "0 bytes"
    for unit in ("bytes", "kB", "MB", "GB", "TB"):
        if abs(size) < 1024.0 or unit == "TB":
            if unit == "bytes":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"
