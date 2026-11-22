# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Body and answer of ``POST /api/jobs/{id}/memory-report/`` (Unreal
per-machine memory test D3, D6, D9, FR-MT-S-3).

Strict JSON, every key required (``null`` where allowed):

* ``measurement_id`` -- the result the job's gate used (positive int);
* ``card_kind`` -- the D1 kind of the card the job ran on;
* ``card_key`` -- that card's key ``pci:dddd:bb:dd.f``;
* ``driver_version`` -- null or ``[0-9A-Za-z.\\-]{1,40}``; UE's ``Unknown`` is null;
* ``spilled`` -- true, false or null (no reading, Linux);
* ``spill_cause`` -- ``self`` or ``contention``; null exactly when ``spilled`` is not true;
* ``shared_peak_mb`` -- null or ``0..MAX_PEAK_MB``;
* ``job_peak_mb`` -- null or ``1..MAX_PEAK_MB``.

The answer is ``{"result": <pair state>}`` with D2's state names
(``unreal_machine.PAIR_STATES``, ``MachineStateEnum``; ``SpillCauseEnum``
for the cause, both pinned in ``drf_config``).
"""

from rest_framework import serializers

from shared.unreal.vram_gate import MAX_PEAK_MB

from ..card_keys import is_pci_card_key
from ..renderers.unreal_machine import PAIR_STATES
from .machine_report_fields import CardKindField, DriverVersionField
from .strict_fields import StrictBooleanField, StrictCharField, StrictIntegerField

SPILL_SELF, SPILL_CONTENTION = 'self', 'contention'
SPILL_CAUSES = (SPILL_SELF, SPILL_CONTENTION)
MAX_RECORD_ID = 2 ** 63 - 1  # a BigAutoField primary key


class MemoryReportRequestSerializer(serializers.Serializer):
    """Body of ``POST /api/jobs/{id}/memory-report/`` (job-independent rules only)."""

    measurement_id = StrictIntegerField(
        min_value=1, max_value=MAX_RECORD_ID, help_text="The memory result the job's gate used.")
    card_kind = CardKindField()
    card_key = StrictCharField(max_length=20, help_text="The key of the card the job ran on, pci:dddd:bb:dd.f.")
    driver_version = DriverVersionField()
    spilled = StrictBooleanField(
        allow_null=True, help_text="The job spilled into shared memory (D9); null when not measurable.")
    spill_cause = serializers.ChoiceField(
        choices=SPILL_CAUSES, allow_null=True, help_text="self or contention; null unless spilled is true.")
    shared_peak_mb = StrictIntegerField(
        min_value=0, max_value=MAX_PEAK_MB, allow_null=True,
        help_text="Largest shared GPU memory of the job's processes on its card.")
    job_peak_mb = StrictIntegerField(
        min_value=1, max_value=MAX_PEAK_MB, allow_null=True, help_text="The job's own peak GPU memory.")

    def validate_card_key(self, value):
        if not is_pci_card_key(value):
            raise serializers.ValidationError("Must be a card key pci:dddd:bb:dd.f.")
        return value

    def validate(self, attrs):
        if (attrs['spilled'] is True) != (attrs['spill_cause'] is not None):
            raise serializers.ValidationError(
                {'spill_cause': ["Must be set exactly when spilled is true."]})
        return attrs


class MemoryReportResponseSerializer(serializers.Serializer):
    result = serializers.ChoiceField(
        choices=PAIR_STATES, help_text="The state of the pair (machine, card kind) for the job's key.")
