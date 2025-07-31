# sethlans_worker_agent/utils/file_operations.py

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
# Created by Mario Estrella on 07/22/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#

"""
A collection of utility functions for file system and networking tasks.

This module provides platform-aware helpers for downloading files, verifying
their integrity, and handling different types of archives (e.g., ZIP, Tar, DMG).
"""

import hashlib
import json
import logging
import os
import requests
import shutil
import platform
import subprocess
import tarfile
import time
from tqdm import tqdm

logger = logging.getLogger(__name__)


# --- JSON Operations ---
def load_json(file_handle):
    """
    Convenience wrapper for loading JSON data from a file handle.

    Args:
        file_handle (file): An open file handle for a JSON file.

    Returns:
        dict or list: The loaded JSON data.
    """
    return json.load(file_handle)


def dump_json(data, file_handle):
    """
    Convenience wrapper for dumping data to a JSON file handle with indentation.

    Args:
        data (dict or list): The data to serialize to JSON.
        file_handle (file): An open file handle for the destination file.
    """
    json.dump(data, file_handle, indent=4)


# --- Download and Archive Operations ---
def download_file(url, dest_folder):
    """
    Downloads a file from a URL to a local destination with a progress bar.

    Args:
        url (str): The URL of the file to download.
        dest_folder (str): The path to the local directory to save the file.

    Returns:
        str: The full local path to the downloaded file.
    """
    local_filename = url.split('/')[-1]
    download_path = os.path.join(dest_folder, local_filename)

    logger.info(f"Downloading {url} to {download_path}...")
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        total_size = int(r.headers.get('content-length', 0))
        with open(download_path, 'wb') as f, tqdm(
                total=total_size, unit='iB', unit_scale=True, desc=local_filename
        ) as bar:
            for chunk in r.iter_content(chunk_size=8192):
                size = f.write(chunk)
                bar.update(size)
    logger.info("Download complete.")
    return download_path


def verify_hash(file_path, expected_hash, algorithm='sha256'):
    """
    Verifies the hash of a downloaded file against an expected value.

    Args:
        file_path (str): The path to the file to verify.
        expected_hash (str): The known hash value to compare against.
        algorithm (str): The hashing algorithm to use (e.g., 'sha256').

    Returns:
        bool: True if the hashes match, False otherwise.
    """
    logger.info(f"Verifying hash of {file_path}...")
    hasher = hashlib.new(algorithm)
    with open(file_path, 'rb') as f:
        while chunk := f.read(8192):
            hasher.update(chunk)

    calculated_hash = hasher.hexdigest()
    if calculated_hash == expected_hash:
        logger.info("Hash verification SUCCESS!")
        return True
    else:
        logger.error(f"Hash verification FAILED. Expected {expected_hash}, got {calculated_hash}")
        return False


def handle_dmg_extraction_on_mac(dmg_path, extract_to):
    """
    Mounts a macOS `.dmg` file, copies the `.app` bundle, and unmounts it.

    This function uses `hdiutil` for mounting/unmounting and `shutil` for copying.
    It's a platform-specific solution for macOS.

    Args:
        dmg_path (str): The path to the downloaded `.dmg` file.
        extract_to (str): The destination directory for the extracted `.app`.

    Returns:
        str: The path to the directory containing the extracted application.

    Raises:
        subprocess.CalledProcessError: If `hdiutil` fails.
        IOError: If the `.app` bundle cannot be found in the mounted image.
    """
    logger.info(f"Mounting {dmg_path}...")
    mount_point = os.path.join("/Volumes", f"BlenderMount_{int(time.time())}")
    hdiutil_command = ["hdiutil", "attach", dmg_path, "-mountpoint", mount_point, "-nobrowse"]

    try:
        process = subprocess.run(hdiutil_command, check=True, capture_output=True, text=True)
        logger.debug(f"hdiutil attach stdout:\n{process.stdout}")

        app_dir = next((d for d in os.listdir(mount_point) if d.endswith(".app")), None)
        if not app_dir:
            raise IOError("Could not find .app directory in the mounted DMG.")

        source_app_path = os.path.join(mount_point, app_dir)

        # 1. Define the standard installation directory name (e.g., blender-4.5.0-macos-arm64)
        install_dir_name = os.path.basename(dmg_path).replace(".dmg", "")
        # 2. Create the full path for that standard installation directory.
        install_dir_path = os.path.join(extract_to, install_dir_name)
        # 3. Define the final destination for the .app bundle *inside* the install directory.
        final_app_dest_path = os.path.join(install_dir_path, app_dir)

        if os.path.exists(install_dir_path):
            shutil.rmtree(install_dir_path)

        logger.info(f"Copying {source_app_path} to {final_app_dest_path}...")
        # 4. Copy the .app bundle to its correct final destination.
        shutil.copytree(source_app_path, final_app_dest_path)

        return install_dir_path

    except subprocess.CalledProcessError as e:
        logger.critical(f"hdiutil attach failed with exit code {e.returncode}.")
        logger.error(f"STDOUT from hdiutil:\n{e.stdout}")
        logger.error(f"STDERR from hdiutil:\n{e.stderr}")
        raise e

    finally:
        if os.path.exists(mount_point):
            logger.info(f"Unmounting {mount_point}...")
            subprocess.run(["hdiutil", "detach", mount_point, "-quiet"], check=False)


def extract_archive(archive_path, extract_to):
    """
    Extracts an archive to a specified directory, handling different formats.

    This function acts as a dispatcher, using a platform-specific handler for
    `.dmg` files on macOS, and the `tarfile` module with a secure filter for
    `.tar.xz` archives. Other formats are handled by `shutil.unpack_archive`.

    Args:
        archive_path (str): The full path to the archive file.
        extract_to (str): The destination directory for the extracted contents.

    Returns:
        str: The full path to the top-level directory of the extracted contents.
    """
    archive_name = os.path.basename(archive_path)

    if platform.system() == "Darwin" and archive_path.endswith(".dmg"):
        logger.info("macOS .dmg detected, using custom handler.")
        extracted_path = handle_dmg_extraction_on_mac(archive_path, extract_to)
        logger.info(f"DMG processing complete. Extracted to {extracted_path}.")
        return extracted_path

    elif archive_path.endswith(".tar.xz"):
        logger.info(f"Extracting {archive_path} to {extract_to} using tarfile with 'data' filter...")
        with tarfile.open(archive_path, 'r:xz') as tar:
            tar.extractall(path=extract_to, filter='data')
        extracted_dir_name = archive_name[:-7]
    else:
        # For .zip and other formats, shutil is still fine.
        logger.info(f"Extracting {archive_path} to {extract_to} using shutil...")
        shutil.unpack_archive(archive_path, extract_to)
        if archive_name.endswith('.zip'):
            extracted_dir_name = archive_name[:-4]
        else:
            extracted_dir_name = archive_name

    full_extracted_path = os.path.join(extract_to, extracted_dir_name)
    logger.info(f"Extraction complete to {full_extracted_path}.")
    return full_extracted_path


def cleanup_archive(archive_path):
    """
    Deletes the specified file from the filesystem.

    Args:
        archive_path (str): The path to the file to delete.
    """
    logger.info(f"Cleaned up temporary download file: {archive_path}")
    os.remove(archive_path)