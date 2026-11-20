# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
GPU memory reader configuration, section ``[gpu_memory]``.

``source`` (env ``SETHLANS_GPU_MEMORY_SOURCE``) picks the reader's
backend: ``auto`` (default: the platform's candidates in order), one
backend name (only that backend is tried) or ``none`` (no reading). Any
other value logs a WARNING and counts as ``auto``.

Read through ``config.get_config_value`` (env > JSON config store >
legacy ``config.ini`` > default), once, at import; never raises.
``config.py`` is not modified.
"""
import logging
from typing import Any, Callable, Optional

from sethlans_worker_agent import config

logger = logging.getLogger(__name__)

SECTION = 'gpu_memory'
AUTO = 'auto'
NONE = 'none'
SOURCES = (AUTO, 'pdh', 'nvidia-smi', 'amdgpu-sysfs', NONE)

Getter = Callable[[str, str, Any], Any]


def _default_getter(section: str, key: str, default: Any) -> Any:
    return config.get_config_value(section, key, default)


def load_source(get_value: Optional[Getter] = None) -> str:
    """The validated ``[gpu_memory] source`` value. Never raises."""
    get_value = get_value or _default_getter
    try:
        raw = get_value(SECTION, 'source', AUTO)
    except Exception as exc:  # noqa: BLE001 - never raise at import
        logger.warning("Config 'gpu_memory.source' could not be read (%s); using '%s'.", exc, AUTO)
        return AUTO
    value = raw.strip().lower() if isinstance(raw, str) else None
    if value not in SOURCES:
        logger.warning("Config 'gpu_memory.source' has invalid value %r (allowed: %s); using '%s'.",
                       raw, ', '.join(SOURCES), AUTO)
        return AUTO
    return value


SOURCE: str = load_source()
