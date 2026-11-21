# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the audio job's ``last_output``
(``workers.services.sequence_sound_result._summary``, #285).

Covers: the ``audio_samples=... audio_rate=... audio_channels=...`` header
is the first line and the worker's summary keeps one ``key=value`` per line
(an empty value does not run into the next line); control characters are
removed inside a line but the line breaks stay; ``\\r\\n`` and a lone
``\\r`` end a line; the size cap keeps whole lines only; an empty summary,
no summary or only blank lines give just the header.
"""

import pytest
from sethlans_worker_agent.unreal_executor.summary import PREFIX, Summary

from workers.services import sequence_sound_result
from workers.services.sequence_sound_result import _summary

HEADER = 'audio_samples=27200 audio_rate=48000 audio_channels=2'


def _last_output(summary=None, **body):
    if summary is not None:
        body['summary'] = summary
    return _summary(body, 48000, 2, 27200)


class TestLastOutput:

    def test_one_key_per_line_after_the_header(self):
        summary = Summary()
        summary.update({'audio_samples': 27200, 'note': '', 'farm_defaults_version': 2})
        summary.warn('export: mute state unknown')
        lines = _last_output(summary.text()).split('\n')
        assert lines[0] == HEADER
        assert lines[1:] == [line for line in summary.text().split('\n') if line]
        assert all(line.count(PREFIX) == 1 and line.startswith(PREFIX) for line in lines[1:])
        assert f'{PREFIX} note=' in lines and f'{PREFIX} farm_defaults_version=2' in lines

    def test_control_characters_removed_but_line_breaks_kept(self):
        text = f'{PREFIX} a=x\x00y\x07\n{PREFIX} b=\x1bz\x7f\x85\t'
        assert _last_output(text) == f'{HEADER}\n{PREFIX} a=xy\n{PREFIX} b=z'

    def test_crlf_and_lone_cr_end_a_line(self):
        text = f'{PREFIX} a=1\r\n{PREFIX} b=2\r{PREFIX} c=3\r\n'
        assert _last_output(text) == f'{HEADER}\n{PREFIX} a=1\n{PREFIX} b=2\n{PREFIX} c=3'

    @pytest.mark.parametrize('keep, expected', [
        (9, 'aaaa\nbbbb'),
        (13, 'aaaa\nbbbb'),
        (14, 'aaaa\nbbbb\ncccc'),
        (3, ''),
    ], ids=['exact-fit', 'next-line-short-by-one', 'all-fit', 'first-line-too-long'])
    def test_size_cap_keeps_whole_lines(self, monkeypatch, keep, expected):
        monkeypatch.setattr(sequence_sound_result, 'SUMMARY_KEEP', keep)
        result = _last_output('aaaa\n\nbbbb\ncccc')
        assert result == (f'{HEADER}\n{expected}' if expected else HEADER)

    def test_real_cap_never_leaves_a_half_line(self):
        line = f'{PREFIX} key=' + 'v' * 100
        result = _last_output('\n'.join([line] * 1000))
        body = result.split('\n')[1:]
        assert body and set(body) == {line}
        assert len('\n'.join(body)) <= sequence_sound_result.SUMMARY_KEEP

    @pytest.mark.parametrize('summary', ['', None, '\r\n\n \r\x00\n'], ids=['empty', 'missing', 'blank-lines'])
    def test_empty_summary_gives_just_the_header(self, summary):
        assert _last_output(summary) == HEADER
