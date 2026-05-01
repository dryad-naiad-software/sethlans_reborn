# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Launcher-side invocation of the ``apply_pending_setup`` Django command.

Implements Spec 2 FR-APPLY-ORDERING (steps 4 + 5) and FR-APPLY-INVOKE.
The launcher invokes ``manage.py migrate`` then ``manage.py
apply_pending_setup --data-dir <path>`` between observing
``.wizard_done`` and spawning the manager runtime.  Both subprocesses
are blocking (``subprocess.run``) per FR-APPLY-ORDERING-SYNC; the
manager runtime MUST NOT start before the apply exits with code 0.

Environment hardening (FR-APPLY-INVOKE): the curated env contains
ONLY ``DJANGO_SETTINGS_MODULE``, ``PATH`` (system default), ``PYTHONPATH``
(manager source dir), and platform vars (``SystemRoot`` Windows / ``HOME``
POSIX).  The launcher's full environment is NEVER inherited.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DJANGO_SETTINGS_MODULE = "sethlans_manager.settings"


def _manager_dir() -> Path:
    """Return the manager source directory (contains ``manage.py``)."""
    # When running from source, this file lives in launcher/ and the
    # manager source is alongside it.  When frozen, sys.executable is
    # the launcher binary; the manage.py wrapper lives in
    # ``<install>/manager/manage.py``.
    here = Path(__file__).resolve().parent
    return here.parent / "manager"


def _system_default_path() -> str:
    """Return the OS's system-default ``PATH``.

    POSIX: ``os.confstr('CS_PATH')``.  Windows: ``%SystemRoot%\\system32;%SystemRoot%``.
    Falls back to a hardcoded sane minimum if either probe fails.
    """
    if platform.system() == "Windows":
        sys_root = os.environ.get("SystemRoot", r"C:\Windows")
        return f"{sys_root}\\system32;{sys_root}"
    try:
        cs_path = os.confstr("CS_PATH")
        if cs_path:
            return cs_path
    except (AttributeError, OSError, ValueError):
        pass
    return "/usr/bin:/bin"


def build_curated_env(manager_dir: Optional[Path] = None) -> dict:
    """Build the FR-APPLY-INVOKE curated env dict.

    Drops everything from the launcher's environment except the bare
    minimum needed for a Django subprocess to start.
    """
    if manager_dir is None:
        manager_dir = _manager_dir()
    env: dict = {
        "DJANGO_SETTINGS_MODULE": DJANGO_SETTINGS_MODULE,
        "PATH": _system_default_path(),
        "PYTHONPATH": str(manager_dir),
    }
    if platform.system() == "Windows":
        env["SystemRoot"] = os.environ.get("SystemRoot", r"C:\Windows")
    else:
        env["HOME"] = os.environ.get("HOME", "/")
    return env


def _manage_py_path(manager_dir: Path) -> Path:
    return manager_dir / "manage.py"


def _log_subproc_output(label: str, result: subprocess.CompletedProcess) -> None:
    """Forward subprocess stdout/stderr to the launcher log only."""
    if result.stdout:
        for line in result.stdout.splitlines():
            logger.info("%s stdout: %s", label, line)
    if result.stderr:
        for line in result.stderr.splitlines():
            logger.info("%s stderr: %s", label, line)


def run_migrate_subprocess(manager_dir: Optional[Path] = None) -> int:
    """Run ``manage.py migrate`` with the curated env (FR-APPLY-ORDERING step 4).

    Returns the subprocess exit code.  Idempotent: re-running migrate
    after migrations have already applied is a no-op in Django.
    """
    if manager_dir is None:
        manager_dir = _manager_dir()
    manage_py = _manage_py_path(manager_dir)
    cmd = [sys.executable, str(manage_py), "migrate", "--noinput"]
    env = build_curated_env(manager_dir)
    logger.info("Running migrate subprocess: %s", manage_py)
    result = subprocess.run(
        cmd, check=False, capture_output=True, text=True, env=env,
    )
    _log_subproc_output("migrate", result)
    return result.returncode


def run_apply_pending_setup_subprocess(
    data_dir: Path,
    manager_dir: Optional[Path] = None,
) -> tuple[int, str]:
    """Run ``manage.py apply_pending_setup --data-dir <path>`` (FR-APPLY-ORDERING step 5).

    Blocks on ``subprocess.run`` (FR-APPLY-ORDERING-SYNC).  Captures
    stdout (logged only) and stderr (logged + returned).  Curated env
    per FR-APPLY-INVOKE.

    Returns ``(exit_code, stderr)``.  The caller surfaces stderr via
    the launcher tray on non-zero.
    """
    if manager_dir is None:
        manager_dir = _manager_dir()
    manage_py = _manage_py_path(manager_dir)
    cmd = [
        sys.executable, str(manage_py), "apply_pending_setup",
        "--data-dir", str(data_dir),
    ]
    env = build_curated_env(manager_dir)
    logger.info(
        "Running apply_pending_setup subprocess (data_dir=%s)", data_dir,
    )
    result = subprocess.run(
        cmd, check=False, capture_output=True, text=True, env=env,
    )
    _log_subproc_output("apply_pending_setup", result)
    return result.returncode, result.stderr or ""


def run_apply_pipeline(data_dir: Path) -> tuple[bool, str]:
    """Run migrate + apply_pending_setup in order (FR-APPLY-ORDERING).

    Returns ``(ok, message)``.  ``ok`` is True only on apply exit 0.
    The launcher MUST NOT spawn the manager runtime when ``ok`` is
    False.
    """
    manager_dir = _manager_dir()
    rc = run_migrate_subprocess(manager_dir)
    if rc != 0:
        return False, f"migrate failed with exit code {rc}"

    apply_rc, stderr = run_apply_pending_setup_subprocess(
        data_dir, manager_dir,
    )
    if apply_rc == 0:
        return True, ""
    msg = stderr.strip() or f"apply_pending_setup exit code {apply_rc}"
    return False, msg


def run_apply_pipeline_if_needed(
    topology: str,
    data_dir: Path,
    wizard_proc,
    terminate_wizard_cb,
    failure_exit_cb,
) -> Optional[int]:
    """Run migrate + apply only for manager-bearing topologies.

    Returns ``None`` on success / worker-only skip.  Returns an exit
    code (from ``failure_exit_cb``) when the pipeline fails — caller
    should propagate that as its own return value.
    """
    if topology not in ("manager", "manager_worker", "manager+worker"):
        return None
    manager_data = data_dir / "manager"
    ok, message = run_apply_pipeline(manager_data)
    if ok:
        return None
    logger.error("apply_pending_setup pipeline failed: %s", message)
    terminate_wizard_cb(wizard_proc)
    return failure_exit_cb("apply_pending_setup_failed")


__all__ = [
    "DJANGO_SETTINGS_MODULE",
    "build_curated_env",
    "run_apply_pending_setup_subprocess",
    "run_apply_pipeline",
    "run_apply_pipeline_if_needed",
    "run_migrate_subprocess",
]
