# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Migration and collectstatic runners for both source and frozen modes.

**Source mode:** Uses ``subprocess.run([sys.executable, manage.py, ...])``
to apply migrations and collect static files in a child process.

**Frozen mode (PyInstaller):** ``sys.executable`` is the frozen binary,
not a Python interpreter, so subprocess invocations fail.  The
in-process helpers use ``django.core.management.call_command()`` instead.

**Boot sequence requirement (frozen only):** ``django.setup()`` MUST be
called before any ``call_command()`` invocation.  The caller
(``run_manager.py``) is responsible for correct ordering:

    1. ``django.setup()``
    2. ``run_migrations_inprocess()``
    3. ``run_collectstatic_inprocess()``

**The first admin's variables:** the child processes get a copy of the
environment without any ``SETHLANS_ADMIN_*`` variable (the admin password
among them). The manager's own process keeps them until its first-start
step reads and removes them, after these children have run.

**Failure text:** a failed in-process run reports the exception class and
only the first line of its message (``error_text.short_error``), never
the full database error.
"""

import logging
import os
import subprocess
import sys

from sethlans_manager.error_text import short_error
from shared.manager_exit_codes import EXIT_FIRST_START_FAILED

logger = logging.getLogger(__name__)

ADMIN_ENV_PREFIX = "SETHLANS_ADMIN_"


def child_env() -> dict:
    """A copy of ``os.environ`` without the ``SETHLANS_ADMIN_*`` variables."""
    return {name: value for name, value in os.environ.items() if not name.upper().startswith(ADMIN_ENV_PREFIX)}


# --- Subprocess runners (source / development mode) ----------------------

def run_migrations_subprocess(manage_py: str) -> None:
    """Apply pending database migrations via subprocess."""
    print("--- Applying database migrations... ---")
    try:
        subprocess.run(
            [sys.executable, manage_py, "migrate"],
            check=True,
            env=child_env(),
        )
        print("--- Migrations applied successfully. ---")
    except subprocess.CalledProcessError as e:
        print(
            f"\n[ERROR] Database migration failed. Error: {e}",
            file=sys.stderr,
        )
        sys.exit(EXIT_FIRST_START_FAILED)


def run_collectstatic_subprocess(manage_py: str) -> None:
    """Collect static files via subprocess."""
    print("--- Collecting static files... ---")
    try:
        subprocess.run(
            [sys.executable, manage_py, "collectstatic", "--noinput"],
            check=True,
            env=child_env(),
        )
        print("--- Static files collected. ---")
    except subprocess.CalledProcessError as e:
        print(
            f"\n[WARNING] collectstatic failed: {e}",
            file=sys.stderr,
        )


# --- In-process runners (frozen / PyInstaller mode) ----------------------

def run_migrations_inprocess() -> None:
    """Apply pending database migrations in-process.

    Calls ``django.core.management.call_command('migrate', '--noinput')``.
    Aborts the process with exit 11 (a failed first start) on failure,
    since migrations are required for a functional manager.
    """
    from django.core.management import call_command

    print("--- Applying database migrations (in-process)... ---")
    try:
        call_command('migrate', '--noinput')
        print("--- Migrations applied successfully. ---")
    except Exception as exc:
        reason = short_error(exc)
        logger.error("Database migration failed: %s", reason)
        print(
            f"\n[ERROR] Database migration failed: {reason}",
            file=sys.stderr,
        )
        sys.exit(EXIT_FIRST_START_FAILED)


def run_collectstatic_inprocess() -> None:
    """Collect static files in-process.

    Calls ``django.core.management.call_command('collectstatic',
    '--noinput')``.  Logs a warning on failure but does not abort --
    the manager can still serve API endpoints without collected statics.
    """
    from django.core.management import call_command

    print("--- Collecting static files (in-process)... ---")
    try:
        call_command('collectstatic', '--noinput')
        print("--- Static files collected. ---")
    except Exception as exc:
        reason = short_error(exc)
        print(
            f"\n[WARNING] collectstatic failed: {reason}",
            file=sys.stderr,
        )
        logger.warning("collectstatic failed: %s", reason)
