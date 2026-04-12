# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``workers/migrations/0017_managersettings.py``.

The migration creates the ``ManagerSettings`` singleton row and seeds
it with a valid Crockford base32 enrollment key and a UUID4
``manager_id``.  Because pytest-django runs migrations up-front, the
row is already in place by the time any test starts — the tests here
verify the **post-state** invariants rather than the step-by-step
migration mechanics:

  * Exactly one row exists, at ``pk=1``.
  * ``manager_id`` parses as a UUID4.
  * ``enrollment_key`` is 16 Crockford base32 characters that
    ``enrollment_key.normalize()`` accepts verbatim.
  * The forward seed function is re-entrant — running it twice against
    an already-seeded database is a noop.

We also invoke the forward function directly against the test DB
(post-migration) to verify the idempotency guard explicitly.  This is
preferable to fabricating a fresh DB inside the test — the migration
framework already owns that path, and ``pytest-django`` hides it from
us.
"""

import importlib
import re
import uuid

import pytest

from workers import enrollment_key
from workers.models import ManagerSettings

# The migration module name starts with a digit so a plain ``from ...``
# import is not valid Python syntax.  Resolve it via ``importlib``.
_migration = importlib.import_module(
    "workers.migrations.0017_managersettings",
)
_seed_manager_settings = _migration._seed_manager_settings

CROCKFORD_RE = re.compile(r'^[0-9A-HJKMNP-TV-Z]{16}$')


@pytest.mark.django_db
class TestManagerSettingsMigrationState:
    """Verify the state the migration leaves behind."""

    def test_exactly_one_row_exists(self):
        assert ManagerSettings.objects.count() == 1
        assert ManagerSettings.objects.filter(pk=1).exists()

    def test_manager_id_is_uuid4(self):
        row = ManagerSettings.objects.get(pk=1)
        # Must parse as a UUID.
        parsed = uuid.UUID(row.manager_id)
        # And be version 4 (random).
        assert parsed.version == 4

    def test_enrollment_key_is_valid_crockford(self):
        row = ManagerSettings.objects.get(pk=1)
        assert CROCKFORD_RE.match(row.enrollment_key)
        # ``normalize`` should accept it verbatim (no case/hyphen fixes).
        assert enrollment_key.normalize(row.enrollment_key) == row.enrollment_key

    def test_enrollment_key_length_is_sixteen(self):
        row = ManagerSettings.objects.get(pk=1)
        assert len(row.enrollment_key) == 16


@pytest.mark.django_db
class TestMigrationSeedIsIdempotent:
    """Running the forward function a second time must be a noop."""

    def test_second_call_does_not_replace_the_row(self):
        row_before = ManagerSettings.objects.get(pk=1)
        key_before = row_before.enrollment_key
        mid_before = row_before.manager_id

        # Fabricate the ``apps`` object the migration sees — it is
        # documented to route through ``apps.get_model``, so we pass
        # the real app registry which returns our real model class.
        from django.apps import apps as global_apps
        _seed_manager_settings(global_apps, None)

        row_after = ManagerSettings.objects.get(pk=1)
        assert row_after.enrollment_key == key_before
        assert row_after.manager_id == mid_before
        # Still exactly one row.
        assert ManagerSettings.objects.count() == 1
