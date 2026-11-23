# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Serializers of the Unreal measured-peak gate (Unreal foundation FR-PM-4,
FR-PM-6, FR-PM-7).

* ``ProjectDetailSerializer`` -- ``ProjectSerializer`` plus the read-only
  ``unreal_measurement`` summary (held counts and, per key, the machine
  list of per-machine memory test FR-MT-U-1); used only by
  ``ProjectViewSet`` (nested ``project_details`` keep ``ProjectSerializer``).
* ``PilotResultRequestSerializer`` (with ``PilotGpuSerializer`` and
  ``PilotShotSerializer``) and ``PilotResultResponseSerializer`` -- the
  ``pilot-result/`` contract, used for validation and the OpenAPI schema.
* ``MeasurementRetryRequestSerializer`` -- the retry body.

The ``Strict*`` fields (``strict_fields.py``, re-exported here) accept only
real JSON types; the ``ram`` object is ``pilot_ram.UnrealRamSerializer``.
"""

import re

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from shared.unreal.builds import BUILD_RE
from shared.unreal.vram_gate import MAX_PEAK_MB, MIN_PLAUSIBLE_PEAK_MB

from ..card_keys import is_pci_card_key
from ..constants import UNREAL_GAME_OVERRIDES, UNREAL_TEXTURE_STREAMING
from ..renderers import UnsupportedRendererError, get_strategy
from ..renderers.unreal_machine import DOES_NOT_FIT, FITS
from .machine_report_fields import CardKindField, DriverVersionField
from .machine_rows import MachineRowSerializer
from .pilot_ram import UnrealRamSerializer, largest_commit_mb, ram_megabytes
from .projects import ProjectSerializer
from .strict_fields import StrictBooleanField, StrictCharField, StrictIntegerField, StrictNumberField

MAX_SECONDS = 259_200
MAX_FRAME = 2 ** 31 - 1
MAX_WORKER_ID = 2 ** 31 - 1  # a worker primary key (signed 32-bit)
MAX_SUMMARY_BYTES = 32 * 1024
MAX_GPU_NAME = 128
# D6 verdicts a pilot reports (``MeasurementVerdictEnum`` in the schema, drf_config).
PILOT_VERDICTS = (FITS, DOES_NOT_FIT)
_SHA256 = re.compile(r'[0-9a-f]{64}', re.ASCII)
# The heartbeat's GPU/CPU name charset (``heartbeat_validators._sanitize_cpu_name``).
_NAME_CHARSET = re.compile(r'[\w\s\-().@,/#+]*', re.ASCII)


class PilotGpuSerializer(serializers.Serializer):
    index = StrictIntegerField(min_value=0, max_value=63)
    name = StrictCharField(allow_blank=True)
    total_mb = StrictIntegerField(min_value=1, max_value=MAX_PEAK_MB)
    card_key = StrictCharField(
        max_length=20,
        help_text="The card's key, pci:dddd:bb:dd.f (gpu-memory-reader FR-GH-10).",
    )

    def validate_card_key(self, value):
        # The heartbeat's per-GPU rule (FR-GH-3); a pilot always ran on a card with a key.
        if not is_pci_card_key(value):
            raise serializers.ValidationError("Must be a card key pci:dddd:bb:dd.f.")
        return value

    def validate_name(self, value):
        # Sanitized like the heartbeat's GPU names (FR-RT-16), never refused.
        if len(value) > MAX_GPU_NAME or not _NAME_CHARSET.fullmatch(value):
            return ''
        return value


class PilotShotSerializer(serializers.Serializer):
    frame = StrictIntegerField(min_value=0, max_value=MAX_FRAME)
    render_s = StrictNumberField(min_value=0, max_value=MAX_SECONDS)


def _seconds(**kwargs):
    return StrictNumberField(min_value=0, max_value=MAX_SECONDS, **kwargs)


def _megabytes(**kwargs):
    return StrictIntegerField(min_value=0, max_value=MAX_PEAK_MB, **kwargs)


def _total_bound_errors(attrs, errors):
    """The card figures that cannot exceed ``gpu.total_mb``.

    The card delta and the baseline are dedicated-memory readings of the card,
    so never above its total.  The CSV LocalUsed peak (and so ``peak_mb``) of
    an over-full render can be (part of it already in shared memory): for
    ``does_not_fit`` those two are bounded by ``MAX_PEAK_MB`` only (D6, #290).
    """
    total = attrs['gpu']['total_mb']
    fields = ['card_delta_peak_mb', 'baseline_used_mb', 'baseline_drift_mb']
    if attrs['verdict'] != DOES_NOT_FIT:
        fields += ['peak_mb', 'csv_local_used_peak_mb']
    for field in fields:
        value = attrs.get(field)
        if value is not None and value > total:
            errors[field] = [f"Must not exceed gpu.total_mb ({total})."]


def _lower_bound_errors(attrs, errors):
    """D6: ``lower_bound_mb`` is ``peak_mb`` exactly for ``does_not_fit`` and null for ``fits``."""
    bound = attrs['lower_bound_mb']
    if attrs['verdict'] == DOES_NOT_FIT and bound != attrs['peak_mb']:
        errors['lower_bound_mb'] = ["Must equal peak_mb for does_not_fit."]
    elif attrs['verdict'] == FITS and bound is not None:
        errors['lower_bound_mb'] = ["Must be null for fits."]


class PilotResultRequestSerializer(serializers.Serializer):
    """Body of ``POST /api/jobs/{id}/pilot-result/`` (job-independent rules only)."""

    peak_mb = StrictIntegerField(min_value=MIN_PLAUSIBLE_PEAK_MB, max_value=MAX_PEAK_MB)
    peak_source = serializers.ChoiceField(choices=['card', 'csv'])
    card_delta_peak_mb = _megabytes()
    baseline_used_mb = _megabytes()
    baseline_drift_mb = _megabytes()
    peak_phase = serializers.ChoiceField(choices=['prep', 'warmup', 'render'])
    max_sample_gap_s = StrictNumberField(min_value=0, max_value=10)
    prep_s = _seconds()
    warmup_s = _seconds(allow_null=True)
    warmup_ran = StrictBooleanField()
    render_s = _seconds()
    frames = StrictIntegerField(min_value=1, max_value=256)
    seconds_per_frame = _seconds()
    per_shot = PilotShotSerializer(many=True, max_length=256, allow_empty=True)
    gpu = PilotGpuSerializer()
    engine_build = StrictCharField()
    csv_local_used_peak_mb = _megabytes(allow_null=True)
    overrides_sha256 = StrictCharField()
    summary = StrictCharField(allow_blank=True)
    # What the pilot ran with, from its preset (gpu-memory-reader FR-GC-8); stored, never keyed.
    game_overrides = serializers.ChoiceField(choices=UNREAL_GAME_OVERRIDES)
    texture_streaming = serializers.ChoiceField(choices=UNREAL_TEXTURE_STREAMING)
    # RAM figures (per-card spec FR-PC-RAM-3, FR-PC-MGR-1): required, null when unknown.
    ram = UnrealRamSerializer(allow_null=True)
    ram_process_commit_peak_mb = ram_megabytes(allow_null=True)
    shared_machine = StrictBooleanField(
        help_text="Another job held the worker at some moment of the pilot (D10).",
    )
    # Per-machine memory test FR-MT-V-3: the card kind it ran on, the driver and D6's verdict.
    card_kind = CardKindField()
    driver_version = DriverVersionField()
    verdict = serializers.ChoiceField(choices=PILOT_VERDICTS)
    shared_peak_mb = _megabytes(
        allow_null=True, help_text="Largest shared GPU memory of the pilot's processes on its card (D9).")
    lower_bound_mb = StrictIntegerField(
        min_value=MIN_PLAUSIBLE_PEAK_MB, max_value=MAX_PEAK_MB, allow_null=True,
        help_text="peak_mb for does_not_fit (a lower bound of the need), null for fits (D6).")

    def validate_engine_build(self, value):
        if not re.fullmatch(BUILD_RE, value, re.ASCII):
            raise serializers.ValidationError("Not a valid Unreal Engine build.")
        return value

    def validate_overrides_sha256(self, value):
        if not _SHA256.fullmatch(value):
            raise serializers.ValidationError("Must be 64 lowercase hex characters.")
        return value

    def validate_summary(self, value):
        if len(value.encode('utf-8')) > MAX_SUMMARY_BYTES:
            raise serializers.ValidationError("At most 32 KiB.")
        return value

    def validate(self, attrs):
        """The bounds that depend on ``gpu.total_mb``, then the RAM process peak."""
        errors = {}
        _total_bound_errors(attrs, errors)
        # RAM figures are not GPU memory: never bounded by gpu.total_mb (FR-PC-MGR-1).
        process, largest = attrs['ram_process_commit_peak_mb'], largest_commit_mb(attrs['ram'])
        if process is not None and largest is not None and process > largest:
            errors['ram_process_commit_peak_mb'] = [f"Must not exceed the largest ram commit_mb ({largest})."]
        _lower_bound_errors(attrs, errors)
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class PilotResultResponseSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=['DONE'])
    peak_mb = serializers.IntegerField()


class MeasurementRetryRequestSerializer(serializers.Serializer):
    """Body of ``POST /api/projects/{id}/unreal-measurement/retry/`` (all optional)."""

    job_id = StrictIntegerField(min_value=1, max_value=MAX_FRAME, required=False)
    remeasure = StrictBooleanField(required=False)
    worker = StrictIntegerField(min_value=1, max_value=MAX_WORKER_ID, required=False,
                                help_text="Test only this worker's card kinds (per-machine memory test FR-MT-P-4).")


class MeasurementRetryResponseSerializer(serializers.Serializer):
    pilot_job_id = serializers.IntegerField()
    measurement_id = serializers.IntegerField()


class MeasurementKeySerializer(serializers.Serializer):
    """Schema of one ``unreal_measurement.keys`` entry (per-machine memory test FR-MT-U-1)."""

    asset_id = serializers.IntegerField()
    target_sha256 = serializers.CharField()
    engine_build = serializers.CharField()
    map = serializers.CharField()
    sequence = serializers.CharField()
    preset = serializers.CharField()
    machines = MachineRowSerializer(many=True)


class MeasurementSummarySerializer(serializers.Serializer):
    """Schema of a project's ``unreal_measurement`` (FR-PM-7, FR-MT-U-1)."""

    held_pending = serializers.IntegerField()
    held_failed = serializers.IntegerField()
    held_none = serializers.IntegerField()
    keys = MeasurementKeySerializer(
        many=True, allow_null=True,
        help_text=("The measurement keys under the current CVars (active jobs' keys first, at most 20), "
                   "each with its machine list. Null in the project list and for worker tokens."),
    )


class ProjectDetailSerializer(ProjectSerializer):
    """``ProjectSerializer`` plus ``unreal_measurement`` (``ProjectViewSet`` only)."""

    unreal_measurement = serializers.SerializerMethodField(
        help_text=(
            "Unreal projects: QUEUED normal jobs held by measurement state and each "
            "measurement key's machines under the current CVars. Null for Blender projects."
        ),
    )

    class Meta(ProjectSerializer.Meta):
        fields = ProjectSerializer.Meta.fields + ['unreal_measurement']

    @extend_schema_field(MeasurementSummarySerializer(allow_null=True))
    def get_unreal_measurement(self, obj):
        try:
            strategy = get_strategy(obj.renderer)
        except UnsupportedRendererError:
            return None
        return strategy.project_measurement(obj, self.context)
