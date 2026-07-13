# Changelog

## v0.2.0 — 2026-07-13

Security-hardening release from a line-wide code review.

### Changed (behavior)
- **Secure by default**: with no `rules.yaml`, high/critical operations now require a
  named approver (`POSTGRES_AUDIT_APPROVED_BY`). A fresh install no longer allows
  destructive writes unattended; `init` seeds a starter `rules.yaml` you can edit,
  and an operator-authored rules file is honoured as-is.
- `__version__` is now single-sourced from package metadata (the previous release
  self-reported a stale version string).
- Sanitize docs no longer overstate scope: it strips control/format characters and
  truncates; semantic prompt-injection resistance must come from the consuming agent.

### Fixed
- CLI `query reset` now executes through the governed MCP twin — the last CLI write that bypassed audit/undo recording.

### Tests
- Governance persistence is now tested against REAL `audit.db`/`undo.db` files
  (write → audit row + inverse undo row with captured prior state).
- The CLI confirmed-write path (dry-run / double-confirm / governed execution) is
  covered end-to-end.
- `pytest-cov` added to the dev dependencies.

## v0.1.1

- Fix: `POSTGRES_AIOPS_HOME` now also relocates `config.yaml` (was hardcoded to `~/.postgres-aiops`).
- Fix: **CLI writes are now audited + undo-recorded** via the governance path — previously only the MCP tools recorded audit/undo; CLI `manage`/`remediate`/etc. writes now go through the same `@governed_tool` layer (they keep their dry-run + double-confirm). CLI write output is now the governed JSON result. No API/tool changes.


All notable changes to postgres-aiops are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); this project uses semantic
versioning (currently 0.x preview — the API may change).

## [0.1.0] — 2026-07-13

Initial preview release: governed AI-ops for **PostgreSQL DBA operations** —
connecting via **psycopg 3** and reading the system catalogs and `pg_stat_*`
views — with a bundled governance harness. **Mock-validated only — not run
against a live cluster.** Community-maintained; not affiliated with the
PostgreSQL project.

### Added

- **psycopg 3 connection layer** (`postgres_aiops.connection`) — parameterised
  reads with a `dict_row` factory, autocommit for maintenance statements
  (VACUUM / CONCURRENTLY / REINDEX), an injectable connection for tests, and
  teaching error translation (`PgError`, with connect/permission/missing-view
  hints).
- **33 governed MCP tools**, every one wrapped with `@governed_tool`:
  - **Overview** — `overview` (one-shot cluster health snapshot).
  - **Server** — `server_version`, `show_settings`, `list_extensions`,
    `list_databases`, `list_roles`.
  - **Activity** — `list_activity`, `long_running_queries`, `list_locks`.
  - **Queries** — `top_queries` (pg_stat_statements), `explain_query`.
  - **Indexes** — `unused_indexes`, `missing_index_hints`, `index_bloat`,
    `invalid_indexes`.
  - **Tables** — `table_sizes`, `table_bloat`, `autovacuum_status`.
  - **Replication** — `replication_status`, `replication_slots`, `wal_status`.
  - **Analysis (flagship)** — `slow_query_rca`, `bloat_and_vacuum_analysis`,
    `blocking_lock_chain_rca`.
  - **Writes** — `terminate_backend` (high), `cancel_query` (high),
    `drop_index` (high), `run_vacuum` (medium), `run_analyze` (medium),
    `create_index` (medium), `reindex` (medium), `update_setting` (medium),
    `reset_query_stats` (medium).
- **Guarded writes** — every write supports a `dry_run` preview and (at the CLI)
  double confirmation. Reversible writes fetch the **real before-state** and
  record a faithful inverse: `create_index`↔`drop_index` (drop captures
  `pg_get_indexdef` so undo recreates it exactly); `update_setting` captures the
  prior value. Irreversible ops record prior stats for audit but no undo.
- **SQL-injection defenses** — all values are bound query parameters; the few
  identifiers that cannot be parameterised (table/index/GUC names, ORDER BY
  columns, index methods) are validated against strict allow-lists and quoted
  before interpolation.
- **Bundled governance harness** (`postgres_aiops.governance`) — audit log, policy
  engine, token/runaway budget guard, undo-token recording, graduated risk tiers,
  prompt-injection `sanitize`. State under `~/.postgres-aiops/` (relocatable via
  `POSTGRES_AIOPS_HOME`).
- **Encrypted secret store** — role passwords in `~/.postgres-aiops/secrets.enc`
  (Fernet + scrypt); legacy `PG_<TARGET>_PASSWORD` env fallback + `secret migrate`.
- **CLI** — `init` wizard, `overview`, `server`, `activity`, `query`, `index`,
  `table`, `repl`, `analyze`, `remediate`, `secret`, `doctor`, `mcp`.

### Known limitations

- Preview / mock-only: catalog and `pg_stat_*` queries need live verification.
- `top_queries` / `slow_query_rca` require the `pg_stat_statements` extension.
- Coverage is a curated subset of PostgreSQL's surface; open an issue/PR for gaps.

[0.1.0]: https://github.com/AIops-tools/Postgres-AIops/releases/tag/v0.1.0
