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
import re
import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from sethlans_worker_agent import config


# --- Blender Release Parsing Functions ---
# These are standalone functions that handle the web scraping and parsing.

def fetch_page_soup(url, timeout=10):
    """Helper to fetch a URL and return a BeautifulSoup object."""
    try:
        print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Fetching page: {url}")
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        return BeautifulSoup(response.text, 'html.parser')
    except requests.exceptions.RequestException as e:
        print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Failed to fetch URL {url}: {e}")
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
                    print(
                        f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Skipping Blender major version < 4: {version_prefix}")
                    continue
            except ValueError:
                print(
                    f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Warning: Could not parse major version from directory '{href}'. Skipping.")
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
                    print(
                        f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}]   Found hash for {expected_zip_filename}: {file_hash}")
                    break
        if not file_hash:
            print(
                f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Warning: Hash for {expected_zip_filename} not found in {sha256_url}.")
    except requests.exceptions.RequestException as req_e:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Warning: Failed to fetch SHA256 for {expected_zip_filename} from {sha256_url} ({req_e})")
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

    print(
        f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}]   Parsing details from: {major_version_dir_url_blender_org}")
    for file_link in dir_soup.find_all('a', href=True):
        file_href = file_link['href']
        # --- NEW: More generic regex to capture version, platform suffix, and extension for all supported OS/Arch ---
        # e.g., blender-X.Y.Z-windows-x64.zip, blender-X.Y.Z-macos-arm64.dmg, blender-X.Y.Z-linux-x64.tar.xz
        blender_file_match = re.match(r'blender-(\d+\.\d+\.\d+)-(.+)\.(zip|tar\.xz|dmg)$', file_href)
        if blender_file_match:
            full_version = blender_file_match.group(1)
            platform_suffix = blender_file_match.group(2)  # e.g., 'windows-x64', 'macos-arm64'
            file_extension = blender_file_match.group(3)  # e.g., 'zip', 'tar.xz', 'dmg'

            try:
                file_major_version = int(full_version.split('.')[0])
                if file_major_version < 4:
                    continue
            except ValueError:
                continue

            # Fetch SHA256 hash file content for this version
            # The hash file URL is still blender-X.Y.Z.sha256 regardless of platform
            sha256_url = urljoin(major_version_dir_url_blender_org, f"blender-{full_version}.sha256")
            expected_zip_filename_in_hash_file = file_href  # The exact filename from the link (e.g., blender-X.Y.Z-platform.zip)
            file_hash = get_sha256_hash_for_zip(sha256_url, expected_zip_filename_in_hash_file)

            primary_download_url = urljoin(major_version_dir_url_blender_org, file_href)

            mirrors_for_version = []
            for mirror_base in config.BLENDER_MIRROR_BASE_URLS:
                mirror_url = primary_download_url.replace(config.BLENDER_RELEASES_URL, mirror_base)
                mirrors_for_version.append(mirror_url)

            all_platform_versions_found_in_dir.append({
                "releaseName": f"Blender {full_version}",
                "version": full_version,
                "platform_suffix": platform_suffix,  # Store platform suffix (e.g., 'windows-x64', 'macos-arm64')
                "file_extension": file_extension,  # Store file extension (e.g., 'zip', 'tar.xz', 'dmg')
                "hash": file_hash,
                "url": primary_download_url,
                "mirrors": mirrors_for_version
            })
    return all_platform_versions_found_in_dir