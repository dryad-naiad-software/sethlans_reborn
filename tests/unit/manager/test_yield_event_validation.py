# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for yield-event validation edge cases.

Covers detailed validation for ``POST /api/heartbeat/{pk}/yield-event/``:
- progress_at_yield boundary floats (0.0, 1.0, out-of-range)
- worker field read-only enforcement
- YieldReason and GraceOutcome enum DB values and counts
- WorkerYieldEvent model field constraints (validators, choices,
  FK cascade/set_null, auto_now_add, ordering)

Core CRUD tests (creation, response format, basic enum validation)
live in ``test_yield_event_endpoint.py``.
"""

from workers.constants import GraceOutcome, YieldReason
from workers.models import WorkerYieldEvent
from workers.serializers import WorkerYieldEventSerializer


# ===================================================================
# Serializer: progress_at_yield validation (boundary floats)
# ===================================================================


class TestSerializerProgressValidation:
    """progress_at_yield must be between 0.0 and 1.0 inclusive."""

    def _make_data(self, progress):
        return {
            "reason": YieldReason.MANUAL_OVERRIDE.value,
            "grace_outcome": GraceOutcome.ABORTED.value,
            "progress_at_yield": progress,
        }

    def test_negative_progress_rejected(self):
        ser = WorkerYieldEventSerializer(data=self._make_data(-0.1))
        assert not ser.is_valid()
        assert "progress_at_yield" in ser.errors

    def test_progress_above_one_rejected(self):
        ser = WorkerYieldEventSerializer(data=self._make_data(1.01))
        assert not ser.is_valid()
        assert "progress_at_yield" in ser.errors

    def test_zero_progress_accepted(self):
        ser = WorkerYieldEventSerializer(data=self._make_data(0.0))
        assert ser.is_valid(), ser.errors

    def test_one_point_zero_progress_accepted(self):
        ser = WorkerYieldEventSerializer(data=self._make_data(1.0))
        assert ser.is_valid(), ser.errors

    def test_mid_range_progress_accepted(self):
        ser = WorkerYieldEventSerializer(data=self._make_data(0.5))
        assert ser.is_valid(), ser.errors


# ===================================================================
# Serializer: worker field read-only
# ===================================================================


class TestSerializerWorkerReadOnly:
    """The 'worker' field is read-only in the serializer."""

    def test_worker_in_read_only_fields(self):
        meta = WorkerYieldEventSerializer.Meta
        assert "worker" in meta.read_only_fields

    def test_worker_in_body_is_ignored(self):
        """Providing 'worker' in the body does not set it."""
        data = {
            "reason": YieldReason.MANUAL_OVERRIDE.value,
            "grace_outcome": GraceOutcome.FINISHED.value,
            "progress_at_yield": 0.5,
            "worker": 999,
        }
        ser = WorkerYieldEventSerializer(data=data)
        assert ser.is_valid(), ser.errors
        # worker should NOT be in validated_data (it's read_only)
        assert "worker" not in ser.validated_data


# ===================================================================
# Enum DB values and counts (FR-9b, FR-9c)
# ===================================================================


class TestEnumValues:
    """Verify enum DB values match the spec."""

    def test_yield_reason_count(self):
        """Six YieldReason values per FR-9b."""
        assert len(YieldReason.choices) == 6

    def test_yield_reason_db_values(self):
        expected = {
            "artist_return_gpu_contention",
            "artist_return_app_launch",
            "artist_return_session_unlock",
            "artist_return_sustained_activity",
            "manual_override",
            "schedule_window_closed",
        }
        actual = {choice[0] for choice in YieldReason.choices}
        assert actual == expected

    def test_grace_outcome_count(self):
        """Two GraceOutcome values per FR-9c."""
        assert len(GraceOutcome.choices) == 2

    def test_grace_outcome_db_values(self):
        expected = {"finished", "aborted"}
        actual = {choice[0] for choice in GraceOutcome.choices}
        assert actual == expected


# ===================================================================
# Model field constraints (no DB)
# ===================================================================


class TestModelFieldConstraints:
    """Verify WorkerYieldEvent model field configuration."""

    def test_progress_at_yield_has_validators(self):
        field = WorkerYieldEvent._meta.get_field("progress_at_yield")
        validator_types = [type(v).__name__ for v in field.validators]
        assert "MinValueValidator" in validator_types
        assert "MaxValueValidator" in validator_types

    def test_reason_field_has_choices(self):
        field = WorkerYieldEvent._meta.get_field("reason")
        assert field.choices == YieldReason.choices

    def test_grace_outcome_field_has_choices(self):
        field = WorkerYieldEvent._meta.get_field("grace_outcome")
        assert field.choices == GraceOutcome.choices

    def test_worker_fk_cascade_delete(self):
        from django.db import models as django_models
        field = WorkerYieldEvent._meta.get_field("worker")
        assert field.remote_field.on_delete is django_models.CASCADE

    def test_job_fk_set_null_on_delete(self):
        from django.db import models as django_models
        field = WorkerYieldEvent._meta.get_field("job")
        assert field.remote_field.on_delete is django_models.SET_NULL

    def test_timestamp_is_auto_now_add(self):
        field = WorkerYieldEvent._meta.get_field("timestamp")
        assert field.auto_now_add is True

    def test_ordering_is_newest_first(self):
        assert WorkerYieldEvent._meta.ordering == ["-timestamp"]
