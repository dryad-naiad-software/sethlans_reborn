# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The VRAM gate per job kind (Unreal per-machine memory test D7, D10,
FR-MT-G-2, FR-MT-V-2; GPU memory reader D22, FR-GU-10).

The arithmetic is ``shared.unreal.vram_gate`` (shared with the manager, so
the two cannot drift). It checks one card at a time, the **free** cards in
D8 order (render per card D4, FR-PC-SEL-1): the reading's cards whose PCI
address (``cards.identity.key_address``, integer tuples) equals that of a
key in ``free_keys``, sorted by ``card_order.sort_key`` (the most
``total_mb``; among equal sizes a card without a screen before one with a
screen; then the lowest card key). The first candidate on which the job's
gate passes is the job's card; its ``card_key`` is the matching
``free_keys`` entry (the inventory's key, the spelling the hold takes). A
reading card without a readable address is never a candidate. No free key
fails with ``NO_FREE_CARD``; free keys that no reading card matches (no
keyed reading of them) with ``NO_FREE_READING``. ``free_keys=None`` (no card
keys to check against) makes every reading card a candidate, as does the
override reading (one keyless GPU, D14).

A card's kind is the reading's ``GpuMem.card_kind`` (D1, set by ``vram``
from the card inventory; ``None`` when the card has none). The gate per kind:

* **render** and **audio** (D7, D10; gate ``measured`` or ``audio``): the
  candidates whose kind has a result in ``params.machine_results``; a card
  passes when ``vram_gate.fits_result(result peak, total, used)``, i.e.
  ``result peak <= floor(total * 0.93) - used``, with no margin. No such
  candidate fails with ``NO_RESULT_CARD``; candidates that all fail name
  the first one's figures (``needs <peak> MB (this machine's test); ...``).
* **pilot** (D7, FR-MT-V-2): the first candidate whose kind equals the
  job's ``unreal.card_kind``, with **no memory need** (S2); the reading must
  measure card memory (``measures_card``; the override does not). None
  fails with ``no free card of kind <k>``.
* **shots** (sequence rendering D3, FR-SD-3): never gated; the prep
  commandlet uses no GPU, so no reading is needed (gate ``skipped``, no
  card key). The reservation holds the first free Unreal card (``reserve_card``).

No reading (``None``) or no GPU in it fails. The result carries the chosen
card's key (``card_key``, ``None`` with the override), its kind
(``card_kind``), its ``used`` (``used_mb``: D9's ``used_at_gate`` when the
reading is the fresh gate's), the chosen card's result (``result``, render
and audio), the FR-UE-21 log line and the summary values (the chosen card,
or on a failure the first candidate, and ``vram_source``, the reading's
source).
"""
from dataclasses import dataclass, field, replace
from typing import Dict, Optional, Sequence

from sethlans_worker_agent.cards.identity import key_address
from sethlans_worker_agent.unreal_executor import card_order, machine_results
from sethlans_worker_agent.unreal_executor.vram import SOURCE_OVERRIDE
from shared.unreal import vram_gate

NO_FREE_CARD = "no free Unreal card."
NO_FREE_READING = "no memory reading for the free Unreal card."
NO_RESULT_CARD = "no free card with a memory result for this render."
NO_KIND_CARD = "no free card of kind {kind}."


@dataclass(frozen=True)
class GateResult:
    ok: bool
    gate: str                     # 'pilot' | 'audio' | 'measured' | 'skipped'
    message: str = ''
    need_mb: Optional[int] = None   # render, audio: the chosen card's result peak; a pilot has none
    summary: Dict[str, object] = field(default_factory=dict)
    reading: object = None
    card_key: Optional[str] = None  # the checked card (FR-GU-10); the job's first launch renders on it
    card_kind: Optional[str] = None  # the checked card's kind (D1)
    used_mb: Optional[int] = None   # the checked card's used (D9 used_at_gate, from the fresh gate)
    result: Optional[machine_results.MachineResult] = None  # render, audio: the checked card's result (D7, D11)


def job_card(reading):
    """D8's card of the reading (``card_order``, as ``adapter_choice.pick``): size, then no screen, then key."""
    return min(reading.gpus, key=lambda gpu: card_order.sort_key(gpu.total_mb, gpu.screen, gpu.card_key))


def _summary(gate: str, gpu, source: str, result) -> Dict[str, object]:
    values = {'vram_gate': gate, 'vram_source': source, 'vram_mb': gpu.total_mb, 'vram_used_mb': gpu.used_mb,
              'vram_need_mb': '' if result is None else result.peak_mb,
              'vram_usable_mb': vram_gate.usable_mb(gpu.total_mb, gpu.used_mb),
              'vram_card_kind': gpu.card_kind or ''}
    if result is not None:
        values['vram_measurement_id'] = result.measurement_id
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


def _passed(gate: str, gpu, key, source: str, result) -> GateResult:
    return GateResult(True, gate, '', None if result is None else result.peak_mb, _summary(gate, gpu, source, result),
                      card_key=key, card_kind=gpu.card_kind, used_mb=gpu.used_mb, result=result)


def _check_result(gate: str, gpu, key, source: str, result) -> GateResult:
    """D7 on one card: the card kind's result peak under the 93% line, no margin."""
    if vram_gate.fits_result(result.peak_mb, gpu.total_mb, gpu.used_mb):
        return _passed(gate, gpu, key, source, result)
    usable = vram_gate.usable_mb(gpu.total_mb, gpu.used_mb)
    return GateResult(False, gate, f"needs {result.peak_mb} MB (this machine's test); {usable} MB usable on a "
                                   f"{gpu.total_mb} MB GPU ({gpu.used_mb} MB in use).", result.peak_mb,
                      _summary(gate, gpu, source, result), card_key=key, card_kind=gpu.card_kind,
                      used_mb=gpu.used_mb, result=result)


def _pilot(params, candidates: list, source: str) -> Optional[GateResult]:
    """FR-MT-V-2: the first free card of the pilot's kind; no memory need (S2)."""
    for gpu, key in candidates:
        if gpu.card_kind is not None and gpu.card_kind == params.card_kind:
            return _passed('pilot', gpu, key, source, None)
    return None


def _measured(params, gate: str, candidates: list, source: str):
    """``(passed result or None, first failure or None)`` over the candidates with a result of their kind (D7)."""
    first = None
    for gpu, key in candidates:
        result = machine_results.for_kind(params.machine_results, gpu.card_kind)
        if result is None:
            continue  # a card of a kind this machine has no result for is skipped
        checked = _check_result(gate, gpu, key, source, result)
        if checked.ok:
            return checked, None
        first = first or checked
    return None, first


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
    candidates = _candidates(reading, free_keys)
    if not candidates:
        return GateResult(False, gate, NO_FREE_READING if free_keys else NO_FREE_CARD)
    if params.is_pilot:
        passed = _pilot(params, candidates, source)
        if passed is None:
            return GateResult(False, gate, NO_KIND_CARD.format(kind=params.card_kind))
        return replace(passed, reading=reading)
    passed, first = _measured(params, gate, candidates, source)
    if passed is not None:
        return replace(passed, reading=reading)
    return first if first is not None else GateResult(False, gate, NO_RESULT_CARD)
