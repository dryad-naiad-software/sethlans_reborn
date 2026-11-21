# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The sound export's ``warnings`` reach the audio job's summary warnings (#284): forwarded whenever the export's
result was read (measured, fallen back or failed in the script), capped and cleaned like every prep warning, a
malformed field handled without failing anything, and each warning added once.
"""
import json

import pytest

from sethlans_worker_agent.unreal_executor import audio_offset_step
from sethlans_worker_agent.unreal_executor.audio_offset_step import INVALID_WARNINGS, export_warnings
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure
from shared.unreal import shots_result
from tests.unit.worker.unreal.sequence._signals import RATE
from tests.unit.worker.unreal.sequence.test_audio_flow import _flow

MUTE = "Mute state of audio tracks is not read; muted sounds are mixed."
DEFAULT = "Section Bed uses the Project Default completion mode; the project config says Keep Playing."
SECTIONS = [{'start': 0, 'end': 400, 'start_offset_samples': 0, 'volume': 1.0, 'looping': False, 'wav': 'src_000.wav',
             'sample_rate': RATE}]


def _run(tmp_path, warnings, ok=True, sections=None, seed=()):
    manifest = {'ok': ok, 'sections': SECTIONS if sections is None else sections, 'warnings': warnings}
    if not ok:
        manifest['error'] = 'A sound wave could not be exported.'
    flow, _runner, _job = _flow(tmp_path, manifest=manifest)
    for text in seed:
        flow.summary.warn(text)
    result = flow.run()
    return flow, result.sound.offset


class TestForwarded:

    def test_measured_export_warnings_reach_the_summary(self, tmp_path):
        flow, offset = _run(tmp_path, [MUTE, DEFAULT])
        assert offset['status'] == 'measured'
        warnings = list(flow.summary.warnings())
        assert MUTE in warnings and DEFAULT in warnings
        assert f"[sethlans-unreal] warning={DEFAULT}" in flow.summary.text().splitlines()

    def test_forwarded_when_the_offset_falls_back(self, tmp_path):
        bad = [dict(SECTIONS[0], wav='src_001.wav')]
        flow, offset = _run(tmp_path, [MUTE], sections=bad)
        assert offset['status'] == 'unavailable' and 'sections[0].wav' in offset['reason']
        assert MUTE in flow.summary.warnings()

    def test_forwarded_when_the_export_script_failed(self, tmp_path):
        flow, offset = _run(tmp_path, [DEFAULT], ok=False, sections=[])
        assert offset['reason'] == "the export step failed: A sound wave could not be exported."
        assert DEFAULT in flow.summary.warnings()

    def test_nothing_forwarded_when_the_launch_failed(self, tmp_path):
        """A timed-out or crashed launch leaves its result unread, even a file it may have left behind."""
        flow, _runner, _job = _flow(tmp_path, export_error=JobFailure("Unreal prep pass timed out after 1200 s."))
        (tmp_path / audio_offset_step.EXPORT_RESULT).write_text(
            json.dumps({'ok': True, 'sections': [], 'warnings': [MUTE]}), encoding='utf-8')
        offset = flow.run().sound.offset
        assert offset['reason'] == "the export step failed (Unreal prep pass timed out after 1200 s.)"
        assert MUTE not in flow.summary.warnings() and INVALID_WARNINGS not in flow.summary.warnings()


class TestCappedAndCleaned:

    def test_at_most_max_warnings_each_cut_and_cleaned(self):
        raw = [f"warning {n}\x00\x1b\n" + 'x' * 600 for n in range(shots_result.MAX_WARNINGS + 6)]
        texts, valid = export_warnings({'warnings': raw})
        assert valid and len(texts) == shots_result.MAX_WARNINGS
        assert all(len(text) == shots_result.WARNING_MAX_CHARS for text in texts)
        assert texts[0].startswith('warning 0x') and not any(c < ' ' for text in texts for c in text)
        assert texts[-1].startswith(f"warning {shots_result.MAX_WARNINGS - 1}x")

    def test_the_flow_forwards_the_cleaned_text(self, tmp_path):
        flow, _offset = _run(tmp_path, ["Section\r\nBed\tis odd." + 'y' * 900])
        forwarded = [text for text in flow.summary.warnings() if text.startswith('Section')]
        assert forwarded == [("SectionBedis odd." + 'y' * 900)[:shots_result.WARNING_MAX_CHARS]]

    def test_blank_entries_are_dropped(self):
        assert export_warnings({'warnings': ['', '\x00\n', ' ', MUTE]}) == ((MUTE,), True)


class TestMalformed:

    @pytest.mark.parametrize('data', [None, {}, {'ok': True}, 'not a dict'])
    def test_no_result_or_no_field_gives_nothing(self, data):
        assert export_warnings(data) == ((), True)

    @pytest.mark.parametrize('field', ['a string', {'a': 1}, 7, None, True])
    def test_a_field_that_is_not_a_list_is_invalid(self, field):
        assert export_warnings({'warnings': field}) == ((), False)

    def test_entries_that_are_not_text_are_skipped(self):
        assert export_warnings({'warnings': [1, None, MUTE, {'a': 1}, ['x']]}) == ((MUTE,), False)

    def test_the_flow_adds_the_fixed_warning_and_still_measures(self, tmp_path):
        flow, offset = _run(tmp_path, 'Mute state unread')
        assert offset['status'] == 'measured'
        assert INVALID_WARNINGS in flow.summary.warnings()
        assert 'Mute state unread' not in flow.summary.warnings()

    def test_the_flow_keeps_the_text_entries_of_a_mixed_list(self, tmp_path):
        flow, _offset = _run(tmp_path, [42, MUTE])
        warnings = list(flow.summary.warnings())
        assert MUTE in warnings and INVALID_WARNINGS in warnings


class TestDeduplicated:

    def test_repeats_in_the_result_are_read_once(self):
        assert export_warnings({'warnings': [MUTE, DEFAULT, MUTE, DEFAULT]}) == ((MUTE, DEFAULT), True)

    def test_repeats_after_cleaning_are_read_once(self):
        assert export_warnings({'warnings': [MUTE, MUTE + '\x00']}) == ((MUTE,), True)

    def test_a_warning_the_summary_has_is_not_added_again(self, tmp_path):
        flow, _offset = _run(tmp_path, [MUTE, MUTE, DEFAULT], seed=[MUTE])
        warnings = list(flow.summary.warnings())
        assert warnings.count(MUTE) == 1 and warnings.count(DEFAULT) == 1

    def test_the_invalid_warning_is_added_once(self, tmp_path):
        flow, _runner, _job = _flow(tmp_path, manifest={'ok': True, 'sections': SECTIONS, 'warnings': 'x'})
        flow.summary.warn(INVALID_WARNINGS)
        flow.run()
        assert list(flow.summary.warnings()).count(INVALID_WARNINGS) == 1
