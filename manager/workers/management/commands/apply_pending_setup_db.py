# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""DB-mutating helpers for ``apply_pending_setup`` (FR-APPLY2 steps 1-3).

Split out so the command file stays under the 300-line ceiling after
the Spec 2 LOW follow-ups landed (review ``6688ada``).  Covers the
atomic apply (password validate + superuser create + enrollment key)
and the in-place dict scrub used by ``_apply_payload``.
"""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from workers.management.commands.apply_pending_setup_helpers import (
    AdminCreateError,
    EnrollmentKeyError,
)

logger = logging.getLogger(__name__)

User = get_user_model()


def apply_atomic(username: str, email: str, password: str) -> None:
    """FR-APPLY2 steps 1-3 inside a single ``transaction.atomic``.

    Concurrency invariant (Spec 2 LOW): DB-mutating steps MUST run
    inside ``atomic()``; ``emit_stderr_and_exit`` uses ``os._exit``
    which skips ``finally`` — only ``atomic()`` rollback during
    unwind keeps state consistent.
    """
    try:
        with transaction.atomic():
            try:
                validate_password(password)
            except ValidationError as exc:
                raise AdminCreateError(
                    f"password validation failed for "
                    f"username={username!r}: {exc.messages}",
                ) from None
            create_superuser(username, email, password)
            ensure_enrollment_key()
    except (AdminCreateError, EnrollmentKeyError):
        raise
    except Exception as exc:  # noqa: BLE001
        raise AdminCreateError(
            f"atomic apply failed: {exc.__class__.__name__}",
        ) from None


def create_superuser(username: str, email: str, password: str) -> None:
    """FR-APPLY2 step 2 — idempotent on existing username."""
    try:
        User.objects.create_superuser(
            username=username, email=email, password=password,
        )
    except IntegrityError:
        logger.warning(
            "superuser %r already exists; skipping creation", username,
        )
        return
    except ValidationError as exc:
        raise AdminCreateError(
            f"create_superuser validation: {exc.messages}",
        ) from None
    except Exception as exc:  # noqa: BLE001
        raise AdminCreateError(
            f"create_superuser failed for username={username!r}: "
            f"{exc.__class__.__name__}",
        ) from None
    else:
        logger.info("superuser %r created", username)


def ensure_enrollment_key() -> None:
    """FR-APPLY2 step 3 — get-or-create singleton, ensure key set."""
    from workers import enrollment_key as ek
    from workers.models import ManagerSettings
    try:
        row, created = ManagerSettings.objects.get_or_create(pk=1)
        if created or not row.enrollment_key:
            row.enrollment_key = ek.generate_key()
            row.save(update_fields=[
                "enrollment_key", "enrollment_key_updated_at",
            ])
            logger.info("enrollment key generated/persisted")
        else:
            logger.info("enrollment key already present; reusing")
    except Exception as exc:  # noqa: BLE001
        raise EnrollmentKeyError(
            f"ensure_enrollment_key: {exc.__class__.__name__}",
        ) from None


def scrub_payload_password(payload: object) -> None:
    """Mutate a pending_setup payload in place to clear plaintext password.

    Spec 2 security LOW (review ``6688ada``): ``payload = None`` only
    rebinds the local; other frames keep referring to the same dict.
    Mutate the dict so all referrers see ``password_plaintext = None``.
    """
    if isinstance(payload, dict):
        admin_dict = payload.get("admin_user")
        if (
            isinstance(admin_dict, dict)
            and "password_plaintext" in admin_dict
        ):
            admin_dict["password_plaintext"] = None


__all__ = [
    "apply_atomic",
    "create_superuser",
    "ensure_enrollment_key",
    "scrub_payload_password",
]
