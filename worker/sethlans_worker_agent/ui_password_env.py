# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The worker page password from the environment (Docker and scripts).

A worker that never runs setup gets its page password from
``SETHLANS_WORKER_UI_PASSWORD_FILE`` (a Docker secret) or
``SETHLANS_WORKER_UI_PASSWORD``, the way the manager's first admin comes
from ``SETHLANS_ADMIN_*``. ``agent.main`` calls ``apply_or_exit`` right
after logging is set up, every start, before the worker page, Caddy, the
enrollment, the GPU probes or any render starts; the ``--setup-*``
commands never call it (they take ``ui_password`` from their JSON input).

* Both variables are read and removed from ``os.environ`` first, so no
  child process the worker starts later sees them.
* Neither set (or both empty): nothing happens.
* Both set: the file wins and a WARNING says so.
* The password must pass the same rules as ``--setup-apply``
  (``web_ui.password_rules`` against ``socket.gethostname()``).
* The stored ``worker.ui_password_hash`` already matches: nothing is
  written. Otherwise only the new hash is saved, with the same format and
  ``config_store`` write as ``--setup-apply``. The environment owns the
  password: a changed secret replaces the old hash on the next start.
* A password that breaks the rules, a password file that cannot be read,
  is larger than 64 KiB or is not UTF-8, a settings file that cannot be
  read, or a save that fails: the reasons are logged and the worker exits
  with ``UI_PASSWORD_FAILED`` (5). It never falls back to "no password".

The password file is read as UTF-8 and one trailing line end is dropped
(secret files usually end with one). The password is never logged.

Worker exit codes: ``enrollment_start.ENROLL_FAILED`` is 4;
``UI_PASSWORD_FAILED`` here is 5.
"""
import logging
import os
import socket
import sys
from typing import Mapping, MutableMapping, Optional

from sethlans_worker_agent import config_store
from sethlans_worker_agent.web_ui import auth, password_hash, password_rules

logger = logging.getLogger(__name__)

PASSWORD_ENV = 'SETHLANS_WORKER_UI_PASSWORD'
PASSWORD_FILE_ENV = 'SETHLANS_WORKER_UI_PASSWORD_FILE'
ENV_NAMES = (PASSWORD_FILE_ENV, PASSWORD_ENV)
MAX_PASSWORD_FILE_BYTES = 64 * 1024

UI_PASSWORD_FAILED = 5

SET = 'set'
UNCHANGED = 'unchanged'


class UiPasswordEnvError(Exception):
    """The password from the environment cannot be used; the text says why (never the password)."""


def take_env(environ: MutableMapping[str, str]) -> dict:
    """Remove both variables from ``environ``; return their non-empty values."""
    values = {name: environ.pop(name, None) for name in ENV_NAMES}
    return {name: value for name, value in values.items() if value}


def _read_password_file(path: str) -> str:
    try:
        with open(path, 'rb') as handle:
            data = handle.read(MAX_PASSWORD_FILE_BYTES + 1)
    except OSError as exc:
        raise UiPasswordEnvError(
            f"{PASSWORD_FILE_ENV} ({path}) cannot be read: {exc.strerror or type(exc).__name__}") from None
    if len(data) > MAX_PASSWORD_FILE_BYTES:
        raise UiPasswordEnvError(f"{PASSWORD_FILE_ENV} ({path}) is larger than {MAX_PASSWORD_FILE_BYTES} bytes")
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        raise UiPasswordEnvError(f"{PASSWORD_FILE_ENV} ({path}) is not UTF-8 text") from None
    if text.endswith('\r\n'):
        return text[:-2]
    return text[:-1] if text.endswith('\n') else text


def _password(values: Mapping[str, str]) -> tuple:
    """``(password, name of the variable it came from)``."""
    path = values.get(PASSWORD_FILE_ENV)
    if path is None:
        return values[PASSWORD_ENV], PASSWORD_ENV
    if PASSWORD_ENV in values:
        logger.warning("Both %s and %s are set; using the file", PASSWORD_FILE_ENV, PASSWORD_ENV)
    return _read_password_file(path), PASSWORD_FILE_ENV


def _stored_hash() -> Optional[str]:
    try:
        return auth.current_hash()
    except config_store.ConfigReadError as error:
        raise UiPasswordEnvError(f"the worker's settings file could not be read: {error}") from None


def _save(password: str) -> None:
    try:
        config_store.set_many([(auth.HASH_KEY, password_hash.make(password))])
    except (config_store.ConfigLockTimeoutError, OSError) as error:
        raise UiPasswordEnvError(f"the worker's settings could not be saved: {error}") from None
    auth.clear_cache()


def apply_from_env(environ: Optional[MutableMapping[str, str]] = None) -> Optional[str]:
    """``SET``, ``UNCHANGED``, or ``None`` when neither variable is set; ``UiPasswordEnvError`` when unusable."""
    values = take_env(os.environ if environ is None else environ)
    if not values:
        return None
    password, source = _password(values)
    found = password_rules.problems(password, socket.gethostname())
    if found:
        raise UiPasswordEnvError(f"the worker page password from {source} is not valid: " + ' '.join(found))
    stored = _stored_hash()
    if stored is not None and password_hash.verify(password, stored):
        logger.info("Worker page password unchanged (%s matches the stored one).", source)
        return UNCHANGED
    _save(password)
    logger.info("Worker page password set from %s.", source)
    return SET


def apply_or_exit(environ: Optional[MutableMapping[str, str]] = None) -> Optional[str]:
    """``apply_from_env``; on ``UiPasswordEnvError`` log the reason and exit ``UI_PASSWORD_FAILED``."""
    try:
        return apply_from_env(environ)
    except UiPasswordEnvError as error:
        logger.critical("Worker page password from the environment cannot be used; exiting with code %d: %s",
                        UI_PASSWORD_FAILED, error)
        sys.exit(UI_PASSWORD_FAILED)
