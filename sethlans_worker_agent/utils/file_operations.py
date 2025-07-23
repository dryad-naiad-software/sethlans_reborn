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

import requests
import os
import datetime
import zipfile
import shutil

from .file_hasher import calculate_file_sha256  # <-- NEW IMPORT


def download_file(url, destination_path, expected_hash=None):
    """
    Downloads a file from a given URL to a destination path, and optionally verifies its SHA256 hash.
    Args:
        url (str): The URL of the file to download.
        destination_path (str): The local path where the file should be saved.
        expected_hash (str, optional): The expected SHA256 hash for verification. Defaults to None.
    Returns:
        bool: True if download and verification (if requested) are successful, False otherwise.
    """
    print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Downloading {url} to {destination_path}...")
    try:
        with requests.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(destination_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Download complete.")

        if expected_hash:
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Verifying hash of {destination_path}...")
            calculated_hash = calculate_file_sha256(destination_path)
            if calculated_hash and calculated_hash.lower() == expected_hash.lower():
                print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Hash verification SUCCESS!")
                return True
            else:
                print(
                    f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Hash verification FAILED! Expected: {expected_hash}, Calculated: {calculated_hash}. Deleting corrupted file.")
                os.remove(destination_path)  # Delete corrupted file
                return False
        return True  # Download successful, no hash to verify or verification passed

    except requests.exceptions.RequestException as e:
        print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Download failed - {e}")
        if os.path.exists(destination_path):  # Clean up partial download
            os.remove(destination_path)
        return False
    except Exception as e:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] An unexpected error occurred during download: {e}")
        if os.path.exists(destination_path):  # Clean up partial download
            os.remove(destination_path)
        return False


def extract_zip_file(zip_path, extract_to_path):
    """
    Extracts a ZIP file to a specified directory.
    Args:
        zip_path (str): Path to the ZIP file.
        extract_to_path (str): Directory where contents should be extracted.
    Returns:
        tuple: (success: bool, actual_extract_path: str)
    """
    print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Extracting {zip_path} to {extract_to_path}...")
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            root_folder_name = ""
            for name in zip_ref.namelist():
                if '/' in name:
                    root_folder_name = name.split('/')[0]
                    break

            actual_extract_path = os.path.join(extract_to_path, root_folder_name)

            os.makedirs(actual_extract_path, exist_ok=True)
            zip_ref.extractall(extract_to_path)

        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Extraction complete to {actual_extract_path}.")
        return True, actual_extract_path
    except zipfile.BadZipFile:
        print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Invalid ZIP file: {zip_path}")
        return False, None
    except Exception as e:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Failed to extract ZIP file {zip_path}: {e}")
        return False, None