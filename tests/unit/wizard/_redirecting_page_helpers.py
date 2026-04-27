# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Shared module-scoped fixtures for the redirecting-page content tests.

Phase F4 split ``test_redirecting_page.py`` into a markup/structure
half and a JS-behaviour half. Both halves load the same three asset
files; defining the loading fixtures here keeps the file-read and
SPDX guards in one place.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REDIRECTING_PATH = (
    PROJECT_ROOT / "wizard" / "frontend" / "static" / "redirecting.html"
)
REDIRECTING_JS_PATH = (
    PROJECT_ROOT / "wizard" / "frontend" / "static" / "js" / "redirecting.js"
)


@pytest.fixture(scope="module")
def redirecting_html() -> str:
    assert REDIRECTING_PATH.is_file(), f"Expected {REDIRECTING_PATH} to exist"
    return REDIRECTING_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def redirecting_js() -> str:
    assert REDIRECTING_JS_PATH.is_file(), (
        f"Expected {REDIRECTING_JS_PATH} to exist"
    )
    return REDIRECTING_JS_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def redirecting_combined(
    redirecting_html: str, redirecting_js: str, common_js: str,
) -> str:
    """Concatenated HTML + JS deps for cross-file behaviour assertions.

    Depends on the shared ``common_js`` fixture from conftest.py.
    """
    return "\n".join((redirecting_html, redirecting_js, common_js))
