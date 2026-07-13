# Postgres AIops v0.1.0 — preview

Governed AI-ops for **PostgreSQL DBA operations** for AI agents — connecting via
**psycopg 3** and reading the system catalogs and `pg_stat_*` views — with a
built-in governance harness (audit, policy, token/runaway budget, undo-token
recording, graduated risk tiers) and an encrypted credential store. Standalone —
no external skill-family dependency.

> **Preview / mock-only.** All behaviour is validated against a mocked psycopg
> cursor/connection; it has **not** been run against a live PostgreSQL cluster.
> The fastest live check is `postgres-aiops doctor`.
>
> Community-maintained; **not affiliated with or endorsed by the PostgreSQL
> Global Development Group.** "PostgreSQL" and related trademarks belong to their
> owners.

## Highlights

- **33 MCP tools** (24 read, 9 write), every one wrapped with `@governed_tool`.
  - Read: cluster `overview`; server (5); activity (3); query stats (2); index
    health (4); table health (3); replication (3); and three flagship analyses.
  - Write: `terminate_backend`/`cancel_query`/`drop_index` (high);
    `run_vacuum`/`run_analyze`/`create_index`/`reindex`/`update_setting`/
    `reset_query_stats` (medium).
- **Three signature analyses** — `slow_query_rca` (worst `pg_stat_statements`
  entry + EXPLAIN → cited cause/action), `bloat_and_vacuum_analysis` (dead-tuple
  ratio + autovacuum recency → recommendation), and `blocking_lock_chain_rca`
  (build the wait-for tree, name the root blocker).
- **Encrypted password store** (`~/.postgres-aiops/secrets.enc`, Fernet + scrypt)
  — never plaintext on disk; legacy `PG_<TARGET>_PASSWORD` env fallback.
- **CLI** with an `init` onboarding wizard, `secret` management, and `doctor`.
- **psycopg 3 connection layer** — parameterised catalog reads, `dict_row`
  results, autocommit for maintenance commands, and teaching error translation
  (`PgError`). Reversible writes fetch the real before-state first.

## Install

```bash
uv tool install postgres-aiops
postgres-aiops init
postgres-aiops doctor
```

## Caveats

- The catalog / `pg_stat_*` queries are modelled from the documented shapes and
  need live verification against a real cluster.
- `top_queries` / `slow_query_rca` require the `pg_stat_statements` extension;
  the read role should have `pg_monitor`.
- Out of scope by design: application-schema migrations, ORM management, logical
  backup/restore orchestration, and any bulk destructive DDL.
- Missing a view, metric, or maintenance command? Open an issue or PR.
