# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The per-process counter items of the Windows ``pdh`` backend (``win_backend``).

``\\GPU Process Memory(*)\\Dedicated Usage`` and ``\\GPU Process Memory(*)\\Shared
Usage`` give one item per ``pid_<pid>_luid_0x<HIGH>_0x<LOW>_phys_<n>``
instance, in bytes. The helpers here turn those items into bytes per LUID
and per pid, and into the reading's card key -> pid -> MiB maps (``per_pid``
for dedicated usage, ``per_pid_shared`` for shared usage, FR-MT-S-1): each
pid's bytes are summed over the card's ``phys_n`` items, then floored to MiB.
Only the given pids count. Items whose ``CStatus`` is not valid or new data,
or whose name does not parse, are skipped. LUIDs are compared as integers,
so the case of the hex digits does not matter.

Pure functions over ``win_pdh.Item`` tuples: nothing here touches ctypes or
a DLL, so the module imports on every platform.
"""
import re
from typing import Dict, FrozenSet, Iterable, Optional, Tuple

from sethlans_worker_agent.gpu_memory import win_pdh

MIB = 1024 * 1024

_PROCESS_NAME = re.compile(r'pid_(\d+)_luid_0x([0-9a-f]{8})_0x([0-9a-f]{8})_phys_(\d+)', re.IGNORECASE)
_GOOD_STATUS = (win_pdh.PDH_CSTATUS_VALID_DATA, win_pdh.PDH_CSTATUS_NEW_DATA)

ByPid = Dict[int, Dict[int, int]]  # {luid: {pid: bytes}}


def hex_luid(high: str, low: str) -> int:
    """The LUID spelled ``0x<HIGH>_0x<LOW>`` in an instance name as one integer."""
    return (int(high, 16) << 32) | int(low, 16)


def valid_items(items: Iterable[win_pdh.Item]):
    """``(name, value)`` of the items with a valid or new ``CStatus`` and a value of at least 0."""
    for name, value, status in items:
        if status in _GOOD_STATUS and value >= 0:
            yield name, value


def process_owner(instance: str) -> Optional[Tuple[int, int]]:
    """``(pid, LUID)`` of a process instance name, or ``None`` when it does not parse."""
    match = _PROCESS_NAME.fullmatch(instance or '')
    return (int(match.group(1)), hex_luid(match.group(2), match.group(3))) if match else None


def process_usage_by_pid(items: Iterable[win_pdh.Item], pids: FrozenSet[int]) -> ByPid:
    """Bytes each of the given pids uses, per LUID: ``{luid: {pid: bytes}}``."""
    per: ByPid = {}
    for name, value in valid_items(items):
        owner = process_owner(name)
        if owner is not None and owner[0] in pids:
            pid, luid = owner
            on_card = per.setdefault(luid, {})
            on_card[pid] = on_card.get(pid, 0) + value
    return per


def by_pid_or_none(items: Optional[Iterable[win_pdh.Item]], pids: FrozenSet[int]) -> Optional[ByPid]:
    """``process_usage_by_pid`` of a counter's array, or ``None`` when the array was not read."""
    return None if items is None else process_usage_by_pid(items, pids)


def luid_sums(by_pid: ByPid) -> Dict[int, int]:
    """Bytes per LUID over every pid of a ``process_usage_by_pid`` map."""
    return {luid: sum(per.values()) for luid, per in by_pid.items()}


def per_pid_map(cards: tuple, by_pid: Optional[ByPid]):
    """``{card_key: {pid: MiB}}`` for the cards with a key, or ``None`` when the process array was not read.

    ``cards`` are ``win_backend.Card`` tuples (``os_card.key`` and ``luid``). Every card with a key has an entry,
    empty when none of the pids uses it.
    """
    if by_pid is None:
        return None
    return {card.os_card.key: {pid: used // MIB for pid, used in by_pid.get(card.luid, {}).items()}
            for card in cards if card.os_card.key is not None}
