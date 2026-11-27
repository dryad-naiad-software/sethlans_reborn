# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The manager's first-start step (``sethlans_manager.first_start``) with the
real test database, the real ``sethlans.ini`` writer and reader, a real
``make_password`` hash, the real login view, and the real local-worker
enrollment and ``config.json`` writer.

``require_first_start`` is called in process, as ``run_manager`` calls it
after ``_prepare_runtime``; an exit is its ``SystemExit`` code. The real
``run_manager`` subprocess case (#307) is in
``setup_mode/test_first_start_process_integration.py``; the recorded
``first_admin_applied`` digest cases are in
``test_first_admin_applied_integration.py``.
"""
import json
import os
import uuid

import pytest
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.hashers import make_password
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from sethlans_manager.first_start.admin_from_env import ADMIN_ENV_NAMES
from shared import settings_file
from shared.frozen_paths import get_identity_dir
from shared.manager_exit_codes import EXIT_FIRST_START_FAILED, EXIT_NO_ADMIN
from workers.services import filesystem_trust

from .first_start_helpers import (
    OLD_PASSWORD, PASSWORD, USERNAME, digest_of, finish_setup, first_admin, first_start, has_first_admin,
    recorded_digest, superuser,
)

pytestmark = pytest.mark.django_db

User = get_user_model()

LOGIN_URL = "/api/auth/login/"


@pytest.fixture(scope="module")
def setup_hash():
    """The hash setup mode writes for ``PASSWORD`` (one per module: each costs about half a second)."""
    return make_password(PASSWORD)


@pytest.fixture(scope="module")
def old_hash():
    return make_password(OLD_PASSWORD)


@pytest.fixture
def ini():
    """The per-test settings file (``SETHLANS_SETTINGS_FILE``, set by the integration conftest)."""
    return settings_file.settings_path()


class TestFirstAdminFromSetupIntegration:
    def test_setup_then_first_start_makes_the_admin_once(self, ini, setup_hash):
        """FR-FS-1, FR-FS-4: the hash setup wrote becomes a superuser that logs in; a second start changes nothing."""
        finish_setup(ini, first_admin=first_admin(setup_hash))

        assert first_start(ini) is None

        user = User.objects.get(username=USERNAME)
        assert (user.is_superuser, user.is_staff, user.is_active) == (True, True, True)
        assert user.email == "admin@example.com"
        login = APIClient().post(LOGIN_URL, {"username": USERNAME, "password": PASSWORD}, format="json")
        assert login.status_code == 200, login.data
        assert not has_first_admin(ini)
        assert settings_file.setup_finished(ini) == "manager"
        assert recorded_digest() == digest_of(setup_hash)

        rows, saved = list(User.objects.values()), ini.read_bytes()
        assert first_start(ini) is None
        assert list(User.objects.values()) == rows
        assert ini.read_bytes() == saved

    @pytest.mark.parametrize("stored, replace, expected_exit, expected_hash, section_kept", [
        pytest.param("setup", "false", None, "setup", False, id="equal-hash-is-a-retry"),
        pytest.param("old", "true", None, "setup", False, id="replace-password"),
        pytest.param("old", "false", EXIT_NO_ADMIN, "old", True, id="neither-exits-12"),
    ])
    def test_username_clash(self, ini, setup_hash, old_hash, stored, replace, expected_exit,
                            expected_hash, section_kept):
        """FR-FS-1, OQ-2 (AC-10): equal hash no-op, replace_password, or exit 12; the digest only on success."""
        hashes = {"setup": setup_hash, "old": old_hash}
        superuser(USERNAME, hashes[stored])
        finish_setup(ini, first_admin=first_admin(setup_hash, replace))
        before = ini.read_bytes()

        assert first_start(ini) == expected_exit

        user = User.objects.get(username=USERNAME)
        assert User.objects.count() == 1
        assert user.password == hashes[expected_hash]
        assert (user.is_superuser, user.is_staff, user.is_active) == (True, True, True)
        assert has_first_admin(ini) is section_kept
        assert recorded_digest() == ("" if section_kept else digest_of(setup_hash))
        if section_kept:
            assert ini.read_bytes() == before

    def test_bad_hash_exits_11_and_keeps_the_section(self, ini, setup_hash, capsys, caplog):
        """FR-FS-1: a hash the default hasher cannot decode is exit 11; nothing is made, the section is kept."""
        bad_hash = setup_hash.rsplit("$", 1)[0]  # the digest is cut off
        finish_setup(ini, first_admin=first_admin(bad_hash))
        before = ini.read_bytes()

        assert first_start(ini) == EXIT_FIRST_START_FAILED

        assert not User.objects.exists()
        assert ini.read_bytes() == before
        assert bad_hash not in capsys.readouterr().err
        assert bad_hash not in caplog.text

    def test_no_usable_admin_exits_12(self, ini):
        """FR-FS-2a: no [first_admin], no SETHLANS_ADMIN_*, and no active superuser is exit 12."""
        finish_setup(ini)
        superuser("retired", is_active=False)
        User.objects.create_user("artist")

        assert first_start(ini) == EXIT_NO_ADMIN

        assert not User.objects.filter(is_superuser=True, is_active=True).exists()


class TestAdminFromEnvironmentIntegration:
    @pytest.mark.parametrize("admin_exists, password, expected_exit, created", [
        pytest.param(False, PASSWORD, None, True, id="valid"),
        pytest.param(False, "password", EXIT_FIRST_START_FAILED, False, id="invalid-exits-11"),
        pytest.param(True, PASSWORD, None, False, id="superuser-exists-ignored"),
    ])
    def test_admin_from_environment(self, ini, tmp_path, monkeypatch, admin_exists, password,
                                    expected_exit, created):
        """FR-FS-2 (FR-DK-3): SETHLANS_ADMIN_* make the first admin; the variables leave os.environ."""
        finish_setup(ini)
        if admin_exists:
            superuser(USERNAME)
        secret = tmp_path / "admin_password"
        secret.write_bytes(password.encode("utf-8") + b"\n")  # secret files end with a line end
        monkeypatch.setenv("SETHLANS_ADMIN_USERNAME", "dockadmin")
        monkeypatch.setenv("SETHLANS_ADMIN_PASSWORD_FILE", str(secret))
        monkeypatch.setenv("SETHLANS_ADMIN_EMAIL", "dock@example.com")

        assert first_start(ini) == expected_exit

        assert not [name for name in ADMIN_ENV_NAMES if name in os.environ]
        assert User.objects.filter(username="dockadmin").exists() is created
        if created:
            user = authenticate(username="dockadmin", password=PASSWORD)
            assert user is not None and user.is_superuser and user.is_staff
            assert user.email == "dock@example.com"


def _worker_manager_section(config_path):
    return json.loads(config_path.read_text(encoding="utf-8"))["manager"]


class TestLocalWorkerTrustIntegration:
    @pytest.mark.parametrize("stale", ["manager_id", "deleted_token"])
    def test_trust_runs_again_when_the_worker_is_stale(self, ini, ready_runtime_state, stale):
        """FR-FS-3, FR-FS-4: trust writes config.json once, again only after a new manager_id or a lost token."""
        finish_setup(ini, mode="manager_worker")
        superuser(USERNAME)
        config_path = filesystem_trust.get_worker_config_path()

        assert first_start(ini) is None
        first = _worker_manager_section(config_path)
        assert first["manager_id"] == ready_runtime_state.manager_id
        assert Token.objects.filter(key=first["api_token"]).exists()
        assert first_start(ini) is None
        assert _worker_manager_section(config_path) == first

        if stale == "manager_id":
            ready_runtime_state.manager_id = str(uuid.uuid4())
        else:
            Token.objects.filter(key=first["api_token"]).delete()
        assert first_start(ini) is None

        again = _worker_manager_section(config_path)
        assert again["api_token"] != first["api_token"]
        assert again["manager_id"] == ready_runtime_state.manager_id
        assert Token.objects.filter(key=again["api_token"]).exists()
        assert not Token.objects.filter(key=first["api_token"]).exists()

    def test_no_trust_in_manager_mode(self, ini, ready_runtime_state):
        """FR-FS-3: a manager-only machine has no local worker; no token and no config.json are made."""
        finish_setup(ini, mode="manager")
        superuser(USERNAME)

        assert first_start(ini) is None

        assert not Token.objects.exists()
        assert not (get_identity_dir("worker") / filesystem_trust.CONFIG_FILENAME).exists()
