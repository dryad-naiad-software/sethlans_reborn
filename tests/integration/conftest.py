# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration-stage fixtures.

Resets worker job-claim state added by the Unreal foundation (chunk 3);
see ``tests/_worker_claim_state.py``.
"""
import pytest

from tests._worker_claim_state import reset_worker_claim_state


@pytest.fixture(autouse=True)
def _reset_worker_claim_state():
    reset_worker_claim_state()
    yield
    reset_worker_claim_state()
