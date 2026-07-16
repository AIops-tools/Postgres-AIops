"""drop_index → create_index(definition=…) undo REPLAY — the descriptor recorded
by _drop_index_undo must be executable by the tool it names (found broken in the
line-wide undo-replayability sweep: create_index had no ``definition`` param)."""

from __future__ import annotations

import pytest

from mcp_server.tools import remediation as gov
from postgres_aiops.ops import remediation as ops

INDEXDEF = 'CREATE UNIQUE INDEX idx_orders_ts ON public.orders USING btree (ts)'


@pytest.mark.unit
def test_drop_index_descriptor_replays_through_create_index(fake_pg, monkeypatch):
    fake = fake_pg(scalars={"pg_get_indexdef": INDEXDEF})
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: fake)

    dropped = gov.drop_index(name="idx_orders_ts")
    assert dropped["priorState"]["indexdef"] == INDEXDEF

    descriptor = gov._drop_index_undo({"name": "idx_orders_ts"}, dropped)
    assert descriptor["tool"] == "create_index"

    replay = gov.create_index(**descriptor["params"])
    assert replay["index"] == "idx_orders_ts"
    assert replay["fromDefinition"] is True
    assert (INDEXDEF, None) in fake.executed


@pytest.mark.unit
def test_create_index_definition_and_columns_are_mutually_exclusive(fake_pg, monkeypatch):
    fake = fake_pg()
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: fake)
    result = gov.create_index(table="t", columns=["a"], definition=INDEXDEF)
    assert "not both" in result["error"]
    result = gov.create_index()
    assert "requires table+columns" in result["error"]
    assert fake.executed == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad",
    [
        "DROP TABLE users",
        "CREATE INDEX a ON t (c); DROP TABLE users",
        "SELECT 1",
        "",
    ],
)
def test_create_index_from_definition_rejects_non_indexdef_shapes(fake_pg, bad):
    fake = fake_pg()
    with pytest.raises(ValueError):
        ops.create_index_from_definition(fake, bad)
    assert fake.executed == []


@pytest.mark.unit
def test_create_index_definition_dry_run_executes_nothing(fake_pg, monkeypatch):
    fake = fake_pg()
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: fake)
    result = gov.create_index(definition=INDEXDEF, dry_run=True)
    assert result["dryRun"] is True and result["wouldExecute"] == INDEXDEF
    assert fake.executed == []
