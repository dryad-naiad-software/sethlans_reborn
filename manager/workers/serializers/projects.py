# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Serializer for the Project model.
"""

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from ..constants import Renderer
from ..constants_sequence import LEAD_IN_MS_MAX, PIECE_SECONDS_MAX, PIECE_SECONDS_MIN
from ..models import Project, SupportedBlenderVersion
from ..renderers.unreal_validation import validate_sound_offsets
from .blender_versions import SupportedBlenderVersionSerializer
from .renderer_checks import check_immutable_and_consistent, resolve_strategy


@extend_schema_field({'type': 'object', 'additionalProperties': {'type': 'string'}})
class CvarOverridesField(serializers.JSONField):
    """``unreal_cvar_overrides``: a CVar-name to value map (OpenAPI type)."""


@extend_schema_field({'type': 'object', 'nullable': True, 'additionalProperties': {'type': 'integer'}})
class SoundOffsetsField(serializers.JSONField):
    """``unreal_sound_offsets`` (sequence D18): checked by ``validate_sound_offsets``."""

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        try:
            return validate_sound_offsets(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class ProjectSerializer(serializers.ModelSerializer):
    """
    Serializer for the `Project` model.

    ``blender_version`` accepts a SupportedBlenderVersion PK on write
    and returns a nested object on read. When omitted on create, the
    default version is used (Blender projects).  ``renderer`` is chosen
    on create and cannot change afterwards; validation and create-time
    defaults are delegated to the renderer strategy.
    """
    blender_version = serializers.PrimaryKeyRelatedField(
        queryset=SupportedBlenderVersion.objects.all(),
        required=False,
        help_text="PK of a SupportedBlenderVersion. Defaults to the default version.",
    )
    # ``allow_null`` only marks the schema nullable (Unreal projects have
    # no Blender version); the runtime output is unchanged.
    blender_version_details = SupportedBlenderVersionSerializer(
        source='blender_version', read_only=True, allow_null=True,
    )
    unreal_cvar_overrides = CvarOverridesField(
        required=False,
        help_text=(
            "Unreal projects: console variable overrides (name -> value). "
            "Not pinned per job: edits also affect jobs that are still queued."
        ),
    )

    unreal_piece_seconds = serializers.IntegerField(
        required=False, min_value=PIECE_SECONDS_MIN, max_value=PIECE_SECONDS_MAX,
        help_text="Unreal sequences: seconds of footage per piece (5-600, default 30).",
    )
    unreal_lead_in_ms = serializers.IntegerField(
        required=False, min_value=0, max_value=LEAD_IN_MS_MAX,
        help_text="Unreal sequences: lead-in rendered and thrown away before a piece (0-10000 ms, default 1000).",
    )
    unreal_sound = serializers.BooleanField(
        required=False, help_text="Unreal sequences: sound on by default; each submission sets it.",
    )
    unreal_sound_offsets = SoundOffsetsField(
        required=False, allow_null=True,
        help_text=(
            'Unreal sequences: manual sound offsets {"<preset object path>@<num>/<den>": ms} (at most 64, '
            "-10000 to 10000), used only when a run's offset cannot be measured; null for none."
        ),
    )

    class Meta:
        model = Project
        fields = [
            'id', 'name', 'renderer', 'blender_version', 'blender_version_details',
            'engine_build', 'unreal_cvar_overrides', 'unreal_piece_seconds', 'unreal_lead_in_ms',
            'unreal_sound', 'unreal_sound_offsets', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']

    def validate(self, attrs):
        """Generic immutability checks, then the renderer strategy (FR-MR-11)."""
        check_immutable_and_consistent(self.instance, attrs)
        renderer = (
            self.instance.renderer if self.instance is not None
            else attrs.get('renderer', Renderer.BLENDER)
        )
        return resolve_strategy(renderer).validate_project(attrs, self.instance)

    def create(self, validated_data):
        strategy = resolve_strategy(validated_data.get('renderer', Renderer.BLENDER))
        return super().create(strategy.prepare_project_create(validated_data))

    def update(self, instance, validated_data):
        """Lock first, write only the sent fields, then the hook (FR-PM-10).

        ``Project.save`` adds ``unreal_overrides_sha256`` when the overrides
        are written.
        """
        strategy = resolve_strategy(instance.renderer)
        with strategy.locked_write(instance) as locked:
            target = locked or instance
            for field, value in validated_data.items():
                setattr(target, field, value)
            if validated_data:
                target.save(update_fields=sorted(validated_data))
            else:
                target.save()
            strategy.after_project_update(target, set(validated_data))
        return target
