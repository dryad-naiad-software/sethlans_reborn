# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where a manager run from source keeps its identity-side state.

Data (database, media, static files, logs, FFmpeg) is not here: in every
mode it is :func:`shared.frozen_paths.get_data_dir` (``manager/`` by
default from source; ``SETHLANS_MANAGER_DATA_DIR``,
``SETHLANS_DATA_ROOT`` and ``[paths] data_dir`` move it). A frozen
(installed) manager keeps its identity files (TLS, secret key,
broadcaster params) in :func:`shared.frozen_paths.get_identity_dir`;
callers handle that branch themselves and do not call this module.

From source the TLS cert and the broadcaster params live next to the
code in ``manager/``, unless ``SETHLANS_MANAGER_DATA_DIR`` is set. The
Docker image sets it to ``/data`` so every piece of state is in the
mounted volume (#297); the e2e harness and the integration tests set it
to a per-test folder. ``SETHLANS_DATA_ROOT`` and ``[paths] data_dir``
never move these. The settings file itself is resolved by
:func:`shared.settings_file.settings_path`, which follows the same rule
from source (``<repo>/manager/sethlans.ini`` or
``$SETHLANS_MANAGER_DATA_DIR/sethlans.ini``).

It is Django-free so it can be used while settings are still loading.
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
