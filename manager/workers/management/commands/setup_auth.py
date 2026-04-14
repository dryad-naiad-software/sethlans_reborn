# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Management command to bootstrap authentication: create admin user,
generate SECRET_KEY, generate enrollment key, and write manager.ini.

The interactive prompts stay in this command; the underlying logic is
delegated to ``workers.services.setup`` so it can be shared with the
setup wizard REST endpoints.
"""

import getpass

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand

from workers import enrollment_key as ek
from workers.services.setup import (
    create_admin_user,
    generate_enrollment_key,
    generate_secret_key,
    write_manager_ini,
)

User = get_user_model()


class Command(BaseCommand):
    help = (
        'Set up authentication: create admin user, generate '
        'SECRET_KEY and enrollment key, write manager.ini.'
    )

    def handle(self, *args, **options):
        self._create_admin_user()
        self._write_config()

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

    def _write_config(self):
        """Generate keys and write them to manager.ini."""
        ini_path = settings.BASE_DIR / 'manager.ini'

        # Determine what config updates to make.
        updates = {}

        # SECRET_KEY
        existing_key = settings.SECRET_KEY
        insecure_default = existing_key.startswith('django-insecure-')
        if not insecure_default:
            answer = input(
                'SECRET_KEY already set. '
                'Overwrite? [y/N]: '
            ).strip().lower()
            if answer == 'y':
                updates['security.secret_key'] = (
                    generate_secret_key()
                )
                self.stdout.write(
                    self.style.SUCCESS('SECRET_KEY regenerated.')
                )
            else:
                self.stdout.write('Keeping existing SECRET_KEY.')
        else:
            updates['security.secret_key'] = generate_secret_key()
            self.stdout.write(
                self.style.SUCCESS('SECRET_KEY generated.')
            )

        # Enrollment key — now uses Crockford base32 via the shared
        # service, stored in the ManagerSettings DB row (not ini).
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
                new_ek = generate_enrollment_key()
                self.stdout.write(
                    self.style.SUCCESS(
                        'Enrollment key regenerated.'
                    )
                )
            else:
                new_ek = existing_ek
                self.stdout.write(
                    'Keeping existing enrollment key.'
                )
        else:
            new_ek = generate_enrollment_key()

        # DEBUG=False
        updates['security.debug'] = 'false'

        write_manager_ini(updates, ini_path)

        display_key = ek.format_display(new_ek)
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('manager.ini updated.'))
        self.stdout.write('')
        self.stdout.write(
            'Enrollment key (provide this to each worker '
            'during enrollment):'
        )
        self.stdout.write(self.style.SUCCESS(f'  {display_key}'))
