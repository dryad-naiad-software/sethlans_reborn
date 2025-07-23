# sethlans_worker_agent/utils/file_hasher.py

# ... (Your existing header) ...

import hashlib
import datetime
import os

import logging # <-- NEW IMPORT
logger = logging.getLogger(__name__) # <-- Get a logger for this module


def calculate_file_sha256(file_path, chunk_size=4096):
    """Calculates the SHA256 hash of a file."""
    sha256_hash = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(chunk_size), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except FileNotFoundError:
        logger.error(f"File not found for hash calculation: {file_path}") # <-- Changed print to logger.error
        return None
    except Exception as e:
        logger.error(f"Failed to calculate hash for {file_path}: {e}") # <-- Changed print to logger.error
        return None