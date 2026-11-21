# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the manager's ``sound-result/`` form checks
(:mod:`workers.serializers.sequence_sound`, with the shared rules of
``shared.unreal.sound_result``; Unreal sequence spec FR-SA-5, FR-SA-6,
AC-16, AC-41, AC-48).

Covers: the FR-SA-5 header bounds the manager applies again (16-bit PCM,
1-8 channels, 8-192 kHz, length within 256 samples: 256 short is accepted,
300 short is refused); the ``offset`` text (``NaN``, more than 16 KiB, an
unknown key, an offset above 10 s of samples, a peak above 1, each named
as ``offset.<key>``); the flag, the seconds and the summary; unknown,
missing and repeated form fields.
"""

import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import QueryDict
from django.utils.datastructures import MultiValueDict

from shared.unreal.sound_result import SoundResultError, check_wav_params, expected_samples
from workers.serializers.sequence_sound import form_problem, parse_form

RATE = 48_000


def _offset(**overrides):
    value = {
        'status': 'measured', 'offset_samples': 9418, 'reason': '', 'shots_tried': 1, 'shots_confident': 1,
        'shots_agreeing': 1, 'min_peak': 0.9, 'spread_samples': 0, 'unsupported_sections': 0,
        'export_s': 1.0, 'correlate_s': 2.0,
        'shots': [{'shot': 1, 'lag_samples': 9418, 'envelope_peak': 0.9, 'envelope_margin': 0.2,
                   'peak': 0.95, 'ambiguous': False}],
    }
    value.update(overrides)
    return value


def _data(**fields):
    data = {'offset': json.dumps(_offset()), 'latency_compensation_skipped': 'false'}
    data.update(fields)
    return data


class TestWavParams:
    def test_expected_samples(self):
        assert expected_samples(10, RATE, 30, 1) == 16_000
        assert expected_samples(30, RATE, 30000, 1001) == 48_048

    def test_skipped_compensation_adds_seven_frames(self):
        assert expected_samples(10, RATE, 30, 1, latency_skipped=True) == 17 * 1600

    @pytest.mark.parametrize('samples', [16_000, 16_000 - 256, 16_000 + 256])
    def test_within_256_accepted(self, samples):
        check_wav_params(2, 2, RATE, samples, 16_000)

    @pytest.mark.parametrize('args, text', [
        ((2, 2, RATE, 16_000 - 300, 16_000), 'the run needs 16000'),
        ((2, 1, RATE, 16_000, 16_000), '16-bit'),
        ((2, 3, RATE, 16_000, 16_000), '16-bit'),
        ((9, 2, RATE, 16_000, 16_000), '9 channels'),
        ((2, 2, 7_999, 16_000, 16_000), 'outside 8,000-192,000'),
        ((2, 2, 192_001, 16_000, 16_000), 'outside 8,000-192,000'),
    ], ids=['300-short', '8-bit', '24-bit', '9-channels', 'rate-low', 'rate-high'])
    def test_refused(self, args, text):
        with pytest.raises(SoundResultError, match=text):
            check_wav_params(*args)


class TestOffset:
    def test_measured_object_accepted(self):
        assert parse_form(_data(), RATE)['offset']['offset_samples'] == 9418

    def test_ten_seconds_at_the_files_rate_accepted(self):
        body = parse_form(_data(offset=json.dumps(_offset(offset_samples=-441_000))), 44_100)
        assert body['offset']['offset_samples'] == -441_000

    @pytest.mark.parametrize('text, message', [
        ('{', 'not valid JSON'),
        ('{"status": NaN}', 'not valid JSON'),
        (json.dumps(_offset()) + ' ' * 16 * 1024, '16 KiB'),
        (json.dumps(_offset(extra=1)), r'Invalid sound result \(offset\)'),
        (json.dumps(_offset(offset_samples=10 * RATE + 1)), r'offset\.offset_samples is invalid'),
        (json.dumps(_offset(offset_samples=None)), r'offset\.offset_samples is invalid'),
        (json.dumps(_offset(shots=[dict(_offset()['shots'][0], peak=1.5)])), r'offset\.shots\[0\]\.peak'),
        (json.dumps(_offset(shots=[dict(_offset()['shots'][0], extra=1)])), r'offset\.shots\[0\] is invalid'),
        (json.dumps(_offset(reason='x' * 513)), r'offset\.reason'),
        (json.dumps(_offset(status='guess')), r'offset\.status'),
    ], ids=['broken', 'nan', 'over-16-kib', 'unknown-key', 'over-10-s', 'measured-null', 'peak-above-1',
            'shot-key', 'long-reason', 'status'])
    def test_refused(self, text, message):
        with pytest.raises(SoundResultError, match=message) as info:
            parse_form(_data(offset=text), RATE)
        assert info.value.field == 'offset'


class TestFields:
    def test_values(self):
        body = parse_form(_data(latency_compensation_skipped='true', render_s='61.25', summary='ok'), RATE)
        assert (body['latency_compensation_skipped'], body['render_s'], body['summary']) == (True, 61.25, 'ok')
        assert parse_form(_data(), RATE)['render_s'] is None

    @pytest.mark.parametrize('field, value', [
        ('latency_compensation_skipped', 'True'), ('latency_compensation_skipped', '1'), ('render_s', '-1'),
        ('render_s', '1e3'), ('render_s', '1234567'), ('summary', 'x' * (32 * 1024 + 1)),
    ], ids=['flag-case', 'flag-digit', 'negative', 'exponent', 'seven-digits', 'long-summary'])
    def test_refused(self, field, value):
        with pytest.raises(SoundResultError) as info:
            parse_form(_data(**{field: value}), RATE)
        assert info.value.field == field


def _query(files=True, **fields):
    data = QueryDict(mutable=True)
    for name, value in fields.items():
        data.setlist(name, value if isinstance(value, list) else [value])
    if files:
        data.update(MultiValueDict({'file': [SimpleUploadedFile('run.wav', b'RIFF')]}))
    return data


class TestFormProblem:
    def test_complete_form(self):
        assert form_problem(_query(offset='{}', latency_compensation_skipped='false')) is None

    @pytest.mark.parametrize('data, text', [
        (_query(offset='{}', latency_compensation_skipped='false', name='x'), 'Unknown fields: name.'),
        (_query(latency_compensation_skipped='false'), 'Send offset.'),
        (_query(files=False, offset='{}', latency_compensation_skipped='false'), 'Send file.'),
        (_query(offset=['{}', '{}'], latency_compensation_skipped='false'), 'Send each field once.'),
    ], ids=['unknown', 'missing-offset', 'missing-file', 'twice'])
    def test_refused(self, data, text):
        assert form_problem(data) == text
