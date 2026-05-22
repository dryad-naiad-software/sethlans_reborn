# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Regression tests for ``apply_pending_setup_db.create_superuser`` (#200).

When ``User.objects.create_superuser`` raises ``IntegrityError`` (because
the configured superuser already exists from a prior apply run), the
exception must be contained inside a *savepoint* — a nested
``transaction.atomic()`` block — so that the **outer** atomic transaction
opened by ``apply_atomic`` is NOT marked "needs rollback". Without the
savepoint, the subsequent ``ensure_enrollment_key()`` query inside the
same outer atomic raised ``TransactionManagementError``.

These tests exercise the production code path directly (no test doubles
for the savepoint itself) by calling ``apply_atomic`` twice against the
same Django test DB.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from workers.management.commands.apply_pending_setup_db import (
    apply_atomic,
    create_superuser,
)
from workers.management.commands.apply_pending_setup_helpers import (
    AdminCreateError,
)
from workers.models import ManagerSettings


User = get_user_model()

# Satisfies Django's default password validators.
STRONG_PASSWORD = "Tr0pical!Mongoose-Hops42"


class TestSavepointPreservesOuterAtomic:
    """Issue #200: idempotent create must not poison the outer atomic."""

    @pytest.mark.django_db
    def test_create_superuser_idempotent_on_existing_user_preserves_outer_atomic(  # noqa: E501
        self,
    ):
        """Second apply against the same DB must NOT raise.

        First call creates the user and the enrollment key. Second call
        hits the ``IntegrityError`` path inside ``create_superuser`` (the
        username already exists). The savepoint must roll back the
        sub-transaction cleanly so ``ensure_enrollment_key`` can still
        run inside the outer atomic without raising
        ``TransactionManagementError``.
        """
        # First run: clean DB, full path succeeds.
        apply_atomic("smoke", "smoke@example.com", STRONG_PASSWORD)

        assert User.objects.filter(username="smoke").exists()
        first_key = ManagerSettings.objects.get(pk=1).enrollment_key
        assert first_key, "first apply must persist an enrollment key"

        # Second run: superuser already exists. Pre-fix this raised
        # ``TransactionManagementError`` (wrapped to ``AdminCreateError``)
        # because the swallowed IntegrityError poisoned the outer atomic.
        apply_atomic("smoke", "smoke@example.com", STRONG_PASSWORD)

        # Outer atomic completed: enrollment key row is still queryable
        # (proves ``ensure_enrollment_key`` ran on the second pass).
        second_key = ManagerSettings.objects.get(pk=1).enrollment_key
        assert second_key, (
            "enrollment key must remain present after the idempotent "
            "second apply (proves ensure_enrollment_key ran)"
        )
        # Existing key is reused, not regenerated.
        assert second_key == first_key

    @pytest.mark.django_db
    def test_savepoint_releases_so_outer_can_commit_more_rows(self):
        """After the swallowed IntegrityError, the outer atomic stays
        usable for additional writes — not just the enrollment-key read.
        """
        # Seed the user so the second create hits IntegrityError.
        User.objects.create_superuser(
            username="seeded",
            email="seeded@example.com",
            password=STRONG_PASSWORD,
        )

        with transaction.atomic():
            # Triggers the IntegrityError → savepoint rollback path.
            create_superuser(
                "seeded", "seeded@example.com", STRONG_PASSWORD,
            )
            # Outer atomic must still accept writes. Pre-fix, this
            # raised ``TransactionManagementError``.
            row, _created = ManagerSettings.objects.get_or_create(pk=1)
            row.enrollment_key = "POST_SAVEPOINT_OK"
            row.save(update_fields=[
                "enrollment_key", "enrollment_key_updated_at",
            ])

        assert (
            ManagerSettings.objects.get(pk=1).enrollment_key
            == "POST_SAVEPOINT_OK"
        )


class TestOtherIntegrityErrorsStillHandled:
    """A non-duplicate IntegrityError still rolls back the savepoint
    cleanly. The existing ``except IntegrityError`` arm logs + returns
    (treating ALL IntegrityErrors as the idempotent "user exists" path
    — current semantic, preserved by the fix). The key invariant is
    that the outer atomic is NOT poisoned afterward.
    """

    @pytest.mark.django_db
    def test_other_integrity_error_does_not_poison_outer_atomic(
        self, mocker,
    ):
        """Patch ``create_superuser`` to raise a non-duplicate
        ``IntegrityError`` (e.g. a hypothetical constraint violation).
        The savepoint must roll back so the outer atomic stays healthy.
        """
        mocker.patch.object(
            User.objects, "create_superuser",
            side_effect=IntegrityError(
                "some other constraint violation",
            ),
        )

        # apply_atomic must NOT raise — current semantic swallows any
        # IntegrityError as "user exists". The savepoint guarantees
        # ensure_enrollment_key can still run.
        apply_atomic("ghost", "ghost@example.com", STRONG_PASSWORD)

        # No user was created.
        assert not User.objects.filter(username="ghost").exists()
        # Enrollment key was generated — proves the outer atomic
        # survived the savepoint rollback.
        assert ManagerSettings.objects.get(pk=1).enrollment_key

    @pytest.mark.django_db
    def test_non_integrity_exception_still_raises_admin_create_error(
        self, mocker,
    ):
        """Non-IntegrityError failures inside create_superuser are
        still wrapped as ``AdminCreateError`` (the existing
        bare-except arm) — the savepoint must not swallow them.
        """
        mocker.patch.object(
            User.objects, "create_superuser",
            side_effect=RuntimeError("boom"),
        )

        with pytest.raises(AdminCreateError):
            apply_atomic("ghost", "ghost@example.com", STRONG_PASSWORD)
