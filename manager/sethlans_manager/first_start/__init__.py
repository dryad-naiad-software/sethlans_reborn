# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The manager's first-start step (FR-FS), run by every normal start.

``run_manager.main()`` calls :func:`require_first_start` after
``_prepare_runtime`` (migrated database, ``runtime_state`` set) and before
Waitress starts, in the manager's own process with its full environment
and database (which is what closes #307). In order:

1. FR-FS-1, the first admin from ``[first_admin]`` (``admin_from_hash``);
2. FR-FS-2, the first admin from ``SETHLANS_ADMIN_*`` (``admin_from_env``);
3. FR-FS-2a, an active superuser must exist, else exit 12;
4. FR-FS-3, local worker trust in ``manager_worker`` mode (``local_worker``).

Each step does nothing when its work is done, so every later start is the
same as the first (FR-FS-4). A failure prints one line on stderr, logs it,
and exits 11 or 12 (``shared.manager_exit_codes``); a database error on
the way is exit 11.
"""
import logging
import sys
from pathlib import Path
from typing import Optional

from django.db import DatabaseError

from sethlans_manager.error_text import short_error
from sethlans_manager.first_start.admin_from_env import apply_env_admin
from sethlans_manager.first_start.admin_from_hash import apply_first_admin
from sethlans_manager.first_start.common import FirstStartError, inactive_admin_exists, usable_admin_exists
from sethlans_manager.first_start.local_worker import ensure_local_worker_trust
from shared import settings_file
from shared.manager_exit_codes import EXIT_FIRST_START_FAILED, EXIT_NO_ADMIN

__all__ = ["FirstStartError", "INACTIVE_ADMIN_REASON", "NO_ADMIN_REASON", "require_first_start", "run"]

logger = logging.getLogger(__name__)

NO_ADMIN_REASON = (
    "there is no admin account; run Reset setup to make one (Docker: set SETHLANS_ADMIN_USERNAME "
    "and SETHLANS_ADMIN_PASSWORD_FILE, or run createsuperuser)"
)
INACTIVE_ADMIN_REASON = (
    "every admin account is inactive; run Reset setup and tick 'Replace its password?' for one of "
    "them to make it active, or make a new one (Docker: set SETHLANS_ADMIN_USERNAME and "
    "SETHLANS_ADMIN_PASSWORD_FILE to a new name, or run createsuperuser)"
)


def run(ini_path: Optional[Path] = None) -> None:
    """The four steps in order; raises :class:`FirstStartError`."""
    ini_path = settings_file.settings_path() if ini_path is None else Path(ini_path)
    apply_first_admin(ini_path)
    apply_env_admin()
    if not usable_admin_exists():
        raise FirstStartError(EXIT_NO_ADMIN, INACTIVE_ADMIN_REASON if inactive_admin_exists() else NO_ADMIN_REASON)
    ensure_local_worker_trust(settings_file.setup_finished(ini_path))


def require_first_start(ini_path: Optional[Path] = None) -> None:
    """Run the step, or print and log why not and exit 11 or 12."""
    try:
        run(ini_path)
    except FirstStartError as exc:
        code, reason = exc.code, exc.reason
    except DatabaseError as exc:
        code, reason = EXIT_FIRST_START_FAILED, f"database error: {short_error(exc)}"
    else:
        return
    logger.error("First start failed (exit %s): %s", code, reason)
    print(f"\n[ERROR] First start failed: {reason}", file=sys.stderr)
    sys.exit(code)
