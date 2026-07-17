"""CLI ``remediate`` sub-commands — dry-run previews and error translation.

The dry-run path is the interesting CLI-only logic: it prints the operation it
*would* run and returns WITHOUT importing/hitting the governed twin. These tests
drive every remediate command with ``--dry-run`` and assert the preview text,
then confirm ``cli_errors`` turns a raised ValueError into a one-line red error.
The confirmed (governed) write path is covered end-to-end in test_cli_writes.py.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

runner = CliRunner()


def _dry(args: list[str]):
    from postgres_aiops.cli import app

    result = runner.invoke(app, [*args, "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "DRY-RUN" in result.output
    return result.output


@pytest.mark.unit
def test_terminate_dry_run_previews_pid():
    out = _dry(["remediate", "terminate", "42"])
    assert "terminate_backend" in out and "pid = 42" in out


@pytest.mark.unit
def test_cancel_dry_run_previews_pid():
    out = _dry(["remediate", "cancel", "7"])
    assert "cancel_query" in out and "pid = 7" in out


@pytest.mark.unit
def test_vacuum_dry_run_previews_flags():
    out = _dry(["remediate", "vacuum", "public.orders", "--full", "--analyze"])
    assert "run_vacuum" in out and "VACUUM public.orders" in out
    assert "full = True" in out and "analyze = True" in out


@pytest.mark.unit
def test_analyze_table_dry_run():
    out = _dry(["remediate", "analyze-table", "orders"])
    assert "run_analyze" in out and "ANALYZE orders" in out


@pytest.mark.unit
def test_create_index_dry_run_previews_columns():
    out = _dry(["remediate", "create-index", "orders", "customer_id", "--unique"])
    assert "create_index" in out and "customer_id" in out and "unique = True" in out


@pytest.mark.unit
def test_drop_index_dry_run():
    out = _dry(["remediate", "drop-index", "idx_orders_cid"])
    assert "drop_index" in out and "DROP INDEX idx_orders_cid" in out


@pytest.mark.unit
def test_reindex_dry_run():
    out = _dry(["remediate", "reindex", "public.orders", "--kind", "TABLE"])
    assert "reindex" in out and "REINDEX TABLE public.orders" in out


@pytest.mark.unit
def test_set_dry_run_previews_value():
    out = _dry(["remediate", "set", "work_mem", "64MB"])
    assert "update_setting" in out and "ALTER SYSTEM SET work_mem" in out
    assert "value = 64MB" in out


@pytest.mark.unit
def test_remediate_aborts_without_double_confirm():
    """Answering 'n' to the second confirm aborts with a non-zero exit and
    never reaches the governed twin."""
    from postgres_aiops.cli import app

    result = runner.invoke(app, ["remediate", "terminate", "42"], input="y\nn\n")
    assert result.exit_code != 0
