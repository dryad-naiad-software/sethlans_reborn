# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Windows ``pdh`` backend of the GPU memory reader, for every brand (FR-GW).

Cards: ``cards.os_probe_windows.cards_with_luids()`` (DXGI in Unreal's
order, the card rule, each card's LUID). Per card ``index`` is the DXGI
index, ``total_mb`` DXGI's dedicated memory (D21) and ``card_key`` the PCI
address key. A card with no ``GPU Adapter Memory`` instance in the first
collect is left out (one INFO line); at most ``MAX_REPORTED_GPUS`` cards.

Each sample is one ``PdhQuery.collect()`` over two wildcard counters:

* ``used_mb``: the card's ``luid_0x<HIGH>_0x<LOW>_phys_<n>`` adapter items
  summed (every ``phys_n``). LUIDs are compared as integers, so the case of
  the hex digits does not matter. Items whose ``CStatus`` is not valid or new
  data, or whose name does not parse, are skipped. A card with no valid item
  makes the sample ``None``.
* ``sethlans_used_mb`` (#226): the ``pid_<pid>_luid_..._phys_<n>`` process
  items of the given pids on the card's LUID, summed; 0 when there is none.
  When the process array cannot be read every card gets ``None`` and
  ``measures_process`` is False.
* ``per_pid`` (render per card FR-PC-ATT-1): the same items per pid, card
  key -> pid -> MiB (each pid's bytes summed over the card's ``phys_n``
  items, then floored to MiB). Every card of the reading has an entry (empty
  when none of the pids uses it); ``None`` when the process array cannot be
  read.

LUID changes: a collect with an adapter LUID never seen, or without a kept
card's LUID, lists the cards again (at most every ``RELIST_SECONDS``). When
the index -> (LUID, key, name) assignment changed, one WARNING is logged and
that sample is ``None``; later samples use the new list. With the same
assignment, the cards are adopted again when the set with a counter changed
(a counter that appears after a driver restart). Only PDH's LUIDs count as
seen, so a card's new LUID that DXGI lists before PDH does is still new.

The one ``nvidia-smi`` cross-check (FR-GS-6) runs in ``before_sample()``,
which the reader calls untimed before the sample after the first good one.

The LUID stays in this object's memory: it is never logged, stored or sent.
The query is used only under the reader's lock. Nothing raises out of
``open()`` or ``sample()`` except through the reader's own guard.
"""
import ctypes
import logging
import re
import sys
import time
from typing import Callable, Dict, FrozenSet, Iterable, NamedTuple, Optional, Tuple

from sethlans_worker_agent.gpu_memory import win_pdh
from sethlans_worker_agent.gpu_memory.types import MAX_REPORTED_GPUS, GpuMemoryReading, make_gpu

logger = logging.getLogger(__name__)

SOURCE = 'pdh'
ADAPTER_PATH = r'\GPU Adapter Memory(*)\Dedicated Usage'
PROCESS_PATH = r'\GPU Process Memory(*)\Dedicated Usage'
PATHS = (ADAPTER_PATH, PROCESS_PATH)
RELIST_SECONDS = 60.0
MIB = 1024 * 1024
_CHECK_WAITING, _CHECK_DUE, _CHECK_DONE = 'waiting', 'due', 'done'  # the one nvidia-smi cross-check

_ADAPTER_NAME = re.compile(r'luid_0x([0-9a-f]{8})_0x([0-9a-f]{8})_phys_(\d+)', re.IGNORECASE)
_PROCESS_NAME = re.compile(r'pid_(\d+)_luid_0x([0-9a-f]{8})_0x([0-9a-f]{8})_phys_(\d+)', re.IGNORECASE)
_GOOD_STATUS = (win_pdh.PDH_CSTATUS_VALID_DATA, win_pdh.PDH_CSTATUS_NEW_DATA)


class Card(NamedTuple):
    """A card of the reading: the card probe's ``OsCard`` and its LUID as one integer."""
    os_card: object
    luid: int


def luid_value(luid: Tuple[int, int]) -> int:
    """A DXGI ``(HighPart, LowPart)`` LUID as the 64-bit integer PDH's instance names spell in hex."""
    high, low = luid
    return ((high & 0xFFFFFFFF) << 32) | (low & 0xFFFFFFFF)


def _hex_luid(high: str, low: str) -> int:
    return (int(high, 16) << 32) | int(low, 16)


def adapter_luid(instance: str) -> Optional[int]:
    """The LUID of an adapter instance name, or ``None`` when it does not parse."""
    match = _ADAPTER_NAME.fullmatch(instance or '')
    return _hex_luid(match.group(1), match.group(2)) if match else None


def process_owner(instance: str) -> Optional[Tuple[int, int]]:
    """``(pid, LUID)`` of a process instance name, or ``None`` when it does not parse."""
    match = _PROCESS_NAME.fullmatch(instance or '')
    return (int(match.group(1)), _hex_luid(match.group(2), match.group(3))) if match else None


def _valid(items: Iterable[win_pdh.Item]):
    for name, value, status in items:
        if status in _GOOD_STATUS and value >= 0:
            yield name, value


def adapter_usage(items: Iterable[win_pdh.Item]) -> Dict[int, int]:
    """Bytes in use per LUID, over every ``phys_n`` instance of it."""
    used: Dict[int, int] = {}
    for name, value in _valid(items):
        luid = adapter_luid(name)
        if luid is not None:
            used[luid] = used.get(luid, 0) + value
    return used


def process_usage_by_pid(items: Iterable[win_pdh.Item], pids: FrozenSet[int]) -> Dict[int, Dict[int, int]]:
    """Bytes each of the given pids uses, per LUID: ``{luid: {pid: bytes}}``."""
    per: Dict[int, Dict[int, int]] = {}
    for name, value in _valid(items):
        owner = process_owner(name)
        if owner is not None and owner[0] in pids:
            pid, luid = owner
            on_card = per.setdefault(luid, {})
            on_card[pid] = on_card.get(pid, 0) + value
    return per


def luid_sums(by_pid: Dict[int, Dict[int, int]]) -> Dict[int, int]:
    """Bytes per LUID over every pid of a ``process_usage_by_pid`` map."""
    return {luid: sum(per.values()) for luid, per in by_pid.items()}


def process_usage(items: Iterable[win_pdh.Item], pids: FrozenSet[int]) -> Dict[int, int]:
    """Bytes the given pids use per LUID."""
    return luid_sums(process_usage_by_pid(items, pids))


def per_pid_map(cards: Tuple[Card, ...], by_pid: Optional[Dict[int, Dict[int, int]]]):
    """``{card_key: {pid: MiB}}`` for the cards with a key, or ``None`` when the process array was not read."""
    if by_pid is None:
        return None
    return {card.os_card.key: {pid: used // MIB for pid, used in by_pid.get(card.luid, {}).items()}
            for card in cards if card.os_card.key is not None}


def build_reading(cards: Tuple[Card, ...], used: Dict[int, int], ours: Optional[Dict[int, int]],
                  taken_at: float, by_pid: Optional[Dict[int, Dict[int, int]]] = None) -> Optional[GpuMemoryReading]:
    """The reading of ``cards``; ``None`` when there is no card or one has no adapter figure (partial).

    ``by_pid`` (``process_usage_by_pid``) fills ``per_pid``; ``None`` leaves it ``None``.
    """
    gpus = []
    for card in cards:
        if card.luid not in used:
            return None
        os_card = card.os_card
        sethlans = None if ours is None else ours.get(card.luid, 0) // MIB
        gpus.append(make_gpu(os_card.dxgi_index, os_card.name, os_card.dedicated_mib, used[card.luid] // MIB,
                             sethlans, os_card.key))
    if not gpus:
        return None
    return GpuMemoryReading(taken_at, SOURCE, tuple(gpus), measures_card=True, measures_process=ours is not None,
                            per_pid=per_pid_map(cards, by_pid))


def _default_cards():
    from sethlans_worker_agent.cards import os_probe_windows
    return os_probe_windows.cards_with_luids()


def _default_cross_check(reading) -> None:
    from sethlans_worker_agent.gpu_memory import win_crosscheck
    win_crosscheck.log_against_nvidia_smi(reading)


class PdhBackend:
    """The reader backend (``gpu_memory.reader.Backend``) over the Windows GPU memory counters."""

    name = SOURCE

    def __init__(self, cards: Callable = _default_cards, query_factory: Callable = win_pdh.PdhQuery,
                 clock: Callable[[], float] = time.monotonic, is_64bit: Optional[bool] = None,
                 cross_check: Callable = _default_cross_check):
        self._list_cards, self._query_factory, self._clock = cards, query_factory, clock
        self._is_64bit = sys.maxsize > 2 ** 32 if is_64bit is None else is_64bit
        self._cross_check = cross_check
        self._query = None
        self._cards: Tuple[Card, ...] = ()
        self._assignment: tuple = ()
        self._known: set = set()
        self._listed_at = 0.0
        self._check_state = _CHECK_WAITING

    def open(self) -> bool:
        """List the cards, open the query, do the first collect; False when any of it fails or no card is left."""
        if not self._is_64bit:
            return False
        pairs = self._cards_now()
        if not pairs:
            return False
        try:
            self._query = self._query_factory(PATHS)
        except (OSError, TypeError, ValueError, AttributeError, ctypes.ArgumentError) as exc:
            logger.info("The Windows GPU memory counters cannot be opened: %s", exc)
            return False
        data = self._query.collect()
        adapter = data.get(ADAPTER_PATH) if data else None
        if adapter is None:
            logger.info("The Windows GPU memory counters gave no first reading.")
            return False
        self._adopt(pairs, adapter_usage(adapter))
        return bool(self._cards)

    def sample(self, pids: FrozenSet[int]) -> Optional[GpuMemoryReading]:
        taken_at = self._clock()
        data = self._query.collect() if self._query is not None else None
        adapter = data.get(ADAPTER_PATH) if data else None
        if adapter is None:
            return None
        used = adapter_usage(adapter)
        if self._moved(used) and self._relisted(used):
            return None
        process = data.get(PROCESS_PATH)
        by_pid = None if process is None else process_usage_by_pid(process, pids)
        ours = None if by_pid is None else luid_sums(by_pid)
        reading = build_reading(self._cards, used, ours, taken_at, by_pid)
        if reading is not None and self._check_state == _CHECK_WAITING:
            self._check_state = _CHECK_DUE
        return reading

    def before_sample(self) -> None:
        """Untimed work the reader runs before a sample: the one ``nvidia-smi`` cross-check (FR-GS-6).

        Due after the first successful sample, so it never delays the first reading; runs once, on a fresh
        collect (no attribution), and is logged only.
        """
        if self._check_state != _CHECK_DUE:
            return
        self._check_state = _CHECK_DONE
        data = self._query.collect() if self._query is not None else None
        adapter = data.get(ADAPTER_PATH) if data else None
        if adapter is not None:
            self._cross_check(build_reading(self._cards, adapter_usage(adapter), None, self._clock()))

    def close(self) -> None:
        query, self._query = self._query, None
        if query is not None:
            query.close()

    # -- cards and LUID changes ---------------------------------------------
    def _cards_now(self):
        self._listed_at = self._clock()
        try:
            return self._list_cards()
        except Exception:  # noqa: BLE001 - a failing card list means "cannot read now"
            logger.debug("Listing the cards for the GPU memory counters failed.", exc_info=True)
            return None

    def _adopt(self, pairs, used: Dict[int, int]) -> None:
        self._assignment = tuple((card.dxgi_index, luid_value(luid), card.key, card.name) for card, luid in pairs)
        kept = []
        for card, luid in pairs:
            if luid_value(luid) in used:
                kept.append(Card(card, luid_value(luid)))
            else:
                logger.info("Card %s '%s' has no GPU Adapter Memory counter; it is left out of the GPU memory "
                            "reading.", card.key, card.name)
        if len(kept) > MAX_REPORTED_GPUS:
            logger.info("Only the first %d of %d cards are read for GPU memory.", MAX_REPORTED_GPUS, len(kept))
        self._cards = tuple(kept[:MAX_REPORTED_GPUS])
        # PDH's LUIDs only, so a card's LUID that PDH lacks counts as new when it appears. While a listed card has
        # no counter, only the kept cards' LUIDs are known, so any other PDH LUID keeps the 60 s re-listing going.
        self._known = set(used) if len(kept) == len(pairs) else {card.luid for card in self._cards}

    def _moved(self, used: Dict[int, int]) -> bool:
        changed = bool(set(used) - self._known) or any(card.luid not in used for card in self._cards)
        return changed and self._clock() - self._listed_at >= RELIST_SECONDS

    def _relisted(self, used: Dict[int, int]) -> bool:
        """List the cards again; True (this sample is ``None``) when the assignment changed.

        With the same assignment, the cards are adopted again when the set that has a counter changed (a card's
        counter appearing after a driver restart, or going away). An empty or failed list changes nothing.
        """
        pairs = self._cards_now()
        if not pairs:
            return False
        assignment = tuple((card.dxgi_index, luid_value(luid), card.key, card.name) for card, luid in pairs)
        if assignment == self._assignment:
            counted = tuple(luid_value(luid) for _card, luid in pairs if luid_value(luid) in used)[:MAX_REPORTED_GPUS]
            if counted != tuple(card.luid for card in self._cards):
                self._adopt(pairs, used)
            elif all(luid_value(luid) in used for _card, luid in pairs):
                self._known |= set(used)
            return False
        logger.warning("GPU adapters changed; the reading restarts.")
        self._adopt(pairs, used)
        return True
