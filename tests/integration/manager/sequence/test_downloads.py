# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the sequence downloads (Unreal sequence spec FR-SL-1..5,
D17, AC-20, AC-21, AC-39).

Covers: the whole version's and one shot's video, merged video, sound and
frames zip, with manager-made file names and content types; the zip's
entries (DONE shots only, 7-digit frames) streamed through a response
iterator with no temporary file; 404 "Not available." for an item not
DONE, a missing file, a file outside ``MEDIA_ROOT``, an unknown shot or
version; 409 instead of a short zip when a frame file is missing or out of
root; 429 for a third concurrent zip; a zip whose client disconnects frees
its slot; the detail's ``downloads`` flags; 409 and 429 are documented on
the frames routes only.
"""

import io
import json
import tempfile
import zipfile
from unittest import mock

import pytest
from django.core.signals import request_finished
from django.db import close_old_connections
from django.test import Client

from workers.models import SequenceFrame, SequenceShot, SequenceSubmission
from workers.views.zip_stream import SLOTS

from ..unreal_helpers import unreal_asset
from .build_helpers import RUN_FFMPEG, FakeFfmpeg, build_all, finish_pieces, finish_sound, manual_queue, version
from .submission_helpers import SUBMISSIONS, VIDEO

pytestmark = pytest.mark.django_db
NOT_AVAILABLE = {'error': 'Not available.'}


@pytest.fixture
def asset(db, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    settings.SETHLANS_MAX_CONCURRENT_ZIP_DOWNLOADS = 2
    return unreal_asset()


@pytest.fixture(autouse=True)
def slots_free():
    yield
    assert SLOTS.active == 0, "a zip download kept its slot"


@pytest.fixture
def done(admin_client, asset, worker_with_token, django_capture_on_commit_callbacks):
    worker, client = worker_with_token
    submission = version(admin_client, asset, worker, client, video_settings=VIDEO, sound=True, merge_sound=True)
    finish_sound(client, worker, submission)
    finish_pieces(client, worker, submission)
    with manual_queue(), mock.patch(RUN_FFMPEG, FakeFfmpeg()):
        build_all(django_capture_on_commit_callbacks, submission.pk)
    submission.refresh_from_db()
    assert submission.status == 'DONE'
    return submission


def _url(submission, action, index=None):
    shot = '' if index is None else f'shots/{index}/'
    return f'{SUBMISSIONS}{submission.pk}/{shot}{action}/'


def _close(resp):
    """Close a streamed response the way the test client does after reading it.

    ``close()`` sends ``request_finished``, whose ``close_old_connections``
    closes a connection inside the test's transaction on MySQL, MariaDB and
    PostgreSQL (SQLite's in-memory test database ignores it), which breaks
    every later query of the test. The test client takes it off around the
    close; so does this.
    """
    request_finished.disconnect(close_old_connections)
    try:
        resp.close()
    finally:
        request_finished.connect(close_old_connections)


def _body(resp):
    data = b''.join(resp.streaming_content)
    _close(resp)
    return data


def _names(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        return archive.namelist()


class TestFiles:

    @pytest.mark.parametrize('action, query, index, name, content_type', [
        ('download-video', '', None, 'unrealproj_ep01_v1.mp4', 'video/mp4'),
        ('download-video', '?sound=1', None, 'unrealproj_ep01_v1.mp4', 'video/mp4'),
        ('download-sound', '', None, 'unrealproj_ep01_v1.wav', 'audio/wav'),
        ('download-video', '', 2, 'unrealproj_ep01_v1_sh020.mp4', 'video/mp4'),
        ('download-video', '?sound=1', 2, 'unrealproj_ep01_v1_sh020.mp4', 'video/mp4'),
        ('download-sound', '', 2, 'unrealproj_ep01_v1_sh020.wav', 'audio/wav'),
    ], ids=['video', 'merged', 'sound', 'shot-video', 'shot-merged', 'shot-sound'])
    def test_file_download(self, admin_client, done, action, query, index, name, content_type):
        resp = admin_client.get(_url(done, action, index) + query)
        assert resp.status_code == 200
        assert resp['Content-Type'] == content_type
        assert resp['Content-Disposition'] == f'attachment; filename="{name}"'
        row = done if index is None else SequenceShot.objects.get(submission=done, index=index)
        field = {'download-sound': 'sound_file'}.get(action, 'sound_video_file' if query else 'video_file')
        with getattr(row, field).open('rb') as handle:
            assert _body(resp) == handle.read()

    def test_not_done_is_404(self, admin_client, done):
        SequenceSubmission.objects.filter(pk=done.pk).update(sound_video_status='ERROR')
        SequenceShot.objects.filter(submission=done, index=1).update(video_status='ASSEMBLING')
        assert admin_client.get(_url(done, 'download-video') + '?sound=1').data == NOT_AVAILABLE
        assert admin_client.get(_url(done, 'download-video', 1)).data == NOT_AVAILABLE

    def test_missing_or_outside_file_is_404(self, admin_client, done):
        done.video_file.storage.delete(done.video_file.name)
        resp = admin_client.get(_url(done, 'download-video'))
        assert (resp.status_code, resp.data) == (404, NOT_AVAILABLE)
        SequenceShot.objects.filter(submission=done, index=1).update(sound_file='../../outside.wav')
        assert admin_client.get(_url(done, 'download-sound', 1)).data == NOT_AVAILABLE

    def test_unknown_version_or_shot_is_404(self, admin_client, done):
        assert admin_client.get(f'{SUBMISSIONS}987654/download-video/').status_code == 404
        assert admin_client.get(_url(done, 'download-sound', 9)).data == NOT_AVAILABLE
        assert admin_client.get(_url(done, 'download-frames', 9)).data == NOT_AVAILABLE

    def test_downloads_flags(self, admin_client, done):
        SequenceShot.objects.filter(submission=done, index=3).update(status='ERROR', video_status=None)
        data = admin_client.get(f'{SUBMISSIONS}{done.pk}/').data
        assert data['downloads'] == {'video': True, 'video_sound': True, 'sound': True, 'frames': True}
        flags = {shot['index']: shot['downloads'] for shot in data['shots']}
        assert flags[1] == {'video': True, 'video_sound': True, 'sound': True, 'frames': True}
        assert flags[3] == {'video': False, 'video_sound': True, 'sound': True, 'frames': False}


class TestZip:

    def test_whole_zip_entries_without_a_temporary_file(self, admin_client, done):
        with mock.patch.object(tempfile, 'SpooledTemporaryFile', side_effect=AssertionError('spooled')), \
                mock.patch.object(tempfile, 'TemporaryFile', side_effect=AssertionError('temp file')), \
                mock.patch.object(tempfile, 'NamedTemporaryFile', side_effect=AssertionError('temp file')):
            resp = admin_client.get(_url(done, 'download-frames'))
            assert resp.status_code == 200 and resp.streaming
            data = _body(resp)
        assert resp['Content-Type'] == 'application/zip'
        assert resp['Content-Disposition'] == 'attachment; filename="unrealproj_ep01_v1.zip"'
        names = _names(data)
        assert names[:5] == ['0001_sh010/sh010.0000000.png', '0001_sh010/sh010.0000001.png',
                             '0001_sh010/sh010.0000002.png', '0001_sh010/sh010.0000003.png',
                             '0002_sh020/sh020.0000004.png']
        assert names[-1] == '0003_sh030/sh030.0000023.png' and len(names) == 14

    def test_only_done_shots(self, admin_client, done):
        SequenceShot.objects.filter(submission=done, index=2).update(status='ERROR')
        names = _names(_body(admin_client.get(_url(done, 'download-frames'))))
        assert {name.split('/')[0] for name in names} == {'0001_sh010', '0003_sh030'}
        assert admin_client.get(_url(done, 'download-frames', 2)).data == NOT_AVAILABLE

    def test_shot_zip(self, admin_client, done):
        resp = admin_client.get(_url(done, 'download-frames', 3))
        assert resp['Content-Disposition'] == 'attachment; filename="unrealproj_ep01_v1_sh030.zip"'
        assert _names(_body(resp)) == [f'sh030.{frame:07d}.png' for frame in range(20, 24)]

    def test_missing_frame_file_is_409_not_a_short_zip(self, admin_client, done):
        """Review item 4: a zip is never streamed without one of its frames."""
        frame = SequenceFrame.objects.get(shot__submission=done, shot__index=2, frame_number=5)
        frame.file.storage.delete(frame.file.name)
        for index in (None, 2):
            resp = admin_client.get(_url(done, 'download-frames', index))
            assert (resp.status_code, resp.data) == (409, {'error': (
                "A frame file of this version is missing or unreadable (sh020.0000005.png); "
                "render its shot again.")})
        other = admin_client.get(_url(done, 'download-frames', 1))
        assert other.status_code == 200
        _close(other)
        SequenceFrame.objects.filter(pk=frame.pk).update(file='../../outside.png')
        assert admin_client.get(_url(done, 'download-frames')).status_code == 409

    def test_no_done_shot_is_404(self, admin_client, done):
        SequenceShot.objects.filter(submission=done).update(status='ERROR')
        assert admin_client.get(_url(done, 'download-frames')).data == NOT_AVAILABLE

    def test_third_concurrent_zip_is_429(self, admin_client, done):
        first = admin_client.get(_url(done, 'download-frames'))
        second = admin_client.get(_url(done, 'download-frames', 1))
        third = admin_client.get(_url(done, 'download-frames'))
        assert (first.status_code, second.status_code) == (200, 200)
        assert (third.status_code, third.data) == (429, {'error': "Too many zip downloads at once; try again shortly."})
        _close(first)
        _close(second)
        again = admin_client.get(_url(done, 'download-frames'))
        assert again.status_code == 200
        _close(again)

    def test_disconnect_frees_the_slot(self, admin_client, done):
        """AC-39: a client that drops a started zip frees its slot, so two more downloads start."""
        dropped = admin_client.get(_url(done, 'download-frames'))
        held = admin_client.get(_url(done, 'download-frames'))
        assert next(iter(dropped.streaming_content))
        _close(dropped)
        never_read = admin_client.get(_url(done, 'download-frames'))
        assert never_read.status_code == 200
        _close(never_read)
        _close(held)
        two = [admin_client.get(_url(done, 'download-frames')) for _ in range(2)]
        assert [resp.status_code for resp in two] == [200, 200]
        for resp in two:
            assert len(_names(_body(resp))) == 14


def test_only_the_zip_routes_document_429(db):
    """Review item 6: 409 and 429 are documented on the two frames routes only."""
    resp = Client().get('/api/schema/?format=json', HTTP_ACCEPT='application/json')
    assert resp.status_code == 200
    paths = json.loads(resp.content.decode('utf-8'))['paths']
    downloads = {path: item['get']['responses'] for path, item in paths.items()
                 if path.startswith('/api/sequence-submissions/') and '/download-' in path}
    assert len(downloads) == 6
    for path, responses in downloads.items():
        zipped = path.endswith('download-frames/')
        assert ('429' in responses, '409' in responses) == (zipped, zipped), path
