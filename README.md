<!-- mcp-name: io.github.AIops-tools/postgres-aiops -->

# Postgres AIops

> **Disclaimer**: Community-maintained open-source project. **Not affiliated with, endorsed by, or sponsored by the PostgreSQL Global Development Group or any vendor.** "PostgreSQL" and the elephant logo are trademarks of the PostgreSQL Community Association; all product/trademark names belong to their respective owners. MIT licensed.

Governed AI-ops for **PostgreSQL DBA operations** — connecting to a server with
**psycopg 3** and reading the system catalogs and `pg_stat_*` views — with a
**built-in governance harness**: unified audit log, policy engine, token/runaway
budget guard, undo-token recording, and graduated-autonomy risk tiers.
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

## Security: read-only mode

This tool is meant to be handed to an AI agent, so its safety story is enforced
by the server rather than requested in a prompt:

```bash
export POSTGRES_READ_ONLY=1
```

With that set, the **10 write tools are never registered**. An MCP client
lists **25 tools instead of 35** — the writes are not hidden, not
gated behind a flag, and not merely refused when called. They are absent from
the session. A model cannot invoke a tool it was never offered, and cannot be
argued into one.

That distinction is the whole point. A tool that exists but refuses still invites
retry loops and "I'll describe the call instead" behaviour from smaller models,
and it leaves a reviewer trusting a promise. An absent tool is a fact you can
check: connect, list the tools, and see that the writes are not there.

Enforcement is two layers deep, so the switch cannot be sidestepped by changing
entry point:

| Layer | What it does | Covers |
|---|---|---|
| `@governed_tool` harness | refuses every non-read operation outright | MCP, CLI, and in-process callers |
| MCP registration | write tools are removed from `list_tools()` | anything speaking MCP |

Read operations are unaffected, and every call is still audited to
`~/.postgres-aiops/audit.db`.

> The read/write split is derived from each tool's declared `risk_level`, and a
> test asserts that this never disagrees with the `[READ]`/`[WRITE]` tag in the
> tool's own documentation — so a write can't quietly present itself as a read.

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

The mock test suite is complemented by a live run: the catalog / `pg_stat_*`
reads, the `bloat_and_vacuum_analysis` RCA, and the `create_index` / `drop_index`
governed write path (audit + undo, with `drop_index` capturing
`pg_get_indexdef` first) were exercised against a live PostgreSQL 16.14 instance running in
Docker. [`docs/VERIFICATION.md`](docs/VERIFICATION.md) records exactly what was
and was not covered. `postgres-aiops doctor` is the fastest live check.
