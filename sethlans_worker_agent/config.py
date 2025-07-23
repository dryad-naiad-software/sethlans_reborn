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

import os
import sys
import platform

# --- Manager API Configuration ---
MANAGER_API_URL = "http://127.0.0.1:8000/api/"

# --- Worker Operation Intervals ---
HEARTBEAT_INTERVAL_SECONDS = 30
JOB_POLLING_INTERVAL_SECONDS = 5

# --- Worker Agent Paths ---
PROJECT_ROOT_FOR_WORKER = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), '..'))

# IMPORTANT: Path to your system's installed Blender executable for default rendering (if not managed)
# This should ideally be updated based on CURRENT_PLATFORM_BLENDER_DETAILS as well.
SYSTEM_BLENDER_EXECUTABLE = None

# Directory where Sethlans Reborn will manage downloaded/extracted tools (like Blender portable versions)
MANAGED_TOOLS_DIR = os.path.join(PROJECT_ROOT_FOR_WORKER, 'sethlans_worker_agent', 'managed_tools')

# Placeholder paths for testing blend files/output.
TEST_BLEND_FILE_PATH = os.path.join(PROJECT_ROOT_FOR_WORKER, 'test_scene.blend')
TEST_OUTPUT_DIR = os.path.join(PROJECT_ROOT_FOR_WORKER, 'render_test_output')

# --- Tool Discovery & Download Configuration ---
BLENDER_RELEASES_URL = "https://download.blender.org/release/"

BLENDER_MIRROR_BASE_URLS = [
    "https://mirror.clarkson.edu/blender/release/",
    "http://ftp.halifax.rwth-aachen.de/blender/release/",
    "http://ftp.nluug.nl/pub/graphics/blender/release/",
]

# Path to the local JSON cache file for Blender versions
BLENDER_VERSIONS_CACHE_FILE = os.path.join(MANAGED_TOOLS_DIR, 'blender_versions_cache.json')

# --- NEW: Platform and Architecture-specific Blender Download/Executable Mappings ---
# Maps (platform.system(), platform.machine().lower()) to Blender download string and executable sub-path
# platform.machine() returns architecture like 'AMD64', 'x86_64', 'arm64'
PLATFORM_BLENDER_MAP = {
    ('Windows', 'amd64'): {
        'download_suffix': 'windows-x64',
        'download_ext': '.zip',
        'executable_path_in_folder': 'blender.exe'
    },
    ('Windows', 'arm64'): { # New Windows ARM64 support
        'download_suffix': 'windows-arm64',
        'download_ext': '.zip',
        'executable_path_in_folder': 'blender.exe'
    },
    ('Linux', 'x86_64'): {
        'download_suffix': 'linux-x64',
        'download_ext': '.tar.xz',
        'executable_path_in_folder': 'blender'
    },
    ('Darwin', 'x86_64'): { # macOS Intel
        'download_suffix': 'macos-x64',
        'download_ext': '.dmg',
        'executable_path_in_folder': 'blender.app/Contents/MacOS/blender'
    },
    ('Darwin', 'arm64'): { # macOS Apple Silicon
        'download_suffix': 'macos-arm64',
        'download_ext': '.dmg',
        'executable_path_in_folder': 'blender.app/Contents/MacOS/blender'
    }
}

# Dynamically determine the current worker's platform details for Blender
CURRENT_PLATFORM_BLENDER_DETAILS = PLATFORM_BLENDER_MAP.get((platform.system(), platform.machine().lower()))
if not CURRENT_PLATFORM_BLENDER_DETAILS:
    print(f"[WARNING] Unsupported OS/Architecture for Blender management: ({platform.system()}, {platform.machine().lower()}). Auto-download may not work.")
    print(f"[DEBUG Config] platform.system(): {platform.system()}") # <-- NEW DEBUG
    print(f"[DEBUG Config] platform.machine().lower(): {platform.machine().lower()}") # <-- NEW DEBUG
    print(f"[DEBUG Config] PLATFORM_BLENDER_MAP: {PLATFORM_BLENDER_MAP}") # <-- NEW DEBUG
else: # <-- NEW: Print details if found
    print(f"[DEBUG Config] Resolved CURRENT_PLATFORM_BLENDER_DETAILS: {CURRENT_PLATFORM_BLENDER_DETAILS.get('download_suffix')}") # <-- NEW DEBUG
