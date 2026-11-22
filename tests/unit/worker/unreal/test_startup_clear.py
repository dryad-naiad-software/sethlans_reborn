# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Where a claim stops being *starting* (render per card FR-PC-SU-1, AC-11): the render phase's first
output file (the runner's tick, at most every 2 s), every release path (claim refused, failure or
cancel before the first frame, normal end, dispatch failure) and ``retain_until_restart``. An audio
or shots job stays starting until it ends.
"""
import dataclasses
import logging

import pytest

from sethlans_worker_agent import job_processor, job_resources
from sethlans_worker_agent.unreal_executor import job_run, phases
from sethlans_worker_agent.unreal_executor.output_watch import FirstOutput
from tests.unit.worker.unreal._executor_fakes import job
from tests.unit.worker.unreal._job_run_world import World
from tests.unit.worker.unreal._process_runner_world import World as RunnerWorld
from tests.unit.worker.unreal.test_phases_card_cut import Job
from tests.unit.worker.unreal.test_reserve import make
from tests.unit.worker.unreal.test_slot_release import _cancel, _flow_bug, _prep_bug, _prep_share, _Run


class TestFirstOutputFile:

    def test_the_callback_runs_once_at_the_first_file(self, tmp_path):
        calls = []
        watch = FirstOutput(tmp_path, on_seen=lambda: calls.append(1))
        assert watch.seen(0.0) is False and calls == []
        (tmp_path / 'sethlans_42.1000.png').write_bytes(b'x')
        assert watch.seen(1.0) is False and calls == []       # checked at most every 2 s
        assert watch.seen(2.0) is True and calls == [1]
        assert watch.seen(5.0) is True and calls == [1]       # latched: never again for the launch

    def test_no_folder_never_calls_it(self, tmp_path):
        calls = []
        FirstOutput(None, on_seen=lambda: calls.append(1)).seen(0.0)
        FirstOutput(tmp_path / 'missing', on_seen=lambda: calls.append(1)).seen(0.0)
        assert calls == []

    def test_a_failing_callback_is_logged_and_never_ends_the_render(self, tmp_path, caplog):
        (tmp_path / 'f.png').write_bytes(b'x')
        watch = FirstOutput(tmp_path, on_seen=lambda: (_ for _ in ()).throw(RuntimeError('a bug')))
        with caplog.at_level(logging.ERROR):
            assert watch.seen(0.0) is True
        assert "start-up as over" in caplog.text

    def test_the_runner_tick_ends_the_start_up_without_a_memory_sample(self, tmp_path):
        """A render launch without GPU samples (e.g. the override) still ends the start-up at its first file."""
        world = RunnerWorld(tmp_path)
        out = tmp_path / 'output'
        out.mkdir()
        calls = []
        world.steps[3] = lambda: (out / 'f.png').write_bytes(b'x')
        world.steps[8] = lambda: setattr(world.root, 'alive', False)
        world.run(world.spec(first_output_dir=out, on_first_output=lambda: calls.append(world.clock.now)))
        assert len(calls) == 1

    def test_a_launch_that_writes_nothing_never_ends_it(self, tmp_path):
        world = RunnerWorld(tmp_path)
        out = tmp_path / 'output'
        out.mkdir()
        calls = []
        world.steps[4] = lambda: setattr(world.root, 'alive', False)
        world.run(world.spec(first_output_dir=out, on_first_output=lambda: calls.append(1)))
        assert calls == []


class TestPhaseSpec:

    @pytest.mark.parametrize('phase, wired', [('prep', False), ('warmup', False), ('render', True)])
    def test_only_the_render_phase_ends_the_start_up(self, phase, wired):
        def done():
            return None
        runner = phases.PhaseRunner(config=phases.PhaseConfig(
            cache_dir='c', zen_port=8668, worker_sid=None, merged_names=[], timeouts={phase: 1}, hang_s=900,
            quit_grace_s=60, log_max_bytes=10, log_flood_bytes_per_min=10), registry=object(), job_dir=Job(),
            cancel=lambda: None, shutdown_event=None, sampler=None, run=None, prelaunch=None,
            loopback_factory=lambda *a, **k: None, startup_done=done)
        spec = runner._spec(_Launch(phase), phase, 1, None, None, False, None, None)
        assert (spec.on_first_output is done) is wired
        assert (spec.first_output_dir is not None) is wired


class _Launch:
    def __init__(self, phase):
        self.phase = phase


class TestJobRunWiring:

    def test_a_picture_render_ends_its_start_up_at_its_first_file(self, tmp_path, monkeypatch):
        w = World(tmp_path, monkeypatch)
        w.flow_action = lambda job_: (_ for _ in ()).throw(RuntimeError('after the wiring'))
        w.run()
        assert w.runner.startup_done == w.reservation.startup_ended

    @pytest.mark.parametrize('kind, flow', [('audio', 'AudioFlow'), ('shots', 'ShotsFlow')])
    def test_audio_and_shots_stay_starting_until_the_job_ends(self, tmp_path, monkeypatch, kind, flow):
        w = World(tmp_path, monkeypatch)
        w.params = dataclasses.replace(w.params, kind=kind)
        monkeypatch.setattr(job_run, flow, w._flow_factory)
        w.flow_action = lambda job_: (_ for _ in ()).throw(RuntimeError('after the wiring'))
        w.run()
        assert w.runner.startup_done is None

    def test_startup_ended_clears_only_its_own_claim_and_logs(self, caplog):
        reserver, _ = make()
        first, second = reserver.try_reserve(job(job_id=1)), reserver.try_reserve(job(job_id=2))
        with caplog.at_level(logging.INFO, logger='sethlans_worker_agent.unreal_executor.reservation'):
            first.startup_ended()
            first.startup_ended()      # a second call does nothing
        assert reserver.startup.starting() == {second.claim_id}
        assert caplog.text.count("Unreal job 1 wrote its first output file; its start-up is over.") == 1


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(job_processor, 'get_worker_capacity', lambda: None)
    return _Run(tmp_path, monkeypatch)


def _starting(run):
    return run.reserver.startup.starting()


class TestEveryReleasePath:

    def test_a_refused_claim_clears_it(self, run):
        """``job_claim``: the manager refused the claim; nothing ran."""
        assert run.reservation.claim_id in _starting(run)
        assert job_resources.release_reservations(run.world.params.job_id, run.reservation, None) is True
        assert _starting(run) == {run.other.claim_id}

    @pytest.mark.parametrize('setup', [None, _prep_bug, _prep_share, _cancel, _flow_bug],
                             ids=['success', 'prep-failure', 'prep-share', 'cancel', 'flow-failure'])
    def test_the_release_after_the_run_clears_it(self, run, setup):
        if setup is not None:
            setup(run.world)
        run.execute()
        assert run.reservation.claim_id in _starting(run)     # the run itself never releases
        assert job_resources.release_reservations(run.world.params.job_id, run.reservation, None) is True
        assert _starting(run) == {run.other.claim_id}
        run.other.release()
        assert _starting(run) == frozenset()

    def test_a_dispatch_failure_clears_it(self, run):
        job_resources.release_after_dispatch_failure(run.job_data, None)
        assert _starting(run) == {run.other.claim_id}

    def test_retain_clears_it_but_keeps_the_slot(self, run):
        run.world.survivors = ((5, 'UnrealEditor-Cmd.exe'),)
        run.execute()
        assert run.reservation.retained and _starting(run) == {run.other.claim_id}
        assert run.reservation.claim_id not in run.reserver.ram.snapshot()
        assert job_resources.release_reservations(run.world.params.job_id, run.reservation, None) is False
        assert 1 not in run.reserver.slots.free()

    def test_a_failing_ledger_release_still_clears_it_and_gives_the_slot(self, run, monkeypatch):
        monkeypatch.setattr(run.reserver.ram, 'unregister', lambda claim_id: (_ for _ in ()).throw(RuntimeError('x')))
        run.reservation.release()           # the base reservation logs the callback's failure
        assert _starting(run) == {run.other.claim_id} and 1 in run.reserver.slots.free()
