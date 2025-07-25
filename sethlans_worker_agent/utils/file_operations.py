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
# sethlans_worker_agent/utils/file_operations.py

import hashlib
import json
import logging
import os
import requests
import shutil
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


def extract_archive(archive_path, extract_to):
    """Extracts a .zip or .tar.xz archive."""
    logger.info(f"Extracting {archive_path} to {extract_to}...")
    shutil.unpack_archive(archive_path, extract_to)

    # Get the name of the extracted directory
    archive_name = os.path.basename(archive_path)
    if archive_name.endswith('.zip'):
        extracted_dir_name = archive_name[:-4]
    elif archive_name.endswith('.tar.xz'):
        extracted_dir_name = archive_name[:-7]
    else:
        extracted_dir_name = archive_name  # Fallback

    full_extracted_path = os.path.join(extract_to, extracted_dir_name)
    logger.info(f"Extraction complete to {full_extracted_path}.")
    return full_extracted_path


def cleanup_archive(archive_path):
    """Deletes the specified file."""
    logger.info(f"Cleaned up temporary download file: {archive_path}")
    os.remove(archive_path)