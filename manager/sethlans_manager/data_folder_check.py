# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The manager start's data-folder check (FR-DF-2), before anything imports
the settings.

``logging_config`` prepares a data folder chosen in setup
(``[paths] data_dir``) when the settings are imported, so an unusable one
would stop the start with a traceback from deep inside Django's set-up.
``run_manager`` calls :func:`require_usable_data_folder` first instead:
one clear line on stderr and exit 13. The later call in ``logging_config``
then finds the folder ready (the check is idempotent).

Exit 13 is "data folder unusable", next to 10 (setup saved), 11 (first
start failed) and 12 (no admin account).
"""
import sys

from shared.data_folder import DataFolderError, ensure_configured_data_root

EXIT_DATA_FOLDER = 13


def require_usable_data_folder() -> None:
    """Prepare the configured data folder, or print why not and exit 13."""
    try:
        ensure_configured_data_root("manager")
    except (DataFolderError, ValueError) as exc:
        reason = str(exc).rstrip(".")
        print(f"[ERROR] The data folder cannot be used: {reason}. Run Reset setup to choose another.",
              file=sys.stderr)
        sys.exit(EXIT_DATA_FOLDER)
