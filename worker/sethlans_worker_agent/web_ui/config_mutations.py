# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Runtime config mutation handlers for the worker web UI.

Extracted from server.py for file size compliance. Every mutator is
serialized on a single module-level lock to prevent race conditions
(e.g., two concurrent requests toggling FORCE_CPU/GPU both to True).
"""
import threading

from sethlans_worker_agent import config

# Lock serializing all config mutations.
_config_lock = threading.Lock()


def _validate_force_modes(force_cpu, force_gpu):
    """Enforce mutual exclusion: at most one can be True."""
    if force_cpu and force_gpu:
        raise ValueError("force_cpu and force_gpu cannot both be enabled")


def _parse_bool(value):
    """Parse a boolean from JSON payload."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ('true', '1', 'yes')
    return bool(value)


def _apply_gpu_mode_change(value):
    """Validate and apply a GPU_MODE config change. Caller holds _config_lock."""
    if not isinstance(value, str):
        raise ValueError('GPU_MODE must be a string')
    mode = value.strip().lower()
    if mode not in ('split', 'combined'):
        raise ValueError("GPU_MODE must be 'split' or 'combined'")
    config.GPU_MODE = mode


def apply_config_change(key, value):
    """Apply a validated config change. Raises ValueError on bad input."""
    with _config_lock:
        if key == 'POLLING_INTERVAL':
            val = int(value)
            if not (1 <= val <= 3600):
                raise ValueError('Polling interval must be 1-3600')
            config.JOB_POLLING_INTERVAL_SECONDS = val
        elif key == 'HEARTBEAT_INTERVAL':
            val = int(value)
            if not (1 <= val <= 3600):
                raise ValueError('Heartbeat interval must be 1-3600')
            config.HEARTBEAT_INTERVAL_SECONDS = val
        elif key == 'FORCE_CPU_ONLY':
            val = _parse_bool(value)
            new_gpu = config.FORCE_GPU_ONLY
            if val:
                new_gpu = False  # Mutual exclusion
            _validate_force_modes(val, new_gpu)
            config.FORCE_CPU_ONLY = val
            config.FORCE_GPU_ONLY = new_gpu
        elif key == 'FORCE_GPU_ONLY':
            val = _parse_bool(value)
            new_cpu = config.FORCE_CPU_ONLY
            if val:
                new_cpu = False  # Mutual exclusion
            _validate_force_modes(new_cpu, val)
            config.FORCE_CPU_ONLY = new_cpu
            config.FORCE_GPU_ONLY = val
        elif key == 'GPU_MODE':
            _apply_gpu_mode_change(value)
