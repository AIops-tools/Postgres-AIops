---
name: postgres-aiops
description: >
  Use this skill whenever the user needs to operate or troubleshoot a PostgreSQL server/cluster as a DBA — a one-shot cluster health overview; server reads (version/uptime, settings, extensions, databases, roles); activity (sessions, idle-in-transaction, long-running queries, locks); query stats (pg_stat_statements top-N, EXPLAIN a statement); index health (unused indexes, missing-index hints, bloat, invalid/duplicate); table health (sizes, dead-tuple bloat, autovacuum status); replication (standby lag, replication slots, WAL); three flagship analyses — slow-query RCA (worst pg_stat_statements entry + EXPLAIN → cause/action), bloat & vacuum analysis (dead tuples + autovacuum lag → recommendation), and blocking lock-chain RCA (build the wait-for tree, name the root blocker); and guarded writes (terminate a backend, cancel a query, VACUUM/ANALYZE, create/drop an index, REINDEX, ALTER SYSTEM SET a parameter, reset query stats).
  Always use this skill for "postgres health check", "why is this query slow", "pg_stat_statements top queries", "EXPLAIN this", "table/index bloat", "which indexes are unused", "missing index", "autovacuum status", "who is blocking whom", "kill the backend holding the lock", "replication lag", "replication slots", "VACUUM this table", "create/drop an index", or "ALTER SYSTEM SET work_mem" when the context is a PostgreSQL database.
  Do NOT use when the target is OT / industrial equipment (Modbus, OPC-UA, PLCs — use industrial-aiops), a hypervisor, a storage appliance, a backup product, a container/cluster orchestrator, or a non-PostgreSQL database (negative routing hints only).
  Preview — common PostgreSQL DBA operations with a built-in governance harness (audit, policy, token budget, undo, risk-tiers). Mock-validated only, not run against a live cluster.
installer:
  kind: uv
  package: postgres-aiops
argument-hint: "[pid / table / index name or describe your DBA task]"
allowed-tools:
  - Bash
metadata: {"openclaw":{"requires":{"env":["POSTGRES_AIOPS_CONFIG"],"bins":["postgres-aiops"],"config":["~/.postgres-aiops/config.yaml","~/.postgres-aiops/secrets.enc"]},"optional":{"env":["POSTGRES_AIOPS_MASTER_PASSWORD"]},"primaryEnv":"POSTGRES_AIOPS_CONFIG","homepage":"https://github.com/AIops-tools/Postgres-AIops","emoji":"🐘","os":["macos","linux"]}}
compatibility: >
  Standalone, self-governed PostgreSQL DBA operations (preview). The governance harness (audit, policy, token/runaway budget, undo, risk-tiers) is bundled in the package — no external skill-family dependency. Connects via psycopg 3 and reads the system catalogs and pg_stat_* views.
  All write operations are audited to a local SQLite DB under ~/.postgres-aiops/ (relocatable via POSTGRES_AIOPS_HOME).
  Credentials: the PostgreSQL role password is stored ENCRYPTED in ~/.postgres-aiops/secrets.enc (Fernet/AES-128 + scrypt-derived key) — never plaintext on disk. Run 'postgres-aiops init' to onboard, or 'postgres-aiops secret set <target>' to add one. The store is unlocked by a master password from POSTGRES_AIOPS_MASTER_PASSWORD (non-interactive/MCP/CI) or an interactive prompt (CLI on a TTY). A legacy plaintext env var PG_<TARGET_NAME_UPPER>_PASSWORD is still honoured as a fallback with a deprecation warning (migrate with 'postgres-aiops secret migrate'). The password is passed to psycopg.connect at connect time and held only in memory; it is never logged or echoed.
  SQL safety: all values are bound query parameters; the few identifiers that cannot be parameterised (table/index/GUC names, ORDER BY columns, index methods) are validated against strict allow-lists and quoted before interpolation. EXPLAIN rejects multi-statement input.
  State-changing operations require double confirmation at the CLI layer and support --dry-run. All write tools pass through the @governed_tool decorator (pre-check + budget guard + audit + risk-tier gate) and take a dry_run preview. Reversible writes fetch the real before-state first and record a faithful inverse (create_index↔drop_index, where drop captures pg_get_indexdef; update_setting restores the prior value); irreversible ops (terminate/cancel, vacuum/analyze, reindex, reset stats) record prior stats only.
  Webhooks: none — no outbound network calls beyond the configured PostgreSQL connection.
  SSL: sslmode follows libpq (default prefer); set require/verify-full on untrusted networks.
  Transitive dependencies: psycopg[binary] (PostgreSQL driver) and the MCP SDK. No post-install scripts or background services.
  PREVIEW: mock-validated only; the catalog / pg_stat_* queries are modelled from documented shapes and need live verification. Community-maintained; not affiliated with the PostgreSQL project — trademarks belong to their owners.
---

# Postgres AIops (preview)

> **Disclaimer**: Community-maintained open-source project, **not affiliated with, endorsed by, or sponsored by the PostgreSQL Global Development Group or any vendor.** "PostgreSQL" and related trademarks belong to their owners. Source at [github.com/AIops-tools/Postgres-AIops](https://github.com/AIops-tools/Postgres-AIops) under the MIT license.

Governed PostgreSQL DBA operations — **33 MCP tools**, every one wrapped with the bundled `@governed_tool` harness: a local unified audit log under `~/.postgres-aiops/`, policy engine, token/runaway budget guard, undo-token recording, and graduated-autonomy risk tiers. The role password is stored **encrypted** (`~/.postgres-aiops/secrets.enc`, Fernet + scrypt) — never plaintext on disk.

> **Standalone**: the governance harness is bundled in the package (`postgres_aiops.governance`) — postgres-aiops has no external skill-family dependency. **Preview / mock-only**: not run against a live cluster.

## What This Skill Does

| Domain | Tools | Count | Read or Write |
|--------|-------|:-----:|:-------------:|
| **Overview** | cluster health snapshot | 1 | 1 read |
| **Server** | version, settings, extensions, databases, roles | 5 | 5 read |
| **Activity** | sessions, long-running queries, locks | 3 | 3 read |
| **Queries** | top-N (pg_stat_statements), EXPLAIN | 2 | 2 read |
| **Indexes** | unused, missing hints, bloat, invalid/duplicate | 4 | 4 read |
| **Tables** | sizes, dead-tuple bloat, autovacuum status | 3 | 3 read |
| **Replication** | status/lag, slots, WAL | 3 | 3 read |
| **Analysis (flagship)** | slow-query RCA, bloat/vacuum, blocking chains | 3 | 3 read |
| **Writes** | terminate, cancel, drop-index | 3 | 3 write (high) |
| | vacuum, analyze, create-index, reindex, ALTER SYSTEM, reset-stats | 6 | 6 write (medium) |

The flagship analyses accept injected records for pure/offline analysis, or pull live from a configured target. `top_queries` / `slow_query_rca` require the `pg_stat_statements` extension; the read role should have `pg_monitor`.

## Quick Install

```bash
uv tool install postgres-aiops
postgres-aiops init       # interactive wizard: connection + encrypted password
postgres-aiops doctor
```

## When to Use This Skill

- Triage a cluster (`overview`): version/uptime, connections by state, idle-in-transaction, longest query, worst bloat, replica lag
- Root-cause a slow query (`analyze slow-query` / `slow_query_rca`): the worst `pg_stat_statements` entry + EXPLAIN → cited cause and action
- Decide what to vacuum (`analyze bloat-vacuum` / `bloat_and_vacuum_analysis`): tables ranked by dead-tuple ratio + autovacuum lag
- Untangle a lock pile-up (`analyze blocking` / `blocking_lock_chain_rca`): the wait-for tree with the root blocker named
- Find unused / missing / bloated indexes; check autovacuum status and table sizes; inspect replication lag and slots
- Terminate/cancel a backend, VACUUM/ANALYZE, create/drop an index (reversible), REINDEX, or ALTER SYSTEM SET — all with dry-run + double-confirm

**Do NOT use when** the target is OT/industrial equipment (use industrial-aiops), a hypervisor, a storage appliance, a backup product, a container cluster, or a non-PostgreSQL database.

## Related Skills — Skill Routing

| If the user wants… | Use |
|--------------------|-----|
| PostgreSQL DBA-ops: slow queries, bloat, locks, index/vacuum maintenance | **postgres-aiops** (this skill) |
| OT / industrial edge (Modbus, OPC-UA, PLC, PROFINET) | the **industrial-aiops** line |
| Hypervisor VM lifecycle (power, snapshot, migrate) | a hypervisor ops skill |
| Container/cluster lifecycle | a cluster ops skill |

## Common Workflows

### Root-cause a slow database

1. `postgres-aiops analyze slow-query` → the worst `pg_stat_statements` entry with cited findings (seq scan, low cache-hit ratio, temp spill, high calls) and an action for each
2. `postgres-aiops query explain "<sql>"` → confirm the plan (look for Seq Scan on a large table)
3. If the fix is an index: `postgres-aiops remediate create-index <table> <cols> --concurrently --dry-run`, then re-run without `--dry-run`

### Reclaim bloat safely (reversible index changes)

1. `postgres-aiops analyze bloat-vacuum` → tables ranked by dead-tuple ratio + autovacuum lag
2. `postgres-aiops remediate vacuum <table> --analyze --dry-run` → preview, then re-run to VACUUM ANALYZE
3. For a redundant index: `postgres-aiops remediate drop-index <name>` — captures `pg_get_indexdef` first and records an inverse recreate undo descriptor

### Break a blocking pile-up

1. `postgres-aiops analyze blocking` → the wait-for tree names the **root blocker** pid
2. Inspect it with `postgres-aiops activity list --state active`
3. `postgres-aiops remediate cancel <pid>` (or `terminate <pid>`) — dry-run + double-confirm; the prior query is captured for audit

### Offline analysis (no live cluster)

Pass data straight to the analysis tools — `slow_query_rca(statements=[...])`, `bloat_and_vacuum_analysis(tables=[...])`, or `blocking_lock_chain_rca(pairs=[...])` — to analyse an exported dataset without connecting.

## Governance & Safety

- Every tool is audited to `~/.postgres-aiops/audit.db` (relocatable via `POSTGRES_AIOPS_HOME`).
- High-risk ops can require a named approver: set `POSTGRES_AUDIT_APPROVED_BY` and `POSTGRES_AUDIT_RATIONALE` (the env-var names the bundled harness reads).
- **Secure by default (v0.2.0+)**: with no `~/.postgres-aiops/rules.yaml`, high/critical operations are denied unless `POSTGRES_AUDIT_APPROVED_BY` names an approver (set `POSTGRES_AUDIT_RATIONALE` too). `postgres-aiops init` seeds a starter rules.yaml; an operator-authored rules file is honoured as-is.
- Writes support `--dry-run` / `dry_run=True` and double confirmation at the CLI.
- Reversible writes fetch the real before-state and record an inverse descriptor; irreversible ops (terminate/cancel, vacuum/analyze, reindex, reset stats) record prior stats only.
- All values are bound query parameters; identifiers that cannot be parameterised are validated and quoted.

## References

- `references/capabilities.md` — full tool + field reference
- `references/cli-reference.md` — CLI command reference
- `references/setup-guide.md` — onboarding, credentials, and connectivity
