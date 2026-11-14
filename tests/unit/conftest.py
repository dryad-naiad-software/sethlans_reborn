# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit-stage fixtures.

Resets worker job-claim state added by the Unreal foundation (chunk 3)
that the older per-package conftests predate: ``_unfinished_job_ids``
outlives a test whose claim is never processed, and ``job_selection``
skips an id that is still listed there.
"""
import pytest

from tests._worker_claim_state import reset_worker_claim_state


@pytest.fixture(autouse=True)
def _reset_worker_claim_state():
    reset_worker_claim_state()
    yield
    reset_worker_claim_state()
