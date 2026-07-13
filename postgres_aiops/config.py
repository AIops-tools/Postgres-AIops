"""Configuration management for Postgres AIops.

Loads connection targets from a YAML config file. The secret (the PostgreSQL
role **password**) is NEVER stored in the config file and never on disk in
plaintext: it lives in the encrypted store ``~/.postgres-aiops/secrets.enc``
(see :mod:`postgres_aiops.secretstore`). For backward compatibility a legacy
plaintext env var (``PG_<TARGET>_PASSWORD``) is still honoured as a fallback,
with a warning nudging migration to the encrypted store.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from postgres_aiops.governance.paths import ops_home
from postgres_aiops.secretstore import SecretStoreError, get_secret, has_store

CONFIG_DIR = ops_home()
CONFIG_FILE = CONFIG_DIR / "config.yaml"
ENV_FILE = CONFIG_DIR / ".env"

DEFAULT_PORT = 5432
DEFAULT_DBNAME = "postgres"
DEFAULT_USER = "postgres"
DEFAULT_SSLMODE = "prefer"
APPLICATION_NAME = "postgres-aiops"

# Legacy env-var prefix/suffix; also used by the migration helper.
SECRET_ENV_PREFIX = "PG_"  # nosec B105 — env-var name, not a secret
SECRET_ENV_SUFFIX = "_PASSWORD"  # nosec B105 — env-var name, not a secret

_log = logging.getLogger("postgres-aiops.config")


def _secret_env_key(name: str) -> str:
    """Legacy per-target password env var name, e.g. PG_PRIMARY_PASSWORD."""
    return f"{SECRET_ENV_PREFIX}{name.upper().replace('-', '_')}{SECRET_ENV_SUFFIX}"


def _resolve_secret(name: str) -> str:
    """Return a target's password: encrypted store first, then legacy env var."""
    if has_store():
        try:
            return get_secret(name)
        except SecretStoreError:
            pass  # fall through to legacy env var
    legacy = os.environ.get(_secret_env_key(name))
    if legacy:
        _log.warning(
            "Using plaintext env var %s. Migrate to the encrypted store with "
            "'postgres-aiops secret migrate'.",
            _secret_env_key(name),
        )
        return legacy
    raise OSError(
        f"No password for target '{name}'. Add one with "
        f"'postgres-aiops secret set {name}' (stored encrypted), or run "
        f"'postgres-aiops init'."
    )


@dataclass(frozen=True)
class TargetConfig:
    """A connection target for a PostgreSQL server/cluster.

    The password is sourced from the encrypted secret store (see ``password``),
    never the config file. ``host``/``port`` locate the server; ``dbname`` is the
    database to connect to; ``sslmode`` follows libpq semantics
    (disable/allow/prefer/require/verify-ca/verify-full).
    """

    name: str
    host: str
    port: int = DEFAULT_PORT
    dbname: str = DEFAULT_DBNAME
    user: str = DEFAULT_USER
    sslmode: str = DEFAULT_SSLMODE

    @property
    def password(self) -> str:
        return _resolve_secret(self.name)

    @property
    def conn_kwargs(self) -> dict:
        """libpq connection keyword args for ``psycopg.connect`` (incl. password)."""
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.dbname,
            "user": self.user,
            "password": self.password,
            "sslmode": self.sslmode,
            "application_name": APPLICATION_NAME,
        }

    @property
    def dsn_redacted(self) -> str:
        """A human-readable DSN with the password redacted (for logs/doctor)."""
        return (
            f"postgresql://{self.user}:***@{self.host}:{self.port}/"
            f"{self.dbname}?sslmode={self.sslmode}"
        )


@dataclass(frozen=True)
class AppConfig:
    """Top-level application config."""

    targets: tuple[TargetConfig, ...] = ()

    def get_target(self, name: str) -> TargetConfig:
        for t in self.targets:
            if t.name == name:
                return t
        available = ", ".join(t.name for t in self.targets) or "(none)"
        raise KeyError(f"Target '{name}' not found. Available: {available}")

    @property
    def default_target(self) -> TargetConfig:
        if not self.targets:
            raise ValueError("No targets configured. Check config.yaml")
        return self.targets[0]


def load_config(config_path: Path | None = None) -> AppConfig:
    """Load config from YAML; the password comes from the encrypted store."""
    path = config_path or CONFIG_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            f"Run 'postgres-aiops init' to set up a target and store its password "
            f"encrypted, or create {CONFIG_FILE} with a 'targets' list."
        )

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    targets = tuple(
        TargetConfig(
            name=t["name"],
            host=t["host"],
            port=t.get("port", DEFAULT_PORT),
            dbname=t.get("dbname", DEFAULT_DBNAME),
            user=t.get("user", DEFAULT_USER),
            sslmode=t.get("sslmode", DEFAULT_SSLMODE),
        )
        for t in raw.get("targets", [])
    )

    return AppConfig(targets=targets)
