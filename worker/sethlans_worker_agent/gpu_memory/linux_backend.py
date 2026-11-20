# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Linux backend of the GPU memory reader, ``linux-drm`` (FR-GL-1..6).

Cards come from the card probe, ``cards.os_probe_linux.probe()``
(display-class PCI devices, one per address). ``index`` is a card's rank by
PCI address (``identity.key_sort_value``) among **every** card the probe
lists, readable or not. It is a display and trace index only and must never
become a launch index (no ``-graphicsadapter``, no Vulkan device index).

* **AMD** (vendor ``0x1002``): ``mem_info_vram_total`` and
  ``mem_info_vram_used`` (bytes, decimal) in the card's device directory,
  ``realpath(<sys>/bus/pci/devices/<address>)``, which must lie under
  ``<sys>/devices/`` and be named by the card's address. Files are read with
  the probe's 4 KiB cap (``os_probe_linux.read_text``) and parsed as
  integers. Name from the probe. Not attributed (``None``). Untested on real
  hardware.
* **NVIDIA** (vendor ``0x10DE``): one ``nvidia-smi`` card query per sample;
  rows are matched to cards by PCI address, compared as integers. Its
  driver exposes no ``mem_info_vram_*``. Attribution from
  ``nvidia-smi -q -d PIDS`` (``nvidia_smi.attribute``). The total is
  ``nvidia-smi``'s, which includes memory the driver reserves, so the 0.93
  line is about 2% looser here than on a DXGI total.
* **Other vendors** (Intel ``0x8086``, ...): not read; logged once at INFO.

``open()`` keeps the cards readable at that moment (at most 16) and fails
when none is. A kept card that fails later makes that sample ``None``. The
readings' ``source`` is ``nvidia-smi`` or ``amdgpu-sysfs`` when every kept
card is of that kind, else ``mixed``. ``amd_only`` (the pinned
``amdgpu-sysfs`` source) reads AMD cards only. When ``open()`` fails (the
PCI list cannot be read, or no card is readable), the reader's next
``auto`` candidate on Linux, ``nvidia-smi`` alone, is tried.
"""
import logging
import os
import re
import threading
import time
from typing import Callable, Dict, FrozenSet, NamedTuple, Optional, Tuple

from sethlans_worker_agent.cards import os_probe_linux
from sethlans_worker_agent.cards.identity import key_address, key_sort_value
from sethlans_worker_agent.gpu_memory import nvidia_smi
from sethlans_worker_agent.gpu_memory.types import MAX_REPORTED_GPUS, GpuMemoryReading, make_gpu

logger = logging.getLogger(__name__)

NAME = 'linux-drm'
AMD_SOURCE = 'amdgpu-sysfs'
MIXED_SOURCE = 'mixed'
AMD_VENDOR = 0x1002
NVIDIA_VENDOR = 0x10DE
MIB = 1024 * 1024
KEY_PREFIX = 'pci:'
_INTEGER = re.compile(r'[0-9]{1,20}')

_logged_lock = threading.Lock()  # leaf lock
_logged: set = set()


class _Card(NamedTuple):
    index: int
    key: str
    name: str
    kind: str                  # AMD_SOURCE or nvidia_smi.SOURCE
    device_dir: Optional[str]  # AMD only


def _log_once(key: str, name: str, reason: str) -> None:
    with _logged_lock:
        if (key, reason) in _logged:
            return
        _logged.add((key, reason))
    logger.info("GPU memory: card %s '%s' is not read: %s.", key, name, reason)


def device_dir(key: str, sys_root: str) -> Optional[str]:
    """The card's resolved device directory, or ``None`` when it is not a device under ``<sys>/devices/``."""
    address = key[len(KEY_PREFIX):]
    devices_root = os.path.join(os.path.realpath(os.path.join(sys_root, 'devices')), '')
    path = os.path.realpath(os.path.join(sys_root, 'bus', 'pci', 'devices', address))
    if not path.startswith(devices_root) or os.path.basename(path) != address:
        return None
    return path


def _read_bytes(path: str) -> Optional[int]:
    text = os_probe_linux.read_text(path)
    return int(text) if text and _INTEGER.fullmatch(text) else None


def read_amd(directory: str) -> Optional[Tuple[int, int]]:
    """``(total_mb, used_mb)`` from an amdgpu device directory, or ``None``."""
    total = _read_bytes(os.path.join(directory, 'mem_info_vram_total'))
    used = _read_bytes(os.path.join(directory, 'mem_info_vram_used'))
    if total is None or used is None or total < MIB:
        return None
    total_mb = total // MIB
    return total_mb, min(used // MIB, total_mb)


def _nvidia_rows(run: Callable) -> Optional[Dict[Tuple[int, ...], nvidia_smi.Row]]:
    """``nvidia-smi`` card rows by PCI address, or ``None`` when the query failed.

    An address listed twice maps to ``None``: that card is unreadable (neither row is trusted).
    """
    rows = nvidia_smi.query_cards(run)
    if rows is None:
        return None
    by_address: Dict[Tuple[int, ...], Optional[nvidia_smi.Row]] = {}
    for row in rows:
        if row[4] is not None:
            address = key_address(row[4])
            by_address[address] = None if address in by_address else row
    return by_address


class LinuxDrmBackend:
    """The reader backend (``gpu_memory.reader.Backend``) over sysfs and ``nvidia-smi``."""

    def __init__(self, amd_only: bool = False, probe: Callable = os_probe_linux.probe,
                 run: Callable = nvidia_smi.run_nvidia_smi, sys_root: str = '/sys',
                 clock: Callable[[], float] = time.monotonic):
        self.name = AMD_SOURCE if amd_only else NAME
        self._amd_only, self._probe, self._run, self._sys_root, self._clock = amd_only, probe, run, sys_root, clock
        self._cards: Tuple[_Card, ...] = ()
        self._source = ''

    def open(self) -> bool:
        """Keep the cards readable now; False when the PCI list cannot be read or no card is readable."""
        listed = self._probe(sys_root=self._sys_root)
        if listed is None:
            logger.info("GPU memory: the PCI device list cannot be read.")
            return False
        ranked = sorted((card for card in listed if card.key), key=lambda card: key_sort_value(card.key))
        planned = [card for card in (self._plan(index, card) for index, card in enumerate(ranked)) if card]
        rows = _nvidia_rows(self._run) if any(card.kind == nvidia_smi.SOURCE for card in planned) else {}
        kept = []
        for card in planned:
            if self._values(card, rows) is None:
                why = "its memory files cannot be read" if card.kind == AMD_SOURCE else "nvidia-smi does not list it"
                _log_once(card.key, card.name, why)
            elif len(kept) >= MAX_REPORTED_GPUS:
                _log_once(card.key, card.name, f"more than {MAX_REPORTED_GPUS} cards")
            else:
                kept.append(card)
        self._cards = tuple(kept)
        kinds = {card.kind for card in kept}
        self._source = kinds.pop() if len(kinds) == 1 else MIXED_SOURCE
        return bool(kept)

    def _plan(self, index: int, card) -> Optional[_Card]:
        if card.vendor_id == AMD_VENDOR:
            return _Card(index, card.key, card.name, AMD_SOURCE, device_dir(card.key, self._sys_root))
        if card.vendor_id != NVIDIA_VENDOR:
            _log_once(card.key, card.name, f"no GPU memory reader for vendor 0x{card.vendor_id or 0:04x}")
            return None
        if self._amd_only:
            _log_once(card.key, card.name, f"the source is pinned to {AMD_SOURCE}")
            return None
        return _Card(index, card.key, card.name, nvidia_smi.SOURCE, None)

    @staticmethod
    def _values(card: _Card, rows) -> Optional[Tuple[str, int, int, Optional[str]]]:
        """``(name, total_mb, used_mb, nvidia-smi card key)`` of one card, or ``None`` when unreadable."""
        if card.kind == AMD_SOURCE:
            sizes = read_amd(card.device_dir) if card.device_dir else None
            return (card.name,) + sizes + (None,) if sizes else None
        row = rows.get(key_address(card.key)) if rows is not None else None
        if row is None:
            return None
        _index, name, total, used, row_key = row
        return name or card.name, total, used, row_key

    def sample(self, pids: FrozenSet[int]) -> Optional[GpuMemoryReading]:
        taken_at = self._clock()
        has_nvidia = any(card.kind == nvidia_smi.SOURCE for card in self._cards)
        rows = _nvidia_rows(self._run) if has_nvidia else {}
        values = [self._values(card, rows) for card in self._cards]
        if not values or any(value is None for value in values):
            return None
        ours = nvidia_smi.attribute([value[3] for value in values], pids, run=self._run,
                                    known_keys=[row[4] for row in rows.values() if row]) if has_nvidia else {}
        gpus = tuple(make_gpu(card.index, name, total, used, ours.get(row_key) if row_key else None, card.key)
                     for card, (name, total, used, row_key) in zip(self._cards, values))
        return GpuMemoryReading(taken_at, self._source, gpus, measures_card=True,
                                measures_process=all(gpu.sethlans_used_mb is not None for gpu in gpus))

    def close(self) -> None:
        """Nothing is held between samples."""
