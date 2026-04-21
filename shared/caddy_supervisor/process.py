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


def spawn(
    binary_path: Path,
    caddyfile_path: Path,
    env: "dict[str, str] | None" = None,
) -> subprocess.Popen:
    """Spawn Caddy in a platform-appropriate process group.

    List-form argv; ``shell=True`` is never used. On POSIX a new
    session is created via ``preexec_fn`` so the group can be signalled
    as a unit at shutdown. On Windows ``CREATE_NEW_PROCESS_GROUP``
    enables delivery of ``CTRL_BREAK_EVENT``.

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
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        kwargs["creationflags"] = flags
    else:
        kwargs["preexec_fn"] = posix_preexec
    logger.info("Spawning Caddy: %s", " ".join(argv))
    return subprocess.Popen(argv, **kwargs)  # nosec B603 - list-form


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
