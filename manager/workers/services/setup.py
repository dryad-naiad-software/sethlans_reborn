# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared service functions for setup wizard and ``setup_auth`` CLI.

Pure Python functions with no request/response dependencies.  Both the
wizard REST endpoints and the management command call into this module.
"""

import configparser
import hashlib
import logging
import os
import platform
import secrets
import subprocess
import tempfile
from pathlib import Path

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management import call_command

logger = logging.getLogger(__name__)

User = get_user_model()


def generate_secret_key() -> str:
    """Return a cryptographically random secret key."""
    return secrets.token_urlsafe(50)


def generate_enrollment_key() -> str:
    """Generate a Crockford base32 enrollment key and persist it.

    Calls ``enrollment_key.generate_key()`` and writes the result to
    the ``ManagerSettings`` singleton DB row (pk=1).

    Returns the canonical 16-char key string.
    """
    from workers import enrollment_key as ek
    from workers.models import ManagerSettings

    key = ek.generate_key()
    row, _ = ManagerSettings.objects.get_or_create(pk=1)
    row.enrollment_key = key
    row.save(
        update_fields=["enrollment_key", "enrollment_key_updated_at"],
    )
    return key


def create_admin_user(
    username: str, email: str, password: str,
) -> "User":
    """Create a Django superuser after validating the password.

    Raises ``ValidationError`` if the password fails Django validators
    or if a superuser with the given username already exists.

    Returns the created ``User`` instance.
    """
    if User.objects.filter(username=username).exists():
        raise ValidationError("Username already taken.")

    validate_password(password)

    user = User.objects.create_superuser(
        username=username, email=email, password=password,
    )
    logger.info("Superuser '%s' created.", username)
    return user


def write_manager_ini(config_dict: dict, ini_path: Path) -> Path:
    """Merge *config_dict* into ``manager.ini`` via atomic write.

    Keys in *config_dict* use dot notation to address INI
    section/option pairs, e.g. ``{"server.port": "8080"}``.

    Creates the file if it does not exist.  Returns *ini_path*.
    """
    config = configparser.ConfigParser()
    if ini_path.exists():
        config.read(ini_path)

    for dotted_key, value in config_dict.items():
        section, option = dotted_key.split(".", 1)
        if not config.has_section(section):
            config.add_section(section)
        config.set(section, option, str(value))

    _atomic_write_ini(config, ini_path)
    return ini_path


def apply_migrations() -> None:
    """Run Django migrations programmatically."""
    logger.info("Applying database migrations...")
    call_command("migrate", "--noinput")
    logger.info("Migrations applied successfully.")


def validate_db_connection(
    engine: str,
    name: str,
    host: str = "",
    port: str = "",
    user: str = "",
    password: str = "",
    timeout: int = 10,
) -> None:
    """Attempt a database connection with *timeout* seconds.

    Raises ``ConnectionError`` with a human-readable message on
    failure.  Uses backend-specific ``OPTIONS`` for the timeout.
    """
    from django.db import connections
    from django.db.utils import OperationalError

    options = {}
    if "postgresql" in engine:
        options = {"connect_timeout": timeout}
    elif "mysql" in engine:
        options = {"connect_timeout": timeout}
    elif "sqlite" in engine:
        options = {"timeout": timeout}

    db_settings = {
        "ENGINE": engine,
        "NAME": name,
        "HOST": host,
        "PORT": port,
        "USER": user,
        "PASSWORD": password,
        "OPTIONS": options,
    }

    # Use a temporary alias to avoid mutating the default connection.
    alias = "_setup_validation"
    connections.databases[alias] = db_settings
    try:
        conn = connections[alias]
        conn.ensure_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
    except (OperationalError, Exception) as exc:
        raise ConnectionError(
            f"Could not connect to database: {exc}"
        ) from exc
    finally:
        try:
            connections[alias].close()
        except Exception:
            pass
        connections.databases.pop(alias, None)


def verify_admin_authenticates(
    username: str, password: str,
) -> bool:
    """Exercise the full Django auth stack and return success."""
    user = authenticate(username=username, password=password)
    return user is not None and user.is_active


def set_worker_ui_password(
    config_path: Path, password: str,
) -> None:
    """PBKDF2-hash *password* and write to the worker config file.

    Uses the same hashing parameters as the worker's
    ``web_ui.auth`` module (PBKDF2-SHA256, 100 000 iterations,
    16-byte salt).
    """
    salt = os.urandom(16)
    pw_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations=100_000,
    )

    parser = configparser.ConfigParser()
    if config_path.exists():
        parser.read(config_path)
    if not parser.has_section("worker"):
        parser.add_section("worker")
    parser.set("worker", "ui_password_hash", pw_hash.hex())
    parser.set("worker", "ui_password_salt", salt.hex())

    # Remove legacy plaintext fields
    for legacy in ("ui_token", "ui_password"):
        if parser.has_option("worker", legacy):
            parser.remove_option("worker", legacy)

    _atomic_write_ini(parser, config_path)
    logger.info("Worker UI password written to %s", config_path)


def verify_ffmpeg_runs(ffmpeg_path: Path) -> str:
    """Execute ``ffmpeg -version`` and return the version string."""
    ffmpeg_path = Path(ffmpeg_path).resolve()
    if not ffmpeg_path.is_file():
        raise RuntimeError(
            f"FFmpeg binary not found: {ffmpeg_path}"
        )

    try:
        result = subprocess.run(
            [str(ffmpeg_path), "-version"],
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "FFmpeg timed out after 30 seconds."
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"Failed to execute FFmpeg: {exc}"
        ) from exc

    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg exited with code {result.returncode}: "
            f"{result.stderr.strip()}"
        )
    # First line: "ffmpeg version N.N.N ..."
    first_line = result.stdout.split("\n", 1)[0]
    return first_line.strip()


def verify_blender_runs(blender_path: Path) -> str:
    """Execute ``blender --version`` and return the version string."""
    blender_path = Path(blender_path).resolve()
    if not blender_path.is_file():
        raise RuntimeError(
            f"Blender binary not found: {blender_path}"
        )

    try:
        result = subprocess.run(
            [str(blender_path), "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "Blender timed out after 30 seconds."
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"Failed to execute Blender: {exc}"
        ) from exc

    if result.returncode != 0:
        raise RuntimeError(
            f"Blender exited with code {result.returncode}: "
            f"{result.stderr.strip()}"
        )
    # First line: "Blender X.Y.Z"
    first_line = result.stdout.split("\n", 1)[0]
    return first_line.strip()


# ---- Internal helpers ----

def _atomic_write_ini(
    parser: configparser.ConfigParser, path: Path,
) -> None:
    """Write a ConfigParser to *path* atomically.

    Creates a temp file in the same directory, writes, then replaces.
    """
    path = Path(path)
    parent = str(path.parent)
    fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".ini")
    try:
        with os.fdopen(fd, "w") as f:
            parser.write(f)
        os.replace(tmp_path, str(path))
        # Restrict permissions — INI may contain password hashes.
        if platform.system() != "Windows":
            os.chmod(str(path), 0o600)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
