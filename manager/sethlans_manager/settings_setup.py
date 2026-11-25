# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Django settings for setup mode (``run_manager --setup``).

No run-time database (``DATABASES = {}``), no ``workers`` app, no
migrations, no data folder. ``django.contrib.auth`` is loaded only for its
password hashers and validators, which come from ``settings_base`` like
the running manager's. The secret key is thrown away when the process
ends: setup mode signs nothing that outlives it. Debug pages are off
whatever ``SETHLANS_SECURITY_DEBUG`` says. Logging is configured by
``setup_mode.entry`` (``LOGGING_CONFIG = None`` from ``settings_base``).
"""
from sethlans_manager.settings_base import *  # noqa: F401,F403

import secrets  # noqa: E402

from shared.frozen_paths import get_frontend_dist_dir  # noqa: E402
from shared.settings_file import SettingsFileError, read_settings  # noqa: E402

from sethlans_manager.body_limits import SETUP_BODY_LIMIT  # noqa: E402
from sethlans_manager.setup_mode.net import allowed_hosts as _allowed_hosts  # noqa: E402

DEBUG = False
DEBUG_PROPAGATE_EXCEPTIONS = False
SECRET_KEY = secrets.token_urlsafe(50)

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "sethlans_manager.setup_mode",
]
DATABASES = {}
ROOT_URLCONF = "sethlans_manager.setup_mode.urls"
MIDDLEWARE = ["sethlans_manager.setup_mode.middleware.SecurityHeadersMiddleware"]
TEMPLATES = []

try:
    _earlier_host = read_settings().get("server", "host", fallback="").strip()
except (SettingsFileError, OSError, ValueError):
    _earlier_host = ""
ALLOWED_HOSTS = _allowed_hosts(_earlier_host)
USE_X_FORWARDED_HOST = False
USE_X_FORWARDED_PORT = False

# The whole request body of a setup request, JSON included: the views
# read ``request.body``, which Django checks against this limit.
DATA_UPLOAD_MAX_MEMORY_SIZE = SETUP_BODY_LIMIT
FILE_UPLOAD_MAX_MEMORY_SIZE = SETUP_BODY_LIMIT
DATA_UPLOAD_MAX_NUMBER_FIELDS = 100

X_FRAME_OPTIONS = "DENY"

# The built front end; setup mode serves ``/setup/`` and its files from it.
SETUP_FRONTEND_DIR = get_frontend_dist_dir()
