# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Fixtures for the card inventory tests.

The once-only log record (``os_probe.log_once``) and the startup log's
"already logged" flag live for the whole process, so every test starts and
ends with both reset. The worker-wide ``_quiet_card_startup_log`` fixture
marks the startup log done for all other tests; this one re-arms it here.
"""
import pytest

from sethlans_worker_agent.cards import os_probe, startup_log


@pytest.fixture(autouse=True)
def _reset_card_log_state(_quiet_card_startup_log):
    os_probe.reset_logged_for_tests()
    startup_log.reset_for_tests()
    yield
    os_probe.reset_logged_for_tests()
    startup_log.reset_for_tests()
