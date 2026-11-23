# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where the manager keeps its state when it runs from source.

A frozen (installed) manager always keeps its state in the per-user
data dir (:func:`shared.frozen_paths.get_data_dir`); callers handle that
branch themselves and do not call this module.

From source the state (``manager.ini``, the TLS cert, the setup-complete
marker, logs, broadcaster params) lives next to the code in ``manager/``,
unless ``SETHLANS_MANAGER_DATA_DIR`` is set. The Docker image sets it to
``/data`` so every piece of state is in the mounted volume (#297); the
e2e harness and the integration tests set it to a per-test folder.

This is the one switch every source-mode reader goes through. It is
Django-free so ``config_loader`` and ``logging_config`` can use it while
settings are still loading.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Union

from shared import frozen_paths

DATA_DIR_ENV = "SETHLANS_MANAGER_DATA_DIR"


def source_data_dir(default: Union[str, Path]) -> Path:
    """Return the source-mode state dir.

    ``SETHLANS_MANAGER_DATA_DIR`` when set (validated as an absolute
    path by :func:`shared.frozen_paths.get_data_dir`), else ``default``
    (the manager source dir).
    """
    if os.environ.get(DATA_DIR_ENV):
        return frozen_paths.get_data_dir("manager")
    return Path(default)
