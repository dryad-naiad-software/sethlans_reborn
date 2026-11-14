# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The VRAM gate per job kind (Unreal foundation FR-UE-21, D2; OQ-28 (d)).

The arithmetic is ``shared.unreal.vram_gate`` (shared with the manager's
routing note, so the two cannot drift). For **every** GPU in the reading:

* **pilot** (the relaxed gate): ``pilot_need_mb(merged) <=
  floor(total * VRAM_BUDGET_FRACTION) - used``, and the reading's source
  must be ``nvidia-smi`` (the pilot measures card memory);
* **normal job** (the measured-peak gate): ``fits_measured(peak,
  caps_sum_mb(merged), total, used)``, i.e. ``measured_need_mb(peak) + used
  <= floor(total * 0.93)`` and the caps sanity bound.

No reading (``None``) or no GPU in it fails. The result carries the
FR-UE-21 log line and the summary values (the smallest GPU).
"""
from dataclasses import dataclass, field
from typing import Dict, Optional

from shared.unreal import vram_gate

SOURCE_NVIDIA = 'nvidia-smi'


@dataclass(frozen=True)
class GateResult:
    ok: bool
    gate: str                     # 'pilot' | 'measured'
    message: str = ''
    need_mb: Optional[int] = None
    summary: Dict[str, object] = field(default_factory=dict)
    reading: object = None


def _need(params) -> int:
    merged = params.merged_dict
    if params.is_pilot:
        return vram_gate.pilot_need_mb(merged)
    return vram_gate.measured_need_mb(params.measured_peak_mb)


def _summary(params, gate: str, need: int, gpu) -> Dict[str, object]:
    values = {'vram_gate': gate, 'vram_mb': gpu.total_mb, 'vram_used_mb': gpu.used_mb, 'vram_need_mb': need,
              'vram_usable_mb': vram_gate.usable_mb(gpu.total_mb, gpu.used_mb)}
    if not params.is_pilot:
        values['vram_measured_peak_mb'] = params.measured_peak_mb
    return values


def evaluate(params, reading) -> GateResult:
    gate = 'pilot' if params.is_pilot else 'measured'
    if reading is None or not reading.gpus:
        return GateResult(False, gate, "no usable GPU memory reading.")
    if params.is_pilot and reading.source != SOURCE_NVIDIA:
        return GateResult(False, gate, "a pilot needs an nvidia-smi GPU memory reading.")
    try:
        need = _need(params)
        caps = vram_gate.caps_sum_mb(params.merged_dict)
    except ValueError as exc:
        return GateResult(False, gate, f"invalid pool CVar ({exc}).")
    smallest = min(reading.gpus, key=lambda g: g.total_mb)
    for gpu in reading.gpus:
        usable = vram_gate.usable_mb(gpu.total_mb, gpu.used_mb)
        if need > usable:
            return GateResult(False, gate, f"needs {need} MB; {usable} MB usable on a {gpu.total_mb} MB GPU "
                                           f"({gpu.used_mb} MB in use).", need, _summary(params, gate, need, gpu))
        if not params.is_pilot and not vram_gate.caps_within_budget(caps, gpu.total_mb):
            limit = vram_gate.usable_mb(gpu.total_mb, 0)  # floor(total * fraction), shared arithmetic
            return GateResult(False, gate, f"pool caps {caps} MB exceed the {limit} MB budget of a "
                                           f"{gpu.total_mb} MB GPU.", need, _summary(params, gate, need, gpu))
    return GateResult(True, gate, '', need, _summary(params, gate, need, smallest), reading)
