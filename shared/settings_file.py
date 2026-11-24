# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Sethlans settings file, ``sethlans.ini``: location, reads and writes.

Django-free and stdlib-only, so it runs before ``django.setup()``.

Location (:func:`settings_path`, the only resolver), first match wins:

1. ``SETHLANS_SETTINGS_FILE`` (absolute path of the file);
2. ``SETHLANS_SETTINGS_DIR/sethlans.ini``;
3. source mode with ``SETHLANS_MANAGER_DATA_DIR`` set:
   ``<that>/sethlans.ini`` (Docker: ``/data/sethlans.ini``);
4. frozen: ``get_settings_dir()/sethlans.ini`` (the OS per-user folder);
5. source: ``<repo>/manager/sethlans.ini``.

:func:`get_settings_dir` is the settings folder: ``SETHLANS_SETTINGS_DIR``,
else the OS per-user folder, computed directly. It does **not** follow
the settings file: ``SETHLANS_SETTINGS_FILE``, ``SETHLANS_MANAGER_DATA_DIR``
and source mode move ``sethlans.ini`` but never this folder. It holds the
identity files (``shared.frozen_paths.get_identity_dir``), ``setup/`` and
the launcher's lock and IPC markers.

Reads and writes use ``configparser.RawConfigParser(interpolation=None)``,
so ``%`` in a value is literal. A file not owned by this process's user
is refused (``SettingsFileError``), and errors never quote file content.
Every write replaces the whole file atomically and owner-only
(``shared.settings_file_io``) under a cross-process lock on
``sethlans.ini.lock``. Reads need no lock.
"""
import configparser
import datetime
import os
import platform
from pathlib import Path
from typing import Callable, Mapping, Optional

from shared import frozen_paths, owner_only_acl
from shared.file_lock import file_lock
from shared.settings_file_io import atomic_replace, ensure_owner_only_dir, read_limited
from shared.settings_parse import (  # noqa: F401 - SettingsFileError, new_parser re-exported
    SettingsFileError,
    check_name,
    check_value,
    new_parser,
    parse_settings_bytes,
    serialise,
)

SETTINGS_FILENAME = "sethlans.ini"
SETTINGS_FILE_ENV = "SETHLANS_SETTINGS_FILE"
SETTINGS_DIR_ENV = "SETHLANS_SETTINGS_DIR"
MANAGER_DATA_DIR_ENV = "SETHLANS_MANAGER_DATA_DIR"
SETUP_SUBDIR = "setup"
MAX_FILE_BYTES = 1024 * 1024

SECTION_SETUP = "setup"
KEY_SETUP_VERSION = "version"
KEY_SETUP_MODE = "mode"
KEY_SETUP_COMPLETED_AT = "completed_at"
SETUP_FORMAT_VERSION = "1"
SETUP_MODES = frozenset({"manager", "manager_worker", "worker"})

SECTION_PATHS = "paths"
KEY_DATA_DIR = "data_dir"

SECTION_SERVER = "server"
KEY_SERVER_HOST = "host"
KEY_SERVER_PORT = "port"
KEY_WAITRESS_PUBLIC_PORT = "waitress_loopback_port_public"
KEY_WAITRESS_INTERNAL_PORT = "waitress_loopback_port_internal"

SECTION_DATABASE = "database"
SECTION_ENROLLMENT = "enrollment"
SECTION_TLS = "tls"
SECTION_FIRST_ADMIN = "first_admin"
SECTION_LAUNCHER = "launcher"

# The wizard-era keys of ``[setup]``. The same section also carries the
# setup-finished keys, which a normal start must never drop.
SETUP_WIZARD_KEYS = ("token", "session_id")


def get_settings_dir() -> Path:
    """Return the settings folder: ``SETHLANS_SETTINGS_DIR`` or the OS default.

    Windows ``%LOCALAPPDATA%\\Sethlans`` (``USERPROFILE`` / home
    fallbacks), macOS ``~/Library/Application Support/Sethlans``, else
    ``$XDG_DATA_HOME/sethlans`` or ``~/.local/share/sethlans``. Never
    derived from where ``sethlans.ini`` is (``SETHLANS_SETTINGS_FILE``,
    ``SETHLANS_MANAGER_DATA_DIR``, source mode) nor from a component's
    data folder.
    """
    override = frozen_paths.env_path(SETTINGS_DIR_ENV)
    if override is not None:
        return override
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            userprofile = os.environ.get("USERPROFILE")
            if userprofile:
                base = os.path.join(userprofile, "AppData", "Local")
            else:
                base = str(Path.home() / "AppData" / "Local")
        return Path(base) / "Sethlans"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Sethlans"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "sethlans"
    return Path.home() / ".local" / "share" / "sethlans"


def settings_path() -> Path:
    """Return the path of ``sethlans.ini`` (resolution order in the module doc)."""
    explicit = frozen_paths.env_path(SETTINGS_FILE_ENV)
    if explicit is not None:
        return explicit
    folder = frozen_paths.env_path(SETTINGS_DIR_ENV)
    if folder is not None:
        return folder / SETTINGS_FILENAME
    if frozen_paths.is_frozen():
        return get_settings_dir() / SETTINGS_FILENAME
    manager_data = frozen_paths.env_path(MANAGER_DATA_DIR_ENV)
    if manager_data is not None:
        return manager_data / SETTINGS_FILENAME
    return frozen_paths.get_manager_dir() / SETTINGS_FILENAME


def lock_path(path) -> Path:
    """The sidecar lock file guarding writes to ``path``."""
    path = Path(path)
    return path.with_name(path.name + ".lock")


def _resolve(path) -> Path:
    return settings_path() if path is None else Path(path)


def read_settings(path=None) -> configparser.RawConfigParser:
    """Parse the settings file; a missing file gives an empty parser.

    Raises ``SettingsFileError`` when the file exists but is not owned by
    this process's user (``owner_only_acl.is_owned_by_current_user``), is
    unreadable, larger than ``MAX_FILE_BYTES``, not UTF-8 or not valid
    INI. The message never contains file content.
    """
    path = _resolve(path)
    try:
        data = read_limited(path, MAX_FILE_BYTES)
    except FileNotFoundError:
        return new_parser()
    except OSError as exc:
        raise SettingsFileError(f"Cannot read {path}: {type(exc).__name__}: {exc.strerror}") from None
    if not owner_only_acl.is_owned_by_current_user(path):
        raise SettingsFileError(f"{path} is not owned by the current user; refusing to trust it")
    if len(data) > MAX_FILE_BYTES:
        raise SettingsFileError(f"{path} is larger than {MAX_FILE_BYTES} bytes")
    return parse_settings_bytes(data, path)


def configured_data_root(path=None) -> Optional[Path]:
    """Return ``[paths] data_dir``, or ``None`` when it is not set.

    A missing, unreadable or unparsable file counts as not set (setup is
    then not finished either, see :func:`setup_finished`). A relative
    value raises ``ValueError``.
    """
    try:
        parser = read_settings(path)
    except SettingsFileError:
        return None
    raw = parser.get(SECTION_PATHS, KEY_DATA_DIR, fallback="").strip()
    if not raw:
        return None
    root = Path(raw)
    if not root.is_absolute():
        raise ValueError(f"[{SECTION_PATHS}] {KEY_DATA_DIR} must be an absolute path, got: {raw}")
    return root


def update_settings(mutate: Callable[[configparser.RawConfigParser], bool], path=None) -> bool:
    """Read-modify-write the settings file under its lock.

    ``mutate`` changes the parser in place and returns True when it
    changed something; only then is the file replaced. The settings folder
    is created owner-only when missing. Returns what ``mutate`` returned.
    Raises ``OSError`` (``SettingsFileError`` for a corrupt file,
    ``SettingsPermissionError`` when owner-only cannot be verified) or
    ``shared.file_lock.FileLockTimeoutError``.
    """
    path = _resolve(path)
    ensure_owner_only_dir(path.parent)
    with file_lock(lock_path(path)):
        parser = read_settings(path)
        changed = bool(mutate(parser))
        if changed:
            atomic_replace(path, serialise(parser))
    return changed


def set_values(section: str, values: Mapping[str, object], path=None) -> Path:
    """Set ``values`` in ``[section]``, keeping every other key. Returns the path."""
    check_name("section", section)
    checked = {check_name("key", key): check_value(key, value) for key, value in values.items()}

    def mutate(parser: configparser.RawConfigParser) -> bool:
        if not parser.has_section(section):
            parser.add_section(section)
        for key, value in checked.items():
            parser.set(section, key, value)
        return True

    path = _resolve(path)
    update_settings(mutate, path)
    return path


def _remove(mutate, path) -> bool:
    path = _resolve(path)
    if not path.exists():
        return False
    return update_settings(mutate, path)


def remove_key(section: str, key: str, path=None) -> bool:
    """Remove ``[section] key``; True when it was there. A missing file is left missing."""
    return _remove(lambda parser: parser.has_section(section) and parser.remove_option(section, key), path)


def remove_section(section: str, path=None) -> bool:
    """Remove ``[section]``; True when it was there. A missing file is left missing."""
    return _remove(lambda parser: parser.remove_section(section), path)


def remove_setup_wizard_keys(path=None) -> bool:
    """Drop :data:`SETUP_WIZARD_KEYS` from ``[setup]``; keep the rest.

    The section itself goes only when nothing else is left in it, so the
    setup-finished keys survive. A missing file is left missing. Returns
    True when the file changed.
    """
    def mutate(parser: configparser.RawConfigParser) -> bool:
        if not parser.has_section(SECTION_SETUP):
            return False
        changed = False
        for key in SETUP_WIZARD_KEYS:
            changed = parser.remove_option(SECTION_SETUP, key) or changed
        if not parser.options(SECTION_SETUP):
            parser.remove_section(SECTION_SETUP)
            changed = True
        return changed

    return _remove(mutate, path)


def setup_finished(path=None) -> Optional[str]:
    """Return the setup mode when setup is finished, else ``None``. Never raises.

    Finished means ``[setup] version = 1``, a ``mode`` in ``SETUP_MODES``
    and a ``completed_at`` that ``datetime.fromisoformat`` parses. A
    missing, unreadable or unparsable file, or any other value, is not
    finished.
    """
    try:
        parser = read_settings(path)
        if not parser.has_section(SECTION_SETUP):
            return None
        setup = parser[SECTION_SETUP]
        if setup.get(KEY_SETUP_VERSION, "").strip() != SETUP_FORMAT_VERSION:
            return None
        mode = setup.get(KEY_SETUP_MODE, "").strip()
        if mode not in SETUP_MODES:
            return None
        datetime.datetime.fromisoformat(setup.get(KEY_SETUP_COMPLETED_AT, "").strip())
        return mode
    except Exception:  # noqa: BLE001 - any failure means "not finished"
        return None
