# sethlans_worker_agent/utils/hash_parser.py
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

import re
import logging
import datetime  # For logging timestamps

logger = logging.getLogger(__name__)


def parse_sha256_content_for_file(sha256_content_string, expected_filename):
    """
    Parses a string containing SHA256 hash lines to find the hash for a specific filename.
    Assumes format: HASH_VALUE  FILENAME (two or more spaces between hash and filename).
    Args:
        sha256_content_string (str): The entire content of the .sha256 file as a string.
        expected_filename (str): The specific filename (e.g., "blender-4.2.12-windows-x64.zip")
                                 whose hash is to be extracted.
    Returns:
        str: The extracted SHA256 hash (lowercase), or None if not found.
    """
    if not sha256_content_string:
        logger.debug(f"SHA256 content string is empty for filename '{expected_filename}'.")
        return None

    for line in sha256_content_string.splitlines():
        # Look for 64 hex chars, followed by two or more whitespace chars, then the escaped filename
        # Using re.match ensures it's at the beginning of the line
        hash_line_match = re.match(rf'([a-f0-9]{{64}})\s{{2,}}{re.escape(expected_filename)}', line)
        if hash_line_match:
            file_hash = hash_line_match.group(1).lower()
            logger.debug(f"Found hash '{file_hash}' for '{expected_filename}' in line: '{line}'.")
            return file_hash

    logger.debug(f"Hash for '{expected_filename}' not found in provided SHA256 content.")
    return None