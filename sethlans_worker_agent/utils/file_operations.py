# sethlans_worker_agent/utils/file_operations.py

# ... (Your existing header) ...

import requests
import os
import datetime
import zipfile
import shutil

from .file_hasher import calculate_file_sha256

import logging  # <-- NEW IMPORT

logger = logging.getLogger(__name__)  # <-- Get a logger for this module


def download_file(url, destination_path, expected_hash=None):
    """
    Downloads a file from a given URL to a destination path, and optionally verifies its SHA256 hash.
    """
    logger.info(f"Downloading {url} to {destination_path}...")  # <-- Changed print to logger.info
    try:
        with requests.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(destination_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        logger.info("Download complete.")  # <-- Changed print to logger.info

        if expected_hash:
            logger.info(f"Verifying hash of {destination_path}...")  # <-- Changed print to logger.info
            calculated_hash = calculate_file_sha256(destination_path)
            if calculated_hash and calculated_hash.lower() == expected_hash.lower():
                logger.info("Hash verification SUCCESS!")  # <-- Changed print to logger.info
                return True
            else:
                logger.error(
                    f"Hash verification FAILED! Expected: {expected_hash}, Calculated: {calculated_hash}. Deleting corrupted file.")  # <-- Changed print to logger.error
                os.remove(destination_path)
                return False
        return True

    except requests.exceptions.RequestException as e:
        logger.error(f"Download failed - {e}")  # <-- Changed print to logger.error
        if os.path.exists(destination_path):
            os.remove(destination_path)
        return False
    except Exception as e:
        logger.error(f"An unexpected error occurred during download: {e}")  # <-- Changed print to logger.error
        if os.path.exists(destination_path):
            os.remove(destination_path)
        return False


def extract_zip_file(zip_path, extract_to_path):
    """Extracts a ZIP file to a specified directory."""
    logger.info(f"Extracting {zip_path} to {extract_to_path}...")  # <-- Changed print to logger.info
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

        logger.info(f"Extraction complete to {actual_extract_path}.")  # <-- Changed print to logger.info
        return True, actual_extract_path
    except zipfile.BadZipFile:
        logger.error(f"Invalid ZIP file: {zip_path}")  # <-- Changed print to logger.error
        return False, None
    except Exception as e:
        logger.error(
            f"An unexpected error occurred during extraction of {zip_path}: {e}")  # <-- Changed print to logger.error
        return False, None