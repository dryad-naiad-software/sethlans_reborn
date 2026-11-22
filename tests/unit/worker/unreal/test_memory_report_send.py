# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Sending the memory report (Unreal per-machine memory test FR-MT-S-4) and its HTTP call (``api_extras``).

200 -> ``memory_report=sent``; 404/409 -> ``refused`` (WARNING, INFO on a cancel or reassignment); a failure
after the retries, a refused body or no call -> ERROR and ``failed``; a report that cannot be built -> ERROR
and ``failed``; nothing raises out of ``send``. A shutdown sends once without retries (``attempts=1``).
"""
import logging

import pytest
import requests

from sethlans_worker_agent import api_extras, config
from sethlans_worker_agent.unreal_executor import memory_report
from tests.unit.worker.unreal._executor_fakes import params
from tests.unit.worker.unreal._memory_report_fakes import Reports, job_run, render_run

NAME = memory_report.__name__


def _send(jr, caplog, level=logging.INFO):
    with caplog.at_level(level, logger=NAME):
        memory_report.send(jr)
    return [(r.levelname, r.getMessage()) for r in caplog.records if r.name == NAME]


class TestSend:

    def test_a_200_is_sent(self, tmp_path, caplog):
        reports = Reports()
        jr = job_run([render_run(tmp_path, max_job=7000)], report=reports)
        lines = _send(jr, caplog)
        [(job_id, body, kwargs)] = reports.calls
        assert job_id == 42 and body == memory_report.body(job_run([render_run(tmp_path, max_job=7000)]))
        assert kwargs == {}                                    # the standard report retries
        assert jr.summary.get('memory_report') == 'sent' and lines[-1][0] == 'INFO'

    @pytest.mark.parametrize('code', [404, 409])
    def test_404_and_409_are_refused_and_warned(self, tmp_path, caplog, code):
        jr = job_run([render_run(tmp_path)], report=Reports(api_extras.PilotReport('terminal', code, 'gone')))
        lines = _send(jr, caplog)
        assert jr.summary.get('memory_report') == 'refused'
        assert lines == [('WARNING', f"memory-report/ for job 42 answered {code} (gone); not sent again.")]

    @pytest.mark.parametrize('cause', ['canceled', 'reassigned'])
    def test_a_refusal_after_a_cancel_is_only_info(self, tmp_path, caplog, cause):
        jr = job_run([render_run(tmp_path)], cause=cause,
                     report=Reports(api_extras.PilotReport('terminal', 409, 'not rendering')))
        lines = _send(jr, caplog)
        assert jr.summary.get('memory_report') == 'refused' and [level for level, _ in lines] == ['INFO']

    @pytest.mark.parametrize('answer,words', [
        (api_extras.PilotReport('failed', 503, 'busy'), 'failed after retries (busy)'),
        (api_extras.PilotReport('failed', None, 'ConnectionError: down'), 'failed after retries (ConnectionError'),
        (api_extras.PilotReport('rejected', 400, 'bad key'), 'was refused (bad key)'),
    ])
    def test_a_final_failure_is_an_error_and_failed(self, tmp_path, caplog, answer, words):
        jr = job_run([render_run(tmp_path)], report=Reports(answer))
        lines = _send(jr, caplog)
        assert jr.summary.get('memory_report') == 'failed'
        [(level, line)] = lines
        assert level == 'ERROR' and words in line and "the job's completion goes ahead without it" in line

    def test_no_wired_call_is_an_error_and_failed(self, tmp_path, caplog):
        jr = job_run([render_run(tmp_path)])
        jr.deps.delivery.report_memory = None
        lines = _send(jr, caplog)
        assert jr.summary.get('memory_report') == 'failed' and lines[0][0] == 'ERROR'

    def test_a_shutdown_sends_once_without_retries(self, tmp_path, caplog):
        reports = Reports()
        jr = job_run([render_run(tmp_path)], cause='shutdown', report=reports)
        _send(jr, caplog)
        [(_job_id, _body, kwargs)] = reports.calls
        assert kwargs == {'attempts': 1} and jr.summary.get('memory_report') == 'sent'

    def test_a_body_that_cannot_be_built_is_not_sent(self, tmp_path, caplog):
        reports = Reports()
        jr = job_run([render_run(tmp_path)], gate_result=None, report=reports)
        lines = _send(jr, caplog)
        assert reports.calls == [] and jr.summary.get('memory_report') == 'failed'
        assert lines == [('ERROR', "Unreal job 42: the memory report cannot be built (the gate chose no memory "
                                   "result); it is not sent.")]

    def test_send_never_raises(self, tmp_path, caplog):
        jr = job_run([render_run(tmp_path)], report=Reports(RuntimeError('bug')))
        lines = _send(jr, caplog)
        assert jr.summary.get('memory_report') == 'failed'
        assert lines[-1] == ('ERROR', 'Unreal job 42: the memory report failed unexpectedly.')

    @pytest.mark.parametrize('kind,runs', [('pilot', True), ('render', False)])
    def test_nothing_when_not_due(self, tmp_path, caplog, kind, runs):
        reports = Reports()
        jr = job_run([render_run(tmp_path)] if runs else [], p=params(kind=kind), report=reports)
        assert _send(jr, caplog) == [] and reports.calls == []
        assert 'memory_report' not in jr.summary.text()

    def test_no_runner_yet_is_not_due(self, tmp_path, caplog):
        jr = job_run([render_run(tmp_path)])
        jr.runner = None
        memory_report.send(jr)
        assert 'memory_report' not in jr.summary.text()


class _Response:
    def __init__(self, status, data=None):
        self.status_code, self._data, self.text = status, data, ''

    def json(self):
        if self._data is None:
            raise ValueError('no json')
        return self._data


class _Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr(config, 'MANAGER_API_URL', 'https://m/api/')
    monkeypatch.setattr(api_extras, 'get_auth_headers', lambda: {'Authorization': 'Token t'})
    monkeypatch.setattr(api_extras, 'handle_auth_response', lambda response: False)

    def install(*responses):
        s = _Session(*responses)
        monkeypatch.setattr(api_extras, 'get_session', lambda: s)
        return s
    return install


class TestReportMemory:
    BODY = {'measurement_id': 7, 'spilled': None}

    def test_posts_the_body_to_the_jobs_memory_report(self, session):
        s = session(_Response(200, {'result': 'fits'}))
        report = api_extras.report_memory(7, self.BODY, sleep=lambda s: None)
        [(method, url, kwargs)] = s.calls
        assert (method, url, kwargs['json'], kwargs['timeout']) == \
            ('post', 'https://m/api/jobs/7/memory-report/', self.BODY, api_extras.PILOT_TIMEOUT_SECONDS)
        assert kwargs['headers'] == {'Authorization': 'Token t'} and report == api_extras.PilotReport('done', 200)

    def test_the_standard_retries_with_backoff(self, session):
        s = session(_Response(500), requests.exceptions.ConnectionError('x'), _Response(503))
        sleeps = []
        report = api_extras.report_memory(7, self.BODY, sleep=sleeps.append)
        assert report.outcome == 'failed' and len(s.calls) == 3 and sleeps == list(api_extras.PILOT_BACKOFF_SECONDS)

    @pytest.mark.parametrize('attempts', [1, 0, -3])
    def test_one_attempt_sends_once_without_a_sleep(self, session, attempts):
        s = session(_Response(503))
        sleeps = []
        report = api_extras.report_memory(7, self.BODY, attempts=attempts, sleep=sleeps.append)
        assert report.outcome == 'failed' and len(s.calls) == 1 and sleeps == []

    @pytest.mark.parametrize('code,outcome', [(404, 'terminal'), (409, 'terminal'), (400, 'rejected')])
    def test_4xx_answers_are_not_retried(self, session, code, outcome):
        s = session(_Response(code, {'error': 'no'}))
        report = api_extras.report_memory(7, self.BODY, sleep=lambda s: None)
        assert (report.outcome, report.status_code, report.detail, len(s.calls)) == (outcome, code, 'no', 1)

    def test_a_retry_that_succeeds_is_done(self, session):
        s = session(_Response(502), _Response(200, {}))
        assert api_extras.report_memory(7, self.BODY, sleep=lambda s: None).outcome == 'done' and len(s.calls) == 2
