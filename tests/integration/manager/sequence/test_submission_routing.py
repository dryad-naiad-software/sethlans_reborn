# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for a sequence version's routing note (Unreal sequence
spec FR-SR-3, FR-SR-4, FR-SR-5, FR-SQ-11, AC-23, AC-42).

Covers: the note of the first QUEUED piece (held while the key is
unmeasured; per-machine memory test D7: no farm-wide memory estimate once
some machine fits); the first QUEUED audio job's note, which follows the
piece key (D10), when no piece is queued; null when nothing is queued; a
version still reading its shots shows its shots job's note; the list's query count stays the
same for 1 and 20 versions with notes; an audio job's own note in the job
API.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from workers.models import Job

from ..unreal_helpers import unreal_asset
from ..unreal_pilot_helpers import JOBS, ready_worker
from .sound_helpers import audio_jobs_of
from .submission_helpers import SUBMISSIONS, done_discovery, measure_submission, pieces_of, submit

pytestmark = pytest.mark.django_db
# Per-machine FR-MT-U-2: the one online machine has not tested the key yet.
HELD = "Waiting for memory tests on 1 machine(s)."


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

    def test_held_then_no_memory_note_once_measured(self, admin_client, asset, card):
        submission, resp = submit(admin_client, asset, sound=False, resolution_x=128, resolution_y=72)
        assert resp.data['routing_note'] == HELD
        assert _list_note(admin_client) == HELD
        # D7: the old peak + 15% estimate refused a 16 GB card for 14,000 MB; now the machine's result decides.
        measure_submission(asset, peak_mb=14000)
        assert _list_note(admin_client) is None
        assert _detail_note(admin_client, submission) is None

    def test_card_size_gives_no_note(self, admin_client, asset, card):
        submission, _ = submit(admin_client, asset, sound=False)
        measure_submission(asset, peak_mb=14000)
        ready_worker(card, total_mb=8188)
        assert _detail_note(admin_client, submission) is None

    def test_audio_job_when_no_piece_is_queued(self, admin_client, asset, card):
        """D10: the audio job waits for the piece key's result; no pilot minimum (S2)."""
        submission, _ = submit(admin_client, asset, sound=True)
        Job.objects.filter(pk__in=[piece.pk for piece in pieces_of(submission)]).update(status='DONE')
        ready_worker(card, total_mb=8188)
        assert _detail_note(admin_client, submission) == HELD
        audio = audio_jobs_of(submission)[0]
        assert admin_client.get(f'{JOBS}{audio.pk}/').data['routing_note'] == HELD
        measure_submission(asset, worker=card, submission=submission)
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
