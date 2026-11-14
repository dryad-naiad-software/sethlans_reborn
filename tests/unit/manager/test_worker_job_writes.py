# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the locked re-checks of worker job writes (issue #217).

``ensure_worker_can_write`` and ``ensure_valid_transition`` run on the
locked job row; plain stand-in objects are enough to exercise them.
"""

from types import SimpleNamespace

import pytest

from workers.models import JobStatus
from workers.services.worker_job_writes import (
    WORKER_PATCH_FIELDS,
    JobWriteConflict,
    apply_worker_patch,
    ensure_valid_transition,
    ensure_worker_can_write,
)

WORKER = SimpleNamespace(pk=7)


def _job(status=JobStatus.RENDERING, worker_id=7, is_paused=False):
    return SimpleNamespace(
        status=status, assigned_worker_id=worker_id, is_paused=is_paused,
    )


class TestEnsureWorkerCanWrite:

    def test_rendering_job_of_this_worker_passes(self):
        ensure_worker_can_write(_job(), WORKER)

    @pytest.mark.parametrize('status', [
        JobStatus.CANCELED, JobStatus.QUEUED, JobStatus.ERROR, JobStatus.DONE,
    ])
    def test_job_not_rendering_conflicts(self, status):
        with pytest.raises(JobWriteConflict, match=status):
            ensure_worker_can_write(_job(status=status), WORKER)

    def test_requeued_job_conflicts(self):
        with pytest.raises(JobWriteConflict, match='no longer assigned'):
            ensure_worker_can_write(
                _job(status=JobStatus.QUEUED, worker_id=None), WORKER,
            )

    def test_job_of_other_worker_conflicts(self):
        with pytest.raises(JobWriteConflict, match='no longer assigned'):
            ensure_worker_can_write(_job(worker_id=8), WORKER)


class TestEnsureValidTransition:

    @pytest.mark.parametrize('new_status', [
        JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELED,
    ])
    def test_rendering_to_terminal_is_valid(self, new_status):
        ensure_valid_transition(_job(), new_status)

    @pytest.mark.parametrize('current, new_status', [
        (JobStatus.CANCELED, JobStatus.DONE),
        (JobStatus.CANCELED, JobStatus.ERROR),
        (JobStatus.QUEUED, JobStatus.DONE),
        (JobStatus.DONE, JobStatus.ERROR),
        (JobStatus.RENDERING, JobStatus.RENDERING),
        (JobStatus.RENDERING, JobStatus.QUEUED),
    ])
    def test_invalid_transition_conflicts(self, current, new_status):
        with pytest.raises(JobWriteConflict, match='Invalid status transition'):
            ensure_valid_transition(_job(status=current), new_status)


class TestWorkerPatchFields:

    def test_allowlist_matches_worker_agent_payloads(self):
        """The fields the worker agent sends (job_lifecycle, job_processor)."""
        assert WORKER_PATCH_FIELDS == {
            'status', 'completed_at', 'last_output', 'error_message',
            'render_time_seconds',
        }

    @pytest.mark.parametrize('field', [
        'assigned_worker', 'is_paused', 'name', 'render_settings',
        'output_file', 'asset',
    ])
    def test_apply_worker_patch_rejects_other_fields(self, field):
        """Rejected before any query, so no database is needed."""
        with pytest.raises(ValueError, match=field):
            apply_worker_patch(1, WORKER, {field: 'x', 'status': 'DONE'})
