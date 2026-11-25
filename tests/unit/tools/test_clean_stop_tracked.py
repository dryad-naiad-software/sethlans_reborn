# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Issue #315: ``stop`` (and so ``clean``) stops a ``.pids/<name>.pid`` process
only when it is this checkout's.

The process must be named python (python3*, Python) for manager / worker, or
caddy for caddy, and its command line must name this checkout's script. A
live PID that fails either check is a stale PID file: the script warns,
drops the file and leaves the process running.

The processes are real and harmless (a sleeping python, ``tail -f``,
``cmd /c ping``) and are always killed in teardown. Git Bash cannot see native
Windows PIDs (``kill -0`` fails on them), so for sethlans.sh on Windows they
are started from bash, as ``sethlans.sh start`` does.
"""

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import psutil
import pytest

from . import _clean_runner as runner
from ._clean_asserts import output
from ._clean_runner import PS_SHELLS, SHELLS
from .conftest import IS_WINDOWS, SCRIPT_TIMEOUT, run_clean

BASE_PYTHON = getattr(sys, "_base_executable", sys.executable)
_SLEEP = "import time; time.sleep(120)\n"
_BASH_LAUNCH = 'nohup "$@" >/dev/null 2>&1 </dev/null & p=$!; sleep 0.5; echo "$p $(cat /proc/$p/winpid)"'


@dataclass
class Proc:
    pid: int          # the PID the script sees (MSYS PID under Git Bash)
    os_pid: int       # the operating-system PID
    popen: subprocess.Popen = None

    def alive(self):
        if self.popen is not None:
            return self.popen.poll() is None
        try:
            return psutil.Process(self.os_pid).status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False

    def wait_gone(self, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.alive():
            time.sleep(0.1)
        return not self.alive()


def _kill_tree(proc: Proc):
    try:
        parent = psutil.Process(proc.os_pid)
        for child in parent.children(recursive=True):
            child.kill()
        parent.kill()
    except psutil.NoSuchProcess:
        pass
    if proc.popen is not None:
        proc.popen.wait(timeout=10)


@pytest.fixture
def spawn():
    """spawn(shell, argv_or_command, cwd) -> Proc; every process is killed in teardown."""
    started = []

    def _spawn(shell, argv, cwd):
        if shell == "sh" and IS_WINDOWS:
            env = dict(os.environ)
            env["PATH"] = str(Path(runner.git_bash()).parent) + os.pathsep + env["PATH"]
            out = subprocess.run([runner.git_bash(), "-c", _BASH_LAUNCH, "launch", *argv], cwd=str(cwd), env=env,
                                 capture_output=True, text=True, timeout=SCRIPT_TIMEOUT, check=True)
            msys_pid, win_pid = (int(v) for v in out.stdout.split())
            proc = Proc(msys_pid, win_pid)
        else:
            popen = subprocess.Popen(argv, cwd=str(cwd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            proc = Proc(popen.pid, popen.pid, popen)
        started.append(proc)
        time.sleep(0.5)
        assert proc.alive(), "precondition: the test process should be running"
        return proc

    yield _spawn
    for proc in started:
        _kill_tree(proc)


def _our_manager_script(project: Path) -> Path:
    script = project / "manager" / "run_manager.py"
    script.write_text(_SLEEP)
    return script


def _foreign_python(shell, spawn, tmp_path_factory):
    folder = tmp_path_factory.mktemp("elsewhere")
    return spawn(shell, [BASE_PYTHON, "-c", _SLEEP], folder)


def _non_python_with_our_path(shell, spawn, project):
    script = _our_manager_script(project)
    if shell in PS_SHELLS:
        return spawn(shell, f'cmd.exe /d /c "ping -n 120 127.0.0.1 >nul & rem {script}"', project)
    if not IS_WINDOWS and shutil.which("tail") is None:
        pytest.skip("needs tail")
    return spawn(shell, ["tail", "-f", str(script)], project)


def _write_pid(project: Path, name: str, proc: Proc):
    (project / ".pids" / f"{name}.pid").write_text(f"{proc.pid}\n")


def _stale_line(name, proc):
    return f"[WARN] Stale PID file for {name} (PID {proc.pid} is not ours); not stopping it"


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("name", ["manager", "worker", "caddy"])
def test_stale_pid_of_another_python_is_left_running(fake_project, spawn, tmp_path_factory, shell, name):
    runner.require_shell(shell)
    proc = _foreign_python(shell, spawn, tmp_path_factory)
    _write_pid(fake_project, name, proc)

    result = run_clean(fake_project, None, command="stop", shell=shell)

    assert result.returncode == 0, output(result)
    assert _stale_line(name, proc) in result.stdout.splitlines(), output(result)
    assert "[OK] No running services found" in result.stdout
    assert not (fake_project / ".pids" / f"{name}.pid").exists(), "the stale PID file should be dropped"
    assert proc.alive(), "a process that is not ours must not be stopped"


@pytest.mark.parametrize("shell", SHELLS)
def test_non_python_with_our_script_path_is_spared(fake_project, spawn, shell):
    runner.require_shell(shell)
    proc = _non_python_with_our_path(shell, spawn, fake_project)
    _write_pid(fake_project, "manager", proc)

    result = run_clean(fake_project, None, command="stop", shell=shell)

    assert result.returncode == 0, output(result)
    assert _stale_line("manager", proc) in result.stdout.splitlines(), output(result)
    assert proc.alive(), "a non-python process naming our script must not be stopped"


@pytest.mark.parametrize("shell", SHELLS)
def test_non_caddy_naming_our_caddyfile_is_stale(fake_project, spawn, shell):
    """The caddy PID file needs a process named caddy, even when its command line names our Caddyfile."""
    runner.require_shell(shell)
    caddyfile = fake_project / "manager" / "caddy" / "Caddyfile"
    caddyfile.parent.mkdir(parents=True)
    caddyfile.write_text("")
    proc = spawn(shell, [BASE_PYTHON, "-c", _SLEEP, "run", "--config", str(caddyfile)], fake_project)
    _write_pid(fake_project, "caddy", proc)

    result = run_clean(fake_project, None, command="stop", shell=shell)

    assert _stale_line("caddy", proc) in result.stdout.splitlines(), output(result)
    assert proc.alive()


@pytest.mark.parametrize("shell", SHELLS)
def test_our_tracked_manager_is_stopped_and_clean_completes(fake_project, spawn, shell):
    """Also the sh regression: ``cmd_stop`` used to end ``clean`` (set -e) after stopping something."""
    runner.require_shell(shell)
    proc = spawn(shell, [BASE_PYTHON, str(_our_manager_script(fake_project))], fake_project)
    _write_pid(fake_project, "manager", proc)

    result = run_clean(fake_project, None, shell=shell)

    assert result.returncode == 0, output(result)
    lines = result.stdout.splitlines()
    assert f"[OK] Manager stopped (PID {proc.pid})" in lines, output(result)
    assert "[OK] Clean complete." in lines, output(result)
    assert "[WARN]" not in result.stdout
    assert proc.wait_gone(), "our tracked manager should be stopped"
    assert not (fake_project / "manager" / "db.sqlite3").exists()


@pytest.mark.skipif(IS_WINDOWS, reason="the POSIX sweep needs pgrep, which Git Bash does not have; "
                                       "the name check is covered there through the PID-file tests")
def test_posix_sweep_spares_non_python_and_kills_our_python(fake_project, spawn):
    runner.require_shell("sh")
    if shutil.which("pgrep") is None or shutil.which("tail") is None:
        pytest.skip("needs pgrep and tail")
    script = _our_manager_script(fake_project)
    tail = spawn("sh", ["tail", "-f", str(script)], fake_project)
    ours = spawn("sh", [BASE_PYTHON, str(script)], fake_project)

    result = run_clean(fake_project, None, shell="sh")

    assert result.returncode == 0, output(result)
    assert f"[KILL] Stopping PID {ours.pid}" in result.stdout, output(result)
    assert ours.wait_gone()
    assert f"PID {tail.pid}" not in result.stdout
    assert tail.alive(), "tail -f on our script is not ours"
