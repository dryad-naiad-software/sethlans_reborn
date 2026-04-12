# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``python manage.py show_enrollment_key``.

Covers:
  * Prints the current enrollment key in hyphenated display form.
  * Exit code 0 (call_command returns without raising CommandError).
  * Stderr carries the secret-warning text.
"""

from io import StringIO

import pytest
from django.core.management import call_command

from workers.models import ManagerSettings


@pytest.mark.django_db
class TestShowEnrollmentKeyCommand:

    def test_prints_current_key_hyphenated(self):
        # Seed a known value so we can assert the output exactly.
        row = ManagerSettings.objects.get(pk=1)
        row.enrollment_key = "4F9XK2PBQ7M3N8RT"
        row.save(
            update_fields=["enrollment_key", "enrollment_key_updated_at"],
        )
        stdout = StringIO()
        stderr = StringIO()
        call_command(
            "show_enrollment_key", stdout=stdout, stderr=stderr,
        )
        assert stdout.getvalue().strip() == "4F9X-K2PB-Q7M3-N8RT"
        # Warning lands on stderr so piping stdout to a file doesn't
        # accidentally capture it.
        assert "secret" in stderr.getvalue().lower()

    def test_stderr_contains_secret_warning(self):
        """The secret warning is written regardless of which key is set."""
        stdout = StringIO()
        stderr = StringIO()
        call_command(
            "show_enrollment_key", stdout=stdout, stderr=stderr,
        )
        assert "WARNING" in stderr.getvalue()
        assert "stdout" in stderr.getvalue()
