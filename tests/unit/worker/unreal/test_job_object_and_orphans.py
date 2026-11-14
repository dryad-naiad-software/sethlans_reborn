# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Job Object wrapper with a fake Win32 layer, and the orphan sweep with fake processes (FR-UE-11, FR-UE-23)."""
import json
import os
import sys

import psutil
import pytest

from sethlans_worker_agent.unreal_executor import job_object, orphans
from sethlans_worker_agent.unreal_executor.job_object import JobObject, JobObjectError

from ._runner_fakes import FakeJobApi, FakePs


def test_create_sets_kill_on_close():
    api = FakeJobApi({})
    job = JobObject.create(api)
    assert api.calls == ['create', 'set'] and not job.closed
    job.close()
    job.close()
    assert api.calls.count('close') == 1 and job.closed
    with pytest.raises(JobObjectError):
        job.pids()


def test_failed_limit_closes_the_handle_and_raises():
    api = FakeJobApi({}, fail={'set'})
    with pytest.raises(JobObjectError):
        JobObject.create(api)
    assert api.calls == ['create', 'set', 'close']


def test_kill_on_close_flag_value():
    assert job_object.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE == 0x2000


ENGINE = os.path.join('C:' + os.sep, 'Program Files', 'Epic Games', 'UE_5.8') if os.name == 'nt' else '/opt/UE_5.8'


def engine_exe(name):
    return os.path.join(ENGINE, 'Engine', 'Binaries', 'Win64', name)


class SweepProc(FakePs):
    def __init__(self, pid, exe, ctime=50.0, cwd='C:\\', cmdline=()):
        super().__init__(pid, exe=exe, ctime=ctime)
        self._cwd, self._cmdline = cwd, list(cmdline)

    def cwd(self):
        return self._cwd

    def cmdline(self):
        return self._cmdline


@pytest.fixture()
def world(tmp_path, monkeypatch):
    procs = {}
    owners = {}
    alive_after_kill = set()
    monkeypatch.setattr(orphans.zen_private, 'is_farm_zenserver',
                        lambda proc, cache, worker_sid=None: getattr(proc, 'farm', False))
    monkeypatch.setattr(orphans.zen_private, 'owned_by_worker',
                        lambda proc, worker_sid=None: owners.get(proc.pid, True))
    monkeypatch.setattr(orphans.psutil, 'wait_procs',
                        lambda targets, timeout=None: ([], [p for p in targets if p.pid in alive_after_kill]))

    def factory(pid):
        if pid not in procs:
            raise psutil.NoSuchProcess(pid)
        return procs[pid]

    def sweep(cache=None):
        return orphans.sweep(cache or str(tmp_path), [ENGINE], process_iter=lambda: list(procs.values()),
                             process_factory=factory)
    world_state = {'owners': owners, 'stubborn': alive_after_kill}
    return procs, sweep, world_state


def write_ledger(tmp_path, job, entries):
    (tmp_path / 'j' / job).mkdir(parents=True)
    (tmp_path / 'j' / job / 'procs.json').write_text(json.dumps({'schema': 1, 'procs': entries}))


def test_ledger_match_is_killed_and_untrusted_entries_are_not(tmp_path, world):
    procs, sweep, state = world
    inside = os.path.join(str(tmp_path), 'ueprofile', 'Zen', 'crashpad_handler.exe')
    procs[11] = SweepProc(11, engine_exe('CrashReportClientEditor.exe'), ctime=10.0)  # under the engine root
    procs[12] = SweepProc(12, engine_exe('notepad.exe'), ctime=99.0)                  # PID reused by another image
    procs[13] = SweepProc(13, inside, ctime=77.0)                                     # reused, same image
    procs[14] = SweepProc(14, inside, ctime=14.0)                                     # under CACHE_DIR
    procs[15] = SweepProc(15, 'C:/Users/x/crashpad_handler.exe', ctime=15.0)          # outside both roots
    procs[16] = SweepProc(16, inside, ctime=16.0)                                     # another owner
    procs[17] = SweepProc(17, '', ctime=17.0)                                         # no image
    state['owners'][16] = False
    write_ledger(tmp_path, '42-1a2b3c4d', [
        {'pid': 11, 'create_time': 10.0, 'exe': engine_exe('CrashReportClientEditor.exe')},
        {'pid': 12, 'create_time': 20.0, 'exe': engine_exe('UnrealTraceServer.exe')},
        {'pid': 13, 'create_time': 30.0, 'exe': inside},
        {'pid': 14, 'create_time': 14.0, 'exe': inside},
        {'pid': 15, 'create_time': 15.0, 'exe': 'C:/Users/x/crashpad_handler.exe'},
        {'pid': 16, 'create_time': 16.0, 'exe': inside},
        {'pid': 17, 'create_time': 17.0, 'exe': ''},
    ])
    result = sweep()
    assert sorted(pid for pid, _exe in result.killed) == [11, 14]
    assert result.survivors == ()


def _proc_with_cmdline(pid, *args):
    return SweepProc(pid, engine_exe('UnrealEditor-Cmd.exe'), cmdline=['UnrealEditor-Cmd.exe', *args])


def test_engine_root_and_cache_path_reference_is_killed(tmp_path, world):
    procs, sweep, _state = world
    cache = str(tmp_path)
    procs[21] = _proc_with_cmdline(21, os.path.join(cache, 'w', 'p.uproject'))
    procs[22] = SweepProc(22, engine_exe('ShaderCompileWorker.exe'), cwd=cache)  # cwd is no signal
    procs[23] = SweepProc(23, engine_exe('CrashReportClientEditor.exe'), cmdline=['x', '-abslog=' + cache])
    procs[24] = _proc_with_cmdline(24, 'D:/Unreal Projects/Artist.uproject')
    procs[25] = SweepProc(25, 'D:/Other/UnrealEditor-Cmd.exe', cmdline=['x', cache])  # not under the engine root
    procs[26] = SweepProc(26, engine_exe('crashpad_handler.exe'), cmdline=['x', cache])  # name rule not widened
    procs[27] = _proc_with_cmdline(27, f'-ExecCmds=py {os.path.join(cache, "j", "s.py")}')
    procs[28] = _proc_with_cmdline(28, f'"{cache}"')
    result = sweep()
    assert sorted(pid for pid, _exe in result.killed) == [21, 23, 27, 28]


@pytest.mark.parametrize('cache_name, other', [('ue', 'UE_Projects'), ('uecache', 'uecache2')])
def test_a_cache_path_prefix_of_another_folder_is_not_a_reference(tmp_path, world, cache_name, other):
    procs, sweep, _state = world
    cache = str(tmp_path / cache_name)
    procs[31] = _proc_with_cmdline(31, os.path.join(str(tmp_path), other, 'Artist.uproject'))
    procs[32] = _proc_with_cmdline(32, '-abslog=' + os.path.join(str(tmp_path), other, 'x.log'))
    procs[33] = _proc_with_cmdline(33, os.path.join(cache, 'w', 'Farm.uproject'))
    assert [pid for pid, _exe in sweep(cache).killed] == [33]


def test_only_the_farm_zenserver_is_swept(tmp_path, world):
    procs, sweep, _state = world
    farm = SweepProc(41, engine_exe('zenserver.exe'))
    farm.farm = True
    other = SweepProc(42, engine_exe('zenserver.exe'), cmdline=['--data-dir', str(tmp_path) + '2'])
    procs.update({41: farm, 42: other})
    write_ledger(tmp_path, '43-00000000', [{'pid': 42, 'create_time': 50.0, 'exe': engine_exe('zenserver.exe')}])
    result = sweep()
    assert [pid for pid, _exe in result.killed] == [41]
    assert not other.killed


def test_editor_roots_die_first_and_survivors_are_reported(tmp_path, world):
    procs, sweep, state = world
    cache = str(tmp_path)
    order = []
    scw = SweepProc(51, engine_exe('ShaderCompileWorker.exe'), cmdline=['x', cache])
    root = _proc_with_cmdline(52, cache)
    for proc in (scw, root):
        proc.kill = (lambda p: lambda: order.append(p.pid))(proc)
    procs.update({51: scw, 52: root})
    state['stubborn'].add(51)
    result = sweep()
    assert order[0] == 52
    assert [pid for pid, _exe in result.survivors] == [51]


def test_access_denied_is_tolerated(tmp_path, world):
    procs, sweep, _state = world

    class Denied(SweepProc):
        def cmdline(self):
            raise psutil.AccessDenied(self.pid)
    procs[61] = Denied(61, engine_exe('UnrealEditor-Cmd.exe'))
    assert sweep().killed == ()


def test_bad_ledgers_are_skipped(tmp_path, world):
    _procs, sweep, _state = world
    (tmp_path / 'j' / 'broken').mkdir(parents=True)
    (tmp_path / 'j' / 'broken' / 'procs.json').write_text('{not json')
    (tmp_path / 'j' / 'huge').mkdir(parents=True)
    (tmp_path / 'j' / 'huge' / 'procs.json').write_bytes(b' ' * (orphans.MAX_LEDGER_BYTES + 1))
    assert orphans.read_ledgers(tmp_path) == []
    assert sweep().killed == ()


@pytest.mark.skipif(sys.platform != 'win32', reason='directory junctions are Windows-only')
def test_a_reparse_point_ledger_directory_is_not_read(tmp_path):
    import _winapi
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'procs.json').write_text(json.dumps({'procs': [{'pid': 1, 'create_time': 1, 'exe': 'x'}]}))
    (tmp_path / 'cache' / 'j').mkdir(parents=True)
    _winapi.CreateJunction(str(outside), str(tmp_path / 'cache' / 'j' / 'link'))
    assert orphans.read_ledgers(tmp_path / 'cache') == []
