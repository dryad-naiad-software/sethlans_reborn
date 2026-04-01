# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A collection of helper functions to support the E2E test suite.

This module centralizes common, reusable actions such as polling API endpoints
for job completion and verifying the integrity of rendered image outputs. This
helps keep the test cases clean, readable, and DRY (Don't Repeat Yourself).
"""
import io
import os
import time
import logging

import psutil
import pytest
import requests
from PIL import Image

from sethlans_worker_agent import system_monitor

logger = logging.getLogger(__name__)


def poll_for_completion(api_url: str, timeout_seconds: int = 240, interval_seconds: int = 2) -> dict:
    """
    Polls a job or animation API endpoint until its status is 'DONE' or 'ERROR'.

    Args:
        api_url (str): The full URL of the job or animation to poll.
        timeout_seconds (int): The maximum number of seconds to wait. The default is
            increased to 240s to accommodate slower CI runners.
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

        logger.info("Polling %s... Status is %s. Waiting %ss.", api_url, status, interval_seconds)
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
                    assert img_data.size == expected_size, (
                        f"Image size mismatch. Expected {expected_size}, got {img_data.size}."
                    )
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
    from sethlans_worker_agent import hardware_detection
    hardware_detection._gpu_devices_cache = None  # Ensure a fresh check
    devices = system_monitor.detect_gpu_devices()
    hardware_detection._gpu_devices_cache = None  # Clear cache for subsequent tests
    return len(devices) > 0


def is_self_hosted_runner() -> bool:
    """
    Checks if the tests are running on a self-hosted CI runner by checking
    the `SETHLANS_SELF_HOSTED_RUNNER` environment variable. This check is
    case-insensitive and includes diagnostic logging.

    Returns:
        bool: True if the environment variable is set to 'true', False otherwise.
    """
    val = os.environ.get("SETHLANS_SELF_HOSTED_RUNNER", "false")
    result = val.lower() == "true"
    # Add diagnostic logging to help debug CI environment issues
    logger.debug('Checked SETHLANS_SELF_HOSTED_RUNNER: got "%s", evaluated to %s.', val, result)
    return result


def get_blender_process_count():
    """Counts the number of currently running Blender processes."""
    count = 0
    for proc in psutil.process_iter(['name']):
        # Use lower() for case-insensitive matching (e.g., blender.exe vs Blender.exe)
        if 'blender' in proc.info['name'].lower():
            count += 1
    return count


def stop_worker_and_collect_logs(test_instance):
    """
    Stops the worker process, joins the log reader thread, and returns
    the full captured log output as a single string.

    This is useful for tests that restart the worker in a special mode
    and need to verify log output after the test completes.

    Args:
        test_instance: A test class instance with ``worker_process``,
            ``worker_log_thread``, and ``worker_log_queue`` attributes.

    Returns:
        str: The concatenated worker log output.
    """
    if test_instance.worker_process and test_instance.worker_process.poll() is None:
        test_instance.worker_process.kill()
        test_instance.worker_process.wait(timeout=10)
    if test_instance.worker_log_thread and test_instance.worker_log_thread.is_alive():
        test_instance.worker_log_thread.join(timeout=5)

    lines = []
    while not test_instance.worker_log_queue.empty():
        lines.append(test_instance.worker_log_queue.get_nowait())
    return "".join(lines)
