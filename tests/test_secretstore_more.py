"""Secret store — master-password resolution, guards, caching and permissions.

Extends test_secretstore.py with the non-roundtrip paths: how the master
password is resolved (env var / interactive prompt / non-TTY failure / confirm
mismatch), the store-format and empty-input guards, the process cache in
``open_store``, and the ``check_permissions`` warning.
"""

from __future__ import annotations

import json

import pytest

import postgres_aiops.secretstore as ss


@pytest.fixture
def store_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(ss, "SECRETS_FILE", tmp_path / "secrets.enc")
    monkeypatch.setattr(ss, "LEGACY_ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(ss, "_cached", None)
    monkeypatch.delenv(ss.MASTER_PASSWORD_ENV, raising=False)
    return tmp_path


# ── resolve_master_password ──────────────────────────────────────────────────


@pytest.mark.unit
def test_master_password_from_env(store_dir, monkeypatch):
    monkeypatch.setenv(ss.MASTER_PASSWORD_ENV, "env-pw")
    assert ss.resolve_master_password() == "env-pw"


@pytest.mark.unit
def test_master_password_non_tty_raises(store_dir, monkeypatch):
    monkeypatch.setattr(ss.sys, "stdin", type("S", (), {"isatty": staticmethod(lambda: False)}))
    with pytest.raises(ss.MasterPasswordError, match=ss.MASTER_PASSWORD_ENV):
        ss.resolve_master_password()


@pytest.mark.unit
def test_master_password_prompt_on_tty(store_dir, monkeypatch):
    monkeypatch.setattr(ss.sys, "stdin", type("S", (), {"isatty": staticmethod(lambda: True)}))
    monkeypatch.setattr(ss.getpass, "getpass", lambda prompt="": "typed-pw")
    assert ss.resolve_master_password() == "typed-pw"


@pytest.mark.unit
def test_master_password_empty_prompt_rejected(store_dir, monkeypatch):
    monkeypatch.setattr(ss.sys, "stdin", type("S", (), {"isatty": staticmethod(lambda: True)}))
    monkeypatch.setattr(ss.getpass, "getpass", lambda prompt="": "")
    with pytest.raises(ss.MasterPasswordError, match="Empty"):
        ss.resolve_master_password()


@pytest.mark.unit
def test_master_password_confirm_mismatch_on_new_store(store_dir, monkeypatch):
    monkeypatch.setattr(ss.sys, "stdin", type("S", (), {"isatty": staticmethod(lambda: True)}))
    answers = iter(["first-pw", "different-pw"])
    monkeypatch.setattr(ss.getpass, "getpass", lambda prompt="": next(answers))
    with pytest.raises(ss.MasterPasswordError, match="did not match"):
        ss.resolve_master_password(confirm_if_new=True)


# ── unlock guards ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_unlock_unsupported_version_rejected(store_dir):
    (store_dir / "secrets.enc").write_text(
        json.dumps({"version": 999, "salt": "AAAA", "ciphertext": "x"})
    )
    with pytest.raises(ss.SecretStoreError, match="version"):
        ss.SecretStore.unlock("pw")


@pytest.mark.unit
def test_unlock_corrupt_file_rejected(store_dir):
    (store_dir / "secrets.enc").write_text("{not valid json")
    with pytest.raises(ss.SecretStoreError, match="Could not read"):
        ss.SecretStore.unlock("pw")


# ── SecretStore write guards + membership ────────────────────────────────────


@pytest.mark.unit
def test_set_empty_name_rejected(store_dir):
    with pytest.raises(ss.SecretStoreError, match="name"):
        ss.SecretStore.unlock("pw").set("", "v")


@pytest.mark.unit
def test_delete_missing_rejected(store_dir):
    with pytest.raises(ss.SecretStoreError, match="No secret named"):
        ss.SecretStore.unlock("pw").delete("ghost")


@pytest.mark.unit
def test_with_password_empty_rejected(store_dir):
    with pytest.raises(ss.SecretStoreError, match="must not be empty"):
        ss.SecretStore.unlock("pw").with_password("")


@pytest.mark.unit
def test_contains_membership(store_dir):
    store = ss.SecretStore.unlock("pw").set("a", "1")
    assert "a" in store and "b" not in store


# ── module-level convenience API ─────────────────────────────────────────────


@pytest.mark.unit
def test_open_store_caches_within_process(store_dir, monkeypatch):
    monkeypatch.setenv(ss.MASTER_PASSWORD_ENV, "cache-pw")
    first = ss.open_store()
    second = ss.open_store()
    assert first is second  # cached, decrypted once


@pytest.mark.unit
def test_check_permissions_none_when_absent(store_dir):
    assert ss.check_permissions() is None


@pytest.mark.unit
def test_check_permissions_warns_on_group_readable(store_dir):
    ss.SecretStore.unlock("pw").set("a", "1")
    (store_dir / "secrets.enc").chmod(0o640)
    warning = ss.check_permissions()
    assert warning and "chmod 600" in warning


@pytest.mark.unit
def test_migrate_no_legacy_file_returns_empty(store_dir):
    assert ss.migrate_legacy_env("PG_", "_PASSWORD", "pw") == []
