# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The VRAM gate per job kind (Unreal foundation FR-UE-21, D2; OQ-28 (d);
GPU memory reader D22, FR-GU-10).

The arithmetic is ``shared.unreal.vram_gate`` (shared with the manager's
routing note, so the two cannot drift). It checks one card at a time, the
**free** cards in D8 order (render per card D4, FR-PC-SEL-1): the reading's
cards whose PCI address (``cards.identity.key_address``, integer tuples)
equals that of a key in ``free_keys``, sorted by ``card_order.sort_key``
(the most ``total_mb``; among equal sizes a card without a screen before
one with a screen; then the lowest card key). The first candidate on which
the job's gate passes is the job's card; its ``card_key`` is the matching
``free_keys`` entry (the inventory's key, the spelling the hold takes). A
reading card without a readable address is never a candidate. No free key
fails with ``NO_FREE_CARD``; free keys that no reading card matches (no
keyed reading of them) with ``NO_FREE_READING``; candidates that all fail
name the first one's figures. ``free_keys=None`` (no card keys to check against) makes every
reading card a candidate, as does the override reading (one keyless GPU,
D14: ``reserve_card`` then picks the card). The gate per kind:

* **pilot** (the relaxed gate): ``pilot_need_mb(merged) <=
  floor(total * VRAM_BUDGET_FRACTION) - used``, and the reading must
  measure card memory (``measures_card``; the override does not);
* **normal job** (the measured-peak gate): ``fits_measured(peak,
  caps_sum_mb(merged), total, used)``, i.e. ``measured_need_mb(peak) + used
  <= floor(total * 0.93)`` and the caps sanity bound.

* **audio** (sequence rendering D3, FR-SR-5): the pilot's need rule on the
  job's card, without ``measures_card`` (the override's reading is enough)
  and without the caps bound (gate ``audio``);
* **shots** (sequence rendering D3, FR-SD-3): never gated; the prep
  commandlet uses no GPU, so no reading is needed (gate ``skipped``, no
  card key). The reservation holds the first free Unreal card (``reserve_card``).

No reading (``None``) or no GPU in it fails. The result carries the
chosen card's key (``card_key``, ``None`` with the override), the FR-UE-21
log line and the summary values (the chosen card, or on a failure the first
candidate, and ``vram_source``, the reading's source).
"""
from dataclasses import dataclass, field, replace
from typing import Dict, Optional, Sequence

from sethlans_worker_agent.cards.identity import key_address
from sethlans_worker_agent.unreal_executor import card_order
from sethlans_worker_agent.unreal_executor.vram import SOURCE_OVERRIDE
from shared.unreal import vram_gate

NO_FREE_CARD = "no free Unreal card."
NO_FREE_READING = "no memory reading for the free Unreal card."


@dataclass(frozen=True)
class GateResult:
    ok: bool
    gate: str                     # 'pilot' | 'audio' | 'measured' | 'skipped'
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
    if params.is_pilot or params.is_audio:
        return vram_gate.pilot_need_mb(merged)
    return vram_gate.measured_need_mb(params.measured_peak_mb)


def _summary(params, gate: str, need: int, gpu, source: str) -> Dict[str, object]:
    values = {'vram_gate': gate, 'vram_source': source, 'vram_mb': gpu.total_mb, 'vram_used_mb': gpu.used_mb,
              'vram_need_mb': need, 'vram_usable_mb': vram_gate.usable_mb(gpu.total_mb, gpu.used_mb)}
    if gate == 'measured':
        values['vram_measured_peak_mb'] = params.measured_peak_mb
    return values


def _candidates(reading, free_keys: Optional[Sequence[str]]) -> list:
    """``(gpu, card key)`` pairs in D8 order: the free cards, by address (D4); every card without ``free_keys``."""
    ordered = sorted(reading.gpus, key=lambda gpu: card_order.sort_key(gpu.total_mb, gpu.screen, gpu.card_key))
    if free_keys is None or reading.source == SOURCE_OVERRIDE:  # the override's one GPU has no key (D14)
        return [(gpu, gpu.card_key) for gpu in ordered]
    free = {}
    for key in free_keys:
        address = key_address(key)
        if address is not None:
            free.setdefault(address, key)
    return [(gpu, free[key_address(gpu.card_key)]) for gpu in ordered if key_address(gpu.card_key) in free]


def _check_card(params, gate: str, need: int, caps: int, gpu, key, source: str) -> GateResult:
    summary = _summary(params, gate, need, gpu, source)
    usable = vram_gate.usable_mb(gpu.total_mb, gpu.used_mb)
    if need > usable:
        return GateResult(False, gate, f"needs {need} MB; {usable} MB usable on a {gpu.total_mb} MB GPU "
                                       f"({gpu.used_mb} MB in use).", need, summary, card_key=key)
    if gate == 'measured' and not vram_gate.caps_within_budget(caps, gpu.total_mb):
        limit = vram_gate.usable_mb(gpu.total_mb, 0)  # floor(total * fraction), shared arithmetic
        return GateResult(False, gate, f"pool caps {caps} MB exceed the {limit} MB budget of a "
                                       f"{gpu.total_mb} MB GPU.", need, summary, card_key=key)
    return GateResult(True, gate, '', need, summary, card_key=key)


def evaluate(params, reading, free_keys: Optional[Sequence[str]] = None) -> GateResult:
    """The first free card (D8 order) on which the job's gate passes; ``free_keys=None``: every card is free."""
    if params.is_shots:  # D3: no memory gate and no card reading
        return GateResult(True, 'skipped', summary={'vram_gate': 'skipped'})
    gate = 'pilot' if params.is_pilot else 'audio' if params.is_audio else 'measured'
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
    first = None
    for gpu, key in _candidates(reading, free_keys):
        result = _check_card(params, gate, need, caps, gpu, key, source)
        if result.ok:
            return replace(result, reading=reading)
        first = first or result
    if first is not None:
        return first
    return GateResult(False, gate, NO_FREE_READING if free_keys else NO_FREE_CARD, need)
