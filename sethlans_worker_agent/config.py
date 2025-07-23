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

# --- Manager API Configuration ---
MANAGER_API_URL = "http://127.0.0.1:8000/api/"

# --- Worker Operation Intervals ---
HEARTBEAT_INTERVAL_SECONDS = 30
JOB_POLLING_INTERVAL_SECONDS = 5

# --- Worker Agent Paths ---
# Calculates the main project root from the worker agent's script location.
# agent.py is in sethlans_reborn/sethlans_worker_agent/, so its parent's parent is sethlans_reborn/
PROJECT_ROOT_FOR_WORKER = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), '..'))

# IMPORTANT: Path to your system's installed Blender executable for default rendering (if not managed)
SYSTEM_BLENDER_EXECUTABLE = r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"

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