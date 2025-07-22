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
# Created by Mario Estrella on 7/22/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
import requests
import json
import time
import platform
import socket
import os
import sys # <-- Import sys for path resolution

# --- Configuration ---
# URL of your Sethlans Reborn Manager's heartbeat API endpoint
# Ensure your Django development server is running!
MANAGER_API_URL = "http://127.0.0.1:8000/api/heartbeat/"
HEARTBEAT_INTERVAL_SECONDS = 30 # How often the worker sends a heartbeat

# Calculate the main project root from the worker agent's script location.
# agent.py is in sethlans_reborn/sethlans_worker_agent/
# os.path.dirname(os.path.abspath(sys.argv[0])) gives C:\Users\mestrella\Projects\sethlans_reborn\sethlans_worker_agent\
# os.path.join(..., '..') moves up to C:\Users\mestrella\Projects\sethlans_reborn\
PROJECT_ROOT_FOR_WORKER = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), '..'))

# IMPORTANT: Update this path to your Blender executable (absolute path on the worker machine)
# This is relevant for when the worker actually runs Blender, not for the heartbeat itself.
BLENDER_EXECUTABLE = r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"

# Paths to blend file and output directory relative to the *main project root*
# These are just placeholders for when the worker agent expands to run renders.
TEST_BLEND_FILE_PATH = os.path.join(PROJECT_ROOT_FOR_WORKER, 'test_scene.blend')
TEST_OUTPUT_DIR = os.path.join(PROJECT_ROOT_FOR_WORKER, 'render_test_output')


def get_system_info():
    """Gathers basic system information for the heartbeat."""
    hostname = socket.gethostname()
    ip_address = None
    try:
        ip_address = socket.gethostbyname(hostname) # Gets primary IPv4 address
    except socket.gaierror:
        pass # Could not resolve hostname

    os_info = platform.system() # e.g., 'Windows', 'Linux', 'Darwin'
    # Add more detailed OS info if needed
    if os_info == 'Windows':
        os_info += f" {platform.release()}"
    elif os_info == 'Linux':
        os_info += f" {platform.version()}" # Kernel version

    # You can expand this to include CPU, GPU, RAM, etc., later (Phase 3.1)
    return {
        "hostname": hostname,
        "ip_address": ip_address,
        "os": os_info
    }

def send_heartbeat(system_info):
    """Sends a heartbeat to the Django Manager."""
    try:
        print(f"[{time.ctime()}] Sending heartbeat to {MANAGER_API_URL}...")
        response = requests.post(MANAGER_API_URL, json=system_info, timeout=5)
        response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)

        print(f"[{time.ctime()}] Heartbeat successful: HTTP {response.status_code}")
        # print(f"Response data: {response.json()}") # Uncomment for detailed debug

    except requests.exceptions.Timeout:
        print(f"[{time.ctime()}] ERROR: Heartbeat timed out after 5 seconds.")
    except requests.exceptions.RequestException as e:
        print(f"[{time.ctime()}] ERROR: Heartbeat failed - {e}")
    except json.JSONDecodeError:
        print(f"[{time.ctime()}] ERROR: Failed to decode JSON response.")
    except Exception as e:
        print(f"[{time.ctime()}] An unexpected error occurred: {e}")

if __name__ == "__main__":
    print("Sethlans Reborn Worker Agent Starting...")
    # Ensure the Django Manager (sethlans_reborn project) is running!

    while True:
        info = get_system_info()
        send_heartbeat(info)
        time.sleep(HEARTBEAT_INTERVAL_SECONDS) # Wait for the next heartbeat