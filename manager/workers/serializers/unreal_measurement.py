# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Serializers of the Unreal measured-peak gate (Unreal foundation FR-PM-4,
FR-PM-6, FR-PM-7).

* ``ProjectDetailSerializer`` -- ``ProjectSerializer`` plus the read-only
  ``unreal_measurement`` summary; used only by ``ProjectViewSet`` (nested
  ``project_details`` keep ``ProjectSerializer``).
* ``PilotResultRequestSerializer`` (with ``PilotGpuSerializer`` and
  ``PilotShotSerializer``) and ``PilotResultResponseSerializer`` -- the
  ``pilot-result/`` contract, used for validation and the OpenAPI schema.
* ``MeasurementRetryRequestSerializer`` -- the retry body.

The ``Strict*`` fields accept only real JSON types: an ``int`` that is not
a ``bool``, a finite number that is not a string, a real ``bool``.
"""

import re

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from shared.unreal.builds import BUILD_RE
from shared.unreal.vram_gate import MAX_PEAK_MB, MIN_PLAUSIBLE_PEAK_MB

from ..renderers import UnsupportedRendererError, get_strategy
from ..strict_json import _int_in, is_number
from .projects import ProjectSerializer

MAX_SECONDS = 259_200
MAX_FRAME = 2 ** 31 - 1
MAX_SUMMARY_BYTES = 32 * 1024
MAX_GPU_NAME = 128
_SHA256 = re.compile(r'[0-9a-f]{64}', re.ASCII)
# The heartbeat's GPU/CPU name charset (``heartbeat_validators._sanitize_cpu_name``).
_NAME_CHARSET = re.compile(r'[\w\s\-().@,/#+]*', re.ASCII)


class StrictIntegerField(serializers.IntegerField):
    """An ``int`` (never ``bool``, a string or a float) in ``[min_value, max_value]``."""

    def to_internal_value(self, data):
        if not _int_in(data, self.min_value, self.max_value):
            raise serializers.ValidationError(
                f"Must be an integer between {self.min_value} and {self.max_value}.")
        return data


class StrictNumberField(serializers.FloatField):
    """An ``int`` or a finite ``float`` (never ``bool`` or a string) in range."""

    def to_internal_value(self, data):
        if not is_number(data) or not self.min_value <= data <= self.max_value:
            raise serializers.ValidationError(
                f"Must be a finite number between {self.min_value} and {self.max_value}.")
        return data


class StrictCharField(serializers.CharField):
    """A JSON string only (DRF's ``CharField`` would turn ``5`` into ``"5"``)."""

    def __init__(self, **kwargs):
        kwargs.setdefault('trim_whitespace', False)
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        if not isinstance(data, str):
            raise serializers.ValidationError("Must be a string.")
        return super().to_internal_value(data)


class StrictBooleanField(serializers.BooleanField):
    """A JSON ``true`` or ``false`` only."""

    def to_internal_value(self, data):
        if not isinstance(data, bool):
            raise serializers.ValidationError("Must be a boolean.")
        return data


class PilotGpuSerializer(serializers.Serializer):
    index = StrictIntegerField(min_value=0, max_value=63)
    name = StrictCharField(allow_blank=True)
    total_mb = StrictIntegerField(min_value=1, max_value=MAX_PEAK_MB)

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
        """The bounds that depend on ``gpu.total_mb``."""
        total = attrs['gpu']['total_mb']
        errors = {}
        for field in ('peak_mb', 'card_delta_peak_mb', 'baseline_used_mb', 'baseline_drift_mb',
                      'csv_local_used_peak_mb'):
            value = attrs.get(field)
            if value is not None and value > total:
                errors[field] = [f"Must not exceed gpu.total_mb ({total})."]
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class PilotResultResponseSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=['DONE'])
    peak_mb = serializers.IntegerField()
    gate_need_mb = serializers.IntegerField()


class MeasurementRetryRequestSerializer(serializers.Serializer):
    """Body of ``POST /api/projects/{id}/unreal-measurement/retry/`` (all optional)."""

    job_id = StrictIntegerField(min_value=1, max_value=MAX_FRAME, required=False)
    remeasure = StrictBooleanField(required=False)


class MeasurementRetryResponseSerializer(serializers.Serializer):
    pilot_job_id = serializers.IntegerField()
    measurement_id = serializers.IntegerField()


class LatestMeasurementSerializer(serializers.Serializer):
    """Schema of ``unreal_measurement.latest`` (FR-PM-7)."""

    measurement_id = serializers.IntegerField()
    status = serializers.ChoiceField(choices=['measured', 'pending', 'failed'])
    reason = serializers.CharField(allow_null=True)
    remeasure = serializers.BooleanField()
    asset_id = serializers.IntegerField()
    engine_build = serializers.CharField()
    map = serializers.CharField()
    sequence = serializers.CharField()
    preset = serializers.CharField()
    peak_mb = serializers.IntegerField(allow_null=True)
    peak_source = serializers.CharField(allow_null=True)
    gate_need_mb = serializers.IntegerField(allow_null=True)
    measured_at = serializers.DateTimeField(allow_null=True)
    worker_hostname = serializers.CharField(allow_null=True)
    gpu_name = serializers.CharField(allow_null=True)
    gpu_total_mb = serializers.IntegerField(allow_null=True)
    prep_s = serializers.FloatField(allow_null=True)
    warmup_s = serializers.FloatField(allow_null=True)
    seconds_per_frame = serializers.FloatField(allow_null=True)
    pilot_job_id = serializers.IntegerField(allow_null=True)


class MeasurementSummarySerializer(serializers.Serializer):
    """Schema of a project's ``unreal_measurement`` (FR-PM-7)."""

    held_pending = serializers.IntegerField()
    held_failed = serializers.IntegerField()
    held_none = serializers.IntegerField()
    latest = LatestMeasurementSerializer(allow_null=True)


class ProjectDetailSerializer(ProjectSerializer):
    """``ProjectSerializer`` plus ``unreal_measurement`` (``ProjectViewSet`` only)."""

    unreal_measurement = serializers.SerializerMethodField(
        help_text=(
            "Unreal projects: QUEUED normal jobs held by measurement state and the "
            "latest measurement under the current CVars. Null for Blender projects."
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
