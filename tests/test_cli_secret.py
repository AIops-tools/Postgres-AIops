"""``postgres-aiops secret`` CLI — set / list / rm / migrate / rotate.

Redirects the encrypted store at a tmp dir and supplies the master password via
the env var (so ``resolve_master_password`` never prompts). Asserts the visible
CLI behaviour: a set value round-trips through list, rm removes it, migrate
imports legacy env secrets, and a rotate with mismatched passwords aborts.
Values are never echoed.
"""

from __future__ import annotations

import getpass

import pytest
from typer.testing import CliRunner

import postgres_aiops.secretstore as ss
from postgres_aiops.cli import app

runner = CliRunner()


@pytest.fixture
def secret_env(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(ss, "SECRETS_FILE", tmp_path / "secrets.enc")
    monkeypatch.setattr(ss, "LEGACY_ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(ss, "_cached", None)
    monkeypatch.setenv(ss.MASTER_PASSWORD_ENV, "master-pw")
    return tmp_path


@pytest.mark.unit
def test_secret_set_then_list_shows_name_not_value(secret_env):
    r = runner.invoke(app, ["secret", "set", "primary", "--value", "hunter2"])
    assert r.exit_code == 0, r.output
    assert "primary" in r.output and "hunter2" not in r.output

    listed = runner.invoke(app, ["secret", "list"])
    assert listed.exit_code == 0
    assert "primary" in listed.output and "hunter2" not in listed.output


@pytest.mark.unit
def test_secret_set_prompts_when_value_omitted(secret_env, monkeypatch):
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "from-prompt")
    r = runner.invoke(app, ["secret", "set", "primary"])
    assert r.exit_code == 0, r.output
    assert ss.SecretStore.unlock("master-pw").get("primary") == "from-prompt"


@pytest.mark.unit
def test_secret_list_empty_hints_how_to_add(secret_env):
    r = runner.invoke(app, ["secret", "list"])
    assert r.exit_code == 0
    assert "No secrets stored" in r.output


@pytest.mark.unit
def test_secret_rm_deletes(secret_env):
    runner.invoke(app, ["secret", "set", "primary", "--value", "v"])
    r = runner.invoke(app, ["secret", "rm", "primary"])
    assert r.exit_code == 0 and "Deleted" in r.output
    assert ss.SecretStore.unlock("master-pw").names() == ()


@pytest.mark.unit
def test_secret_migrate_imports_legacy_env(secret_env):
    (secret_env / ".env").write_text("PG_PRIMARY_PASSWORD=legacy-secret\n")
    r = runner.invoke(app, ["secret", "migrate"])
    assert r.exit_code == 0, r.output
    assert "Imported 1" in r.output and "primary" in r.output
    assert ss.SecretStore.unlock("master-pw").get("primary") == "legacy-secret"


@pytest.mark.unit
def test_secret_migrate_nothing_to_do(secret_env):
    r = runner.invoke(app, ["secret", "migrate"])
    assert r.exit_code == 0 and "Nothing to migrate" in r.output


@pytest.mark.unit
def test_secret_rotate_password_mismatch_aborts(secret_env, monkeypatch):
    runner.invoke(app, ["secret", "set", "primary", "--value", "v"])
    answers = iter(["new-pw", "typo-pw"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))
    r = runner.invoke(app, ["secret", "rotate-password"])
    assert r.exit_code == 1 and "did not match" in r.output


@pytest.mark.unit
def test_secret_rotate_password_succeeds(secret_env, monkeypatch):
    runner.invoke(app, ["secret", "set", "primary", "--value", "v"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "brand-new-pw")
    r = runner.invoke(app, ["secret", "rotate-password"])
    assert r.exit_code == 0, r.output
    assert "rotated" in r.output
    assert ss.SecretStore.unlock("brand-new-pw").get("primary") == "v"
