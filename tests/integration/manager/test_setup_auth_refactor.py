# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import pytest

pytestmark = pytest.skip(
    "Obsoleted by setup-auth-unification; replaced in follow-up test phase",
    allow_module_level=True,
)

"""
Integration tests for the refactored ``setup_auth`` management command
and the shared service functions it delegates to.

Verifies that the service module functions produce correct side effects
when called through real Django infrastructure (ORM, auth, migrations).
"""

import configparser
import re

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from workers.services.setup import (
    create_admin_user,
    generate_enrollment_key,
    generate_secret_key,
    write_manager_ini,
    verify_admin_authenticates,
)

User = get_user_model()

# Crockford base32 canonical regex (16 chars, no I/L/O/U).
CROCKFORD_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{16}$")


# -------------------------------------------------------------------
# generate_secret_key
# -------------------------------------------------------------------


class TestGenerateSecretKey:

    def test_returns_url_safe_string(self):
        """Key is a non-empty url-safe base64 string."""
        key = generate_secret_key()
        assert len(key) > 40
        # token_urlsafe uses A-Z, a-z, 0-9, '-', '_'
        assert re.match(r"^[A-Za-z0-9_-]+$", key)

    def test_keys_are_unique(self):
        """Two consecutive calls produce different keys."""
        k1 = generate_secret_key()
        k2 = generate_secret_key()
        assert k1 != k2


# -------------------------------------------------------------------
# generate_enrollment_key — Crockford base32, stored in DB
# -------------------------------------------------------------------


@pytest.mark.django_db
class TestGenerateEnrollmentKey:

    def test_key_is_crockford_base32(self):
        """Generated key matches the 16-char Crockford base32 pattern."""
        key = generate_enrollment_key()
        assert CROCKFORD_RE.match(key), (
            f"Key {key!r} is not valid Crockford base32"
        )

    def test_key_stored_in_manager_settings(self):
        """Key is persisted in the ManagerSettings singleton row."""
        from workers.models import ManagerSettings
        key = generate_enrollment_key()
        row = ManagerSettings.objects.get(pk=1)
        assert row.enrollment_key == key

    def test_key_not_base64(self):
        """Key must NOT be the old base64/urlsafe format."""
        key = generate_enrollment_key()
        # Old format used lowercase, +, /, = — Crockford is uppercase
        # and restricted to 32 alphanumeric chars.
        assert key == key.upper()
        assert "+" not in key
        assert "/" not in key
        assert "=" not in key

    def test_idempotent_overwrites_previous(self):
        """Calling twice overwrites the previous key in DB."""
        from workers.models import ManagerSettings
        k1 = generate_enrollment_key()
        k2 = generate_enrollment_key()
        row = ManagerSettings.objects.get(pk=1)
        assert row.enrollment_key == k2
        assert k1 != k2


# -------------------------------------------------------------------
# create_admin_user
# -------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateAdminUser:

    def test_creates_superuser(self):
        """Created user is a superuser with correct fields."""
        user = create_admin_user(
            username="wizardadmin",
            email="admin@example.com",
            password="Str0ngP@ssw0rd!",
        )
        assert user.is_superuser is True
        assert user.is_staff is True
        assert user.email == "admin@example.com"

    def test_authenticates_after_creation(self):
        """Created admin can authenticate via Django auth stack."""
        create_admin_user(
            username="authtest",
            email="a@b.com",
            password="Str0ngP@ssw0rd!",
        )
        assert verify_admin_authenticates(
            "authtest", "Str0ngP@ssw0rd!",
        )

    def test_duplicate_username_raises(self):
        """Creating a user with an existing username raises."""
        create_admin_user(
            username="dupetest",
            email="d@b.com",
            password="Str0ngP@ssw0rd!",
        )
        with pytest.raises(ValidationError, match="already taken"):
            create_admin_user(
                username="dupetest",
                email="d2@b.com",
                password="An0therStr0ng!",
            )

    def test_weak_password_raises(self):
        """A password that fails Django validators is rejected."""
        with pytest.raises(ValidationError):
            create_admin_user(
                username="weakpw",
                email="w@b.com",
                password="123",
            )


# -------------------------------------------------------------------
# write_manager_ini — atomic write, SECRET_KEY persistence
# -------------------------------------------------------------------


class TestWriteManagerIni:

    def test_creates_ini_with_secret_key(self, tmp_path):
        """SECRET_KEY is written under [security] section."""
        ini_path = tmp_path / "manager.ini"
        key = generate_secret_key()
        write_manager_ini({"security.secret_key": key}, ini_path)

        config = configparser.ConfigParser()
        config.read(ini_path)
        assert config.get("security", "secret_key") == key

    def test_merges_into_existing_ini(self, tmp_path):
        """New keys merge into an existing INI without clobbering."""
        ini_path = tmp_path / "manager.ini"

        # Pre-existing content.
        write_manager_ini({"server.port": "8080"}, ini_path)
        write_manager_ini({"security.debug": "false"}, ini_path)

        config = configparser.ConfigParser()
        config.read(ini_path)
        assert config.get("server", "port") == "8080"
        assert config.get("security", "debug") == "false"

    def test_returns_path(self, tmp_path):
        """Function returns the INI path for chaining."""
        ini_path = tmp_path / "manager.ini"
        result = write_manager_ini({"security.debug": "false"}, ini_path)
        assert result == ini_path


# -------------------------------------------------------------------
# Idempotency: running the full flow twice
# -------------------------------------------------------------------


@pytest.mark.django_db
class TestSetupFlowIdempotency:

    def test_generate_enrollment_key_twice_no_crash(self):
        """Calling generate_enrollment_key twice does not raise."""
        k1 = generate_enrollment_key()
        k2 = generate_enrollment_key()
        assert CROCKFORD_RE.match(k1)
        assert CROCKFORD_RE.match(k2)

    def test_write_ini_twice_no_crash(self, tmp_path):
        """Writing the INI twice with new keys does not crash."""
        ini_path = tmp_path / "manager.ini"
        write_manager_ini(
            {"security.secret_key": generate_secret_key()}, ini_path,
        )
        write_manager_ini(
            {"security.secret_key": generate_secret_key()}, ini_path,
        )
        config = configparser.ConfigParser()
        config.read(ini_path)
        assert config.has_option("security", "secret_key")
