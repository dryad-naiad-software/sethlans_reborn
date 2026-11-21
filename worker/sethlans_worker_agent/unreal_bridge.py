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
import sys
import threading
from typing import Optional, Tuple

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


def poll_params(can_take: bool) -> dict:
    """Extra poll parameters for Unreal jobs (FR-WD-9); fallback ``{}``.

    ``{}`` keeps the poll parameters byte-identical to a Blender-only
    worker's. ``can_take`` is ``can_take_unreal()`` (render per card
    FR-PC-CAP-5): Unreal is asked for while an Unreal card is free.
    """
    try:
        from sethlans_worker_agent.unreal_executor import capabilities
        return dict(capabilities.poll_params(can_take=can_take))
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


def startup() -> None:
    """Agent startup hook: run the Unreal startup checks once (FR-WD-6).

    No-op when Unreal is disabled (NF-6). The first heartbeat or poll
    would run them lazily otherwise; calling this before registration
    makes the first ``unreal_capabilities`` complete.
    """
    try:
        from sethlans_worker_agent.unreal_executor import capabilities
        capabilities.startup()
    except Exception as exc:
        _log_failure_once('startup', exc)


def unreal_card_in_use() -> Optional[Tuple[int, str]]:
    """``(job id, card key)`` of the card the running Unreal job chose at its latest launch (C-24 m).

    ``None`` when no Unreal job has chosen a card; the module is looked up,
    never imported here, so a worker that never ran Unreal imports nothing.
    Fallback ``None``.
    """
    try:
        module = sys.modules.get('sethlans_worker_agent.unreal_executor.adapter_choice')
        choice = module.current() if module is not None else None
        return None if choice is None else (choice.job_id, choice.key)
    except Exception as exc:
        _log_failure_once('unreal_card_in_use', exc)
        return None


def can_take_unreal() -> bool:
    """True when an Unreal job may be claimed now (render per card FR-PC-CAP-5); fallback ``False``.

    A free Unreal card in use (``cards.unreal_cards.current_keys`` from the
    cached card probe: no probe on the poll path), no Blender CPU job and
    no pending rebuild (``WorkerCapacity.can_take_unreal``). The poll, the
    polling gate and the reserver's check 0 all use it. Takes the card
    store's lock, then ``WorkerCapacity._lock``, one after the other.
    """
    try:
        from sethlans_worker_agent import job_processor
        from sethlans_worker_agent.cards import unreal_cards
        capacity = job_processor.get_worker_capacity()
        return capacity is not None and capacity.can_take_unreal(unreal_cards.current_keys(cached_only=True))
    except Exception as exc:
        _log_failure_once('can_take_unreal', exc)
        return False


def drift_check_allowed() -> bool:
    """False while any Unreal job holds a card (FR-WX-14, render per card FR-PC-CAP-6, D15).

    The GPU drift check spawns a Blender ``detect_gpus.py`` subprocess
    that enumerates CUDA/OptiX devices on every card for 5-15 s; it must
    not run beside an Unreal render. Fallback ``True`` (today's behavior).
    Takes ``WorkerCapacity._lock`` briefly and nothing else.
    """
    try:
        from sethlans_worker_agent import job_processor
        capacity = job_processor.get_worker_capacity()
        return capacity is None or not capacity.has_unreal_job()
    except Exception as exc:
        _log_failure_once('drift_check_allowed', exc)
        return True
