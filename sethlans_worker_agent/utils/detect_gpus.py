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
# Created by Mario Estrella on 8/4/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# sethlans_worker_agent/utils/detect_gpus.py
"""
A utility script to be executed by Blender for detecting available GPU devices.

This script accesses Cycles render preferences, forces a hardware scan, and prints
details for all non-CPU compute devices found. The output is designed to be
easily parsed by another process.
"""
import bpy
import sys
import json
import logging

# Configure logging to stderr to avoid interfering with stdout JSON output
logging.basicConfig(level=logging.INFO, stream=sys.stderr, format='[%(asctime)s] [%(levelname)s] [detect_gpus] %(message)s')

def get_gpu_devices():
    """
    Detects and returns a list of all available GPU compute devices for Cycles.

    Returns:
        list: A list of dictionaries, where each dictionary represents a GPU
              and contains its index, name, type, and ID.
    """
    devices_list = []
    try:
        logging.info("Starting GPU detection inside Blender...")
        # Ensure Cycles is the active render engine to access its preferences
        bpy.context.scene.render.engine = 'CYCLES'

        # Get the Cycles preferences object
        cycles_prefs = bpy.context.preferences.addons['cycles'].preferences

        # This is a critical step: it forces Blender to refresh its device list.
        # Without this, the list might be empty in a headless environment.
        logging.info("Forcing device scan...")
        cycles_prefs.get_devices()
        logging.info(f"Found {len(cycles_prefs.devices)} total devices (including CPU).")

        # Filter out CPU devices to only list GPUs
        for i, device in enumerate(cycles_prefs.devices):
            if device.type != 'CPU':
                device_info = {
                    "index": i,
                    "name": device.name,
                    "type": device.type,
                    "id": device.id
                }
                devices_list.append(device_info)
                logging.info(f"Detected GPU: {device.name} (Type: {device.type})")

    except Exception as e:
        # Print errors to stderr for better script integration
        logging.critical(f"Error occurred during GPU detection: {e}", exc_info=True)
        # Exit with a non-zero status code to indicate failure
        sys.exit(1)

    return devices_list

if __name__ == "__main__":
    gpu_devices = get_gpu_devices()
    logging.info(f"Detection complete. Found {len(gpu_devices)} GPU(s).")
    # Print the result as a JSON string for easy parsing by the calling process
    print(json.dumps(gpu_devices))