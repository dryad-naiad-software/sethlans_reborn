# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
End-to-end manager flow of one sequence submission (Unreal sequence spec
FR-SQ-4, FR-SP-8, FR-SR-1, FR-SS-2b, FR-SS-3, FR-SS-4, D8): read the
shots, submit, plan pieces and a pilot, measure, claim a piece, upload its
frames, finish it, and see the shots and the submission follow.
"""

import pytest

from ..unreal_helpers import unreal_asset
from ..unreal_pilot_helpers import claim, ready_worker
from .submission_helpers import (
    claimed_piece, done_discovery, piece_body, pieces_of, pilot_of, post_piece, shots_of, submit, upload_all,
)


@pytest.fixture
def asset(db, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return unreal_asset()


@pytest.mark.django_db
class TestFlow:

    def test_submit_plans_pieces_and_a_pilot(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        submission, resp = submit(admin_client, asset)
        assert resp.data['status'] == 'QUEUED'
        assert [(s.index, s.start_frame, s.end_frame) for s in shots_of(submission)] == [(1, 0, 4), (2, 4, 10), (3, 20, 24)]
        pieces = pieces_of(submission)
        assert [(p.start_frame, p.end_frame, p.unreal_lead_in_frames) for p in pieces] == [(0, 9, 0), (20, 23, 0)]
        pilot = pilot_of(asset)
        assert pilot.render_settings['unreal.pilot_frames'] == [0, 4, 20]
        assert {p.unreal_target_sha256 for p in pieces} == {pilot.unreal_target_sha256}

    def test_piece_to_done(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        submission, piece = claimed_piece(admin_client, asset, worker, client)
        submission.refresh_from_db()
        assert submission.status == 'RENDERING'
        upload_all(client, piece)
        resp = post_piece(client, piece, piece_body(piece))
        assert resp.status_code == 200, resp.data
        piece.refresh_from_db()
        assert piece.status == 'DONE'
        shots = shots_of(submission)
        assert [s.status for s in shots] == ['DONE', 'DONE', 'PENDING']
        assert len(shots[0].timings) == 1
        other = pieces_of(submission)[1]
        ready_worker(worker)
        assert claim(client, other, worker).status_code == 200
        other.refresh_from_db()
        upload_all(client, other)
        assert post_piece(client, other, piece_body(other)).status_code == 200
        submission.refresh_from_db()
        assert submission.status == 'DONE'
        assert submission.completed_at is not None
