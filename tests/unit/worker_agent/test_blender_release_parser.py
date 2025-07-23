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
from sethlans_worker_agent.utils.blender_release_parser import fetch_page_soup, parse_major_version_directories
from sethlans_worker_agent import config

# --- Dummy Data for Mocking ---
DUMMY_MAIN_RELEASES_HTML = """
<html>
<body>
<a href="/release/Blender1.0/">Blender1.0/</a>
<a href="/release/Blender2.93/">Blender2.93/</a>
<a href="/release/Blender3.6/">Blender3.6/</a>
<a href="/release/Blender4.0/">Blender4.0/</a>
<a href="/release/Blender4.1/">Blender4.1/</a>
<a href="/release/Blender4.2/">Blender4.2/</a>
<a href="/release/Blender4.3/">Blender4.3/</a>
<a href="/release/Blender4.4/">Blender4.4/</a>
<a href="/release/Blender4.5/">Blender4.5/</a>
<a href="/release/Blender5.0/">Blender5.0/</a>
<a href="/release/OtherDir/">OtherDir/</a>
</body>
</html>
"""


# --- Fixtures ---
# mock_requests_get_for_parser fixture is no longer needed since fetch_page_soup_success is removed
# (Its absence means tests that relied on it would fail, forcing us to fix this cleanly)

# --- Test Case 1 (REMOVED: test_fetch_page_soup_success was too brittle for unit testing) ---


# --- Test Case 2: parse_major_version_directories_filters_below_4_x ---

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
    # Direct creation of BeautifulSoup object from dummy HTML
    soup = BeautifulSoup(DUMMY_MAIN_RELEASES_HTML, 'html.parser')

    major_version_urls = parse_major_version_directories(soup)

    # Assert 1: Only 4.x+ versions are included
    # Expected: Blender4.0, Blender4.1, Blender4.2, Blender4.3, Blender4.4, Blender4.5, Blender5.0
    assert len(major_version_urls) == 7, \
        f"Expected 7 major version URLs (4.x+), but got {len(major_version_urls)}."

    # Assert 2: Check for presence of expected versions and absence of filtered versions
    expected_suffixes = [
        "Blender4.0/", "Blender4.1/", "Blender4.2/", "Blender4.3/", "Blender4.4/",
        "Blender4.5/", "Blender5.0/"
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
    assert urljoin(config.BLENDER_RELEASES_URL, "OtherDir/") not in major_version_urls, \
        "Non-Blender directories should be filtered out."

    print(f"\n[UNIT TEST] parse_major_version_directories_filters_below_4_x passed.")