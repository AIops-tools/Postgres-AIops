# Security Policy

## Disclaimer

Community-maintained open-source project. **Not affiliated with, endorsed by, or
sponsored by the PostgreSQL Global Development Group or any vendor.**
"PostgreSQL" and related trademarks belong to their respective owners. Source is
publicly auditable under the MIT license.

## Reporting Vulnerabilities

Report privately via a GitHub Security Advisory on
[github.com/AIops-tools/Postgres-AIops](https://github.com/AIops-tools/Postgres-AIops/security/advisories)
or email zhouwei008@gmail.com. Please do not open public issues for security
reports.

## Security Design

### Credential Management
- Per-target PostgreSQL role passwords live **encrypted** in
  `~/.postgres-aiops/secrets.enc` (Fernet/AES-128 + scrypt-derived key; chmod
  600), never in `config.yaml` and never in source. The master password is never
  stored — only a per-store random salt and the ciphertext are on disk.
- A legacy plaintext env var `PG_<TARGET_NAME_UPPER>_PASSWORD` is still honoured
  as a fallback with a deprecation warning (migrate with `postgres-aiops secret
  migrate`).
- The password is passed to `psycopg.connect` at connect time and held only in
  memory. It is never logged or echoed; `config.yaml` holds only host, port,
  dbname, user, and `sslmode`. The redacted DSN (`dsn_redacted`) masks it.

### SQL-Injection Defenses
- **All values are bound query parameters** (pids, thresholds, limits, setting
  values) — never string-formatted into SQL.
- The few identifiers that cannot be parameterised (table/index/schema names, GUC
  names, `ORDER BY` columns, index methods, `REINDEX` kinds) are validated
  against strict allow-lists (`postgres_aiops.ops._util`) and double-quoted
  before the single interpolation site; anything that is not a plain identifier
  is rejected, not interpolated.
- `EXPLAIN` rejects multi-statement input (an embedded `;` is refused).

### Governed Operations
Every MCP tool runs through the bundled `@governed_tool` harness
(`postgres_aiops.governance`):
- **Audit** — every call logged to a local SQLite DB under `~/.postgres-aiops/`
  (relocatable via `POSTGRES_AIOPS_HOME`), agent-attributed, secret-redacted.
- **Token/runaway budget** — hard ceilings (`POSTGRES_MAX_TOOL_CALLS` /
  `POSTGRES_MAX_TOOL_SECONDS` — the env-var names the bundled harness reads) plus
  an on-by-default guard that trips a tight poll/retry loop.
- **Graduated risk tiers** — `~/.postgres-aiops/rules.yaml` `risk_tiers` gate
  writes by environment/tag; the highest tiers require a recorded approver
  (`POSTGRES_AUDIT_APPROVED_BY` / `POSTGRES_AUDIT_RATIONALE`).
- **Undo-token recording** — reversible writes fetch the **real before-state
  first** and record a faithful inverse (`create_index`↔`drop_index`, where drop
  captures `pg_get_indexdef`; `update_setting` restores the prior value).

### State-Changing Operations
Every write supports `--dry-run` (CLI) / `dry_run=True` (MCP) and requires double
confirmation at the CLI layer. Destructive/irreversible ops (`terminate_backend`,
`cancel_query`, `drop_index`) are `risk_level=high`; mutating maintenance ops
(`run_vacuum`, `run_analyze`, `create_index`, `reindex`, `update_setting`,
`reset_query_stats`) are `medium`. Irreversible ops capture prior stats for the
audit record but record no undo token. `ALTER SYSTEM SET` writes
`postgresql.auto.conf` and reports (but does not auto-run) the required
`pg_reload_conf()` / restart.

### SSL/TLS
`sslmode` follows libpq semantics (default `prefer`); set `require`/`verify-full`
for untrusted networks.

### Prompt-Injection Protection
All catalog- and query-returned text (query text, object names, descriptions) is
passed through a `sanitize()` truncate + control-character strip before reaching
the agent.

### Network Scope
No webhooks, no telemetry, no outbound calls beyond the configured PostgreSQL
connection. No post-install scripts or background services.

## Static Analysis

```bash
uvx bandit -r postgres_aiops/ mcp_server/
uv run ruff check .
```

## Supported Versions

The latest released version receives security fixes. This is a preview (0.x);
pin a version in production.
