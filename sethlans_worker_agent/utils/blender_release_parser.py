# sethlans_worker_agent/utils/blender_release_parser.py

# ... (Your existing header) ...

import requests
import re
import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from sethlans_worker_agent import config

import logging  # <-- NEW IMPORT

logger = logging.getLogger(__name__)  # <-- Get a logger for this module


# --- Blender Release Parsing Functions ---
def fetch_page_soup(url, timeout=10):
    """Helper to fetch a URL and return a BeautifulSoup object."""
    try:
        logger.debug(f"Fetching page: {url}")  # <-- Changed print to logger.debug
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        return BeautifulSoup(response.text, 'html.parser')
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch URL {url}: {e}")  # <-- Changed print to logger.error
        return None


def parse_major_version_directories(soup):
    """Parses the main releases page soup for Blender major version directory URLs (4.x+ only)."""
    major_version_dir_urls = []
    for link in soup.find_all('a', href=True):
        href = link['href']
        major_minor_dir_match = re.match(r'Blender(\d+\.\d+(?:\.\d+)?)/$', href)
        if major_minor_dir_match:
            version_prefix = major_minor_dir_match.group(1)
            major_version_str = version_prefix.split('.')[0]
            try:
                major_version_num = int(major_version_str)
                if major_version_num < 4:
                    logger.debug(
                        f"Skipping Blender major version < 4: {version_prefix}")  # <-- Changed print to logger.debug
                    continue
            except ValueError:
                logger.warning(
                    f"Could not parse major version from directory '{href}'. Skipping.")  # <-- Changed print to logger.warning
                continue
            major_version_dir_urls.append(urljoin(config.BLENDER_RELEASES_URL, href))
    return major_version_dir_urls


def get_sha256_hash_for_zip(sha256_url, expected_zip_filename):
    """Fetches a .sha256 file and extracts the hash for a specific zip filename."""
    file_hash = None
    try:
        hash_response = requests.get(sha256_url, timeout=5)
        hash_response.raise_for_status()
        for line in hash_response.text.splitlines():
            if expected_zip_filename in line:
                hash_line_match = re.match(r'([a-f0-9]{64})\s+', line)
                if hash_line_match:
                    file_hash = hash_line_match.group(1).lower()
                    logger.debug(
                        f"  Found hash for {expected_zip_filename}: {file_hash}")  # <-- Changed print to logger.debug
                    break
        if not file_hash:
            logger.warning(
                f"Hash for {expected_zip_filename} not found in {sha256_url}.")  # <-- Changed print to logger.warning
    except requests.exceptions.RequestException as req_e:
        logger.warning(
            f"Failed to fetch SHA256 for {expected_zip_filename} from {sha256_url} ({req_e})")  # <-- Changed print to logger.warning
    return file_hash


def collect_blender_version_details(major_version_dir_url_blender_org):
    """
    Fetches a major version directory page, parses it for ALL relevant platform/architecture zip details,
    and constructs primary/mirror URLs and fetches hashes.
    Returns a list of dictionaries, one for each platform-specific download found.
    """
    all_platform_versions_found_in_dir = []
    dir_soup = fetch_page_soup(major_version_dir_url_blender_org)
    if not dir_soup:
        return []

    logger.debug(f"  Parsing details from: {major_version_dir_url_blender_org}")  # <-- Changed print to logger.debug
    for file_link in dir_soup.find_all('a', href=True):
        file_href = file_link['href']
        blender_file_match = re.match(r'blender-(\d+\.\d+\.\d+)-(.+)\.(zip|tar\.xz|dmg)$', file_href)
        if blender_file_match:
            full_version = blender_file_match.group(1)
            platform_suffix = blender_file_match.group(2)
            file_extension = blender_file_match.group(3)

            try:
                file_major_version = int(full_version.split('.')[0])
                if file_major_version < 4:
                    continue
            except ValueError:
                continue

            sha256_url = urljoin(major_version_dir_url_blender_org, f"blender-{full_version}.sha256")
            expected_zip_filename_in_hash_file = file_href
            file_hash = get_sha256_hash_for_zip(sha256_url, expected_zip_filename_in_hash_file)

            primary_download_url = urljoin(major_version_dir_url_blender_org, file_href)

            mirrors_for_version = []
            for mirror_base in config.BLENDER_MIRROR_BASE_URLS:
                mirror_url = primary_download_url.replace(config.BLENDER_RELEASES_URL, mirror_base)
                mirrors_for_version.append(mirror_url)

            all_platform_versions_found_in_dir.append({
                "releaseName": f"Blender {full_version}",
                "version": full_version,
                "platform_suffix": platform_suffix,
                "file_extension": file_extension,
                "hash": file_hash,
                "url": primary_download_url,
                "mirrors": mirrors_for_version
            })
    return all_platform_versions_found_in_dir