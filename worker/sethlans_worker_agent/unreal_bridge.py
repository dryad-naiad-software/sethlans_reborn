# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Guarded entry points from the Blender-era worker into Unreal code
(Unreal foundation FR-WX-1, Dom N-5; FR-WX-14).

``job_processor``, ``job_claim`` and ``system_monitor`` call Unreal code
only through this module. Nothing from ``unreal_executor``,
``unreal_share`` or ``project_archive`` is imported at module level.
Each function wraps a function-local import plus the call in
``try/except Exception``; on failure it logs ERROR once per process (per
function) and returns a safe value, so it never raises into the poll
loop, the heartbeat or registration.
"""
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_failures_logged = set()
_failures_lock = threading.Lock()  # leaf lock


def _log_failure_once(name: str, exc: BaseException) -> None:
    with _failures_lock:
        if name in _failures_logged:
            return
        _failures_logged.add(name)
    logger.error(
        "Unreal support failed in %s (%s: %s); using the fallback value.",
        name, type(exc).__name__, exc,
    )


def _fallback_snapshot(exc: BaseException) -> dict:
    return {
        "schema": 1,
        "enabled": False,
        "disabled_reason": f"Unreal support failed to load: {type(exc).__name__}",
        "installs": [],
        "share": "",
        "share_reachable": False,
    }


def poll_params(node_idle: bool) -> dict:
    """Extra poll parameters for Unreal jobs (FR-WD-9); fallback ``{}``.

    ``{}`` keeps the poll parameters byte-identical to a Blender-only
    worker's. ``node_idle`` is ``capacity.has_no_reservations()``
    (FR-WX-13): Unreal is asked for only on a fully idle machine.
    """
    try:
        from sethlans_worker_agent.unreal_executor import capabilities
        return dict(capabilities.poll_params(node_idle=node_idle))
    except Exception as exc:
        _log_failure_once('poll_params', exc)
        return {}


def snapshot() -> dict:
    """``unreal_capabilities`` for registration and heartbeats (FR-WD-8)."""
    try:
        from sethlans_worker_agent.unreal_executor import capabilities
        return dict(capabilities.snapshot())
    except Exception as exc:
        _log_failure_once('snapshot', exc)
        return _fallback_snapshot(exc)


def gpu_memory_payload() -> Optional[dict]:
    """Heartbeat ``gpu_memory`` (FR-WD-8, FR-RT-16); ``None`` omits the key."""
    try:
        from sethlans_worker_agent.unreal_executor import capabilities
        payload = capabilities.gpu_memory_payload()
        return dict(payload) if payload is not None else None
    except Exception as exc:
        _log_failure_once('gpu_memory_payload', exc)
        return None


def drift_check_allowed() -> bool:
    """False while an exclusive (Unreal) reservation is held (FR-WX-14).

    The GPU drift check spawns a Blender ``detect_gpus.py`` subprocess
    that enumerates CUDA/OptiX devices for 5-15 s; it must not run while
    an Unreal render owns the machine. Fallback ``True`` (today's
    behavior). Takes ``WorkerCapacity._lock`` briefly and nothing else.
    """
    try:
        from sethlans_worker_agent import job_processor
        capacity = job_processor.get_worker_capacity()
        return capacity is None or not capacity.has_exclusive_reservation()
    except Exception as exc:
        _log_failure_once('drift_check_allowed', exc)
        return True
