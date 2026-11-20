# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
One ``nvidia-smi`` cross-check of the ``pdh`` reading at selection (GPU memory reader FR-GS-6).

Runs only when ``%SystemRoot%\\System32\\nvidia-smi.exe`` exists
(``nvidia_smi.nvidia_smi_executable``). One card query; each card of the
PDH reading is matched to an ``nvidia-smi`` row by card key (addresses
compared as integers). Per matched card one INFO line gives both ``used``
and both ``total`` figures; a ``used`` difference above
``USED_WARNING_MIB`` is logged at WARNING instead. The totals differ by
design (DXGI against ``nvidia-smi``), so they never warn. The result is
logged only, never used for a decision. Never raises.
"""
import logging
from typing import Callable, List, Optional, Tuple

from sethlans_worker_agent.cards.identity import key_address
from sethlans_worker_agent.gpu_memory import nvidia_smi
from sethlans_worker_agent.gpu_memory.types import GpuMemoryReading

logger = logging.getLogger(__name__)

USED_WARNING_MIB = 32


def compare(reading: GpuMemoryReading, rows: List[nvidia_smi.Row]) -> List[Tuple[int, str]]:
    """``(level, message)`` per card of ``reading`` that has an ``nvidia-smi`` row with the same address."""
    by_address = {key_address(row[4]): row for row in rows if key_address(row[4]) is not None}
    lines = []
    for gpu in reading.gpus:
        row = by_address.get(key_address(gpu.card_key))
        if row is None:
            continue
        _index, _name, smi_total, smi_used, _key = row
        difference = gpu.used_mb - smi_used
        level = logging.WARNING if abs(difference) > USED_WARNING_MIB else logging.INFO
        lines.append((level, f"GPU memory cross-check of {gpu.card_key} '{gpu.name}': used {gpu.used_mb} MiB "
                             f"(pdh) and {smi_used} MiB (nvidia-smi), difference {difference:+d} MiB; total "
                             f"{gpu.total_mb} MiB (DXGI) and {smi_total} MiB (nvidia-smi)."))
    return lines


def log_against_nvidia_smi(reading: Optional[GpuMemoryReading],
                           run: Callable = nvidia_smi.run_nvidia_smi,
                           executable: Callable[[], Optional[str]] = nvidia_smi.nvidia_smi_executable) -> None:
    """Log the cross-check when the System32 ``nvidia-smi`` exists; otherwise do nothing."""
    try:
        if reading is None or executable() is None:
            return
        out = run(nvidia_smi.QUERY_GPU)
        if out is None:
            logger.info("GPU memory cross-check: nvidia-smi gave no answer.")
            return
        for level, message in compare(reading, nvidia_smi.parse_query_gpu(out)):
            logger.log(level, message)
    except Exception:  # noqa: BLE001 - a log-only check never stops the reader
        logger.debug("GPU memory cross-check failed.", exc_info=True)
