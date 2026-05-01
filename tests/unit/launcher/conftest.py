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

Issue #185 expanded this to clear ``_shutdown_event`` as well: tests
in ``test_tray_quit_event.py`` set the shutdown event in their
``finally`` blocks but never clear it, so a follow-up test that
spawns a fresh IPC poll thread would see the loop's
``while not _shutdown_event.is_set()`` guard exit on the first
iteration. On macOS CI this manifested as a flaky 2-second wait that
never observed the quit marker; clearing the event before each test
removes the cross-test leak entirely.

The autouse fixture below clears both events before AND after each
test, so cross-test contamination is impossible regardless of whether
the test under inspection sets either directly or via a code path
that sets one as a side effect.
"""

from __future__ import annotations

import pytest

from launcher import supervision


@pytest.fixture(autouse=True)
def _reset_supervision_state():
    """Clear ``_quit_requested`` and ``_shutdown_event`` around each test.

    The before-clear protects this test from leaks left by prior
    tests (in case some other path forgets to clean up). The
    after-clear protects subsequent tests from this test.
    """
    supervision._reset_quit_requested_for_tests()
    supervision.get_shutdown_event().clear()
    yield
    supervision._reset_quit_requested_for_tests()
    supervision.get_shutdown_event().clear()
