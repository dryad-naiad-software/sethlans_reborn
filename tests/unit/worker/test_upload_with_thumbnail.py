# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for the thumbnail upload in the worker's api_handler.
"""


class TestUploadWithThumbnail:

    def test_upload_includes_thumbnail(self, mocker, tmp_path):
        mocker.patch(
            'sethlans_worker_agent.config.MANAGER_API_URL',
            'https://localhost:8080/api/'
        )
        mocker.patch(
            'sethlans_worker_agent.api_auth.get_auth_headers',
            return_value={'Authorization': 'Token test'}
        )
        mocker.patch(
            'sethlans_worker_agent.api_auth.handle_auth_response',
            return_value=False
        )

        out_file = tmp_path / 'render.exr'
        out_file.write_bytes(b'EXR data')
        thumb_file = tmp_path / 'thumb.png'
        thumb_file.write_bytes(b'PNG data')

        mock_resp = mocker.Mock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = mocker.Mock()

        captured_files = {}

        def capture_retry(method, url, **kw):
            files = kw.get('files')
            if files:
                for key, val in files.items():
                    captured_files[key] = val[0]  # filename
            return mock_resp

        mocker.patch(
            'sethlans_worker_agent.api_handler._retry_request',
            side_effect=capture_retry,
        )

        from sethlans_worker_agent.api_handler import (
            upload_render_output,
        )
        result = upload_render_output(
            1, str(out_file), thumbnail_path=str(thumb_file)
        )
        assert result is True
        assert 'output_file' in captured_files
        assert 'thumbnail' in captured_files
        assert captured_files['thumbnail'] == 'thumb.png'

    def test_upload_without_thumbnail(self, mocker, tmp_path):
        mocker.patch(
            'sethlans_worker_agent.config.MANAGER_API_URL',
            'https://localhost:8080/api/'
        )
        mocker.patch(
            'sethlans_worker_agent.api_auth.get_auth_headers',
            return_value={'Authorization': 'Token test'}
        )
        mocker.patch(
            'sethlans_worker_agent.api_auth.handle_auth_response',
            return_value=False
        )

        out_file = tmp_path / 'render.png'
        out_file.write_bytes(b'PNG data')

        mock_resp = mocker.Mock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = mocker.Mock()

        captured_files = {}

        def capture_retry(method, url, **kw):
            files = kw.get('files')
            if files:
                for key in files:
                    captured_files[key] = True
            return mock_resp

        mocker.patch(
            'sethlans_worker_agent.api_handler._retry_request',
            side_effect=capture_retry,
        )

        from sethlans_worker_agent.api_handler import (
            upload_render_output,
        )
        result = upload_render_output(1, str(out_file))
        assert result is True
        assert 'output_file' in captured_files
        assert 'thumbnail' not in captured_files

    def test_thumbnail_ioerror_still_uploads(self, mocker, tmp_path):
        """IOError on thumbnail should not prevent output upload."""
        mocker.patch(
            'sethlans_worker_agent.config.MANAGER_API_URL',
            'https://localhost:8080/api/'
        )
        mocker.patch(
            'sethlans_worker_agent.api_auth.get_auth_headers',
            return_value={'Authorization': 'Token test'}
        )
        mocker.patch(
            'sethlans_worker_agent.api_auth.handle_auth_response',
            return_value=False
        )

        out_file = tmp_path / 'render.exr'
        out_file.write_bytes(b'EXR data')
        # Thumbnail path that does not exist
        thumb_path = str(tmp_path / 'nonexistent_thumb.png')

        mock_resp = mocker.Mock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = mocker.Mock()

        captured_files = {}

        def capture_retry(method, url, **kw):
            files = kw.get('files')
            if files:
                for key in files:
                    captured_files[key] = True
            return mock_resp

        mocker.patch(
            'sethlans_worker_agent.api_handler._retry_request',
            side_effect=capture_retry,
        )

        from sethlans_worker_agent.api_handler import (
            upload_render_output,
        )
        result = upload_render_output(
            1, str(out_file), thumbnail_path=thumb_path
        )
        assert result is True
        assert 'output_file' in captured_files
        # Thumbnail should not be included since file doesn't exist
        assert 'thumbnail' not in captured_files

    def test_mime_type_is_octet_stream(self, mocker, tmp_path):
        """Output file MIME type should be application/octet-stream."""
        mocker.patch(
            'sethlans_worker_agent.config.MANAGER_API_URL',
            'https://localhost:8080/api/'
        )
        mocker.patch(
            'sethlans_worker_agent.api_auth.get_auth_headers',
            return_value={'Authorization': 'Token test'}
        )
        mocker.patch(
            'sethlans_worker_agent.api_auth.handle_auth_response',
            return_value=False
        )

        out_file = tmp_path / 'render.exr'
        out_file.write_bytes(b'EXR data')

        mock_resp = mocker.Mock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = mocker.Mock()

        captured_mime = {}

        def capture_retry(method, url, **kw):
            files = kw.get('files')
            if files:
                for key, val in files.items():
                    captured_mime[key] = val[2]  # mime type
            return mock_resp

        mocker.patch(
            'sethlans_worker_agent.api_handler._retry_request',
            side_effect=capture_retry,
        )

        from sethlans_worker_agent.api_handler import (
            upload_render_output,
        )
        upload_render_output(1, str(out_file))
        assert captured_mime['output_file'] == 'application/octet-stream'
