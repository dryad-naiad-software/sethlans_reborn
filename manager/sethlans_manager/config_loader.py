# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Config-loading machinery for :mod:`sethlans_manager.settings`.

This module centralizes the resolution of ``BASE_DIR`` and the
``manager.ini`` location, plus the ``_get_config`` helper that enforces
the hierarchy ``env var > manager.ini > default``.  Extracting it keeps
``settings.py`` under the 300-line cap (see GitHub issue #103).  Pure
move — no behavior change.
"""

from pathlib import Path
import configparser
import os

from shared.frozen_paths import get_data_dir, get_manager_dir, is_frozen


# Build paths inside the project like this: BASE_DIR / 'subdir'.
if is_frozen():
    BASE_DIR = get_manager_dir()
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

# --- Manager Configuration (manager.ini) ---
# Hierarchy: env vars > manager.ini > defaults
_INSECURE_DEFAULT_KEY = (
    'django-insecure-^&r@p#+r6h*!@!1u=l!0j_z%z!%^n#b=2#h&l16b%c!0609t'
)

_config = configparser.ConfigParser()
# Honor SETHLANS_MANAGER_DATA_DIR in source mode so integration tests (and
# dev scripts that set the env var) get the same isolation as frozen mode.
if is_frozen() or os.environ.get('SETHLANS_MANAGER_DATA_DIR'):
    _config_file_path = get_data_dir('manager') / 'manager.ini'
else:
    _config_file_path = BASE_DIR / 'manager.ini'
if _config_file_path.exists():
    _config.read(_config_file_path)


def _get_config(section, key, env_var, default):
    """Read a setting: env var > manager.ini > default."""
    value = os.getenv(env_var)
    if value is not None:
        return value
    if _config.has_option(section, key):
        return _config.get(section, key)
    return default
