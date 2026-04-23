# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Low-level Caddy subprocess operations (spawn / signal / kill).

Platform-specific hardening lives here so the supervisor loop in
``supervisor.py`` reads as pure lifecycle logic.
"""

from __future__ import annotations

import logging
import os
import platform
import signal
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


# Windows Job Object state (issue #108). The launcher attaches every
# spawned Caddy to this Job, configured with KILL_ON_JOB_CLOSE. When the
# launcher process exits (normally, via taskkill, or via a crash) the
# kernel closes the handle and kills every attached Caddy. This is the
# Windows analogue of the Linux ``PR_SET_PDEATHSIG`` path above.
#
# The handle is created lazily on the first Windows spawn, cached for
# the lifetime of the Python process, and never closed by us — the
# kernel closing it is what triggers the kill-on-close behaviour.
_windows_job_handle = None
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JobObjectExtendedLimitInformation = 9


def set_linux_pdeathsig() -> None:
    """On Linux, set PR_SET_PDEATHSIG(SIGTERM) so Caddy dies with us.

    Called as part of a POSIX ``preexec_fn``. On non-Linux POSIX
    (macOS, BSD) this is a silent no-op — ``prctl`` is a Linux-only
    syscall.
    """
    if sys.platform != "linux":
        return
    try:
        import ctypes
        PR_SET_PDEATHSIG = 1
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)
    except Exception:  # pragma: no cover - defensive
        pass


def posix_preexec() -> None:  # pragma: no cover - exercised in child process
    """POSIX preexec: new session + pdeathsig on Linux."""
    os.setsid()
    set_linux_pdeathsig()


def _CreateJobObjectW():
    """Create an unnamed Job Object. Returns a HANDLE (int) or 0 on failure.

    Factored as a module-level indirection so tests can monkeypatch it
    without touching ``ctypes``. Import is lazy so POSIX never loads
    ``windll``.
    """
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = kernel32.CreateJobObjectW
    fn.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    fn.restype = wintypes.HANDLE
    return fn(None, None)


def _SetInformationJobObject(handle, flags: int) -> int:
    """Configure Job with ``LimitFlags = flags``. Returns BOOL (1/0)."""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_void_p),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    fn = kernel32.SetInformationJobObject
    fn.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    fn.restype = wintypes.BOOL
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = flags
    return fn(
        handle,
        _JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )


def _AssignProcessToJobObject(job_handle, proc_handle: int) -> int:
    """Attach ``proc_handle`` to ``job_handle``. Returns BOOL (1/0)."""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = kernel32.AssignProcessToJobObject
    fn.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    fn.restype = wintypes.BOOL
    return fn(job_handle, proc_handle)


def _ensure_windows_job_handle():
    """Lazily create + configure the launcher-owned Job. Returns handle or None.

    Caches the handle in ``_windows_job_handle``. Returns ``None`` on
    any failure; caller is expected to log and continue (best-effort
    per spec FR-4).
    """
    global _windows_job_handle
    if _windows_job_handle is not None:
        return _windows_job_handle
    try:
        handle = _CreateJobObjectW()
    except Exception as exc:
        logger.warning(
            "CreateJobObjectW raised (%s); Caddy orphan prevention "
            "disabled.", exc,
        )
        return None
    if not handle:
        logger.warning(
            "CreateJobObjectW returned NULL; Caddy orphan prevention "
            "disabled.",
        )
        return None
    try:
        ok = _SetInformationJobObject(
            handle, _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        )
    except Exception as exc:
        logger.warning(
            "SetInformationJobObject raised (%s); Caddy orphan "
            "prevention disabled.", exc,
        )
        return None
    if not ok:
        logger.warning(
            "SetInformationJobObject returned 0; Caddy orphan "
            "prevention disabled.",
        )
        return None
    _windows_job_handle = handle
    return handle


def _attach_to_windows_job(proc: subprocess.Popen) -> None:
    """Attach ``proc`` to the launcher-owned Windows Job Object.

    Best-effort per spec FR-4: any failure is logged as a warning but
    never raised. The Caddy child stays alive either way; only the
    orphan-prevention guarantee is lost. The Job is never closed by
    Python — the kernel closes it when the launcher process exits, and
    that close is what triggers ``KILL_ON_JOB_CLOSE``.
    """
    job_handle = _ensure_windows_job_handle()
    if job_handle is None:
        return

    # proc._handle is a _winapi.Handle on Windows; int() yields the raw
    # HANDLE value that ctypes can pass straight to the Win32 call.
    try:
        proc_handle = int(proc._handle)
    except Exception as exc:
        logger.warning("Cannot read Caddy process handle: %s", exc)
        return

    try:
        ok = _AssignProcessToJobObject(job_handle, proc_handle)
    except Exception as exc:
        logger.warning(
            "AssignProcessToJobObject raised (%s); Caddy will not be "
            "killed automatically when launcher exits.", exc,
        )
        return
    if not ok:
        logger.warning(
            "AssignProcessToJobObject returned 0; Caddy will not be "
            "killed automatically when launcher exits.",
        )


def spawn(
    binary_path: Path,
    caddyfile_path: Path,
    env: "dict[str, str] | None" = None,
) -> subprocess.Popen:
    """Spawn Caddy in a platform-appropriate process group.

    List-form argv; ``shell=True`` is never used. On POSIX a new
    session is created via ``preexec_fn`` so the group can be signalled
    as a unit at shutdown. On Windows ``CREATE_NEW_PROCESS_GROUP``
    enables delivery of ``CTRL_BREAK_EVENT`` and the child is attached
    to a launcher-owned Job Object so the kernel kills it if the
    launcher dies abnormally (issue #108).

    :param env: optional environment mapping passed to the child. When
        ``None`` the child inherits the parent environment. When a dict
        is supplied, it is merged **on top of** the parent environment
        so Caddy still sees ``PATH`` / ``HOME`` / etc. while picking up
        the Sethlans-specific ``{$VAR}`` placeholder substitutions the
        Docker Caddyfile depends on.
    """
    argv = [
        str(binary_path),
        "run",
        "--config", str(caddyfile_path),
        "--adapter", "caddyfile",
    ]
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if env is not None:
        merged = dict(os.environ)
        merged.update(env)
        kwargs["env"] = merged
    if platform.system() == "Windows":
        # CREATE_NEW_PROCESS_GROUP keeps the CTRL_BREAK_EVENT seam used by
        # signal_graceful_stop. CREATE_NO_WINDOW suppresses the console
        # window Windows would otherwise allocate — the launcher is a
        # windowed (no-console) app, so without this flag the OS spawns
        # a fresh console for caddy.exe and pops it on the desktop.
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        kwargs["creationflags"] = flags
    else:
        kwargs["preexec_fn"] = posix_preexec
    logger.info("Spawning Caddy: %s", " ".join(argv))
    proc = subprocess.Popen(argv, **kwargs)  # nosec B603 - list-form
    if platform.system() == "Windows":
        # Post-spawn attach. Safe for Caddy because it does not fork
        # grandchildren under normal operation; the race window
        # between Popen return and Job assignment is negligible.
        _attach_to_windows_job(proc)
    return proc


def signal_graceful_stop(proc: subprocess.Popen) -> None:
    """Send the platform-appropriate graceful-stop signal."""
    try:
        if platform.system() == "Windows":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, OSError) as exc:
        logger.debug("Caddy already exited before signal: %s", exc)


def force_kill(proc: subprocess.Popen) -> None:
    """Hard-kill Caddy (SIGKILL on POSIX, TerminateProcess on Windows)."""
    try:
        if platform.system() == "Windows":
            proc.kill()
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError) as exc:
        logger.debug("Caddy already exited before kill escalation: %s", exc)
