# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Top-level test fixtures shared across all test stages.
"""

import pytest


@pytest.fixture(autouse=True)
def _bypass_setup_gate():
    """Mark setup as complete so the gate middleware passes through.

    The ``SetupGateMiddleware`` blocks all non-setup routes when the
    ``.setup_complete`` sentinel is absent.  During tests, there is no
    sentinel file and pytest-django forces ``DEBUG=False``, so the
    middleware would block every API request.

    This fixture sets the module-level ``_setup_complete`` boolean to
    ``True`` before each test and restores it after.
    """
    try:
        from sethlans_manager.middleware import setup_gate
        prev = setup_gate._setup_complete
        setup_gate._setup_complete = True
        yield
        setup_gate._setup_complete = prev
    except ImportError:
        # Middleware module not available (e.g., worker-only tests).
        yield
