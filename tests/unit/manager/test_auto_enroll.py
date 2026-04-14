# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``manager/workers/services/auto_enroll.py``.

Covers:
  - ``auto_enroll_local_worker()`` — happy path, RuntimeError guards,
    hostname defaulting, ORM side effects (Worker/User/Token creation),
    manager_url construction from manager.ini.
"""

import pytest

from sethlans_manager import runtime_state
from workers.models import Worker
from workers.services.auto_enroll import auto_enroll_local_worker


FAKE_MANAGER_ID = "test-mgr-uuid-0001"
FAKE_FINGERPRINT = "bb" * 32
FAKE_HOSTNAME = "test-render-box"


# ---- Fixtures -------------------------------------------------------------


@pytest.fixture(autouse=True)
def _set_runtime_state():
    """Populate runtime_state for the duration of each test."""
    prev_mid = runtime_state.manager_id
    prev_fp = runtime_state.cert_fingerprint
    runtime_state.manager_id = FAKE_MANAGER_ID
    runtime_state.cert_fingerprint = FAKE_FINGERPRINT
    yield
    runtime_state.manager_id = prev_mid
    runtime_state.cert_fingerprint = prev_fp


@pytest.fixture
def manager_ini(tmp_path, settings):
    """Point settings.BASE_DIR at tmp_path and create a manager.ini."""
    settings.BASE_DIR = tmp_path
    ini_path = tmp_path / "manager.ini"
    ini_path.write_text(
        "[server]\nport = 9090\n", encoding="utf-8",
    )
    return ini_path


@pytest.fixture
def no_manager_ini(tmp_path, settings):
    """Point settings.BASE_DIR at tmp_path with no manager.ini."""
    settings.BASE_DIR = tmp_path
    return tmp_path


# ---- RuntimeError guards --------------------------------------------------


@pytest.mark.django_db
class TestRuntimeGuards:

    def test_raises_when_manager_id_is_none(self):
        runtime_state.manager_id = None
        with pytest.raises(RuntimeError, match="manager_id is None"):
            auto_enroll_local_worker(hostname=FAKE_HOSTNAME)

    def test_raises_when_cert_fingerprint_is_none(self):
        runtime_state.cert_fingerprint = None
        with pytest.raises(
            RuntimeError, match="cert_fingerprint.*None",
        ):
            auto_enroll_local_worker(hostname=FAKE_HOSTNAME)

    def test_manager_id_checked_before_fingerprint(self):
        runtime_state.manager_id = None
        runtime_state.cert_fingerprint = None
        with pytest.raises(RuntimeError, match="manager_id"):
            auto_enroll_local_worker(hostname=FAKE_HOSTNAME)


# ---- Happy path -----------------------------------------------------------


@pytest.mark.django_db
class TestHappyPath:

    def test_returns_dict_with_expected_keys(self, no_manager_ini):
        result = auto_enroll_local_worker(hostname=FAKE_HOSTNAME)
        assert set(result.keys()) == {
            "api_token", "cert_fingerprint", "manager_id",
            "manager_url", "hostname",
        }

    def test_cert_fingerprint_matches_runtime(self, no_manager_ini):
        result = auto_enroll_local_worker(hostname=FAKE_HOSTNAME)
        assert result["cert_fingerprint"] == FAKE_FINGERPRINT

    def test_manager_id_matches_runtime(self, no_manager_ini):
        result = auto_enroll_local_worker(hostname=FAKE_HOSTNAME)
        assert result["manager_id"] == FAKE_MANAGER_ID

    def test_hostname_returned(self, no_manager_ini):
        result = auto_enroll_local_worker(hostname=FAKE_HOSTNAME)
        assert result["hostname"] == FAKE_HOSTNAME

    def test_api_token_is_nonempty_string(self, no_manager_ini):
        result = auto_enroll_local_worker(hostname=FAKE_HOSTNAME)
        assert isinstance(result["api_token"], str)
        assert len(result["api_token"]) > 0

    def test_api_token_is_40_chars(self, no_manager_ini):
        result = auto_enroll_local_worker(hostname=FAKE_HOSTNAME)
        assert len(result["api_token"]) == 40


# ---- Hostname defaulting --------------------------------------------------


@pytest.mark.django_db
class TestHostnameDefault:

    def test_defaults_to_socket_gethostname(
        self, no_manager_ini, mocker,
    ):
        mocker.patch(
            "workers.services.auto_enroll.socket.gethostname",
            return_value="auto-detected-host",
        )
        result = auto_enroll_local_worker(hostname=None)
        assert result["hostname"] == "auto-detected-host"

    def test_explicit_hostname_overrides_default(
        self, no_manager_ini,
    ):
        result = auto_enroll_local_worker(hostname="explicit-host")
        assert result["hostname"] == "explicit-host"


# ---- ORM side effects -----------------------------------------------------


@pytest.mark.django_db
class TestOrmSideEffects:

    def test_creates_worker_row(self, no_manager_ini):
        auto_enroll_local_worker(hostname=FAKE_HOSTNAME)
        assert Worker.objects.filter(
            hostname=FAKE_HOSTNAME,
        ).exists()

    def test_worker_is_active(self, no_manager_ini):
        auto_enroll_local_worker(hostname=FAKE_HOSTNAME)
        worker = Worker.objects.get(hostname=FAKE_HOSTNAME)
        assert worker.is_active is True

    def test_worker_has_user(self, no_manager_ini):
        auto_enroll_local_worker(hostname=FAKE_HOSTNAME)
        worker = Worker.objects.get(hostname=FAKE_HOSTNAME)
        assert worker.user is not None
        assert worker.user.username == f"worker_{FAKE_HOSTNAME}"

    def test_creates_drf_token(self, no_manager_ini):
        from rest_framework.authtoken.models import Token
        result = auto_enroll_local_worker(hostname=FAKE_HOSTNAME)
        assert Token.objects.filter(key=result["api_token"]).exists()

    def test_worker_user_has_unusable_password(self, no_manager_ini):
        auto_enroll_local_worker(hostname=FAKE_HOSTNAME)
        worker = Worker.objects.get(hostname=FAKE_HOSTNAME)
        assert worker.user.has_usable_password() is False

    def test_idempotent_reuses_existing_worker(self, no_manager_ini):
        result1 = auto_enroll_local_worker(hostname=FAKE_HOSTNAME)
        result2 = auto_enroll_local_worker(hostname=FAKE_HOSTNAME)
        assert Worker.objects.filter(
            hostname=FAKE_HOSTNAME,
        ).count() == 1
        # Token should be the same since user is reused
        assert result1["api_token"] == result2["api_token"]


# ---- manager_url construction ---------------------------------------------


@pytest.mark.django_db
class TestManagerUrl:

    def test_uses_loopback_address(self, no_manager_ini):
        result = auto_enroll_local_worker(hostname=FAKE_HOSTNAME)
        assert "127.0.0.1" in result["manager_url"]

    def test_default_port_8080_without_ini(self, no_manager_ini):
        result = auto_enroll_local_worker(hostname=FAKE_HOSTNAME)
        assert result["manager_url"] == "https://127.0.0.1:8080"

    def test_reads_port_from_manager_ini(self, manager_ini):
        result = auto_enroll_local_worker(hostname=FAKE_HOSTNAME)
        assert result["manager_url"] == "https://127.0.0.1:9090"

    def test_url_uses_https_scheme(self, no_manager_ini):
        result = auto_enroll_local_worker(hostname=FAKE_HOSTNAME)
        assert result["manager_url"].startswith("https://")
