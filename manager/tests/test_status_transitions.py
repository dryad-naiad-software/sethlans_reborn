# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for status transition validation in JobSerializer.

Covers: valid transitions, invalid transitions, and ensuring
validation only fires on updates (not creates).
"""

import pytest
from unittest.mock import MagicMock

from workers.models import JobStatus
from workers.serializers import JobSerializer, VALID_STATUS_TRANSITIONS


class TestValidStatusTransitionsMap:
    """Verify the VALID_STATUS_TRANSITIONS constant is correct."""

    def test_queued_can_transition_to_rendering_or_canceled(self):
        assert set(VALID_STATUS_TRANSITIONS[JobStatus.QUEUED]) == {
            JobStatus.RENDERING, JobStatus.CANCELED
        }

    def test_rendering_can_transition_to_done_error_or_canceled(self):
        assert set(VALID_STATUS_TRANSITIONS[JobStatus.RENDERING]) == {
            JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELED
        }

    def test_error_can_transition_to_queued(self):
        assert VALID_STATUS_TRANSITIONS[JobStatus.ERROR] == [JobStatus.QUEUED]

    def test_done_has_no_valid_transitions(self):
        assert VALID_STATUS_TRANSITIONS[JobStatus.DONE] == []

    def test_canceled_can_transition_to_queued(self):
        assert VALID_STATUS_TRANSITIONS[JobStatus.CANCELED] == [JobStatus.QUEUED]


class TestJobSerializerValidateStatus:
    """Tests for JobSerializer.validate_status method."""

    def _make_serializer_with_instance(self, current_status):
        """
        Create a JobSerializer bound to a mock instance with the given status.
        """
        mock_instance = MagicMock()
        mock_instance.status = current_status
        serializer = JobSerializer()
        serializer.instance = mock_instance
        return serializer

    @pytest.mark.parametrize("current,new", [
        (JobStatus.QUEUED, JobStatus.RENDERING),
        (JobStatus.QUEUED, JobStatus.CANCELED),
        (JobStatus.RENDERING, JobStatus.DONE),
        (JobStatus.RENDERING, JobStatus.ERROR),
        (JobStatus.RENDERING, JobStatus.CANCELED),
        (JobStatus.ERROR, JobStatus.QUEUED),
        (JobStatus.CANCELED, JobStatus.QUEUED),
    ])
    def test_valid_transitions_are_accepted(self, current, new):
        """Each valid transition should pass validation and return the value."""
        serializer = self._make_serializer_with_instance(current)
        result = serializer.validate_status(new)
        assert result == new

    @pytest.mark.parametrize("current,new", [
        (JobStatus.DONE, JobStatus.QUEUED),
        (JobStatus.DONE, JobStatus.RENDERING),
        (JobStatus.DONE, JobStatus.ERROR),
        (JobStatus.QUEUED, JobStatus.DONE),
        (JobStatus.QUEUED, JobStatus.ERROR),
        (JobStatus.RENDERING, JobStatus.QUEUED),
        (JobStatus.ERROR, JobStatus.DONE),
        (JobStatus.ERROR, JobStatus.RENDERING),
        (JobStatus.CANCELED, JobStatus.DONE),
        (JobStatus.CANCELED, JobStatus.RENDERING),
    ])
    def test_invalid_transitions_raise_validation_error(self, current, new):
        """Invalid transitions should raise a ValidationError."""
        from rest_framework import serializers
        serializer = self._make_serializer_with_instance(current)
        with pytest.raises(serializers.ValidationError) as exc_info:
            serializer.validate_status(new)
        assert "Invalid status transition" in str(exc_info.value)

    def test_validation_skipped_on_create(self):
        """
        When self.instance is None (i.e., during creation),
        validate_status should accept any value without error.
        """
        serializer = JobSerializer()
        serializer.instance = None

        # Should accept any status value without raising
        for status_val in JobStatus.values:
            result = serializer.validate_status(status_val)
            assert result == status_val

    def test_validation_fires_on_update_only(self):
        """
        Confirm that validate_status distinguishes between create and update
        by checking the instance attribute.
        """
        # Create mode: instance is None, any value passes
        create_serializer = JobSerializer()
        create_serializer.instance = None
        assert create_serializer.validate_status(JobStatus.DONE) == JobStatus.DONE

        # Update mode: instance exists, invalid transition raises
        from rest_framework import serializers
        update_serializer = self._make_serializer_with_instance(JobStatus.QUEUED)
        with pytest.raises(serializers.ValidationError):
            update_serializer.validate_status(JobStatus.DONE)
