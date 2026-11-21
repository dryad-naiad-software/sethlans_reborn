# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""UE process runner: exit confirmation, survivors, ledger, GPU memory samples, drain (FR-UE-11)."""
import json
import logging

import pytest

from sethlans_worker_agent.unreal_executor import process_runner

from ._process_runner_world import World
from ._runner_fakes import ROOT_PID, FakePs


@pytest.fixture()
def world(tmp_path):
    return World(tmp_path)


def test_exception_mid_monitor_confirms_exit_before_propagating(world, caplog):
    def boom(deadline):
        raise UnicodeDecodeError('utf-8', b'x', 0, 1, 'bad')
    registry = process_runner.RunnerRegistry()
    with pytest.raises(UnicodeDecodeError):
        world.run(world.spec(vram_sample=boom), registry=registry)
    assert 'terminate' in world.api.calls and 'close' in world.api.calls and not world.root.alive
    assert registry.confirm_all() == ()  # idempotent


def test_survivors_are_reported(world, monkeypatch, caplog):
    world.steps[2] = lambda: setattr(world.root, 'alive', False)
    stuck = FakePs(1003, exe='C:/UE/CrashReportClientEditor.exe')
    world.procs[1003] = stuck
    original = world.api.terminate

    def terminate(handle, code):
        original(handle, code)
        stuck.alive = True  # refuses to die
    world.api.terminate = terminate
    with caplog.at_level(logging.ERROR):
        outcome = world.run(exit_wait_s=0.0)
    assert outcome.survivors == ((1003, 'C:/UE/CrashReportClientEditor.exe'),)


def test_ledger_lists_job_members_that_are_not_descendants(tmp_path):
    crashpad = FakePs(2001, exe='C:/Users/x/crashpad_handler.exe', ctime=60.0)
    world = World(tmp_path, kids=[crashpad])  # in the job, not in root.children()
    world.steps[2] = lambda: setattr(world.root, 'alive', False)
    world.run()
    ledger = json.loads((tmp_path / 'procs.json').read_text())
    assert {entry['pid'] for entry in ledger['procs']} == {ROOT_PID, 2001}
    assert next(e for e in ledger['procs'] if e['pid'] == 2001)['exe'].endswith('crashpad_handler.exe')


def test_log_tail_opens_the_log_only_after_it_exists(tmp_path, monkeypatch):
    from sethlans_worker_agent.unreal_executor import log_tail
    opened = []
    real_open = open
    monkeypatch.setattr(log_tail, 'open', lambda *a, **k: opened.append(a[0]) or real_open(*a, **k),
                        raising=False)
    log = tmp_path / 'render.1.log'
    tail = log_tail.LogTail(log)
    assert tail.read_new() == [] and opened == []
    log.write_bytes(b'\xef\xbb\xbffirst\r\nsecond\npart')
    assert tail.read_new() == ['first', 'second'] and len(opened) == 1
    with real_open(log, 'ab') as handle:
        handle.write(b'ial\n' + b'\xff' * 3 + b'\n')
    assert tail.read_new() == ['partial', '�' * 3]
    tail.close()


def test_tick_check_ends_the_phase_as_a_local_conflict(world):
    world.steps[2] = lambda: world.flags.append(1)
    world.flags = []
    outcome = world.run(world.spec(tick_check=lambda: 'farm zenserver on 0.0.0.0' if world.flags else None))
    assert outcome.reason == 'local_conflict' and '0.0.0.0' in outcome.detail


def test_vram_peak_is_over_the_baseline_and_duplicates_are_skipped(world):
    from tests._unreal_worker_state import reading
    used = iter([1500, 9000, 7000, 8000] + [1200] * 100)
    seen = []

    def sample(timeout_s):
        seen.append(timeout_s)
        return reading(used=next(used), taken_at=world.clock.now)
    world.steps[5] = lambda: setattr(world.root, 'alive', False)
    outcome = world.run(world.spec(vram_sample=sample, vram_baseline={0: 1000}))
    assert outcome.vram_peak_mb == 8000 and outcome.vram.samples >= 4 and outcome.vram.max_gap_s <= 2.0
    assert set(seen) == {1.5}  # a relative timeout, never a deadline on the runner's clock
    trace = outcome.vram
    count = trace.samples
    trace.add(reading(used=99999, taken_at=trace.last_sample_at))  # already added: skipped
    assert trace.samples == count and max(trace.max_used.values()) == 9000


def test_vram_ticks_follow_a_fixed_schedule(world):
    from tests._unreal_worker_state import reading
    stamps = []

    def slow_sample(timeout_s):
        world.clock.now += 0.6  # a slow nvidia-smi call
        stamps.append(world.clock.now)
        return reading(taken_at=world.clock.now)
    world.steps[10] = lambda: setattr(world.root, 'alive', False)
    world.run(world.spec(vram_sample=slow_sample))
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert gaps and max(gaps) <= 1.0 + 1e-9  # the 0.6 s calls do not stretch the 1 s cadence


def test_idle_tree_at_0_9_cpu_s_per_minute_is_a_hang(world):
    def idle(tick):
        world.root.cpu += 0.9 * 2.0 / 60.0  # 0.9 CPU-s per minute
    world.scenario = idle
    outcome = world.run(world.spec(hang_s=900, timeout_s=100_000))
    assert outcome.reason == 'hang'
    assert 900 <= outcome.wall_s <= 910


def test_confirm_failure_fails_closed(world, monkeypatch):
    world.steps[2] = lambda: setattr(world.root, 'alive', False)
    registry = process_runner.RunnerRegistry()

    def boom(self):
        raise OSError('query failed')
    monkeypatch.setattr(process_runner.ProcessRunner, '_confirm', boom)
    with pytest.raises(OSError):
        world.run(registry=registry)
    assert registry.confirm_all() == process_runner.UNKNOWN_SURVIVOR


def test_run_requires_a_registry(world):
    with pytest.raises(ValueError):
        process_runner.run(world.spec(), None)


def test_ledger_write_failure_is_retried_next_tick(world, tmp_path, monkeypatch, caplog):
    from sethlans_worker_agent.unreal_executor import runner_monitor
    real_replace = runner_monitor.os.replace
    calls = []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) == 1:
            raise OSError(28, 'disk full')
        return real_replace(src, dst)
    monkeypatch.setattr(runner_monitor.os, 'replace', flaky)
    world.steps[4] = lambda: setattr(world.root, 'alive', False)
    with caplog.at_level(logging.WARNING):
        outcome = world.run()
    assert outcome.reason == 'exited' and len(calls) >= 2
    assert (tmp_path / 'procs.json').exists()
    assert not list(tmp_path.glob('procs.json.tmp-*'))
    assert any('retrying next tick' in r.getMessage() for r in caplog.records)


def test_ledger_is_cumulative_across_phases(tmp_path):
    first = World(tmp_path, kids=[FakePs(3001, ctime=1.0)])
    first.steps[2] = lambda: [setattr(p, 'alive', False) for p in first.procs.values()]
    first.run()
    second = World(tmp_path, kids=[FakePs(3002, ctime=2.0)])
    second.steps[2] = lambda: [setattr(p, 'alive', False) for p in second.procs.values()]
    second.run()
    ledger = json.loads((tmp_path / 'procs.json').read_text())
    assert {e['pid'] for e in ledger['procs']} == {ROOT_PID, 3001, 3002}


def test_reused_pid_gets_a_fresh_process_object(tmp_path):
    from sethlans_worker_agent.unreal_executor.runner_monitor import Members
    procs = {7: FakePs(7, ctime=1.0)}
    factory_calls = []

    def factory(pid):
        factory_calls.append(pid)
        return procs[pid]

    class Job:
        def pids(self):
            return [7]
    members = Members(ROOT_PID, job=Job(), process_factory=factory)
    members.current()
    procs[7].alive = False
    procs[7] = FakePs(7, ctime=9.0)  # the PID was reused
    assert members.current()[0].create_time() == 9.0 and factory_calls == [7, 7]


def test_final_drain_is_skipped_on_shutdown(world):
    spec = world.spec()

    def flood_then_shutdown():
        world.log(*(['x'] * 10))
        spec.shutdown_event.set()
    world.steps[2] = flood_then_shutdown
    outcome = world.run(spec)
    assert outcome.reason == 'shutdown'


def test_fallback_kills_the_root_first(tmp_path):
    kid = FakePs(1004, exe='/ue/ShaderCompileWorker')
    zen = FakePs(1005, exe='/ue/zenserver')
    world = World(tmp_path, kids=[kid, zen])
    world.root.kids = [kid, zen]
    order = []
    for proc in (world.root, kid, zen):
        proc.kill = (lambda p: lambda: (order.append(p.pid), setattr(p, 'alive', False)))(proc)
    outcome = world.run(world.spec(timeout_s=10, excluded_images=frozenset({'/ue/zenserver'})), windows=False)
    assert outcome.reason == 'timeout' and outcome.job_object == 'none'
    assert order[0] == ROOT_PID and 1005 not in order
