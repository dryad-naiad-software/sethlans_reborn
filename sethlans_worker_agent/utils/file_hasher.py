# sethlans_worker_agent/utils/file_hasher.py

# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
#
# Created by Mario Estrella on 07/22/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#

"""
Utility for performing file integrity checks.

This module contains a single function for calculating the SHA256 hash
of a file, which is essential for verifying the integrity of downloaded
Blender archives.
"""

import hashlib
import datetime
import os

import logging
logger = logging.getLogger(__name__)


def calculate_file_sha256(file_path, chunk_size=4096):
    """
    Calculates the SHA256 hash of a file.

    The file is read in chunks to efficiently handle large files without
    consuming excessive memory.

    Args:
        file_path (str): The path to the file to be hashed.
        chunk_size (int): The size of the chunks to read.

    Returns:
        str or None: The hexadecimal SHA256 hash string, or None if an
                     error occurs (e.g., file not found).
    """
    sha256_hash = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(chunk_size), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except FileNotFoundError:
        logger.error(f"File not found for hash calculation: {file_path}")
        return None
    except Exception as e:
        logger.error(f"Failed to calculate hash for {file_path}: {e}")
        return None