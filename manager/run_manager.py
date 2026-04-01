# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A user-friendly script to run the Django development server for the Sethlans Manager.

This script first applies any pending database migrations and then starts the server
on the port specified in 'manager.ini' or the 'SETHLANS_MANAGER_PORT' environment
variable.

This provides a simple, one-command entry point for users.
"""
import os
import sys
import subprocess
import configparser
from pathlib import Path


def get_manager_port():
    """
    Gets the manager port, respecting the override hierarchy.
    1. Environment variable (SETHLANS_MANAGER_PORT)
    2. manager.ini file
    3. Hardcoded default (7075)
    """
    # 1. Check environment variable first
    port = os.getenv('SETHLANS_MANAGER_PORT')
    if port:
        return port

    # 2. Check config file
    try:
        config = configparser.ConfigParser()
        config_file_path = Path(__file__).resolve().parent / 'manager.ini'
        if config_file_path.exists():
            config.read(config_file_path)
            if config.has_option('server', 'port'):
                return config.get('server', 'port')
    except Exception as e:
        print(f"[WARNING] Could not read manager.ini: {e}", file=sys.stderr)

    # 3. Fallback to default
    return '7075'


MANAGER_DIR = Path(__file__).resolve().parent
MANAGE_PY = str(MANAGER_DIR / 'manage.py')


if __name__ == "__main__":
    port = get_manager_port()

    # --- Migration Step ---
    print("--- Applying database migrations... ---")
    try:
        subprocess.run(
            [sys.executable, MANAGE_PY, "migrate"],
            check=True,
        )
        print("--- Migrations applied successfully. ---")
    except subprocess.CalledProcessError as e:
        print(
            f"\n[ERROR] Database migration failed. Error: {e}",
            file=sys.stderr,
        )
        sys.exit(1)

    # --- Run Server Step ---
    print(f"\n--- Starting Sethlans Manager on port {port} ---")
    print("--- To stop the server, press CTRL+C ---")

    try:
        subprocess.run(
            [sys.executable, MANAGE_PY, "runserver", port],
            check=True,
        )
    except KeyboardInterrupt:
        print("\n--- Sethlans Manager stopped ---")
    except subprocess.CalledProcessError as e:
        print(
            f"\n[ERROR] The manager server failed to start. Error: {e}",
            file=sys.stderr,
        )
        sys.exit(1)