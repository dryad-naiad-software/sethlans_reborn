# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``_save_output_with_lock_retry`` (Issue #118).

The helper guards ``upload_output`` against SQLite lock-upgrade errors
that the ``busy_timeout`` PRAGMA does not cover.  These tests exercise
the retry contract with a mocked save target so they run in
milliseconds and never touch a real database.
"""

import io
from unittest.mock import MagicMock, patch

import pytest
from django.db import OperationalError

from workers.views.job_worker_actions import (
    _SAVE_RETRY_ATTEMPTS,
    _save_output_with_lock_retry,
)


class _FakeOutputFile:
    """Stand-in for ``FieldFile`` that records save() calls."""

    def __init__(self, raises=None, succeed_after=None):
        self.calls = 0
        self.raises = raises  # exception instance to raise
        self.succeed_after = succeed_after  # stop raising after N calls

    def save(self, name, content, save=True):
        self.calls += 1
        if self.raises is None:
            return
        if self.succeed_after is not None and self.calls > self.succeed_after:
            return
        raise self.raises


class _FakeJob:
    def __init__(self, raises=None, succeed_after=None):
        self.pk = 42
        self.output_file = _FakeOutputFile(
            raises=raises, succeed_after=succeed_after,
        )


def _file_obj(data=b"PNGDATA"):
    buf = io.BytesIO(data)
    buf.name = "out.png"
    return buf


class TestSaveOutputWithLockRetrySuccess:

    def test_succeeds_on_first_try_when_no_error(self):
        """No exception → exactly one save call, no retry/backoff."""
        job = _FakeJob()
        with patch("workers.views.job_worker_actions.time.sleep") as m_sleep:
            _save_output_with_lock_retry(job, "out.png", _file_obj())
        assert job.output_file.calls == 1
        m_sleep.assert_not_called()

    def test_recovers_after_transient_lock_error(self):
        """Lock error on attempt 1 → retries → succeeds on attempt 2."""
        job = _FakeJob(
            raises=OperationalError("database table is locked"),
            succeed_after=1,
        )
        with patch("workers.views.job_worker_actions.time.sleep") as m_sleep:
            _save_output_with_lock_retry(job, "out.png", _file_obj())
        assert job.output_file.calls == 2
        assert m_sleep.call_count == 1

    def test_backoff_is_exponential(self):
        """Each failed attempt doubles the sleep delay."""
        # Fail twice, then succeed on the third.
        job = _FakeJob(
            raises=OperationalError("database is locked"),
            succeed_after=2,
        )
        with patch("workers.views.job_worker_actions.time.sleep") as m_sleep:
            _save_output_with_lock_retry(job, "out.png", _file_obj())
        assert job.output_file.calls == 3
        delays = [call.args[0] for call in m_sleep.call_args_list]
        assert len(delays) == 2
        # Exponential back-off: second delay is 2x the first.
        assert delays[1] == pytest.approx(delays[0] * 2)


class TestSaveOutputWithLockRetryFailure:

    def test_exhausts_retries_and_raises_last_exception(self):
        """Persistent lock error → raises after max attempts."""
        job = _FakeJob(raises=OperationalError("database table is locked"))
        with patch("workers.views.job_worker_actions.time.sleep"):
            with pytest.raises(OperationalError, match="locked"):
                _save_output_with_lock_retry(job, "out.png", _file_obj())
        assert job.output_file.calls == _SAVE_RETRY_ATTEMPTS

    def test_non_lock_operational_error_is_not_retried(self):
        """A non-lock OperationalError surfaces immediately."""
        job = _FakeJob(raises=OperationalError("no such table"))
        with patch("workers.views.job_worker_actions.time.sleep") as m_sleep:
            with pytest.raises(OperationalError, match="no such table"):
                _save_output_with_lock_retry(job, "out.png", _file_obj())
        # Exactly one call — no retry for non-lock errors.
        assert job.output_file.calls == 1
        m_sleep.assert_not_called()

    def test_unrelated_exception_is_not_retried(self):
        """``ValueError`` (not ``OperationalError``) propagates immediately."""
        job = _FakeJob(raises=ValueError("bad upload"))
        with patch("workers.views.job_worker_actions.time.sleep") as m_sleep:
            with pytest.raises(ValueError, match="bad upload"):
                _save_output_with_lock_retry(job, "out.png", _file_obj())
        assert job.output_file.calls == 1
        m_sleep.assert_not_called()


class TestSaveOutputWithLockRetryRewindsInput:

    def test_file_obj_is_rewound_between_attempts(self):
        """On retry the file pointer is seeked back to 0 so the next
        attempt can re-read the full payload."""
        file_obj = MagicMock()
        file_obj.seek = MagicMock()
        job = _FakeJob(
            raises=OperationalError("locked"),
            succeed_after=1,
        )
        with patch("workers.views.job_worker_actions.time.sleep"):
            _save_output_with_lock_retry(job, "out.png", file_obj)
        # One retry = one seek(0) call between attempts.
        assert file_obj.seek.call_count == 1
        file_obj.seek.assert_called_with(0)

    def test_missing_seek_attribute_is_tolerated(self):
        """A file-like object without ``seek`` must not crash the retry."""

        class _NoSeek:
            pass

        file_obj = _NoSeek()
        job = _FakeJob(
            raises=OperationalError("locked"),
            succeed_after=1,
        )
        with patch("workers.views.job_worker_actions.time.sleep"):
            # Must not raise AttributeError — the retry wrapper swallows
            # seek() failures.
            _save_output_with_lock_retry(job, "out.png", file_obj)
        assert job.output_file.calls == 2
