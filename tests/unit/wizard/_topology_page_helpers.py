# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Shared module-scoped fixtures for the topology-page content tests.

Phase F4 split ``test_topology_page.py`` into a markup/structure half
and an API/behaviour half. Both halves load the same three asset
files; defining the loading fixtures here keeps the file-read and
SPDX guards in one place.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
TOPOLOGY_PATH = (
    PROJECT_ROOT / "wizard" / "frontend" / "static" / "topology.html"
)
TOPOLOGY_JS_PATH = (
    PROJECT_ROOT / "wizard" / "frontend" / "static" / "js" / "topology.js"
)


@pytest.fixture(scope="module")
def topology_html() -> str:
    assert TOPOLOGY_PATH.is_file(), f"Expected {TOPOLOGY_PATH} to exist"
    return TOPOLOGY_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def topology_js() -> str:
    assert TOPOLOGY_JS_PATH.is_file(), f"Expected {TOPOLOGY_JS_PATH} to exist"
    return TOPOLOGY_JS_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def topology_combined(
    topology_html: str, topology_js: str, common_js: str,
) -> str:
    """Concatenated HTML + JS deps for cross-file behaviour assertions.

    Depends on the shared ``common_js`` fixture from conftest.py.
    """
    return "\n".join((topology_html, topology_js, common_js))
