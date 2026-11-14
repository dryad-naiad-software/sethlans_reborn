# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
One UE launch (``PhaseRunner``: chunk 7 req. 10, 11, 15; FR-UE-26, FR-UE-27)
and the startup recovery wiring (FR-PA-18, FR-UE-23, req. 1 and 5).
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from sethlans_worker_agent import config_unreal
from sethlans_worker_agent.unreal_executor import capabilities, isolation_state, phases, startup_recovery, zen_port
from sethlans_worker_agent.unreal_executor.outcomes import Cancelled, JobFailure, LocalConflict, SurvivorsLeft
from sethlans_worker_agent.unreal_executor.prelaunch import PreLaunch
from sethlans_worker_agent.unreal_executor.zen_log import ZenLogResult
from tests._unreal_worker_state import enable_unreal, reachable, unreal_runtime_reset  # noqa: F401
from tests.unit.worker.unreal._executor_fakes import outcome, reading

REAL_RUN_ONCE = startup_recovery.run_once


class Job:
    def log(self, phase, n):
        return Path(f'{phase}.{n}.log')

    def stdout(self, phase, n):
        return Path(f'{phase}.{n}.stdout.txt')

    ledger = Path('procs.json')


class Sampler:
    def __init__(self):
        self.calls = 0

    def fresh(self, max_age_s, deadline):
        self.calls += 1
        return reading(used=1000 + self.calls)


def runner(run_outcome=None, pre=None, zen_ok=True, **kwargs):
    calls = {'prelaunch': [], 'specs': [], 'disable': [], 'watches': []}
    pre = pre or PreLaunch(ok=True, profile_reset=1, user_cvars=0, user_cvar_collisions=('r.X',))

    def prelaunch(cache_dir, port, names, first, worker_sid=None):
        calls['prelaunch'].append(first)
        return pre

    def run(spec, registry):
        calls['specs'].append(spec)
        return run_outcome or outcome()

    def watch(port, cache_dir, worker_sid=None):
        calls['watches'].append(object())
        return calls['watches'][-1]

    config = phases.PhaseConfig(cache_dir='D:\\c', zen_port=8668, worker_sid=None, merged_names=['r.X'],
                                timeouts={'prep': 1, 'warmup': 2, 'render': 3}, hang_s=900, quit_grace_s=60,
                                log_max_bytes=10, log_flood_bytes_per_min=10)
    r = phases.PhaseRunner(config=config, registry=object(), job_dir=Job(), cancel=lambda: None, shutdown_event=None,
                           sampler=kwargs.get('sampler'), run=run, prelaunch=prelaunch,
                           verify_zen=lambda *a, **k: ZenLogResult(ok=zen_ok, detail='bad'),
                           loopback_factory=watch, disable_until_restart=calls['disable'].append)
    return r, calls


def test_first_launch_flag_fresh_watch_and_specs():
    r, calls = runner()
    r.launch('prep', 1, SimpleNamespace())
    r.launch('render', 1, SimpleNamespace())
    assert calls['prelaunch'] == [True, False]
    first, second = calls['specs']
    assert first.tick_check is not second.tick_check and first.timeout_s == 1 and second.timeout_s == 3
    assert first.vram_sample is None and r.profile_reset == 2 and r.collisions == ['r.X'] and r.zen_ok


def test_sampled_phases_record_pre_and_post_readings():
    sampler = Sampler()
    r, calls = runner(sampler=sampler)
    run = r.launch('render', 1, SimpleNamespace(), sample=True)
    assert run.pre == {0: 1001} and run.post == {0: 1002} and calls['specs'][0].vram_baseline == {0: 1001}
    assert calls['specs'][0].vram_sample is not None


@pytest.mark.parametrize('first,reason', [(True, 'dispatch_failed'), (False, 'local_conflict')])
def test_prelaunch_failure_is_a_local_conflict(first, reason):
    r, calls = runner(pre=PreLaunch(ok=False, release_reason=reason, message='port held'))
    r.first_launch = first
    with pytest.raises(LocalConflict) as info:
        r.launch('prep', 1, SimpleNamespace())
    assert info.value.reason == reason and calls['specs'] == []


def test_survivors_cancel_and_loopback():
    r, _ = runner(outcome(survivors=((1, 'x'),)))
    with pytest.raises(SurvivorsLeft):
        r.launch('render', 1, SimpleNamespace())
    r, _ = runner(outcome(reason='reassigned'))
    with pytest.raises(Cancelled, match='reassigned'):
        r.launch('render', 1, SimpleNamespace())
    r, calls = runner(outcome(reason='local_conflict', detail='farm zenserver listened on a non-loopback address'))
    with pytest.raises(LocalConflict) as info:
        r.launch('render', 1, SimpleNamespace())
    assert info.value.reason == 'local_conflict' and calls['disable'] == [zen_port.LOOPBACK_DISABLE_REASON]


def test_zen_failure_fails_only_a_phase_that_otherwise_succeeded():
    r, _ = runner(zen_ok=False)
    with pytest.raises(JobFailure, match='farm-private Zen data path'):
        r.launch('prep', 1, SimpleNamespace())
    r, _ = runner(outcome(reason='timeout'), zen_ok=False)
    run = r.launch('prep', 1, SimpleNamespace())
    assert not run.zen_ok and not r.zen_ok


class TestStartupRecovery:

    @pytest.fixture(autouse=True)
    def _reset(self, unreal_runtime_reset):  # noqa: F811
        yield

    def _enable(self, monkeypatch, tmp_path, reason=''):
        calls = []
        enable_unreal(monkeypatch, recovered=False, share=r'\\nas.lan\sethlans', share_state=reachable())
        monkeypatch.setattr(startup_recovery, 'run_once', REAL_RUN_ONCE)  # enable_unreal stubs it out
        prepared = SimpleNamespace(path=tmp_path)
        monkeypatch.setattr(startup_recovery.runtime, 'prepared_cache_dir', lambda: prepared)
        monkeypatch.setattr(startup_recovery.runtime, 'static_reason', lambda: '')
        monkeypatch.setattr(startup_recovery, 'worker_sid', lambda: 'S-1')
        monkeypatch.setattr(isolation_state, 'expect', lambda: calls.append('expect'))
        monkeypatch.setattr(isolation_state, 'register', lambda p: calls.append(('register', Path(p).name)))
        monkeypatch.setattr(startup_recovery.user_state, 'repair_profile', lambda p, t: calls.append('repair'))
        monkeypatch.setattr(startup_recovery.user_state, 'housekeeping', lambda p: calls.append('housekeeping'))
        monkeypatch.setattr(startup_recovery.zen_private, 'ensure_zen_dirs', lambda c: calls.append('zen'))
        monkeypatch.setattr(startup_recovery.runtime, 'disable_until_restart', lambda r: calls.append(('disable', r)))

        def recover(prepared, limits, keep_job_logs, sweep):
            calls.append(('recover', keep_job_logs))
            sweep()
            return SimpleNamespace(reason=reason, cache=None if reason else 'CACHE')
        monkeypatch.setattr(startup_recovery.recovery, 'recover', recover)

        def sweep(*args):
            calls.append(('sweep', args[2]))
            return SimpleNamespace(survivors=())
        monkeypatch.setattr(startup_recovery.orphans, 'sweep', sweep)
        return calls

    def test_success_order_and_poll_gate(self, monkeypatch, tmp_path):
        calls = self._enable(monkeypatch, tmp_path)
        assert capabilities.poll_params(node_idle=True) == {}  # not before recover() succeeded
        startup_recovery.run_once()
        startup_recovery.run_once()  # exactly once
        assert calls == ['expect', ('recover', config_unreal.KEEP_JOB_LOGS), ('sweep', 'S-1'), 'repair', 'zen',
                         ('register', 'ueprofile'), 'housekeeping']
        assert startup_recovery.ready() and startup_recovery.cache() == 'CACHE'
        assert capabilities.poll_params(node_idle=True)['renderers'] == 'BLENDER,UNREAL'

    def test_failed_recovery_disables_and_keeps_unreal_out_of_polls(self, monkeypatch, tmp_path):
        calls = self._enable(monkeypatch, tmp_path, reason='could not clear Unreal working directories')
        startup_recovery.run_once()
        assert ('disable', 'could not clear Unreal working directories') in calls and not startup_recovery.ready()
        assert 'register' not in [c[0] if isinstance(c, tuple) else c for c in calls]

    def test_sweep_survivors_disable_until_restart(self, monkeypatch):
        disabled = []
        monkeypatch.setattr(startup_recovery.runtime, 'disable_until_restart', disabled.append)
        monkeypatch.setattr(startup_recovery.orphans, 'sweep',
                            lambda *a: SimpleNamespace(survivors=((1, 'UnrealEditor-Cmd.exe'),)))
        with pytest.raises(startup_recovery.SweepSurvivorsError):
            startup_recovery.make_sweep('D:\\c', [], None)()
        assert disabled == ['orphaned Unreal processes']

    def test_disabled_by_config_does_nothing(self, monkeypatch):
        monkeypatch.setattr(config_unreal, 'ENABLED', False)
        monkeypatch.setattr(startup_recovery.recovery, 'recover', lambda *a, **k: pytest.fail('ran'))
        startup_recovery.run_once()
        assert not startup_recovery.ready()


def test_no_launch_after_a_cancel_cause_or_shutdown():
    r, calls = runner()
    r.cancel = lambda: 'canceled'
    with pytest.raises(Cancelled, match='canceled'):
        r.launch('render', 1, SimpleNamespace())
    r, calls2 = runner()
    r.shutdown_event = SimpleNamespace(is_set=lambda: True)
    with pytest.raises(Cancelled, match='shutdown'):
        r.launch('render', 1, SimpleNamespace())
    assert calls['prelaunch'] == calls2['prelaunch'] == [] and calls['specs'] == []


def test_the_zen_guard_runs_again_before_every_launch():
    from sethlans_worker_agent.unreal_executor.zen_guard import GuardHit, ZenGuardResult
    r, calls = runner()
    scans = []
    r.project_dir = Path('Demo')
    r.zen_scan = lambda path: (scans.append(path), ZenGuardResult())[1]
    r.launch('prep', 1, SimpleNamespace())
    r.zen_scan = lambda path: ZenGuardResult(hits=(GuardHit('Saved/Config/x.ini', 'Zen.AutoLaunch', 'DataPath'),))
    with pytest.raises(JobFailure, match=r'Saved/Config/x.ini: \[Zen.AutoLaunch\] DataPath'):
        r.launch('warmup', 1, SimpleNamespace())
    assert scans == [Path('Demo')] and len(calls['specs']) == 1


def test_sweep_survivors_raise_so_recovery_deletes_nothing(monkeypatch, tmp_path):
    from sethlans_worker_agent.project_archive import owner_lock, recovery
    disabled = []
    monkeypatch.setattr(startup_recovery.runtime, 'disable_until_restart', disabled.append)
    monkeypatch.setattr(startup_recovery.orphans, 'sweep', lambda *a: SimpleNamespace(survivors=((1, 'x.exe'),)))
    monkeypatch.setattr(owner_lock, 'held_for_process', lambda path: True)
    monkeypatch.setattr(recovery, '_clean', lambda *a: pytest.fail('deleted after a failed sweep'))
    result = recovery.recover(SimpleNamespace(path=tmp_path), None, keep_job_logs=1,
                              sweep=startup_recovery.make_sweep(tmp_path, [], None))
    assert result.cache is None and result.reason.startswith(recovery.SWEEP_REASON)
    assert disabled == ['orphaned Unreal processes']
