# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``python manage.py rotate_enrollment_key``.

Covers:
  * No ``--key``: generates a fresh key and writes it to the singleton.
  * Valid ``--key``: normalised and written verbatim.
  * Invalid ``--key``: stderr error + non-zero exit + DB unchanged.
  * Secret warning is always written to stderr.
"""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from workers.models import ManagerSettings


VALID_HYPHEN_KEY = "4F9X-K2PB-Q7M3-N8RT"
VALID_CANONICAL = "4F9XK2PBQ7M3N8RT"
BAD_KEY = "4F9X-K2PB-Q7M3-N8RI"  # contains a Crockford-excluded I


@pytest.mark.django_db
class TestRotateEnrollmentKeyCommand:

    def test_no_key_generates_fresh_key(self):
        """Without ``--key``: generate fresh, print to stdout, warning on stderr."""
        stdout = StringIO()
        stderr = StringIO()
        row_before = ManagerSettings.objects.get(pk=1)
        old_key = row_before.enrollment_key
        call_command(
            "rotate_enrollment_key",
            stdout=stdout, stderr=stderr,
        )
        # Stdout contains the hyphenated 19-char display form.
        printed = stdout.getvalue().strip()
        assert len(printed) == 19
        assert printed.count("-") == 3
        # The DB row was updated.
        row_after = ManagerSettings.objects.get(pk=1)
        assert row_after.enrollment_key != old_key
        # Stderr carries the secret warning.
        assert "secret" in stderr.getvalue().lower()

    def test_valid_explicit_key_writes_verbatim(self):
        """Valid ``--key``: normalised form is persisted and displayed."""
        stdout = StringIO()
        stderr = StringIO()
        call_command(
            "rotate_enrollment_key",
            key=VALID_HYPHEN_KEY,
            stdout=stdout, stderr=stderr,
        )
        printed = stdout.getvalue().strip()
        assert printed == VALID_HYPHEN_KEY
        # The DB row should hold the canonical form.
        row = ManagerSettings.objects.get(pk=1)
        assert row.enrollment_key == VALID_CANONICAL
        assert "secret" in stderr.getvalue().lower()

    def test_invalid_explicit_key_exits_non_zero_without_db_change(self):
        """A bad ``--key`` must not touch the DB and must raise CommandError."""
        row_before = ManagerSettings.objects.get(pk=1)
        key_before = row_before.enrollment_key
        stdout = StringIO()
        stderr = StringIO()
        with pytest.raises(CommandError):
            call_command(
                "rotate_enrollment_key",
                key=BAD_KEY,
                stdout=stdout, stderr=stderr,
            )
        # Stderr carries the ValueError message.
        assert "ERROR" in stderr.getvalue()
        assert "I/L/O/U" in stderr.getvalue()
        # DB row is unchanged.
        row_after = ManagerSettings.objects.get(pk=1)
        assert row_after.enrollment_key == key_before
