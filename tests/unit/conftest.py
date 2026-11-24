# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit-stage fixtures.

Resets worker job-claim state added by the Unreal foundation (chunk 3)
that the older per-package conftests predate: ``_unfinished_job_ids``
outlives a test whose claim is never processed, and ``job_selection``
skips an id that is still listed there.

Also forgets the worker's TLS state (``tls_adapter``, #306): the test
thread's session and the process-wide pin, so no test inherits a pin
another test read. Only when the module was imported, so manager tests
import nothing from the worker.
"""
import sys

import pytest

from tests._worker_claim_state import reset_worker_claim_state


@pytest.fixture(autouse=True)
def _reset_worker_claim_state():
    reset_worker_claim_state()
    yield
    reset_worker_claim_state()


def _forget_tls_state():
    """Close and drop this thread's session and forget the process pin, without the reset log line."""
    tls_adapter = sys.modules.get('sethlans_worker_agent.tls_adapter')
    if tls_adapter is None:
        return
    session = getattr(tls_adapter._thread_local, 'session', None)
    if session is not None:
        session.close()
        del tls_adapter._thread_local.session
    tls_adapter._thread_local.pinned = False
    tls_adapter.clear_pin()


@pytest.fixture(autouse=True)
def _reset_tls_state():
    _forget_tls_state()
    yield
    _forget_tls_state()
