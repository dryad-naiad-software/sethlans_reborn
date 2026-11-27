# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``0035_managersettings_first_admin_applied``
(setup-rebuild FR-FS-1): one additive field on the ``ManagerSettings``
singleton that reads ``''`` on the existing row, and the migration reverses
without touching the row's other values.
"""

import pytest
from django.db import connection

from .migration_helpers import migrate_to, migrate_to_latest

BEFORE = [('workers', '0034_blender_builds')]
AFTER = [('workers', '0035_managersettings_first_admin_applied')]
COLUMN = 'first_admin_applied'
ROW = {'manager_id': '00000000-0000-4000-8000-000000000035', 'enrollment_key': '0123456789ABCDEF'}


def _columns():
    with connection.cursor() as cursor:
        return {c.name for c in connection.introspection.get_table_description(cursor, 'workers_managersettings')}


def _row(apps):
    return apps.get_model('workers', 'ManagerSettings').objects.values().get(pk=1)


@pytest.fixture
def at_0034(transactional_db):
    """The database at 0034 with the singleton row pinned to ``ROW`` (a flush may have removed the seeded one)."""
    try:
        apps = migrate_to(BEFORE)
        apps.get_model('workers', 'ManagerSettings').objects.update_or_create(pk=1, defaults=ROW)
        yield apps
    finally:
        migrate_to_latest()


@pytest.mark.django_db(transaction=True)
class TestFirstAdminAppliedMigration:

    def test_forward_adds_the_field_and_the_row_reads_empty(self, at_0034):
        """FR-FS-1: after 0035 the existing row reads first_admin_applied '' and keeps its identity and key."""
        assert COLUMN not in _columns()

        row = _row(migrate_to(AFTER))

        assert row[COLUMN] == ''
        assert {name: row[name] for name in ROW} == ROW

    def test_reverse_drops_only_the_field(self, at_0034):
        """FR-FS-1: 0035 reverses; the column goes and the row keeps its other values."""
        before_columns, before_row = _columns(), _row(at_0034)
        apps = migrate_to(AFTER)
        apps.get_model('workers', 'ManagerSettings').objects.filter(pk=1).update(**{COLUMN: 'ab' * 32})

        after_row = _row(migrate_to(BEFORE))

        assert _columns() == before_columns
        assert after_row == before_row
