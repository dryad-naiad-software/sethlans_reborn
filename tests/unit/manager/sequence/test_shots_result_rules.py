# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``shared.unreal.shots_result``: the shot discovery result
the manager checks again (Unreal sequence spec FR-SD-5..8, AC-41).

Covers the shot-name rules (cleaning, the manager's refusals, duplicates,
the 64-character cap), every bound of the body (range, rate, shots,
preset facts, warnings), unknown and missing keys, and the normalised
output (1-based indexes, end exclusive, cleaned names).
"""

import pytest

from shared.unreal.shots_result import (
    FACT_KEYS, MAX_SHOTS, MAX_WARNINGS, NEGATIVE_START_MESSAGE, SHOT_NAME_MAX, TOP_KEYS, WARNING_MAX_CHARS,
    ShotsResultError, check_shots_result, clean_shot_name, clean_shot_names, clean_text, refused_shot_name,
)
from tests._sequence_bodies import PRESET_FACTS, shots_body, with_facts


def _field(body, **kwargs):
    with pytest.raises(ShotsResultError) as info:
        check_shots_result(body, **kwargs)
    return info.value.field


class TestCleanShotName:
    @pytest.mark.parametrize('raw, expected', [
        ('sh010', 'sh010'),
        ('Shot A/B\\C', 'Shot_A_B_C'),
        ('naïve:shot*', 'na_ve_shot_'),
        ('.hidden', '_hidden'),
        ('-flag', '_flag'),
        ('..', 'shot_007'),
        ('.', 'shot_007'),
        ('', 'shot_007'),
        (None, 'shot_007'),
        ('a.b-c_d', 'a.b-c_d'),
    ])
    def test_rules(self, raw, expected):
        assert clean_shot_name(raw, 7) == expected

    def test_capped_at_64(self):
        assert clean_shot_name('x' * 200, 1) == 'x' * SHOT_NAME_MAX

    def test_ordinal_has_three_digits_or_more(self):
        assert clean_shot_name('', 1234) == 'shot_1234'


class TestCleanShotNames:
    def test_duplicates_get_numbered(self):
        assert clean_shot_names(['a', 'a', 'a']) == ['a', 'a-2', 'a-3']

    def test_duplicates_are_case_insensitive(self):
        assert clean_shot_names(['Intro', 'intro']) == ['Intro', 'intro-2']

    def test_suffix_skips_a_taken_name(self):
        assert clean_shot_names(['a-2', 'a', 'a']) == ['a-2', 'a', 'a-3']

    def test_suffix_keeps_the_cap(self):
        names = clean_shot_names(['y' * 80, 'y' * 70])
        assert names == ['y' * 64, 'y' * 62 + '-2']

    def test_cleaned_list_is_stable(self):
        once = clean_shot_names(['a', 'a', '..', '-x', 'b c'])
        assert clean_shot_names(once) == once


class TestRefusedShotName:
    @pytest.mark.parametrize('raw', ['.', '..', '.x', '-x', '', None, 5, 'z' * 513])
    def test_refused(self, raw):
        assert refused_shot_name(raw) is not None

    @pytest.mark.parametrize('raw', ['sh010', 'a..b', 'x-', '_.', 'Shot A'])
    def test_accepted(self, raw):
        assert refused_shot_name(raw) is None


class TestValidBody:
    def test_normalised_output(self):
        result = check_shots_result(shots_body(warnings=['frame step ignored']))
        assert result['playback_start'] == 0 and result['playback_end'] == 360
        assert (result['frame_rate_num'], result['frame_rate_den']) == (30, 1)
        assert result['shots'] == [
            {'index': 1, 'name': 'sh010_open', 'start': 0, 'end': 120},
            {'index': 2, 'name': 'sh020_walk', 'start': 120, 'end': 300},
            {'index': 3, 'name': 'sh030_door', 'start': 300, 'end': 360},
        ]
        assert result['preset_facts'] == PRESET_FACTS
        assert result['warnings'] == ['frame step ignored']
        assert result['shot_source'] == 'shot_tracks' and result['preset_kind'] == 'legacy'

    def test_names_are_cleaned_after_the_refusals(self):
        body = shots_body(shots=[['a b', 0, 10], ['a_b', 10, 20]])
        assert [s['name'] for s in check_shots_result(body)['shots']] == ['a_b', 'a_b-2']

    def test_gaps_between_shots_allowed(self):
        body = shots_body(shots=[['a', 10, 20], ['b', 40, 50]])
        assert len(check_shots_result(body)['shots']) == 2

    def test_rational_rate_accepted(self):
        assert check_shots_result(shots_body(frame_rate=[30000, 1001]))['frame_rate_den'] == 1001

    def test_null_optional_facts_accepted(self):
        body = with_facts(frame_step=None, handle_frames=None, frame_number_offset=None, zero_pad=None,
                          custom_frame_rate=[24, 1], resolution=None)
        facts = check_shots_result(body)['preset_facts']
        assert facts['frame_step'] is None and facts['custom_frame_rate'] == [24, 1]
        assert facts['resolution'] is None

    def test_handle_frames_up_to_100000(self):
        assert check_shots_result(with_facts(handle_frames=100_000))['preset_facts']['handle_frames'] == 100_000

    def test_worker_mode_cleans_instead_of_refusing(self):
        body = shots_body(shots=[['..', 0, 10], ['-x', 10, 20]])
        names = [s['name'] for s in check_shots_result(body, refuse_names=False)['shots']]
        assert names == ['shot_001', '_x']

    def test_upper_limits_accepted(self):
        shots = [[f's{i}', i, i + 1] for i in range(MAX_SHOTS)]
        body = shots_body(playback_range=[0, 10_000_000], shots=shots,
                          warnings=['w' * WARNING_MAX_CHARS] * MAX_WARNINGS, frame_rate=[120, 1])
        assert len(check_shots_result(body)['shots']) == MAX_SHOTS


class TestRefusedBody:
    @pytest.mark.parametrize('name', ['..', '.', '-x', '.hidden', ''])
    def test_refused_name(self, name):
        body = shots_body(shots=[['ok', 0, 10], [name, 10, 20]])
        assert _field(body) == 'shots[1].name'

    def test_warnings_lose_control_characters_and_surrogates(self):
        body = shots_body(warnings=['a\u0000b', 'c\r\nd\te', '\u009bx\u007f', 'y\udfffz', 'plain é'])
        assert check_shots_result(body)['warnings'] == ['ab', 'cde', 'x', 'yz', 'plain é']

    def test_clean_text(self):
        assert clean_text('\u0000') == '' and clean_text('ok text') == 'ok text'

    def test_too_many_warnings(self):
        assert _field(shots_body(warnings=['w'] * (MAX_WARNINGS + 1))) == 'warnings'

    def test_warning_too_long(self):
        assert _field(shots_body(warnings=['ok', 'w' * (WARNING_MAX_CHARS + 1)])) == 'warnings[1]'

    @pytest.mark.parametrize('warnings', [None, 'text', [3]])
    def test_warnings_shape(self, warnings):
        assert _field(shots_body(warnings=warnings)).startswith('warnings')

    def test_negative_start_has_its_own_message(self):
        with pytest.raises(ShotsResultError, match=NEGATIVE_START_MESSAGE):
            check_shots_result(shots_body(playback_range=[-10, 360], shots=[['a', 0, 10]]))

    @pytest.mark.parametrize('playback', [[10, 10], [0, 10_000_001], [0], ['0', 10], [0, True], None])
    def test_bad_range(self, playback):
        assert _field(shots_body(playback_range=playback)) == 'playback_range'

    @pytest.mark.parametrize('rate', [[0, 1], [1, 0], [121, 1], [1, 2], [1_000_001, 1], [30, 1002],
                                      [30.0, 1], [True, 1], [30], None])
    def test_bad_rate(self, rate):
        assert _field(shots_body(frame_rate=rate)) == 'frame_rate'

    @pytest.mark.parametrize('shots, field', [
        ([], 'shots'),
        (None, 'shots'),
        ([['a', 0, 10], ['b', 5, 20]], 'shots[1]'),
        ([['a', 10, 20], ['b', 0, 5]], 'shots[1]'),
        ([['a', 0, 0]], 'shots[0]'),
        ([['a', 300, 400]], 'shots[0]'),
        ([['a', 0, 10, 1]], 'shots[0]'),
        ([['a', '0', 10]], 'shots[0]'),
        ([['a', False, 10]], 'shots[0]'),
        ({'a': [0, 10]}, 'shots'),
    ])
    def test_bad_shots(self, shots, field):
        assert _field(shots_body(shots=shots)) == field

    def test_too_many_shots(self):
        shots = [[f's{i}', i, i + 1] for i in range(MAX_SHOTS + 1)]
        assert _field(shots_body(playback_range=[0, MAX_SHOTS + 1], shots=shots)) == 'shots'

    @pytest.mark.parametrize('key, value', [('preset_kind', 'other'), ('shot_source', 'markers')])
    def test_bad_choice(self, key, value):
        assert _field(shots_body(**{key: value})) == key

    @pytest.mark.parametrize('facts, field', [
        ({'resolution': [0, 1080]}, 'resolution'),
        ({'resolution': [1920]}, 'resolution'),
        ({'temporal_samples': 0}, 'temporal_samples'),
        ({'spatial_samples': '1'}, 'spatial_samples'),
        ({'can_exr': 1}, 'can_exr'),
        ({'layers_ok': None}, 'layers_ok'),
        ({'game_overrides': 'maybe'}, 'game_overrides'),
        ({'texture_streaming': ''}, 'texture_streaming'),
        ({'custom_frame_rate': [0, 1]}, 'custom_frame_rate'),
        ({'frame_step': 0}, 'frame_step'),
        ({'handle_frames': -1}, 'handle_frames'),
        ({'handle_frames': 100_001}, 'handle_frames'),
        ({'frame_number_offset': 10_000_001}, 'frame_number_offset'),
        ({'zero_pad': 33}, 'zero_pad'),
    ])
    def test_bad_fact(self, facts, field):
        assert _field(with_facts(**facts)) == f'preset_facts.{field}'

    def test_unknown_top_key(self):
        assert _field(shots_body(extra=1)) == 'extra'

    def test_unknown_fact_key(self):
        assert _field(with_facts(bonus=True)) == 'preset_facts.bonus'

    @pytest.mark.parametrize('key', TOP_KEYS)
    def test_missing_top_key(self, key):
        body = shots_body()
        del body[key]
        assert _field(body) == key

    @pytest.mark.parametrize('key', FACT_KEYS)
    def test_missing_fact_key(self, key):
        body = shots_body()
        del body['preset_facts'][key]
        assert _field(body) == f'preset_facts.{key}'

    @pytest.mark.parametrize('body', [None, [], 'x'])
    def test_not_an_object(self, body):
        assert _field(body) == 'body'

    def test_message_names_the_field(self):
        with pytest.raises(ShotsResultError, match=r"Shot discovery returned an invalid result \(frame_rate\)\."):
            check_shots_result(shots_body(frame_rate=[0, 1]))
