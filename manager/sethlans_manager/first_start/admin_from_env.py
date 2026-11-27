# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
FR-FS-2: the first admin from the environment (Docker and scripts).

``SETHLANS_ADMIN_USERNAME`` with ``SETHLANS_ADMIN_PASSWORD_FILE`` (a Docker
secret) or ``SETHLANS_ADMIN_PASSWORD``, and the optional
``SETHLANS_ADMIN_EMAIL``. All four are read and removed from
``os.environ`` first, every time this step runs, so no child process the
manager starts after it sees them. The ``migrate`` and ``collectstatic``
child processes of a source-mode start run before this step;
``migration_runner`` gives them a copy of the environment without any
``SETHLANS_ADMIN_*`` variable. The ``--dev`` watchdog parent never runs
this step: it keeps the variables and hands them to each manager child it
starts, and that child removes them.

* An active superuser exists already: the values are ignored (INFO).
* Otherwise the username, email and password are checked (Django's
  username rules after the same normalization as setup mode, an email of
  at most 254 characters that ``validate_email`` accepts, and the
  configured password validators for an unsaved ``User``); anything wrong
  is exit 11 with the messages. A username that is taken is exit 12; the
  message says when the account is an admin that is not active.
* The superuser is created; only the username is logged.

The password file is read as UTF-8 and one trailing line end is dropped
(secret files usually end with one). When both password variables are
set, the file wins and a WARNING says so.
"""
import logging
import os
from typing import Mapping, MutableMapping, Optional

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.views.decorators.debug import sensitive_variables

from sethlans_manager.first_start.common import FirstStartError, clean_username, email_problems, usable_admin_exists
from shared.manager_exit_codes import EXIT_FIRST_START_FAILED, EXIT_NO_ADMIN

logger = logging.getLogger(__name__)

USERNAME_ENV = "SETHLANS_ADMIN_USERNAME"
PASSWORD_ENV = "SETHLANS_ADMIN_PASSWORD"
PASSWORD_FILE_ENV = "SETHLANS_ADMIN_PASSWORD_FILE"
EMAIL_ENV = "SETHLANS_ADMIN_EMAIL"
ADMIN_ENV_NAMES = (USERNAME_ENV, PASSWORD_ENV, PASSWORD_FILE_ENV, EMAIL_ENV)
MAX_PASSWORD_FILE_BYTES = 64 * 1024


def _failed(reason: str) -> FirstStartError:
    return FirstStartError(EXIT_FIRST_START_FAILED, reason)


def take_admin_env(environ: MutableMapping[str, str]) -> dict:
    """Remove the four variables from ``environ``; return their non-empty values."""
    values = {name: environ.pop(name, None) for name in ADMIN_ENV_NAMES}
    return {name: value for name, value in values.items() if value}


def _read_password_file(path: str) -> str:
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_PASSWORD_FILE_BYTES + 1)
    except OSError as exc:
        raise _failed(f"{PASSWORD_FILE_ENV} ({path}) cannot be read: {exc.strerror or type(exc).__name__}") from None
    if len(data) > MAX_PASSWORD_FILE_BYTES:
        raise _failed(f"{PASSWORD_FILE_ENV} ({path}) is larger than {MAX_PASSWORD_FILE_BYTES} bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise _failed(f"{PASSWORD_FILE_ENV} ({path}) is not UTF-8 text") from None
    if text.endswith("\r\n"):
        return text[:-2]
    return text[:-1] if text.endswith("\n") else text


@sensitive_variables("values")
def _password(values: Mapping[str, str]) -> str:
    path = values.get(PASSWORD_FILE_ENV)
    if path is None:
        return values[PASSWORD_ENV]
    if PASSWORD_ENV in values:
        logger.warning("Both %s and %s are set; using the file", PASSWORD_FILE_ENV, PASSWORD_ENV)
    return _read_password_file(path)


@sensitive_variables("password")
def _problems(username_problems: list, username: str, email: str, password: str) -> list:
    """All the problems: ``username_problems`` from ``clean_username``, the email's and the password's."""
    problems = list(username_problems) + email_problems(email, EMAIL_ENV)
    try:
        validate_password(password, user=get_user_model()(username=username, email=email))
    except ValidationError as exc:
        problems.extend(str(message) for message in exc.messages)
    return problems


def _taken_reason(username: str) -> str:
    """The exit-12 text for a username that is taken (an active admin would have skipped this step)."""
    taken = get_user_model().objects.filter(username=username, is_superuser=True, is_active=False).exists()
    if taken:
        return (f"the account {username!r} from {USERNAME_ENV} already exists as an admin that is not active; "
                "run Reset setup and tick 'Replace its password?' to make it active")
    return f"the account {username!r} from {USERNAME_ENV} already exists and is not an admin"


@sensitive_variables("password")
def _create(username: str, email: str, password: str) -> None:
    try:
        with transaction.atomic():
            get_user_model().objects.create_superuser(username, email, password)
    except IntegrityError:
        raise FirstStartError(EXIT_NO_ADMIN, _taken_reason(username)) from None


@sensitive_variables("values", "password")
def apply_env_admin(environ: Optional[MutableMapping[str, str]] = None) -> bool:
    """FR-FS-2. True when an admin account was created from the environment."""
    values = take_admin_env(os.environ if environ is None else environ)
    has_password = PASSWORD_ENV in values or PASSWORD_FILE_ENV in values
    if USERNAME_ENV not in values and not has_password:
        return False
    if usable_admin_exists():
        logger.info("An admin account exists already; %s and the password variables are ignored", USERNAME_ENV)
        return False
    if USERNAME_ENV not in values:
        raise _failed(f"{PASSWORD_FILE_ENV} or {PASSWORD_ENV} is set without {USERNAME_ENV}")
    if not has_password:
        raise _failed(f"{USERNAME_ENV} is set without {PASSWORD_FILE_ENV} or {PASSWORD_ENV}")
    username, username_problems = clean_username(values[USERNAME_ENV])
    email = values.get(EMAIL_ENV, "").strip()
    password = _password(values)
    problems = _problems(username_problems, username, email, password)
    if problems:
        raise _failed(f"the admin account from {USERNAME_ENV} is not valid: " + "; ".join(problems))
    _create(username, email, password)
    logger.info("Created the admin account %r from %s", username, USERNAME_ENV)
    return True
