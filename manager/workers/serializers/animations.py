# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Serializers for the Animation and AnimationFrame models.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from ..models import (
    Animation, AnimationFrame, Asset, Project, JobStatus,
    SupportedBlenderVersion,
)
from ..constants import PILLOW_COMPATIBLE_FORMATS, RenderSettings
from .projects import ProjectSerializer
from .assets import AssetSerializer
from .blender_versions import EffectiveBlenderVersionSerializer
from .validation_utils import validate_render_settings, validate_output_pattern_extension
from .video_settings_validation import (
    validate_video_settings_create_guard,
    expand_and_validate_video_settings,
    raise_video_settings_immutable,
)


class AnimationFrameSerializer(serializers.ModelSerializer):
    """
    Serializer for the individual, assembled frames of a tiled animation.
    """
    class Meta:
        model = AnimationFrame
        fields = ['id', 'frame_number', 'status', 'output_file', 'thumbnail', 'render_time_seconds']


class AnimationSerializer(serializers.ModelSerializer):
    """
    Serializer for the `Animation` model.

    This serializer includes custom fields to report progress and links to the
    child `AnimationFrame` objects for tiled animations.
    """
    progress = serializers.SerializerMethodField(
        help_text="Human-readable progress string (e.g., '3 of 10 frames complete')."
    )
    total_frames = serializers.SerializerMethodField(
        help_text="The total number of frames in the animation."
    )
    completed_frames = serializers.SerializerMethodField(
        help_text="The number of frames that are in a 'DONE' status."
    )
    frames = AnimationFrameSerializer(
        many=True, read_only=True,
        help_text="List of child frames for tiled animations."
    )

    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())
    project_details = ProjectSerializer(source='project', read_only=True)
    asset = AssetSerializer(read_only=True)
    asset_id = serializers.PrimaryKeyRelatedField(
        queryset=Asset.objects.all(), source='asset', write_only=True
    )
    blender_version = serializers.PrimaryKeyRelatedField(
        queryset=SupportedBlenderVersion.objects.all(),
        required=False, allow_null=True,
    )
    effective_blender_version = EffectiveBlenderVersionSerializer(read_only=True)

    class Meta:
        model = Animation
        fields = [
            'id', 'name', 'status', 'progress', 'total_frames', 'completed_frames',
            'project', 'project_details', 'asset', 'asset_id', 'output_file_pattern',
            'start_frame', 'end_frame', 'frame_step',
            'blender_version', 'effective_blender_version',
            'render_engine', 'render_device', 'cycles_feature_set',
            'render_settings', 'tiling_config',
            'submitted_at', 'completed_at',
            'total_render_time_seconds', 'thumbnail', 'frames',
            'video_settings', 'video_status', 'video_file', 'video_error',
        ]
        read_only_fields = (
            'status', 'progress', 'total_frames', 'completed_frames',
            'submitted_at', 'completed_at',
            'total_render_time_seconds', 'asset', 'project_details',
            'thumbnail', 'frames', 'effective_blender_version',
            'video_status', 'video_file', 'video_error',
        )
        extra_kwargs = {
            'project': {'write_only': True}
        }

    def validate_render_settings(self, value):
        """Validate render_settings field values."""
        return validate_render_settings(value)

    def validate_video_settings(self, value):
        """Reject video_settings creates while FFmpeg is not ready.

        Delegates to the shared helper in ``video_settings_validation``;
        see that module for the full rationale (spec FR §128-133).
        """
        return validate_video_settings_create_guard(value)

    def validate(self, data):
        """
        Custom validation to ensure the selected `Asset` belongs to the `Project`,
        that model-level constraints (frame range, frame step) are satisfied,
        that tiled animations use Pillow-compatible output formats, and that
        video_settings are valid when provided.
        """
        project = data.get('project')
        asset = data.get('asset')
        if project and asset and asset.project != project:
            raise serializers.ValidationError(
                "The selected Asset does not belong to the selected Project."
            )

        # Validate output_file_pattern extension matches format
        validate_output_pattern_extension(data)

        # Validate format is Pillow-compatible for tiled animations
        tiling_config = data.get('tiling_config', 'NONE')
        render_settings = data.get('render_settings') or {}
        file_format = render_settings.get(
            RenderSettings.IMAGE_FILE_FORMAT, 'PNG'
        )
        if tiling_config != 'NONE':
            if file_format not in PILLOW_COMPATIBLE_FORMATS:
                allowed = ', '.join(sorted(PILLOW_COMPATIBLE_FORMATS))
                raise serializers.ValidationError(
                    f"Output format '{file_format}' is not supported for "
                    f"tiled rendering. Tiled jobs support: {allowed}."
                )

        # Expand + validate video_settings BEFORE the immutability
        # check so a no-op PATCH like ``{"preset": "1080p", "framerate":
        # 30}`` (which expands to the same dict already persisted)
        # compares equal to ``self.instance.video_settings`` (which is
        # ALWAYS stored in expanded form).  Without this ordering, every
        # legitimate PATCH that round-trips video_settings would fire a
        # spurious ``video_settings_immutable`` rejection.
        video_settings = data.get('video_settings')
        if video_settings is not None:
            data['video_settings'] = expand_and_validate_video_settings(
                video_settings, file_format
            )
            data['video_status'] = 'PENDING'

        # video_settings is immutable after creation (spec FR §135-138).
        # Without this, the post-save signal handler's race-impossibility
        # argument collapses: an admin could PATCH video_settings to
        # non-null after a frame completes and assembly would fire
        # against a not-yet-ready FFmpeg.  Any change — adding,
        # removing, or replacing — is rejected with the closed-vocab
        # code ``video_settings_immutable``.
        if self.instance is not None and 'video_settings' in data:
            if data['video_settings'] != self.instance.video_settings:
                raise_video_settings_immutable()

        # Run model-level clean() validation only on CREATE.  On
        # update, ``data`` is partial (PATCH) and ``Animation(**data)``
        # would have ``None`` for unsupplied fields, which is unsafe to
        # pass through ``clean()`` (e.g. ``end_frame < start_frame``
        # would compare two ``None`` values).  The existing instance
        # is already valid; the partial fields are validated by the
        # field-level validators above.
        if self.instance is None:
            instance = Animation(**data)
            try:
                instance.clean()
            except DjangoValidationError as e:
                raise serializers.ValidationError(e.message_dict)

        return data

    def get_total_frames(self, obj):
        """
        Calculates the total number of frames in the animation,
        accounting for frame_step.
        """
        if obj.frame_step <= 0:
            return 0
        return len(range(obj.start_frame, obj.end_frame + 1, obj.frame_step))

    def get_completed_frames(self, obj):
        """
        Counts the number of completed frames. Prefers annotated counts
        from the queryset to avoid N+1 queries, falling back to a direct
        query when annotations are not present.
        """
        if obj.tiling_config != 'NONE':
            annotated = getattr(obj, 'annotated_completed_frames', None)
            if annotated is not None:
                return annotated
            return obj.frames.filter(status='DONE').count()
        annotated = getattr(obj, 'annotated_completed_jobs', None)
        if annotated is not None:
            return annotated
        return obj.jobs.filter(status=JobStatus.DONE).count()

    def get_progress(self, obj):
        """
        Generates a human-readable string representing the progress of the animation.
        """
        completed = self.get_completed_frames(obj)
        total = self.get_total_frames(obj)
        if total == 0:
            return "0 of 0 frames complete"
        return f"{completed} of {total} frames complete"
