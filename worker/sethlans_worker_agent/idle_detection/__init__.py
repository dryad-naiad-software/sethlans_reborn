# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Idle detection package -- public API.

Provides ``is_idle()`` for the main loop's claim gate and
``get_worker_runtime_state()`` for status reporting. Includes a
cooldown mechanism: when a yield trigger fires, ``is_idle()`` returns
False for at least one full idle polling interval (10 seconds) to
prevent the race where the worker immediately re-claims after yield.

Initializes the appropriate platform backend at first call.
"""
import logging
import threading
import time
from typing import Optional

from sethlans_worker_agent.idle_detection.headless import is_headless
from sethlans_worker_agent.idle_detection.input_monitor import (
    get_seconds_since_last_input,
)
from sethlans_worker_agent.idle_detection.contention import (
    check_gpu_contention,
    check_cpu_contention,
)
from sethlans_worker_agent.idle_detection.schedule import (  # noqa: F401
    is_inside_claim_window,
)
from sethlans_worker_agent.idle_detection.yield_monitor import (  # noqa: F401
    YieldMonitor,
    YieldReason,
)
from sethlans_worker_agent.idle_detection.session_win32 import (  # noqa: F401
    start_session_monitor,
)

logger = logging.getLogger(__name__)

# Idle detection polling interval (not configurable in v1).
IDLE_POLL_INTERVAL_SECONDS = 10

# Cooldown: after a yield trigger, force "not idle" for at least one
# full polling interval to prevent immediate re-claim (FR-11).
_cooldown_lock = threading.Lock()
_cooldown_until: float = 0.0

# Cached headless detection result (evaluated once at first call).
_headless_result: Optional[bool] = None
_headless_lock = threading.Lock()

# Track idle gate state for transition logging (FR-11c).
_last_idle_result: Optional[bool] = None


def set_yield_cooldown() -> None:
    """Called when a yield trigger fires.

    Forces ``is_idle()`` to return False for at least one full idle
    polling interval, preventing the race where the worker re-claims
    immediately after yielding.
    """
    with _cooldown_lock:
        global _cooldown_until
        _cooldown_until = time.monotonic() + IDLE_POLL_INTERVAL_SECONDS


def _is_in_cooldown() -> bool:
    """Return True if we are within the post-yield cooldown window."""
    with _cooldown_lock:
        return time.monotonic() < _cooldown_until


def _get_headless() -> bool:
    """Lazy-evaluate and cache headless detection."""
    global _headless_result
    with _headless_lock:
        if _headless_result is None:
            _headless_result = is_headless()
        return _headless_result


def is_idle() -> bool:
    """Return True if the machine is idle and no contention is detected.

    Checks (all must pass):
    1. Not in post-yield cooldown
    2. Idle detection is enabled (not headless, not disabled by config)
    3. Input idle time exceeds threshold
    4. No GPU contention
    5. No CPU contention

    Returns True immediately on headless hosts or when idle detection
    is explicitly disabled.
    """
    global _last_idle_result

    from sethlans_worker_agent import config

    # Headless hosts: always idle (FR-3b)
    if _get_headless():
        return True

    # Config disabled: always idle (FR-3d)
    if not config.IDLE_DETECTION_ENABLED:
        return True

    # Post-yield cooldown: NOT idle
    if _is_in_cooldown():
        _log_transition(False, "post-yield cooldown active")
        return False

    # Input idle check
    idle_seconds = get_seconds_since_last_input()
    if idle_seconds is None:
        # API unavailable: graceful degradation to schedule-only mode
        logger.debug("Input idle detection unavailable, treating as idle.")
        return True

    threshold = config.IDLE_THRESHOLD_SECONDS
    if idle_seconds < threshold:
        _log_transition(False, f"input {idle_seconds:.0f}s < threshold {threshold}s")
        return False

    # GPU contention check
    if check_gpu_contention(config.IDLE_GPU_UTILIZATION_THRESHOLD):
        _log_transition(False, "GPU contention detected")
        return False

    # CPU contention check
    if check_cpu_contention(config.IDLE_CPU_UTILIZATION_THRESHOLD):
        _log_transition(False, "CPU contention detected")
        return False

    _log_transition(True, "machine idle")
    return True


def _log_transition(current: bool, reason: str) -> None:
    """Log idle gate state transitions at INFO, steady state at DEBUG."""
    global _last_idle_result
    if _last_idle_result is not None and _last_idle_result != current:
        logger.info("Idle gate: %s (%s)", "passing" if current else "blocking", reason)
    else:
        logger.debug("Idle gate: %s (%s)", "passing" if current else "blocking", reason)
    _last_idle_result = current


def get_worker_runtime_state() -> str:
    """Return a human-readable runtime state for status reporting.

    Returns one of: 'idle_available', 'idle_waiting', 'headless',
    'disabled', 'contention', 'active'.
    """
    from sethlans_worker_agent import config

    if _get_headless():
        return "headless"
    if not config.IDLE_DETECTION_ENABLED:
        return "disabled"
    if _is_in_cooldown():
        return "idle_waiting"

    idle_seconds = get_seconds_since_last_input()
    if idle_seconds is None:
        return "disabled"

    if idle_seconds < config.IDLE_THRESHOLD_SECONDS:
        return "active"

    if check_gpu_contention(config.IDLE_GPU_UTILIZATION_THRESHOLD):
        return "contention"
    if check_cpu_contention(config.IDLE_CPU_UTILIZATION_THRESHOLD):
        return "contention"

    return "idle_available"
