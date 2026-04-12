# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Management command: ``python manage.py show_enrollment_key``.

Read-only display of the current worker enrollment key.  Prints the
hyphenated display form to stdout and a reminder warning to stderr.
"""

import sys

from django.core.management.base import BaseCommand

from workers import enrollment_key


class Command(BaseCommand):
    help = (
        "Print the current worker enrollment key in hyphenated "
        "display form."
    )

    def handle(self, *args, **options):
        canonical = enrollment_key.load_current()
        display = enrollment_key.format_display(canonical)
        self.stderr.write(
            "WARNING: stdout contains a secret. Handle accordingly.",
        )
        self.stdout.write(display)
        sys.stdout.flush()
