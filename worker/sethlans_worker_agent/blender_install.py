# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Installing one Blender version: the single entry point (BS-WK-1, BS-WK-3, BS-WK-4).

``install(version, blocking=...)`` is used by every installer: version sync
(main loop, ``blocking=False``) and the setup page's download thread
(``blocking=True``). The job path never installs (BS-WK-11).

Steps, under a per-version lock: check "installed" again after taking the
lock (``ToolManager._installed_exe_path``, the plain file check), mark the
version in ``ToolManager._downloading_versions`` (so a scan and
``get_blender_executable_path`` never report a half-extracted folder), get
the verified archive into ``<tools>/blender/``, then extract it there and
set the execute bit off Windows. A failed or interrupted extraction removes
``<tools>/blender/blender-<version>-<platform>`` before the mark is cleared.

The archive comes from the manager first (``blender_from_manager.fetch``,
pinned session, the manager's official SHA-256; BS-WK-4..7). Only when the
manager cannot supply it does the online step run: ``find_build`` (release
list, cached in ``config.BLENDER_VERSIONS_CACHE_FILE``) and
``download_build`` (fallback over the sources, official SHA-256), each with
its own plain session, never the manager's (BS-WK-5). With
``wait_for_manager`` (version sync, BS-WK-10) a manager still fetching the
build ends the install at once with ``WAITING`` and no online request; the
caller asks again later. ``InstallResult.manager_state`` is the manager's
answer (``"ready"``, ``"queued"``, ``"fetching"``, ``"failed"``,
``"not_wanted"``), or None when it gave none or was not asked.

A non-blocking call that finds the lock held returns ``BUSY`` at once:
version sync skips the version this round, with no back-off. A blocking
call waits for the lock in 0.5 s steps and returns ``STOPPED`` when ``stop``
is set meanwhile.

Locks: ``_locks_guard`` guards the lock table only and is never held with
another lock. A version lock is held for the whole install; inside it only
``ToolManager._download_lock`` (a leaf) is taken, briefly.
"""
import enum
import logging
import os
import platform
import shutil
import stat
import threading
from dataclasses import dataclass, replace
from typing import Callable, Dict, Optional

from shared.blender_sources import (
    BlenderSourcesError, DownloadStoppedError, current_platform_id, download_build, find_build, is_version,
)
from sethlans_worker_agent import blender_from_manager, config
from sethlans_worker_agent.blender_from_manager import ManagerAnswer
from sethlans_worker_agent.tool_manager import tool_manager_instance
from sethlans_worker_agent.utils import file_operations

logger = logging.getLogger(__name__)

_locks_guard = threading.Lock()  # guards _version_locks only
_version_locks: Dict[str, threading.Lock] = {}
_LOCK_WAIT_STEP_SECONDS = 0.5  # how often a blocking install waiting for the lock checks ``stop``


class InstallOutcome(enum.Enum):
    INSTALLED = "installed"
    BUSY = "busy"  # non-blocking call; another install of this version holds the lock
    STOPPED = "stopped"  # the caller's stop event was set
    FAILED = "failed"
    WAITING = "waiting"  # the manager is fetching the build; nothing was sent online (BS-WK-10)


@dataclass(frozen=True)
class InstallResult:
    outcome: InstallOutcome
    exe_path: Optional[str] = None
    error: Optional[str] = None
    manager_state: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.outcome is InstallOutcome.INSTALLED

    @property
    def busy(self) -> bool:
        return self.outcome is InstallOutcome.BUSY


def _version_lock(version: str) -> threading.Lock:
    with _locks_guard:
        return _version_locks.setdefault(version, threading.Lock())


def _step(on_step: Optional[Callable[[str], None]], name: str) -> None:
    if on_step is not None:
        on_step(name)


def _set_downloading(tm, version: str, active: bool) -> None:
    with tm._download_lock:
        if active:
            tm._downloading_versions.add(version)
        else:
            tm._downloading_versions.discard(version)
    tm._scan_cache = None


def _make_executable(exe_path: str) -> None:
    if platform.system() != "Windows":
        logger.info(f"Setting execute permission on {exe_path}")
        st = os.stat(exe_path)
        os.chmod(exe_path, st.st_mode | stat.S_IEXEC)


def _unpack(tm, archive, version: str, platform_id: str) -> None:
    """Extract ``archive``; on any failure remove the version's folder, so it never counts as installed."""
    try:
        file_operations.extract_archive(str(archive), str(tm.blender_dir))
    except BaseException:
        shutil.rmtree(tm.blender_dir / f"blender-{version}-{platform_id}", ignore_errors=True)
        raise


def _download_online(tm, version: str, platform_id: str, stop):
    """The online step: its own plain sessions, never the manager's (BS-WK-5, BS-DL-7)."""
    entry = find_build(version, platform_id, config.BLENDER_VERSIONS_CACHE_FILE)
    return download_build(entry, tm.blender_dir, stop=stop)


def _extract(tm, archive, version: str, platform_id: str, stop, on_step) -> InstallResult:
    _step(on_step, "verifying")  # the archive's SHA-256 has been checked
    try:
        if stop is not None and stop.is_set():
            raise DownloadStoppedError("install stopped before extraction")
        _step(on_step, "extracting")
        _unpack(tm, archive, version, platform_id)
    finally:
        if archive.exists():
            file_operations.cleanup_archive(str(archive))
    tm._scan_cache = None
    exe_path = tm._installed_exe_path(version)  # still marked as installing
    if not exe_path:
        return InstallResult(InstallOutcome.FAILED, error=f"Blender {version} was extracted but has no executable.")
    _make_executable(exe_path)
    logger.info(f"Installed Blender {version}: {exe_path}")
    return InstallResult(InstallOutcome.INSTALLED, exe_path=exe_path)


def _install_locked(version: str, stop, on_step, wait_for_manager: bool) -> InstallResult:
    tm = tool_manager_instance
    exe_path = tm._installed_exe_path(version)
    if exe_path:  # installed meanwhile by the lock's previous holder
        return InstallResult(InstallOutcome.INSTALLED, exe_path=exe_path)
    platform_id = current_platform_id()
    if platform_id is None:
        return InstallResult(InstallOutcome.FAILED, error="This OS and architecture have no Blender build.")
    logger.info(f"Blender {version} is not installed; downloading it.")
    _set_downloading(tm, version, True)
    state = None  # the manager's answer, kept on every result
    try:
        tm._create_tools_directory_if_not_exists()
        _step(on_step, "downloading")
        got = blender_from_manager.fetch(version, platform_id, tm.blender_dir, stop=stop, wait=wait_for_manager)
        state = got.state
        if got.answer is ManagerAnswer.WAIT:
            return InstallResult(InstallOutcome.WAITING, manager_state=state)
        archive = got.archive if got.answer is ManagerAnswer.SUPPLIED else _download_online(
            tm, version, platform_id, stop)
        return replace(_extract(tm, archive, version, platform_id, stop, on_step), manager_state=state)
    except DownloadStoppedError:
        logger.info(f"Install of Blender {version} stopped.")
        return InstallResult(InstallOutcome.STOPPED, error="Cancelled", manager_state=state)
    except BlenderSourcesError as e:
        logger.error(f"Could not install Blender {version}: {e}")  # BS-LOG-2: logged once, here
        return InstallResult(InstallOutcome.FAILED, error=str(e), manager_state=state)
    except Exception as e:  # noqa: BLE001 - extraction or a local write error
        logger.critical(f"An error occurred while installing Blender {version}: {e}", exc_info=True)
        return InstallResult(InstallOutcome.FAILED, error=str(e), manager_state=state)
    finally:
        _set_downloading(tm, version, False)


def _acquire_unless_stopped(lock: threading.Lock, stop) -> bool:
    """Wait for ``lock``; False (lock not taken) as soon as ``stop`` is set."""
    while not lock.acquire(timeout=_LOCK_WAIT_STEP_SECONDS):
        if stop is not None and stop.is_set():
            return False
    return True


def install(version: str, *, blocking: bool, stop: Optional[threading.Event] = None,
            on_step: Optional[Callable[[str], None]] = None, wait_for_manager: bool = False) -> InstallResult:
    """Install Blender ``version`` (``X.Y.Z``) unless it is installed already.

    ``stop`` ends a running download or the wait for another install of
    the same version (the setup page's cancel, agent shutdown). ``on_step``
    is called with ``"downloading"``, ``"verifying"`` and ``"extracting"``.
    ``wait_for_manager``: a manager answering ``queued``/``fetching`` gives
    ``WAITING`` at once instead of the online step (version sync only;
    BS-WK-10). Never raises; never sleeps.
    """
    if not is_version(version):
        logger.error(f"Refusing to install Blender {version!r}: not an X.Y.Z version.")
        return InstallResult(InstallOutcome.FAILED, error=f"Not a Blender version: {version!r}")
    exe_path = tool_manager_instance.get_blender_executable_path(version)
    if exe_path:
        return InstallResult(InstallOutcome.INSTALLED, exe_path=exe_path)
    lock = _version_lock(version)
    if not blocking:
        if not lock.acquire(blocking=False):
            logger.info(f"Blender {version} is being installed by another task; skipping it this round.")
            return InstallResult(InstallOutcome.BUSY)
    elif not _acquire_unless_stopped(lock, stop):
        logger.info(f"Install of Blender {version} cancelled while waiting for another install of it.")
        return InstallResult(InstallOutcome.STOPPED, error="Cancelled")
    try:
        return _install_locked(version, stop, on_step, wait_for_manager)
    finally:
        lock.release()
