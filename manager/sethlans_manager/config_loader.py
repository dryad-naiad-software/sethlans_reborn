# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Config-loading machinery for :mod:`sethlans_manager.settings`.

This module centralizes the resolution of ``BASE_DIR`` and the settings
file location, plus the ``_get_config`` helper that enforces the
hierarchy ``env var > settings file > default``.  Extracting it keeps
``settings.py`` under the 300-line cap (see GitHub issue #103).

The settings file is ``sethlans.ini`` at
:func:`shared.settings_file.settings_path`, parsed without
interpolation (a ``%`` in a value is literal).
"""

from pathlib import Path
import os

from shared.frozen_paths import get_manager_dir, is_frozen
from shared.settings_file import read_settings, settings_path


# Build paths inside the project like this: BASE_DIR / 'subdir'.
if is_frozen():
    BASE_DIR = get_manager_dir()
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

# --- Manager configuration (the settings file) ---
# Hierarchy: env vars > settings file > defaults
_INSECURE_DEFAULT_KEY = (
    'django-insecure-^&r@p#+r6h*!@!1u=l!0j_z%z!%^n#b=2#h&l16b%c!0609t'
)

_config_file_path = settings_path()
# A file that exists but cannot be read or parsed stops the start, as
# it did before: running on silent defaults would hide the fault.
_config = read_settings(_config_file_path)


def _get_config(section, key, env_var, default):
    """Read a setting: env var > settings file > default."""
    value = os.getenv(env_var)
    if value is not None:
        return value
    if _config.has_option(section, key):
        return _config.get(section, key)
    return default
