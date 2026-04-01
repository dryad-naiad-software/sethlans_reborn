# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
First-time setup script for the Sethlans Manager.

Generates manager.ini with a secure SECRET_KEY and enrollment key,
runs database migrations, creates an admin user, and optionally
builds the Angular frontend.

Run from anywhere: python manager/setup.py
"""
import configparser
import getpass
import os
import secrets
import subprocess
import sys
from pathlib import Path

MANAGER_DIR = Path(__file__).resolve().parent
CONFIG_PATH = MANAGER_DIR / 'manager.ini'
MANAGE_PY = MANAGER_DIR / 'manage.py'
FRONTEND_DIR = MANAGER_DIR / 'frontend'


def generate_secret_key():
    """Generate a Django-compatible secret key without importing Django."""
    return secrets.token_urlsafe(50)


def setup_config():
    """Create or update manager.ini with security settings."""
    config = configparser.ConfigParser()
    if CONFIG_PATH.exists():
        config.read(CONFIG_PATH)
        print(f"[OK] Found existing {CONFIG_PATH.name}")
    else:
        print(f"[NEW] Creating {CONFIG_PATH.name}")

    if not config.has_section('server'):
        config.add_section('server')
    if not config.has_option('server', 'port'):
        config.set('server', 'port', '7075')

    if not config.has_section('security'):
        config.add_section('security')

    # Generate SECRET_KEY if missing
    if not config.get('security', 'secret_key', fallback=''):
        key = generate_secret_key()
        config.set('security', 'secret_key', key)
        print("[OK] Generated SECRET_KEY")
    else:
        print("[OK] SECRET_KEY already configured")

    # Generate enrollment key if missing
    if not config.get('security', 'enrollment_key', fallback=''):
        enrollment_key = secrets.token_urlsafe(32)
        config.set('security', 'enrollment_key', enrollment_key)
        print(f"[OK] Generated enrollment key")
        print(f"\n{'=' * 60}")
        print(f"  ENROLLMENT KEY (copy to each worker's config.ini):")
        print(f"  {enrollment_key}")
        print(f"{'=' * 60}\n")
    else:
        enrollment_key = config.get('security', 'enrollment_key')
        print("[OK] Enrollment key already configured")

    # Set debug = true for development
    if not config.get('security', 'debug', fallback=''):
        config.set('security', 'debug', 'true')
        print("[OK] Set DEBUG=true (development mode)")

    with open(CONFIG_PATH, 'w') as f:
        config.write(f)

    return enrollment_key


def run_migrations():
    """Run Django database migrations."""
    print("\n--- Running database migrations ---")
    result = subprocess.run(
        [sys.executable, str(MANAGE_PY), 'migrate'],
        cwd=str(MANAGER_DIR),
    )
    if result.returncode != 0:
        print("[ERROR] Migration failed.", file=sys.stderr)
        sys.exit(1)
    print("[OK] Migrations applied")


def create_admin():
    """Create an admin superuser interactively."""
    print("\n--- Create admin account ---")
    print("(Skip with Ctrl+C if admin already exists)\n")
    try:
        username = input("Admin username: ").strip()
        if not username:
            print("[SKIP] No username provided")
            return
        email = input("Admin email (optional): ").strip()
        while True:
            password = getpass.getpass("Admin password: ")
            confirm = getpass.getpass("Confirm password: ")
            if password == confirm:
                break
            print("Passwords don't match. Try again.")

        result = subprocess.run(
            [sys.executable, str(MANAGE_PY), 'createsuperuser',
             '--username', username,
             '--email', email or '',
             '--noinput'],
            cwd=str(MANAGER_DIR),
            env={**os.environ, 'DJANGO_SUPERUSER_PASSWORD': password},
        )
        if result.returncode == 0:
            print(f"[OK] Admin '{username}' created")
        else:
            print("[WARN] Admin creation failed (may already exist)")
    except KeyboardInterrupt:
        print("\n[SKIP] Admin creation skipped")


def build_frontend():
    """Build the Angular frontend if node_modules exist."""
    if not (FRONTEND_DIR / 'node_modules').exists():
        print("\n--- Installing frontend dependencies ---")
        result = subprocess.run(
            ['npm', 'install'],
            cwd=str(FRONTEND_DIR),
            shell=True,
        )
        if result.returncode != 0:
            print("[WARN] npm install failed. Frontend won't be built.")
            return

    print("\n--- Building Angular frontend ---")
    result = subprocess.run(
        ['npm', 'run', 'build'],
        cwd=str(FRONTEND_DIR),
        shell=True,
    )
    if result.returncode == 0:
        print("[OK] Frontend built")
    else:
        print("[WARN] Frontend build failed")
        return

    print("\n--- Collecting static files ---")
    subprocess.run(
        [sys.executable, str(MANAGE_PY), 'collectstatic', '--noinput'],
        cwd=str(MANAGER_DIR),
    )
    print("[OK] Static files collected")


def main():
    print("=" * 60)
    print("  Sethlans Manager - First Time Setup")
    print("=" * 60)

    # Step 1: Config (pre-Django — no settings import)
    setup_config()

    # Step 2: Migrations
    run_migrations()

    # Step 3: Admin user
    create_admin()

    # Step 4: Frontend
    if FRONTEND_DIR.exists():
        build = input("\nBuild Angular frontend? [Y/n]: ").strip().lower()
        if build != 'n':
            build_frontend()
    else:
        print("\n[SKIP] Frontend directory not found")

    print("\n" + "=" * 60)
    print("  Setup complete!")
    print(f"  Start the manager:  python {MANAGE_PY.relative_to(MANAGER_DIR.parent)} runserver 7075")
    print("=" * 60)


if __name__ == '__main__':
    main()
