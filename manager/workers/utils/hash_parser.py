# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Utility for parsing hash files from the Blender download site.

This module fetches a `.sha256` file from a URL and parses its contents,
returning a dictionary that maps filenames to their corresponding SHA256 hashes.
This is used to verify the integrity of downloaded Blender archives.

NOTE: This file is duplicated in worker/sethlans_worker_agent/utils/hash_parser.py.
Keep both in sync.
"""

import logging
import requests

logger = logging.getLogger(__name__)

# Explicit (connect, read) timeout tuple — see the rationale in
# ``blender_release_parser.HTTP_TIMEOUT`` (issue #113). Duplicated here
# rather than imported to keep this module import-cycle-free.
HTTP_TIMEOUT = (5, 15)


def get_all_hashes_from_url(sha_url):
    """
    Fetches a `.sha256` file and returns a dictionary of all hashes.

    The `.sha256` file is expected to have a format of `hash filename` on each line.

    Args:
        sha_url (str): The URL of the `.sha256` file.

    Returns:
        dict: A dictionary where keys are filenames and values are the SHA256 hashes.
    """
    hashes = {}
    try:
        response = requests.get(sha_url, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        for line in response.text.splitlines():
            parts = line.strip().split()
            if len(parts) == 2:
                hash_value, filename = parts
                hashes[filename] = hash_value
    except requests.exceptions.RequestException as e:
        logger.warning(f"Could not fetch or parse hash file {sha_url}: {e}")
    return hashes
