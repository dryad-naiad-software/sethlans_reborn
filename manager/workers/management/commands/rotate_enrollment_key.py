# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Management command: ``python manage.py rotate_enrollment_key``.

Without ``--key`` the command generates a fresh Crockford base32 key
and writes it to the ``ManagerSettings`` singleton row.  With
``--key XXXX-XXXX-XXXX-XXXX`` the supplied key is normalised (any
ValueError surfaces on stderr) and the normalised form is written
verbatim — this path is for DR/recovery scenarios where an admin
needs to sync keys across a secondary manager.

Both paths print a stderr warning reminding the operator that the
stdout output is a secret.
"""

import sys

from django.core.management.base import BaseCommand, CommandError

from workers import enrollment_key


class Command(BaseCommand):
    help = (
        "Rotate the shared worker enrollment key.  Writes to the "
        "ManagerSettings singleton row and prints the hyphenated "
        "display form to stdout."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--key",
            dest="key",
            default=None,
            help=(
                "Optional explicit key in hyphenated or compact form. "
                "If omitted, a random key is generated."
            ),
        )

    def handle(self, *args, **options):
        supplied = options.get("key")
        try:
            display = enrollment_key.rotate(new_key=supplied)
        except ValueError as exc:
            # Invalid ``--key`` input — print to stderr and exit non-zero
            # WITHOUT touching the DB.
            self.stderr.write(f"ERROR: {exc}")
            raise CommandError(str(exc)) from exc

        # Stderr warning (so piping stdout to a file doesn't silently
        # capture the warning too).
        self.stderr.write(
            "WARNING: stdout contains a secret. Handle accordingly.",
        )
        # Stdout carries only the key so automation can read it
        # directly without a parsing step.
        self.stdout.write(display)
        sys.stdout.flush()
