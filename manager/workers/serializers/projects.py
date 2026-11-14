# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Serializer for the Project model.
"""

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from ..constants import Renderer
from ..models import Project, SupportedBlenderVersion
from .blender_versions import SupportedBlenderVersionSerializer
from .renderer_checks import check_immutable_and_consistent, resolve_strategy


@extend_schema_field({'type': 'object', 'additionalProperties': {'type': 'string'}})
class CvarOverridesField(serializers.JSONField):
    """``unreal_cvar_overrides``: a CVar-name to value map (OpenAPI type)."""


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

    class Meta:
        model = Project
        fields = [
            'id', 'name', 'renderer', 'blender_version', 'blender_version_details',
            'engine_build', 'unreal_cvar_overrides', 'created_at',
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
