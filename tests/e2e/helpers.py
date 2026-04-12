# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Polling and verification utilities for E2E tests.

Provides:
- poll_for_completion: wait for a job/animation/tiled-job to reach
  terminal status (DONE or ERROR).
- verify_image_output: download and validate a rendered image.
- is_gpu_available / is_self_hosted_runner: hardware detection helpers.
"""

import io
import logging
import os
import time

from PIL import Image

logger = logging.getLogger(__name__)

# Terminal statuses that end polling.
_TERMINAL_STATUSES = {"DONE", "ERROR", "CANCELED"}

# Default polling parameters.
DEFAULT_TIMEOUT = 240
POLL_INTERVAL = 2

# Minimum pixel brightness to confirm image is not solid black.
_MIN_BRIGHTNESS = 10


def poll_for_completion(session, url, timeout=DEFAULT_TIMEOUT):
    """
    Poll a resource URL until it reaches a terminal status.

    Args:
        session: requests.Session with auth cookies.
        url: Full URL of the resource (job, animation, tiled-job).
        timeout: Maximum seconds to wait.

    Returns:
        dict: The final JSON response from the resource.

    Raises:
        TimeoutError: If the resource does not reach terminal status
            within the timeout.
    """
    deadline = time.monotonic() + timeout
    last_status = None

    while time.monotonic() < deadline:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        last_status = data.get("status")

        if last_status in _TERMINAL_STATUSES:
            logger.info(
                "Resource %s reached status: %s", url, last_status,
            )
            return data

        logger.debug(
            "Polling %s — status: %s", url, last_status,
        )
        time.sleep(POLL_INTERVAL)

    raise TimeoutError(
        f"Resource at {url} did not reach terminal status within "
        f"{timeout}s. Last status: {last_status}"
    )


def verify_image_output(session, image_url, base_url=None):
    """
    Download and verify a rendered image is valid and non-black.

    Args:
        session: requests.Session with auth cookies.
        image_url: URL or path to the output image. If relative,
            base_url is prepended.
        base_url: Manager base URL for resolving relative paths.

    Returns:
        PIL.Image.Image: The verified image.

    Raises:
        AssertionError: If the image is invalid or solid black.
    """
    if image_url and not image_url.startswith("http"):
        full_url = f"{base_url}{image_url}"
    else:
        full_url = image_url

    resp = session.get(full_url, timeout=30)
    assert resp.status_code == 200, (
        f"Failed to download image from {full_url}: "
        f"HTTP {resp.status_code}"
    )

    img = Image.open(io.BytesIO(resp.content))
    assert img.mode in ("RGB", "RGBA", "L"), (
        f"Unexpected image mode: {img.mode}"
    )

    # Check the image is not solid black by examining pixel statistics.
    stat = img.getextrema()
    if img.mode in ("RGB", "RGBA"):
        # stat is a tuple of (min, max) per channel.
        max_values = [channel[1] for channel in stat[:3]]
        max_brightness = max(max_values)
    else:
        max_brightness = stat[1]

    assert max_brightness > _MIN_BRIGHTNESS, (
        f"Image appears to be solid black (max brightness: "
        f"{max_brightness}). URL: {full_url}"
    )

    logger.info(
        "Image verified: %dx%d, mode=%s, max_brightness=%d",
        img.width, img.height, img.mode, max_brightness,
    )
    return img


def is_gpu_available():
    """
    Check if a GPU is available for rendering.

    Returns True only if the worker's GPU detection finds at least
    one compatible device.
    """
    try:
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, "-c",
             "from worker.sethlans_worker_agent.hardware_detection "
             "import detect_gpu_devices; "
             "print(len(detect_gpu_devices()))"],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0 and int(result.stdout.strip()) > 0
    except Exception:
        return False


def is_self_hosted_runner():
    """Check if running on a self-hosted CI runner with GPU support."""
    return os.environ.get("SETHLANS_SELF_HOSTED_RUNNER", "").lower() == "true"


def admin_login(session, base_url, username, password):
    """
    Authenticate as admin via the session auth flow.

    1. GET /api/auth/csrf/ to obtain the CSRF cookie.
    2. POST /api/auth/login/ with credentials and CSRF token.

    Args:
        session: requests.Session to store cookies.
        base_url: Manager base URL (e.g., https://127.0.0.1:8080).
        username: Admin username.
        password: Admin password.

    Returns:
        dict: Login response JSON.
    """
    # Step 1: Fetch CSRF token.
    csrf_resp = session.get(f"{base_url}/api/auth/csrf/", timeout=30)
    csrf_resp.raise_for_status()
    csrf_token = session.cookies.get("csrftoken")
    assert csrf_token, "CSRF cookie not set after GET /api/auth/csrf/"

    # Step 2: Login with CSRF header.
    session.headers.update({"X-CSRFToken": csrf_token})
    login_resp = session.post(
        f"{base_url}/api/auth/login/",
        json={"username": username, "password": password},
        timeout=30,
    )
    assert login_resp.status_code == 200, (
        f"Login failed: HTTP {login_resp.status_code} — "
        f"{login_resp.text}"
    )
    # Update CSRF token after login (Django rotates it).
    new_csrf = session.cookies.get("csrftoken")
    if new_csrf:
        session.headers.update({"X-CSRFToken": new_csrf})
    # Django's CSRF middleware rejects HTTPS POST requests without a
    # Referer header ("Referer checking failed - no Referer").  The
    # requests library does not send Referer by default.  Setting it
    # on the session ensures every subsequent POST passes the check.
    session.headers.update({"Referer": f"{base_url}/"})

    logger.info("Admin login successful for user '%s'", username)
    return login_resp.json()
