# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The VRAM gate per job kind (Unreal foundation FR-UE-21, D2; OQ-28 (d);
GPU memory reader D22, FR-GU-10).

The arithmetic is ``shared.unreal.vram_gate`` (shared with the manager's
routing note, so the two cannot drift). It checks **the job's card** only:
``job_card(reading)``, the card the card selection's D8 picks
(``card_order``: the most ``total_mb``; among equal sizes a card without a
screen (``GpuMem.screen``, from ``vram``) before one with a screen; then the
lowest card key; with the override, its one GPU), which is the card the
job's first launch renders on:

* **pilot** (the relaxed gate): ``pilot_need_mb(merged) <=
  floor(total * VRAM_BUDGET_FRACTION) - used``, and the reading must
  measure card memory (``measures_card``; the override does not);
* **normal job** (the measured-peak gate): ``fits_measured(peak,
  caps_sum_mb(merged), total, used)``, i.e. ``measured_need_mb(peak) + used
  <= floor(total * 0.93)`` and the caps sanity bound.

No reading (``None``) or no GPU in it fails. The result carries the
checked card's key (``card_key``, ``None`` with the override), the
FR-UE-21 log line and the summary values (the checked card, and
``vram_source``, the reading's source).
"""
from dataclasses import dataclass, field
from typing import Dict, Optional

from sethlans_worker_agent.unreal_executor import card_order
from shared.unreal import vram_gate


@dataclass(frozen=True)
class GateResult:
    ok: bool
    gate: str                     # 'pilot' | 'measured'
    message: str = ''
    need_mb: Optional[int] = None
    summary: Dict[str, object] = field(default_factory=dict)
    reading: object = None
    card_key: Optional[str] = None  # the checked card (FR-GU-10); the job's first launch renders on it


def job_card(reading):
    """D8's card of the reading (``card_order``, as ``adapter_choice.pick``): size, then no screen, then key."""
    return min(reading.gpus, key=lambda gpu: card_order.sort_key(gpu.total_mb, gpu.screen, gpu.card_key))


def _need(params) -> int:
    merged = params.merged_dict
    if params.is_pilot:
        return vram_gate.pilot_need_mb(merged)
    return vram_gate.measured_need_mb(params.measured_peak_mb)


def _summary(params, gate: str, need: int, gpu, source: str) -> Dict[str, object]:
    values = {'vram_gate': gate, 'vram_source': source, 'vram_mb': gpu.total_mb, 'vram_used_mb': gpu.used_mb,
              'vram_need_mb': need, 'vram_usable_mb': vram_gate.usable_mb(gpu.total_mb, gpu.used_mb)}
    if not params.is_pilot:
        values['vram_measured_peak_mb'] = params.measured_peak_mb
    return values


def evaluate(params, reading) -> GateResult:
    gate = 'pilot' if params.is_pilot else 'measured'
    if reading is None or not reading.gpus:
        return GateResult(False, gate, "no usable GPU memory reading.")
    source = reading.source
    if params.is_pilot and not getattr(reading, 'measures_card', False):
        return GateResult(False, gate, f"a pilot needs a live GPU memory reading (source {source}).")
    try:
        need = _need(params)
        caps = vram_gate.caps_sum_mb(params.merged_dict)
    except ValueError as exc:
        return GateResult(False, gate, f"invalid pool CVar ({exc}).")
    gpu = job_card(reading)
    summary, key = _summary(params, gate, need, gpu, source), gpu.card_key
    usable = vram_gate.usable_mb(gpu.total_mb, gpu.used_mb)
    if need > usable:
        return GateResult(False, gate, f"needs {need} MB; {usable} MB usable on a {gpu.total_mb} MB GPU "
                                       f"({gpu.used_mb} MB in use).", need, summary, card_key=key)
    if not params.is_pilot and not vram_gate.caps_within_budget(caps, gpu.total_mb):
        limit = vram_gate.usable_mb(gpu.total_mb, 0)  # floor(total * fraction), shared arithmetic
        return GateResult(False, gate, f"pool caps {caps} MB exceed the {limit} MB budget of a "
                                       f"{gpu.total_mb} MB GPU.", need, summary, card_key=key)
    return GateResult(True, gate, '', need, summary, reading, key)
