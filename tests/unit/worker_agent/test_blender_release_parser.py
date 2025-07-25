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
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
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

# Import the module to be tested and its dependency
from sethlans_worker_agent.utils import blender_release_parser
from sethlans_worker_agent.utils import hash_parser

# --- Mock HTML Data ---
MOCK_MAIN_PAGE_HTML = """
<html><body>
    <a href="Blender3.6/">Blender3.6/</a>
    <a href="Blender4.1/">Blender4.1/</a>
</body></html>
"""

MOCK_VERSION_PAGE_HTML = """
<html><body>
    <a href="blender-4.1.1-windows-x64.zip">blender-4.1.1-windows-x64.zip</a>
    <a href="blender-4.1.1.sha256">blender-4.1.1.sha256</a>
</body></html>
"""


def test_get_blender_releases_success(mocker):
    """
    Tests the full successful workflow of scraping the main and version pages.
    """
    # Arrange
    # Configure the .content attribute, which is what BeautifulSoup uses.
    mock_main_response = MagicMock()
    mock_main_response.content = MOCK_MAIN_PAGE_HTML

    mock_version_response = MagicMock()
    mock_version_response.content = MOCK_VERSION_PAGE_HTML

    mocker.patch('requests.get', side_effect=[mock_main_response, mock_version_response])

    # Mock the dependency on hash_parser
    mocker.patch.object(
        hash_parser,
        'get_all_hashes_from_url',
        return_value={"blender-4.1.1-windows-x64.zip": "hash123"}
    )

    # Act
    releases = blender_release_parser.get_blender_releases()

    # Assert
    assert "3.6.0" not in releases
    assert "4.1.1" in releases
    win_release = releases["4.1.1"]["windows-x64"]
    assert "blender-4.1.1-windows-x64.zip" in win_release["url"]
    assert win_release["sha256"] == "hash123"
    assert requests.get.call_count == 2


def test_get_blender_releases_network_failure(mocker):
    """
    Tests that the function returns an empty dictionary if a network error occurs.
    """
    # Arrange: Mock requests.get to raise an exception
    mocker.patch('requests.get', side_effect=requests.exceptions.RequestException)

    # Act
    releases = blender_release_parser.get_blender_releases()

    # Assert: The function should catch the exception and return an empty dict
    assert releases == {}