# sethlans_reborn/tests/unit/worker_agent/test_hash_parser.py
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
import logging

# Import the function to be tested
from sethlans_worker_agent.utils.hash_parser import parse_sha256_content_for_file

# --- Dummy Data for Test ---
# This is a snippet of the SHA256 content we expect to parse
DUMMY_SHA256_CONTENT_FOR_TEST = """
a309f559f9d756e333c0eae97254b57cc24df65d3cddd69270044ee8626c216d  blender-4.2.12-linux-x64.tar.xz
277c2618298368d0a80fe4aec89af8e46c441af850a1d34528ad9f7cd6b9b615  blender-4.2.12-macos-arm64.dmg
e7575e7bb12715984f1195fba3537cb890e12355b473e47a8f55e7bab184f509  blender-4.2.12-macos-x64.dmg
4ee5c4da98afb925cf04ae05b53d66c23f2e8db1d805cd747365a8fca00b880a  blender-4.2.12-windows-arm64.zip
d7b77bf3a925722be87e5b5e429b584d7baa3bcc82579afa7952fc1f8c19d2e1  blender-4.2.12-windows-x64.zip
"""


# --- Test Case 1: parse_sha256_content_for_file_success ---

def test_parse_sha256_content_for_file_success():
    """
    Test Case: parse_sha256_content_for_file_success
    Purpose: Verify that parse_sha256_content_for_file successfully extracts the hash
             for a specific filename from a given SHA256 content string.
    Asserts:
        - The correct SHA256 hash is returned.
    """
    content_string = DUMMY_SHA256_CONTENT_FOR_TEST
    expected_filename = "blender-4.2.12-windows-x64.zip"

    file_hash = parse_sha256_content_for_file(content_string, expected_filename)

    # Expected hash for blender-4.2.12-windows-x64.zip from DUMMY_SHA256_CONTENT_FOR_TEST
    expected_hash_value = "d7b77bf3a925722be87e5b5e429b584d7baa3bcc82579afa7952fc1f8c19d2e1"

    assert file_hash == expected_hash_value, \
        f"Expected hash {expected_hash_value}, but got {file_hash}."

    print(f"\n[UNIT TEST] parse_sha256_content_for_file_success passed.")


# --- Test Case 2: parse_sha256_content_for_file_hash_not_found ---

def test_parse_sha256_content_for_file_hash_not_found():
    """
    Test Case: parse_sha256_content_for_file_hash_not_found
    Purpose: Verify that parse_sha256_content_for_file returns None
             if the hash for the expected filename is not present in the content.
    Asserts:
        - The returned hash is None.
    """
    content_string = DUMMY_SHA256_CONTENT_FOR_TEST
    # This filename is NOT in DUMMY_SHA256_CONTENT_FOR_TEST
    expected_filename = "blender-99.99.99-nonexistent-platform.zip"

    file_hash = parse_sha256_content_for_file(content_string, expected_filename)

    assert file_hash is None, "Should return None if hash for filename is not found."

    print(f"\n[UNIT TEST] parse_sha256_content_for_file_hash_not_found passed.")


# --- Test Case 3: parse_sha256_content_for_file_empty_content ---

def test_parse_sha256_content_for_file_empty_content():
    """
    Test Case: parse_sha256_content_for_file_empty_content
    Purpose: Verify that parse_sha256_content_for_file handles empty content string gracefully.
    Asserts:
        - The returned hash is None.
    """
    content_string = ""  # Empty string input
    expected_filename = "any_file.zip"

    file_hash = parse_sha256_content_for_file(content_string, expected_filename)

    assert file_hash is None, "Should return None for empty content string."

    print(f"\n[UNIT TEST] parse_sha256_content_for_file_empty_content passed.")
