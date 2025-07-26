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

import hashlib
import json
import logging
import os
import requests
import shutil
import platform
import subprocess
import time
from tqdm import tqdm

logger = logging.getLogger(__name__)


# --- JSON Operations ---
def load_json(file_handle):
    """Convenience wrapper for loading JSON data from a file handle."""
    return json.load(file_handle)


def dump_json(data, file_handle):
    """Convenience wrapper for dumping data to a JSON file handle with indentation."""
    json.dump(data, file_handle, indent=4)


# --- Download and Archive Operations ---
def download_file(url, dest_folder):
    """Downloads a file with a progress bar."""
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
    """Verifies the hash of a downloaded file."""
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


# --- MODIFIED: DMG handling function for macOS with diagnostics ---
def handle_dmg_extraction_on_mac(dmg_path, extract_to):
    """
    Mounts a DMG, copies the .app, and unmounts it.
    """
    logger.info(f"Mounting {dmg_path}...")
    # Use a unique mount point to avoid conflicts
    mount_point = os.path.join("/Volumes", f"BlenderMount_{int(time.time())}")

    # The hdiutil command to be executed.
    # We are removing '-quiet' to get more verbose output for debugging.
    hdiutil_command = ["hdiutil", "attach", dmg_path, "-mountpoint", mount_point, "-nobrowse"]

    try:
        # Run the command and capture output
        process = subprocess.run(
            hdiutil_command,
            check=True,
            capture_output=True,  # Capture stdout and stderr
            text=True  # Decode output as text
        )
        logger.debug(f"hdiutil attach stdout:\n{process.stdout}")

        # Find the .app directory inside the mounted volume
        app_dir = next((d for d in os.listdir(mount_point) if d.endswith(".app")), None)
        if not app_dir:
            raise IOError("Could not find .app directory in the mounted DMG.")

        source_app_path = os.path.join(mount_point, app_dir)

        # The final extracted folder name should match the standard format
        dest_folder_name = os.path.basename(dmg_path).replace(".dmg", "")
        final_dest_path = os.path.join(extract_to, dest_folder_name)

        if os.path.exists(final_dest_path):
            shutil.rmtree(final_dest_path)

        logger.info(f"Copying {source_app_path} to {final_dest_path}...")
        shutil.copytree(source_app_path, final_dest_path)

        return final_dest_path

    except subprocess.CalledProcessError as e:
        logger.critical(f"hdiutil attach failed with exit code {e.returncode}.")
        logger.error(f"STDOUT from hdiutil:\n{e.stdout}")
        logger.error(f"STDERR from hdiutil:\n{e.stderr}")
        # Re-raise the exception so the program still fails as expected
        raise e

    finally:
        # Unmount the DMG
        if os.path.exists(mount_point):
            logger.info(f"Unmounting {mount_point}...")
            # Use a separate run call for detach that doesn't capture output unless needed
            subprocess.run(["hdiutil", "detach", mount_point, "-quiet"], check=False)


def extract_archive(archive_path, extract_to):
    """Extracts an archive, handling DMG on macOS and other types elsewhere."""
    # Check for macOS and .dmg file
    if platform.system() == "Darwin" and archive_path.endswith(".dmg"):
        logger.info("macOS .dmg detected, using custom handler.")
        extracted_path = handle_dmg_extraction_on_mac(archive_path, extract_to)
        logger.info(f"DMG processing complete. Extracted to {extracted_path}.")
        return extracted_path
    else:
        # Use shutil for .zip, .tar.xz, etc.
        logger.info(f"Extracting {archive_path} to {extract_to}...")
        shutil.unpack_archive(archive_path, extract_to)

        # Get the name of the extracted directory for logging
        archive_name = os.path.basename(archive_path)
        if archive_name.endswith('.zip'):
            extracted_dir_name = archive_name[:-4]
        elif archive_name.endswith('.tar.xz'):
            extracted_dir_name = archive_name[:-7]
        else:
            extracted_dir_name = archive_name

        full_extracted_path = os.path.join(extract_to, extracted_dir_name)
        logger.info(f"Extraction complete to {full_extracted_path}.")
        return full_extracted_path


def cleanup_archive(archive_path):
    """Deletes the specified file."""
    logger.info(f"Cleaned up temporary download file: {archive_path}")
    os.remove(archive_path)