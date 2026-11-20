# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A piece's delivery (sequence rendering FR-SW-10, FR-SS-4, D12): every kept frame to ``frames/`` from a checked
handle, deleted after a 2xx, then ``piece-result/``; 404/409 stop quietly, other 4xx fail the job, retries
exhausted leave it for reconciliation. Also the two worker calls and their production wiring.
"""
import dataclasses
import threading
from types import SimpleNamespace

import pytest

from sethlans_worker_agent import api_extras, api_sequence, config
from sethlans_worker_agent.blender_job_stop import WORKER_HARDWARE_CHANGED
from sethlans_worker_agent.unreal_executor import frame_timing, job_run, sequence_delivery
from sethlans_worker_agent.unreal_executor.delivery import Delivery, DeliveryDeps
from sethlans_worker_agent.unreal_executor.summary import Summary
from sethlans_worker_agent.unreal_executor.job_flow import FlowResult
from sethlans_worker_agent.unreal_executor.sequence_flow import PieceOutput
from sethlans_worker_agent.unreal_executor.vram import GpuMem
from tests.unit.worker.unreal._job_run_world import World
from tests.unit.worker.unreal.sequence._pieces import piece_params

CARD = GpuMem(0, 'Example GPU 32G', 32607, 1000, None, None, 'pci:0000:01:00.0')
TIMING = frame_timing.PieceTiming(shots=({'index': 1, 'frames': 3, 'seconds': 6.0, 's_per_frame': 2.0},),
                                  startup_s=40.0, lead_in_s=50.0)


class PieceWorld(World):

    def __init__(self, tmp_path, monkeypatch, card=CARD):
        super().__init__(tmp_path, monkeypatch)
        self.params = piece_params(start=100, end=102, lead=2)
        self.card = card
        monkeypatch.setattr(job_run, 'RangeFlow', self._flow_factory)
        self.flow_action = self._frames

    def _frames(self, job):
        paths = []
        for frame in (100, 101, 102):
            path = job.output / f'sethlans_42.{frame:04d}.png'
            path.write_bytes(b'png%d' % frame)
            paths.append(path)
        self.paths = paths
        return FlowResult(prep=None, render_s=90.5, prep_s=20.0, warmup_s=0.0, warmup_ran=False,
                          render_time_seconds=85, outputs=paths,
                          piece=PieceOutput(frames=(100, 101, 102), timing=TIMING, vram_peak_mb=7700, card=self.card))


def _calls(world, name):
    return [call for call in world.calls if call[0] == name]


def test_every_frame_goes_up_then_the_piece_result(tmp_path, monkeypatch):
    world = PieceWorld(tmp_path, monkeypatch)
    result = world.run()
    assert result.success and result.released and not result.error_message
    assert [(c[1], c[2], c[3]) for c in _calls(world, 'frame')] == [
        (100, 'sethlans_42.0100.png', b'png100'), (101, 'sethlans_42.0101.png', b'png101'),
        (102, 'sethlans_42.0102.png', b'png102')]
    assert not any(path.exists() for path in world.paths)  # each deleted after its 2xx
    (_name, job_id, body), = _calls(world, 'piece')
    assert job_id == 42 and body['shots'] == [dict(TIMING.shots[0])]
    assert (body['render_s'], body['startup_s'], body['lead_in_s'], body['vram_peak_mb']) == (90.5, 40.0, 50.0, 7700)
    assert body['gpu'] == {'card_key': CARD.card_key, 'name': CARD.name, 'total_mb': CARD.total_mb}
    assert body['summary'].startswith('[sethlans-unreal]') and body['warnings'] == []
    assert world.names().index('finish') < world.names().index('frame')  # after the integrity check
    assert 'upload' not in world.names() and 'release' not in world.names()


def test_no_card_key_sends_gpu_null(tmp_path, monkeypatch):
    world = PieceWorld(tmp_path, monkeypatch, card=None)
    assert world.run().success
    assert _calls(world, 'piece')[0][2]['gpu'] is None


def test_a_frame_the_manager_no_longer_wants_stops_quietly(tmp_path, monkeypatch):
    world = PieceWorld(tmp_path, monkeypatch)
    world.frame_reports = [api_extras.PilotReport('done', 200), api_extras.PilotReport('terminal', 409)]
    result = world.run()
    assert result.success and result.released and len(_calls(world, 'frame')) == 2 and not _calls(world, 'piece')
    assert not world.paths[0].exists() and world.paths[1].exists()  # kept: never accepted


def test_retries_exhausted_leave_the_job_for_reconciliation(tmp_path, monkeypatch):
    world = PieceWorld(tmp_path, monkeypatch)
    world.frame_reports = [api_extras.PilotReport('failed', 503)]
    result = world.run()
    assert result.success and result.released and len(_calls(world, 'frame')) == 1 and not _calls(world, 'piece')


def test_a_rejected_frame_fails_the_job(tmp_path, monkeypatch):
    world = PieceWorld(tmp_path, monkeypatch)
    world.frame_reports = [api_extras.PilotReport('rejected', 400, "The file is not a valid PNG image.")]
    result = world.run()
    assert not result.success and not result.released and not _calls(world, 'piece')
    assert result.error_message == "Frame 100 rejected by the manager: The file is not a valid PNG image."


def test_a_rejected_piece_result_fails_the_job(tmp_path, monkeypatch):
    world = PieceWorld(tmp_path, monkeypatch)
    world.piece_report = api_extras.PilotReport('rejected', 400, "{'shots': ['bad']}")
    result = world.run()
    assert not result.success and result.error_message == "Piece result rejected by the manager: {'shots': ['bad']}"


def test_an_integrity_failure_uploads_nothing(tmp_path, monkeypatch):
    world = PieceWorld(tmp_path, monkeypatch)
    world.integrity_ok = False
    result = world.run()
    assert not result.success and not _calls(world, 'frame') and not _calls(world, 'piece')


def test_a_409_from_piece_result_stops_quietly(tmp_path, monkeypatch):
    world = PieceWorld(tmp_path, monkeypatch)
    world.piece_report = api_extras.PilotReport('terminal', 409, 'Job is not rendering on this worker.')
    result = world.run()
    assert result.success and result.released and not result.error_message
    assert len(_calls(world, 'frame')) == 3 and len(_calls(world, 'piece')) == 1 and 'release' not in world.names()


def test_a_shutdown_during_the_uploads_releases_the_job_at_once(tmp_path, monkeypatch):
    """Review S8: a long piece never holds the job thread past the shutdown budget."""
    world = PieceWorld(tmp_path, monkeypatch)
    shutdown = threading.Event()
    upload = world.upload_frame
    world.upload_frame = lambda *args: (shutdown.set(), upload(*args))[1]
    result = world.run(shutdown=shutdown)
    assert result.released and len(_calls(world, 'frame')) == 1 and not _calls(world, 'piece')
    assert ('release', 'shutdown') in world.calls


@pytest.mark.parametrize('cause,canceled,released', [('canceled', True, False), ('reassigned', False, True)])
def test_a_cancel_or_reassignment_during_the_uploads_stops_them(tmp_path, cause, canceled, released):
    calls, delivery = _delivery(tmp_path, lambda: cause)
    result = sequence_delivery.deliver_piece(delivery, _flow_of(tmp_path))
    assert (result.was_canceled, result.released) == (canceled, released) and calls == []


def test_a_lost_card_does_not_stop_the_uploads(tmp_path):
    calls, delivery = _delivery(tmp_path, lambda: WORKER_HARDWARE_CHANGED)
    result = sequence_delivery.deliver_piece(delivery, _flow_of(tmp_path))
    assert result.success and [c[0] for c in calls] == ['frame', 'frame', 'piece']


def test_an_unsafe_frame_names_no_path(tmp_path, monkeypatch):
    """Review S6: the error the manager stores holds the class name, never the worker's cache path."""
    calls, delivery = _delivery(tmp_path, lambda: '')
    monkeypatch.setattr(sequence_delivery.fs_safe, 'open_output_file',
                        lambda path, root: (_ for _ in ()).throw(PermissionError(f'denied: {path}')))
    result = sequence_delivery.deliver_piece(delivery, _flow_of(tmp_path))
    assert result.error_message == "Frame 100 failed the output safety checks (PermissionError); not uploaded."
    assert str(tmp_path) not in result.error_message and calls == []


def _flow_of(tmp_path):
    paths = []
    for frame in (100, 101):
        paths.append(tmp_path / f'sethlans_42.{frame:04d}.png')
        paths[-1].write_bytes(b'x')
    return FlowResult(prep=None, render_s=1.0, prep_s=1.0, warmup_s=0.0, warmup_ran=False, render_time_seconds=1,
                      outputs=paths, piece=PieceOutput(frames=(100, 101), timing=TIMING, vram_peak_mb=None))


def _delivery(tmp_path, cause):
    calls = []
    done = api_extras.PilotReport('done', 200)
    deps = DeliveryDeps(release_job=lambda job_id, reason: calls.append(('release', reason)),
                        mark_unreachable=lambda reason: None, upload=None, report_pilot=None, report_shots=None,
                        upload_frame=lambda *args: (calls.append(('frame',)), done)[1],
                        report_piece=lambda job_id, body: (calls.append(('piece',)), done)[1])
    return calls, Delivery(piece_params(start=100, end=101, lead=0), deps, Summary(), None, cause=cause)


# -- the worker calls ----------------------------------------------------------------------------------------------

def _session(monkeypatch, *codes):
    calls = []
    monkeypatch.setattr(config, 'MANAGER_API_URL', 'https://m/api/')
    monkeypatch.setattr(api_extras, 'get_auth_headers', lambda: {'Authorization': 'Token t'})
    monkeypatch.setattr(api_extras, 'handle_auth_response', lambda response: False)
    responses = [SimpleNamespace(status_code=code, json=lambda: {'error': 'no'}, text='') for code in codes]
    session = SimpleNamespace(request=lambda method, url, **kw: (calls.append((method, url, kw)), responses.pop(0))[1])
    monkeypatch.setattr(api_extras, 'get_session', lambda: session)
    return calls


class Handle:
    def __init__(self, data):
        self.data, self.seeks = data, 0

    def seek(self, offset):
        self.seeks += 1

    def read(self, size=-1):
        return self.data[:size] if size >= 0 else self.data


@pytest.mark.parametrize('codes,outcome,attempts', [
    ((200,), 'done', 1), ((503, 200), 'done', 2), ((409,), 'terminal', 1), ((404,), 'terminal', 1),
    ((400,), 'rejected', 1), ((413,), 'rejected', 1), ((500, 500, 500), 'failed', 3), ((403,), 'failed', 1),
])
def test_upload_frame(monkeypatch, codes, outcome, attempts):
    calls = _session(monkeypatch, *codes)
    report = api_sequence.upload_frame(7, 100, Handle(b'\x89PNG...'), 'sethlans_7.0100.png', sleep=lambda s: None)
    assert report.outcome == outcome and len(calls) == attempts
    method, url, kw = calls[0]
    assert (method, url, kw['data']) == ('post', 'https://m/api/jobs/7/frames/', {'frame': '100'})
    assert kw['files'] == {'file': ('sethlans_7.0100.png', b'\x89PNG...', 'image/png')}
    assert all(c[2]['files'] == kw['files'] for c in calls)  # every attempt sends the same checked bytes


def test_upload_frame_refuses_a_frame_over_the_cap_without_sending_it(monkeypatch):
    calls = _session(monkeypatch)
    monkeypatch.setattr(api_sequence, 'MAX_FRAME_BYTES', 4)
    report = api_sequence.upload_frame(7, 1, Handle(b'12345'), 'sethlans_7.0001.exr', sleep=lambda s: None)
    assert (report.outcome, report.status_code, calls) == ('rejected', 413, [])


def test_report_piece_result_posts_to_its_endpoint(monkeypatch):
    calls = _session(monkeypatch, 502, 200)
    report = api_sequence.report_piece_result(7, {'shots': []}, sleep=lambda s: None)
    assert report.outcome == 'done' and [c[1] for c in calls] == ['https://m/api/jobs/7/piece-result/'] * 2


def test_production_wiring_and_the_piece_calls_are_required():
    from sethlans_worker_agent.unreal_executor import executor
    deps = executor.default_run_deps().delivery
    assert deps.upload_frame is api_sequence.upload_frame and deps.report_piece is api_sequence.report_piece_result
    for name in ('upload_frame', 'report_piece'):
        field, = [f for f in dataclasses.fields(DeliveryDeps) if f.name == name]
        assert field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING
