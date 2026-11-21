# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The sequence submission API (Unreal sequence rendering FR-SQ-1..11,
FR-SS-1).

``SequenceSubmissionCreateSerializer`` checks the request body only
(strict JSON types; ``sequence_validation.request_errors``); the rules that
need the shots run in ``services/sequence_submit.py`` with the discovery.
The read serializers are read-only: a submission changes only through its
actions (FR-SQ-10).  The progress counts come from the view's subquery
annotations (``shots_total`` ...), never from a query per row.
"""

from rest_framework import serializers

from shared.unreal.shots_result import FRAME_MAX, SHOT_NAME_MAX

from ..constants_sequence import MAX_SUBMISSION_SHOTS, RESOLUTION_MAX, RESOLUTION_MIN, FrameFormat
from ..models import SequenceShot, SequenceSubmission
from .sequence_discovery import CREATE_KEYS as DISCOVERY_KEYS, SequenceDiscoveryCreateSerializer
from .sequence_validation import request_errors
from .unreal_measurement import StrictBooleanField, StrictCharField, StrictIntegerField
from .video_settings_validation import (
    expand_and_validate_video_settings, validate_video_settings_create_guard,
)

CREATE_KEYS = DISCOVERY_KEYS | {
    'shots', 'frame_start', 'frame_end', 'resolution_x', 'resolution_y', 'frame_format',
    'video_settings', 'sound', 'merge_sound', 'draft',
}


def _size(**kwargs):
    return StrictIntegerField(required=False, min_value=RESOLUTION_MIN, max_value=RESOLUTION_MAX, **kwargs)


def _frame(**kwargs):
    return StrictIntegerField(required=False, min_value=0, max_value=FRAME_MAX - 1, **kwargs)


class SequenceSubmissionCreateSerializer(SequenceDiscoveryCreateSerializer):
    """``POST /api/sequence-submissions/`` body (FR-SQ-3..9)."""

    shots = serializers.ListField(
        child=StrictCharField(max_length=SHOT_NAME_MAX), required=False, min_length=1,
        max_length=MAX_SUBMISSION_SHOTS, help_text="Names of shots of a DONE discovery; omit for all.",
    )
    frame_start = _frame(help_text="First edit frame (inclusive); with frame_end, instead of shots.")
    frame_end = _frame(help_text="Last edit frame (inclusive).")
    resolution_x = _size(help_text="Output width; omit both for the preset's size.")
    resolution_y = _size(help_text="Output height.")
    frame_format = serializers.ChoiceField(choices=FrameFormat.choices, default=FrameFormat.PNG)
    video_settings = serializers.JSONField(
        required=False, allow_null=True, default=None,
        help_text="A video preset or custom settings, without framerate (the sequence's rate is used).",
    )
    sound = StrictBooleanField(required=False, help_text="Render sound; default: the project's unreal_sound.")
    merge_sound = StrictBooleanField(default=False, help_text="Also make videos with the sound merged in.")
    draft = serializers.JSONField(
        required=False, allow_null=True, default=None,
        help_text=(
            "Draft quality: temporal_samples, spatial_samples, screen_percentage (10-100), "
            "motion_blur, ray_traced_shadows, ray_traced_reflections (false only)."
        ),
    )

    def validate_video_settings(self, value):
        validate_video_settings_create_guard(value)
        if value is None:
            return None
        try:
            return expand_and_validate_video_settings(dict(value), 'PNG', require_framerate=False)
        except serializers.ValidationError as exc:
            # The shared rules key their errors by field; this is the field's own validator.
            detail = exc.detail.get('video_settings', exc.detail) if isinstance(exc.detail, dict) else exc.detail
            raise serializers.ValidationError(detail) from exc

    def validate(self, attrs):
        if 'sound' not in attrs:
            attrs['sound'] = attrs['asset'].project.unreal_sound
        errors = request_errors(attrs)
        if errors:
            raise serializers.ValidationError({field: [message] for field, message in errors.items()})
        return attrs


class SequenceShotSerializer(serializers.ModelSerializer):
    """One shot of a submission (FR-SS-1); ``end_frame`` is exclusive."""

    timings = serializers.JSONField(read_only=True, help_text="R7 timings, newest last (at most 50).")

    class Meta:
        model = SequenceShot
        fields = [
            'index', 'name', 'start_frame', 'end_frame', 'status', 'error_message', 'render_time_seconds',
            'timings', 'video_status', 'video_error', 'sound_status', 'sound_error', 'sound_video_status',
            'sound_video_error',
        ]
        read_only_fields = fields


COUNT_FIELDS = ('shots_total', 'shots_done', 'shots_error', 'pieces_total', 'pieces_done')


class SequenceSubmissionSerializer(serializers.ModelSerializer):
    """A submission (version) as the list shows it; read-only."""

    shots_total = serializers.IntegerField(read_only=True)
    shots_done = serializers.IntegerField(read_only=True)
    shots_error = serializers.IntegerField(read_only=True)
    pieces_total = serializers.IntegerField(read_only=True)
    pieces_done = serializers.IntegerField(read_only=True)
    selected_shots = serializers.ListField(child=serializers.CharField(), read_only=True, allow_null=True)
    video_settings = serializers.JSONField(read_only=True)
    draft = serializers.JSONField(read_only=True)
    warnings = serializers.ListField(child=serializers.CharField(), read_only=True)

    class Meta:
        model = SequenceSubmission
        fields = [
            'id', 'project', 'asset', 'discovery', 'engine_build', 'map', 'sequence', 'preset', 'version', 'name',
            'selected_shots', 'frame_start', 'frame_end', 'resolution_x', 'resolution_y', 'frame_format',
            'video_settings', 'sound', 'merge_sound', 'draft', 'piece_seconds', 'lead_in_ms', 'sound_offset_ms',
            'status', 'cancel_requested', 'error_message', 'warnings', 'submitted_at', 'completed_at',
            'total_render_time_seconds', 'video_status', 'video_error', 'sound_status', 'sound_error',
            'sound_video_status', 'sound_video_error',
            *COUNT_FIELDS,
        ]
        read_only_fields = fields


class SequenceSubmissionDetailSerializer(SequenceSubmissionSerializer):
    """One submission with its shots, its pilot frames and its sound runs."""

    shots = SequenceShotSerializer(many=True, read_only=True)
    pilot_frames = serializers.ListField(child=serializers.IntegerField(), read_only=True)
    sound_runs = serializers.JSONField(
        read_only=True,
        help_text=(
            "One entry per audio job (FR-SA-11): job, start, end (exclusive), r0, rate, the measured "
            "offset object, applied_samples and applied_source (measured, override or none)."
        ),
    )

    class Meta(SequenceSubmissionSerializer.Meta):
        fields = SequenceSubmissionSerializer.Meta.fields + ['pilot_frames', 'sound_runs', 'shots']
        read_only_fields = fields
