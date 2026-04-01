# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Management command to bootstrap authentication: create admin user,
generate SECRET_KEY, generate enrollment key, and write manager.ini.
"""

import configparser
import getpass
import os
import secrets
import tempfile

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import (
    validate_password,
)
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from django.core.management.utils import get_random_secret_key

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

        User.objects.create_superuser(
            username=username, email=email, password=password,
        )
        self.stdout.write(
            self.style.SUCCESS(f'Superuser "{username}" created.')
        )

    def _write_config(self):
        """Generate keys and write them to manager.ini."""
        config_path = settings.BASE_DIR / 'manager.ini'
        config = configparser.ConfigParser()
        if config_path.exists():
            config.read(config_path)

        if not config.has_section('security'):
            config.add_section('security')

        # SECRET_KEY
        existing_key = config.get('security', 'secret_key', fallback='')
        if existing_key:
            answer = input(
                'SECRET_KEY already set in manager.ini. '
                'Overwrite? [y/N]: '
            ).strip().lower()
            if answer != 'y':
                self.stdout.write('Keeping existing SECRET_KEY.')
            else:
                config.set(
                    'security', 'secret_key', get_random_secret_key()
                )
                self.stdout.write(
                    self.style.SUCCESS('SECRET_KEY regenerated.')
                )
        else:
            config.set(
                'security', 'secret_key', get_random_secret_key()
            )
            self.stdout.write(
                self.style.SUCCESS('SECRET_KEY generated.')
            )

        # Enrollment key
        existing_ek = config.get(
            'security', 'enrollment_key', fallback=''
        )
        if existing_ek:
            answer = input(
                'Enrollment key already set in manager.ini. '
                'Overwrite? [y/N]: '
            ).strip().lower()
            if answer != 'y':
                self.stdout.write('Keeping existing enrollment key.')
            else:
                new_ek = secrets.token_urlsafe(32)
                config.set('security', 'enrollment_key', new_ek)
                self.stdout.write(
                    self.style.SUCCESS('Enrollment key regenerated.')
                )
        else:
            new_ek = secrets.token_urlsafe(32)
            config.set('security', 'enrollment_key', new_ek)

        # DEBUG=False
        config.set('security', 'debug', 'false')

        # Atomic write
        config_dir = os.path.dirname(config_path)
        fd, tmp_path = tempfile.mkstemp(
            dir=config_dir, suffix='.ini'
        )
        try:
            with os.fdopen(fd, 'w') as f:
                config.write(f)
            os.replace(tmp_path, config_path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

        enrollment_key = config.get(
            'security', 'enrollment_key', fallback=''
        )
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('manager.ini updated.'))
        self.stdout.write('')
        self.stdout.write(
            'Enrollment key (copy this to each worker\'s '
            'config.ini under [manager] enrollment_key):'
        )
        self.stdout.write(self.style.SUCCESS(f'  {enrollment_key}'))
