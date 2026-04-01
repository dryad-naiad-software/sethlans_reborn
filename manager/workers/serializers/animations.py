# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Serializers for the Animation and AnimationFrame models.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from ..models import Animation, AnimationFrame, Asset, Project, JobStatus
from .projects import ProjectSerializer
from .assets import AssetSerializer


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

    class Meta:
        model = Animation
        fields = [
            'id', 'name', 'status', 'progress', 'total_frames', 'completed_frames',
            'project', 'project_details', 'asset', 'asset_id', 'output_file_pattern',
            'start_frame', 'end_frame', 'frame_step',
            'blender_version', 'render_engine', 'render_device', 'cycles_feature_set',
            'render_settings', 'tiling_config',
            'submitted_at', 'completed_at',
            'total_render_time_seconds', 'thumbnail', 'frames'
        ]
        read_only_fields = (
            'status', 'progress', 'total_frames', 'completed_frames',
            'submitted_at', 'completed_at',
            'total_render_time_seconds', 'asset', 'project_details',
            'thumbnail', 'frames'
        )
        extra_kwargs = {
            'project': {'write_only': True}
        }

    def validate(self, data):
        """
        Custom validation to ensure the selected `Asset` belongs to the `Project`
        and that model-level constraints (frame range, frame step) are satisfied.
        """
        project = data.get('project')
        asset = data.get('asset')
        if project and asset and asset.project != project:
            raise serializers.ValidationError(
                "The selected Asset does not belong to the selected Project."
            )

        # Run model-level clean() validation
        instance = Animation(**data)
        try:
            instance.clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.message_dict)

        return data

    def get_total_frames(self, obj):
        """
        Calculates the total number of frames in the animation.
        """
        return (obj.end_frame - obj.start_frame) + 1

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
