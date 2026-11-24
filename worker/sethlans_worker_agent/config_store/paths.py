# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Path selection for the worker config store (FR-24, FR-24a).

Selects:
  * ``get_data_dir()``     — the worker's data folder (tools, assets,
                             unreal, output, temp, logs, failed_uploads),
                             from :func:`shared.frozen_paths.get_data_dir`.
  * ``get_identity_dir()`` — the worker's identity folder (``config.json``,
                             its lock, ``tls/``, setup state, Caddyfile),
                             from :func:`shared.frozen_paths.get_identity_dir`;
                             it never moves with the data folder.
  * ``SYSTEM_CONFIG_PATH`` — ``/etc/sethlans/worker.json`` on Linux;
                             ``None`` on Windows/macOS.

Both folders are the same by default (``<settings>/worker``) and when
``SETHLANS_WORKER_DATA_DIR`` is set (Docker: ``/data``).
"""

import platform
from pathlib import Path
from typing import Optional

from shared import frozen_paths

WORKER_COMPONENT = "worker"


def get_data_dir() -> Path:
    """Return the worker's data folder (env > data root > settings file > default)."""
    return frozen_paths.get_data_dir(WORKER_COMPONENT)


def get_identity_dir() -> Path:
    """Return the worker's identity folder (``SETHLANS_WORKER_DATA_DIR`` or ``<settings>/worker``)."""
    return frozen_paths.get_identity_dir(WORKER_COMPONENT)


def _system_config_path() -> Optional[Path]:
    if platform.system() == "Linux":
        return Path("/etc/sethlans/worker.json")
    return None


SYSTEM_CONFIG_PATH: Optional[Path] = _system_config_path()


def user_config_path() -> Path:
    return get_identity_dir() / "config.json"


def lockfile_path() -> Path:
    return get_identity_dir() / "config.json.lock"
