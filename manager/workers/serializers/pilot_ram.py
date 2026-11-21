# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The pilot's RAM figures (per-card spec FR-PC-RAM-3, FR-PC-MGR-1).

``UnrealRamSerializer`` is the ``ram`` object of the ``pilot-result/``
body and, for the OpenAPI schema, of a job's ``unreal_measured_ram`` and
``unreal_measurement.latest.ram``::

    {"prep":   {"ws_mb": n, "commit_mb": n},
     "warmup": {"ws_mb": n, "commit_mb": n, "cold": bool} | null,
     "render": {"ws_mb": n, "commit_mb": n}}

Every object takes exactly its keys (an extra key is a 400 on that key);
every figure is a strict integer ``1..MAX_PEAK_MB``.
"""

from rest_framework import serializers

from shared.unreal.vram_gate import MAX_PEAK_MB

from .strict_fields import StrictBooleanField, StrictIntegerField

UNKNOWN_KEY = "Unknown key."


def ram_megabytes(**kwargs):
    """A RAM figure in MB: a strict integer ``1..MAX_PEAK_MB``."""
    return StrictIntegerField(min_value=1, max_value=MAX_PEAK_MB, **kwargs)


class _ExactKeysMixin:
    """Refuse keys outside the declared fields (DRF would drop them silently)."""

    def to_internal_value(self, data):
        if isinstance(data, dict):
            extra = sorted(key for key in data if key not in self.fields)
            if extra:
                raise serializers.ValidationError({str(key): [UNKNOWN_KEY] for key in extra})
        return super().to_internal_value(data)


class UnrealRamPhaseSerializer(_ExactKeysMixin, serializers.Serializer):
    """One phase's peak private working set and commit (D8)."""

    ws_mb = ram_megabytes(help_text="Peak summed private working set of the job's processes (MB).")
    commit_mb = ram_megabytes(help_text="Peak commit of the launch's Job Object (MB).")


class UnrealRamWarmupSerializer(UnrealRamPhaseSerializer):
    """The warm-up phase, and whether it ran cold (no marker in its slot)."""

    cold = StrictBooleanField()


class UnrealRamSerializer(_ExactKeysMixin, serializers.Serializer):
    """A pilot's RAM figures per phase; ``warmup`` is null when no warm-up ran."""

    prep = UnrealRamPhaseSerializer()
    warmup = UnrealRamWarmupSerializer(allow_null=True)
    render = UnrealRamPhaseSerializer()


def largest_commit_mb(ram):
    """The largest ``commit_mb`` of a validated ``ram`` object (``None`` when ``ram`` is null)."""
    if ram is None:
        return None
    return max(phase['commit_mb'] for phase in ram.values() if phase is not None)
