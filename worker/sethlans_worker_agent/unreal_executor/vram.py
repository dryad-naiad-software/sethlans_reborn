# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
GPU memory reading for Unreal (Unreal foundation FR-WD-10, D2/Q1, D8/Q2):
the Unreal adapter on top of the GPU memory reader (``gpu_memory``).

One ``GpuMemoryReading`` feeds both the VRAM gate (FR-UE-21) and the
heartbeat's ``gpu_memory`` (FR-RT-16), so what the API shows is what the
gate decided on. The sampler thread lives in ``vram_sampler``.

* ``SETHLANS_UNREAL_VRAM_MB`` > 0 replaces the reader: one GPU of that
  size, ``used_mb=0``, no attribution, ``source="override"``, measuring
  nothing (``measures_card`` False). The reader is then never called and
  nothing is cut.
* Otherwise ``gpu_memory.reader.get_reader().read(sethlans_pids)``, cut to
  the Unreal cards in use (FR-GU-2): a card is kept when its ``card_key``
  address equals one of theirs (``identity.key_address`` integers); a card
  without a key is dropped; kept cards keep their ``index``; no card left
  gives ``None``. Only the sampler calls this, and the sampler runs only
  while Unreal is enabled, so with Unreal off no reader exists (NF-6).

The set of Unreal cards comes from ``cards.unreal_cards.current(cached_only=
True)``, read after the reader returned (never under its lock): the cached
card probe only, never a probe or a wait on ``_probe_lock``; no cached
probe is "cannot decide". A decided set is reused
for at most ``CARD_SET_MAX_AGE_S`` and only while
``capacity.rebuild.generation()`` is unchanged. "Cannot decide" (``None``)
or a failure (logged once) keeps the last decided set and is never cached;
before any decision the reading is ``None``. ``_set_lock`` is a leaf lock,
held only to read or replace the cached set.

``only_card(reading, key)`` keeps the one card a launch renders on
(FR-GU-11). ``taken_at`` is the time the sample started. Any failure
yields ``None``.
"""
import logging
import os
import threading
import time
from dataclasses import replace
from typing import Callable, NamedTuple, Optional

import psutil

from sethlans_worker_agent.cards.identity import key_address
from sethlans_worker_agent.gpu_memory import reader as gpu_reader
from sethlans_worker_agent.gpu_memory.types import (  # also re-exported for the Unreal modules
    MAX_AGE_SECONDS, MAX_GPU_INDEX, MAX_NAME_LENGTH, MAX_REPORTED_GPUS, GpuMem, GpuMemoryReading)

logger = logging.getLogger(__name__)

SOURCE_OVERRIDE = 'override'
CARD_SET_MAX_AGE_S = 5.0


class _Decided(NamedTuple):
    addresses: frozenset      # (domain, bus, device, function) of each Unreal card in use
    at: float
    generation: int


_set_lock = threading.Lock()  # leaf lock
_decided: Optional[_Decided] = None
_failure_logged = False


def sethlans_pids() -> frozenset:
    """The worker process and all its descendants (Blender, UE trees)."""
    try:
        me = psutil.Process(os.getpid())
        return frozenset([me.pid] + [child.pid for child in me.children(recursive=True)])
    except psutil.Error:
        return frozenset([os.getpid()])


def _read_live(pids: Callable[[], frozenset]) -> Optional[GpuMemoryReading]:
    return gpu_reader.get_reader().read(pids)


def _current_cards():
    """The sampler thread never probes or waits for a probe (it would stall the 2 s fresh gate)."""
    from sethlans_worker_agent.cards import unreal_cards
    return unreal_cards.current(cached_only=True)


def _generation() -> int:
    from sethlans_worker_agent.capacity import rebuild
    return rebuild.generation()


def _note_failure() -> None:
    global _failure_logged
    with _set_lock:
        first, _failure_logged = not _failure_logged, True
    if first:
        logger.exception("Could not decide the Unreal cards in use; the last decided set stands.")


def unreal_card_set(clock: Callable[[], float] = time.monotonic, current: Callable = _current_cards,
                    generation: Callable[[], int] = _generation) -> Optional[frozenset]:
    """FR-GU-2: the addresses of the Unreal cards in use, or ``None`` before any decision.

    The check-then-store below is not atomic; it is safe only because the
    sampler thread is its sole caller (``take_reading``).
    """
    global _decided, _failure_logged
    now, gen = clock(), generation()
    with _set_lock:
        last = _decided
    if last is not None and last.generation == gen and 0 <= now - last.at < CARD_SET_MAX_AGE_S:
        return last.addresses
    try:
        found = current()
    except Exception:  # noqa: BLE001 - a failure keeps the last decided set
        _note_failure()
        found = None
    if found is None:
        return last.addresses if last is not None else None
    addresses = frozenset(address for address in (key_address(card.key) for card in found[0]) if address is not None)
    with _set_lock:
        _decided, _failure_logged = _Decided(addresses, now, gen), False
    return addresses


def cut_to_cards(reading: Optional[GpuMemoryReading], addresses: Optional[frozenset]) -> Optional[GpuMemoryReading]:
    """The reading's cards whose key address is in ``addresses``; ``None`` when none is left."""
    if reading is None or addresses is None:
        return None
    gpus = tuple(gpu for gpu in reading.gpus if key_address(gpu.card_key) in addresses)
    return replace(reading, gpus=gpus) if gpus else None


def only_card(reading: Optional[GpuMemoryReading], key: Optional[str]) -> Optional[GpuMemoryReading]:
    """FR-GU-11: the reading with only the card ``key``; ``None`` (no sample) when it lacks that card."""
    address = key_address(key)
    if reading is None or address is None:
        return None
    gpus = tuple(gpu for gpu in reading.gpus if key_address(gpu.card_key) == address)
    return replace(reading, gpus=gpus) if gpus else None


def take_reading(override_mb: int, clock: Callable[[], float] = time.monotonic,
                 read: Optional[Callable] = None,
                 pids: Callable[[], frozenset] = sethlans_pids,
                 cards: Optional[Callable[[], Optional[frozenset]]] = None) -> Optional[GpuMemoryReading]:
    """One reading, or None when there is no usable source. Never raises.

    ``cards``: the addresses of the Unreal cards in use (``unreal_card_set`` when None).
    """
    try:
        taken_at = clock()
        if override_mb > 0:
            return GpuMemoryReading(taken_at, SOURCE_OVERRIDE, (GpuMem(0, '', override_mb, 0, None, None),))
        reading = (read or _read_live)(pids)
        if reading is None:
            return None
        return cut_to_cards(replace(reading, taken_at=taken_at), (cards or unreal_card_set)())
    except Exception:  # noqa: BLE001 - any failure means "no reading"
        logger.exception("GPU memory reading failed.")
        return None


def to_payload(reading: Optional[GpuMemoryReading],
               now: Optional[float] = None) -> Optional[dict]:
    """The heartbeat ``gpu_memory`` value (FR-RT-16 shape, ``schema`` 1), or None."""
    if reading is None:
        return None
    now = time.monotonic() if now is None else now
    age = int(min(max(0.0, now - reading.taken_at), MAX_AGE_SECONDS))
    gpus = []
    for gpu in reading.gpus:
        if 0 <= gpu.index <= MAX_GPU_INDEX and len(gpus) < MAX_REPORTED_GPUS:
            gpus.append({
                'index': gpu.index, 'name': gpu.name[:MAX_NAME_LENGTH],
                'total_mb': gpu.total_mb, 'used_mb': gpu.used_mb,
                'sethlans_used_mb': gpu.sethlans_used_mb, 'other_used_mb': gpu.other_used_mb,
                'card_key': gpu.card_key,
            })
    return {'schema': 1, 'source': reading.source, 'age_s': age,
            'capabilities': {'card': bool(reading.measures_card), 'process': bool(reading.measures_process)},
            'gpus': gpus}


def reset_for_tests() -> None:
    """Forget the decided set of Unreal cards. Test-only hook."""
    global _decided, _failure_logged
    with _set_lock:
        _decided, _failure_logged = None, False
