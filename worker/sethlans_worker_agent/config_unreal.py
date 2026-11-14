# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unreal worker configuration, section ``[unreal]`` (Unreal foundation
FR-WC-1, FR-WC-2).

Read through ``config.get_config_value`` (env ``SETHLANS_UNREAL_<KEY>`` >
JSON config store > legacy ``config.ini`` > default) and validated with
``config._validate_int``. ``config.py`` is not modified; consumers import
this module directly and read its constants at call time.

Every value is read once, at import; changing one needs a worker
restart. A malformed value falls back to its default with a WARNING, and
this module never raises at import (Dom B-3).

NF-6: ``SHARE`` is read only when ``ENABLED`` is true. Here it is only
the raw string; ``unreal_share`` validates and canonicalizes it at
startup. ``CACHE_DIR`` is likewise only a path string here (no
filesystem call); ``unreal_executor.cache_dir`` checks it (FR-WC-3).
"""
import logging
from typing import Any, Callable, Dict, Optional

from sethlans_worker_agent import config

logger = logging.getLogger(__name__)

SECTION = 'unreal'
_TRUE = ('true', '1', 'yes', 'on')
_FALSE = ('false', '0', 'no', 'off', '')

# (KEY, default, minimum, maximum) for the plain int settings.
_INT_SETTINGS = (
    ('SHARE_RECHECK_SECONDS', 60, 10, 3600),
    ('SHARE_RECHECK_MAX_SECONDS', 900, 60, 86400),
    ('VRAM_SAMPLE_SECONDS', 30, 5, 600),
    ('CACHE_MAX_GB', 200, 10, 100000),
    ('MIN_FREE_GB', 20, 1, 10000),
    ('MAX_ARCHIVE_GB', 256, 1, 100000),
    ('MAX_ARCHIVE_UNCOMPRESSED_GB', 256, 1, 100000),
    ('PREP_TIMEOUT_SECONDS', 1200, 60, 86400),
    ('COPY_STALL_SECONDS', 120, 10, 3600),
    ('WARMUP_TIMEOUT_SECONDS', 3600, 60, 86400),
    ('RENDER_TIMEOUT_SECONDS', 14400, 60, 259200),
    ('HANG_SECONDS', 900, 120, 86400),
    ('SHARE_TIMEOUT_SECONDS', 5, 1, 60),
    ('POLL_PROBE_BUDGET_SECONDS', 5, 1, 60),
    ('WARMUP_HOLD_SECONDS', 240, 0, 3600),
    ('WARMUP_STEP_FRAMES', 24, 1, 1000),
    ('WARMUP_STEP_SECONDS', 4, 1, 600),
    ('KEEP_JOB_LOGS', 10, 0, 1000),
    ('RESCAN_SECONDS', 600, 60, 86400),
    ('CANCEL_POLL_SECONDS', 5, 1, 60),
    ('VRAM_MB', 0, 0, 1048576),
    ('QUIT_GRACE_SECONDS', 60, 10, 600),
    ('LOG_MAX_MB', 512, 16, 65536),
    ('LOG_FLOOD_MB_PER_MIN', 100, 10, 10000),
)
# FR-WC-2 / FR-UE-26: the farm-private zenserver's port; never UE's default 8558.
ZEN_PORT_DEFAULT = 8668
ZEN_PORT_FORBIDDEN = 8558
WORKCOPY_MODES = ('hardlink', 'copy')

Getter = Callable[[str, str, Any], Any]


def _default_getter(section: str, key: str, default: Any) -> Any:
    return config.get_config_value(section, key, default)


def _read(get_value: Getter, key: str, default: Any) -> Any:
    try:
        return get_value(SECTION, key.lower(), default)
    except Exception as exc:  # noqa: BLE001 - never raise at import
        logger.warning("Config 'unreal.%s' could not be read (%s); using the default.", key.lower(), exc)
        return default


def _as_int(get_value: Getter, key: str, default: int, low: int, high: int) -> int:
    raw = _read(get_value, key, default)
    name = f'unreal.{key.lower()}'
    if isinstance(raw, bool):
        value = None
    elif isinstance(raw, int):
        value = raw
    else:
        try:
            value = int(str(raw).strip())
        except ValueError:
            value = None
    if value is None:
        logger.warning("Config '%s' has non-integer value %r. Falling back to default: %s", name, raw, default)
        return default
    return config._validate_int(name, value, default, low, high)


def _as_bool(get_value: Getter, key: str, default: bool) -> bool:
    raw = _read(get_value, key, default)
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    logger.warning("Config 'unreal.%s' has non-boolean value %r. Using default: %s", key.lower(), raw, default)
    return default


def _as_str(get_value: Getter, key: str, default: str) -> str:
    raw = _read(get_value, key, default)
    if raw is None:
        return default
    if not isinstance(raw, str):
        logger.warning("Config 'unreal.%s' is not a string. Using default: %r", key.lower(), default)
        return default
    return raw


def default_cache_dir() -> str:
    """``<data_dir>/unreal`` (FR-WC-2); just a path string, nothing is created."""
    return str(config._DATA_DIR / 'unreal')


def _apply_cross_checks(values: Dict[str, Any]) -> None:
    base, ceiling = values['SHARE_RECHECK_SECONDS'], values['SHARE_RECHECK_MAX_SECONDS']
    if ceiling < base:
        logger.warning(
            "Config 'unreal.share_recheck_max_seconds' (%d) is below share_recheck_seconds (%d); using %d.",
            ceiling, base, base)
        values['SHARE_RECHECK_MAX_SECONDS'] = base
    floor = values['WARMUP_HOLD_SECONDS'] + 60
    if values['HANG_SECONDS'] <= floor:
        logger.warning(
            "Config 'unreal.hang_seconds' (%d) must exceed warmup_hold_seconds + 60; using %d.",
            values['HANG_SECONDS'], floor + 1)
        values['HANG_SECONDS'] = floor + 1
    if values['ZEN_PORT'] == ZEN_PORT_FORBIDDEN:
        logger.warning("Config 'unreal.zen_port' must not be %d (an artist's editor uses it); using %d.",
                       ZEN_PORT_FORBIDDEN, ZEN_PORT_DEFAULT)
        values['ZEN_PORT'] = ZEN_PORT_DEFAULT
    if values['WORKCOPY_MODE'] not in WORKCOPY_MODES:
        logger.warning("Config 'unreal.workcopy_mode' must be one of %s; using 'hardlink'.", WORKCOPY_MODES)
        values['WORKCOPY_MODE'] = 'hardlink'


def load(get_value: Optional[Getter] = None) -> Dict[str, Any]:
    """Read and validate every ``[unreal]`` setting. Never raises."""
    get_value = get_value or _default_getter
    enabled = _as_bool(get_value, 'ENABLED', False)
    values: Dict[str, Any] = {
        'ENABLED': enabled,
        # NF-6: the share is not even read while Unreal is disabled.
        'SHARE': _as_str(get_value, 'SHARE', '').strip() if enabled else '',
        'ENGINE_PATHS': _as_str(get_value, 'ENGINE_PATHS', ''),
        'CACHE_DIR': _as_str(get_value, 'CACHE_DIR', '') or default_cache_dir(),
        'WORKCOPY_MODE': _as_str(get_value, 'WORKCOPY_MODE', 'hardlink').strip().lower(),
    }
    for key, default, low, high in _INT_SETTINGS:
        values[key] = _as_int(get_value, key, default, low, high)
    values['ZEN_PORT'] = _as_int(get_value, 'ZEN_PORT', ZEN_PORT_DEFAULT, 1024, 65535)
    _apply_cross_checks(values)
    return values


_values = load()

ENABLED: bool = _values['ENABLED']
SHARE: str = _values['SHARE']
ENGINE_PATHS: str = _values['ENGINE_PATHS']
CACHE_DIR: str = _values['CACHE_DIR']
WORKCOPY_MODE: str = _values['WORKCOPY_MODE']
SHARE_RECHECK_SECONDS: int = _values['SHARE_RECHECK_SECONDS']
SHARE_RECHECK_MAX_SECONDS: int = _values['SHARE_RECHECK_MAX_SECONDS']
VRAM_SAMPLE_SECONDS: int = _values['VRAM_SAMPLE_SECONDS']
CACHE_MAX_GB: int = _values['CACHE_MAX_GB']
MIN_FREE_GB: int = _values['MIN_FREE_GB']
MAX_ARCHIVE_GB: int = _values['MAX_ARCHIVE_GB']
MAX_ARCHIVE_UNCOMPRESSED_GB: int = _values['MAX_ARCHIVE_UNCOMPRESSED_GB']
PREP_TIMEOUT_SECONDS: int = _values['PREP_TIMEOUT_SECONDS']
COPY_STALL_SECONDS: int = _values['COPY_STALL_SECONDS']
WARMUP_TIMEOUT_SECONDS: int = _values['WARMUP_TIMEOUT_SECONDS']
RENDER_TIMEOUT_SECONDS: int = _values['RENDER_TIMEOUT_SECONDS']
HANG_SECONDS: int = _values['HANG_SECONDS']
SHARE_TIMEOUT_SECONDS: int = _values['SHARE_TIMEOUT_SECONDS']
POLL_PROBE_BUDGET_SECONDS: int = _values['POLL_PROBE_BUDGET_SECONDS']
WARMUP_HOLD_SECONDS: int = _values['WARMUP_HOLD_SECONDS']
WARMUP_STEP_FRAMES: int = _values['WARMUP_STEP_FRAMES']
WARMUP_STEP_SECONDS: int = _values['WARMUP_STEP_SECONDS']
KEEP_JOB_LOGS: int = _values['KEEP_JOB_LOGS']
RESCAN_SECONDS: int = _values['RESCAN_SECONDS']
CANCEL_POLL_SECONDS: int = _values['CANCEL_POLL_SECONDS']
VRAM_MB: int = _values['VRAM_MB']
QUIT_GRACE_SECONDS: int = _values['QUIT_GRACE_SECONDS']
LOG_MAX_MB: int = _values['LOG_MAX_MB']
LOG_FLOOD_MB_PER_MIN: int = _values['LOG_FLOOD_MB_PER_MIN']
ZEN_PORT: int = _values['ZEN_PORT']


def force_gpu_index_set() -> bool:
    """True when ``SETHLANS_FORCE_GPU_INDEX`` is set (D10f disables Unreal)."""
    value = config.FORCE_GPU_INDEX
    return value is not None and str(value).strip() != ''
