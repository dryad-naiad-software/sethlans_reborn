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

* **Windows: always ``None``.** No ``nvidia-smi`` listing is a valid
  source for D3D processes there (under WDDM they are listed with
  ``[N/A]``); the ``pdh`` backend attributes on Windows.
* Elsewhere: ``nvidia-smi -q -d PIDS``, which lists graphics (Vulkan) and
  compute processes, parsed by ``nvidia_pids`` (pid and memory pairs inside
  a bus-id block only; process names are never kept). Summed over the
  worker's process tree, per card key. A card is attributed only when every
  process listed on it has a number; a card the listing does not show is
  not attributed.
* ``per_pid`` (render per card FR-PC-ATT-1, ``attribute_pids``): the same
  listing per pid, card key -> pid -> MiB, for the given pids only and only
  for attributed cards (a missing key is not attributed). ``None`` on Windows
  (never 0) and when the call fails. The Linux backend leaves it ``None``.
"""
import logging
import os
import subprocess
import sys
import time
from typing import Callable, Dict, FrozenSet, Iterable, List, Optional, Tuple

from sethlans_worker_agent.cards.identity import card_key, clean_name
from sethlans_worker_agent.gpu_memory.nvidia_pids import parse_pids
from sethlans_worker_agent.gpu_memory.types import MAX_REPORTED_GPUS, GpuMemoryReading, make_gpu

logger = logging.getLogger(__name__)

SOURCE = 'nvidia-smi'
NVIDIA_SMI_TIMEOUT_SECONDS = 10
MAX_STDOUT_CHARS = 1024 * 1024  # larger output is a failed call, never parsed in part
QUERY_GPU = ['nvidia-smi', '--query-gpu=index,pci.bus_id,memory.total,memory.used,name',
             '--format=csv,noheader,nounits']
QUERY_PIDS = ['nvidia-smi', '-q', '-d', 'PIDS']

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


def query_cards(run: Callable = run_nvidia_smi) -> Optional[List[Row]]:
    """The card query's rows, or ``None`` when the call failed."""
    out = _capped(run(QUERY_GPU))
    return parse_query_gpu(out) if out is not None else None


PerPid = Dict[str, Dict[int, int]]


def attribute_pids(keys: Iterable[Optional[str]], ours: FrozenSet[int], run: Callable = run_nvidia_smi,
                   known_keys: Optional[Iterable[Optional[str]]] = None) -> Optional[PerPid]:
    """``{card_key: {pid: MiB}}`` of the given pids on the given, attributable cards (``None`` keys skipped).

    ``None`` when nothing can be attributed: on Windows (module doc), with no key, or when the call fails.
    A card the listing cannot attribute is left out. One ``nvidia-smi -q -d PIDS`` call. ``known_keys``
    (default ``keys``): every key of the card query; a listing block outside them distrusts the whole listing.
    """
    wanted = [key for key in keys if key is not None]
    if _is_windows() or not wanted:
        return None
    out = _capped(run(QUERY_PIDS))
    if out is None:
        return None
    known = set(wanted) if known_keys is None else {key for key in known_keys if key is not None}
    listed = parse_pids(out, known_keys=known)
    return {key: {pid: used for pid, used in listed[key] if pid in ours}
            for key in wanted if listed.get(key) is not None}


def sums(keys: Iterable[Optional[str]], per_pid: Optional[PerPid]) -> Dict[str, Optional[int]]:
    """``{card_key: MiB of every listed pid, or None when not attributed}`` for the given keys."""
    per_pid = per_pid or {}
    return {key: (sum(per_pid[key].values()) if key in per_pid else None) for key in keys if key is not None}


def attribute(keys: Iterable[Optional[str]], ours: FrozenSet[int], run: Callable = run_nvidia_smi,
              known_keys: Optional[Iterable[Optional[str]]] = None) -> Dict[str, Optional[int]]:
    """``{card_key: sethlans_used_mb or None}`` for the given keys (``None`` keys skipped).

    None when the source cannot attribute the card; always None on Windows
    (see module doc). One ``nvidia-smi -q -d PIDS`` call (``attribute_pids``).
    """
    keys = list(keys)
    return sums(keys, attribute_pids(keys, ours, run=run, known_keys=known_keys))


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
        every_row = query_cards(self._run) or []
        rows = every_row[:MAX_REPORTED_GPUS]
        if not rows:
            return None
        keys = [row[4] for row in rows]
        per_pid = attribute_pids(keys, pids, run=self._run,
                                 known_keys=[row[4] for row in every_row])  # cards past the cap are still known
        sethlans = sums(keys, per_pid)
        gpus = tuple(make_gpu(index, name, total, used, sethlans.get(key) if key else None, key)
                     for index, name, total, used, key in rows)
        return GpuMemoryReading(taken_at, SOURCE, gpus, measures_card=True,
                                measures_process=all(gpu.sethlans_used_mb is not None for gpu in gpus),
                                per_pid=per_pid)

    def close(self) -> None:
        """Nothing is held between samples."""
