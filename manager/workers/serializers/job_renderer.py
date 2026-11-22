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

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from ..constants import UnrealKind
from ..renderers import UnsupportedRendererError, get_strategy
from ..renderers.unreal_routing import _is_worker_request
from .machine_results import MachineResultSerializer, machine_results
from .machine_rows import MachineRowSerializer, expect_page_keys, job_machines
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
    """Adds ``routing_note``, ``unreal_machine_results``, ``unreal_machines`` and renderer-dispatched validate/create."""

    unreal_kind = serializers.ChoiceField(
        choices=UnrealKind.choices, read_only=True,
        help_text=(
            "'render' for a normal Unreal job; manager-created jobs are 'pilot' (a memory "
            "measurement), 'shots' (shot discovery) or 'audio' (a sound render)."
        ),
    )
    sequence_submission = serializers.PrimaryKeyRelatedField(
        read_only=True,
        help_text="Unreal sequence pieces and audio jobs: their submission (a piece may span frames).",
    )
    unreal_lead_in_frames = serializers.IntegerField(
        read_only=True,
        help_text="Unreal pieces: frames rendered before start_frame and thrown away (sequence FR-SP-4).",
    )
    unreal_machine_results = serializers.SerializerMethodField(
        help_text=(
            "Unreal render and audio jobs, for worker-token requests: the caller's own memory "
            "results that fit, one per card kind (per-machine memory test FR-MT-T-3); [] when "
            "it has none. Null for other callers and for Blender, pilot and shots jobs."
        ),
    )

    unreal_machines = serializers.SerializerMethodField(
        help_text=(
            "Unreal render and audio jobs, on GET /api/jobs/{id}/ only: every machine and card "
            "kind of the job's key with its memory test state (per-machine memory test "
            "FR-MT-U-1). Null in lists, for worker-token requests and for other jobs."
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
        request = self.context.get('request')
        if strategy.supports('pilot_measurement') and (request is None or not _is_worker_request(request)):
            # FR-MT-U-2, NF-3: the page's keys are read in one query.
            expect_page_keys(self, obj)
        return strategy.routing_note(obj, self.context)

    @extend_schema_field(MachineRowSerializer(many=True, allow_null=True))
    def get_unreal_machines(self, obj):
        """The machine list of the job's key (detail only, never for workers)."""
        return job_machines(self, obj)

    @extend_schema_field(MachineResultSerializer(many=True, allow_null=True))
    def get_unreal_machine_results(self, obj):
        """The caller's fits results of the job's key (one query per response)."""
        request = self.context.get('request')
        if request is None or not _is_worker_request(request):
            return None
        return machine_results(self, obj, request.user.worker_profile)

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
