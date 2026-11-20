# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``GET /api/sequence-submissions/`` and the detail
(Unreal sequence spec FR-SQ-11, FR-SS-1, AC-42).

Covers: the list's query count does not grow with the number of rows;
the progress counts (one subquery each); newest first; the project
filter; the detail's shots and pilot frames.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from workers.models import Job, SequenceShot

from ..unreal_helpers import unreal_asset, unreal_project
from .submission_helpers import SUBMISSIONS, done_discovery, pieces_of, submit


@pytest.fixture
def asset(db, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return unreal_asset()


@pytest.fixture
def discovered(admin_client, asset, worker_with_token):
    worker, client = worker_with_token
    return done_discovery(admin_client, asset, worker, client)


def _list_queries(admin_client, params=None):
    with CaptureQueriesContext(connection) as ctx:
        resp = admin_client.get(SUBMISSIONS, params or {})
    assert resp.status_code == 200, resp.data
    return len(ctx.captured_queries), resp.data['results']


@pytest.mark.django_db
class TestList:

    def test_query_count_flat(self, admin_client, asset, discovered):
        submit(admin_client, asset)
        one, rows = _list_queries(admin_client)
        assert len(rows) == 1
        for _ in range(19):
            submit(admin_client, asset)
        twenty, rows = _list_queries(admin_client)
        assert len(rows) == 20
        assert twenty == one

    def test_counts(self, admin_client, asset, discovered):
        submission, _ = submit(admin_client, asset)
        SequenceShot.objects.filter(submission=submission, index=1).update(status='DONE')
        SequenceShot.objects.filter(submission=submission, index=3).update(status='ERROR')
        Job.objects.filter(pk=pieces_of(submission)[0].pk).update(status='DONE')
        _count, rows = _list_queries(admin_client)
        counts = {key: rows[0][key] for key in ('shots_total', 'shots_done', 'shots_error', 'pieces_total',
                                                'pieces_done')}
        assert counts == {'shots_total': 3, 'shots_done': 1, 'shots_error': 1, 'pieces_total': 2, 'pieces_done': 1}

    def test_waiting_submission_counts_zero(self, admin_client, asset):
        submit(admin_client, asset)
        _count, rows = _list_queries(admin_client)
        assert (rows[0]['status'], rows[0]['shots_total'], rows[0]['pieces_total']) == ('DISCOVERING', 0, 0)

    def test_newest_first(self, admin_client, asset, discovered):
        first, _ = submit(admin_client, asset)
        second, _ = submit(admin_client, asset)
        _count, rows = _list_queries(admin_client)
        assert [row['id'] for row in rows] == [second.pk, first.pk]
        assert [row['version'] for row in rows] == [2, 1]

    def test_project_filter(self, admin_client, asset, discovered, make_worker_client):
        mine, _ = submit(admin_client, asset)
        other_asset = unreal_asset(project=unreal_project(name='OtherShow'), name='OtherArchive',
                                   archive_sha256='ef' * 32)
        worker, client, _token = make_worker_client('otherhost')
        done_discovery(admin_client, other_asset, worker, client)
        theirs, _ = submit(admin_client, other_asset)
        _count, rows = _list_queries(admin_client, {'project': str(asset.project_id)})
        assert [row['id'] for row in rows] == [mine.pk]
        _count, rows = _list_queries(admin_client, {'asset': other_asset.pk})
        assert [row['id'] for row in rows] == [theirs.pk]


@pytest.mark.django_db
class TestDetail:

    def test_shots_and_pilot_frames(self, admin_client, asset, discovered):
        submission, _ = submit(admin_client, asset)
        data = admin_client.get(f'{SUBMISSIONS}{submission.pk}/').data
        assert data['pilot_frames'] == [0, 4, 20]
        assert [(s['index'], s['name'], s['start_frame'], s['end_frame'], s['status']) for s in data['shots']] == [
            (1, 'sh010', 0, 4, 'PENDING'), (2, 'sh020', 4, 10, 'PENDING'), (3, 'sh030', 20, 24, 'PENDING'),
        ]
        assert (data['shots_total'], data['pieces_total']) == (3, 2)
        assert data['warnings'] == [] and data['name'] == 'Ep01 v1'

    def test_create_response_is_the_detail(self, admin_client, asset, discovered):
        _submission, resp = submit(admin_client, asset)
        assert len(resp.data['shots']) == 3 and resp.data['pieces_total'] == 2
