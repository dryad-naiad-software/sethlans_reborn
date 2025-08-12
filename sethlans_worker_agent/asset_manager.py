# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
#
# Created by Mario Estrella on 7/28/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# sethlans_worker_agent/asset_manager.py
"""
Handles the management of local render assets for the worker agent.

This module is responsible for ensuring that the `.blend` file required for a
render job is present in the local cache. If the file does not exist locally,
it is downloaded from the manager's API endpoint.
"""

import logging
import os
import requests
from urllib.parse import urlparse
from pathlib import Path

from sethlans_worker_agent import config
from sethlans_worker_agent.utils import file_operations

logger = logging.getLogger(__name__)


def ensure_asset_is_available(asset_data):
    """
    Ensures a given asset is available in the local cache, downloading it if necessary.

    The function first checks if the asset file already exists in the local
    `managed_assets` directory. If so, it returns the local path directly.
    Otherwise, it initiates a download from the provided URL, creates the
    necessary directory structure, and saves the file locally.

    Args:
        asset_data (dict): The asset dictionary from the job payload,
                           containing the 'blend_file' URL.

    Returns:
        str: The absolute local path to the cached asset file, or None if it fails.
    """
    if not asset_data or 'blend_file' not in asset_data:
        logger.error("Job data is missing required 'asset' information.")
        return None

    file_url = asset_data['blend_file']
    if not file_url:
        logger.error("Asset data is missing the 'blend_file' URL.")
        return None

    # Parse the URL to get the path component (e.g., /media/assets/2025/07/28/scene.blend)
    parsed_url = urlparse(file_url)
    # Strip the leading slash to make it a relative path
    relative_path = parsed_url.path.lstrip('/')

    local_path = Path(config.MANAGED_ASSETS_DIR) / relative_path

    # Check if the file already exists in our local cache
    if local_path.exists():
        logger.info(f"Asset found in local cache: {local_path}")
        return str(local_path)

    logger.info(f"Asset not found locally. Downloading from {file_url}...")

    # Ensure the target directory exists
    local_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # We can reuse the download_file utility. We'll download it to its final parent directory.
        downloaded_file_path = file_operations.download_file(file_url, str(local_path.parent))

        # download_file returns the full path, which should match our calculated local_path
        if downloaded_file_path == str(local_path):
            logger.info(f"Successfully downloaded asset to {local_path}")
            return str(local_path)
        else:
            # This case is unlikely but good to handle
            logger.error(f"Downloaded file path mismatch. Expected {local_path}, got {downloaded_file_path}")
            # Clean up the wrongly named file
            if os.path.exists(downloaded_file_path):
                os.remove(downloaded_file_path)
            return None

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to download asset from {file_url}: {e}")
        return None
    except Exception as e:
        logger.critical(f"An unexpected error occurred in asset download: {e}", exc_info=True)
        return None