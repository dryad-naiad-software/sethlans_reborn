# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Process lookups for the worker stop / restart E2E tests (#246, #247).

Every lookup is scoped to processes this test started: Blender renders
are matched by the job's unique output name on their command line, and
leftovers (Blender, Caddy) by the test's own temp directory on their
command line. Nothing here touches a process outside that scope.

Windows venv note: ``.venv\\Scripts\\python.exe`` is a launcher that
starts the real interpreter as its child and waits for it. ``Popen.pid``
is the launcher; the worker agent itself is that child.
"""

import logging
import os
import platform
import sys
import time

import psutil

logger = logging.getLogger(__name__)


def _norm(text):
    """Case- and separator-insensitive form for command-line matching."""
    return os.path.normcase(str(text)).replace("/", "\\")


def _cmdline(proc):
    try:
        return _norm(" ".join(proc.info.get("cmdline") or []))
    except Exception:  # noqa: BLE001 - psutil raises various
        return ""


def _uses_venv_launcher():
    base = getattr(sys, "_base_executable", sys.executable)
    return (platform.system() == "Windows"
            and _norm(base) != _norm(sys.executable))


def real_worker_pid(proc, timeout=15):
    """Return the pid of the worker agent interpreter started by ``proc``."""
    if not _uses_venv_launcher():
        return proc.pid
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            kids = [c for c in psutil.Process(proc.pid).children()
                    if "python" in c.name().lower()]
        except psutil.NoSuchProcess:
            kids = []
        if kids:
            return kids[0].pid
        if proc.poll() is not None:
            break
        time.sleep(0.2)
    raise AssertionError(f"No worker interpreter found under launcher {proc.pid}")


def procs_matching(needle, name_part=None):
    """Live processes whose command line contains ``needle``.

    ``name_part`` (for example ``"blender"``) narrows the match to
    processes whose executable name contains it.
    """
    wanted = _norm(needle)
    found = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if name_part and name_part not in (proc.info.get("name") or "").lower():
                continue
            if wanted in _cmdline(proc):
                found.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


def blender_renders(marker):
    """Blender processes rendering the job whose output name is ``marker``."""
    return procs_matching(marker, name_part="blender")


def cpu_seconds(proc):
    """User + system CPU seconds of ``proc``, or 0 when it is gone."""
    try:
        times = proc.cpu_times()
        return times.user + times.system
    except psutil.Error:
        return 0.0


def is_alive(pid):
    """True while a process with ``pid`` runs (and is not a zombie)."""
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def describe(procs):
    """Short ``name(pid)`` list for logs and observations."""
    out = []
    for proc in procs:
        try:
            out.append(f"{proc.name()}({proc.pid})")
        except psutil.Error:
            out.append(f"?({proc.pid})")
    return out


def kill_all(procs, timeout=10):
    """Kill ``procs`` (and their children) and wait until they are gone."""
    victims = []
    for proc in procs:
        try:
            victims.extend(proc.children(recursive=True))
        except psutil.Error:
            pass
        victims.append(proc)
    for proc in victims:
        try:
            proc.kill()
        except psutil.Error:
            pass
    _gone, alive = psutil.wait_procs(victims, timeout=timeout)
    if alive:
        logger.error("Processes survived kill: %s", describe(alive))
    return alive


def sweep(path):
    """Kill every leftover process whose command line names ``path``."""
    leftovers = procs_matching(path)
    if leftovers:
        logger.warning("Killing leftover test processes: %s", describe(leftovers))
        kill_all(leftovers)
    return leftovers
