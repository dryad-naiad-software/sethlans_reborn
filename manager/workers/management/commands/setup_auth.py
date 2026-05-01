# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Management command to bootstrap authentication for headless / CI installs:
create the admin superuser and ensure an enrollment key exists in the
``ManagerSettings`` DB row.

This is the non-browser counterpart to the standalone setup wizard.  Per
Spec 2 FR-DEL7 the command is retained as the sole headless / CI
bootstrap path; the wizard now owns ``manager.ini`` writes
(FR-M2-INI), SECRET_KEY generation (handled by the launcher's
``_bootstrap_first_run``), worker UI password hashing (FR-M2-6), and
DB validation (FR-M2-4), so this command no longer touches any of those.
"""

import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand

from workers import enrollment_key as ek
from workers.services.setup import (
    create_admin_user,
    generate_enrollment_key,
)

User = get_user_model()


class Command(BaseCommand):
    help = (
        'Set up authentication for headless installs: create admin '
        'superuser and ensure an enrollment key exists.'
    )

    def handle(self, *args, **options):
        self._create_admin_user()
        new_ek = self._ensure_enrollment_key()
        self._announce(new_ek)

    def _create_admin_user(self):
        """Create an admin superuser if none exists."""
        if User.objects.filter(is_superuser=True).exists():
            self.stdout.write(
                self.style.WARNING(
                    'A superuser already exists. Skipping creation.'
                )
            )
            return

        self.stdout.write('Creating admin superuser...')
        username = input('Username: ').strip()
        email = input('Email: ').strip()

        while True:
            password = getpass.getpass('Password: ')
            password2 = getpass.getpass('Password (again): ')
            if password != password2:
                self.stderr.write('Passwords do not match.')
                continue
            try:
                validate_password(password)
            except ValidationError as e:
                for msg in e.messages:
                    self.stderr.write(f'  - {msg}')
                continue
            break

        try:
            create_admin_user(
                username=username, email=email, password=password,
            )
        except ValidationError as e:
            self.stderr.write(self.style.ERROR(str(e)))
            return
        self.stdout.write(
            self.style.SUCCESS(f'Superuser "{username}" created.')
        )

    def _ensure_enrollment_key(self) -> str:
        """Idempotent: create the singleton key only if absent.

        Returns the canonical key string (existing or freshly generated).
        """
        from workers.models import ManagerSettings
        try:
            row = ManagerSettings.objects.get(pk=1)
            existing_ek = row.enrollment_key
        except ManagerSettings.DoesNotExist:
            existing_ek = ''

        if existing_ek:
            answer = input(
                'Enrollment key already set in database. '
                'Overwrite? [y/N]: '
            ).strip().lower()
            if answer == 'y':
                self.stdout.write(
                    self.style.SUCCESS(
                        'Enrollment key regenerated.'
                    )
                )
                return generate_enrollment_key()
            self.stdout.write(
                'Keeping existing enrollment key.'
            )
            return existing_ek
        return generate_enrollment_key()

    def _announce(self, new_ek: str) -> None:
        """Print the enrollment key for the operator to copy."""
        display_key = ek.format_display(new_ek)
        self.stdout.write('')
        self.stdout.write(
            'Enrollment key (provide this to each worker '
            'during enrollment):'
        )
        self.stdout.write(self.style.SUCCESS(f'  {display_key}'))
