# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``POST /api/heartbeat/{pk}/yield-event/``.

Covers: yield event creation, all YieldReason enum values, job FK
handling, denormalized job_name, validation of invalid reason /
grace_outcome / progress_at_yield, and auth enforcement.

Spec references: FR-9a through FR-9e.
"""

import pytest
from rest_framework.test import APIClient

from workers.constants import GraceOutcome, YieldReason
from workers.models import Job, JobStatus, WorkerYieldEvent


def _yield_url(worker_pk):
    return f'/api/heartbeat/{worker_pk}/yield-event/'


def _valid_payload(**overrides):
    """Return a minimal valid yield-event payload."""
    data = {
        'reason': YieldReason.ARTIST_RETURN_GPU_CONTENTION,
        'grace_outcome': GraceOutcome.FINISHED,
        'progress_at_yield': 0.82,
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
class TestYieldEventCreation:
    """POST /api/heartbeat/{pk}/yield-event/ creates a DB row."""

    def test_creates_yield_event_row(self, worker_with_token):
        worker, client = worker_with_token
        resp = client.post(
            _yield_url(worker.pk),
            data=_valid_payload(),
            format='json',
        )
        assert resp.status_code == 201
        assert WorkerYieldEvent.objects.filter(
            worker=worker,
        ).count() == 1

    def test_response_contains_expected_fields(
        self, worker_with_token,
    ):
        worker, client = worker_with_token
        resp = client.post(
            _yield_url(worker.pk),
            data=_valid_payload(),
            format='json',
        )
        assert resp.status_code == 201
        assert resp.data['reason'] == (
            YieldReason.ARTIST_RETURN_GPU_CONTENTION
        )
        assert resp.data['grace_outcome'] == GraceOutcome.FINISHED
        assert resp.data['progress_at_yield'] == 0.82
        assert resp.data['worker'] == worker.pk


@pytest.mark.django_db
class TestYieldEventAllReasons:
    """Every YieldReason enum value is accepted by the endpoint."""

    @pytest.mark.parametrize('reason', [
        YieldReason.ARTIST_RETURN_GPU_CONTENTION,
        YieldReason.ARTIST_RETURN_APP_LAUNCH,
        YieldReason.ARTIST_RETURN_SESSION_UNLOCK,
        YieldReason.ARTIST_RETURN_SUSTAINED_ACTIVITY,
        YieldReason.MANUAL_OVERRIDE,
        YieldReason.SCHEDULE_WINDOW_CLOSED,
    ])
    def test_reason_accepted(self, worker_with_token, reason):
        worker, client = worker_with_token
        resp = client.post(
            _yield_url(worker.pk),
            data=_valid_payload(reason=reason),
            format='json',
        )
        assert resp.status_code == 201
        evt = WorkerYieldEvent.objects.get(pk=resp.data['id'])
        assert evt.reason == reason


@pytest.mark.django_db
class TestYieldEventJobFK:
    """Verify job FK and denormalized job_name handling."""

    def test_job_fk_set_when_provided(
        self, worker_with_token, asset,
    ):
        worker, client = worker_with_token
        job = Job.objects.create(
            name='YieldTestJob1',
            asset=asset,
            output_file_pattern='//render/#.png',
            status=JobStatus.RENDERING,
            assigned_worker=worker,
        )
        resp = client.post(
            _yield_url(worker.pk),
            data=_valid_payload(job=job.pk),
            format='json',
        )
        assert resp.status_code == 201
        evt = WorkerYieldEvent.objects.get(pk=resp.data['id'])
        assert evt.job_id == job.pk

    def test_job_fk_null_when_omitted(self, worker_with_token):
        worker, client = worker_with_token
        resp = client.post(
            _yield_url(worker.pk),
            data=_valid_payload(),
            format='json',
        )
        assert resp.status_code == 201
        evt = WorkerYieldEvent.objects.get(pk=resp.data['id'])
        assert evt.job is None

    def test_job_name_denormalized_when_sent(
        self, worker_with_token, asset,
    ):
        worker, client = worker_with_token
        job = Job.objects.create(
            name='DenormTest01',
            asset=asset,
            output_file_pattern='//render/#.png',
        )
        resp = client.post(
            _yield_url(worker.pk),
            data=_valid_payload(
                job=job.pk,
                job_name='DenormTest01',
            ),
            format='json',
        )
        assert resp.status_code == 201
        evt = WorkerYieldEvent.objects.get(pk=resp.data['id'])
        assert evt.job_name == 'DenormTest01'


@pytest.mark.django_db
class TestYieldEventValidation:
    """Invalid payloads rejected with 400."""

    def test_invalid_reason_rejected(self, worker_with_token):
        worker, client = worker_with_token
        resp = client.post(
            _yield_url(worker.pk),
            data=_valid_payload(reason='not_a_real_reason'),
            format='json',
        )
        assert resp.status_code == 400

    def test_invalid_grace_outcome_rejected(
        self, worker_with_token,
    ):
        worker, client = worker_with_token
        resp = client.post(
            _yield_url(worker.pk),
            data=_valid_payload(grace_outcome='exploded'),
            format='json',
        )
        assert resp.status_code == 400

    def test_progress_above_1_rejected(self, worker_with_token):
        worker, client = worker_with_token
        resp = client.post(
            _yield_url(worker.pk),
            data=_valid_payload(progress_at_yield=1.5),
            format='json',
        )
        assert resp.status_code == 400

    def test_progress_below_0_rejected(self, worker_with_token):
        worker, client = worker_with_token
        resp = client.post(
            _yield_url(worker.pk),
            data=_valid_payload(progress_at_yield=-0.1),
            format='json',
        )
        assert resp.status_code == 400

    def test_missing_reason_rejected(self, worker_with_token):
        worker, client = worker_with_token
        payload = _valid_payload()
        del payload['reason']
        resp = client.post(
            _yield_url(worker.pk),
            data=payload,
            format='json',
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestYieldEventAuth:
    """Unauthenticated requests are rejected."""

    def test_unauthenticated_request_rejected(
        self, worker_with_token,
    ):
        worker, _ = worker_with_token
        anon = APIClient()
        resp = anon.post(
            _yield_url(worker.pk),
            data=_valid_payload(),
            format='json',
        )
        assert resp.status_code == 401

    def test_nonexistent_worker_returns_404(
        self, worker_with_token,
    ):
        _, client = worker_with_token
        resp = client.post(
            _yield_url(999999),
            data=_valid_payload(),
            format='json',
        )
        assert resp.status_code == 404
