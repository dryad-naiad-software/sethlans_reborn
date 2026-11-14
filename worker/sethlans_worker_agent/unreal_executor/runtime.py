# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unreal startup checks and process-wide Unreal state (Unreal foundation
FR-WD-6, FR-WD-7, FR-WC-3/4/6/7, FR-WD-10).

``startup()`` runs once per process and only when
``config_unreal.ENABLED`` is true (callers check that first, NF-6). In
order, stopping at the first failure:

1. ``SETHLANS_FORCE_GPU_INDEX`` set (D10f);
2. Windows remote symlink evaluation enabled (Sec N5; read once);
3. ``SETHLANS_UNREAL_SHARE`` invalid (FR-WC-7);
4. ``CACHE_DIR`` invalid, not ours, its ACL not verifiable (FR-WC-3/4);
5. the owner lock held by another process (FR-WC-6); only after the
   lock is held are the marker and the write test written (Sec S2).

These "static" reasons hold until restart; so does any reason set later
with ``disable_until_restart`` (e.g. surviving Unreal processes,
FR-UE-11). When none applies, the share-check thread and the GPU memory
sampler start (after one synchronous first sample). Engine detection
runs at startup in any case (so the heartbeat shows the installs) and
again every ``RESCAN_SECONDS``; each scan replaces an immutable tuple.
Dynamic reasons: zero supported installs, no GPU memory reading
ever since the first sample (F6), and a failed known-folder check
(FR-UE-27, ``isolation_state``; refreshed with each engine scan once the
farm profile is registered).

Locks: ``_start_lock`` serializes startup (nothing else is taken while
it is held except leaf locks of the helpers); ``_lock`` is a leaf lock
guarding the fields below.
"""
import logging
import sys
import threading
import time
from typing import Optional, Tuple

from sethlans_worker_agent import config_unreal, unreal_share
from sethlans_worker_agent.project_archive import owner_lock
from sethlans_worker_agent.unreal_executor import cache_dir, detection, isolation_state, symlink_policy, vram
from sethlans_worker_agent.unreal_executor.vram_sampler import VramSampler

logger = logging.getLogger(__name__)

FORCE_GPU_INDEX_REASON = "SETHLANS_FORCE_GPU_INDEX is set: Unreal needs the whole machine (phase 1)"
REMOTE_SYMLINK_REASON = "remote symlink evaluation is enabled"
NO_INSTALL_REASON = "no supported Unreal Engine install found"
NO_VRAM_REASON = "no GPU memory reading: install nvidia-smi or set SETHLANS_UNREAL_VRAM_MB"

_start_lock = threading.Lock()
_lock = threading.Lock()  # leaf lock
_started = False
_static_reason = ''
_prepared: Optional[cache_dir.PreparedCacheDir] = None
_sampler: Optional[VramSampler] = None
_installs: Tuple[detection.EngineInstall, ...] = ()
_scanned_at: Optional[float] = None
_scanning = False
_clock = time.monotonic


def _is_windows() -> bool:
    return sys.platform == 'win32'


def _check_platform_and_config() -> str:
    if config_unreal.force_gpu_index_set():
        return FORCE_GPU_INDEX_REASON
    try:
        if symlink_policy.remote_symlink_evaluation_enabled():
            return REMOTE_SYMLINK_REASON
    except Exception as exc:  # noqa: BLE001 - fail closed
        logger.error("Could not read the remote symlink evaluation policy: %s", exc)
        return REMOTE_SYMLINK_REASON
    try:
        unreal_share.configure(config_unreal.SHARE, config_unreal.SHARE_RECHECK_SECONDS,
                               config_unreal.SHARE_RECHECK_MAX_SECONDS,
                               config_unreal.SHARE_TIMEOUT_SECONDS)
    except ValueError as exc:
        return f"invalid SETHLANS_UNREAL_SHARE: {exc}"
    return ''


def _check_cache_dir() -> str:
    global _prepared
    if not _is_windows():
        return f"Unreal rendering is not implemented on {sys.platform} (phase 4)"
    try:
        prepared = cache_dir.prepare_cache_dir(config_unreal.CACHE_DIR)
    except cache_dir.CacheDirError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - fail closed, never crash the worker
        logger.exception("Unreal cache dir setup failed.")
        return f"cache dir setup failed ({type(exc).__name__})"
    if not owner_lock.acquire_for_process(prepared.path):
        return owner_lock.OWNER_LOCK_REASON
    try:  # marker and write test only while holding the owner lock (Sec S2)
        cache_dir.complete_cache_dir(prepared)
    except cache_dir.CacheDirError as exc:
        return str(exc)
    with _lock:
        _prepared = prepared
    return ''


def _start_threads() -> None:
    global _sampler
    unreal_share.start()
    sampler = VramSampler(lambda: vram.take_reading(config_unreal.VRAM_MB),
                          config_unreal.VRAM_SAMPLE_SECONDS)
    sampler.sample_once()  # the first report already knows whether a reading exists (F6)
    sampler.start(initial_sample_done=True)
    with _lock:
        _sampler = sampler


def startup() -> None:
    """Run the startup checks once (Unreal enabled only). Never raises."""
    global _started, _static_reason
    if not config_unreal.ENABLED:
        return
    with _start_lock:
        if _started:
            return
        try:
            reason = _check_platform_and_config() or _check_cache_dir()
            if not reason:
                _start_threads()
        except Exception as exc:  # noqa: BLE001 - fail closed
            logger.exception("Unreal startup failed.")
            reason = f"Unreal startup failed ({type(exc).__name__})"
        with _lock:
            _static_reason = reason
            _started = True
        if reason:
            logger.warning("Unreal rendering disabled: %s", reason)
        rescan(force=True)


def rescan(force: bool = False) -> Tuple[detection.EngineInstall, ...]:
    """Re-run detection when due (``RESCAN_SECONDS``); returns the current tuple."""
    global _installs, _scanned_at, _scanning
    with _lock:
        due = force or _scanned_at is None or _clock() - _scanned_at >= config_unreal.RESCAN_SECONDS
        if not due or _scanning:
            return _installs
        _scanning = True
    found = None
    try:
        found = tuple(detection.detect_engines(engine_paths=config_unreal.ENGINE_PATHS))
        # FR-UE-27: a no-op until the farm profile is registered. rescan() is already rate-limited
        # by _scanned_at, so the probe is forced here: one phase for both, and check_now()/register()
        # moving checked_at cannot double the probe period (Conc R2-M1). The probe (a child process,
        # at most 10 s) runs on whichever thread called rescan(): the heartbeat or the poll thread.
        isolation_state.refresh(force=True)
    except Exception:  # noqa: BLE001 - detection must not break the heartbeat
        logger.exception("Unreal engine detection or the known-folder refresh failed.")
    with _lock:
        _scanning = False
        _scanned_at = _clock()
        if found is not None:
            if found != _installs:
                logger.info("Unreal installs: %s", ', '.join(
                    f"{i.build or '?'} ({'supported' if i.supported else i.reason})" for i in found) or 'none')
            _installs = found
        return _installs


def installs() -> Tuple[detection.EngineInstall, ...]:
    return rescan()


def status(current: Tuple[detection.EngineInstall, ...]) -> Tuple[bool, str]:
    """``(enabled, disabled_reason)`` for the given installs."""
    with _lock:
        reason = _static_reason if _started else 'Unreal startup has not run'
        current_sampler = _sampler
    if reason:
        return False, reason
    if not any(install.supported for install in current):
        return False, NO_INSTALL_REASON
    if current_sampler is not None and current_sampler.never_succeeded():
        return False, NO_VRAM_REASON
    isolation_reason = isolation_state.reason()
    if isolation_reason:
        return False, isolation_reason
    return True, ''


def disable_until_restart(reason: str) -> None:
    """Disable Unreal for the rest of the process (e.g. FR-UE-11 survivors)."""
    global _static_reason
    with _lock:
        if not _static_reason:
            _static_reason = reason
    logger.error("Unreal rendering disabled until restart: %s", reason)


def static_reason() -> str:
    """The startup or ``disable_until_restart`` reason ('' while none applies)."""
    with _lock:
        return _static_reason if _started else 'Unreal startup has not run'


def sampler() -> Optional[VramSampler]:
    with _lock:
        return _sampler


def prepared_cache_dir() -> Optional[cache_dir.PreparedCacheDir]:
    with _lock:
        return _prepared


def reset_for_tests() -> None:
    """Forget all state and stop threads (test isolation only)."""
    global _started, _static_reason, _prepared, _sampler, _installs, _scanned_at, _scanning
    with _lock:
        old_sampler = _sampler
        _started, _static_reason, _prepared, _sampler = False, '', None, None
        _installs, _scanned_at, _scanning = (), None, False
    if old_sampler is not None:
        old_sampler.stop(timeout=5)
    unreal_share.reset_for_tests()
    isolation_state.reset_for_tests()
    owner_lock.release_for_process()
    recovery_gate = sys.modules.get('sethlans_worker_agent.unreal_executor.startup_recovery')  # imports this one
    if recovery_gate is not None:
        recovery_gate.reset_for_tests()
