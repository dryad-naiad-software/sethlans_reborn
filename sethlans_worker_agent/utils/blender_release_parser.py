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
# Created by Mario Estrella on 07/23/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# sethlans_worker_agent/utils/blender_release_parser.py

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
        logger.error(f"An unexpected error occurred while parsing Blender releases: {e}", exc_info=True)

    # --- CORRECTED: Filter for only the latest patch of each minor version ---
    latest_patches = {}
    # Correctly sort by converting version parts to integers
    sorted_versions = sorted(all_releases.keys(), key=lambda v: [int(p) for p in v.split('.')], reverse=True)

    for version in sorted_versions:
        major_minor = ".".join(version.split('.')[:2])
        if major_minor not in latest_patches:
            latest_patches[major_minor] = {'version': version, 'data': all_releases[version]}

    final_releases = {v['version']: v['data'] for v in latest_patches.values()}

    for version_series, data in latest_patches.items():
        logger.info(f"  Selected latest for {version_series} series: {data['version']}")

    return final_releases


def parse_version_page(url, releases):
    """Parses a specific version page (e.g., /Blender4.1/) for download links."""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        # Pre-fetch all hashes for this version page
        version_from_url = url.strip('/').split('/')[-1].replace('Blender', '')
        sha_files = [a.get('href') for a in soup.find_all('a') if '.sha256' in a.get('href', '')]
        all_hashes = {}
        for sha_file in sha_files:
            sha_url = f"{url}{sha_file}"
            all_hashes.update(hash_parser.get_all_hashes_from_url(sha_url))

        # Find download links and match them with pre-fetched hashes
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
                'sha256': all_hashes.get(href)  # Look up the hash
            }
    except requests.exceptions.RequestException as e:
        logger.warning(f"Could not parse version page {url}: {e}")