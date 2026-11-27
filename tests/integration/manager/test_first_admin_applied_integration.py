# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
FR-FS-1, the recorded ``[first_admin]``: ``ManagerSettings.first_admin_applied``
holds the SHA-256 digest of the hash the first start applied, so a leftover
section (its removal failed) never touches the account again, while a Reset
(a new hash) still applies.

Real test database, real ``sethlans.ini`` writer and reader, real
``make_password`` hashes. Only the section removal is made to fail where a
case needs it. The digest after create, the equal-hash retry, replace and
exit 12 is checked in ``test_first_start_integration.py``.
"""
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password

from sethlans_manager.first_start.admin_from_hash import ALREADY_APPLIED, REPLACED, apply_first_admin
from sethlans_manager.first_start.common import FirstStartError
from shared import settings_file
from shared.manager_exit_codes import EXIT_FIRST_START_FAILED, EXIT_NO_ADMIN
from workers.models import ManagerSettings, Worker

from .first_start_helpers import (
    PASSWORD, USERNAME, digest_of, finish_setup, first_admin, first_start, has_first_admin, recorded_digest,
)

pytestmark = pytest.mark.django_db

User = get_user_model()

CHANGED_PASSWORD = "Changed-Admin-Secret-3e7d01"


@pytest.fixture(scope="module")
def setup_hash():
    """The hash setup mode writes for ``PASSWORD`` (one per module: each costs about half a second)."""
    return make_password(PASSWORD)


@pytest.fixture(scope="module")
def reset_hash():
    """A Reset with the same password typed again: a new salt, so a new hash."""
    return make_password(PASSWORD)


@pytest.fixture
def ini():
    """The per-test settings file (``SETHLANS_SETTINGS_FILE``, set by the integration conftest)."""
    return settings_file.settings_path()


def _removal_fails(section, ini_path):
    raise PermissionError(13, "Access is denied", str(ini_path))


class TestFirstAdminAppliedIntegration:
    def test_leftover_section_keeps_the_changed_password(self, ini, setup_hash, monkeypatch):
        """W1 (chunk 3 review), FR-FS-1: a section whose removal failed is already_applied next time; no revert."""
        finish_setup(ini, first_admin=first_admin(setup_hash, replace="true"))
        with monkeypatch.context() as patch:
            patch.setattr(settings_file, "remove_section", _removal_fails)
            assert first_start(ini) is None
        assert has_first_admin(ini)
        admin = User.objects.get(username=USERNAME)
        admin.set_password(CHANGED_PASSWORD)
        admin.save(update_fields=["password"])

        assert apply_first_admin(ini) == ALREADY_APPLIED

        admin.refresh_from_db()
        assert admin.check_password(CHANGED_PASSWORD)
        assert recorded_digest() == digest_of(setup_hash)
        assert not has_first_admin(ini)  # the removal is tried again

    def test_reset_hash_is_applied_and_recorded(self, ini, setup_hash, reset_hash):
        """FR-FS-1: a Reset writes a new hash for the same username (replace_password); it applies, digest updated."""
        assert reset_hash != setup_hash
        finish_setup(ini, first_admin=first_admin(setup_hash))
        assert first_start(ini) is None
        finish_setup(ini, first_admin=first_admin(reset_hash, replace="true"))

        assert apply_first_admin(ini) == REPLACED

        assert User.objects.get(username=USERNAME).password == reset_hash
        assert recorded_digest() == digest_of(reset_hash)
        assert not has_first_admin(ini)

    def test_missing_settings_row_exits_11_before_the_account(self, ini, setup_hash):
        """FR-FS-1: no ManagerSettings row (database not migrated) is exit 11; no account, the section is kept."""
        ManagerSettings.objects.all().delete()
        finish_setup(ini, first_admin=first_admin(setup_hash))
        before = ini.read_bytes()

        assert first_start(ini) == EXIT_FIRST_START_FAILED

        assert not User.objects.exists()
        assert ini.read_bytes() == before

    def test_replace_password_on_a_worker_service_account_exits_12(self, ini, setup_hash):
        """FR-FS-1: replace_password never makes a worker's service account the admin: exit 12, account unchanged."""
        account = User.objects.create_user(USERNAME)
        account.set_unusable_password()
        account.save(update_fields=["password"])
        Worker.objects.create(hostname="render-01", user=account, is_active=True)
        before = User.objects.values().get(pk=account.pk)
        finish_setup(ini, first_admin=first_admin(setup_hash, replace="true"))

        with pytest.raises(FirstStartError) as caught:
            apply_first_admin(ini)

        assert caught.value.code == EXIT_NO_ADMIN
        assert User.objects.values().get(pk=account.pk) == before
        assert recorded_digest() == ""
        assert has_first_admin(ini)
