# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Startup recovery wiring (Unreal foundation FR-PA-18, FR-UE-23, FR-UE-27;
chunk 7 integration requirements 1 and 5).

``run_once()`` is called by ``capabilities.startup()`` (reached from the
agent's startup hook ``unreal_bridge.startup()``, before the first poll),
right after ``runtime.startup()``. It does
nothing when Unreal is disabled by config (NF-6) or the startup checks
failed. Otherwise, exactly once per process:

1. ``isolation_state.expect()`` (the capabilities report says "not run
   yet" until the first known-folder check);
2. ``recovery.recover(prepared, limits.cache, keep_job_logs=KEEP_JOB_LOGS,
   sweep=_sweep)``, where ``_sweep`` is the no-argument closure calling
   ``orphans.sweep(CACHE_DIR, engine roots, worker SID)``; when it leaves
   survivors it disables Unreal until restart and raises, so recovery
   deletes nothing (FR-UE-23);
3. FR-PA-18 step 1b: a leftover ``userstate/`` goes to ``trash/``; the farm
   profile is repaired/created (``user_state.repair_profile``) and the
   farm Zen folders created; then ``isolation_state.register(P)`` runs the
   first known-folder check, and the profile housekeeping runs.

A failed recovery (or ``CacheUnavailableError``) calls
``runtime.disable_until_restart``. ``ready()`` is True only after a
successful recovery: ``capabilities.poll_params`` reports ``UNREAL`` only
then (requirement 1), and ``try_reserve`` refuses work before it.
``cache()`` is the recovered ``ArchiveCache`` the executor uses.

Locking: ``_lock`` is a leaf lock over the module state; recovery runs
under ``_run_lock`` (startup only), never while another worker lock is held.
"""
import logging
import sys
import threading
from pathlib import Path
from typing import Optional

from sethlans_worker_agent import config_unreal
from sethlans_worker_agent.project_archive import fs_safe, limits, recovery
from sethlans_worker_agent.project_archive.cache import ArchiveCache
from sethlans_worker_agent.project_archive.errors import CacheUnavailableError
from sethlans_worker_agent.unreal_executor import isolation_state, orphans, runtime, user_state, zen_private
from sethlans_worker_agent.unreal_executor.runner_types import SURVIVORS_DISABLED_REASON

logger = logging.getLogger(__name__)

LEGACY_USERSTATE = 'userstate'

_run_lock = threading.Lock()
_lock = threading.Lock()  # leaf lock
_state: Optional[str] = None  # None | 'ready' | 'failed'
_cache: Optional[ArchiveCache] = None


def worker_sid() -> Optional[str]:
    if sys.platform != 'win32':
        return None
    from shared import owner_only_acl
    return owner_only_acl.current_user_sid()


def engine_roots():
    return [install.root for install in runtime.installs() if install.root is not None]


class SweepSurvivorsError(RuntimeError):
    """The orphan sweep left processes alive: recovery must delete nothing (review L5)."""


def make_sweep(cache_dir, roots, sid):
    """The FR-UE-23 closure ``recover`` calls with no arguments.

    With survivors it disables Unreal until restart and then **raises**, so
    ``recover`` takes its fail-closed path: nothing is deleted and the
    ``procs.json`` ledgers stay for the next start's sweep.
    """
    def _sweep():
        result = orphans.sweep(cache_dir, roots, sid)
        if result.survivors:
            runtime.disable_until_restart(SURVIVORS_DISABLED_REASON)
            raise SweepSurvivorsError(f"orphaned Unreal processes survived the sweep: {result.survivors}")
        return result
    return _sweep


def _step_1b(cache_dir: Path) -> None:
    trash = cache_dir / 'trash'
    leftover = cache_dir / LEGACY_USERSTATE
    if fs_safe.lexists(leftover) and fs_safe.move_aside(leftover, trash) is None:
        logger.warning("Could not move the leftover %s to trash.", leftover)
    profile = Path(user_state.profile_dir(cache_dir))
    user_state.repair_profile(profile, trash)
    zen_private.ensure_zen_dirs(cache_dir)
    isolation_state.register(profile)
    user_state.housekeeping(profile)


def _set(state: str, cache: Optional[ArchiveCache] = None) -> None:
    global _state, _cache
    with _lock:
        _state, _cache = state, cache


def _fail(reason: str) -> None:
    runtime.disable_until_restart(reason)
    _set('failed')


def _run(prepared) -> None:
    isolation_state.expect()
    cache_dir = Path(prepared.path)
    sweep = make_sweep(cache_dir, engine_roots(), worker_sid())
    result = recovery.recover(prepared, limits.from_config().cache, keep_job_logs=config_unreal.KEEP_JOB_LOGS,
                              sweep=sweep)
    if result.reason or result.cache is None:
        _fail(result.reason or 'Unreal cache recovery failed')
        return
    try:
        _step_1b(cache_dir)
    except CacheUnavailableError as exc:
        _fail(str(exc))
        return
    except OSError as exc:
        _fail(f"the farm profile could not be prepared ({exc})")
        return
    _set('ready', result.cache)
    logger.info("Unreal cache recovery finished; Unreal jobs can be polled.")


def run_once() -> None:
    """FR-PA-18 once per process (Unreal enabled and started only). Never raises."""
    if not config_unreal.ENABLED:
        return
    with _run_lock:
        with _lock:
            if _state is not None:
                return
        prepared = runtime.prepared_cache_dir()
        if prepared is None or runtime.static_reason():
            _set('failed')  # the startup checks failed: nothing to recover (Unreal stays disabled)
            return
        try:
            _run(prepared)
        except Exception as exc:  # noqa: BLE001 - fail closed, never crash the worker
            logger.exception("Unreal startup recovery failed.")
            _fail(f"Unreal startup recovery failed ({type(exc).__name__})")


def ready() -> bool:
    with _lock:
        return _state == 'ready'


def cache() -> Optional[ArchiveCache]:
    with _lock:
        return _cache


def mark_ready_for_tests(archive_cache: Optional[ArchiveCache] = None) -> None:
    _set('ready', archive_cache)


def reset_for_tests() -> None:
    _set(None, None)
