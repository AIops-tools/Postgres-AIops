"""Config loading, target lookup and the password-resolution fallback chain.

Verifies the YAML loader builds ``TargetConfig`` rows (never a password from the
file), the ``get_target`` / ``default_target`` teaching errors, and that
``_resolve_secret`` prefers the encrypted store, falls back to the legacy env
var (with a warning), and finally fails with an actionable OSError.
"""

from __future__ import annotations

import pytest

import postgres_aiops.config as cfg
from postgres_aiops.config import AppConfig, TargetConfig, load_config
from postgres_aiops.secretstore import SecretStoreError

# ── target lookup ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_get_target_found():
    c = AppConfig(targets=(TargetConfig(name="primary", host="a"),))
    assert c.get_target("primary").host == "a"


@pytest.mark.unit
def test_get_target_missing_lists_available():
    c = AppConfig(targets=(TargetConfig(name="primary", host="a"),))
    with pytest.raises(KeyError, match="primary"):
        c.get_target("nope")


@pytest.mark.unit
def test_default_target_is_first():
    c = AppConfig(targets=(TargetConfig(name="p", host="a"), TargetConfig(name="r", host="b")))
    assert c.default_target.name == "p"


@pytest.mark.unit
def test_default_target_empty_raises():
    with pytest.raises(ValueError, match="No targets"):
        _ = AppConfig().default_target


# ── load_config ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_load_config_missing_file_teaches_init(tmp_path):
    with pytest.raises(FileNotFoundError, match="init"):
        load_config(tmp_path / "absent.yaml")


@pytest.mark.unit
def test_load_config_parses_targets_with_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "targets:\n"
        "  - name: primary\n"
        "    host: db.local\n"
        "  - name: replica\n"
        "    host: db2.local\n"
        "    port: 6543\n"
        "    dbname: app\n"
        "    user: monitor\n"
        "    sslmode: require\n"
    )
    conf = load_config(path)
    assert [t.name for t in conf.targets] == ["primary", "replica"]
    # defaults applied to the first, overrides honoured on the second
    assert conf.targets[0].port == 5432 and conf.targets[0].dbname == "postgres"
    assert conf.targets[1].port == 6543 and conf.targets[1].sslmode == "require"


@pytest.mark.unit
def test_load_config_empty_yaml_yields_no_targets(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("")
    assert load_config(path).targets == ()


# ── password resolution fallback chain ───────────────────────────────────────


@pytest.mark.unit
def test_resolve_secret_prefers_encrypted_store(monkeypatch):
    monkeypatch.setattr(cfg, "has_store", lambda: True)
    monkeypatch.setattr(cfg, "get_secret", lambda name: "from-store")
    assert TargetConfig(name="primary", host="a").password == "from-store"


@pytest.mark.unit
def test_resolve_secret_falls_back_to_legacy_env(monkeypatch):
    # store present but has no entry -> fall through to the legacy env var
    monkeypatch.setattr(cfg, "has_store", lambda: True)

    def _boom(name):
        raise SecretStoreError("no such secret")

    monkeypatch.setattr(cfg, "get_secret", _boom)
    monkeypatch.setenv("PG_PRIMARY_PASSWORD", "legacy-pw")
    assert TargetConfig(name="primary", host="a").password == "legacy-pw"


@pytest.mark.unit
def test_resolve_secret_missing_everywhere_raises_oserror(monkeypatch):
    monkeypatch.setattr(cfg, "has_store", lambda: False)
    monkeypatch.delenv("PG_PRIMARY_PASSWORD", raising=False)
    with pytest.raises(OSError, match="secret set primary"):
        _ = TargetConfig(name="primary", host="a").password


@pytest.mark.unit
def test_secret_env_key_uppercases_and_replaces_dashes():
    assert cfg._secret_env_key("prod-db") == "PG_PROD_DB_PASSWORD"
