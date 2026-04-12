# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Idle detection and scheduling configuration (FR-1 through FR-8).

Separated from ``config.py`` to keep both modules under the 300-line
ceiling. All values are re-exported by ``config`` for a flat API.
"""
import logging
import re

logger = logging.getLogger(__name__)

_APP_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9._-]+$')

_DEFAULT_CREATIVE_APPS = [
    "blender", "maya", "houdini", "nuke", "premiere",
    "afterfx", "resolve", "cinema4d", "modo",
]


def load_idle_config(get_config_value, _validate_int):
    """Load and validate all idle detection config values.

    Returns a dict of config key -> value.
    """
    raw_enabled = get_config_value('idle_detection', 'enabled', 'true')
    enabled = (
        raw_enabled.lower() in ('true', '1', 'yes')
        if isinstance(raw_enabled, str) else bool(raw_enabled)
    )

    threshold = get_config_value(
        'idle_detection', 'idle_threshold_seconds', 900, is_int=True,
    )
    threshold = _validate_int(
        'idle_detection.idle_threshold_seconds', threshold, 900, 1, 86400,
    )

    gpu_threshold = get_config_value(
        'idle_detection', 'gpu_utilization_threshold', 20, is_int=True,
    )
    gpu_threshold = _validate_int(
        'idle_detection.gpu_utilization_threshold',
        gpu_threshold, 20, 0, 100,
    )

    cpu_threshold = get_config_value(
        'idle_detection', 'cpu_utilization_threshold', 70, is_int=True,
    )
    cpu_threshold = _validate_int(
        'idle_detection.cpu_utilization_threshold',
        cpu_threshold, 70, 0, 100,
    )

    slow_path = get_config_value(
        'idle_detection', 'slow_path_threshold_seconds', 90, is_int=True,
    )
    slow_path = _validate_int(
        'idle_detection.slow_path_threshold_seconds',
        slow_path, 90, 10, 3600,
    )

    grace_cap = get_config_value(
        'idle_detection', 'grace_period_cap_seconds', 120, is_int=True,
    )
    grace_cap = _validate_int(
        'idle_detection.grace_period_cap_seconds',
        grace_cap, 120, 10, 600,
    )

    creative_apps = _load_creative_app_names(get_config_value)

    return {
        'IDLE_DETECTION_ENABLED': enabled,
        'IDLE_THRESHOLD_SECONDS': threshold,
        'IDLE_GPU_UTILIZATION_THRESHOLD': gpu_threshold,
        'IDLE_CPU_UTILIZATION_THRESHOLD': cpu_threshold,
        'IDLE_SLOW_PATH_THRESHOLD_SECONDS': slow_path,
        'IDLE_GRACE_PERIOD_CAP_SECONDS': grace_cap,
        'IDLE_CREATIVE_APP_NAMES': creative_apps,
    }


def _load_creative_app_names(get_config_value):
    """Load and validate creative_app_names from config."""
    raw = get_config_value(
        'idle_detection', 'creative_app_names', None,
    )
    if raw is None:
        return list(_DEFAULT_CREATIVE_APPS)
    # Env var: comma-separated string
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.split(',') if x.strip()]
    if not isinstance(raw, list):
        return list(_DEFAULT_CREATIVE_APPS)
    validated = []
    for entry in raw:
        entry = str(entry).strip()
        if not entry:
            continue
        if not _APP_NAME_PATTERN.match(entry):
            logger.warning(
                "Rejecting creative_app_names entry %r: "
                "contains characters outside [a-zA-Z0-9._-].", entry,
            )
            continue
        validated.append(entry)
    return validated if validated else list(_DEFAULT_CREATIVE_APPS)


def load_schedule_config(json_config):
    """Load the scheduling.claim_window config from the JSON store."""
    scheduling = json_config.get('scheduling')
    if isinstance(scheduling, dict):
        return scheduling.get('claim_window', {})
    return {}


def get_schedule_config_live(config_store):
    """Return the claim_window config, re-reading from the store.

    Called by YieldMonitor on each poll to support live schedule changes.
    """
    fresh = config_store.load()
    scheduling = fresh.get('scheduling')
    if isinstance(scheduling, dict):
        return scheduling.get('claim_window', {})
    return {}
