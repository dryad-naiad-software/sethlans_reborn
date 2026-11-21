# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The RAM figures wired into the job (render per card FR-PC-RAM-2, -3, -5; D16): ``try_reserve``
registers each claim under its own id, the release drops it on every path and a retained claim
drops it but keeps its slot; the job's runner publishes under the claim; every summary carries the
per-phase keys; the pilot body carries the figures.
"""
from types import SimpleNamespace

import pytest

from sethlans_worker_agent import job_processor, job_resources
from sethlans_worker_agent.unreal_executor import executor, ram_ledger
from sethlans_worker_agent.unreal_executor.reservation import UnrealReservation
from sethlans_worker_agent.unreal_executor.runner_ram import PhaseRam, RamTarget
from tests.unit.worker.unreal._executor_fakes import job, outcome
from tests.unit.worker.unreal._job_run_world import World, pilot_flow
from tests.unit.worker.unreal.test_reserve import make
from tests.unit.worker.unreal.test_slot_release import _cancel, _flow_bug, _prep_bug, _Run


def _registered(reserver):
    return set(reserver.ram.snapshot())


class TestRegistration:

    def test_each_claim_is_registered_under_its_own_id(self):
        reserver, _ = make()
        first, second = reserver.try_reserve(job(job_id=1)), reserver.try_reserve(job(job_id=1))
        assert first.claim_id != second.claim_id and len(first.claim_id) == 32
        assert _registered(reserver) == {first.claim_id, second.claim_id}
        entry = reserver.ram.snapshot()[first.claim_id]
        # FR-PC-RAM-4: no measurement, so the defaults; commit = max(32768, 16384 + the 32607 MB card).
        assert (entry.need_ws, entry.need_commit) == (16384, 16384 + 32607)
        assert first.ram_target() == RamTarget(reserver.ram, first.claim_id, *((16384, 16384 + 32607), (16384, 16384 + 32607)))

    def test_a_declined_job_is_never_registered(self):
        reserver, _ = make(isolation_ok=lambda: False)
        assert reserver.try_reserve(job()) is None and _registered(reserver) == set()

    def test_a_failed_registration_holds_nothing(self, monkeypatch):
        reserver, _ = make()
        monkeypatch.setattr(reserver.ram, 'register', lambda *a: (_ for _ in ()).throw(ValueError('duplicate')))
        with pytest.raises(ValueError):
            reserver.try_reserve(job())
        assert reserver.slots.free() == tuple(range(16))

    def test_the_worker_uses_its_one_ledger(self):
        assert executor.default_reserve_deps().ram is ram_ledger.ledger

    def test_a_reservation_built_without_a_ledger_has_no_target(self):
        reservation = UnrealReservation(None, None, None, on_release=lambda: None)
        assert reservation.ram_target() is None
        reservation.retain_until_restart()   # nothing to unregister


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(job_processor, 'get_worker_capacity', lambda: None)
    return _Run(tmp_path, monkeypatch)


class TestEveryReleasePath:

    @pytest.mark.parametrize('setup', [None, _prep_bug, _cancel, _flow_bug], ids=['success', 'prep-failure',
                                                                                  'cancel', 'flow-failure'])
    def test_the_release_unregisters(self, run, setup):
        if setup is not None:
            setup(run.world)
        run.execute()
        assert run.reservation.claim_id in _registered(run.reserver)   # the run itself never releases
        assert job_resources.release_reservations(run.world.params.job_id, run.reservation, None) is True
        assert _registered(run.reserver) == {run.other.claim_id}
        run.other.release()
        assert _registered(run.reserver) == set()

    def test_retain_unregisters_but_keeps_the_slot(self, run):
        run.world.survivors = ((5, 'UnrealEditor-Cmd.exe'),)
        run.execute()
        assert run.reservation.retained and _registered(run.reserver) == {run.other.claim_id}
        assert job_resources.release_reservations(run.world.params.job_id, run.reservation, None) is False
        assert 1 not in run.reserver.slots.free()

    def test_a_dispatch_failure_unregisters(self, run):
        job_resources.release_after_dispatch_failure(run.job_data, None)
        assert _registered(run.reserver) == {run.other.claim_id}

    def test_the_runner_publishes_under_the_claim(self, run):
        run.execute()
        target = run.world.runner.ram_target
        assert (target.ledger, target.claim_id) == (run.reserver.ram, run.reservation.claim_id)


class _Phases:
    """``World``'s phase runner with launches that measured RAM."""

    def __init__(self, world, runs):
        self.world, self.runs = world, runs

    def __call__(self, config, registry, job_, cancel, shutdown, sampler):
        return SimpleNamespace(config=config, runs=self.runs, profile_reset=0, user_cvars=0, collisions=[],
                               zen_ok=True)


def _phase_run(phase, ws, commit, process, shader_workers=False):
    run = SimpleNamespace(phase=phase, outcome=outcome(ram=PhaseRam(ws, commit, process)))
    run.outcome.shader_workers = shader_workers
    run.outcome.adapter_chosen = 1   # a pilot's warm-up and render logs name their card
    return run


def _lines(result):
    return dict(line.split(' ', 1)[1].split('=', 1) for line in result.stdout.splitlines() if '=' in line)


class TestSummary:

    def test_a_job_that_failed_before_any_launch_carries_empty_keys(self, tmp_path, monkeypatch):
        w = World(tmp_path, monkeypatch)
        w.share_error = RuntimeError('a bug in the workspace prep')
        values = _lines(w.run())
        assert values['ram_prep_ws_mb'] == '' and values['ram_render_commit_mb'] == ''
        assert values['ram_warmup_cold'] == '0' and values['vram_job_peak_mb'] == ''

    def test_the_measured_figures_per_phase(self, tmp_path, monkeypatch):
        w = World(tmp_path, monkeypatch)
        w.flow_action = lambda job_: (_ for _ in ()).throw(RuntimeError('after the launches'))
        w.phase_runner = _Phases(w, [_phase_run('prep', 3000, 5000, 4000), _phase_run('warmup', 6000, 20000, 1, True),
                                     _phase_run('render', 9000, 28000, 2)])
        values = _lines(w.run())
        assert (values['ram_prep_ws_mb'], values['ram_prep_commit_mb']) == ('3000', '5000')
        assert (values['ram_warmup_ws_mb'], values['ram_render_commit_mb']) == ('6000', '28000')
        assert values['ram_warmup_cold'] == '1'


class TestPilotBody:

    def test_the_flows_figures_reach_the_body(self, tmp_path, monkeypatch):
        w = World(tmp_path, monkeypatch, kind='pilot')
        ram = {'prep': {'ws_mb': 3000, 'commit_mb': 5000}, 'warmup': {'ws_mb': 6000, 'commit_mb': 20000, 'cold': True},
               'render': {'ws_mb': 9000, 'commit_mb': 28000}}

        def flow(job_):
            result = pilot_flow(job_)
            result.ram, result.ram_process_commit_peak_mb = ram, 27000
            return result
        w.flow_action = flow
        w.run()
        body = next(call[1] for call in w.calls if call[0] == 'pilot')
        assert body['ram'] == ram and body['ram_process_commit_peak_mb'] == 27000
        assert body['shared_machine'] is False   # until the per-card hold (chunk 4a)

    def test_the_pilot_collect_fills_the_figures_from_its_launches(self, monkeypatch, tmp_path):
        from tests.unit.worker.unreal.test_job_flow_card import IDLE_KEY, _pilot_flow
        flow = _pilot_flow(monkeypatch, tmp_path, IDLE_KEY)
        flow.runner.runs = [_phase_run('prep', 3000, 5000, 4000), _phase_run('warmup', 6000, 20000, 15000),
                            _phase_run('render', 9000, 28000, 27000)]
        result = flow.run()
        assert result.ram['render'] == {'ws_mb': 9000, 'commit_mb': 28000} and result.ram['warmup']['cold'] is False
        assert result.ram_process_commit_peak_mb == 27000
