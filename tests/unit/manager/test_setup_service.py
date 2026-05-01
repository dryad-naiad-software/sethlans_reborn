# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for the retained helpers in ``workers.services.setup``.

Spec 2 cluster B1 (FR-DEL5) pruned the module to three helpers:
``apply_migrations``, ``create_admin_user``, ``generate_enrollment_key``.
The setup wizard (``wizard/sethlans_wizard``) and the launcher's
``_bootstrap_first_run`` now own all the deleted concerns.
"""

from unittest.mock import MagicMock

import pytest
from django.core.exceptions import ValidationError

from workers.services.setup import (
    create_admin_user,
    generate_enrollment_key,
)


# ---- generate_enrollment_key() -------------------------------------------

class TestGenerateEnrollmentKey:

    def test_returns_crockford_base32(self, mocker):
        mocker.patch(
            'workers.enrollment_key.generate_key',
            return_value='4F9XK2PBQ7M3N8RT',
        )
        mock_row = MagicMock()
        mock_mgr = MagicMock()
        mock_mgr.get_or_create.return_value = (mock_row, True)
        mock_model = MagicMock()
        mock_model.objects = mock_mgr
        mocker.patch(
            'workers.models.ManagerSettings', mock_model,
        )
        key = generate_enrollment_key()
        assert key == '4F9XK2PBQ7M3N8RT'
        # Crockford base32 excludes I, L, O, U
        for ch in key:
            assert ch not in 'ILOU'

    def test_persists_to_manager_settings(self, mocker):
        mocker.patch(
            'workers.enrollment_key.generate_key',
            return_value='ABCDEFGH12345678',
        )
        mock_row = MagicMock()
        mock_mgr = MagicMock()
        mock_mgr.get_or_create.return_value = (mock_row, True)
        mock_model = MagicMock()
        mock_model.objects = mock_mgr
        mocker.patch(
            'workers.models.ManagerSettings', mock_model,
        )
        generate_enrollment_key()
        assert mock_row.enrollment_key == 'ABCDEFGH12345678'
        mock_row.save.assert_called_once()


# ---- create_admin_user() -------------------------------------------------

class TestCreateAdminUser:

    @pytest.mark.django_db
    def test_creates_superuser(self):
        user = create_admin_user(
            'admin', 'admin@test.com', 'Str0ng!Pass99',
        )
        assert user.is_superuser
        assert user.username == 'admin'

    @pytest.mark.django_db
    def test_raises_on_duplicate_username(self):
        create_admin_user(
            'admin', 'a@test.com', 'Str0ng!Pass99',
        )
        with pytest.raises(ValidationError, match="already taken"):
            create_admin_user(
                'admin', 'b@test.com', 'Str0ng!Pass99',
            )

    @pytest.mark.django_db
    def test_rejects_short_password(self):
        with pytest.raises(ValidationError):
            create_admin_user('admin', 'a@test.com', 'ab')

    @pytest.mark.django_db
    def test_rejects_numeric_only_password(self):
        with pytest.raises(ValidationError):
            create_admin_user('admin', 'a@test.com', '123456789')

    @pytest.mark.django_db
    def test_rejects_common_password(self):
        with pytest.raises(ValidationError):
            create_admin_user('admin', 'a@test.com', 'password')
