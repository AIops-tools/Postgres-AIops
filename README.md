<!-- mcp-name: io.github.AIops-tools/postgres-aiops -->

# Postgres AIops

> **Disclaimer**: Community-maintained open-source project. **Not affiliated with, endorsed by, or sponsored by the PostgreSQL Global Development Group or any vendor.** "PostgreSQL" and the elephant logo are trademarks of the PostgreSQL Community Association; all product/trademark names belong to their respective owners. MIT licensed.

Governed AI-ops for **PostgreSQL DBA operations** — connecting to a server with
**psycopg 3** and reading the system catalogs and `pg_stat_*` views — with a
**built-in governance harness**: unified audit log, token/runaway
budget guard, undo-token recording, and descriptive risk-tier labels.
Beyond the mock test suite, the reads, a governed write, and its undo have been
exercised against a live PostgreSQL 16.14 instance — see [`docs/VERIFICATION.md`](docs/VERIFICATION.md).

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
- **MCP server** (`postgres-aiops mcp` or `postgres-aiops-mcp`): **35 tools** (25 read, 10 write), every one wrapped with the bundled `@governed_tool` harness.
- **Encrypted credentials**: the role password lives in an encrypted store `~/.postgres-aiops/secrets.enc` (Fernet + scrypt) — **never plaintext on disk**. Unlock with a master password from `POSTGRES_AIOPS_MASTER_PASSWORD` (MCP/CI) or an interactive prompt (CLI).
- **Reversibility**: mutating writes fetch the **real before-state first** and record a faithful inverse — `create_index`↔`drop_index`; `drop_index` captures `pg_get_indexdef` so undo recreates it exactly; `update_setting` captures the prior value so undo sets it back. Irreversible ops (`terminate_backend`, `cancel_query`, `run_vacuum`, `run_analyze`, `reindex`, `reset_query_stats`) record prior stats for audit but declare no undo.
- **Safety**: every state-changing CLI op supports `--dry-run` and requires double confirmation; every write MCP tool takes a `dry_run` preview. All identifiers that cannot be parameterised (table/index/GUC names) are validated and quoted; all values are bound query parameters.

## Capability matrix (35 MCP tools)

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
| **Undo** | `undo_list` | 1 | read |
| | `undo_apply` | 1 | write (medium) |

The flagship analyses accept injected records for pure/offline analysis, or pull
live from a configured target. `top_queries`/`slow_query_rca` require the
`pg_stat_statements` extension; the read role should have `pg_monitor`.

## What this tool does, and does not, decide

It delivers PostgreSQL DBA operations — reads and writes — accurately and
efficiently, and records every one of them. It does **not** decide whether a
write is allowed to happen. That is the agent's judgement, or the permission of
the account you connect it with: connect with a PostgreSQL role that has no
write privileges (a read-only role, or one without INSERT/UPDATE/DELETE/DDL),
and the writes fail at the server — the place that actually owns the
permission.

So there is no read-only switch, no policy file, no approval gate to configure.
The one thing the tool guarantees is that nothing is silent: **every call, over
MCP and over the CLI alike, lands an audit row** in `~/.postgres-aiops/audit.db`,
and destructive writes still capture their before-state and record an inverse
where one exists.

> Each tool declares a `risk_level`, carried into the audit row as a descriptive
> tier (none/confirm/review) — so a reviewer can see at a glance that a row was
> a high-risk delete. It is a label, not a gate.

Running a smaller / local model? See
[agent-guardrails.md](skills/postgres-aiops/references/agent-guardrails.md) — it lists
the guardrails this tool now enforces for you (so you don't spend prompt budget
restating them) and gives a ready-made system prompt for what's left.

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

- **Audit** — every call (params, result, status, duration, risk tier, and any
  operator-supplied approver/rationale) is logged to `~/.postgres-aiops/audit.db`
  (relocatable via `POSTGRES_AIOPS_HOME`). The CLI writes the same row the MCP
  path does — there is no unaudited entry point.
- **Runaway guard** — a safety backstop, not an authorization gate: the same call
  hammered in a tight loop trips a circuit breaker. Disable with
  `POSTGRES_RUNAWAY_MAX=0`; optional hard ceilings via `POSTGRES_MAX_TOOL_CALLS` /
  `POSTGRES_MAX_TOOL_SECONDS`.
- **Undo recording** — reversible writes record an inverse descriptor built from
  the fetched before-state.
- **Risk tier** — a descriptive label on the audit row derived from `risk_level`;
  it gates nothing.

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

The mock test suite is complemented by a live run: the catalog / `pg_stat_*`
reads, the `bloat_and_vacuum_analysis` RCA, and the `create_index` / `drop_index`
governed write path (audit + undo, with `drop_index` capturing
`pg_get_indexdef` first) were exercised against a live PostgreSQL 16.14 instance running in
Docker. [`docs/VERIFICATION.md`](docs/VERIFICATION.md) records exactly what was
and was not covered. `postgres-aiops doctor` is the fastest live check.
