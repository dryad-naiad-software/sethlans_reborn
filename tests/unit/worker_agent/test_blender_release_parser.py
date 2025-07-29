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
from unittest.mock import MagicMock, call

# Import the module to be tested and its dependency
from sethlans_worker_agent.utils import blender_release_parser
from sethlans_worker_agent.utils import hash_parser

# --- Mock HTML Data ---
MOCK_MAIN_PAGE_HTML_MULTI_PATCH = """
<html><body>
    <a href="Blender4.1/">Blender4.1/</a>
    <a href="Blender4.2/">Blender4.2/</a>
</body></html>
"""

MOCK_4_1_PAGE_HTML = """
<html><body>
    <a href="blender-4.1.0-windows-x64.zip">blender-4.1.0-windows-x64.zip</a>
    <a href="blender-4.1.1-windows-x64.zip">blender-4.1.1-windows-x64.zip</a>
    <a href="blender-4.1.1.sha256">blender-4.1.1.sha256</a>
</body></html>
"""

MOCK_4_2_PAGE_HTML = """
<html><body>
    <a href="blender-4.2.0-windows-x64.zip">blender-4.2.0-windows-x64.zip</a>
    <a href="blender-4.2.0.sha256">blender-4.2.0.sha256</a>
</body></html>
"""

def test_get_blender_releases_filters_for_latest_patch(mocker):
    """
    Tests that the scraper correctly identifies multiple patch versions
    and filters the final result to include only the latest one for each series.
    """
    # Arrange
    mock_main_response = MagicMock(content=MOCK_MAIN_PAGE_HTML_MULTI_PATCH)
    mock_4_1_response = MagicMock(content=MOCK_4_1_PAGE_HTML)
    mock_4_2_response = MagicMock(content=MOCK_4_2_PAGE_HTML)

    def get_side_effect(url, timeout):
        if url.endswith('/release/'):
            return mock_main_response
        if url.endswith('/Blender4.1/'):
            return mock_4_1_response
        if url.endswith('/Blender4.2/'):
            return mock_4_2_response
        return MagicMock() # Default mock for any other calls

    mocker.patch('requests.get', side_effect=get_side_effect)
    mocker.patch.object(
        hash_parser,
        'get_all_hashes_from_url',
        return_value={
            "blender-4.1.1-windows-x64.zip": "hash411",
            "blender-4.2.0-windows-x64.zip": "hash420"
        }
    )

    # Act
    releases = blender_release_parser.get_blender_releases()

    # Assert
    assert "4.1.0" not in releases  # Should be filtered out
    assert "4.1.1" in releases      # Should be the latest for 4.1
    assert "4.2.0" in releases      # Should be the latest for 4.2
    assert len(releases) == 2

    release_411 = releases["4.1.1"]["windows-x64"]
    assert "blender-4.1.1-windows-x64.zip" in release_411["url"]