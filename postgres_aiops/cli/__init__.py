"""CLI package for postgres-aiops.

Re-exports ``app`` so the pyproject entry point
``postgres-aiops = "postgres_aiops.cli:app"`` works unchanged.
"""

from postgres_aiops.cli._root import app

__all__ = ["app"]
