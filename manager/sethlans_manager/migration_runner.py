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
"""

import logging
import subprocess
import sys

logger = logging.getLogger(__name__)


# --- Subprocess runners (source / development mode) ----------------------

def run_migrations_subprocess(manage_py: str) -> None:
    """Apply pending database migrations via subprocess."""
    print("--- Applying database migrations... ---")
    try:
        subprocess.run(
            [sys.executable, manage_py, "migrate"],
            check=True,
        )
        print("--- Migrations applied successfully. ---")
    except subprocess.CalledProcessError as e:
        print(
            f"\n[ERROR] Database migration failed. Error: {e}",
            file=sys.stderr,
        )
        sys.exit(1)


def run_collectstatic_subprocess(manage_py: str) -> None:
    """Collect static files via subprocess."""
    print("--- Collecting static files... ---")
    try:
        subprocess.run(
            [sys.executable, manage_py, "collectstatic", "--noinput"],
            check=True,
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
    Aborts the process on failure since migrations are required for a
    functional manager.
    """
    from django.core.management import call_command

    print("--- Applying database migrations (in-process)... ---")
    try:
        call_command('migrate', '--noinput')
        print("--- Migrations applied successfully. ---")
    except Exception as exc:
        print(
            f"\n[ERROR] Database migration failed: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)


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
        print(
            f"\n[WARNING] collectstatic failed: {exc}",
            file=sys.stderr,
        )
        logger.warning("collectstatic failed: %s", exc)
