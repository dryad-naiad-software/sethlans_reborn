# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
#
# Created by Mario Estrella on 07/23/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# sethlans_worker_agent/utils/hash_parser.py
"""
Utility for parsing hash files from the Blender download site.

This module fetches a `.sha256` file from a URL and parses its contents,
returning a dictionary that maps filenames to their corresponding SHA256 hashes.
This is used to verify the integrity of downloaded Blender archives.
"""

import logging
import requests

logger = logging.getLogger(__name__)


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
        response = requests.get(sha_url, timeout=5)
        response.raise_for_status()
        for line in response.text.splitlines():
            parts = line.strip().split()
            if len(parts) == 2:
                hash_value, filename = parts
                hashes[filename] = hash_value
    except requests.exceptions.RequestException as e:
        logger.warning(f"Could not fetch or parse hash file {sha_url}: {e}")
    return hashes