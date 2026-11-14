# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``api_extras`` (FR-UE-3, FR-UE-16, FR-UE-17, FR-UE-25 "Report"), the summary
(FR-UE-14), the lifecycle's post-report hook (chunk 7 req. 3, 4),
registration on a 503 (req. 7) and the PyInstaller data (FR-UE-20).
"""
import io
from pathlib import Path

import pytest
import requests

from sethlans_worker_agent import api_extras, api_handler, config, job_lifecycle, system_monitor
from sethlans_worker_agent.api_conflict import JobNoLongerAssigned
from sethlans_worker_agent.unreal_executor import summary as summary_mod
from tests.unit.worker.executors._fakes import make_result

REPO = Path(__file__).resolve().parents[4]
PNG_MAGIC = bytes([0x89]) + b'PNG\r\n\x1a\n'


class Response:
    def __init__(self, status, data=None, text=''):
        self.status_code, self._data, self.text = status, data, text

    def json(self):
        if self._data is None:
            raise ValueError('no json')
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f'{self.status_code}')


class Session:
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
    holder = {}

    def install(*responses):
        holder['s'] = Session(*responses)
        monkeypatch.setattr(api_extras, 'get_session', lambda: holder['s'])
        monkeypatch.setattr(api_handler, 'get_session', lambda: holder['s'])
        return holder['s']
    return install


class TestApiExtras:

    def test_release_is_one_attempt_with_a_5_s_timeout(self, session):
        s = session(Response(503))
        assert api_extras.release_job(7, 'shutdown') is False
        (method, url, kwargs), = s.calls
        expected = ('post', 'https://m/api/jobs/7/release/', {'reason': 'shutdown'}, 5)
        assert (method, url, kwargs['json'], kwargs['timeout']) == expected
        session(requests.exceptions.ConnectionError('down'))
        assert api_extras.release_job(7, 'local_conflict') is False
        session(Response(200))
        assert api_extras.release_job(7, 'share_unavailable') is True

    def test_get_assignment(self, session):
        session(Response(200, {'status': 'RENDERING', 'assigned_worker': 3}))
        assert api_extras.get_job_assignment(1) == ('RENDERING', 3)
        session(Response(200, {'status': 'QUEUED', 'assigned_worker': None}))
        assert api_extras.get_job_assignment(1) == ('QUEUED', None)
        session(Response(500))
        assert api_extras.get_job_assignment(1) is None
        session(requests.exceptions.Timeout())
        assert api_extras.get_job_assignment(1) is None

    @pytest.mark.parametrize('responses,outcome,attempts', [
        ([Response(200, {'status': 'DONE'})], 'done', 1),
        ([Response(409, {'error': 'Job is not rendering on this worker.'})], 'terminal', 1),
        ([Response(404, {'error': 'Job not found.'})], 'terminal', 1),
        ([Response(400, {'gpu': ['Does not match.']})], 'rejected', 1),
        ([Response(500), Response(502), Response(200, {})], 'done', 3),
        ([Response(500), requests.exceptions.ConnectionError('x'), Response(503)], 'failed', 3),
    ])
    def test_pilot_report_responses(self, session, responses, outcome, attempts):
        s = session(*responses)
        sleeps = []
        report = api_extras.report_pilot_result(5, {'peak_mb': 1}, sleep=sleeps.append)
        assert report.outcome == outcome and len(s.calls) == attempts and len(sleeps) == attempts - 1
        assert s.calls[0][1] == 'https://m/api/jobs/5/pilot-result/'
        if outcome == 'rejected':
            assert report.detail == "{'gpu': ['Does not match.']}"

    def test_upload_from_handle(self, session):
        s = session(Response(201))
        handle = io.BytesIO(b'png-bytes')
        handle.read()
        assert api_extras.upload_output_handle(3, handle, 'out.png') is True
        sent = s.calls[0][2]['files']['output_file']
        assert sent[0] == 'out.png' and sent[1] == b'png-bytes'
        session(Response(409, {'error': 'Job is not rendering on this worker.'}))
        with pytest.raises(JobNoLongerAssigned):
            api_extras.upload_output_handle(3, io.BytesIO(b'x'), 'out.png')

    def test_upload_retry_sends_the_checked_bytes_again(self, session, monkeypatch):
        """Review M1/W2: a 5xx retry must not send an empty body (a file object is consumed once)."""
        monkeypatch.setattr(api_handler.time, 'sleep', lambda s: None)
        s = session(Response(500), Response(201))
        png = PNG_MAGIC + b'-bytes'
        assert api_extras.upload_output_handle(3, io.BytesIO(png), 'out.png') is True
        bodies = [call[2]['files']['output_file'][1] for call in s.calls]
        assert bodies == [png, png]

    def test_upload_read_error_is_a_failed_upload(self, session):
        class Broken(io.BytesIO):
            def read(self, *args):
                raise OSError('device gone')
        s = session()
        assert api_extras.upload_output_handle(3, Broken(b'x'), 'out.png') is False and s.calls == []

    @pytest.mark.parametrize('status', [401, 403])
    def test_pilot_auth_failure_is_failed_not_rejected(self, session, monkeypatch, status):
        handled = []
        monkeypatch.setattr(api_extras, 'handle_auth_response', lambda response: handled.append(response.status_code))
        s = session(Response(status, {'detail': 'Invalid token.'}))
        report = api_extras.report_pilot_result(5, {}, sleep=lambda s: None)
        assert report.outcome == 'failed' and report.status_code == status and handled == [status]
        assert len(s.calls) == 1


class TestSummary:

    def test_order_values_and_cap(self):
        s = summary_mod.Summary()
        s.set('engine', '5.8.4-1')
        s.set('farm_defaults_version', 2)
        s.append('quit_forced', 'prep')
        s.append('quit_forced', 'render')
        s.append('quit_forced', 'prep')
        s.set('bad', 'a\nb')
        s.warn('cvar_collision=r.X')
        lines = s.text().splitlines()
        assert lines[0] == '[sethlans-unreal] farm_defaults_version=2' and lines[1].endswith('log_rules_version=3')
        assert '[sethlans-unreal] quit_forced=prep,render' in lines and '[sethlans-unreal] bad=a b' in lines
        assert lines[-1] == '[sethlans-unreal] warning=cvar_collision=r.X'
        for index in range(2000):
            s.set(f'k{index}', 'x' * 100)
        assert len(s.text().encode()) <= summary_mod.MAX_BYTES


class TestLifecycleHook:

    def test_after_report_runs_after_the_report_and_never_raises(self, monkeypatch):
        order = []
        monkeypatch.setattr(job_lifecycle, '_report_result', lambda job, result, jp: order.append('report'))
        monkeypatch.setattr(job_lifecycle, '_execute_and_release', lambda job, cap, ctx: make_result(
            after_report=lambda: (order.append('hook'), 1 / 0)))
        job_lifecycle._process_claimed_job_inner({'id': 5})
        assert order == ['report', 'hook']

    def test_released_results_also_run_the_hook(self, monkeypatch):
        order = []
        monkeypatch.setattr(job_lifecycle, '_execute_and_release', lambda job, cap, ctx: make_result(
            released=True, after_report=lambda: order.append('hook')))
        monkeypatch.setattr(job_lifecycle.api_handler, 'update_job_status', lambda *a: order.append('patch'))
        job_lifecycle._process_claimed_job_inner({'id': 5})
        assert order == ['hook']


def test_registration_treats_a_503_as_failure(monkeypatch):
    """Chunk 7 req. 7: FR-RT-12's boot-reconciliation rollback answers 503; that is not 'registered'."""
    monkeypatch.setattr(config, 'API_TOKEN', 'tok')
    monkeypatch.setattr(config, 'MANAGER_API_URL', 'https://m/api/')
    monkeypatch.setattr(system_monitor, 'get_system_info', lambda: {})
    monkeypatch.setattr(system_monitor, '_add_unreal_payload', lambda payload: None)
    monkeypatch.setattr(api_handler.time, 'sleep', lambda s: None)
    s = Session(Response(503), Response(503), Response(503))
    monkeypatch.setattr(api_handler, 'get_session', lambda: s)
    monkeypatch.setattr(system_monitor, 'WORKER_ID', None)
    assert system_monitor.register_with_manager() is None
    assert system_monitor.WORKER_ID is None and len(s.calls) == 3


def test_worker_spec_bundles_the_ue_scripts_as_data():
    spec = (REPO / 'packaging' / 'pyinstaller' / 'worker.spec').read_text(encoding='utf-8')
    assert "'sethlans_worker_agent' / 'unreal_executor' / 'ue_scripts'" in spec
    assert "'sethlans_worker_agent/unreal_executor/ue_scripts'" in spec
    assert "'sethlans_worker_agent.unreal_executor.executor'" in spec
    scripts = REPO / 'worker' / 'sethlans_worker_agent' / 'unreal_executor' / 'ue_scripts'
    assert {p.name for p in scripts.glob('*.py')} == {'sethlans_prepare_queue.py', 'sethlans_warm_up.py'}
    assert not (scripts / '__init__.py').exists()  # data, never collected as modules (they import unreal)
