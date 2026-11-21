# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the chunk 5 review items on the manager side (Unreal
sequence spec FR-SA-6, FR-SA-11, D24).

Covers: a shot's ``lag_samples`` is bounded to 20 s of samples, so a huge
value is a 400 and never an ``OverflowError``; the OpenAPI enums of
``applied_source`` and ``latency_compensation_skipped`` are pinned to one
constant tuple each, and the serializers use them; the run's correction
and disagreement come from the shared rules; one ``sound-result/`` upload
per job at a time.
"""

import json

import pytest
from django.conf import settings
from django.utils.module_loading import import_string

from shared.unreal import sound_result
from shared.unreal.sound_result import SoundResultError
from workers.constants_sequence import SOUND_APPLIED_SOURCES, SOUND_FLAG_VALUES
from workers.serializers.sequence_sound import SoundResultRequestSerializer, SoundResultResponseSerializer, parse_form
from workers.services import sound_runs
from workers.services.sequence_frames import FrameRefused
from workers.services.sequence_sound_result import UPLOAD_IN_PROGRESS, upload_slot

RATE = 48_000


def _row(shot, lag):
    return {'shot': shot, 'lag_samples': lag, 'envelope_peak': 0.9, 'envelope_margin': 0.2, 'peak': 0.95,
            'ambiguous': False}


def _offset(rows):
    return {
        'status': 'measured', 'offset_samples': 9418, 'reason': '', 'shots_tried': len(rows),
        'shots_confident': len(rows), 'shots_agreeing': len(rows), 'min_peak': 0.9, 'spread_samples': 0,
        'unsupported_sections': 0, 'export_s': 1.0, 'correlate_s': 1.0, 'shots': rows,
    }


def _form(rows):
    return {'offset': json.dumps(_offset(rows)), 'latency_compensation_skipped': 'false'}


class TestOneShotsKey:
    def test_piece_key_is_the_shared_run_key(self):
        from workers import constants_sequence
        assert constants_sequence.PIECE_SHOTS_KEY is sound_result.RUN_SHOTS_KEY == 'unreal.piece_shots'


class TestLagBound:
    def test_twenty_seconds_accepted(self):
        assert parse_form(_form([_row(1, -20 * RATE)]), RATE)['offset']['shots'][0]['lag_samples'] == -20 * RATE

    @pytest.mark.parametrize('lag', [20 * RATE + 1, 10 ** 400], ids=['one-over', 'huge'])
    def test_over_twenty_seconds_refused(self, lag):
        with pytest.raises(SoundResultError, match=r'offset\.shots\[0\]\.lag_samples'):
            parse_form(_form([_row(1, lag)]), RATE)


class TestEnums:
    def test_pinned_to_the_constant_tuples(self):
        overrides = settings.SPECTACULAR_SETTINGS['ENUM_NAME_OVERRIDES']
        assert import_string(overrides['SoundAppliedSourceEnum']) is SOUND_APPLIED_SOURCES
        assert import_string(overrides['SoundLatencySkippedEnum']) is SOUND_FLAG_VALUES

    def test_serializers_use_them(self):
        applied = SoundResultResponseSerializer().fields['applied_source']
        flag = SoundResultRequestSerializer().fields['latency_compensation_skipped']
        assert list(applied.choices) == list(SOUND_APPLIED_SOURCES)
        assert list(flag.choices) == list(SOUND_FLAG_VALUES)

    def test_the_shared_rule_returns_only_these_sources(self):
        offset = _offset([_row(1, 9418)])
        sources = {sound_runs.applied(offset, RATE, None)[1],
                   sound_runs.applied(dict(offset, status='unavailable', offset_samples=None), RATE, 5)[1],
                   sound_runs.applied(dict(offset, status='unavailable', offset_samples=None), RATE, None)[1]}
        assert sources == set(SOUND_APPLIED_SOURCES)


class TestSharedRules:
    def test_applied_is_the_shared_rule(self, monkeypatch):
        calls = []
        monkeypatch.setattr(sound_runs, 'applied_offset', lambda *args: calls.append(args) or (7, 'measured'))
        assert sound_runs.applied(_offset([]), RATE, 10_001) == (7, 'measured')
        assert calls == [(_offset([]), None, RATE)]

    def test_disagreement_names_the_shared_rules_shots(self, monkeypatch):
        rows = [_row(1, 9418), _row(2, 9418 + 4800)]
        monkeypatch.setattr(sound_runs, 'disagreeing_shots', lambda data: [2])
        warnings = sound_runs.run_warnings(_offset(rows), RATE, 'measured', 9418, {1: 'sh010', 2: 'sh020'}, 'p')
        assert warnings == ["Sound offset: shots sh020 differ from the run's offset by up to 100 ms; "
                            "check the sound there."]
        assert sound_result.disagreeing_shots(_offset(rows)) == [2]


class TestUploadSlot:
    def test_second_upload_of_the_same_job_is_409(self):
        with upload_slot(41):
            with pytest.raises(FrameRefused) as info:
                with upload_slot(41):
                    pass
            assert (info.value.status, info.value.message) == (409, UPLOAD_IN_PROGRESS)
            with upload_slot(42):
                pass

    def test_released_after_an_error(self):
        with pytest.raises(RuntimeError):
            with upload_slot(43):
                raise RuntimeError
        with upload_slot(43):
            pass
