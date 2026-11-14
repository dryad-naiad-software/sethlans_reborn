# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
GPU memory reading (Unreal foundation FR-WD-10, D2/Q1, D8/Q2).

One ``GpuMemoryReading`` feeds both the VRAM gate (FR-UE-21) and the
heartbeat's ``gpu_memory`` (FR-RT-16), so what the API shows is what the
gate decided on. The sampler thread lives in ``vram_sampler``.

Source: ``nvidia-smi --query-gpu=index,name,memory.total,memory.used``
(list-form ``subprocess.run``, ``timeout=10``, as in
``idle_detection/contention.py``). On Windows the binary is
``%SystemRoot%\\System32\\nvidia-smi.exe`` (where the driver installs it)
when that file exists, so a ``nvidia-smi.exe`` earlier on ``PATH`` is not
run; otherwise ``PATH`` is searched. ``SETHLANS_UNREAL_VRAM_MB`` > 0
replaces the source (one GPU of that size, ``used_mb=0``, no attribution,
``source="override"``) and then ``nvidia-smi`` is never run.

Attribution ("where obtainable"):

* **Windows: always ``None``** until a PDH source (the
  ``GPU Process Memory`` counters, chosen by spike S6) lands.
  ``--query-compute-apps`` is not a valid source for D3D processes: under
  WDDM it lists them with ``[N/A]``, and under TCC it does not cover D3D
  at all, so Unreal's memory would read as "other".
* Elsewhere: ``--query-compute-apps`` per-process ``used_memory`` (CUDA,
  i.e. Blender). ``sethlans_used_mb`` sums the worker process and its
  descendants; ``other_used_mb = used - sethlans``. A GPU is attributed
  only when every process listed on it has a number.

The report never guesses: unknown is ``None``.

Any failure yields ``None``.
"""
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

import psutil

logger = logging.getLogger(__name__)

NVIDIA_SMI_TIMEOUT_SECONDS = 10
QUERY_GPU = ['nvidia-smi', '--query-gpu=index,name,memory.total,memory.used',
             '--format=csv,noheader,nounits']
QUERY_UUIDS = ['nvidia-smi', '--query-gpu=index,uuid', '--format=csv,noheader,nounits']
QUERY_APPS = ['nvidia-smi', '--query-compute-apps=gpu_uuid,pid,used_memory',
              '--format=csv,noheader,nounits']
SOURCE_NVIDIA = 'nvidia-smi'
SOURCE_OVERRIDE = 'override'
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
    sethlans_used_mb: Optional[int]
    other_used_mb: Optional[int]


@dataclass(frozen=True)
class GpuMemoryReading:
    taken_at: float  # time.monotonic()
    source: str
    gpus: Tuple[GpuMem, ...]


def _int_field(text: str) -> Optional[int]:
    text = text.strip()
    try:
        value = int(text)
    except ValueError:
        return None
    return value if value >= 0 else None


def parse_query_gpu(stdout: str) -> List[Tuple[int, str, int, int]]:
    """Rows ``(index, name, total_mb, used_mb)``; rows with ``[N/A]`` numbers are dropped."""
    rows = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        # The name may itself contain commas; the numbers are the outer fields.
        head = line.split(',', 1)
        tail = head[1].rsplit(',', 2) if len(head) == 2 else []
        if len(tail) != 3:
            continue
        index, total, used = _int_field(head[0]), _int_field(tail[1]), _int_field(tail[2])
        name = tail[0].strip()
        if index is None or total is None or used is None:
            continue
        if name.startswith('[') and name.endswith(']'):
            name = ''
        rows.append((index, name[:MAX_NAME_LENGTH], total, min(used, total)))
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


def nvidia_smi_executable() -> str:
    """``%SystemRoot%\\System32\\nvidia-smi.exe`` when present (Windows), else ``nvidia-smi``."""
    if _is_windows():
        system_root = os.environ.get('SystemRoot', r'C:\Windows')
        candidate = os.path.join(system_root, 'System32', 'nvidia-smi.exe')
        if os.path.isfile(candidate):
            return candidate
    return 'nvidia-smi'


def run_nvidia_smi(args: List[str]) -> Optional[str]:
    """stdout of a successful ``nvidia-smi`` call, else None. Never raises.

    ``args[0]`` is replaced by ``nvidia_smi_executable()``.
    """
    try:
        args = [nvidia_smi_executable()] + list(args[1:])
        result = subprocess.run(args, capture_output=True, text=True,
                                timeout=NVIDIA_SMI_TIMEOUT_SECONDS)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
        logger.debug("nvidia-smi failed: %s", exc)
        return None
    if result.returncode != 0:
        logger.debug("nvidia-smi exited %s: %s", result.returncode, (result.stderr or '').strip()[:200])
        return None
    return result.stdout


def sethlans_pids() -> frozenset:
    """The worker process and all its descendants (Blender, UE trees)."""
    try:
        me = psutil.Process(os.getpid())
        return frozenset([me.pid] + [child.pid for child in me.children(recursive=True)])
    except psutil.Error:
        return frozenset([os.getpid()])


def attribute(indices: List[int], run: Callable = run_nvidia_smi,
              pids: Callable[[], frozenset] = sethlans_pids) -> Dict[int, Optional[int]]:
    """``{index: sethlans_used_mb or None}``; None when the source cannot attribute.

    Always None on Windows (compute-apps cannot see D3D memory; see module doc).
    """
    unknown = {index: None for index in indices}
    if _is_windows():
        return unknown
    apps_out = run(QUERY_APPS)
    if apps_out is None:
        return unknown
    apps = parse_compute_apps(apps_out)
    if all(used is None for _uuid, _pid, used in apps):
        return unknown  # nothing listed, or WDDM [N/A] everywhere
    uuid_out = run(QUERY_UUIDS)
    uuid_to_index = parse_uuids(uuid_out) if uuid_out is not None else {}
    ours = pids()
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


def _gpu(index, name, total, used, sethlans) -> GpuMem:
    if sethlans is None:
        return GpuMem(index, name, total, used, None, None)
    sethlans = min(sethlans, total)
    return GpuMem(index, name, total, used, sethlans, max(0, used - sethlans))


def take_reading(override_mb: int, clock: Callable[[], float] = time.monotonic,
                 run: Callable = run_nvidia_smi,
                 pids: Callable[[], frozenset] = sethlans_pids) -> Optional[GpuMemoryReading]:
    """One reading, or None when there is no usable source. Never raises."""
    try:
        taken_at = clock()
        if override_mb > 0:
            return GpuMemoryReading(taken_at, SOURCE_OVERRIDE,
                                    (GpuMem(0, '', override_mb, 0, None, None),))
        out = run(QUERY_GPU)
        rows = parse_query_gpu(out) if out is not None else []
        if not rows:
            return None
        sethlans = attribute([row[0] for row in rows], run=run, pids=pids)
        gpus = tuple(_gpu(index, name, total, used, sethlans.get(index))
                     for index, name, total, used in rows)
        return GpuMemoryReading(taken_at, SOURCE_NVIDIA, gpus)
    except Exception:  # noqa: BLE001 - any failure means "no reading"
        logger.exception("GPU memory reading failed.")
        return None


def to_payload(reading: Optional[GpuMemoryReading],
               now: Optional[float] = None) -> Optional[dict]:
    """The heartbeat ``gpu_memory`` value (FR-RT-16 shape), or None."""
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
            })
    return {'schema': 1, 'source': reading.source, 'age_s': age, 'gpus': gpus}
