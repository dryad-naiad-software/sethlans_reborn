# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``run_with_lock_retry`` (Issue #118, reworked for #217).

The helper guards the worker's locked job writes (``upload_output`` and
the worker PATCH) against SQLite lock errors that the ``busy_timeout``
PRAGMA does not cover.  Since #217 it retries the whole locked
transaction, not a bare ``output_file.save``; the file itself is stored
once, before the transaction.  These tests exercise the retry contract
with a fake operation so they never touch a real database.
"""

from unittest.mock import patch

import pytest
from django.db import OperationalError

from workers.services.worker_job_writes import (
    _SAVE_RETRY_ATTEMPTS,
    JobWriteConflict,
    run_with_lock_retry,
)

SLEEP = "workers.services.worker_job_writes.time.sleep"


class _FakeOperation:
    """Callable that raises ``raises`` for its first ``fail_times`` calls."""

    def __init__(self, raises=None, fail_times=None, result="saved"):
        self.calls = 0
        self.raises = raises
        self.fail_times = fail_times
        self.result = result

    def __call__(self):
        self.calls += 1
        if self.raises is None:
            return self.result
        if self.fail_times is not None and self.calls > self.fail_times:
            return self.result
        raise self.raises


class TestRunWithLockRetrySuccess:

    def test_succeeds_on_first_try_when_no_error(self):
        """No exception: exactly one call, no back-off, result returned."""
        op = _FakeOperation()
        with patch(SLEEP) as m_sleep:
            assert run_with_lock_retry(op, 42) == "saved"
        assert op.calls == 1
        m_sleep.assert_not_called()

    def test_recovers_after_transient_lock_error(self):
        """Lock error on attempt 1, success on attempt 2."""
        op = _FakeOperation(
            raises=OperationalError("database table is locked"),
            fail_times=1,
        )
        with patch(SLEEP) as m_sleep:
            assert run_with_lock_retry(op, 42) == "saved"
        assert op.calls == 2
        assert m_sleep.call_count == 1

    def test_backoff_is_exponential(self):
        """Each failed attempt doubles the sleep delay."""
        op = _FakeOperation(
            raises=OperationalError("database is locked"),
            fail_times=2,
        )
        with patch(SLEEP) as m_sleep:
            run_with_lock_retry(op, 42)
        assert op.calls == 3
        delays = [call.args[0] for call in m_sleep.call_args_list]
        assert len(delays) == 2
        assert delays[1] == pytest.approx(delays[0] * 2)


class TestRunWithLockRetryFailure:

    def test_exhausts_retries_and_raises_last_exception(self):
        """Persistent lock error: raises after the maximum attempts."""
        op = _FakeOperation(raises=OperationalError("database table is locked"))
        with patch(SLEEP):
            with pytest.raises(OperationalError, match="locked"):
                run_with_lock_retry(op, 42)
        assert op.calls == _SAVE_RETRY_ATTEMPTS

    def test_non_lock_operational_error_is_not_retried(self):
        """A non-lock OperationalError surfaces immediately."""
        op = _FakeOperation(raises=OperationalError("no such table"))
        with patch(SLEEP) as m_sleep:
            with pytest.raises(OperationalError, match="no such table"):
                run_with_lock_retry(op, 42)
        assert op.calls == 1
        m_sleep.assert_not_called()

    def test_unrelated_exception_is_not_retried(self):
        """``ValueError`` (not ``OperationalError``) propagates immediately."""
        op = _FakeOperation(raises=ValueError("bad upload"))
        with patch(SLEEP) as m_sleep:
            with pytest.raises(ValueError, match="bad upload"):
                run_with_lock_retry(op, 42)
        assert op.calls == 1
        m_sleep.assert_not_called()

    def test_conflict_is_not_retried(self):
        """A 409 conflict is final: the locked re-check already decided."""
        op = _FakeOperation(raises=JobWriteConflict("canceled"))
        with patch(SLEEP) as m_sleep:
            with pytest.raises(JobWriteConflict):
                run_with_lock_retry(op, 42)
        assert op.calls == 1
        m_sleep.assert_not_called()
