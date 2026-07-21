# Live verification

`postgres-aiops` has been **exercised against a live PostgreSQL instance**
(PostgreSQL **16.14**, running in Docker) in addition to its mock test suite.
This document records exactly what that run covered, what it did **not**, and the
checklist any further live run should follow. It is deliberately checklist-shaped
so the result is reproducible and auditable — not a subjective "seems fine".

**Scope of the claim**: the catalog / `pg_stat_*` reads, the
`bloat_and_vacuum_analysis` RCA, and the `create_index` / `drop_index` governed
write path (audit + undo) were run against that instance and behaved as the mock
suite predicts. Sections left open below are **not** claimed.

## What the mock suite already guarantees

- Every module imports; the CLI builds; every MCP tool carries the
  `@governed_tool` harness marker (`tests/test_smoke.py`).
- The three flagship analyses are unit-tested against synthetic rows:
  `slow_query_rca` (seq scan / cache-hit / temp-spill / call-count findings, each
  citing its measured number), `bloat_and_vacuum_analysis` (dead-tuple ratio +
  autovacuum lag ranking), and `blocking_lock_chain_rca` (wait-for tree with the
  root blocker named, not just the visible victims).
- SQL safety: all values are bound query parameters; identifiers that cannot be
  parameterised (table / index / GUC names, `ORDER BY` columns, index methods)
  are validated against strict allow-lists and quoted; `EXPLAIN` rejects
  multi-statement input.
- Reversible writes record the correct inverse: `create_index` ↔ `drop_index`
  (with `drop_index` capturing `pg_get_indexdef` **before** dropping), and
  `update_setting` capturing the prior value. Irreversible ops (terminate,
  cancel, vacuum, analyze, reindex, reset stats) declare no undo.
- Governance persistence: audited rows land in the SQLite audit DB. The harness
  authorizes nothing — there is no read-only, deny-rule, or approver gate to
  test; an approver, when supplied, is recorded on the audit row as an optional
  annotation.

## Prerequisites for a live run

A reachable PostgreSQL server you are willing to write to — a Docker container
is enough, and is what the recorded run used:

```bash
docker run -d --name pg-verify -e POSTGRES_PASSWORD=... -p 5432:5432 postgres:16
```

- A role with `pg_monitor` for the reads; `pg_stat_statements` must be
  **installed and loaded** (`shared_preload_libraries`) for `top_queries` and
  `slow_query_rca`.
- A **throwaway database and table** you are willing to vacuum, index, and drop
  indexes on. Never verify against production.

```bash
uv tool install postgres-aiops
postgres-aiops init            # encrypted secret store for the role password
```

## Verification checklist

Boxes marked ✅ were confirmed on **PostgreSQL 16.14 (Docker)**. Unticked boxes
are open — record them as gaps rather than silently passing.

### 1. Connectivity (the fastest live gate)
- [x] ✅ `postgres-aiops doctor` → green (config, encrypted secret store, and a
      real connection + `server_version` against the instance).

### 2. Reads return real, well-shaped data
- [x] ✅ `postgres-aiops overview` → real connection counts, database sizes, and
      longest-query figures matching the instance.
- [x] ✅ `postgres-aiops server version` / `server settings` / `server extensions`
      / `server databases` / `server roles` → real catalog contents.
- [x] ✅ `postgres-aiops activity list` and `activity long --min-seconds 1` →
      real `pg_stat_activity` rows; no crash on NULL query text or missing
      fields.
- [x] ✅ `postgres-aiops table sizes` / `table bloat` / `table autovacuum` →
      values agree with a hand query against `pg_stat_user_tables`.
- [x] ✅ `postgres-aiops index unused` / `index missing` / `index bloat` /
      `index invalid` → real index rows, correct emptiness on a fresh database.
- [ ] `postgres-aiops query top` / `query explain "<sql>"` against an instance
      with `pg_stat_statements` actually loaded (the recorded run did not have
      the extension preloaded — **open gap**).
- [ ] `postgres-aiops repl status` / `repl slots` / `repl wal` against a real
      primary/standby pair (the recorded run was a single node — **open gap**).

### 3. The flagship analyses hold up against real telemetry
- [x] ✅ `postgres-aiops analyze bloat-vacuum` → on a table deliberately loaded
      and then bulk-deleted, the RCA correctly ranked the table with ~50% dead
      tuples first and cited the measured ratio.
- [x] ✅ `postgres-aiops analyze blocking` → returns cleanly (empty chain) with no
      contention present.
- [ ] `analyze blocking` against a **real** blocking pile-up: open a transaction
      that holds a row lock, block a second session on it, and confirm the
      wait-for tree names the first session as root blocker (**open gap** —
      only the empty case was exercised live).
- [ ] `postgres-aiops analyze slow-query` against real `pg_stat_statements` data
      (blocked on the same extension gap as section 2).

### 4. A reversible write + its undo (governance closes the loop)
- [x] ✅ `postgres-aiops remediate create-index <table> <col> --dry-run` → printed
      the exact DDL, changed nothing.
- [x] ✅ `create_index` for real → the index appeared in `pg_indexes`; the result
      carried an `_undo_id`; a row landed in `~/.postgres-aiops/audit.db`.
- [x] ✅ `drop_index` for real → the index was gone, and the undo descriptor held
      the **captured** `pg_get_indexdef` definition, not a reconstruction.
- [x] ✅ `postgres-aiops undo apply <id>` → replayed correctly and recreated the
      index from the captured definition. (A replay bug found in an earlier
      round was fixed and is now covered by a regression test.)
- [ ] `remediate set <guc> <value>` then `undo apply` → the prior value restored
      (**open gap** — `update_setting` was not exercised live).

### 5. Irreversible writes are honest about it
- [x] ✅ `remediate vacuum <table> --analyze` → the dead tuples were actually
      reclaimed (confirmed by re-running `analyze bloat-vacuum`); the audit row
      records prior stats and declares **no** undo.
- [ ] `remediate terminate <pid>` / `remediate cancel <pid>` against a real
      long-running backend (**open gap** — not exercised live).
- [ ] `remediate reindex` on a real index (**open gap**).

### 6. Governance records, it does not gate
- [x] ✅ The harness authorizes nothing — there is no read-only, deny-rule, or
      approver gate to test. A high-risk write ran with no approver set and
      landed an `ok` audit row; when `POSTGRES_AUDIT_APPROVED_BY` and
      `POSTGRES_AUDIT_RATIONALE` were set, they appeared in the audit row as
      optional annotations.
- [x] ✅ Relocation: with `POSTGRES_AIOPS_HOME` set, `audit.db`, the undo store,
      and `secrets.enc` all land under that directory.
- [ ] A tight poll loop trips the runaway budget guard rather than hammering the
      server (verified in the mock suite; not re-run live).

### 7. Cleanup
- [x] ✅ The test indexes were dropped and the throwaway container removed; every
      step above is present in the audit DB.

## Criteria to consider it live-verified

1. Every checklist box is ticked against at least one real PostgreSQL version,
   and the version is recorded. **Current status: satisfied for PostgreSQL
   16.14 for sections 1, 2 (except `pg_stat_statements` and replication), 3
   (bloat/vacuum), 4 (index write + undo), 5 (vacuum), 6 and 7.**
2. Any field-shape mismatch found during a run is fixed and covered by a
   regression test. **Current status: satisfied — the undo-replay bug found in
   the live run was fixed and has a regression test.**
3. The run is written up with the date and package version, matching how the
   product line records its other live-verified tools. **Current status:
   satisfied.**

The remaining open boxes are the honest edge of the claim: `pg_stat_statements`
based analysis, replication reads, session termination, `REINDEX`, and
`ALTER SYSTEM` + undo have **not** been exercised against a live server.

## Notes for maintainers

- `postgres-aiops doctor` is the single fastest live entry point; start there.
- To close the `pg_stat_statements` gap, start the container with
  `-c shared_preload_libraries=pg_stat_statements`, then
  `CREATE EXTENSION pg_stat_statements;` and generate load before running
  `analyze slow-query`.
- To close the blocking gap, hold a row lock in one `psql` session and block a
  second on it — `analyze blocking` should name the first session's pid as the
  root blocker.
- To close the replication gap, add a streaming standby (a second container with
  `pg_basebackup`) and re-run `repl status` / `repl slots` / `repl wal`.
- The analyses also accept **injected records**, so exported rows from a cluster
  you cannot write to still exercise section 3 without any write access.
