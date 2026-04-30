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
from ..constants import (
    PILLOW_COMPATIBLE_FORMATS, RenderSettings,
    VIDEO_PRESETS, VIDEO_CODECS, VIDEO_CONTAINERS,
    VIDEO_CODEC_CONTAINER_MAP, VIDEO_COMPATIBLE_FORMATS,
)
from .projects import ProjectSerializer
from .assets import AssetSerializer
from .blender_versions import EffectiveBlenderVersionSerializer
from .validation_utils import validate_render_settings, validate_output_pattern_extension


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

        Defensive guard for the boot-window race where an admin
        submits an animation with ``video_settings`` before the
        manager's parts-check has finished resolving FFmpeg.  Per
        spec FR §128-133, the rejection is a standard DRF 400 with
        ``code="video_assembly_unavailable"`` (not a custom error
        envelope), and a non-null ``video_settings`` is the only
        case that triggers it.

        Race-window note (LOW concurrency, per spec FR §133):
        there is a microscopic window between this status read and
        the model save where the parts-check could flip
        ``installing -> ready``.  Worst case is a spurious 400
        during the boot-overlap window; the user retries.  Failing
        closed is the safe direction — synchronization is not added.
        """
        if value is not None:
            from ..services import parts_check
            snapshot = parts_check.get_status("ffmpeg")
            if snapshot.status != "ready":
                raise serializers.ValidationError(
                    "video_assembly_unavailable",
                    code="video_assembly_unavailable",
                )
        return value

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

        # video_settings is immutable after creation (spec FR §135-138).
        # Without this, the post-save signal handler's race-impossibility
        # argument collapses: an admin could PATCH video_settings to
        # non-null after a frame completes and assembly would fire
        # against a not-yet-ready FFmpeg.  Any change — adding,
        # removing, or replacing — is rejected with the closed-vocab
        # code ``video_settings_immutable``.
        if self.instance is not None and 'video_settings' in data:
            if data['video_settings'] != self.instance.video_settings:
                raise serializers.ValidationError(
                    {
                        "video_settings": [
                            "video_settings_immutable",
                        ],
                    },
                    code="video_settings_immutable",
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

        # Validate video_settings
        video_settings = data.get('video_settings')
        if video_settings is not None:
            data['video_settings'] = self._validate_video_settings(
                video_settings, file_format
            )
            data['video_status'] = 'PENDING'

        # Run model-level clean() validation
        instance = Animation(**data)
        try:
            instance.clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.message_dict)

        return data

    def _validate_video_settings(self, video_settings, file_format):
        """Validate and expand video_settings, returning the final dict."""
        if not isinstance(video_settings, dict):
            raise serializers.ValidationError(
                {"video_settings": "Must be a JSON object."}
            )

        # Check HDR format restriction
        if file_format not in VIDEO_COMPATIBLE_FORMATS:
            raise serializers.ValidationError(
                {"video_settings": (
                    f"Video output is not available for {file_format} format. "
                    f"Use PNG, JPEG, TIFF, BMP, or Targa."
                )}
            )

        preset = video_settings.get('preset')
        if preset is None:
            raise serializers.ValidationError(
                {"video_settings": "The 'preset' key is required."}
            )

        if preset != 'custom':
            self._expand_preset(video_settings, preset)
        else:
            self._validate_custom_settings(video_settings)

        # Validate framerate (required for both modes)
        framerate = video_settings.get('framerate')
        if not isinstance(framerate, int) or not (1 <= framerate <= 120):
            raise serializers.ValidationError(
                {"video_settings": "Framerate must be an integer between 1 and 120."}
            )

        return video_settings

    def _expand_preset(self, video_settings, preset):
        """Look up a preset and merge its values into video_settings."""
        if preset not in VIDEO_PRESETS:
            raise serializers.ValidationError(
                {"video_settings": f"Unknown video preset '{preset}'."}
            )
        preset_config = VIDEO_PRESETS[preset]
        video_settings['container'] = preset_config['container']
        video_settings['codec'] = preset_config['codec']
        video_settings['crf'] = preset_config['crf']

    def _validate_custom_settings(self, video_settings):
        """Validate custom mode container, codec, and crf values."""
        container = video_settings.get('container')
        codec = video_settings.get('codec')
        if container not in VIDEO_CONTAINERS:
            raise serializers.ValidationError(
                {"video_settings": f"Invalid container '{container}'."}
            )
        if codec not in VIDEO_CODECS:
            raise serializers.ValidationError(
                {"video_settings": f"Invalid codec '{codec}'."}
            )
        valid_containers = VIDEO_CODEC_CONTAINER_MAP.get(codec, [])
        if container not in valid_containers:
            raise serializers.ValidationError(
                {"video_settings": (
                    f"Codec '{codec}' is not valid for "
                    f"container '{container}'."
                )}
            )
        crf = video_settings.get('crf')
        if not isinstance(crf, int) or not (0 <= crf <= 51):
            raise serializers.ValidationError(
                {"video_settings": "CRF must be an integer between 0 and 51."}
            )

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
