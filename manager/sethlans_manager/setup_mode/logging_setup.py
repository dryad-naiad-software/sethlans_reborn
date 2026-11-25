# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Logging for setup mode: the console and ``<settings>/logs/setup-mode.log``.

Setup mode logs into the settings folder, never into a data folder: the
data folder is one of the answers, and the run-time logging module would
create (and check) the old one. The folder is made owner-only when
missing, and so is the log file (:class:`OwnerOnlyRotatingFileHandler`):
it is created owner-only from birth before the handler opens it, at start
and after each rollover; the rotated copies are renames of it and keep
that. Request bodies are never logged: no handler here logs requests, and
the database drivers stay at WARNING.
"""
import logging.config
import logging.handlers
import os
from pathlib import Path

from shared import owner_only_acl
from shared.settings_file import get_settings_dir
from shared.settings_file_io import ensure_owner_only_dir, make_owner_only

LOG_FILE_NAME = "setup-mode.log"


def logs_dir() -> Path:
    return get_settings_dir() / "logs"


def ensure_owner_only_file(path) -> None:
    """Create ``path`` empty and owner-only, or make an existing one owner-only.

    Raises ``OSError`` (``SettingsPermissionError`` when owner-only cannot
    be verified, ``PermissionError`` for a file owned by another account).
    """
    try:
        os.close(owner_only_acl.create_owner_only_file(path))
        return
    except FileExistsError:
        pass
    if not owner_only_acl.is_owned_by_current_user(path):
        raise PermissionError(f"{path} is not owned by the current user")
    if not owner_only_acl.verify_owner_only(path):
        make_owner_only(path)


class OwnerOnlyRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """A ``RotatingFileHandler`` whose file is owner-only before it is opened."""

    def _open(self):
        ensure_owner_only_file(self.baseFilename)
        return super()._open()


def build_logging() -> dict:
    ensure_owner_only_dir(get_settings_dir())  # the settings folder itself first
    folder = ensure_owner_only_dir(logs_dir())
    handlers = ["console", "file"]
    quiet = {"handlers": handlers, "level": "WARNING", "propagate": False}
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "[{asctime}] [{levelname}] [{name}] {message}",
                "style": "{",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "console": {"class": "logging.StreamHandler", "formatter": "standard"},
            "file": {
                "class": "sethlans_manager.setup_mode.logging_setup.OwnerOnlyRotatingFileHandler",
                "filename": str(folder / LOG_FILE_NAME),
                "formatter": "standard",
                "maxBytes": 1024 * 1024,
                "backupCount": 3,
                "encoding": "utf-8",
            },
        },
        "loggers": {
            "": {"handlers": handlers, "level": "INFO"},
            "django.db.backends": quiet,
            "pymysql": quiet,
            "psycopg": quiet,
        },
    }


def configure(config: dict) -> None:
    logging.config.dictConfig(config)
