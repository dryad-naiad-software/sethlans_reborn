# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for :mod:`workers.services.sound_runs` (Unreal sequence spec
FR-SA-11, D18, D24, AC-45d, AC-47).

Covers: the correction a run uses (measured; else the preset's override in
samples, within +-10,000 ms; else none); the D24 warning of a run that is
not measured names its first and last shot, the status, the cleaned reason
and the correction used; a measured run whose shots 7 and 8 lie 2,000
samples away names them (AC-45d) and an agreeing run has no warning; the
``sound_runs`` entry and its replacement on a repeat; the submission's
warnings drop the run's stale ones and never lose a new one at the cap;
the audio job's summary warnings (#284): only the worker's ``warning=``
lines, cleaned, cut to 512 characters, deduplicated, at most 64 read and
16 added after the run's own warnings (another run's warning survives), and
the worker's real summary format parses.
"""

from types import SimpleNamespace

import pytest
from sethlans_worker_agent.unreal_executor.summary import PREFIX, Summary

from workers.services.sound_runs import (
    SUMMARY_WARNINGS_PER_RUN, applied, merged_runs, merged_warnings, override_ms, run_entry, run_warnings,
    summary_warnings, with_summary_warnings,
)

RATE = 48_000
NAMES = {n: f'sh{n:03d}' for n in range(1, 9)}


def _row(shot, lag, peak=0.95, ambiguous=False):
    return {'shot': shot, 'lag_samples': lag, 'envelope_peak': 0.9, 'envelope_margin': 0.3, 'peak': peak,
            'ambiguous': ambiguous}


def _offset(status='measured', offset_samples=9418, reason='', rows=None):
    rows = rows if rows is not None else [_row(shot, 9418) for shot in (1, 2, 3)]
    return {
        'status': status, 'offset_samples': offset_samples, 'reason': reason, 'shots_tried': len(rows),
        'shots_confident': len(rows), 'shots_agreeing': len(rows), 'min_peak': 0.9, 'spread_samples': 0,
        'unsupported_sections': 0, 'export_s': 1.0, 'correlate_s': 1.0, 'shots': rows,
    }


class TestApplied:
    def test_measured_wins_over_an_override(self):
        assert applied(_offset(), RATE, 250) == (9418, 'measured')

    @pytest.mark.parametrize('status', ['low_confidence', 'unavailable'])
    def test_override_in_samples(self, status):
        assert applied(_offset(status=status, offset_samples=None), RATE, 196) == (9408, 'override')
        assert applied(_offset(status=status, offset_samples=None), RATE, -5) == (-240, 'override')

    def test_none_without_an_override(self):
        assert applied(_offset(status='unavailable', offset_samples=None), RATE, None) == (0, 'none')

    @pytest.mark.parametrize('value, expected', [
        (10_000, 10_000), (-10_000, -10_000), (10_001, None), (-10_001, None), (True, None), ('5', None), (None, None),
    ])
    def test_override_bounds(self, value, expected):
        assert override_ms(value) == expected


class TestWarnings:
    def test_unmeasured_without_override(self):
        offset = _offset(status='unavailable', offset_samples=None, reason='no enabled audio\x00 section\x07', rows=[])
        warnings = run_warnings(offset, RATE, 'none', 0, {1: 'sh010', 2: 'sh020', 4: 'sh030'}, '/Game/P.P')
        assert warnings == [
            "Sound for shots sh010–sh030 could not be lined up automatically (unavailable: no enabled audio "
            "section); no correction was applied. Set a sound offset for preset /Game/P.P to correct it."
        ]

    def test_low_confidence_with_override(self):
        offset = _offset(status='low_confidence', offset_samples=None, reason='shots disagree')
        warnings = run_warnings(offset, RATE, 'override', 9408, {1: 'sh010'}, '/Game/P.P')
        assert warnings == [
            "Sound for shots sh010–sh010 could not be lined up automatically (low_confidence: shots "
            "disagree); the preset's sound offset of 196 ms was applied."
        ]

    def test_empty_reason_is_named(self):
        offset = _offset(status='unavailable', offset_samples=None, rows=[])
        assert '(unavailable: no reason given)' in run_warnings(offset, RATE, 'none', 0, {}, 'p')[0]

    def test_ac45d_disagreeing_shots_are_named(self):
        rows = [_row(shot, 9418) for shot in range(1, 7)] + [_row(shot, 11_418) for shot in (7, 8)]
        warnings = run_warnings(_offset(rows=rows), RATE, 'measured', 9418, NAMES, 'p')
        assert warnings == [
            "Sound offset: shots sh007, sh008 differ from the run's offset by up to 42 ms; check the sound there."
        ]

    def test_unconfident_shots_are_not_named(self):
        rows = [_row(shot, 9418) for shot in range(1, 5)]
        rows += [_row(5, 20_000, peak=0.5), _row(6, 20_000, ambiguous=True)]
        assert run_warnings(_offset(rows=rows), RATE, 'measured', 9418, NAMES, 'p') == []

    def test_agreeing_run_has_no_warning(self):
        rows = [_row(shot, 9418 + shot) for shot in NAMES]
        assert run_warnings(_offset(rows=rows), RATE, 'measured', 9418, NAMES, 'p') == []


class TestRecords:
    def _job(self, pk=7, start=20, end=23, lead=8):
        return SimpleNamespace(pk=pk, start_frame=start, end_frame=end, unreal_lead_in_frames=lead)

    def test_entry(self):
        entry = run_entry(self._job(), _offset(), RATE, None, {'silent': False})
        assert (entry['job'], entry['start'], entry['end'], entry['r0'], entry['rate']) == (7, 20, 24, 12, RATE)
        assert (entry['applied_samples'], entry['applied_source'], entry['silent']) == (9418, 'measured', False)

    def test_runs_replace_the_same_job_in_order(self):
        old = [{'job': 9, 'start': 30}, {'job': 7, 'start': 20, 'applied_samples': 0}]
        runs = merged_runs(old, {'job': 7, 'start': 20, 'applied_samples': 5})
        assert runs == [{'job': 7, 'start': 20, 'applied_samples': 5}, {'job': 9, 'start': 30}]

    def test_warnings_drop_the_runs_stale_ones(self):
        result = merged_warnings(['a', 'old', 'b'], {'warnings': ['old']}, ['new', 'new'], 64)
        assert result == ['a', 'b', 'new']

    def test_new_warnings_survive_the_cap(self):
        existing = [f'w{n}' for n in range(64)]
        result = merged_warnings(existing, None, ['sound'], 64)
        assert len(result) == 64 and result[-1] == 'sound' and 'w63' not in result


def _lines(*warnings):
    return '\n'.join([f'{PREFIX} sound_offset=measured'] + [f'{PREFIX} warning={text}' for text in warnings])


class TestSummaryWarnings:
    """#284: the audio job's own warnings from the worker's summary text."""

    def test_only_warning_lines_in_order(self):
        text = _lines('b', 'a') + f'\n{PREFIX} note=warning=x\nwarning=y\n {PREFIX} warning=z'
        assert summary_warnings(text) == ['b', 'a']

    def test_cleaned_cut_and_blank_skipped(self):
        text = _lines('a\x00b\x7f\x9f\r', ' ', 'c' * 600, 'n m')
        assert summary_warnings(text) == ['ab', 'c' * 512, 'n m']

    def test_deduplicated_and_capped_at_64(self):
        text = _lines(*['same'] * 3, *[f'w{n}' for n in range(80)])
        found = summary_warnings(text)
        assert len(found) == 64 and found[:2] == ['same', 'w0'] and found[-1] == 'w62'

    @pytest.mark.parametrize('text', ['', None, f'{PREFIX} sound_offset=measured'], ids=['empty', 'none', 'no-lines'])
    def test_none(self, text):
        assert summary_warnings(text) == []

    def test_after_the_run_warnings_deduplicated_and_limited(self):
        merged = with_summary_warnings(['offset', 'silent'], _lines('export', 'offset', 'more'), 3)
        assert merged == ['offset', 'silent', 'export']

    def test_summary_share_capped_at_16(self):
        merged = with_summary_warnings(['offset'], _lines('offset', *[f'w{n}' for n in range(64)]), 64)
        assert merged == ['offset'] + [f'w{n}' for n in range(SUMMARY_WARNINGS_PER_RUN)]
        assert SUMMARY_WARNINGS_PER_RUN == 16

    def test_another_runs_warning_survives_a_full_summary(self):
        mine = with_summary_warnings([], _lines(*[f'w{n}' for n in range(64)]), 64)
        assert merged_warnings(['other run'], None, mine, 64) == ['other run'] + mine

    def test_the_workers_summary_format(self):
        summary = Summary()
        summary.update({'audio_samples': 1})
        summary.warn('export: mute state unknown')
        summary.warn('second\nline')
        assert summary_warnings(summary.text()) == ['export: mute state unknown', 'second line']
