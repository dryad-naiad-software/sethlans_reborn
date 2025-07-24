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
import logging

# Import the functions to be tested
from sethlans_worker_agent.utils.blender_release_parser import (
    collect_blender_version_details,
    get_sha256_hash_for_zip,
    parse_major_version_directories
)
from sethlans_worker_agent import config

logging.basicConfig(level=logging.DEBUG, format='[TEST_LOG] %(asctime)s - %(levelname)s - %(name)s - %(message)s')

# --- Dummy Data for Mocking ---
DUMMY_MAIN_RELEASES_HTML = """
<html>
<body>
<a href="/release/Blender1.0/">Blender1.0/</a>
<a href="/release/Blender2.93/">Blender2.93/</a>
<a href="/release/Blender3.6/">Blender3.6/</a>
<a href="/release/Blender4.0/">Blender4.0/</a>
<a href="/release/Blender4.1/">Blender4.1/</a>
</body>
</html>
"""


# --- Test Cases ---

def test_get_sha256_hash_for_zip_success(mocker):
    """
    Test Case: get_sha256_hash_for_zip_success
    Purpose: Verify the function correctly fetches SHA256 content and passes it
             to the hash parser utility.
    Asserts:
        - requests.get is called with the correct URL.
        - The parse_sha256_content_for_file utility is called with the correct arguments.
        - The final return value is correct.
    """
    # --- Mock Data and Config ---
    mock_sha_url = "http://example.com/blender.sha256"
    mock_zip_filename = "blender-4.2.0-windows-x64.zip"
    mock_hash_content = "d7b7...  blender-4.2.0-windows-x64.zip"
    expected_hash = "d7b7..."

    # --- Mock Function Calls ---
    # 1. Mock the network call
    mock_response = mocker.Mock()
    mock_response.raise_for_status.return_value = None
    mock_response.text = mock_hash_content
    mocker.patch('requests.get', return_value=mock_response)

    # 2. Mock the downstream parser utility that this function uses
    mock_parser = mocker.patch(
        'sethlans_worker_agent.utils.blender_release_parser.parse_sha256_content_for_file',
        return_value=expected_hash
    )

    # --- Run the function under test ---
    result = get_sha256_hash_for_zip(mock_sha_url, mock_zip_filename)

    # --- Assertions ---
    assert result == expected_hash
    requests.get.assert_called_once_with(mock_sha_url, timeout=5)
    mock_parser.assert_called_once_with(mock_hash_content, mock_zip_filename)

    print(f"\n[UNIT TEST] get_sha256_hash_for_zip_success passed.")


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
        full_url = urljoin(config.BLENDER_RELEASES_URL, f"/release/{suffix}")
        assert full_url in major_version_urls, \
            f"Expected URL for {suffix} to be in the list."

    assert urljoin(config.BLENDER_RELEASES_URL, "/release/Blender1.0/") not in major_version_urls, \
        "Blender1.0/ should have been filtered out."
    assert urljoin(config.BLENDER_RELEASES_URL, "/release/Blender2.93/") not in major_version_urls, \
        "Blender2.93/ should have been filtered out."
    assert urljoin(config.BLENDER_RELEASES_URL, "/release/Blender3.6/") not in major_version_urls, \
        "Blender3.6/ should have been filtered out."

    print(f"\n[UNIT TEST] parse_major_version_directories_filters_below_4_x passed.")

# In tests/unit/worker_agent/test_blender_release_parser.py

def test_get_sha256_hash_for_zip_network_failure(mocker):
    """
    Test Case: test_get_sha256_hash_for_zip_network_failure
    Purpose: Verify the function returns None when requests.get fails.
    Asserts:
        - The function returns None.
        - The downstream parser utility is never called.
    """
    # --- Mock Data and Config ---
    mock_sha_url = "http://example.com/blender.sha256"
    mock_zip_filename = "blender-4.2.0-windows-x64.zip"

    # --- Mock Function Calls ---
    # 1. Mock the network call to raise an exception
    mocker.patch(
        'requests.get',
        side_effect=requests.exceptions.RequestException("Network error")
    )

    # 2. Mock the downstream parser to ensure it's not called
    mock_parser = mocker.patch(
        'sethlans_worker_agent.utils.blender_release_parser.parse_sha256_content_for_file'
    )

    # --- Run the function under test ---
    result = get_sha256_hash_for_zip(mock_sha_url, mock_zip_filename)

    # --- Assertions ---
    assert result is None
    requests.get.assert_called_once_with(mock_sha_url, timeout=5)
    mock_parser.assert_not_called()

    print(f"\n[UNIT TEST] get_sha256_hash_for_zip_network_failure passed.")


def test_collect_blender_version_details_success(mocker):
    """
    Test Case: test_collect_blender_version_details_success
    Purpose: Verify the function correctly orchestrates the scraping and data
             collection for a specific Blender version page.
    Asserts:
        - Helper functions are called correctly.
        - The returned list of dictionaries is structured as expected.
    """
    # --- Mock Data and Config ---
    mock_page_url = f"{config.BLENDER_RELEASES_URL}Blender4.2/"
    mock_html = """
    <html><body>
        <a href="blender-4.2.0-windows-x64.zip">blender-4.2.0-windows-x64.zip</a>
        <a href="blender-4.2.0-linux-x64.tar.xz">blender-4.2.0-linux-x64.tar.xz</a>
        <a href="blender-4.2.0.sha256">blender-4.2.0.sha256</a>
        <a href="not-a-blender-file.txt">some other file</a>
    </body></html>
    """
    mock_soup = BeautifulSoup(mock_html, 'html.parser')

    # --- Mock Function Calls ---
    # 1. Mock the page fetcher to return our dummy soup
    mocker.patch(
        'sethlans_worker_agent.utils.blender_release_parser.fetch_page_soup',
        return_value=mock_soup
    )

    # 2. Mock the hash utility to return a consistent dummy hash
    mock_get_hash = mocker.patch(
        'sethlans_worker_agent.utils.blender_release_parser.get_sha256_hash_for_zip',
        return_value="dummy_hash_123"
    )

    # --- Run the function under test ---
    results = collect_blender_version_details(mock_page_url)

    # --- Assertions ---
    # 3. Check the final output
    assert len(results) == 2  # Should find two valid blender files

    win_result = results[0]
    assert win_result['version'] == "4.2.0"
    assert win_result['platform_suffix'] == "windows-x64"
    assert win_result['file_extension'] == 'zip'
    assert win_result['hash'] == "dummy_hash_123"
    assert "mirror.clarkson.edu" in win_result['mirrors'][0]

    linux_result = results[1]
    assert linux_result['version'] == "4.2.0"
    assert linux_result['platform_suffix'] == "linux-x64"
    assert linux_result['file_extension'] == 'tar.xz'

    # 4. Check that helpers were called correctly
    mock_get_hash.assert_any_call(
        f"{mock_page_url}blender-4.2.0.sha256", "blender-4.2.0-windows-x64.zip"
    )
    mock_get_hash.assert_any_call(
        f"{mock_page_url}blender-4.2.0.sha256", "blender-4.2.0-linux-x64.tar.xz"
    )
    assert mock_get_hash.call_count == 2

    print(f"\n[UNIT TEST] test_collect_blender_version_details_success passed.")