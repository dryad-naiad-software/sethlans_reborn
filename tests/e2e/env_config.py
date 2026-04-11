# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Environment configuration for E2E test subprocesses.

Provides env-dict builders for the Django manager and worker agent,
along with shared constants (paths, credentials, secrets).
"""

import os
import secrets
import socket
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MANAGE_PY = str(REPO_ROOT / "manager" / "manage.py")
RUN_MANAGER = str(REPO_ROOT / "manager" / "run_manager.py")
WORKER_ENTRY = str(REPO_ROOT / "worker" / "run_worker.py")

# Credentials for the test admin user.
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "testpass123!"
ADMIN_EMAIL = "admin@test.local"


def find_free_port():
    """Find and return a free TCP port using OS allocation."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def build_manager_env(
    db_path, media_root, enrollment_key, secret_key, port
):
    """
    Build the environment dict for the manager subprocess.

    All secrets are passed exclusively via env vars -- never
    written to os.environ in the parent process.
    """
    env = os.environ.copy()
    env.update({
        "DJANGO_SETTINGS_MODULE": "sethlans_manager.settings",
        "SETHLANS_DB_NAME": str(db_path),
        "SETHLANS_MEDIA_ROOT": str(media_root),
        "SETHLANS_SECURITY_SECRET_KEY": secret_key,
        "SETHLANS_SECURITY_DEBUG": "true",
        "SETHLANS_SECURITY_ENROLLMENT_KEY": enrollment_key,
        "SETHLANS_MANAGER_HOST": "127.0.0.1",
        "SETHLANS_MANAGER_PORT": str(port),
        "DJANGO_SUPERUSER_USERNAME": ADMIN_USERNAME,
        "DJANGO_SUPERUSER_PASSWORD": ADMIN_PASSWORD,
        "DJANGO_SUPERUSER_EMAIL": ADMIN_EMAIL,
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return env


def build_worker_env(enrollment_key, manager_host, manager_port):
    """
    Build the environment dict for the worker subprocess.

    Uses the SETHLANS_{SECTION}_{KEY} naming convention from
    worker config.py.
    """
    env = os.environ.copy()
    env.update({
        "SETHLANS_MANAGER_HOST": manager_host,
        "SETHLANS_MANAGER_PORT": str(manager_port),
        "SETHLANS_MANAGER_ENROLLMENT_KEY": enrollment_key,
        # Override any stale token from worker/config.ini so the
        # worker enrolls fresh with the test enrollment key.
        "SETHLANS_MANAGER_API_TOKEN": "",
        # Clear any stale cert fingerprint so the worker accepts the
        # new test manager's self-signed cert during enrollment.
        "SETHLANS_MANAGER_CERT_FINGERPRINT": "",
        "SETHLANS_WORKER_HEARTBEAT_INTERVAL": "5",
        "SETHLANS_WORKER_POLLING_INTERVAL": "3",
        "SETHLANS_WORKER_UI_ENABLED": "false",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return env


def generate_secrets():
    """Generate per-test-class secrets for enrollment and Django."""
    return {
        "enrollment_key": secrets.token_urlsafe(32),
        "secret_key": secrets.token_urlsafe(50),
    }
