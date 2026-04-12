# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``POST /api/heartbeat/{pk}/yield-event/``
(``manager/workers/views/yield_actions.py``).

Core CRUD tests: creation, response format, basic enum validation.

Detailed validation edge cases (invalid enums, boundary floats,
ownership checks, model field constraints, job_name denormalization)
live in ``test_yield_event_validation.py``.
"""

from unittest.mock import MagicMock, patch

import pytest
from rest_framework import serializers as drf_serializers

from workers.constants import GraceOutcome, YieldReason
from workers.serializers import WorkerYieldEventSerializer
from workers.views.yield_actions import WorkerYieldActionsMixin


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_worker():
    """A mock Worker instance with pk and hostname."""
    worker = MagicMock()
    worker.pk = 7
    worker.hostname = "render-box-01"
    return worker


@pytest.fixture
def valid_payload():
    """A valid yield-event payload (string enum values)."""
    return {
        "reason": YieldReason.ARTIST_RETURN_GPU_CONTENTION.value,
        "grace_outcome": GraceOutcome.FINISHED.value,
        "progress_at_yield": 0.82,
    }


@pytest.fixture
def mixin():
    """Instantiate the mixin directly for unit testing."""
    return WorkerYieldActionsMixin()


# ---------------------------------------------------------------------------
# Helper: build a mock serializer class for view-level tests
# ---------------------------------------------------------------------------

def _build_mock_serializer(is_valid=True, data=None, errors=None):
    """Return a mock serializer class whose instances behave predictably.

    The mock serializer's ``is_valid(raise_exception=True)`` either
    succeeds or raises ``ValidationError``.  ``save(worker=...)``
    records the worker kwarg.  ``data`` returns *data* (or {}).
    """
    instance = MagicMock()
    instance.validated_data = data or {}
    instance.data = data or {}
    if is_valid:
        instance.is_valid.return_value = True
    else:
        instance.is_valid.side_effect = drf_serializers.ValidationError(
            errors or {"detail": "invalid"},
        )
    klass = MagicMock(return_value=instance)
    return klass, instance


# ===================================================================
# Group 1: View logic tests (serializer is mocked)
# ===================================================================


class TestYieldEventViewHappyPath:
    """POST yield-event returns 201 and delegates to the serializer."""

    @patch("workers.views.yield_actions.Worker.objects")
    @patch("workers.views.yield_actions.WorkerYieldEventSerializer")
    def test_valid_post_returns_201(
        self, MockSerializer, mock_qs, mixin, mock_worker,
        valid_payload,
    ):
        mock_qs.get.return_value = mock_worker
        ser_instance = MockSerializer.return_value
        ser_instance.is_valid.return_value = True
        ser_instance.data = valid_payload
        ser_instance.validated_data = valid_payload

        request = MagicMock()
        request.data = valid_payload
        request.user.worker_profile = mock_worker

        response = mixin.yield_event(request, pk=mock_worker.pk)
        assert response.status_code == 201

    @patch("workers.views.yield_actions.Worker.objects")
    @patch("workers.views.yield_actions.WorkerYieldEventSerializer")
    def test_serializer_save_called_with_worker(
        self, MockSerializer, mock_qs, mixin, mock_worker,
        valid_payload,
    ):
        """Worker is injected from URL pk via serializer.save(worker=...)."""
        mock_qs.get.return_value = mock_worker
        ser_instance = MockSerializer.return_value
        ser_instance.is_valid.return_value = True
        ser_instance.data = valid_payload
        ser_instance.validated_data = valid_payload

        request = MagicMock()
        request.data = valid_payload
        request.user.worker_profile = mock_worker

        mixin.yield_event(request, pk=mock_worker.pk)
        ser_instance.save.assert_called_once_with(worker=mock_worker)

    @patch("workers.views.yield_actions.Worker.objects")
    @patch("workers.views.yield_actions.WorkerYieldEventSerializer")
    def test_response_body_is_serializer_data(
        self, MockSerializer, mock_qs, mixin, mock_worker,
        valid_payload,
    ):
        mock_qs.get.return_value = mock_worker
        ser_instance = MockSerializer.return_value
        ser_instance.is_valid.return_value = True
        expected_data = {**valid_payload, "id": 1, "worker": 7}
        ser_instance.data = expected_data
        ser_instance.validated_data = valid_payload

        request = MagicMock()
        request.data = valid_payload
        request.user.worker_profile = mock_worker

        response = mixin.yield_event(request, pk=mock_worker.pk)
        assert response.data == expected_data

    @patch("workers.views.yield_actions.Worker.objects")
    @patch("workers.views.yield_actions.WorkerYieldEventSerializer")
    def test_body_worker_field_ignored(
        self, MockSerializer, mock_qs, mixin, mock_worker,
        valid_payload,
    ):
        """Even if body includes 'worker', save() receives the URL worker."""
        mock_qs.get.return_value = mock_worker
        ser_instance = MockSerializer.return_value
        ser_instance.is_valid.return_value = True
        ser_instance.data = valid_payload
        ser_instance.validated_data = valid_payload

        payload_with_worker = {**valid_payload, "worker": 999}
        request = MagicMock()
        request.data = payload_with_worker
        request.user.worker_profile = mock_worker

        mixin.yield_event(request, pk=mock_worker.pk)
        # The view always calls save(worker=<url_worker>).
        ser_instance.save.assert_called_once_with(worker=mock_worker)


class TestYieldEventViewValidationFailure:
    """View returns 400 when the serializer raises ValidationError."""

    @patch("workers.views.yield_actions.Worker.objects")
    @patch("workers.views.yield_actions.WorkerYieldEventSerializer")
    def test_invalid_data_raises_and_returns_400(
        self, MockSerializer, mock_qs, mixin, mock_worker,
    ):
        mock_qs.get.return_value = mock_worker
        ser_instance = MockSerializer.return_value
        ser_instance.is_valid.side_effect = (
            drf_serializers.ValidationError({"reason": ["Invalid."]})
        )

        request = MagicMock()
        request.data = {"reason": "bogus"}
        request.user.worker_profile = mock_worker

        with pytest.raises(drf_serializers.ValidationError):
            mixin.yield_event(request, pk=mock_worker.pk)


class TestYieldEventViewWorkerNotFound:
    """Return 404 when the worker pk does not exist."""

    @patch("workers.views.yield_actions.Worker.objects")
    def test_nonexistent_worker_returns_404(
        self, mock_qs, mixin, valid_payload,
    ):
        from workers.models import Worker
        mock_qs.get.side_effect = Worker.DoesNotExist
        request = MagicMock()
        request.data = valid_payload

        response = mixin.yield_event(request, pk=9999)

        assert response.status_code == 404
        assert response.data["detail"] == "Worker not found."


# ===================================================================
# Group 2: Basic enum validation (real serializer, no DB)
# ===================================================================


class TestSerializerValidReason:
    """All YieldReason values pass serializer validation."""

    @pytest.mark.parametrize("reason", [r.value for r in YieldReason])
    def test_valid_reason_accepted(self, reason):
        data = {
            "reason": reason,
            "grace_outcome": GraceOutcome.FINISHED.value,
            "progress_at_yield": 0.5,
        }
        ser = WorkerYieldEventSerializer(data=data)
        assert ser.is_valid(), ser.errors

    def test_bogus_reason_rejected(self):
        data = {
            "reason": "not_a_real_reason",
            "grace_outcome": GraceOutcome.FINISHED.value,
            "progress_at_yield": 0.5,
        }
        ser = WorkerYieldEventSerializer(data=data)
        assert not ser.is_valid()
        assert "reason" in ser.errors


class TestSerializerValidGraceOutcome:
    """All GraceOutcome values pass serializer validation."""

    @pytest.mark.parametrize("outcome", [o.value for o in GraceOutcome])
    def test_valid_grace_outcome_accepted(self, outcome):
        data = {
            "reason": YieldReason.MANUAL_OVERRIDE.value,
            "grace_outcome": outcome,
            "progress_at_yield": 0.0,
        }
        ser = WorkerYieldEventSerializer(data=data)
        assert ser.is_valid(), ser.errors

    def test_bogus_grace_outcome_rejected(self):
        data = {
            "reason": YieldReason.MANUAL_OVERRIDE.value,
            "grace_outcome": "exploded",
            "progress_at_yield": 0.5,
        }
        ser = WorkerYieldEventSerializer(data=data)
        assert not ser.is_valid()
        assert "grace_outcome" in ser.errors
