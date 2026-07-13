<!-- mcp-name: io.github.AIops-tools/postgres-aiops -->

# Postgres AIops (preview)

> **Disclaimer**: Community-maintained open-source project. **Not affiliated with, endorsed by, or sponsored by the PostgreSQL Global Development Group or any vendor.** "PostgreSQL" and the elephant logo are trademarks of the PostgreSQL Community Association; all product/trademark names belong to their respective owners. MIT licensed.

Governed AI-ops for **PostgreSQL DBA operations** — connecting to a server with
**psycopg 3** and reading the system catalogs and `pg_stat_*` views — with a
**built-in governance harness**: unified audit log, policy engine, token/runaway
budget guard, undo-token recording, and graduated-autonomy risk tiers.
**Preview — mock-validated only, not run against a live cluster.**

## What it does

Three flagship signature analyses, plus the guarded reads and writes around them:

- **Slow-query RCA** — take the worst `pg_stat_statements` entry (plus an optional
  `EXPLAIN` plan) and map its numbers — mean time, cache-hit ratio, temp spill,
  call count, plan node types — to a cited cause and a concrete action. Every
  finding carries its measured number, not a black-box verdict.
- **Bloat & vacuum analysis** — combine per-table dead-tuple ratio and autovacuum
  recency into a ranked, cited recommendation (VACUUM / tune autovacuum).
- **Blocking lock-chain RCA** — build the wait-for tree from `pg_blocking_pids`,
  name the **root blocker** (blocks others, waits on none), and give the action;
  a cycle is flagged as a likely deadlock.

## What works

- **CLI** (`postgres-aiops ...`): `init`, `overview`, `server`, `activity`, `query`, `index`, `table`, `repl`, `analyze`, `remediate`, `secret`, `doctor`, `mcp`.
- **MCP server** (`postgres-aiops mcp` or `postgres-aiops-mcp`): **33 tools** (24 read, 9 write), every one wrapped with the bundled `@governed_tool` harness.
- **Encrypted credentials**: the role password lives in an encrypted store `~/.postgres-aiops/secrets.enc` (Fernet + scrypt) — **never plaintext on disk**. Unlock with a master password from `POSTGRES_AIOPS_MASTER_PASSWORD` (MCP/CI) or an interactive prompt (CLI).
- **Reversibility**: mutating writes fetch the **real before-state first** and record a faithful inverse — `create_index`↔`drop_index`; `drop_index` captures `pg_get_indexdef` so undo recreates it exactly; `update_setting` captures the prior value so undo sets it back. Irreversible ops (`terminate_backend`, `cancel_query`, `run_vacuum`, `run_analyze`, `reindex`, `reset_query_stats`) record prior stats for audit but declare no undo.
- **Safety**: every state-changing CLI op supports `--dry-run` and requires double confirmation; every write MCP tool takes a `dry_run` preview. All identifiers that cannot be parameterised (table/index/GUC names) are validated and quoted; all values are bound query parameters.

## Capability matrix (33 MCP tools)

| Domain | Tools | Count | R/W |
|--------|-------|:-----:|:---:|
| **Overview** | `overview` | 1 | read |
| **Server** | `server_version`, `show_settings`, `list_extensions`, `list_databases`, `list_roles` | 5 | read |
| **Activity** | `list_activity`, `long_running_queries`, `list_locks` | 3 | read |
| **Queries** | `top_queries`, `explain_query` | 2 | read |
| **Indexes** | `unused_indexes`, `missing_index_hints`, `index_bloat`, `invalid_indexes` | 4 | read |
| **Tables** | `table_sizes`, `table_bloat`, `autovacuum_status` | 3 | read |
| **Replication** | `replication_status`, `replication_slots`, `wal_status` | 3 | read |
| **Analysis (flagship)** | `slow_query_rca`, `bloat_and_vacuum_analysis`, `blocking_lock_chain_rca` | 3 | read |
| **Writes** | `terminate_backend`, `cancel_query`, `drop_index` | 3 | write (high) |
| | `run_vacuum`, `run_analyze`, `create_index`, `reindex`, `update_setting`, `reset_query_stats` | 6 | write (medium) |

The flagship analyses accept injected records for pure/offline analysis, or pull
live from a configured target. `top_queries`/`slow_query_rca` require the
`pg_stat_statements` extension; the read role should have `pg_monitor`.

## Quick start

```bash
uv tool install postgres-aiops             # or: pipx install postgres-aiops
postgres-aiops init                        # wizard: add a target + store the password (encrypted)
postgres-aiops doctor                      # verify config, secrets, connectivity
postgres-aiops overview                    # one-shot cluster health snapshot
postgres-aiops analyze slow-query          # RCA the worst pg_stat_statements entry
postgres-aiops table bloat                 # dead-tuple bloat proxy per table
```

Run as an MCP server (stdio):

```bash
export POSTGRES_AIOPS_MASTER_PASSWORD=...  # unlock secrets non-interactively
postgres-aiops-mcp
```

## Governance

Every MCP tool passes through the bundled `@governed_tool` harness:

- **Audit** — every call (params, result, status, duration, risk tier, approver,
  rationale) is logged to `~/.postgres-aiops/audit.db` (relocatable via
  `POSTGRES_AIOPS_HOME`).
- **Budget / runaway guard** — token and call budgets trip a circuit breaker.
- **Risk tiers** — graduated autonomy; high-risk ops can require a named approver
  (`POSTGRES_AUDIT_APPROVED_BY` / `POSTGRES_AUDIT_RATIONALE` — the env-var names
  the bundled harness reads).
- **Undo recording** — reversible writes record an inverse descriptor built from
  the fetched before-state.

## Scope

This is the **PostgreSQL DBA-ops** member of the AIops-tools family (governed
AI-ops with audit + budget + undo + risk tiers). Do **NOT** use it for OT /
industrial edge (Modbus, OPC-UA, PROFINET) — see the separate `industrial-aiops`
line — nor for application-schema migrations or ORM management.

## Missing a capability?

Coverage is intentionally a curated subset of PostgreSQL's catalogs and
maintenance surface. Missing a view, a metric, or a maintenance command? **Open
an issue or PR** — contributions welcome.

## Status

**Preview — mock-validated only, not run against a live cluster.** The catalog
queries are modelled from the documented `pg_catalog` / `pg_stat_*` shapes and
need live verification. `postgres-aiops doctor` is the fastest live check.
