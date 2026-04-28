# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared launcher unit-test fixtures.

Issue #163 introduces a process-wide :class:`threading.Event` in
:mod:`launcher.supervision` (``_quit_requested``). It is consulted by
every wizard-mode wait loop and by the cold-boot health probe in
normal mode. Without test isolation, a test that sets the event would
poison every subsequent test in the same process — they would observe
``quit_requested`` instantly and skip their happy paths.

The autouse fixture below clears the event before AND after each
test, so cross-test contamination is impossible regardless of whether
the test under inspection sets it directly or via a code path that
sets it as a side effect.
"""

from __future__ import annotations

import pytest

from launcher import supervision


@pytest.fixture(autouse=True)
def _reset_quit_requested_event():
    """Clear the tray-quit event before AND after every launcher test.

    The before-clear protects this test from leaks left by prior
    tests (in case some other path forgets to clean up). The
    after-clear protects subsequent tests from this test.
    """
    supervision._reset_quit_requested_for_tests()
    yield
    supervision._reset_quit_requested_for_tests()
