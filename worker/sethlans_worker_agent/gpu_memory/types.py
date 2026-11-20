# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The GPU memory reading types and their limits.

``GpuMem`` is one card, ``GpuMemoryReading`` one sample of every card a
backend reads. The fields added after the first version have defaults, so
the old positional constructors keep working. Sizes are MiB
(bytes // 1,048,576).

* ``card_key``: ``pci:dddd:bb:dd.f`` (``cards.identity.card_key``), or
  ``None`` when the source does not give an address.
* ``measures_card``: ``used_mb`` is a live reading (False for the
  ``SETHLANS_UNREAL_VRAM_MB`` override).
* ``measures_process``: every card has ``sethlans_used_mb``.

``problem(reading)`` names the first broken rule of a backend's reading
('' when it holds); the reader drops a reading that breaks one.
"""
from dataclasses import dataclass
from typing import Optional, Tuple

from sethlans_worker_agent.cards.identity import clean_name

MAX_REPORTED_GPUS = 16
MAX_GPU_INDEX = 63
MAX_NAME_LENGTH = 128
MAX_AGE_SECONDS = 86400


@dataclass(frozen=True)
class GpuMem:
    index: int
    name: str
    total_mb: int
    used_mb: int
    sethlans_used_mb: Optional[int]   # None = not attributed
    other_used_mb: Optional[int]      # None iff sethlans_used_mb is None
    card_key: Optional[str] = None


@dataclass(frozen=True)
class GpuMemoryReading:
    taken_at: float                   # time.monotonic()
    source: str                       # 'nvidia-smi' | 'override' (more sources come with their backends)
    gpus: Tuple[GpuMem, ...]
    measures_card: bool = False
    measures_process: bool = False


def make_gpu(index: int, name: str, total_mb: int, used_mb: int, sethlans_used_mb: Optional[int],
             card_key: Optional[str] = None) -> GpuMem:
    """A card with ``used`` and ``sethlans`` clamped to the total and ``other = max(0, used - sethlans)``."""
    used_mb = min(used_mb, total_mb)
    if sethlans_used_mb is None:
        return GpuMem(index, name, total_mb, used_mb, None, None, card_key)
    sethlans_used_mb = min(sethlans_used_mb, total_mb)
    return GpuMem(index, name, total_mb, used_mb, sethlans_used_mb, max(0, used_mb - sethlans_used_mb), card_key)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _gpu_problem(gpu: GpuMem) -> str:
    if not (_is_int(gpu.total_mb) and _is_int(gpu.used_mb) and 0 <= gpu.used_mb <= gpu.total_mb):
        return f"card {gpu.index}: used_mb is not within 0..total_mb"
    if (gpu.sethlans_used_mb is None) != (gpu.other_used_mb is None):
        return f"card {gpu.index}: attribution is half missing"
    if gpu.sethlans_used_mb is not None:
        if not (_is_int(gpu.sethlans_used_mb) and 0 <= gpu.sethlans_used_mb <= gpu.total_mb):
            return f"card {gpu.index}: sethlans_used_mb is not within 0..total_mb"
        if gpu.other_used_mb != max(0, gpu.used_mb - gpu.sethlans_used_mb):
            return f"card {gpu.index}: other_used_mb does not match"
    if not isinstance(gpu.name, str) or gpu.name != clean_name(gpu.name):
        return f"card {gpu.index}: the name is not cleaned"
    return ''


def problem(reading: GpuMemoryReading) -> str:
    """The first rule the reading breaks, or ''."""
    if len(reading.gpus) > MAX_REPORTED_GPUS:
        return f"more than {MAX_REPORTED_GPUS} cards"
    indices = [gpu.index for gpu in reading.gpus]
    if len(set(indices)) != len(indices) or not all(_is_int(index) for index in indices):
        return "card indices are not unique integers"
    keys = [gpu.card_key for gpu in reading.gpus if gpu.card_key is not None]
    if len(set(keys)) != len(keys):
        return "card keys are not unique"
    for gpu in reading.gpus:
        text = _gpu_problem(gpu)
        if text:
            return text
    return ''
