# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for the hash_parser utility.

Tests parsing of SHA256 hash files fetched from the Blender download site.
"""
import requests

from sethlans_worker_agent.utils.hash_parser import get_all_hashes_from_url


class TestGetAllHashesFromUrl:

    def test_parses_standard_sha256_file(self, mocker):
        content = (
            "abc123 blender-4.1.1-linux-x64.tar.xz\n"
            "def456 blender-4.1.1-windows-x64.zip\n"
            "789ghi blender-4.1.1-macos-arm64.dmg\n"
        )
        mock_resp = mocker.Mock()
        mock_resp.text = content
        mock_resp.raise_for_status = mocker.Mock()
        mocker.patch('requests.get', return_value=mock_resp)

        result = get_all_hashes_from_url('http://example.com/hash.sha256')
        assert result == {
            'blender-4.1.1-linux-x64.tar.xz': 'abc123',
            'blender-4.1.1-windows-x64.zip': 'def456',
            'blender-4.1.1-macos-arm64.dmg': '789ghi',
        }

    def test_skips_malformed_lines(self, mocker):
        content = (
            "abc123 blender.tar.xz\n"
            "malformed-line-no-space\n"
            "\n"
            "def456 blender.zip\n"
            "too many parts here now\n"
        )
        mock_resp = mocker.Mock()
        mock_resp.text = content
        mock_resp.raise_for_status = mocker.Mock()
        mocker.patch('requests.get', return_value=mock_resp)

        result = get_all_hashes_from_url('http://example.com/h.sha256')
        assert result == {
            'blender.tar.xz': 'abc123',
            'blender.zip': 'def456',
        }

    def test_empty_file_returns_empty_dict(self, mocker):
        mock_resp = mocker.Mock()
        mock_resp.text = ""
        mock_resp.raise_for_status = mocker.Mock()
        mocker.patch('requests.get', return_value=mock_resp)

        result = get_all_hashes_from_url('http://example.com/empty.sha256')
        assert result == {}

    def test_network_error_returns_empty_dict(self, mocker):
        mocker.patch(
            'requests.get',
            side_effect=requests.exceptions.ConnectionError("timeout")
        )
        result = get_all_hashes_from_url('http://unreachable/hash.sha256')
        assert result == {}

    def test_http_error_returns_empty_dict(self, mocker):
        mock_resp = mocker.Mock()
        mock_resp.raise_for_status.side_effect = (
            requests.exceptions.HTTPError("404")
        )
        mocker.patch('requests.get', return_value=mock_resp)

        result = get_all_hashes_from_url('http://example.com/missing.sha256')
        assert result == {}

    def test_handles_leading_trailing_whitespace(self, mocker):
        # strip().split() collapses whitespace, yielding exactly 2 parts
        content = "  abc123   blender.tar.xz  \n"
        mock_resp = mocker.Mock()
        mock_resp.text = content
        mock_resp.raise_for_status = mocker.Mock()
        mocker.patch('requests.get', return_value=mock_resp)

        result = get_all_hashes_from_url('http://example.com/h.sha256')
        assert result == {'blender.tar.xz': 'abc123'}
