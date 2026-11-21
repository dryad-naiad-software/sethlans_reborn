# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
An audio job's delivery (sequence rendering FR-SA-6): the ``.wav`` checked again from the handle it is sent from,
streamed to ``sound-result/`` with the ``offset`` object, then deleted; and the worker call's multipart body,
retries and size cap.
"""
import email.parser
import json
from types import SimpleNamespace

import pytest

from sethlans_worker_agent import api_extras, api_sequence
from sethlans_worker_agent.blender_job_stop import WORKER_HARDWARE_CHANGED
from sethlans_worker_agent.unreal_executor import audio_flow, wav_check
from sethlans_worker_agent.unreal_executor.delivery import Delivery, DeliveryDeps, Outcome
from sethlans_worker_agent.unreal_executor.job_flow import FlowResult
from sethlans_worker_agent.unreal_executor.summary import Summary
from shared.unreal import sound_result
from tests.unit.worker.unreal.sequence._pieces import audio_params
from tests.unit.worker.unreal.sequence._signals import SPF, wav_bytes
from tests.unit.worker.unreal.sequence.test_piece_delivery import _session

FRAMES = 188
OFFSET = {'status': 'measured', 'offset_samples': 9418, 'reason': '', 'shots_tried': 2, 'shots_confident': 2,
          'shots_agreeing': 2, 'min_peak': 0.97, 'spread_samples': 0, 'unsupported_sections': 0, 'export_s': 4.0,
          'correlate_s': 0.4, 'shots': [{'shot': 3, 'lag_samples': 9418, 'envelope_peak': 0.95,
                                         'envelope_margin': 0.3, 'peak': 0.97, 'ambiguous': False}]}


def _flow(tmp_path, samples=FRAMES * SPF):
    out = tmp_path / 'output'
    out.mkdir(exist_ok=True)
    path = out / 'sethlans_42.MasterSubmixDefault.wav'
    path.write_bytes(wav_bytes([0.1] * samples))
    info = wav_check.WavInfo(rate=48_000, channels=2, samples=samples, expected=FRAMES * SPF)
    sound = audio_flow.SoundOutput(path=path, frames=FRAMES, rate_fraction=(30, 1), info=info, offset=OFFSET,
                                   latency_skipped=False)
    return FlowResult(prep=None, render_s=61.25, prep_s=5.0, warmup_s=0.0, warmup_ran=False, render_time_seconds=60,
                      outputs=[path], sound=sound)


def _deliver(tmp_path, report=None, cause='', flow=None):
    cause_text = cause
    calls = []

    def upload(job_id, handle, offset, skipped, render_s, summary, cause):
        assert cause() == ('' if cause_text == WORKER_HARDWARE_CHANGED else cause_text)
        calls.append((job_id, handle.read(), offset, skipped, render_s, summary))
        return report or api_extras.PilotReport('done', 200)
    deps = DeliveryDeps(release_job=lambda job_id, reason: calls.append(('release', reason)), mark_unreachable=None,
                        upload=None, report_pilot=None, report_shots=None, upload_frame=None, report_piece=None,
                        upload_sound=upload)
    flow = flow or _flow(tmp_path)
    delivery = Delivery(audio_params(), deps, Summary(), None, cause=lambda: cause)
    return delivery.deliver(Outcome('success', flow=flow), True), calls, flow


def test_the_checked_wav_and_its_offset_go_up_then_the_file_is_deleted(tmp_path):
    result, calls, flow = _deliver(tmp_path)
    assert result.success and result.released and not result.error_message
    (job_id, data, offset, skipped, render_s, summary), = calls
    assert job_id == 42 and data[:4] == b'RIFF' and offset is OFFSET and skipped is False and render_s == 61.25
    assert summary.startswith('[sethlans-unreal]') and not flow.sound.path.exists()


@pytest.mark.parametrize('report,success,error', [
    (api_extras.PilotReport('terminal', 409, 'gone'), True, ''),
    (api_extras.PilotReport('failed', 503), True, ''),
    (api_extras.PilotReport('rejected', 400, 'offset: bad'), False, 'Sound result rejected by the manager: offset: bad'),
])
def test_manager_answers(tmp_path, report, success, error):
    result, _calls, flow = _deliver(tmp_path, report=report)
    assert result.success is success and result.error_message == error and result.released is success
    assert flow.sound.path.exists()  # kept unless the manager took it


def test_a_wav_changed_after_its_check_is_not_sent(tmp_path):
    flow = _flow(tmp_path)
    flow.sound.path.write_bytes(wav_bytes([0.1] * (FRAMES * SPF - 1000)))
    result, calls, _flow_ = _deliver(tmp_path, flow=flow)
    assert not result.success and 'the run needs' in result.error_message and calls == []


def test_a_cancel_meanwhile_sends_nothing(tmp_path):
    result, calls, _flow_ = _deliver(tmp_path, cause='canceled')
    assert result.was_canceled and calls == []


def test_a_lost_card_does_not_stop_the_upload(tmp_path):
    """The .wav is on disk: a worker_hardware_changed cause is ignored, as for a piece's frames."""
    result, calls, flow = _deliver(tmp_path, cause=WORKER_HARDWARE_CHANGED)
    assert result.success and len(calls) == 1 and not flow.sound.path.exists()


# -- the worker call ---------------------------------------------------------------------------------------------

def _parts(kw):
    head = f"Content-Type: {kw['headers']['Content-Type']}\r\n\r\n".encode('ascii')
    body = kw['data']
    data = b''.join(iter(lambda: body.read(7000), b''))
    assert len(data) == len(body)
    message = email.parser.BytesParser().parsebytes(head + data)
    return {part.get_param('name', header='content-disposition'): part.get_payload(decode=True)
            for part in message.get_payload()}


@pytest.mark.parametrize('codes,outcome,attempts', [
    ((200,), 'done', 1), ((503, 200), 'done', 2), ((409,), 'terminal', 1), ((400,), 'rejected', 1),
    ((500, 500, 500), 'failed', 3),
])
def test_upload_sound_streams_the_file_with_its_fields(tmp_path, monkeypatch, codes, outcome, attempts):
    sent = []
    calls = _session(monkeypatch, *codes)
    path = tmp_path / 'run.wav'
    path.write_bytes(wav_bytes([0.2] * 5000))
    with open(path, 'rb') as handle:
        session = api_extras.get_session()
        original = session.request
        session.request = lambda method, url, **kw: (sent.append(_parts(kw)), original(method, url, **kw))[1]
        report = api_sequence.upload_sound(7, handle, OFFSET, False, 61.25, 'summary', sleep=lambda s: None)
    assert report.outcome == outcome and len(calls) == attempts and len(sent) == attempts
    method, url, kw = calls[0]
    assert (method, url) == ('post', 'https://m/api/jobs/7/sound-result/')
    assert kw['headers']['Authorization'] == 'Token t' and kw['headers']['Content-Type'].startswith('multipart/')
    for parts in sent:  # every attempt sends the whole file again
        assert parts['file'] == path.read_bytes() and parts['render_s'] == b'61.250'
        assert parts['latency_compensation_skipped'] == b'false' and parts['summary'] == b'summary'
        assert sound_result.parse_offset_text(parts['offset'].decode('utf-8'), 48_000) == OFFSET
        assert set(parts) == set(sound_result.FORM_FIELDS)


def test_upload_sound_refuses_a_file_over_4_gib_without_sending_it(tmp_path, monkeypatch):
    calls = _session(monkeypatch)
    monkeypatch.setattr(api_sequence.os, 'fstat', lambda fd: SimpleNamespace(st_size=4 * 1024 ** 3 + 1))
    with open(tmp_path / 'x.wav', 'wb+') as handle:
        report = api_sequence.upload_sound(7, handle, OFFSET, False, 1.0, '', sleep=lambda s: None)
    assert (report.outcome, report.status_code, calls) == ('rejected', 413, [])


def test_a_file_that_shrinks_while_it_is_sent_is_rejected(tmp_path):
    path = tmp_path / 'run.wav'
    path.write_bytes(b'x' * 100)
    with open(path, 'rb') as handle:
        body = api_sequence._Multipart([('offset', json.dumps(OFFSET))], handle, 200)
        with pytest.raises(OSError, match='shrank'):
            while body.read(64):
                pass


def test_production_wiring():
    from sethlans_worker_agent.unreal_executor import executor
    assert executor.default_run_deps().delivery.upload_sound is api_sequence.upload_sound
