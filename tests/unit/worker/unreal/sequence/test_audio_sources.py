# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The strict parse of the export step's ``audio_sources.json`` (sequence rendering FR-SA-9, AC-46): it comes from a
UE process running project code, so every bad field is refused by name; unsupported rows are only counted;
sections need only overlap the run (a music bed from before ``r0`` is kept).
"""
import pytest

from sethlans_worker_agent.unreal_executor import sequence_prep

R0, END = 92, 280
ROW = {'start': 0, 'end': 400, 'start_offset_samples': 0, 'volume': 1.0, 'looping': True, 'wav': 'src_000.wav',
       'sample_rate': 48_000}


def _parse(rows, ok=True):
    return sequence_prep.parse_sources({'ok': ok, 'sections': rows}, R0, END)


def test_rows_and_unsupported_counts():
    rows, unsupported = _parse([ROW, {'unsupported': 'a sound cue'}, dict(ROW, start=270, end=500, volume=4)])
    assert unsupported == 1 and len(rows) == 2
    assert rows[0] == sequence_prep.SourceRow(0, 400, 0, 1.0, True, 'src_000.wav', 48_000)
    assert rows[1].start == 270 and rows[1].volume == 4.0
    assert _parse([]) == ((), 0)


@pytest.mark.parametrize('change,field', [
    ({'start': 280}, 'sections[0].start'), ({'end': 92}, 'sections[0].start'), ({'start': 5, 'end': 5}, 'sections[0].start'),
    ({'start': '0'}, 'sections[0].start'), ({'end': True}, 'sections[0].start'),
    ({'start_offset_samples': -1}, 'sections[0].start_offset_samples'),
    ({'start_offset_samples': 1.5}, 'sections[0].start_offset_samples'),
    ({'volume': 4.5}, 'sections[0].volume'), ({'volume': -0.1}, 'sections[0].volume'),
    ({'volume': True}, 'sections[0].volume'), ({'looping': 1}, 'sections[0].looping'),
    ({'wav': 'C:\\x\\src_000.wav'}, 'sections[0].wav'), ({'wav': '../src_000.wav'}, 'sections[0].wav'),
    ({'wav': 'src_000.WAV'}, 'sections[0].wav'), ({'wav': 'other.wav'}, 'sections[0].wav'),
    ({'sample_rate': 7_999}, 'sections[0].sample_rate'), ({'sample_rate': 192_001}, 'sections[0].sample_rate'),
    ({'extra': 1}, 'sections[0]'),
])
def test_each_bad_field_is_named(change, field):
    with pytest.raises(sequence_prep.SourcesError) as caught:
        _parse([dict(ROW, **change)])
    assert caught.value.field == field and str(caught.value) == f"the sound export returned an invalid result ({field})"


@pytest.mark.parametrize('data,field', [
    (None, 'result'), ({'ok': False}, 'ok'), ({'ok': True}, 'sections'), ({'ok': True, 'sections': {}}, 'sections'),
    ({'ok': True, 'sections': [ROW] * 65}, 'sections'), ({'ok': True, 'sections': [{'unsupported': 3}]}, 'sections[0]'),
    ({'ok': True, 'sections': ['x']}, 'sections[0]'),
])
def test_bad_results(data, field):
    with pytest.raises(sequence_prep.SourcesError) as caught:
        sequence_prep.parse_sources(data, R0, END)
    assert caught.value.field == field


def test_a_row_without_one_key_is_refused():
    row = dict(ROW)
    del row['looping']
    with pytest.raises(sequence_prep.SourcesError, match=r'\(sections\[0\]\)'):
        _parse([row])
