# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Utility for dynamically scraping the official Blender download site.

This module fetches and parses the Blender download page to discover available
release versions and their corresponding download URLs and SHA256 hashes. It
focuses on the modern Blender versions (4.0+) and intelligently filters for
the latest patch version of each major.minor series.

NOTE: This file is duplicated in worker/sethlans_worker_agent/utils/blender_release_parser.py.
Keep both in sync.
"""

import logging
import requests
import re
from bs4 import BeautifulSoup
from . import hash_parser

logger = logging.getLogger(__name__)

BASE_URL = "https://download.blender.org/release/"
VERSION_REGEX = re.compile(r'^Blender(\d+\.\d+)/$')
FILE_REGEX = re.compile(r'blender-(\d+\.\d+\.\d+)-(.+)\.(zip|tar\.xz|dmg|msi|msix)')


def get_blender_releases():
    """
    Scrapes the Blender download page to get all official release URLs,
    filtering for only the latest patch of each minor version.

    Returns:
        dict: A dictionary of available Blender versions, where each key is a
              full version string (e.g., `'4.1.1'`) and the value is a nested
              dictionary containing download information for each platform.
    """
    all_releases = {}
    logger.info("Performing dynamic Blender download info generation (4.x+ only)...")
    try:
        response = requests.get(BASE_URL, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        for a_tag in soup.find_all('a'):
            href = a_tag.get('href')
            match = VERSION_REGEX.match(href)
            if not match:
                continue

            major_version_str = match.group(1)
            if float(major_version_str) < 4.0:
                continue

            version_url = f"{BASE_URL}{href}"
            logger.debug(f"Parsing major version page: {version_url}")
            parse_version_page(version_url, all_releases)

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch Blender release index: {e}")
    except Exception as e:
        logger.error(
            f"An unexpected error occurred while parsing Blender releases: {e}",
            exc_info=True,
        )

    # Filter for only the latest patch of each minor version
    latest_patches = {}
    sorted_versions = sorted(
        all_releases.keys(),
        key=lambda v: [int(p) for p in v.split('.')],
        reverse=True,
    )

    for version in sorted_versions:
        major_minor = ".".join(version.split('.')[:2])
        if major_minor not in latest_patches:
            latest_patches[major_minor] = {
                'version': version, 'data': all_releases[version],
            }

    final_releases = {v['version']: v['data'] for v in latest_patches.values()}

    for version_series, data in latest_patches.items():
        logger.info(f"  Selected latest for {version_series} series: {data['version']}")

    return final_releases


def resolve_latest_patch(series, timeout=5):
    """
    Resolve the latest patch version for a given Blender series.

    Args:
        series (str): The major.minor series string (e.g., "4.2").
        timeout (int): HTTP request timeout in seconds.

    Returns:
        str or None: The latest full patch version (e.g., "4.2.19"),
                     or None if resolution fails.
    """
    version_url = f"{BASE_URL}Blender{series}/"
    try:
        response = requests.get(version_url, timeout=timeout)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        versions_found = []
        for a_tag in soup.find_all('a'):
            href = a_tag.get('href', '')
            file_match = FILE_REGEX.match(href)
            if file_match:
                versions_found.append(file_match.group(1))

        if not versions_found:
            return None

        # Return the highest patch version
        versions_found.sort(
            key=lambda v: [int(p) for p in v.split('.')],
            reverse=True,
        )
        return versions_found[0]

    except requests.exceptions.RequestException as e:
        logger.warning(f"Could not resolve latest patch for series {series}: {e}")
        return None


def parse_version_page(url, releases):
    """
    Parses a specific Blender version page for download links and SHA256 hashes.

    Args:
        url (str): The URL of the version page to parse.
        releases (dict): The dictionary to populate with the parsed release data.
    """
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        # Pre-fetch all hashes for this version page
        sha_files = [
            a.get('href') for a in soup.find_all('a')
            if '.sha256' in a.get('href', '')
        ]
        all_hashes = {}
        for sha_file in sha_files:
            sha_url = f"{url}{sha_file}"
            all_hashes.update(hash_parser.get_all_hashes_from_url(sha_url))

        for a_tag in soup.find_all('a'):
            href = a_tag.get('href')
            file_match = FILE_REGEX.match(href)
            if not file_match:
                continue

            version = file_match.group(1)
            platform = file_match.group(2)

            if version not in releases:
                releases[version] = {}

            releases[version][platform] = {
                'url': f"{url}{href}",
                'sha256': all_hashes.get(href),
            }
    except requests.exceptions.RequestException as e:
        logger.warning(f"Could not parse version page {url}: {e}")
