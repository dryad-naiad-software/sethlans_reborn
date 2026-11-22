# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The slot through the reserver, the executor's wiring, ``JobRun``, the pre-launch check, the phases and
start-up housekeeping (render per card FR-PC-SLOT-1, -2, -5, -6, -8; AC-4)."""
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from sethlans_worker_agent import config_unreal
from sethlans_worker_agent.executors.base import ExecutionContext, ProbePending
from sethlans_worker_agent.unreal_executor import (
    executor, isolation_state, job_run, phases, prelaunch, reserve, startup_recovery, user_state, vram_gate_check,
    zen_port, zen_private)
from sethlans_worker_agent.unreal_executor import slot as slots
from sethlans_worker_agent.unreal_executor.prelaunch import PreLaunch
from sethlans_worker_agent.unreal_executor.profile_config import ProfileConfigResult
from sethlans_worker_agent.unreal_executor.zen_log import ZenLogResult
from tests._unreal_worker_state import install, reachable
from tests.unit.worker.unreal._executor_fakes import KIND, job, outcome, reading
from tests.unit.worker.unreal._job_run_world import World, png_flow
from tests.unit.worker.unreal.test_isolation_state import FAIL, state  # noqa: F401 - fixture
from tests.unit.worker.unreal.test_phases_card_cut import Job
from tests.unit.worker.unreal.test_reserve import Reach, Sampler, make

ALL = tuple(range(16))


def _boom():
    raise RuntimeError('a bug in a check')


def _two_keyed():
    from sethlans_worker_agent.unreal_executor.vram import GpuMem
    return reading(gpus=tuple(GpuMem(i, 'a', 32607, 1000, None, None, f'pci:0000:2{3 + i}:00.0', card_kind=KIND)
                              for i in range(2)))


class TestReserverSlot:

    @pytest.mark.parametrize('kind', ['port', 'install', 'gate', 'fresh_gate', 'share', 'cards', 'pending',
                                      'raises'])
    def test_every_failure_path_returns_its_slot_and_keeps_the_others(self, kind):
        on = {'fail': False}
        reach = Reach({})

        def fresh():
            return reading(used=25000) if on['fail'] else reading()
        overrides = {
            'port': dict(port_ok=lambda slot: zen_port.PortCheck(ok=not on['fail'], error='x')),
            'install': dict(installs=lambda: () if on['fail'] else (install(),)),
            'gate': dict(sampler=lambda: Sampler(reading(used=25000) if on['fail'] else reading())),
            'fresh_gate': dict(sampler=lambda: Sampler(reading(), fresh=fresh())),
            'share': dict(share_state=lambda: reachable(ok=not on['fail'])),
            'cards': dict(card_keys=lambda: () if on['fail'] else ('pci:0000:23:00.0', 'pci:0000:24:00.0'),
                          sampler=lambda: Sampler(_two_keyed())),
            'pending': dict(reachability=lambda: reach),
            'raises': dict(installs=lambda: _boom() if on['fail'] else (install(),)),
        }[kind]
        reserver, _reach = make(**overrides)
        first = reserver.try_reserve(job(job_id=1))
        assert first.slot == 0
        on['fail'] = True
        reach.results['incoming/Demo.zip'] = None
        if kind in ('pending', 'raises'):
            with pytest.raises(ProbePending if kind == 'pending' else RuntimeError):
                reserver.try_reserve(job(job_id=2))
        else:
            assert reserver.try_reserve(job(job_id=2)) is None
        assert reserver.slots.free() == ALL[1:]
        first.release()
        assert reserver.slots.free() == ALL

    def test_the_port_check_is_for_the_reserved_slot(self):
        seen = []
        reserver, _reach = make(port_ok=lambda slot: (seen.append(slot), zen_port.PortCheck(ok=True))[1])
        first, second = reserver.try_reserve(job(job_id=1)), reserver.try_reserve(job(job_id=2))
        assert seen == [0, 1] and (first.slot, second.slot) == (0, 1)

    def test_no_free_slot_declines_before_any_check(self):
        calls = []
        reserver, reach = make(installs=lambda: (calls.append('install'), (install(),))[1])
        for _ in ALL:
            reserver.slots.take()
        assert reserver.try_reserve(job()) is None and calls == [] and reach.calls == []

    def test_release_gives_the_slot_back_and_a_retained_reservation_keeps_it(self):
        reserver, _reach = make()
        kept, freed = reserver.try_reserve(job(job_id=1)), reserver.try_reserve(job(job_id=2))
        kept.retain_until_restart()
        kept.release()
        freed.release()
        freed.release()  # idempotent: given back once
        assert reserver.slots.free() == ALL[1:]


class TestExecutorWiring:

    def test_check_6a_uses_the_slots_port(self, monkeypatch):
        seen = []
        monkeypatch.setattr(executor, '_cache_dir', lambda: r'D:\uecache')
        monkeypatch.setattr(startup_recovery, 'worker_sid', lambda: 'S-1')
        monkeypatch.setattr(zen_port, 'check_port', lambda port, cache, worker_sid=None, slot=None:
                            (seen.append((port, cache, worker_sid, slot)), zen_port.PortCheck(ok=True))[1])
        assert executor._port_ok(3).ok
        assert seen == [(config_unreal.ZEN_PORT + 3, r'D:\uecache', 'S-1', 3)]

    def test_launch_context_and_phase_config_take_the_slot(self):
        slot = slots.slot_for(r'D:\uecache', 8668, 2)
        deps = executor.default_run_deps()
        ctx = deps.launch_context(install(), slot)
        expected = (r'D:\uecache', 8670, r'D:\uecache\ueprofile-s2', 2)
        assert (ctx.cache_dir, ctx.zen_port, ctx.profile, ctx.slot) == expected
        cfg = deps.phase_config(slot, ['r.X'], install())
        assert (cfg.cache_dir, cfg.zen_port, cfg.slot) == (r'D:\uecache', 8670, 2)
        own = zen_private.zenserver_images([], r'D:\uecache\ueprofile-s2')
        assert own <= cfg.excluded_images
        assert not zen_private.zenserver_images([], r'D:\uecache\ueprofile') <= cfg.excluded_images


class TestJobRunSlot:

    def test_the_slot_reaches_the_work_copy_launches_marker_profile_and_summary(self, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch)
        world.flow_action = png_flow
        seen = {}
        flow_factory, deps = world._flow_factory, world.deps()
        prepare = deps.prepare_workspace

        def flow(*args, **kwargs):
            seen['marker'] = args[6]
            return flow_factory(*args, **kwargs)

        def prepare_workspace(cache, request, **kwargs):
            seen['request'] = request
            return prepare(cache, request, **kwargs)
        monkeypatch.setattr(job_run, 'UeFlow', flow)
        deps.prepare_workspace = prepare_workspace
        deps.phase_config = lambda slot, names, inst: (seen.__setitem__('phase', slot),
                                                       SimpleNamespace(zen_port=slot.zen_port))[1]
        deps.launch_context = lambda inst, slot: seen.__setitem__('ctx', slot)
        deps.flow_config = lambda profile: seen.__setitem__('profile', profile)
        gate = vram_gate_check.evaluate(world.params, world.reading)
        reservation = reserve.UnrealReservation(world.params, install(), gate, on_release=lambda: None, slot=3)
        ex = executor.UnrealExecutor(reserve_deps=executor.default_reserve_deps(), run_deps=deps)
        result = ex.execute({'id': world.params.job_id, '_renderer_reservation': reservation},
                            ExecutionContext(None, None, None, threading.Event()))
        assert seen['request'].slot == 3  # FR-PC-SLOT-6: w/<key16><ov8>-s3
        assert seen['phase'] == seen['ctx'] == slots.slot_for(world.layout.root, config_unreal.ZEN_PORT, 3)
        assert seen['profile'] == user_state.profile_dir(world.layout.root, 3)
        assert seen['marker'].name == f"{world.params.engine_build}-s3.json"
        lines = result.stdout.splitlines()
        assert '[sethlans-unreal] slot=3' in lines and '[sethlans-unreal] card=' in lines
        assert f'[sethlans-unreal] zen=private:{config_unreal.ZEN_PORT + 3}' in lines

    def test_slot_0_is_the_default_reservation(self, tmp_path, monkeypatch):
        world = World(tmp_path, monkeypatch)
        world.flow_action = png_flow
        assert '[sethlans-unreal] slot=0' in world.run().stdout.splitlines()


class TestPrelaunchSlot:

    def test_the_slots_profile_zen_folders_port_and_known_folder_check(self, tmp_path, monkeypatch):
        cache = tmp_path / 'uecache'
        (cache / 'trash').mkdir(parents=True)
        seen = {}
        monkeypatch.setattr(isolation_state, 'check_now', lambda profile=None: (
            seen.__setitem__('known', profile), user_state.KnownFolderResult(ok=True))[1])
        result = prelaunch.check_before_launch(
            cache, 8670, [], True, slot=2,
            config_check=lambda p, t, m: (seen.__setitem__('config', p), ProfileConfigResult())[1],
            port_check=lambda port, c, worker_sid=None, slot=None: (
                seen.__setitem__('port', (port, slot)), zen_port.PortCheck(ok=True))[1])
        profile = cache / 'ueprofile-s2'
        assert result.ok and seen == {'known': profile, 'config': profile, 'port': (8670, 2)}
        assert user_state.missing_folders(profile) == ()
        assert (cache / 'zen-s2' / 'data').is_dir() and (cache / 'zen-s2' / 'fs').is_dir()
        assert not (cache / 'ueprofile').exists() and not (cache / 'zen').exists()


class TestKnownFolderCheckPerSlot:

    def test_another_slots_probe_does_not_change_the_node_condition(self, state, caplog):  # noqa: F811
        _clock, results, calls = state
        slot0, slot1 = Path(r'D:\uecache\ueprofile'), Path(r'D:\uecache\ueprofile-s1')
        assert isolation_state.register(slot0).ok
        results.append(FAIL)
        assert not isolation_state.check_now(slot1).ok
        assert isolation_state.latest().ok and isolation_state.reason() == ''
        assert 'ueprofile-s1' in caplog.text
        results.append(FAIL)
        assert not isolation_state.check_now(slot0).ok and not isolation_state.latest().ok
        assert calls == [slot0, slot1, slot0]
        assert isolation_state.check_now().ok and calls[-1] == slot0

    def test_nothing_runs_for_a_slot_before_registration(self, state):  # noqa: F811
        assert isolation_state.check_now(Path(r'D:\uecache\ueprofile-s1')) is None and state[2] == []


def test_phases_pass_the_slot_to_the_prelaunch_and_zen_checks():
    seen = {}
    config = phases.PhaseConfig(cache_dir='D:\\c', zen_port=8671, worker_sid=None, merged_names=[], slot=3,
                                timeouts={'prep': 1, 'warmup': 2, 'render': 3}, hang_s=900, quit_grace_s=60,
                                log_max_bytes=10, log_flood_bytes_per_min=10)
    runner = phases.PhaseRunner(
        config=config, registry=object(), job_dir=Job(), cancel=lambda: None, shutdown_event=None, sampler=None,
        run=lambda spec, registry: outcome(),
        prelaunch=lambda *a, **k: (seen.__setitem__('prelaunch', (a[:2], k['slot'])), PreLaunch(ok=True))[1],
        verify_zen=lambda *a, **k: (seen.__setitem__('zen', (a[1:3], k['slot'])), ZenLogResult(ok=True))[1],
        loopback_factory=lambda *a, **k: seen.__setitem__('loopback', (a, k['slot'])))
    runner.launch('prep', 1, SimpleNamespace())
    assert seen == {'prelaunch': (('D:\\c', 8671), 3), 'zen': (('D:\\c', 8671), 3), 'loopback': ((8671, 'D:\\c'), 3)}


def test_startup_housekeeps_every_existing_slot_profile(tmp_path, monkeypatch):
    for name in ('ueprofile-s2', 'ueprofile-s15', 'zen-s4'):
        (tmp_path / name).mkdir()
    (tmp_path / 'ueprofile-s3').write_text('not a folder')
    seen = []
    monkeypatch.setattr(startup_recovery.user_state, 'repair_profile', lambda p, t: None)
    monkeypatch.setattr(startup_recovery.zen_private, 'ensure_zen_dirs', lambda c: None)
    monkeypatch.setattr(isolation_state, 'register', lambda p: None)
    monkeypatch.setattr(startup_recovery.user_state, 'housekeeping', lambda p: seen.append(Path(p).name))
    startup_recovery._step_1b(tmp_path)
    assert seen == ['ueprofile', 'ueprofile-s2', 'ueprofile-s15']
