# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the audio job's own warnings on the version (#284,
Unreal sequence spec FR-SA-11): ``POST /api/jobs/{id}/sound-result/``
reads the ``warning=`` lines of the worker's ``summary`` field (the sound
export's warnings among them) into the run's warnings and the submission's.

Covers: export warnings show on the version after the offset warning; at
most 16 are added, each cut to 512 characters and cleaned, so another
run's warning survives a summary of 64; a warning the
offset already gave and a repeated one are listed once; no summary
warning (or no summary) adds nothing.  The summaries are the worker's real
``Summary.text()``, so the line format is checked end to end.
"""

import pytest

from sethlans_worker_agent.unreal_executor.summary import Summary

from workers.models import SequenceSubmission

from ..unreal_helpers import unreal_asset
from .sequence_helpers import PRESET
from .sound_helpers import claimed_audio, post_sound, unmeasured

SECTION = "Sound section 'Music' on track 'Audio': its mute state could not be read; it is treated as unmuted."
MODE = "Sound section 'VO': completion mode Project Default was resolved from the project config (KeepState)."
NOT_LINED_UP = ("Sound for shots sh010–sh020 could not be lined up automatically (unavailable: no enabled audio "
                f"section); no correction was applied. Set a sound offset for preset {PRESET} to correct it.")


def _summary(*warnings, **values):
    """The worker's real summary text with ``values`` and ``warnings``."""
    summary = Summary()
    summary.update(values or {'sound_offset': 'measured'})
    for text in warnings:
        summary.warn(text)
    return summary.text()


@pytest.fixture
def asset(db, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return unreal_asset()


@pytest.fixture
def audio(admin_client, asset, worker_with_token):
    worker, client = worker_with_token
    submission, job = claimed_audio(admin_client, asset, worker, client)
    return submission, job, client


def _run_warnings(audio, summary, **kwargs):
    """Post the result with ``summary``; returns ``(run warnings, submission warnings)``."""
    submission, job, client = audio
    resp = post_sound(client, job, summary=summary, **kwargs)
    assert resp.status_code == 200, resp.data
    submission.refresh_from_db()
    return submission.sound_runs[0]['warnings'], submission.warnings


@pytest.mark.django_db
class TestSummaryWarningsOnTheVersion:

    def test_export_warnings_show_after_the_offset_warning(self, audio):
        run, version = _run_warnings(audio, _summary(SECTION, MODE), offset_value=unmeasured())
        assert run == [NOT_LINED_UP, SECTION, MODE]
        assert version == run

    def test_measured_run_lists_only_the_export_warnings(self, audio):
        run, version = _run_warnings(audio, _summary(SECTION))
        assert run == [SECTION] and version == [SECTION]

    def test_capped_at_16_cut_and_cleaned(self, audio):
        lines = [f"[sethlans-unreal] warning=w{n:03d}" for n in range(80)]
        lines[0] = "[sethlans-unreal] warning=" + 'x' * 600 + '\x7f\x85'
        run, version = _run_warnings(audio, '\n'.join(lines), offset_value=unmeasured())
        assert len(run) == 17 and run[0] == NOT_LINED_UP
        assert run[1] == 'x' * 512 and run[-1] == 'w015' and 'w016' not in run
        assert version == run

    def test_64_summary_warnings_keep_another_runs_warning(self, audio):
        submission, _job, _client = audio
        other = "Sound for shots sh030–sh030 could not be lined up automatically (another run)."
        SequenceSubmission.objects.filter(pk=submission.pk).update(warnings=[other])
        run, version = _run_warnings(audio, _summary(*[f'export {n}' for n in range(64)]))
        assert run == [f'export {n}' for n in range(16)]
        assert version == [other] + run

    def test_deduplicated_with_the_offset_warning(self, audio):
        summary = _summary(NOT_LINED_UP, SECTION, SECTION)
        run, version = _run_warnings(audio, summary, offset_value=unmeasured())
        assert run == [NOT_LINED_UP, SECTION] and version == run

    def test_none_when_there_are_none(self, audio):
        run, version = _run_warnings(audio, _summary(sound_offset='measured', note='warning=not a warning line'))
        assert run == [] and version == []

    def test_none_without_a_summary(self, audio):
        submission, job, client = audio
        assert post_sound(client, job).status_code == 200
        submission.refresh_from_db()
        assert submission.sound_runs[0]['warnings'] == [] and submission.warnings == []
