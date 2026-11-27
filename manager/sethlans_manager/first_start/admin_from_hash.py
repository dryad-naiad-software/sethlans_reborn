# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
FR-FS-1: the first admin from ``[first_admin]`` in ``sethlans.ini``.

Setup mode wrote only a password hash there (D6). Here, in the running
manager's own process and against its own database:

1. The section is checked: a username Django accepts (normalized the same
   way setup mode did, ``admin_fields.clean_username``), an email of at
   most 254 characters that Django accepts, ``replace_password`` ``true``
   or ``false``, and a hash that starts with ``pbkdf2_sha256$``, whose
   hasher is the configured default, that is usable, that the hasher can
   decode into a non-empty salt and hash, and that the hasher would not
   update (same iterations and salt size). Anything else: exit 11, the
   section is kept.
2. The ``ManagerSettings`` row is locked. When its ``first_admin_applied``
   is the SHA-256 digest of this hash, an earlier start applied the
   section and only its removal failed: the account is not touched (its
   admin may have changed the password since), an INFO says the leftover
   section is ignored, and its removal is tried again.
3. Otherwise, in the same ``transaction.atomic()``, the superuser is
   created with no password, then given the hash. When the username
   exists (``IntegrityError``): the same stored hash is a retry and the
   account is left as it is; ``replace_password = true`` puts the hash on
   that account and makes it an active superuser, unless it is a worker's
   service account (exit 12); otherwise exit 12. On exit 12 the section is
   kept. On success the digest is recorded in the same transaction.
4. The section is removed. If that fails, a WARNING is logged and the
   start goes on: the next start finds the digest and ignores the section.

A Reset setup writes a new hash (``pbkdf2`` has a random salt), so its
digest is new and it is applied. Neither the hash nor any password is
ever logged; the stored digest is of the hash, never the hash itself.
"""
import hashlib
import hmac
import logging
from pathlib import Path
from typing import Optional

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import get_hasher, identify_hasher, is_password_usable
from django.db import IntegrityError, transaction

from sethlans_manager.first_start.common import FirstStartError, clean_username, email_problems
from shared import settings_file
from shared.manager_exit_codes import EXIT_FIRST_START_FAILED, EXIT_NO_ADMIN

logger = logging.getLogger(__name__)

SECTION = settings_file.SECTION_FIRST_ADMIN
HASH_PREFIX = "pbkdf2_sha256$"
ADMIN_FIELDS = ("is_superuser", "is_staff", "is_active")
CREATED, UNCHANGED, REPLACED, ALREADY_APPLIED = "created", "unchanged", "replaced", "already_applied"


def _unusable(reason: str) -> FirstStartError:
    return FirstStartError(EXIT_FIRST_START_FAILED, f"[{SECTION}] in the settings file is not usable: {reason}")


def check_hash(password_hash: str) -> None:
    """The hash checks of step 1; raises :class:`FirstStartError` (exit 11)."""
    if not password_hash.startswith(HASH_PREFIX):
        raise _unusable(f"the password hash does not start with {HASH_PREFIX}")
    try:
        hasher = identify_hasher(password_hash)
        decoded = hasher.decode(password_hash)
    except ValueError:
        raise _unusable("the password hash has an unknown format") from None
    if hasher.algorithm != get_hasher("default").algorithm:
        raise _unusable("the password hash is not made with the default hasher")
    if not is_password_usable(password_hash):
        raise _unusable("the password hash is not usable")
    if not decoded.get("salt") or not decoded.get("hash"):
        raise _unusable("the password hash has an empty salt or hash")
    if hasher.must_update(password_hash):
        raise _unusable("the password hash does not match this manager's hasher settings; run Reset setup")


def hash_digest(password_hash: str) -> str:
    """The SHA-256 hex digest recorded in ``ManagerSettings.first_admin_applied``."""
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()


def read_first_admin(parser) -> Optional[dict]:
    """The checked ``[first_admin]`` values, or ``None`` when there is no section."""
    if not parser.has_section(SECTION):
        return None
    section = parser[SECTION]
    username, problems = clean_username(section.get("username", ""))
    email = section.get("email", "").strip()
    problems = problems + email_problems(email)
    if problems:
        raise _unusable("; ".join(problems))
    replace = section.get("replace_password", "false").strip().lower()
    if replace not in ("true", "false"):
        raise _unusable("replace_password must be true or false")
    password_hash = section.get("password_hash", "").strip()
    check_hash(password_hash)
    return {
        "username": username,
        "email": email,
        "password_hash": password_hash,
        "replace_password": replace == "true",
    }


def _settings_row():
    """The locked ``ManagerSettings`` row; exit 11 when it is missing."""
    from workers.models import ManagerSettings

    row = ManagerSettings.objects.select_for_update().filter(pk=1).first()
    if row is None:
        raise FirstStartError(EXIT_FIRST_START_FAILED, "the manager settings row is missing; the database is not migrated")
    return row


def _is_worker_account(user) -> bool:
    from workers.models import Worker

    return Worker.objects.filter(user=user).exists()


def _existing_account(admin: dict, error_name: str) -> str:
    """Step 3: the username is taken. Returns ``unchanged`` or ``replaced``."""
    username = admin["username"]
    user = get_user_model().objects.select_for_update().filter(username=username).first()
    if user is None:
        raise FirstStartError(EXIT_FIRST_START_FAILED, (
            f"the admin account {username!r} could not be created ({error_name})"
        ))
    if user.password == admin["password_hash"]:
        if not (user.is_superuser and user.is_active):
            logger.warning("The account %r has the password from setup but is not an active admin; "
                           "it is left as it is", username)
        return UNCHANGED
    if not admin["replace_password"]:
        raise FirstStartError(EXIT_NO_ADMIN, (
            f"the account {username!r} already exists; run Reset setup and tick "
            "'Replace its password?', or choose another username"
        ))
    if _is_worker_account(user):
        raise FirstStartError(EXIT_NO_ADMIN, (
            f"the account {username!r} is a worker's service account and cannot become the admin; "
            "run Reset setup and choose another username"
        ))
    user.password = admin["password_hash"]
    for name in ADMIN_FIELDS:
        setattr(user, name, True)
    user.save(update_fields=["password", *ADMIN_FIELDS])
    return REPLACED


def _apply(admin: dict) -> str:
    """Create the account, or handle the taken username (inside the caller's transaction)."""
    try:
        with transaction.atomic():  # a savepoint, so the IntegrityError leaves the outer transaction usable
            user = get_user_model().objects.create_superuser(admin["username"], admin["email"], password=None)
            user.password = admin["password_hash"]
            user.save(update_fields=["password"])
    except IntegrityError as exc:
        return _existing_account(admin, type(exc).__name__)
    return CREATED


_OUTCOME_LOG = {
    CREATED: "Created the first admin account %r",
    UNCHANGED: "First admin %r already has the password from setup; nothing to do",
    REPLACED: "Replaced the password of the existing account %r; it is an admin",
    ALREADY_APPLIED: f"[{SECTION}] for %r was applied by an earlier start; ignoring the leftover section",
}


def create_first_admin(admin: dict) -> str:
    """Steps 2 and 3. Returns ``created``, ``unchanged``, ``replaced`` or ``already_applied``."""
    digest = hash_digest(admin["password_hash"])
    with transaction.atomic():
        row = _settings_row()
        if hmac.compare_digest(row.first_admin_applied, digest):
            outcome = ALREADY_APPLIED
        else:
            outcome = _apply(admin)
            row.first_admin_applied = digest
            row.save(update_fields=["first_admin_applied"])
    logger.info(_OUTCOME_LOG[outcome], admin["username"])
    return outcome


def remove_first_admin(ini_path: Path) -> None:
    """Step 4: drop the section; a failure is a WARNING, never fatal."""
    try:
        settings_file.remove_section(SECTION, ini_path)
    except OSError as exc:
        logger.warning(
            "Could not remove [%s] from %s (%s: %s); the next start finds it recorded as applied and ignores it",
            SECTION, ini_path, type(exc).__name__, exc,
        )


def apply_first_admin(ini_path: Path) -> Optional[str]:
    """FR-FS-1. Returns what was done, or ``None`` when there is no ``[first_admin]``."""
    try:
        parser = settings_file.read_settings(ini_path)
    except settings_file.SettingsFileError as exc:
        raise FirstStartError(EXIT_FIRST_START_FAILED, f"the settings file cannot be read: {exc}") from None
    admin = read_first_admin(parser)
    if admin is None:
        return None
    outcome = create_first_admin(admin)
    remove_first_admin(ini_path)
    return outcome
