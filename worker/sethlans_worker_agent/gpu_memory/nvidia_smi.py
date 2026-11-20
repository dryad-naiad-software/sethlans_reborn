# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The ``nvidia-smi`` backend of the GPU memory reader (source ``nvidia-smi``).

Card query: ``nvidia-smi --query-gpu=index,pci.bus_id,memory.total,
memory.used,name --format=csv,noheader,nounits``. The name is the last
field, so it may contain commas. Rows with ``[N/A]`` in a number are
dropped; ``card_key`` comes from ``pci.bus_id`` (``00000000:01:00.0`` ->
``pci:0000:01:00.0``). At most ``MAX_REPORTED_GPUS`` cards.

The binary: on Windows only ``%SystemRoot%\\System32\\nvidia-smi.exe``
(where the driver installs it), never a ``nvidia-smi`` found on ``PATH``;
without that file the backend does not open. Elsewhere ``nvidia-smi``
from ``PATH``. List-form ``subprocess.run``, ``timeout=10``; a failed call
gives ``None`` and never raises.

Attribution ("where obtainable"; unknown is ``None``, never a guess):

* **Windows: always ``None``.** ``--query-compute-apps`` is not a valid
  source for D3D processes: under WDDM it lists them with ``[N/A]``, and
  under TCC it does not cover D3D at all.
* Elsewhere: ``--query-compute-apps`` per-process ``used_memory`` (CUDA,
  i.e. Blender), summed over the worker's process tree. A card is
  attributed only when every process listed on it has a number.
"""
import logging
import os
import subprocess
import sys
import time
from typing import Callable, Dict, FrozenSet, List, Optional, Tuple

from sethlans_worker_agent.cards.identity import card_key, clean_name
from sethlans_worker_agent.gpu_memory.types import MAX_REPORTED_GPUS, GpuMemoryReading, make_gpu

logger = logging.getLogger(__name__)

SOURCE = 'nvidia-smi'
NVIDIA_SMI_TIMEOUT_SECONDS = 10
MAX_STDOUT_CHARS = 1024 * 1024  # larger output is a failed call, never parsed in part
QUERY_GPU = ['nvidia-smi', '--query-gpu=index,pci.bus_id,memory.total,memory.used,name',
             '--format=csv,noheader,nounits']
QUERY_UUIDS = ['nvidia-smi', '--query-gpu=index,uuid', '--format=csv,noheader,nounits']
QUERY_APPS = ['nvidia-smi', '--query-compute-apps=gpu_uuid,pid,used_memory',
              '--format=csv,noheader,nounits']

# (index, name, total_mb, used_mb, card_key)
Row = Tuple[int, str, int, int, Optional[str]]


def _int_field(text: str) -> Optional[int]:
    text = text.strip()
    try:
        value = int(text)
    except ValueError:
        return None
    return value if value >= 0 else None


def parse_query_gpu(stdout: str) -> List[Row]:
    """Rows ``(index, name, total_mb, used_mb, card_key)``; rows with ``[N/A]`` numbers are dropped."""
    rows = []
    for line in stdout.splitlines():
        fields = line.split(',', 4)
        if len(fields) != 5:
            continue
        index, total, used = _int_field(fields[0]), _int_field(fields[2]), _int_field(fields[3])
        if index is None or total is None or used is None:
            continue
        name = fields[4].strip()
        if name.startswith('[') and name.endswith(']'):
            name = ''
        rows.append((index, clean_name(name), total, min(used, total), card_key(fields[1].strip())))
    return rows


def parse_compute_apps(stdout: str) -> List[Tuple[str, int, Optional[int]]]:
    """Rows ``(gpu_uuid, pid, used_mb or None for [N/A])``."""
    rows = []
    for line in stdout.splitlines():
        fields = [field.strip() for field in line.split(',')]
        if len(fields) != 3:
            continue
        pid = _int_field(fields[1])
        if not fields[0] or pid is None:
            continue
        rows.append((fields[0], pid, _int_field(fields[2])))
    return rows


def parse_uuids(stdout: str) -> Dict[str, int]:
    out = {}
    for line in stdout.splitlines():
        fields = [field.strip() for field in line.split(',')]
        if len(fields) == 2 and _int_field(fields[0]) is not None and fields[1]:
            out[fields[1]] = int(fields[0])
    return out


def _is_windows() -> bool:
    return sys.platform == 'win32'


def nvidia_smi_executable() -> Optional[str]:
    """Windows: ``%SystemRoot%\\System32\\nvidia-smi.exe`` when that file exists, else None.

    Elsewhere ``nvidia-smi`` (``PATH``).
    """
    if not _is_windows():
        return 'nvidia-smi'
    system_root = os.environ.get('SystemRoot', r'C:\Windows')
    if not os.path.isabs(system_root):
        return None  # a relative SystemRoot would resolve against the working directory
    candidate = os.path.join(system_root, 'System32', 'nvidia-smi.exe')
    return candidate if os.path.isfile(candidate) else None


def _capped(out: Optional[str]) -> Optional[str]:
    """None for output over ``MAX_STDOUT_CHARS`` (a failed sample, never a truncated parse)."""
    if out is not None and len(out) > MAX_STDOUT_CHARS:
        logger.debug("nvidia-smi output over %d characters ignored.", MAX_STDOUT_CHARS)
        return None
    return out


def run_nvidia_smi(args: List[str]) -> Optional[str]:
    """stdout of a successful ``nvidia-smi`` call, else None. Never raises.

    ``args[0]`` is replaced by ``nvidia_smi_executable()``.
    """
    try:
        executable = nvidia_smi_executable()
        if executable is None:
            logger.debug("nvidia-smi is not installed in System32.")
            return None
        result = subprocess.run([executable] + list(args[1:]), capture_output=True, text=True,
                                timeout=NVIDIA_SMI_TIMEOUT_SECONDS)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
        logger.debug("nvidia-smi failed: %s", exc)
        return None
    if result.returncode != 0:
        logger.debug("nvidia-smi exited %s: %s", result.returncode, (result.stderr or '').strip()[:200])
        return None
    return _capped(result.stdout)


def attribute(indices: List[int], ours: FrozenSet[int],
              run: Callable = run_nvidia_smi) -> Dict[int, Optional[int]]:
    """``{index: sethlans_used_mb or None}``; None when the source cannot attribute.

    Always None on Windows (compute-apps cannot see D3D memory; see module doc).
    """
    unknown = {index: None for index in indices}
    if _is_windows():
        return unknown
    apps_out = _capped(run(QUERY_APPS))
    if apps_out is None:
        return unknown
    apps = parse_compute_apps(apps_out)
    if all(used is None for _uuid, _pid, used in apps):
        return unknown  # nothing listed, or WDDM [N/A] everywhere
    uuid_out = _capped(run(QUERY_UUIDS))
    uuid_to_index = parse_uuids(uuid_out) if uuid_out is not None else {}
    sums: Dict[int, int] = {}
    blocked = set()
    for uuid, pid, used in apps:
        index = uuid_to_index.get(uuid)
        if index not in unknown:
            continue
        if used is None:
            blocked.add(index)
            continue
        sums[index] = sums.get(index, 0) + (used if pid in ours else 0)
    result = dict(unknown)
    for index, total in sums.items():
        if index not in blocked:
            result[index] = total
    return result


class NvidiaSmiBackend:
    """The reader backend (``gpu_memory.reader.Backend``) over ``nvidia-smi``."""

    name = SOURCE

    def __init__(self, run: Callable = run_nvidia_smi, clock: Callable[[], float] = time.monotonic):
        self._run = run
        self._clock = clock

    def open(self) -> bool:
        """False on Windows without the System32 binary (FR-GN-1); nothing to set up otherwise."""
        return not _is_windows() or nvidia_smi_executable() is not None

    def sample(self, pids: FrozenSet[int]) -> Optional[GpuMemoryReading]:
        taken_at = self._clock()
        out = _capped(self._run(QUERY_GPU))
        rows = parse_query_gpu(out)[:MAX_REPORTED_GPUS] if out is not None else []
        if not rows:
            return None
        sethlans = attribute([row[0] for row in rows], pids, run=self._run)
        gpus = tuple(make_gpu(index, name, total, used, sethlans.get(index), key)
                     for index, name, total, used, key in rows)
        return GpuMemoryReading(taken_at, SOURCE, gpus, measures_card=True,
                                measures_process=all(gpu.sethlans_used_mb is not None for gpu in gpus))

    def close(self) -> None:
        """Nothing is held between samples."""
