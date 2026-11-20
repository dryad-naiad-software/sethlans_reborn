# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The worker's strict check of a ``list_shots`` result (sequence rendering FR-SD-5..8, AC-41): every bad field,
the FR-SD-6 names cleaned again, the caps, and the ``shots-result/`` body, which the manager checks with the same
rules (``shared.unreal.shots_result``).
"""
import copy
import json

import pytest

from sethlans_worker_agent.unreal_executor import sequence_prep
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure
from shared.unreal import shots_result
from tests.unit.worker.unreal._executor_fakes import params

FACTS = {'resolution': [1920, 1080], 'custom_frame_rate': None, 'frame_step': None, 'handle_frames': 0,
         'frame_number_offset': 0, 'zero_pad': 4, 'temporal_samples': 8, 'spatial_samples': 1, 'layers_ok': True,
         'can_png': True, 'can_exr': True, 'can_sound': False, 'game_overrides': 'preset', 'texture_streaming': 'off'}
RESULT = {'ok': True, 'preset_kind': 'graph', 'playback_range': [0, 400], 'frame_rate': [30000, 1001],
          'shot_source': 'shot_tracks', 'shots': [['sh010', 0, 99], ['sh020', 99, 202], ['sh020-part2', 250, 400]],
          'preset_facts': FACTS, 'warnings': ['Shot sh030 has no camera cut inside the playback range; its frames '
                                              'are not rendered.'],
          # what the prep script also writes; never sent
          'prep_s': 3.2, 'python': '3.11.8', 'scalability': 4, 'game_overrides': 'preset', 'texture_streaming': 'off'}


def parse(**changes):
    data = copy.deepcopy(RESULT)
    data.update(changes)
    return sequence_prep.parse_shots(data)


def facts(**changes):
    return dict(copy.deepcopy(FACTS), **changes)


def test_the_params_carry_the_mode_and_only_validated_values():
    data = sequence_prep.shots_params(params(kind='shots', frame=0), 'C:\\j\\result.json', 'C:\\j\\scripts')
    assert data == {'schema': 1, 'job_id': 42, 'mode': 'list_shots', 'map': '/Game/Maps/Main',
                    'sequence': '/Game/Seq/Edit', 'preset': '/Game/Presets/MRG', 'result_path': 'C:\\j\\result.json',
                    'scripts_dir': 'C:\\j\\scripts', 'max_shots': 2000}


def test_a_good_result_and_its_body_which_the_manager_accepts_unchanged():
    body = parse().body()
    assert body == {
        'preset_kind': 'graph', 'playback_range': [0, 400], 'frame_rate': [30000, 1001], 'shot_source': 'shot_tracks',
        'shots': [['sh010', 0, 99], ['sh020', 99, 202], ['sh020-part2', 250, 400]], 'preset_facts': FACTS,
        'warnings': RESULT['warnings']}
    checked = shots_result.check_shots_result(json.loads(json.dumps(body)))  # the manager's own check, names refused
    assert [(s['index'], s['name']) for s in checked['shots']] == [(1, 'sh010'), (2, 'sh020'), (3, 'sh020-part2')]


@pytest.mark.parametrize('names,expected', [
    (['sh 010/ä', '..', '-x', '.hidden', '名前'], ['sh_010__', 'shot_002', '_x', '_hidden', '__']),
    (['a', 'a', 'A', 'a-2'], ['a', 'a-2', 'A-3', 'a-2-2']),       # duplicates numbered, case-insensitively
    (['y' * 70, 'y' * 64, ''], ['y' * 64, 'y' * 62 + '-2', 'shot_003']),
    (['../x', 'a\x00b\nc'], ['_._x', 'a_b_c']),
])
def test_names_from_the_engine_are_cleaned_again(names, expected):
    rows = [[name, i * 10, i * 10 + 10] for i, name in enumerate(names)]
    discovery = parse(shots=rows)
    assert [row[0] for row in discovery.shots] == expected
    assert shots_result.clean_shot_names(expected) == expected  # the manager's cleaning changes nothing


@pytest.mark.parametrize('changes,field', [
    ({'preset_kind': 'video'}, 'preset_kind'), ({'preset_kind': None}, 'preset_kind'),
    ({'playback_range': [0]}, 'playback_range'), ({'playback_range': [10, 10]}, 'playback_range'),
    ({'playback_range': [0, 10_000_001]}, 'playback_range'), ({'playback_range': [0.0, 400]}, 'playback_range'),
    ({'playback_range': [True, 400]}, 'playback_range'),
    ({'frame_rate': [30, 0]}, 'frame_rate'), ({'frame_rate': [0, 1]}, 'frame_rate'),
    ({'frame_rate': [121, 1]}, 'frame_rate'), ({'frame_rate': [1, 2]}, 'frame_rate'),
    ({'frame_rate': [30000, 1002]}, 'frame_rate'), ({'frame_rate': [1_000_001, 10000]}, 'frame_rate'),
    ({'frame_rate': '30/1'}, 'frame_rate'),
    ({'shot_source': 'markers'}, 'shot_source'),
    ({'shots': []}, 'shots'), ({'shots': [['a', i, i + 1] for i in range(2001)]}, 'shots'),
    ({'shots': [['a', 5, 5]]}, 'shots[0]'), ({'shots': [['a', 10, 20], ['b', 0, 5]]}, 'shots[1]'),
    ({'shots': [['a', 0, 20], ['b', 10, 30]]}, 'shots[1]'), ({'shots': [['a', 300, 401]]}, 'shots[0]'),
    ({'shots': [['a', 0, 10, 'x']]}, 'shots[0]'), ({'shots': [{'name': 'a', 'start': 0, 'end': 10}]}, 'shots[0]'),
    ({'preset_facts': None}, 'preset_facts'), ({'preset_facts': facts(extra=1)}, 'preset_facts.extra'),
    ({'preset_facts': {k: v for k, v in FACTS.items() if k != 'zero_pad'}}, 'preset_facts.zero_pad'),
    ({'preset_facts': facts(resolution=[1920])}, 'preset_facts.resolution'),
    ({'preset_facts': facts(resolution=[0, 1080])}, 'preset_facts.resolution'),
    ({'preset_facts': facts(resolution=[32769, 1080])}, 'preset_facts.resolution'),
    ({'preset_facts': facts(custom_frame_rate=[24, 0])}, 'preset_facts.custom_frame_rate'),
    ({'preset_facts': facts(temporal_samples=0)}, 'preset_facts.temporal_samples'),
    ({'preset_facts': facts(temporal_samples=None)}, 'preset_facts.temporal_samples'),
    ({'preset_facts': facts(frame_step=0)}, 'preset_facts.frame_step'),
    ({'preset_facts': facts(zero_pad=33)}, 'preset_facts.zero_pad'),
    ({'preset_facts': facts(handle_frames=-1)}, 'preset_facts.handle_frames'),
    ({'preset_facts': facts(layers_ok=1)}, 'preset_facts.layers_ok'),
    ({'preset_facts': facts(can_exr='yes')}, 'preset_facts.can_exr'),
    ({'warnings': 'one'}, 'warnings'),
])
def test_every_bad_field_fails_naming_it(changes, field):
    with pytest.raises(JobFailure) as caught:
        parse(**changes)
    assert caught.value.message == f"Shot discovery returned an invalid result ({field})."


def test_a_missing_key_fails_naming_it():
    data = copy.deepcopy(RESULT)
    del data['frame_rate']
    with pytest.raises(JobFailure) as caught:
        sequence_prep.parse_shots(data)
    assert caught.value.message == "Shot discovery returned an invalid result (frame_rate)."


def test_a_playback_start_below_zero_has_its_own_message():
    with pytest.raises(JobFailure) as caught:
        parse(playback_range=[-10, 400])
    assert caught.value.message == "Sequences that start before frame 0 are not supported."


def test_optional_facts_may_be_absent_and_unknown_words_are_unknown():
    discovery = parse(preset_facts=facts(frame_step=None, handle_frames=None, frame_number_offset=None, zero_pad=None,
                                         resolution=None, custom_frame_rate=[25, 1], game_overrides='IMPLICIT',
                                         texture_streaming=['off']))
    got = discovery.preset_facts
    assert (got['frame_step'], got['handle_frames'], got['frame_number_offset'], got['zero_pad']) == (None,) * 4
    assert got['resolution'] is None and got['custom_frame_rate'] == [25, 1]
    assert (got['game_overrides'], got['texture_streaming']) == ('unknown', 'unknown')


def test_warnings_are_capped_and_cleaned():
    discovery = parse(warnings=['w\x07' + 'x' * 600] + [f'w{i}' for i in range(80)])
    assert len(discovery.warnings) == 64 and discovery.warnings[0] == 'w' + 'x' * 511


def test_failed_and_unreadable_results():
    with pytest.raises(JobFailure) as caught:
        sequence_prep.parse_shots({'ok': False, 'error': 'Shot sections a and b overlap;\x1b[31m'})
    assert caught.value.message == 'Shot sections a and b overlap;[31m'
    with pytest.raises(JobFailure) as caught:
        sequence_prep.parse_shots(None)
    assert caught.value.message == 'Shot discovery wrote no readable result.'


def test_the_largest_body_fits_the_result_class():
    """D21: 2,000 shots with 64-character names and 64 long warnings stay far under 1 MiB."""
    shots = [['n' * 64, i * 5000, i * 5000 + 5000] for i in range(2000)]
    discovery = parse(playback_range=[0, 10_000_000], shots=shots, warnings=['w' * 600] * 64)
    assert len(json.dumps(discovery.body()).encode('utf-8')) < 400 * 1024
