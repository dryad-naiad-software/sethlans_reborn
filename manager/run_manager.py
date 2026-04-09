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


def get_manager_bind():
    """
    Gets the manager host:port, respecting the override hierarchy.
    1. Environment variables (SETHLANS_MANAGER_HOST, SETHLANS_MANAGER_PORT)
    2. manager.ini file
    3. Hardcoded defaults (0.0.0.0:8080)
    """
    host = os.getenv('SETHLANS_MANAGER_HOST')
    port = os.getenv('SETHLANS_MANAGER_PORT')

    try:
        config = configparser.ConfigParser()
        config_file_path = Path(__file__).resolve().parent / 'manager.ini'
        if config_file_path.exists():
            config.read(config_file_path)
            if not host and config.has_option('server', 'host'):
                host = config.get('server', 'host')
            if not port and config.has_option('server', 'port'):
                port = config.get('server', 'port')
    except Exception as e:
        print(f"[WARNING] Could not read manager.ini: {e}", file=sys.stderr)

    host = host or '0.0.0.0'
    port = port or '8080'
    return f"{host}:{port}"


MANAGER_DIR = Path(__file__).resolve().parent
MANAGE_PY = str(MANAGER_DIR / 'manage.py')


if __name__ == "__main__":
    bind_addr = get_manager_bind()

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
    print(f"\n--- Starting Sethlans Manager on {bind_addr} ---")
    print("--- To stop the server, press CTRL+C ---")

    try:
        subprocess.run(
            [sys.executable, MANAGE_PY, "runserver", bind_addr],
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