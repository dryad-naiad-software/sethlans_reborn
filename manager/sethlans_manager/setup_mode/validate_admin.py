# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Admin section (FR-WZ-5, FR-WZ-6, FR-WZ-11; D6).

* ``keep_existing: true`` keeps the database's admin accounts and makes
  none; the database must hold at least one superuser.
* Otherwise a new first admin: username (1-150 characters of Django's
  username set), optional email, a password that passes Django's
  configured validators for an unsaved ``User`` with that username and
  email. A username already in the database needs
  ``replace_password: true``, else the submit is refused on the username.
* The password is hashed here with ``make_password`` (the default
  hasher, ``pbkdf2_sha256``); only the hash leaves this module.
"""
from typing import Dict, Optional

from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.views.decorators.debug import sensitive_variables

from sethlans_manager.setup_mode import fields
from sethlans_manager.setup_mode.db_probe import AdminAccounts

ACCOUNT_EXISTS = "This account exists. Replace its password?"
NO_ACCOUNT_TO_KEEP = "The chosen database has no admin account to keep."
MAX_PASSWORD_LENGTH = 4096


def _messages(exc: ValidationError):
    return [str(message) for message in exc.messages]


def _username(admin: dict, errors: fields.Errors) -> Optional[str]:
    username = fields.text(admin, "username", "admin.username", errors, required=True, max_length=150,
                           label="The username")
    if username:
        try:
            UnicodeUsernameValidator()(username)
        except ValidationError as exc:
            for message in _messages(exc):
                errors.add("admin.username", message)
            return None
    return username


def _email(admin: dict, errors: fields.Errors) -> str:
    email = fields.text(admin, "email", "admin.email", errors, max_length=254, label="The email address")
    if email:
        try:
            validate_email(email)
        except ValidationError:
            errors.add("admin.email", "Enter a valid email address or leave it empty.")
    return email or ""


@sensitive_variables("admin", "password")
def check_password(password: object, username: str, email: str, field: str, errors: fields.Errors) -> bool:
    """Django's configured validators for an unsaved ``User`` (FR-WZ-6)."""
    from django.contrib.auth.models import User

    if not isinstance(password, str) or not password:
        errors.add(field, "A password is required.")
        return False
    if len(password) > MAX_PASSWORD_LENGTH:
        errors.add(field, f"The password is longer than {MAX_PASSWORD_LENGTH} characters.")
        return False
    try:
        validate_password(password, user=User(username=username or "", email=email or ""))
    except ValidationError as exc:
        for message in _messages(exc):
            errors.add(field, message)
        return False
    return True


@sensitive_variables("answers", "admin", "password")
def first_admin(answers: dict, accounts: Optional[AdminAccounts],
                errors: fields.Errors) -> Optional[Dict[str, str]]:
    """The ``[first_admin]`` keys, or ``None`` when the existing admins are kept.

    ``accounts`` is ``None`` when the database could not be read; the
    account checks are then skipped (the database already has an error).
    """
    admin = fields.section(answers, "admin", errors)
    if fields.boolean(admin, "keep_existing", "admin.keep_existing", errors, default=False):
        if accounts is not None and not accounts.accounts_exist:
            errors.add("admin.keep_existing", NO_ACCOUNT_TO_KEEP)
        return None
    username = _username(admin, errors)
    email = _email(admin, errors)
    replace = fields.boolean(admin, "replace_password", "admin.replace_password", errors, default=False)
    password = admin.get("password")
    valid = check_password(password, username, email, "admin.password", errors)
    if username and accounts is not None and accounts.username_exists and not replace:
        errors.add("admin.username", ACCOUNT_EXISTS)
    if errors or not valid or not username:
        return None  # no plan is made after any error, so nothing is hashed
    exists = bool(accounts and accounts.username_exists)
    return {
        "username": username,
        "email": email,
        "password_hash": make_password(password),
        "replace_password": "true" if (replace and exists) else "false",
    }
