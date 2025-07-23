# tests/unit/worker_agent/test_blender_release_parser.py
#
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#
#
# Created by Mario Estrella on 07/23/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#

import pytest
import requests
from unittest.mock import MagicMock
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# Import the functions to be tested
from sethlans_worker_agent.utils.blender_release_parser import fetch_page_soup, parse_major_version_directories, \
    get_sha256_hash_for_zip
from sethlans_worker_agent import config

import logging

logging.basicConfig(level=logging.DEBUG, format='[TEST_LOG] %(asctime)s - %(levelname)s - %(name)s - %(message)s')

# --- Dummy Data for Mocking ---
DUMMY_MAIN_RELEASES_HTML = """
<html>
<body>
<a href="/release/Blender1.0/">Blender1.0/</a>
<a href="/release/Blender2.93/">Blender2.93/</a>
<a href="/release/Blender3.6/">Bl Blender3.6/</a>
<a href="/release/Blender4.0/">Blender4.0/</a>
<a href="/release/Blender4.1/">Blender4.1/</a>
</body>
</html>
"""

# NEW: Dummy SHA256 content for testing get_sha256_hash_for_zip
DUMMY_SHA256_CONTENT_4_2_12 = """
a309f559f9d756e333c0eae97254b57cc24df65d3cddd69270044ee8626c216d  blender-4.2.12-linux-x64.tar.xz
277c2618298368d0a80fe4aec89af8e46c441af850a1d34528ad9f7cd6b9b615  blender-4.2.12-macos-arm64.dmg
e7575e7bb12715984f1195fba3537cb890e12355b473e47a8f55e7bab184f509  blender-4.2.12-macos-x64.dmg
4ee5c4da98afb925cf04ae05b53d66c23f2e8db1d805cd747365a8fca00b880a  blender-4.2.12-windows-arm64.zip
d7b77bf3a925722be87e5b5e429b584d7baa3bcc82579afa7952fc1f8c19d2e1  blender-4.2.12-windows-x64.zip
"""


# --- Fixtures ---

@pytest.fixture
def mock_requests_get_for_parser(mocker):
    """
    Mocks requests.get to return a mock response that supports the context manager protocol
    and correctly provides .text and .content as simple strings/bytes.
    """

    def _mock_get(url, *args, **kwargs):
        # Create a basic mock for the response object
        mock_response = mocker.Mock()  # <-- REMOVED spec=requests.Response
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None

        # Assign text/content directly as plain strings/bytes
        if url == config.BLENDER_RELEASES_URL:
            mock_response.text = DUMMY_MAIN_RELEASES_HTML
            mock_response.content = DUMMY_MAIN_RELEASES_HTML.encode('utf-8')
        elif "blender-4.2.12.sha256" in url:
            mock_response.text = DUMMY_SHA256_CONTENT_4_2_12
            mock_response.content = DUMMY_SHA256_CONTENT_4_2_12.encode('utf-8')
        else:
            mock_response.status_code = 404
            mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("404 Not Found")
            mock_response.text = ""
            mock_response.content = b""

        # Configure the mock to act as a context manager
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=None)
        return mock_response

    mocker.patch('requests.get', side_effect=_mock_get)
    return mocker.patch('requests.get')


# --- Test Case 1: parse_major_version_directories_filters_below_4_x ---

def test_parse_major_version_directories_filters_below_4_x():
    """
    Test Case: parse_major_version_directories_filters_below_4_x
    Purpose: Verify that parse_major_version_directories correctly filters out
             Blender major versions below 4.x from the main releases HTML.
    Asserts:
        - Only URLs for Blender 4.x+ directories are returned.
        - The count of returned URLs is correct.
        - Specific old versions are NOT in the results.
    """
    soup = BeautifulSoup(DUMMY_MAIN_RELEASES_HTML, 'html.parser')

    major_version_urls = parse_major_version_directories(soup)

    assert len(major_version_urls) == 2, \
        f"Expected 2 major version URLs (4.x+), but got {len(major_version_urls)}."

    expected_suffixes = [
        "Blender4.0/", "Blender4.1/"
    ]
    for suffix in expected_suffixes:
        assert urljoin(config.BLENDER_RELEASES_URL, suffix) in major_version_urls, \
            f"Expected URL for {suffix} to be in the list."

    assert urljoin(config.BLENDER_RELEASES_URL, "Blender1.0/") not in major_version_urls, \
        "Blender1.0/ should have been filtered out."
    assert urljoin(config.BLENDER_RELEASES_URL, "Blender2.93/") not in major_version_urls, \
        "Blender2.93/ should have been filtered out."
    assert urljoin(config.BLENDER_RELEASES_URL, "Blender3.6/") not in major_version_urls, \
        "Blender3.6/ should have been filtered out."

    print(f"\n[UNIT TEST] parse_major_version_directories_filters_below_4_x passed.")


# --- Test Case 3: get_sha256_hash_for_zip_success ---

def test_get_sha256_hash_for_zip_success(mock_requests_get_for_parser):
    """
    Test Case: get_sha256_hash_for_zip_success
    Purpose: Verify successful fetching of a .sha256 file and extraction of the correct hash
             for a specific Windows x64 zip file.
    Asserts:
        - The correct SHA256 hash is returned.
        - requests.get was called exactly once with the .sha256 URL.
    """
    sha256_url = urljoin(config.BLENDER_RELEASES_URL, "Blender4.2/blender-4.2.12.sha256")
    expected_zip_filename = "blender-4.2.12-windows-x64.zip"

    file_hash = get_sha256_hash_for_zip(sha256_url, expected_zip_filename)

    # Expected hash for blender-4.2.12-windows-x64.zip from DUMMY_SHA256_CONTENT_4_2_12
    expected_hash_value = "d7b77bf3a925722be87e5b5e429b584d7baa3bcc82579afa7952fc1f8c19d2e1"

    assert file_hash == expected_hash_value, \
        f"Expected hash {expected_hash_value}, but got {file_hash}."

    # Ensure requests.get was called for the SHA256 file
    mock_requests_get_for_parser.assert_called_once_with(sha256_url, timeout=5), \
        "requests.get should have been called exactly once with the SHA256 URL and timeout."

    print(f"\n[UNIT TEST] test_get_sha256_hash_for_zip_success passed.")