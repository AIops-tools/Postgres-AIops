"""Home-directory resolution for the governance harness.

State lives under ``ops_home()`` — by default ``~/.postgres-aiops``, overridable
via the ``POSTGRES_AIOPS_HOME`` environment variable so an operator can relocate
the audit / policy / budget / undo store.
"""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_HOME = "~/.postgres-aiops"


def ops_home() -> Path:
    """Return the harness state directory, honoring ``POSTGRES_AIOPS_HOME``."""
    return Path(os.environ.get("POSTGRES_AIOPS_HOME") or _DEFAULT_HOME).expanduser()


def ops_path(*parts: str) -> Path:
    """Resolve a file under the harness home, e.g. ``ops_path('audit.db')``."""
    return ops_home().joinpath(*parts)
