# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fakes for the UE process runner tests: Win32 Job API, Popen, psutil processes, a manual clock."""
from collections import namedtuple
from pathlib import Path

import psutil

from sethlans_worker_agent.unreal_executor import command, runner_types, user_state
from sethlans_worker_agent.unreal_executor.job_object import JobObjectError
from sethlans_worker_agent.unreal_executor.runner_types import RunSpec

CpuTimes = namedtuple('CpuTimes', 'user system')
ROOT_PID = 1000


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


class TickEvent:
    """A ``shutdown_event`` stand-in: ``wait`` advances the clock and runs the scenario."""

    def __init__(self, clock, scenario=None):
        self.clock, self.scenario, self._set, self.ticks = clock, scenario, False, 0

    def wait(self, timeout):
        self.clock.now += timeout
        self.ticks += 1
        if self.scenario is not None:
            self.scenario(self.ticks)
        return self._set

    def set(self):
        self._set = True

    def is_set(self):
        return self._set


class FakePs:
    def __init__(self, pid, exe='C:/UE/Engine/Binaries/Win64/UnrealEditor-Cmd.exe', ctime=50.0, cpu=0.0):
        self.pid, self._exe, self._ctime, self.cpu, self.alive = pid, exe, ctime, cpu, True
        self.resumed = self.killed = False
        self.kids = []

    def create_time(self):
        if not self.alive:
            raise psutil.NoSuchProcess(self.pid)
        return self._ctime

    def exe(self):
        return self._exe

    def name(self):
        return self._exe.replace('\\', '/').rsplit('/', 1)[-1]

    def cpu_times(self):
        if not self.alive:
            raise psutil.NoSuchProcess(self.pid)
        return CpuTimes(self.cpu, 0.0)

    def children(self, recursive=False):
        return [kid for kid in self.kids if kid.alive]

    def resume(self):
        self.resumed = True

    def is_running(self):
        return self.alive

    def kill(self):
        self.killed = True
        self.alive = False

    def wait(self, timeout=None):
        if self.alive:
            raise psutil.TimeoutExpired(timeout, self.pid)
        return 0


class FakeJobApi:
    def __init__(self, procs, fail=()):
        self.procs, self.fail, self.calls, self.active_override = procs, set(fail), [], None

    def _maybe(self, name):
        self.calls.append(name)
        if name in self.fail:
            raise JobObjectError(5, f'{name} failed')

    def create(self):
        self._maybe('create')
        return 77

    def set_kill_on_close(self, handle):
        self._maybe('set')

    def assign(self, handle, process_handle):
        self._maybe('assign')

    def terminate(self, handle, exit_code):
        self._maybe('terminate')
        for proc in self.procs.values():
            proc.alive = False

    def active_processes(self, handle):
        self._maybe('active')
        if self.active_override is not None:
            return self.active_override
        return sum(1 for proc in self.procs.values() if proc.alive)

    def pids(self, handle):
        self._maybe('pids')
        return [pid for pid, proc in self.procs.items() if proc.alive]

    def close(self, handle):
        self.calls.append('close')


class FakePopen:
    """``Popen`` stand-in whose process is ``procs[ROOT_PID]``."""

    def __init__(self, procs):
        self.procs, self.calls = procs, []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        return FakeProcess(self.procs[ROOT_PID])


class FakeProcess:
    def __init__(self, ps):
        self.ps, self.pid, self._handle, self.exit_code = ps, ps.pid, 555, 0

    def poll(self):
        return None if self.ps.alive else self.exit_code

    def kill(self):
        self.ps.kill()

    def wait(self, timeout=None):
        return self.exit_code


def make_launch(tmp_path, phase='render', pilot=False, env=None):
    profile = str(tmp_path / 'ueprofile')
    full_env = dict(user_state.isolation_env(profile), PATH='x') if env is None else env
    return command.UeLaunch(phase=phase, pilot=pilot, args=('UnrealEditor-Cmd.exe', 'p.uproject'),
                            env=full_env, cwd=str(tmp_path), profile=profile)


def make_spec(tmp_path, clock, scenario=None, **overrides):
    values = dict(launch=make_launch(tmp_path, overrides.pop('phase', 'render')),
                  log_path=Path(tmp_path) / 'render.1.log', stdout_path=Path(tmp_path) / 'render.1.stdout.txt',
                  ledger_path=Path(tmp_path) / 'procs.json', timeout_s=3600, hang_s=900, quit_grace_s=60,
                  log_max_bytes=512 * runner_types.MB, log_flood_bytes_per_min=100 * runner_types.MB,
                  shutdown_event=TickEvent(clock, scenario))
    values.update(overrides)
    return RunSpec(**values)
