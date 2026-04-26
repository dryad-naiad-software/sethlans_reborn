# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""psutil-backed orphan process cleanup for the wizard E2E suite.

Windows ``Popen.terminate()`` is ``TerminateProcess`` — instant kill,
no signal handlers fire. The driver subprocess can't run its own
``terminate_all`` cleanup pass on Windows when killed that way, so the
wizard / mock-runtime grandchildren leak. This module walks two
indices to find them: ppid match against the driver pid (catches the
still-alive venvlauncher shim), and cmdline match scoped by env var
(catches the real wizard whose ppid points to an already-reaped
venvlauncher).
"""

from __future__ import annotations

from pathlib import Path


def kill_descendants(pid: int, data_dir: Path | None = None) -> None:
    """SIGKILL processes in *pid*'s descendant tree.

    Two passes: ppid match + cmdline-match scoped by *data_dir*.
    """
    try:
        import psutil
    except ImportError:
        return
    _kill_by_ppid(pid, psutil)
    if data_dir is not None:
        _kill_by_cmdline(data_dir, psutil)


def enumerate_descendants(pid: int) -> list:
    """Return psutil.Process objects for *pid*'s descendants."""
    try:
        import psutil
    except ImportError:
        return []
    try:
        parent = psutil.Process(pid)
        return parent.children(recursive=True)
    except Exception:  # noqa: BLE001 — psutil raises various
        return []


def _kill_by_ppid(pid: int, psutil) -> None:
    for proc in psutil.process_iter(["pid", "ppid"]):
        try:
            if proc.info["ppid"] == pid:
                _kill_subtree(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def _kill_by_cmdline(data_dir: Path, psutil) -> None:
    """Kill wizard / mock-runtime processes scoped to *data_dir* via env."""
    data_dir_str = str(data_dir).replace("/", "\\").lower()
    needles = ("wizard\\run_wizard.py", "_mock_runtime.py")
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            joined = " ".join(cmdline).replace("/", "\\").lower()
            if not any(n in joined for n in needles):
                continue
            if not _proc_belongs_to_data_dir(proc, data_dir_str):
                continue
            _kill_subtree(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def _proc_belongs_to_data_dir(proc, data_dir_str: str) -> bool:
    """Return True if *proc*'s SETHLANS_DATA_DIR env matches *data_dir_str*."""
    try:
        env = proc.environ()
    except Exception:  # noqa: BLE001 — psutil environ() can fail
        # If we can't read the env, fall back to killing it (the
        # cmdline already matched our needles). Better to over-clean
        # than leak.
        return True
    val = env.get("SETHLANS_DATA_DIR", "").replace("/", "\\").lower()
    return val == data_dir_str


def _kill_subtree(proc) -> None:
    """SIGKILL *proc* and all its descendants. Best-effort.

    Collects descendants BEFORE killing anything so killing the parent
    doesn't break the parent-child link mid-walk. Windows has no
    process group / job-object semantics — killing a parent doesn't
    propagate to children.
    """
    try:
        descendants = proc.children(recursive=True)
    except Exception:  # noqa: BLE001 — psutil
        descendants = []
    for victim in (*descendants, proc):
        _kill_quietly(victim)
    for victim in (*descendants, proc):
        _wait_quietly(victim, 2.0)


def _kill_quietly(victim) -> None:
    try:
        victim.kill()
    except Exception:  # noqa: BLE001 — psutil raises various
        pass


def _wait_quietly(victim, timeout: float) -> None:
    try:
        victim.wait(timeout=timeout)
    except Exception:  # noqa: BLE001 — psutil TimeoutExpired etc.
        pass
