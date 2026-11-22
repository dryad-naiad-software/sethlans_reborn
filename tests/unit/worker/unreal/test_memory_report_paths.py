# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The memory report on every end path of ``JobRun.run`` (Unreal per-machine memory test FR-MT-S-4).

Once per render or audio job whose render launch ran, after exit confirmation and the workspace finish, and
**before** the completion call (the upload, the ERROR or CANCELED report, ``release/``) on success, failure,
cancel, release, survivors and shutdown; never for a pilot or a shots job, never without a render launch.
The completion goes ahead whatever the report's answer. The summary carries the spill keys and
``memory_report``.
"""
import dataclasses
import threading

import pytest

from sethlans_worker_agent import api_extras
from sethlans_worker_agent.project_archive.errors import AbortedError
from sethlans_worker_agent.unreal_executor import job_run
from sethlans_worker_agent.unreal_executor.outcomes import Cancelled, JobFailure, LocalConflict, SurvivorsLeft
from sethlans_worker_agent.unreal_executor.summary import Summary
from tests.unit.worker.unreal._executor_fakes import params
from tests.unit.worker.unreal._job_run_world import World, pilot_flow, png_flow
from tests.unit.worker.unreal._memory_report_fakes import render_run, watch

CARD_KEY = 'pci:0000:01:00.0'


def world(tmp_path, monkeypatch, kind='render', answer=None):
    w = World(tmp_path, monkeypatch, kind=kind)
    w.card_key = CARD_KEY
    deps = w.deps()

    def report_memory(job_id, body, **kwargs):
        w.calls.append(('memory', job_id, body, kwargs))
        return answer or api_extras.PilotReport('done', 200)
    deps.delivery = dataclasses.replace(deps.delivery, report_memory=report_memory)
    w.deps = lambda: deps
    return w


def rendered(w, tmp_path, then, spill_watch=None, max_job=7300):
    """A flow whose render launch ran (it is in ``runner.runs``), then ``then(job)``."""
    def action(job):
        w.flow.choice, w.flow.warmup_logs, w.flow.summary = None, [], Summary()  # pilot_flow.driver reads them
        w.runner.runs.append(render_run(tmp_path, max_job=max_job, spill_watch=spill_watch))
        return then(job)
    return action


def throw(exc):
    def raise_it(job):
        raise exc
    return raise_it


def memory_calls(w):
    return [call for call in w.calls if call[0] == 'memory']


class TestEveryEndPath:

    def test_success_reports_before_the_upload(self, tmp_path, monkeypatch):
        w = world(tmp_path, monkeypatch)
        w.flow_action = rendered(w, tmp_path, png_flow, spill_watch=watch(spilled=True, cause='self', shared=2000))
        result = w.run()
        assert result.success and w.names()[-3:] == ['finish', 'memory', 'upload']
        [(_, job_id, body, kwargs)] = memory_calls(w)
        assert job_id == 42 and kwargs == {}
        assert body == {'measurement_id': 1, 'card_kind': '10de:2b85:00a1', 'card_key': CARD_KEY,
                        'driver_version': '32.0.22029.1019', 'spilled': True, 'spill_cause': 'self',
                        'shared_peak_mb': 2000, 'job_peak_mb': 7300}
        for line in ('memory_report=sent', 'spilled=1', 'spill_cause=self', 'spill_shared_peak_mb=2000',
                     'spill_reference_mb=1000', 'driver_changed=0'):
            assert line in result.stdout

    def test_a_failure_reports_before_the_error(self, tmp_path, monkeypatch):
        w = world(tmp_path, monkeypatch)
        w.flow_action = rendered(w, tmp_path, throw(JobFailure('Unreal render failed: exit code 3.')))
        result = w.run()
        assert not result.success and 'exit code 3' in result.error_message
        assert w.names()[-2:] == ['finish', 'memory'] and 'memory_report=sent' in result.stdout

    def test_a_cancel_reports_before_the_canceled_result(self, tmp_path, monkeypatch):
        w = world(tmp_path, monkeypatch)
        w.flow_action = rendered(w, tmp_path, throw(Cancelled('canceled')))
        result = w.run()
        assert result.was_canceled and w.names()[-2:] == ['finish', 'memory']

    def test_a_release_reports_before_release(self, tmp_path, monkeypatch):
        w = world(tmp_path, monkeypatch)
        w.flow_action = rendered(w, tmp_path, throw(LocalConflict('dispatch_failed', 'port held')))
        result = w.run()
        assert result.released and w.names()[-3:] == ['finish', 'memory', 'release']

    def test_survivors_still_report(self, tmp_path, monkeypatch):
        w = world(tmp_path, monkeypatch)
        w.flow_action = rendered(w, tmp_path, throw(SurvivorsLeft(((5, 'x'),))))
        w.run()
        assert len(memory_calls(w)) == 1

    def test_a_shutdown_reports_once_without_retries_before_release(self, tmp_path, monkeypatch):
        w = world(tmp_path, monkeypatch)
        shutdown = threading.Event()
        shutdown.set()
        w.flow_action = rendered(w, tmp_path, throw(AbortedError('shutdown')))
        result = w.run(shutdown)
        [(_, _job_id, _body, kwargs)] = memory_calls(w)
        assert kwargs == {'attempts': 1} and result.released
        assert w.names().index('memory') < w.names().index('release')

    @pytest.mark.parametrize('answer,state', [
        (api_extras.PilotReport('terminal', 409, 'not rendering'), 'refused'),
        (api_extras.PilotReport('failed', 503, 'busy'), 'failed'),
    ])
    def test_the_completion_goes_ahead_whatever_the_answer(self, tmp_path, monkeypatch, answer, state):
        w = world(tmp_path, monkeypatch, answer=answer)
        w.flow_action = rendered(w, tmp_path, png_flow)
        result = w.run()
        assert result.success and 'upload' in w.names() and f'memory_report={state}' in result.stdout

    def test_an_unbuildable_report_does_not_stop_the_upload(self, tmp_path, monkeypatch):
        w = world(tmp_path, monkeypatch)
        w.flow_action = rendered(w, tmp_path, png_flow)
        w.card_key = 'apple-gpu'           # the held card has no PCI key: the body cannot be built
        result = w.run()
        assert memory_calls(w) == [] and 'memory_report=failed' in result.stdout and 'upload' in w.names()


class TestNotSent:

    def test_no_render_launch_no_report(self, tmp_path, monkeypatch):
        w = world(tmp_path, monkeypatch)
        w.flow_action = throw(JobFailure('Project config sets a Zen/DDC data path'))
        result = w.run()
        assert memory_calls(w) == [] and 'memory_report' not in result.stdout

    def test_a_pilot_never_reports(self, tmp_path, monkeypatch):
        w = world(tmp_path, monkeypatch, kind='pilot')
        w.flow_action = rendered(w, tmp_path, pilot_flow)
        w.run()
        assert memory_calls(w) == [] and 'pilot' in w.names()

    def test_a_shots_job_never_reports(self, tmp_path, monkeypatch):
        w = world(tmp_path, monkeypatch)
        w.params = params(kind='shots', frame=0)
        monkeypatch.setattr(job_run, 'ShotsFlow', w._flow_factory)
        w.flow_action = rendered(w, tmp_path, throw(JobFailure('stop')))
        w.run()
        assert memory_calls(w) == []
