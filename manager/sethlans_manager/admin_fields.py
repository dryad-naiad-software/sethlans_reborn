# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The first admin's username and email rules, shared by setup mode and the
first start.

Setup mode checks the username and writes it to ``[first_admin]``; the
first start reads it back and creates the account. Both go through
:func:`clean_username`, so a name setup accepts is written already
normalized and is never changed or refused at the first start.

Django's ``AbstractBaseUser.normalize_username`` (verified in Django
5.2.4 ``django/contrib/auth/base_user.py``) is NFKC
(``unicodedata.normalize("NFKC", username)``); it can change the length,
so the length rule runs on the normalized name.
"""
from typing import List, Tuple

from django.contrib.auth import get_user_model
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

__all__ = [
    "MAX_EMAIL_LENGTH", "MAX_USERNAME_LENGTH", "clean_username", "email_problems", "username_problems",
]

MAX_USERNAME_LENGTH = 150
MAX_EMAIL_LENGTH = 254  # User.email's max_length


def username_problems(username: str) -> list:
    """Why ``username`` cannot be an account name (empty list when it can)."""
    if not username:
        return ["the username is empty"]
    if len(username) > MAX_USERNAME_LENGTH:
        return [f"the username is longer than {MAX_USERNAME_LENGTH} characters"]
    try:
        UnicodeUsernameValidator()(username)
    except ValidationError as exc:
        return [str(message) for message in exc.messages]
    return []


def clean_username(raw: str) -> Tuple[str, List[str]]:
    """``raw`` stripped and normalized like Django does, and why it cannot be used."""
    username = get_user_model().normalize_username(raw.strip())
    return username, username_problems(username)


def email_problems(email: str, label: str = "the email address") -> list:
    """Why ``email`` cannot be the admin's email (empty list when it can, or is empty)."""
    if not email:
        return []
    if len(email) > MAX_EMAIL_LENGTH:
        return [f"{label} is longer than {MAX_EMAIL_LENGTH} characters"]
    try:
        validate_email(email)
    except ValidationError:
        return [f"{label} is not a valid email address"]
    return []
