"""Shared test doubles for the ops layer (no live database).

``FakePg`` mimics :class:`postgres_aiops.connection.PgConnection`'s surface
(``query``/``query_one``/``scalar``/``execute``). Responses are matched by
substring of the SQL, so a single fake can serve the several queries a flagship
analysis issues, and every executed write is recorded for assertions.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _default_approver(monkeypatch):
    """Record a synthetic approver annotation globally.

    The harness authorizes nothing, so this gates nothing; it only ensures the
    optional ``approved_by`` audit field is populated for tests that do not set
    their own. The governance-persistence tests clear it to show the annotation
    is genuinely optional."""
    monkeypatch.setenv("POSTGRES_AUDIT_APPROVED_BY", "pytest")


class FakePg:
    def __init__(
        self,
        responses: dict[str, list[dict]] | None = None,
        scalars: dict[str, Any] | None = None,
    ) -> None:
        self.responses = responses or {}
        self.scalars = scalars or {}
        self.executed: list[tuple[str, Any]] = []
        self.queried: list[tuple[str, Any]] = []

    @staticmethod
    def _match(table: dict, sql: str) -> Any:
        for key, value in table.items():
            if key in sql:
                return value
        return None

    def query(self, sql: str, params: Any | None = None) -> list[dict]:
        self.queried.append((sql, params))
        rows = self._match(self.responses, sql)
        return list(rows) if rows is not None else []

    def query_one(self, sql: str, params: Any | None = None) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Any | None = None) -> Any:
        self.queried.append((sql, params))
        return self._match(self.scalars, sql)

    def execute(self, sql: str, params: Any | None = None) -> str:
        self.executed.append((sql, params))
        return "OK"


@pytest.fixture
def fake_pg():
    return FakePg
