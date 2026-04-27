# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Shared pytest fixtures for the wizard unit tests.

Phase F4 split several oversized wizard test files into focused
halves; the shared helpers / fixtures live in private ``_*_helpers``
modules and are re-exported here so pytest discovers them via the
standard conftest mechanism (no fragile module-namespace imports
for fixture machinery).

Each fixture is scoped narrowly:

* ``common_js``, ``topology_html`` / ``topology_js``,
  ``redirecting_html`` / ``redirecting_js`` and the ``*_combined``
  helpers are module-scoped — pytest reads each file once per test
  module that asks for it.
* ``_reset_auth_state`` (autouse) and ``_ensure_wizard_on_path``
  (autouse) reset isolated, in-memory state and matter to any test
  that touches ``auth_state`` or imports from the wizard script
  directory; deliberately broadcast to all wizard tests.
* ``provisioned_data_dir`` is an opt-in fixture (function-scoped),
  not autouse.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Re-export shared fixtures by importing them. pytest discovers any
# ``@pytest.fixture``-decorated callable in this module's namespace.
from ._redirecting_page_helpers import (  # noqa: F401
    redirecting_combined,
    redirecting_html,
    redirecting_js,
)
from ._run_wizard_helpers import (  # noqa: F401
    _ensure_wizard_on_path,
    provisioned_data_dir,
)
from ._static_file_helpers import _reset_auth_state  # noqa: F401
from ._topology_page_helpers import (  # noqa: F401
    topology_combined,
    topology_html,
    topology_js,
)

_COMMON_JS_PATH = (
    Path(__file__).resolve().parents[3]
    / "wizard" / "frontend" / "static" / "js" / "common.js"
)


@pytest.fixture(scope="module")
def common_js() -> str:
    """Shared loader for ``static/js/common.js`` used by both the
    redirecting-page and topology-page test halves."""
    assert _COMMON_JS_PATH.is_file(), f"Expected {_COMMON_JS_PATH} to exist"
    return _COMMON_JS_PATH.read_text(encoding="utf-8")
