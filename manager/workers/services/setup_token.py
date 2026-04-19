# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup-wizard shared token helper.

The 256-bit setup token lives in ``manager.ini [setup] token``.  It is
consumed by ``setup_bootstrap_view`` exactly once to swap for a
setup-phase Django session cookie (see the setup-auth-unification
spec).  No other view reads the token.
"""

import configparser
from pathlib import Path

from django.conf import settings

from shared.frozen_paths import get_data_dir, is_frozen


def _data_dir() -> Path:
    """Return the manager data directory (frozen vs dev)."""
    if is_frozen():
        return get_data_dir("manager")
    return settings.BASE_DIR


def read_setup_token() -> str | None:
    """Return the setup token from ``manager.ini [setup] token``.

    Returns ``None`` if ``manager.ini`` does not exist or has no
    ``[setup]`` section / ``token`` option.  Callers must treat
    ``None`` as "token path unavailable" and fall back to their own
    authentication check.
    """
    ini_path = _data_dir() / "manager.ini"
    if not ini_path.exists():
        return None
    config = configparser.ConfigParser()
    config.read(ini_path)
    return config.get("setup", "token", fallback=None)
