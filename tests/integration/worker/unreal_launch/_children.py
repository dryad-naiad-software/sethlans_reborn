# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Harmless child processes for the Windows Job Object / runner / sweep integration tests (no Unreal)."""
import glob
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil

SLEEP = 'import time; time.sleep({seconds})'
# A child that starts a grandchild (both sleep), then sleeps itself.
WITH_GRANDCHILD = ("import subprocess, sys, time; "
                   "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep({seconds})']); "
                   "time.sleep({seconds})")


def python_args(code: str):
    return [sys.executable, '-c', code]


def fake_zenserver(bin_dir: Path) -> Path:
    """A copy of the base interpreter named ``zenserver.exe`` (needs ``PYTHONHOME``)."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    base = Path(sys.base_prefix)
    shutil.copy2(base / 'python.exe', bin_dir / 'zenserver.exe')
    for dll in glob.glob(str(base / '*.dll')):
        shutil.copy2(dll, bin_dir / Path(dll).name)
    return bin_dir / 'zenserver.exe'


def start_zenserver(exe: Path, data_dir: str, code: str = SLEEP.format(seconds=60)) -> subprocess.Popen:
    env = dict(os.environ, PYTHONHOME=sys.base_prefix)
    env.pop('PYTHONPATH', None)
    return subprocess.Popen([str(exe), '-c', code, '--data-dir', data_dir], env=env,
                            creationflags=subprocess.CREATE_NO_WINDOW)


def wait_until(predicate, timeout=15.0, interval=0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def kill_quietly(*procs) -> None:
    for proc in procs:
        try:
            for child in psutil.Process(proc.pid).children(recursive=True):
                child.kill()
            proc.kill()
            proc.wait(10)
        except (psutil.Error, OSError, subprocess.TimeoutExpired):
            pass
