# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Chunk 5 review items (sequence rendering FR-SA-5, FR-SA-6, FR-SA-10): the upload stops on a shutdown or cancel and
never resends after a read timeout; a refined peak on the window's edge is not confident; the log scan bounds a line
without a newline; a skipped latency compensation expects 7 more frames.
"""
from types import SimpleNamespace

import pytest
import requests

from sethlans_worker_agent import api_extras, api_sequence
from sethlans_worker_agent.unreal_executor import sound_offset, wav_check
from shared.unreal import sound_result
from tests.unit.worker.unreal.sequence._signals import wav_bytes
from tests.unit.worker.unreal.sequence.test_audio_delivery import OFFSET, _deliver
from tests.unit.worker.unreal.sequence.test_piece_delivery import _session


def _wav(tmp_path, samples=20_000):
    path = tmp_path / 'run.wav'
    path.write_bytes(wav_bytes([0.2] * samples))
    return path


class TestUploadStops:

    def test_a_cause_before_the_first_attempt_sends_nothing(self, tmp_path, monkeypatch):
        calls = _session(monkeypatch, 200)
        with open(_wav(tmp_path), 'rb') as handle:
            report = api_sequence.upload_sound(7, handle, OFFSET, False, 1.0, '', sleep=lambda s: None,
                                               cause=lambda: 'shutdown')
        assert report.outcome == 'stopped' and calls == []

    def test_a_cause_during_the_send_stops_it(self, tmp_path, monkeypatch):
        _session(monkeypatch)
        reads, causes = [], iter([''] * 3 + ['canceled'] * 100)

        def request(method, url, data=None, **kw):
            while data.read(4096):
                reads.append(1)
            raise AssertionError('the body was sent whole')
        monkeypatch.setattr(api_extras, 'get_session', lambda: SimpleNamespace(request=request))
        with open(_wav(tmp_path), 'rb') as handle:
            report = api_sequence.upload_sound(7, handle, OFFSET, False, 1.0, '', sleep=lambda s: None,
                                               cause=lambda: next(causes))
        assert report.outcome == 'stopped' and 0 < len(reads) < 5

    def test_a_read_timeout_is_never_sent_again(self, tmp_path, monkeypatch):
        sent = []

        def request(method, url, **kw):
            sent.append(url)
            raise requests.exceptions.ReadTimeout('no answer')
        _session(monkeypatch)
        monkeypatch.setattr(api_extras, 'get_session', lambda: SimpleNamespace(request=request))
        with open(_wav(tmp_path), 'rb') as handle:
            report = api_sequence.upload_sound(7, handle, OFFSET, False, 1.0, '', sleep=lambda s: None)
        assert report.outcome == 'failed' and len(sent) == 1 and 'not sent again' in report.detail

    def test_a_stopped_upload_is_delivered_as_its_cause(self, tmp_path):
        result, calls, flow = _deliver(tmp_path, report=api_extras.PilotReport('stopped'))
        assert result.success and result.released and flow.sound.path.exists()  # no cause left: reconciliation


def test_a_refined_peak_on_the_window_edge_is_not_confident(monkeypatch):
    lags = 2 * round(sound_offset.LAG_S * 48_000 / 48) + 1
    answers = iter([[0.0] * (lags // 2) + [0.95] + [0.0] * (lags // 2),          # a clear envelope peak at lag 0
                    [0.99] + [0.5] * (2 * sound_offset.REFINE)])                # the fine peak on the edge
    monkeypatch.setattr(sound_offset, 'scan', lambda ref, sig, starts: next(answers))
    row = sound_offset.correlate([0.0] * 96_000, [0.0] * 200_000, 48_000, 1100)
    assert row['ambiguous'] is True and row['peak'] == 0.99 and not sound_result.confident(row)


def test_a_line_without_a_newline_is_scanned_in_bounded_pieces(tmp_path):
    log = tmp_path / 'render.1.log'
    log.write_text('x' * (3 * 1024 * 1024) + ' Audio latency compensation (7 frames) ... Skipping compensation. '
                   + 'y' * 100_000 + 'seek' + 'table', encoding='utf-8')
    facts = wav_check.scan_log(log, 8 * 1024 * 1024)
    assert facts.fault is True and facts.latency_skipped is True
    assert wav_check.CARRY_MAX == 64 * 1024


@pytest.mark.parametrize('skipped,expected', [(False, 16_000), (True, 16_000 + 7 * 1_600)])
def test_expected_samples_with_a_skipped_compensation(skipped, expected):
    assert sound_result.expected_samples(10, 48_000, 30, 1, skipped) == expected
