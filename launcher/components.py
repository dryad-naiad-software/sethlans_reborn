# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Starting the launcher's children, and why one stopped (FR-LN-1, -6, -7).

Every child gets :func:`launcher.child_env.child_env`: no setup secrets,
the settings location exported, plus its own ``extra``.

* :func:`start_setup_mode`: ``run_manager --setup`` with
  ``SETHLANS_SETUP_PORT`` (added after the scrub).
* :func:`start_manager`: ``run_manager``; stderr goes to a file
  (``logs/manager-start.log``) so :func:`failure_reason` can read why a
  start failed. A file, never a pipe: nothing drains a pipe, and a full
  one blocks the child's logging (issue #204). The file is new on each
  start and owner-only from birth, in an owner-only folder (it can name
  database hosts and users). ``SETHLANS_CONSOLE_LOG_LEVEL=WARNING`` keeps
  the manager's console log (its stderr) to warnings and errors, so the
  file stays small while the manager runs; the start-failure lines are
  ``print()`` and not affected. ``manager.log`` keeps the full log.
* :func:`start_worker`: ``run_worker`` with the web UI forced on.

:func:`failure_reason` is the last line that starts with ``[ERROR]``
(how ``run_manager`` prints a start failure), cleaned by
``launcher.setup_state.clean_reason``; or a plain "stopped with exit
code N" when there is none. It ends up in
``setup/last_failure``, which setup mode shows on its unauthenticated
``/api/health/``, so it is kept short and never holds more than that
one line.
"""
from __future__ import annotations

import contextlib
import subprocess
import sys
from pathlib import Path
from typing import BinaryIO, Mapping, Optional, Sequence

from launcher.child_env import TRAY_SECRET_ENV, child_env
from launcher.component_paths import find_component_exe, popen_kwargs_for_component
from launcher.setup_state import clean_reason
from shared import owner_only_acl
from shared.frozen_paths import is_frozen
from shared.settings_file_io import SettingsPermissionError, ensure_owner_only_dir

MANAGER_START_LOG = "manager-start.log"
SETUP_START_LOG = "setup-start.log"
SETUP_PORT_ENV = "SETHLANS_SETUP_PORT"
WORKER_UI_ENV = "SETHLANS_WORKER_UI_ENABLED"
CONSOLE_LOG_LEVEL_ENV = "SETHLANS_CONSOLE_LOG_LEVEL"
MANAGER_CONSOLE_LOG_LEVEL = "WARNING"
ERROR_PREFIX = "[ERROR]"
TAIL_BYTES = 64 * 1024


def open_owner_only_log(path: Path) -> BinaryIO:
    """A new, empty ``path``, owner-only from birth, open for writing.

    The folder is made owner-only when missing. An old file is removed
    first, so nothing of an earlier start survives. The file is created
    owner-only, checked, then opened again normally, so it can be read
    (View Logs, :func:`failure_reason`) while the child writes to it.
    """
    path = Path(path)
    ensure_owner_only_dir(path.parent)
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
    fd = owner_only_acl.create_owner_only_file(path)
    with open(fd, "wb", closefd=True):
        pass
    if not owner_only_acl.verify_owner_only(path):
        raise SettingsPermissionError(f"Could not make {path} owner-only")
    return open(path, "r+b")


def start_component(
    component: str,
    args: Sequence[str] = (),
    extra_env: Optional[Mapping[str, str]] = None,
    stderr_path: Optional[Path] = None,
) -> subprocess.Popen:
    """Start ``component`` (``manager``, ``worker`` or ``tray``) with a scrubbed env.

    stdout and stderr go to ``DEVNULL`` (the tray inherits the
    launcher's), except stderr when ``stderr_path`` is given: a new
    owner-only file (:func:`open_owner_only_log`) receives it.
    """
    exe = find_component_exe(component)
    cmd = [str(exe)] if is_frozen() else [sys.executable, str(exe)]
    cmd.extend(args)
    inherit = component == "tray"
    stdout = None if inherit else subprocess.DEVNULL
    stderr_file = None
    stderr = None if inherit else subprocess.DEVNULL
    if stderr_path is not None:
        stderr_file = open_owner_only_log(stderr_path)
        stderr = stderr_file
    try:
        return subprocess.Popen(
            cmd, stdout=stdout, stderr=stderr, env=child_env(extra_env),
            **popen_kwargs_for_component(),
        )
    finally:
        if stderr_file is not None:
            stderr_file.close()  # the child holds its own copy


def start_setup_mode(port: int, stderr_path: Optional[Path] = None) -> subprocess.Popen:
    """``run_manager --setup`` serving people on ``port`` (FR-LN-3)."""
    return start_component(
        "manager", ["--setup"], {SETUP_PORT_ENV: str(int(port))}, stderr_path,
    )


def start_manager(stderr_path: Path) -> subprocess.Popen:
    """``run_manager``, its stderr in ``stderr_path`` for :func:`failure_reason`."""
    return start_component(
        "manager", extra_env={CONSOLE_LOG_LEVEL_ENV: MANAGER_CONSOLE_LOG_LEVEL}, stderr_path=stderr_path,
    )


def start_worker() -> subprocess.Popen:
    """``run_worker`` with the embedded web UI on (FR-10, D4)."""
    return start_component("worker", extra_env={WORKER_UI_ENV: "true"})


def _tail(path: Path) -> str:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - TAIL_BYTES))
            return handle.read(TAIL_BYTES).decode("utf-8", "replace")
    except OSError:
        return ""


def failure_reason(stderr_path: Optional[Path], exit_code: int, program: str = "The manager") -> str:
    """Why a child stopped: its last ``[ERROR]`` line, else the exit code (see the module doc)."""
    if stderr_path is not None:
        for line in reversed(_tail(Path(stderr_path)).splitlines()):
            stripped = line.strip()
            if stripped.startswith(ERROR_PREFIX):
                reason = clean_reason(stripped[len(ERROR_PREFIX):])
                if reason:
                    return reason
    return f"{program} stopped with exit code {exit_code}"


def start_tray(ipc_secret: str, extra_env: Optional[Mapping[str, str]] = None) -> subprocess.Popen:
    """The tray helper; the only child that gets the tray IPC secret (FR-LN-7)."""
    env = dict(extra_env or {})
    env[TRAY_SECRET_ENV] = ipc_secret
    return start_component("tray", extra_env=env)
