"""Replication reads: standby lag, replication slots, and WAL/archiver status.

Each query is written to work on both a primary and a standby (guarded with
``pg_is_in_recovery()``), so calling them against a replica does not error.
"""

from __future__ import annotations

from typing import Any

from postgres_aiops.ops._util import human_bytes, s

_REPLICATION_SQL = """
SELECT pid,
       usename AS username,
       application_name,
       client_addr::text AS client_addr,
       state,
       sync_state,
       sent_lsn::text AS sent_lsn,
       replay_lsn::text AS replay_lsn,
       write_lag,
       flush_lag,
       replay_lag,
       pg_wal_lsn_diff(sent_lsn, replay_lsn) AS replay_lag_bytes
FROM pg_stat_replication
ORDER BY application_name
"""

_SLOTS_SQL = """
SELECT slot_name,
       plugin,
       slot_type,
       database,
       temporary,
       active,
       active_pid,
       restart_lsn::text AS restart_lsn,
       wal_status,
       pg_wal_lsn_diff(
         CASE WHEN pg_is_in_recovery()
              THEN pg_last_wal_receive_lsn() ELSE pg_current_wal_lsn() END,
         restart_lsn) AS retained_bytes
FROM pg_replication_slots
ORDER BY active, slot_name
"""

_WAL_SQL = """
SELECT pg_is_in_recovery() AS in_recovery,
       CASE WHEN pg_is_in_recovery()
            THEN pg_last_wal_receive_lsn()::text
            ELSE pg_current_wal_lsn()::text END AS current_lsn,
       CASE WHEN pg_is_in_recovery()
            THEN NULL
            ELSE pg_walfile_name(pg_current_wal_lsn()) END AS current_wal_file,
       current_setting('wal_level') AS wal_level,
       current_setting('max_wal_size') AS max_wal_size,
       current_setting('min_wal_size') AS min_wal_size,
       current_setting('archive_mode') AS archive_mode
"""

_ARCHIVER_SQL = """
SELECT archived_count, last_archived_wal, last_archived_time,
       failed_count, last_failed_wal, last_failed_time
FROM pg_stat_archiver
"""


def replication_status(conn: Any) -> dict:
    """[READ] Connected standbys and their replay lag (bytes) from pg_stat_replication."""
    rows = conn.query(_REPLICATION_SQL)
    replicas = [
        {
            "pid": r.get("pid"),
            "username": s(r.get("username"), 128),
            "applicationName": s(r.get("application_name"), 128),
            "clientAddr": s(r.get("client_addr"), 64),
            "state": s(r.get("state"), 32),
            "syncState": s(r.get("sync_state"), 32),
            "sentLsn": s(r.get("sent_lsn"), 32),
            "replayLsn": s(r.get("replay_lsn"), 32),
            "replayLag": s(r.get("replay_lag"), 64),
            "replayLagBytes": r.get("replay_lag_bytes"),
            "replayLagPretty": human_bytes(r.get("replay_lag_bytes")),
        }
        for r in rows
    ]
    return {
        "count": len(replicas),
        "replicas": replicas,
        "note": "Empty on a primary with no standbys, or on a standby itself.",
    }


def replication_slots(conn: Any) -> dict:
    """[READ] Replication slots, flagging inactive slots that retain WAL."""
    rows = conn.query(_SLOTS_SQL)
    slots = [
        {
            "slotName": s(r.get("slot_name"), 128),
            "plugin": s(r.get("plugin"), 64),
            "slotType": s(r.get("slot_type"), 32),
            "database": s(r.get("database"), 128),
            "active": bool(r.get("active")),
            "activePid": r.get("active_pid"),
            "restartLsn": s(r.get("restart_lsn"), 32),
            "walStatus": s(r.get("wal_status"), 32),
            "retainedBytes": r.get("retained_bytes"),
            "retainedPretty": human_bytes(r.get("retained_bytes")),
        }
        for r in rows
    ]
    inactive = [slot for slot in slots if not slot["active"]]
    return {
        "count": len(slots),
        "inactiveCount": len(inactive),
        "inactive": inactive,
        "slots": slots,
        "note": (
            "Inactive slots pin WAL and can fill the disk (wal_status='lost' means "
            "WAL was already removed). Drop slots left by decommissioned standbys."
        ),
    }


def wal_status(conn: Any) -> dict:
    """[READ] WAL position, level, size settings and archiver health."""
    wal = conn.query_one(_WAL_SQL) or {}
    arch = conn.query_one(_ARCHIVER_SQL) or {}
    return {
        "inRecovery": bool(wal.get("in_recovery")),
        "currentLsn": s(wal.get("current_lsn"), 32),
        "currentWalFile": s(wal.get("current_wal_file"), 64),
        "walLevel": s(wal.get("wal_level"), 32),
        "maxWalSize": s(wal.get("max_wal_size"), 32),
        "minWalSize": s(wal.get("min_wal_size"), 32),
        "archiveMode": s(wal.get("archive_mode"), 32),
        "archiver": {
            "archivedCount": arch.get("archived_count"),
            "lastArchivedWal": s(arch.get("last_archived_wal"), 64),
            "failedCount": arch.get("failed_count"),
            "lastFailedWal": s(arch.get("last_failed_wal"), 64),
        },
    }
