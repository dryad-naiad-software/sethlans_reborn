# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for a sequence version's routing note (Unreal sequence
spec FR-SR-3, FR-SR-4, FR-SR-5, FR-SQ-11, AC-23, AC-42).

Covers: the note of the first QUEUED piece (held while the key is
unmeasured; the memory text with the output size, its source and the
largest card online once measured); the first QUEUED audio job's note with
the pilot's need when no piece is queued; null when nothing is queued; a
version still reading its shots shows its shots job's note; the list's query count stays the
same for 1 and 20 versions with notes; an audio job's own note in the job
API.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from workers.models import Job

from ..unreal_helpers import BUILD, unreal_asset
from ..unreal_pilot_helpers import JOBS, ready_worker
from .sound_helpers import audio_jobs_of
from .submission_helpers import SUBMISSIONS, done_discovery, measure_submission, pieces_of, submit

pytestmark = pytest.mark.django_db
HELD = "Waiting for this project's memory measurement (pilot render)."


def _memory(need, size, largest):
    return (f"No online worker with Unreal Engine build {BUILD} has enough GPU memory: this render needs {need} MB "
            f"{size}; the largest card online has {largest} MB. "
            "A card with more memory or a smaller output size is needed.")


@pytest.fixture
def asset(db, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return unreal_asset()


@pytest.fixture
def card(admin_client, asset, worker_with_token):
    """Shots read; the only online worker has one 16 GB card."""
    worker, client = worker_with_token
    done_discovery(admin_client, asset, worker, client)
    return ready_worker(worker, total_mb=16303)


def _list_note(admin_client):
    rows = admin_client.get(SUBMISSIONS).data['results']
    return rows[0]['routing_note']


def _detail_note(admin_client, submission):
    return admin_client.get(f'{SUBMISSIONS}{submission.pk}/').data['routing_note']


class TestNote:

    def test_held_then_memory_with_the_submission_size(self, admin_client, asset, card):
        submission, resp = submit(admin_client, asset, sound=False, resolution_x=128, resolution_y=72)
        assert resp.data['routing_note'] == HELD
        assert _list_note(admin_client) == HELD
        measure_submission(asset, peak_mb=14000)
        expected = _memory(16100, "at 128x72 (the submission's size)", 16303)
        assert _list_note(admin_client) == expected
        assert _detail_note(admin_client, submission) == expected

    def test_preset_size_and_a_card_that_fits(self, admin_client, asset, card):
        submission, _ = submit(admin_client, asset, sound=False)
        measure_submission(asset, peak_mb=14000)
        assert _detail_note(admin_client, submission) == _memory(16100, "at 64x36 (the preset's size)", 16303)
        ready_worker(card, total_mb=32607)
        assert _detail_note(admin_client, submission) is None

    def test_audio_job_when_no_piece_is_queued(self, admin_client, asset, card):
        submission, _ = submit(admin_client, asset, sound=True)
        Job.objects.filter(pk__in=[piece.pk for piece in pieces_of(submission)]).update(status='DONE')
        ready_worker(card, total_mb=8188)
        expected = _memory(12478, "at 128x72 (the sound render's size)", 8188)
        assert _detail_note(admin_client, submission) == expected
        audio = audio_jobs_of(submission)[0]
        assert admin_client.get(f'{JOBS}{audio.pk}/').data['routing_note'] == expected
        ready_worker(card, total_mb=16303)
        assert _detail_note(admin_client, submission) is None

    def test_nothing_queued_is_null(self, admin_client, asset, card):
        submission, _ = submit(admin_client, asset, sound=False)
        Job.objects.filter(sequence_submission=submission).update(status='RENDERING')
        assert _detail_note(admin_client, submission) is None

    def test_reading_shots_follows_the_shots_job(self, admin_client, asset, worker_with_token):
        """A waiting version shows its shots job's note: the share and build rules, never a memory note."""
        submission, resp = submit(admin_client, asset)
        assert (submission.status, resp.data['routing_note']) == ('DISCOVERING', "The Sethlans share is not configured.")
        ready_worker(worker_with_token[0], total_mb=4096)
        assert _detail_note(admin_client, submission) is None


def test_list_query_count_is_flat_with_notes(admin_client, asset, card):
    """AC-42 with routing notes: one query per response for the picked jobs, none per row."""
    submit(admin_client, asset, sound=True)
    with CaptureQueriesContext(connection) as one:
        rows = admin_client.get(SUBMISSIONS).data['results']
    assert rows[0]['routing_note'] == HELD
    for _ in range(19):
        submit(admin_client, asset, sound=True)
    with CaptureQueriesContext(connection) as twenty:
        rows = admin_client.get(SUBMISSIONS).data['results']
    assert len(rows) == 20 and {row['routing_note'] for row in rows} == {HELD}
    assert len(twenty.captured_queries) == len(one.captured_queries)
