# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
GPU and CPU contention checks (FR-2).

Used by the idle detection claim gate to prevent claiming when
the machine is under external load even if keyboard/mouse is idle.

IMPORTANT: The ``_cpu_samples`` deque is used ONLY by idle detection
polling (10s interval). The YieldMonitor has its own separate sample
buffer. These must not be shared.
"""
import collections
import logging
import subprocess
import sys
from typing import Optional

import psutil

logger = logging.getLogger(__name__)

# Rolling 3-sample CPU average for idle detection (30s at 10s intervals).
# NOT shared with YieldMonitor -- it has its own buffer.
_cpu_samples: collections.deque = collections.deque(maxlen=3)


def check_gpu_contention(threshold_pct: int = 20) -> bool:
    """Return True if any GPU is above threshold utilization.

    NVIDIA: calls nvidia-smi --query-gpu=utilization.gpu.
    AMD: calls rocm-smi --showuse with a higher threshold (50%).
    macOS: skipped in v1 (no reliable cross-vendor CLI).

    Returns False (no contention) if neither tool is available.
    """
    if sys.platform == "darwin":
        return False

    result = _check_nvidia_gpu_contention(threshold_pct)
    if result is not None:
        return result

    result = _check_amd_gpu_contention(max(threshold_pct, 50))
    if result is not None:
        return result

    return False


def _check_nvidia_gpu_contention(threshold_pct: int) -> Optional[bool]:
    """Check NVIDIA GPU utilization via nvidia-smi."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                utilization = int(line)
                if utilization > threshold_pct:
                    return True
            except ValueError:
                continue
        return False
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def _check_amd_gpu_contention(threshold_pct: int) -> Optional[bool]:
    """Check AMD GPU utilization via rocm-smi (aggregate only)."""
    try:
        result = subprocess.run(
            ["rocm-smi", "--showuse"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if "%" in line:
                # Parse lines like "GPU[0] : GPU use(%): 25"
                parts = line.split(":")
                for part in reversed(parts):
                    cleaned = part.strip().rstrip("%").strip()
                    try:
                        utilization = int(cleaned)
                        if utilization > threshold_pct:
                            return True
                    except ValueError:
                        continue
        return False
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def check_cpu_contention(threshold_pct: int = 70) -> bool:
    """Return True if rolling CPU average exceeds threshold.

    Uses psutil.cpu_percent(interval=None) called each poll cycle.
    Maintains a 3-sample rolling window (30 seconds at 10s intervals).
    """
    sample = psutil.cpu_percent(interval=None)
    _cpu_samples.append(sample)

    if len(_cpu_samples) < 3:
        return False

    avg = sum(_cpu_samples) / len(_cpu_samples)
    return avg > threshold_pct


def check_gpu_contention_per_process(
    exclude_pid: Optional[int] = None,
) -> bool:
    """Check if any non-Sethlans GPU process is active (NVIDIA only).

    Uses nvidia-smi --query-compute-apps for per-process GPU data.
    Returns False on non-NVIDIA or if nvidia-smi is not available.
    """
    if sys.platform == "darwin":
        return False
    pids = _get_nvidia_compute_pids()
    for pid in pids:
        if exclude_pid is not None and pid == exclude_pid:
            continue
        return True
    return False


def _get_nvidia_compute_pids():
    """Return a list of PIDs using NVIDIA compute (CUDA/OptiX)."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,used_memory",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return []
        pids = []
        for line in result.stdout.strip().splitlines():
            parts = line.strip().split(",")
            if parts:
                try:
                    pids.append(int(parts[0].strip()))
                except ValueError:
                    pass
        return pids
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
