# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Renderer fields and validation for the Job serializers (spec FR-MU-14).

``JobRendererFieldsMixin`` subclasses ``serializers.Serializer`` on
purpose: DRF's ``SerializerMetaclass`` collects declared fields only from
bases that have ``_declared_fields``, so a plain mixin's
``routing_note`` would be silently dropped.
"""

from rest_framework import serializers

from ..constants import UnrealKind
from ..renderers import UnsupportedRendererError, get_strategy
from .renderer_checks import check_immutable_and_consistent, resolve_strategy

_FEATURE_LABELS = {'tiled_job': 'Tiled jobs', 'animation': 'Animations'}


def ensure_feature_supported(asset, feature):
    """Reject a TiledJob/Animation whose asset's renderer lacks *feature*.

    Runs before the Pillow-format checks so the renderer-specific message
    wins (FR-MR-4).  ``asset`` may be ``None`` (nothing to check).

    Raises:
        rest_framework.exceptions.ValidationError: HTTP 400.
    """
    if asset is None:
        return
    strategy = resolve_strategy(asset.project.renderer)
    if not strategy.supports(feature):
        raise serializers.ValidationError(
            f"{_FEATURE_LABELS[feature]} are not supported for "
            f"{strategy.label} projects yet."
        )


class JobRendererFieldsMixin(serializers.Serializer):
    """Adds ``routing_note``, the measured-peak fields and renderer-dispatched validate/create."""

    unreal_kind = serializers.ChoiceField(
        choices=UnrealKind.choices, read_only=True,
        help_text="'pilot' for a manager-created Unreal measurement render, else 'render'.",
    )
    unreal_measured_peak_mb = serializers.SerializerMethodField(
        help_text=(
            "Unreal normal jobs: the measured peak GPU memory (MB) of the job's "
            "measurement key, or null while unmeasured. Null for Blender jobs and pilots."
        ),
    )

    routing_note = serializers.SerializerMethodField(
        help_text=(
            "Why a queued job is not being picked up by any worker, or "
            "null. Always null for worker-authenticated requests."
        ),
    )

    def get_routing_note(self, obj) -> str | None:
        """Delegate to the job's renderer strategy (FR-RT-9)."""
        try:
            strategy = get_strategy(obj.renderer)
        except UnsupportedRendererError:
            return None
        return strategy.routing_note(obj, self.context)

    def get_unreal_measured_peak_mb(self, obj) -> int | None:
        """The list annotation, else one query (single-object responses; FR-PM-5)."""
        if obj.unreal_kind != UnrealKind.RENDER or not obj.unreal_target_sha256:
            return None
        if hasattr(obj, '_unreal_peak_mb'):
            return obj._unreal_peak_mb
        # Imported here: the service imports the renderer registry.
        from ..services.unreal_pilot import cached_key_state
        return cached_key_state(obj).peak_mb

    def validate_for_renderer(self, data):
        """Generic checks, then the strategy of the job's renderer.

        On update the strategy comes from ``instance.renderer``, never
        from the request (FR-MU-12).
        """
        check_immutable_and_consistent(self.instance, data)
        asset = data.get('asset') or self.instance.asset
        renderer = (
            self.instance.renderer if self.instance is not None
            else asset.project.renderer
        )
        return resolve_strategy(renderer).validate_job(data, asset, self.instance)

    def create(self, validated_data):
        """Insert the job with the project locked first, then the hook (FR-PM-10)."""
        asset = validated_data['asset']
        strategy = resolve_strategy(asset.project.renderer)
        with strategy.locked_write(asset.project):
            job = super().create(strategy.prepare_job_create(validated_data, asset))
            strategy.after_job_create(job)
        return job
