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
  nothing (``measures_card`` False). The reader is then never called.
* Otherwise ``gpu_memory.reader.get_reader().read(sethlans_pids)``. Only
  the sampler calls this, and the sampler runs only while Unreal is
  enabled, so with Unreal off no reader exists (NF-6).

``taken_at`` is the time the sample started. Any failure yields ``None``.
"""
import logging
import os
import time
from dataclasses import replace
from typing import Callable, Optional

import psutil

from sethlans_worker_agent.gpu_memory import reader as gpu_reader
from sethlans_worker_agent.gpu_memory.types import (  # also re-exported for the Unreal modules
    MAX_AGE_SECONDS, MAX_GPU_INDEX, MAX_NAME_LENGTH, MAX_REPORTED_GPUS, GpuMem, GpuMemoryReading)

logger = logging.getLogger(__name__)

SOURCE_OVERRIDE = 'override'


def sethlans_pids() -> frozenset:
    """The worker process and all its descendants (Blender, UE trees)."""
    try:
        me = psutil.Process(os.getpid())
        return frozenset([me.pid] + [child.pid for child in me.children(recursive=True)])
    except psutil.Error:
        return frozenset([os.getpid()])


def _read_live(pids: Callable[[], frozenset]) -> Optional[GpuMemoryReading]:
    return gpu_reader.get_reader().read(pids)


def take_reading(override_mb: int, clock: Callable[[], float] = time.monotonic,
                 read: Optional[Callable] = None,
                 pids: Callable[[], frozenset] = sethlans_pids) -> Optional[GpuMemoryReading]:
    """One reading, or None when there is no usable source. Never raises."""
    try:
        taken_at = clock()
        if override_mb > 0:
            return GpuMemoryReading(taken_at, SOURCE_OVERRIDE, (GpuMem(0, '', override_mb, 0, None, None),))
        reading = (read or _read_live)(pids)
        return replace(reading, taken_at=taken_at) if reading is not None else None
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
