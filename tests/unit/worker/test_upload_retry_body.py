# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Issue #230: a retried Blender output upload must send the whole file again.

The fake session encodes ``files`` exactly as requests does
(``RequestEncodingMixin._encode_files``), which reads a file object to EOF.
A second attempt that reused a consumed file object would therefore produce
a body without the file's bytes.
"""
import pytest
import requests
from requests.models import RequestEncodingMixin

from sethlans_worker_agent import api_handler, api_upload, config

OUTPUT_BYTES = b'EXR-header' + bytes(range(256)) * 64 + b'-tail'
THUMB_BYTES = b'\x89PNG\r\n\x1a\n' + b'thumbnail-pixels' * 32


class Response:
    def __init__(self, status):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(str(self.status_code))


class EncodingSession:
    """Records the multipart body each attempt would put on the wire."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.bodies = []

    def request(self, method, url, **kwargs):
        body, _content_type = RequestEncodingMixin._encode_files(kwargs['files'], {})
        self.bodies.append(body)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def install_session(monkeypatch):
    monkeypatch.setattr(config, 'MANAGER_API_URL', 'https://m/api/')
    monkeypatch.setattr(api_upload, 'get_auth_headers', lambda: {'Authorization': 'Token t'})
    monkeypatch.setattr(api_upload, 'handle_auth_response', lambda response: response.status_code == 401)
    monkeypatch.setattr(api_handler.time, 'sleep', lambda seconds: None)

    def install(*outcomes):
        session = EncodingSession(*outcomes)
        monkeypatch.setattr(api_handler, 'get_session', lambda: session)
        return session
    return install


@pytest.fixture
def output_file(tmp_path):
    path = tmp_path / 'frame_0001.exr'
    path.write_bytes(OUTPUT_BYTES)
    return path


@pytest.fixture
def thumb_file(tmp_path):
    path = tmp_path / 'thumb.png'
    path.write_bytes(THUMB_BYTES)
    return path


class TestRetriedUploadSendsFullFile:

    def test_500_then_201_resends_output_and_thumbnail(self, install_session, output_file, thumb_file):
        session = install_session(Response(500), Response(201))
        assert api_upload.upload_render_output(4, str(output_file), str(thumb_file)) is True
        assert len(session.bodies) == 2
        for body in session.bodies:
            assert OUTPUT_BYTES in body
            assert THUMB_BYTES in body

    def test_500_then_201_without_thumbnail(self, install_session, output_file):
        session = install_session(Response(500), Response(201))
        assert api_upload.upload_render_output(4, str(output_file)) is True
        assert len(session.bodies) == 2
        for body in session.bodies:
            assert OUTPUT_BYTES in body
            assert b'name="thumbnail"' not in body

    def test_timeout_then_success_resends_output_and_thumbnail(self, install_session, output_file, thumb_file):
        session = install_session(requests.exceptions.Timeout('slow'), Response(201))
        assert api_upload.upload_render_output(4, str(output_file), str(thumb_file)) is True
        assert len(session.bodies) == 2
        for body in session.bodies:
            assert OUTPUT_BYTES in body
            assert THUMB_BYTES in body

    def test_every_attempt_failing_returns_false(self, install_session, output_file):
        session = install_session(*[requests.exceptions.ConnectionError('down')] * api_handler.MAX_RETRIES)
        assert api_upload.upload_render_output(4, str(output_file)) is False
        assert len(session.bodies) == api_handler.MAX_RETRIES
        assert all(OUTPUT_BYTES in body for body in session.bodies)


class TestUploadBehaviourKept:

    def test_401_retains_the_output_without_retrying(self, install_session, output_file, monkeypatch):
        retained = []
        monkeypatch.setattr(api_upload, 'retain_failed_upload',
                            lambda job_id, path: retained.append((job_id, path)))
        session = install_session(Response(401))
        assert api_upload.upload_render_output(4, str(output_file)) is False
        assert retained == [(4, str(output_file))]
        assert len(session.bodies) == 1

    def test_unreadable_thumbnail_only_warns(self, install_session, output_file, tmp_path, caplog):
        unreadable = tmp_path / 'thumb_dir.png'
        unreadable.mkdir()  # exists, but open() raises an OSError on every platform
        session = install_session(Response(201))
        assert api_upload.upload_render_output(4, str(output_file), str(unreadable)) is True
        assert OUTPUT_BYTES in session.bodies[0]
        assert b'name="thumbnail"' not in session.bodies[0]
        assert 'Uploading without thumbnail' in caplog.text

    def test_missing_output_is_not_uploaded(self, install_session, tmp_path):
        session = install_session()
        assert api_upload.upload_render_output(4, str(tmp_path / 'gone.exr')) is False
        assert session.bodies == []
