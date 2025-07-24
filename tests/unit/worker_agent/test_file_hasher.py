# sethlans_reborn/tests/unit/worker_agent/test_file_hasher.py
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
import os
import tempfile
from sethlans_worker_agent.utils.file_hasher import calculate_file_sha256


def test_calculate_file_sha256_basic():
    """
    Tests SHA256 hash calculation for a basic text file.
    The file is written in binary mode to ensure consistent hashing across OS.
    """
    # Create a temporary file with known content (in bytes)
    with tempfile.NamedTemporaryFile(mode='wb+', delete=False) as tmp_file:  # Changed mode to 'wb+'
        tmp_file.write(b"Hello, Sethlans Reborn!")  # Write bytes
        tmp_file_path = tmp_file.name

    try:
        calculated_hash = calculate_file_sha256(tmp_file_path)

        # Corrected expected hash for b"Hello, Sethlans Reborn!"
        expected_hash = "1d68f2316c6a7951f74bd2a3f9c3aab5c2c31b8ab2ba6ac7a2efe08986184e97"

        assert calculated_hash == expected_hash
        print(f"\n[UNIT TEST] SHA256 calculation basic test passed for: {tmp_file_path}")
        print(f"  Calculated: {calculated_hash}")
        print(f"  Expected:   {expected_hash}")
    finally:
        os.remove(tmp_file_path)


def test_calculate_file_sha256_empty_file():
    """
    Tests SHA256 hash calculation for an empty file.
    """
    with tempfile.NamedTemporaryFile(mode='wb+', delete=False) as tmp_file:  # Changed mode to 'wb+'
        tmp_file_path = tmp_file.name

    try:
        calculated_hash = calculate_file_sha256(tmp_file_path)
        # Expected hash for an empty byte string
        expected_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert calculated_hash == expected_hash
        print(f"\n[UNIT TEST] SHA256 calculation empty file test passed for: {tmp_file_path}")
    finally:
        os.remove(tmp_file_path)


def test_calculate_file_sha256_non_existent_file():
    """
    Tests SHA256 hash calculation for a non-existent file (should return None).
    """
    non_existent_path = "non_existent_file_12345.txt"
    calculated_hash = calculate_file_sha256(non_existent_path)
    assert calculated_hash is None
    print(f"\n[UNIT TEST] SHA256 calculation non-existent file test passed.")
