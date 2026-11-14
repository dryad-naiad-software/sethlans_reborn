# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""UE process runner with a fake Win32 job, Popen and psutil (FR-UE-11; AC-41, FR-T-5)."""
import json
import logging

import pytest

from sethlans_worker_agent.unreal_executor import log_validation, process_runner, user_state
from sethlans_worker_agent.unreal_executor.runner_types import (
    MB, RENDER_SUCCESS, UNCONFINED_MESSAGE, WARMUP_SUCCESS, UnconfinedLaunchError)

from ._runner_fakes import ROOT_PID, Clock, FakeJobApi, FakePopen, FakePs, make_launch, make_spec

FATAL = log_validation.fatal_rules()


class World:
    def __init__(self, tmp_path, kids=()):
        self.tmp_path = tmp_path
        self.clock = Clock()
        self.root = FakePs(ROOT_PID, cpu=0.0)
        self.procs = {ROOT_PID: self.root}
        for kid in kids:
            self.procs[kid.pid] = kid
        self.api = FakeJobApi(self.procs)
        self.popen = FakePopen(self.procs)
        self.steps = {}

    def scenario(self, tick):
        step = self.steps.get(tick)
        if step is not None:
            step()

    def log(self, *lines):
        with open(self.tmp_path / 'render.1.log', 'a', encoding='utf-8') as handle:
            handle.write(''.join(line + '\n' for line in lines))

    def spec(self, **overrides):
        return make_spec(self.tmp_path, self.clock, self.scenario, **overrides)

    def run(self, spec=None, registry=None, windows=True, exit_wait_s=30.0):
        return process_runner.run(spec or self.spec(), registry or process_runner.RunnerRegistry(),
                                  job_api=self.api, popen=self.popen,
                                  clock=self.clock, windows=windows, process_factory=lambda pid: self.procs[pid],
                                  process_iter=lambda: [], exit_wait_s=exit_wait_s)


@pytest.fixture()
def world(tmp_path):
    return World(tmp_path)


def test_exit_is_confirmed_and_the_process_was_suspended_assigned_resumed(world):
    world.steps[3] = lambda: setattr(world.root, 'alive', False)
    outcome = world.run()
    assert outcome.reason == 'exited' and outcome.exit_code == 0 and outcome.survivors == ()
    assert world.api.calls[:3] == ['create', 'set', 'assign'] and world.root.resumed
    flags = world.popen.calls[0][1]['creationflags']
    assert flags & process_runner.CREATE_SUSPENDED
    assert world.popen.calls[0][1]['shell'] is False and 'stdout' in world.popen.calls[0][1]
    assert 'terminate' in world.api.calls and world.api.calls[-1] == 'close'
    assert outcome.job_object == 'ok'


def test_create_failure_refuses_to_launch(world):
    world.api.fail.add('create')
    with pytest.raises(UnconfinedLaunchError, match='refusing to run unconfined'):
        world.run()
    assert world.popen.calls == []


def test_assignment_failure_kills_the_process(world):
    world.api.fail.add('assign')
    with pytest.raises(UnconfinedLaunchError) as info:
        world.run()
    assert str(info.value) == UNCONFINED_MESSAGE
    assert world.root.killed and not world.root.resumed and 'close' in world.api.calls


def test_environment_without_isolation_is_refused(world, tmp_path):
    spec = world.spec(launch=make_launch(tmp_path, env={'PATH': 'x'}))
    with pytest.raises(user_state.IsolationError):
        world.run(spec)
    assert world.popen.calls == [] and world.api.calls == []


def test_timeout_terminates_the_job(world):
    outcome = world.run(world.spec(timeout_s=10))
    assert outcome.reason == 'timeout' and 'terminate' in world.api.calls and outcome.survivors == ()


@pytest.mark.parametrize('cause', ['canceled', 'reassigned', 'shutdown'])
def test_cancel_causes(world, cause):
    world.steps[2] = lambda: world.cause.append(cause)
    world.cause = []
    outcome = world.run(world.spec(cancel=lambda: world.cause[0] if world.cause else None))
    assert outcome.reason == cause and not world.root.alive


def test_shutdown_event(world):
    spec = world.spec()
    world.steps[2] = spec.shutdown_event.set
    assert world.run(spec).reason == 'shutdown'


def test_fatal_line_terminates_at_once(world):
    world.steps[2] = lambda: world.log('[x]LogD3D12RHI: Error: DXGI_ERROR_DEVICE_REMOVED')
    outcome = world.run(world.spec(fatal_rules=FATAL))
    assert outcome.reason == 'fatal' and outcome.fatal_rule == 'gpu_device_lost'
    assert outcome.rule_counts['gpu_device_lost'] == 1


def test_hang_needs_silence_and_idle_cpu(world):
    outcome = world.run(world.spec(hang_s=120, timeout_s=10_000))
    assert outcome.reason == 'hang' and 'CPU-s' in outcome.detail


def test_busy_cpu_is_not_a_hang_even_when_a_worker_exits(tmp_path):
    scw = FakePs(1001, exe='C:/UE/Engine/Binaries/Win64/ShaderCompileWorker.exe')
    world = World(tmp_path, kids=[scw])

    def burn(tick):
        scw.cpu += 10.0 if scw.alive else 0.0
        if tick == 40:
            scw.alive = False  # an exited SCW must not lower the accumulated CPU
        if tick > 40:
            world.root.cpu += 1.0
        if tick == 150:
            world.root.alive = False
    world.scenario = burn
    outcome = world.run(world.spec(hang_s=120, timeout_s=10_000))
    assert outcome.reason == 'exited'


def test_zenserver_cpu_is_excluded_by_exact_image(tmp_path):
    zen_image = 'C:/UE/Engine/Binaries/Win64/zenserver.exe'
    zen = FakePs(1002, exe=zen_image)
    world = World(tmp_path, kids=[zen])

    def tick(n):
        zen.cpu += 50.0
    world.scenario = tick
    outcome = world.run(world.spec(hang_s=120, timeout_s=10_000, excluded_images=frozenset({zen_image.lower()})))
    assert outcome.reason == 'hang'


def test_a_lookalike_zenserver_image_is_counted(tmp_path):
    fake_zen = FakePs(1002, exe='D:/other/zenserver_x.exe')
    world = World(tmp_path, kids=[fake_zen])

    def tick(n):
        fake_zen.cpu += 50.0
    world.scenario = tick
    spec = world.spec(hang_s=120, timeout_s=400,
                      excluded_images=frozenset({'c:/ue/engine/binaries/win64/zenserver.exe'}))
    assert world.run(spec).reason == 'timeout'
    assert not fake_zen.alive  # terminated with the job


@pytest.mark.parametrize('phase, marker, kwargs', [
    ('warmup', '[Warm] done, quitting', {'success_marker': WARMUP_SUCCESS}),
    ('render', 'LogWindows: FPlatformMisc::RequestExitWithStatus(0, 0, <NoCallSiteInfo>)',
     {'success_marker': RENDER_SUCCESS}),
])
def test_quit_grace_after_the_success_marker(world, tmp_path, phase, marker, kwargs):
    world.steps[2] = lambda: world.log(marker)
    spec = world.spec(launch=make_launch(tmp_path, phase=phase), quit_grace_s=60, **kwargs)
    outcome = world.run(spec)
    assert outcome.reason == 'exited_after_grace' and outcome.quit_forced and outcome.success_marker_seen
    assert 58 <= outcome.wall_s <= 66
    assert outcome.phase == phase


def test_prep_success_check_counts_as_the_marker(world, tmp_path):
    result = tmp_path / 'prep_result.json'
    world.steps[3] = lambda: result.write_text(json.dumps({'ok': True}))

    def check():
        try:
            return json.loads(result.read_text()).get('ok') is True
        except (OSError, ValueError):
            return False
    outcome = world.run(world.spec(launch=make_launch(tmp_path, phase='prep'), success_check=check))
    assert outcome.reason == 'exited_after_grace' and outcome.success_marker_seen


def test_no_marker_means_hang_and_timeout_rules_apply(world):
    outcome = world.run(world.spec(success_marker=WARMUP_SUCCESS, timeout_s=300, hang_s=900))
    assert outcome.reason == 'timeout' and not outcome.success_marker_seen


def _grow(path, megabytes):
    with open(path, 'ab') as handle:
        handle.write(b'x' * int(megabytes * MB))


def test_log_over_the_size_cap_is_fatal_flood(world, tmp_path):
    world.steps[2] = lambda: _grow(tmp_path / 'render.1.stdout.txt', 17)
    outcome = world.run(world.spec(log_max_bytes=16 * MB, log_flood_bytes_per_min=10_000 * MB))
    assert outcome.reason == 'fatal' and outcome.fatal_rule == 'log_flood'
    assert outcome.rule_counts['log_flood'] == 1 and 'log flood' in outcome.detail


def test_fast_growth_within_a_minute_is_a_flood(world, tmp_path):
    for tick in range(2, 8):
        world.steps[tick] = lambda: _grow(tmp_path / 'render.1.log', 3)
    outcome = world.run(world.spec(log_max_bytes=512 * MB, log_flood_bytes_per_min=10 * MB))
    assert outcome.reason == 'fatal' and outcome.fatal_rule == 'log_flood' and 'MB/min' in outcome.detail


def test_slow_growth_is_not_a_flood(world, tmp_path):
    for tick in range(2, 60, 30):
        world.steps[tick] = lambda: _grow(tmp_path / 'render.1.log', 6)
    world.steps[70] = lambda: setattr(world.root, 'alive', False)
    assert world.run(world.spec(log_flood_bytes_per_min=10 * MB)).reason == 'exited'


def test_flood_after_the_marker_is_a_forced_quit(world, tmp_path):
    world.steps[2] = lambda: world.log('[Warm] done, quitting')
    world.steps[4] = lambda: _grow(tmp_path / 'render.1.log', 20)
    outcome = world.run(world.spec(success_marker=WARMUP_SUCCESS, log_max_bytes=16 * MB))
    assert outcome.reason == 'exited_after_grace' and outcome.quit_forced
    assert outcome.rule_counts['log_flood'] == 1 and outcome.fatal_rule == ''


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
