# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The environment the launcher gives its children (FR-LN-7, FR-SF-1).

:func:`prepare_launcher_environment` runs once at launcher start:

* it removes the setup secrets from ``os.environ``, so no child and no
  Caddy inherits them (FR-LN-7): every ``SETHLANS_SETUP_*`` and every
  ``SETHLANS_ADMIN_*`` variable (the whole prefix, as
  ``sethlans_manager.migration_runner.child_env`` does: FR-LN-7 names the
  two password variables, and a stray ``SETHLANS_ADMIN_USERNAME`` alone
  would make every first start fail with exit 11), plus the tray's
  ``SETHLANS_TRAY_IPC_SECRET``, which only the tray gets, through
  ``extra``;
* it exports the absolute ``SETHLANS_SETTINGS_FILE`` and
  ``SETHLANS_SETTINGS_DIR``, so every child resolves the same
  ``sethlans.ini`` and settings folder as the launcher.

It returns the removed values. The one child allowed to see them is
``run_manager --setup --answers`` (unattended setup, chunk 8), which gets
them through ``extra``; until then the caller drops them.

:func:`child_env` builds each child's environment: a copy of
``os.environ``, scrubbed again (nothing put back since start leaks),
the two settings variables, then ``extra`` (``SETHLANS_SETUP_PORT`` for
setup mode is added here, after the scrub).

Names are compared in upper case: Windows environment names are
case-insensitive, and on POSIX dropping a differently-cased copy is
harmless.
"""
from __future__ import annotations

import os
from typing import Mapping, MutableMapping, Optional

from shared import settings_file

SCRUB_PREFIXES = ("SETHLANS_SETUP_", "SETHLANS_ADMIN_")
TRAY_SECRET_ENV = "SETHLANS_TRAY_IPC_SECRET"
SCRUB_NAMES = frozenset({TRAY_SECRET_ENV})


def is_scrubbed(name: str) -> bool:
    """True when ``name`` is a setup secret no child may inherit (FR-LN-7)."""
    upper = name.upper()
    return upper in SCRUB_NAMES or upper.startswith(SCRUB_PREFIXES)


def scrub(environ: MutableMapping[str, str]) -> dict:
    """Remove the setup secrets from ``environ`` in place; return what was removed."""
    removed = {name: environ[name] for name in list(environ) if is_scrubbed(name)}
    for name in removed:
        del environ[name]
    return removed


def settings_location() -> dict:
    """The absolute ``SETHLANS_SETTINGS_FILE`` and ``SETHLANS_SETTINGS_DIR``."""
    return {
        settings_file.SETTINGS_FILE_ENV: os.path.abspath(settings_file.settings_path()),
        settings_file.SETTINGS_DIR_ENV: os.path.abspath(settings_file.get_settings_dir()),
    }


def prepare_launcher_environment() -> dict:
    """Scrub ``os.environ`` and export the settings location, once at start.

    Returns the removed values (see the module doc).
    """
    removed = scrub(os.environ)
    os.environ.update(settings_location())
    return removed


def child_env(extra: Optional[Mapping[str, str]] = None) -> dict:
    """A child's environment: scrubbed ``os.environ`` + settings location + ``extra``."""
    env = dict(os.environ)
    scrub(env)
    env.update(settings_location())
    if extra:
        env.update(extra)
    return env
