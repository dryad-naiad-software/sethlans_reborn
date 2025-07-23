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

import platform
import socket
import datetime
import requests
import json

from . import config  # Import config module for settings
from .tool_manager import scan_for_blender_versions  # Import scanner from tool_manager

# Global variable to store worker's own info once registered
WORKER_INFO = {}  # This object will be updated here and accessed by other modules


def get_system_info():
    """
    Gathers basic system information and available managed tools for the heartbeat.
    """
    hostname = socket.gethostname()
    ip_address = None
    try:
        ip_address = socket.gethostbyname(hostname)
    except socket.gaierror:
        pass

    os_info = platform.system()
    if os_info == 'Windows':
        os_info += f" {platform.release()}"
    elif os_info == 'Linux':
        os_info += f" {platform.version()}"

    available_tools = scan_for_blender_versions()

    return {
        "hostname": hostname,
        "ip_address": ip_address,
        "os": os_info,
        "available_tools": available_tools
    }


def send_heartbeat(system_info):
    """
    Sends a heartbeat to the Django Manager's heartbeat API endpoint.
    Updates the global WORKER_INFO with the worker's ID received from the manager.
    """
    global WORKER_INFO
    heartbeat_url = f"{config.MANAGER_API_URL}heartbeat/"  # Use URL from config
    try:
        print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Sending heartbeat to {heartbeat_url}...")
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Heartbeat payload: {json.dumps(system_info, indent=2)}")

        response = requests.post(heartbeat_url, json=system_info, timeout=5)
        response.raise_for_status()

        response_data = response.json()
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Heartbeat successful: HTTP {response.status_code}")
        WORKER_INFO = response_data
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] Worker registered as ID: {WORKER_INFO.get('id')}, Hostname: {WORKER_INFO.get('hostname')}")

    except requests.exceptions.Timeout:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Heartbeat timed out after 5 seconds.")
    except requests.exceptions.RequestException as e:
        print(f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Heartbeat failed - {e}")
    except json.JSONDecodeError:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] ERROR: Failed to decode JSON response from heartbeat.")
    except Exception as e:
        print(
            f"[{datetime.datetime.now().strftime('%a %b %d %H:%M:%S %Y')}] An unexpected error occurred during heartbeat: {e}")