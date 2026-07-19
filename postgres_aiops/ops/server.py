"""Server-level reads: version, settings, extensions, databases, roles.

All read-only queries against the system catalogs. Values that could be large or
caller-influenced (setting descriptions) are bounded via ``s`` before returning.
"""

from __future__ import annotations

from typing import Any

from postgres_aiops.ops._util import human_bytes, opt, s

_VERSION_SQL = """
SELECT version() AS version,
       current_setting('server_version') AS server_version,
       current_setting('server_version_num') AS server_version_num,
       pg_postmaster_start_time() AS start_time,
       (now() - pg_postmaster_start_time()) AS uptime,
       pg_is_in_recovery() AS in_recovery,
       current_database() AS database,
       current_setting('data_directory', true) AS data_directory
"""

_SETTINGS_SQL = """
SELECT name, setting, unit, category, context, source, pending_restart,
       boot_val, reset_val, short_desc
FROM pg_settings
WHERE (%(pattern)s IS NULL OR name ILIKE %(pattern)s)
ORDER BY category, name
"""

_EXTENSIONS_SQL = """
SELECT e.extname AS name, e.extversion AS installed_version,
       a.default_version, a.comment
FROM pg_extension e
LEFT JOIN pg_available_extensions a ON a.name = e.extname
ORDER BY e.extname
"""

_DATABASES_SQL = """
SELECT d.datname AS name,
       pg_get_userbyid(d.datdba) AS owner,
       pg_encoding_to_char(d.encoding) AS encoding,
       d.datcollate AS collate,
       d.datctype AS ctype,
       pg_database_size(d.datname) AS size_bytes,
       d.datistemplate AS is_template,
       d.datallowconn AS allow_conn
FROM pg_database d
WHERE d.datistemplate = false
ORDER BY pg_database_size(d.datname) DESC
"""

_ROLES_SQL = """
SELECT rolname AS name, rolsuper AS superuser, rolcreatedb AS create_db,
       rolcreaterole AS create_role, rolcanlogin AS can_login,
       rolreplication AS replication, rolbypassrls AS bypass_rls,
       rolconnlimit AS conn_limit, rolvaliduntil AS valid_until
FROM pg_roles
ORDER BY rolname
"""


def server_version(conn: Any) -> dict:
    """[READ] Server version, uptime, recovery state and data directory."""
    row = conn.query_one(_VERSION_SQL) or {}
    return {
        "version": s(row.get("version"), 300),
        "serverVersion": s(row.get("server_version"), 50),
        "serverVersionNum": row.get("server_version_num"),
        "startTime": s(row.get("start_time"), 64),
        "uptime": s(row.get("uptime"), 64),
        "inRecovery": bool(row.get("in_recovery")),
        "database": s(row.get("database"), 128),
        "dataDirectory": opt(row.get("data_directory"), 256),
    }


def show_settings(conn: Any, pattern: str | None = None) -> list[dict]:
    """[READ] Configuration parameters (pg_settings), optional ILIKE ``pattern``."""
    like = f"%{pattern}%" if pattern else None
    rows = conn.query(_SETTINGS_SQL, {"pattern": like})
    return [
        {
            "name": s(r.get("name"), 128),
            "setting": s(r.get("setting"), 256),
            "unit": opt(r.get("unit"), 32),
            "category": s(r.get("category"), 128),
            "context": s(r.get("context"), 32),
            "source": s(r.get("source"), 64),
            "pendingRestart": bool(r.get("pending_restart")),
            "bootVal": s(r.get("boot_val"), 256),
            "resetVal": s(r.get("reset_val"), 256),
            "description": opt(r.get("short_desc"), 256),
        }
        for r in rows
    ]


def list_extensions(conn: Any) -> list[dict]:
    """[READ] Installed extensions and whether a newer version is available."""
    rows = conn.query(_EXTENSIONS_SQL)
    out = []
    for r in rows:
        installed = opt(r.get("installed_version"), 32)
        default = opt(r.get("default_version"), 32)
        out.append({
            "name": s(r.get("name"), 128),
            "installedVersion": installed,
            "defaultVersion": default,
            "updateAvailable": bool(default and installed and default != installed),
            "comment": opt(r.get("comment"), 256),
        })
    return out


def list_databases(conn: Any) -> list[dict]:
    """[READ] Databases with owner, encoding and on-disk size (largest first)."""
    rows = conn.query(_DATABASES_SQL)
    return [
        {
            "name": s(r.get("name"), 128),
            "owner": s(r.get("owner"), 128),
            "encoding": s(r.get("encoding"), 32),
            "collate": s(r.get("collate"), 64),
            "ctype": s(r.get("ctype"), 64),
            "sizeBytes": r.get("size_bytes"),
            "sizePretty": human_bytes(r.get("size_bytes")),
            "allowConn": bool(r.get("allow_conn")),
        }
        for r in rows
    ]


def list_roles(conn: Any) -> list[dict]:
    """[READ] Roles and their attributes (superuser/login/replication/…)."""
    rows = conn.query(_ROLES_SQL)
    return [
        {
            "name": s(r.get("name"), 128),
            "superuser": bool(r.get("superuser")),
            "createDb": bool(r.get("create_db")),
            "createRole": bool(r.get("create_role")),
            "canLogin": bool(r.get("can_login")),
            "replication": bool(r.get("replication")),
            "bypassRls": bool(r.get("bypass_rls")),
            "connLimit": r.get("conn_limit"),
            "validUntil": opt(r.get("valid_until"), 64),
        }
        for r in rows
    ]
