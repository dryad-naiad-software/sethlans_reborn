# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The ``shots`` job kind on the worker (sequence rendering D3, FR-SD-3, FR-SD-4, FR-SD-8): the payload rules, no
memory gate but the Unreal cards still held, the prep-only flow, and ``shots-result/`` delivery.
"""
import json
from types import SimpleNamespace

import pytest

from sethlans_worker_agent import api_extras, api_sequence, config
from sethlans_worker_agent.unreal_executor import job_params, job_run, sequence_prep, vram_gate_check
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure
from sethlans_worker_agent.unreal_executor.sequence_flow import ShotsFlow, ShotsResult
from sethlans_worker_agent.unreal_executor.summary import Summary
from tests.unit.worker.unreal._executor_fakes import job, outcome, params
from tests.unit.worker.unreal._job_run_world import World
from tests.unit.worker.unreal.sequence.test_sequence_prep import RESULT
from tests.unit.worker.unreal.test_reserve import Sampler, make, token_free

KEYS = ('pci:0000:01:00.0', 'pci:0000:23:00.0')


class TestPayload:

    def test_a_shots_job_is_valid_with_frame_0_and_no_peak(self):
        p = params(kind='shots', frame=0)
        assert p.kind == 'shots' and p.is_shots and not p.is_pilot and p.frame is None and p.machine_results == ()

    @pytest.mark.parametrize('changes,field', [
        ({'start_frame': 5, 'end_frame': 5}, 'start_frame'), ({'end_frame': 1}, 'start_frame'),
        ({'start_frame': None}, 'start_frame'), ({'unreal_machine_results': [{}]}, 'unreal_machine_results'),
        ({'unreal_kind': 'sound'}, 'unreal_kind'),
    ])
    def test_bad_shots_payloads(self, changes, field):
        with pytest.raises(job_params.JobDataError) as caught:
            job_params.validate_job_data(job(kind='shots', frame=0, **changes))
        assert caught.value.field == field


class TestReservation:

    def test_no_memory_gate_and_no_reading_needed(self):
        gate = vram_gate_check.evaluate(params(kind='shots', frame=0), None)
        assert gate.ok and gate.gate == 'skipped' and gate.card_key is None and gate.summary == {'vram_gate': 'skipped'}

    @pytest.mark.parametrize('sampler', [None, Sampler(None)])
    def test_reserved_without_a_reading_and_holding_the_unreal_cards(self, sampler):
        reserver, reach = make(sampler=lambda: sampler, card_keys=lambda: KEYS)
        reservation = reserver.try_reserve(job(kind='shots', frame=0))
        assert reservation is not None and reservation.card_keys == KEYS  # reserve_exclusive marks them (D3)
        assert reservation.gate.gate == 'skipped' and reservation.gate.card_key is None
        assert reach.calls  # every other check still runs (here: the share probes)
        reservation.release()
        assert token_free(reserver)

    def test_still_declined_without_an_unreal_card_in_use(self):
        reserver, _reach = make(sampler=lambda: None, card_keys=lambda: ())
        assert reserver.try_reserve(job(kind='shots', frame=0)) is None and token_free(reserver)


class Runner:
    def __init__(self, job, write=RESULT, exit_code=0):
        self.job, self.write, self.exit_code, self.launches = job, write, exit_code, []

    def launch(self, phase, attempt, launch, **kwargs):
        self.launches.append((phase, attempt, launch, kwargs))
        if self.write is not None:
            self.job.prep_result.write_text(json.dumps(self.write), encoding='utf-8')
        return SimpleNamespace(outcome=outcome(exit_code=self.exit_code, wall_s=12.0))


def _flow(tmp_path, **runner_kw):
    job = SimpleNamespace(params=tmp_path / 'params.json', prep_result=tmp_path / 'prep_result.json',
                          scripts=tmp_path / 'scripts', script=lambda name: tmp_path / 'scripts' / name,
                          log=lambda phase, attempt: tmp_path / f'{phase}.{attempt}.log')
    runner, summary = Runner(job, **runner_kw), Summary()
    builders = SimpleNamespace(build_prep=lambda *args: ('prep-launch', args))

    def no_choice(*args, **kwargs):
        raise AssertionError('a shots job never chooses a card')
    flow = ShotsFlow(params(kind='shots', frame=0), 'ctx', job, runner, summary, builders, None, 'Demo.uproject',
                     None, choose=no_choice)
    return flow, runner, summary, job


class TestFlow:

    def test_it_runs_the_prep_alone_in_mode_list_shots(self, tmp_path):
        flow, runner, summary, job = _flow(tmp_path)
        result = flow.run()
        (phase, attempt, launch, kwargs), = runner.launches  # no warm-up, no render
        assert (phase, attempt, kwargs['sample']) == ('prep', 1, False)
        assert launch[1][0] == 'ctx' and launch[1][2] == job.script('sethlans_prepare_queue.py')  # no adapter ctx
        written = json.loads(job.params.read_text(encoding='utf-8'))
        assert written['mode'] == 'list_shots' and written['scripts_dir'] == str(job.scripts)
        assert isinstance(result, ShotsResult) and result.prep_s == 12.0
        assert result.discovery.body()['shots'] == RESULT['shots']
        assert (summary.get('shots'), summary.get('shot_source'), summary.get('frame_rate'), summary.get('warmup'),
                summary.get('playback_range'), summary.get('game_overrides')) == \
            ('3', 'shot_tracks', '30000/1001', 'skipped', '0-400', 'preset')
        assert list(summary.warnings()) == RESULT['warnings']

    @pytest.mark.parametrize('runner_kw,message', [
        ({'write': None, 'exit_code': 3}, 'Unreal prep pass failed (exit code 3).'),
        ({'write': {'ok': False, 'error': 'Shot discovery failed: no shot track or camera cut found inside the '
                                          'playback range.'}, 'exit_code': -1},
         'Shot discovery failed: no shot track or camera cut found inside the playback range.'),
        ({'write': dict(RESULT, shots=[['a', 0, 20], ['b', 10, 30]])},
         'Shot discovery returned an invalid result (shots[1]).'),
    ])
    def test_failures(self, tmp_path, runner_kw, message):
        flow, _runner, _summary, _job = _flow(tmp_path, **runner_kw)
        with pytest.raises(JobFailure) as caught:
            flow.run()
        assert caught.value.message == message


class ShotsWorld(World):

    def __init__(self, tmp_path, monkeypatch, report=None):
        super().__init__(tmp_path, monkeypatch)
        self.params = params(kind='shots', frame=0)
        self.shots_report = report or api_extras.PilotReport('done', 200)
        monkeypatch.setattr(job_run, 'ShotsFlow', self._flow_factory)
        self.flow_action = lambda job: ShotsResult(sequence_prep.parse_shots(RESULT), 12.0)


class TestDelivery:

    @pytest.mark.parametrize('outcome', ['done', 'terminal', 'failed'])
    def test_the_discovery_goes_to_shots_result_and_nothing_more_is_reported(self, tmp_path, monkeypatch, outcome):
        world = ShotsWorld(tmp_path, monkeypatch, api_extras.PilotReport(outcome, 200))
        result = world.run()
        assert result.success and result.released and not result.error_message
        (_name, job_id, body), = [call for call in world.calls if call[0] == 'shots']
        assert job_id == 42 and body == sequence_prep.parse_shots(RESULT).body()
        assert 'upload' not in world.names() and 'pilot' not in world.names() and 'release' not in world.names()
        assert '[sethlans-unreal] kind=shots' in result.stdout and 'vram_gate=skipped' in result.stdout

    def test_a_rejected_result_fails_the_job(self, tmp_path, monkeypatch):
        world = ShotsWorld(tmp_path, monkeypatch, api_extras.PilotReport('rejected', 400, "{'shots': ['bad']}"))
        result = world.run()
        assert not result.success and not result.released
        assert result.error_message == "Shot discovery result rejected by the manager: {'shots': ['bad']}"

    def test_a_failure_in_the_flow_is_the_jobs_error_without_a_prefix(self, tmp_path, monkeypatch):
        world = ShotsWorld(tmp_path, monkeypatch)

        def fail(job):
            raise JobFailure("Shot sections a and b overlap; Sethlans cannot tell which one renders.")
        world.flow_action = fail
        result = world.run()
        assert not result.success and 'shots' not in world.names()
        assert result.error_message == "Shot sections a and b overlap; Sethlans cannot tell which one renders."


def test_report_shots_result_posts_to_its_endpoint(monkeypatch):
    calls = []
    monkeypatch.setattr(config, 'MANAGER_API_URL', 'https://m/api/')
    monkeypatch.setattr(api_extras, 'get_auth_headers', lambda: {'Authorization': 'Token t'})
    monkeypatch.setattr(api_extras, 'handle_auth_response', lambda response: False)
    responses = [SimpleNamespace(status_code=503, json=lambda: {}, text=''),
                 SimpleNamespace(status_code=200, json=lambda: {}, text='')]
    session = SimpleNamespace(request=lambda method, url, **kw: (calls.append((method, url, kw)), responses.pop(0))[1])
    monkeypatch.setattr(api_extras, 'get_session', lambda: session)
    report = api_sequence.report_shots_result(7, {'shots': []}, sleep=lambda s: None)
    assert report.outcome == 'done' and [c[1] for c in calls] == ['https://m/api/jobs/7/shots-result/'] * 2
    assert calls[0][2]['json'] == {'shots': []}


def test_production_wiring_and_report_shots_is_required():
    import dataclasses
    from sethlans_worker_agent.unreal_executor import executor
    from sethlans_worker_agent.unreal_executor.delivery import DeliveryDeps
    assert executor.default_run_deps().delivery.report_shots is api_sequence.report_shots_result
    field, = [f for f in dataclasses.fields(DeliveryDeps) if f.name == 'report_shots']
    assert field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING
    with pytest.raises(TypeError):
        DeliveryDeps(release_job=None, mark_unreachable=None, upload=None, report_pilot=None)
