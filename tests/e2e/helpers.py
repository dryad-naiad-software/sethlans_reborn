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
# Created by Gemini on 8/1/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# tests/e2e/helpers.py
"""
A collection of helper functions to support the E2E test suite.

This module centralizes common, reusable actions such as polling API endpoints
for job completion and verifying the integrity of rendered image outputs. This
helps keep the test cases clean, readable, and DRY (Don't Repeat Yourself).
"""
import io
import time

import pytest
import requests
from PIL import Image

from sethlans_worker_agent import system_monitor


def poll_for_completion(api_url: str, timeout_seconds: int = 120, interval_seconds: int = 2) -> dict:
    """
    Polls a job or animation API endpoint until its status is 'DONE' or 'ERROR'.

    Args:
        api_url (str): The full URL of the job or animation to poll.
        timeout_seconds (int): The maximum number of seconds to wait.
        interval_seconds (int): The number of seconds to wait between polls.

    Returns:
        dict: The final JSON response data from the API.

    Raises:
        TimeoutError: If the job does not complete within the specified timeout.
        AssertionError: If the final status is 'ERROR'.
    """
    start_time = time.time()
    while time.time() - start_time < timeout_seconds:
        response = requests.get(api_url)
        response.raise_for_status()
        data = response.json()
        status = data.get('status')

        if status in ['DONE', 'ERROR']:
            assert status != 'ERROR', f"Job at {api_url} failed with status 'ERROR'. Details: {data.get('error_message')}"
            return data

        print(f"  Polling {api_url}... Status is {status}. Waiting {interval_seconds}s.")
        time.sleep(interval_seconds)

    raise TimeoutError(f"Job at {api_url} did not complete within {timeout_seconds} seconds.")


def verify_image_output(image_url: str, expected_size: tuple = None):
    """
    Downloads an image from a URL and performs basic validation.

    Checks that the response is successful, the content is a valid image,
    and the image is not completely black. Optionally verifies image dimensions.

    Args:
        image_url (str): The URL of the image file to verify.
        expected_size (tuple, optional): A tuple (width, height) for dimension assertion.
    """
    assert image_url, "Image URL was not provided for verification."

    response = requests.get(image_url)
    assert response.status_code == 200, f"Failed to download image from {image_url}"

    try:
        with Image.open(io.BytesIO(response.content)) as img:
            img.verify()  # Verifies that this is a valid image
            # Re-open after verify
            with Image.open(io.BytesIO(response.content)) as img_data:
                min_val, max_val = img_data.convert('L').getextrema()
                assert max_val > 10, "Image is unexpectedly dark or completely black."

                if expected_size:
                    assert img_data.size == expected_size, f"Image size mismatch. Expected {expected_size}, got {img_data.size}."
    except Exception as e:
        pytest.fail(f"Image verification failed for {image_url}: {e}")


def is_gpu_available() -> bool:
    """
    Checks if a compatible GPU is available for rendering on the host machine.

    This function temporarily resets the system_monitor's cache to ensure it
    performs a fresh hardware detection scan.

    Returns:
        bool: True if one or more GPU devices are detected, False otherwise.
    """
    system_monitor._gpu_devices_cache = None  # Ensure a fresh check
    devices = system_monitor.detect_gpu_devices()
    system_monitor._gpu_devices_cache = None  # Clear cache for subsequent tests
    return len(devices) > 0