# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Smoke tests for ``ManagerSettings`` singleton enforcement.

The DB-level ``CheckConstraint`` must block any attempt to create a
second row, regardless of whether the caller goes through the ORM
shortcut (``objects.create``) or the default save path.  These tests
pin the invariant at the database boundary — the Python-level
``save(pk=1)`` override is a secondary safety net.
"""

import pytest
from django.db import IntegrityError, transaction

from workers.models import ManagerSettings


@pytest.mark.django_db
class TestManagerSettingsSingleton:

    def test_fixture_row_exists(self):
        """The seeding migration created the row with pk=1."""
        row = ManagerSettings.objects.get(pk=1)
        assert row.manager_id
        assert len(row.enrollment_key) == 16

    def test_create_second_row_blocked(self):
        """``CheckConstraint`` blocks an explicit second row."""
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ManagerSettings.objects.create(
                    id=2,
                    manager_id="00000000-0000-0000-0000-000000000002",
                    enrollment_key="4F9XK2PBQ7M3N8RT",
                )

    def test_save_without_pk_collides_with_singleton(self):
        """An accidental unqualified ``save()`` collides with pk=1.

        The ``save()`` override forces ``self.pk = 1``; when the
        singleton is already present, the ORM performs an INSERT and
        the row conflicts with the existing pk, raising
        ``IntegrityError``.  This is the intended behaviour — it
        blocks a second instance from being created.
        """
        ghost = ManagerSettings(
            manager_id="00000000-0000-0000-0000-000000000003",
            enrollment_key="4F9XK2PBQ7M3N8RT",
        )
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ghost.save()
        # The singleton row count stays at 1.
        assert ManagerSettings.objects.count() == 1

    def test_save_override_forces_pk_one(self):
        """Even with ``id`` pre-set to something else, ``save()`` pins pk=1."""
        ghost = ManagerSettings(
            id=99,
            manager_id="00000000-0000-0000-0000-000000000099",
            enrollment_key="4F9XK2PBQ7M3N8RT",
        )
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ghost.save()
        # No row at id=99 was created.
        assert not ManagerSettings.objects.filter(pk=99).exists()

    def test_update_persists(self):
        row = ManagerSettings.objects.get(pk=1)
        row.enrollment_key = "5F9XK2PBQ7M3N8RT"
        row.save(update_fields=[
            "enrollment_key", "enrollment_key_updated_at",
        ])
        reloaded = ManagerSettings.objects.get(pk=1)
        assert reloaded.enrollment_key == "5F9XK2PBQ7M3N8RT"
