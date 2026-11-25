# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Fixtures for the setup-mode unit tests.

Every test gets its own settings folder, ``LOCALAPPDATA`` and settings
file under ``tmp_path``, no database or data-folder env overrides, a fresh
submit state machine and no installed setup token. ``setup_client`` runs
the setup-mode URLconf and middleware inside the test process (the
process itself runs the run-time settings; ``test_entry_subprocess``
checks the real setup-mode process). On macOS ``tmp_path`` is a folder
under the home folder, since the data-folder checks refuse
``/private/var`` (``tests.unit._off_system_tmp``).
"""
import pytest

from shared.settings_parse import new_parser
from tests.unit._off_system_tmp import off_system_tmp_path_fixture
from tests.unit.manager.setup_mode._helpers import (
    FAKE_HASH, LAN_ADDRESS, MOVING_ENV, RESERVED_PORTS, SETUP_PORT, TOKEN, FakeClock, open_session,
)

tmp_path = off_system_tmp_path_fixture()

SETUP_MIDDLEWARE = ["sethlans_manager.setup_mode.middleware.SecurityHeadersMiddleware"]
SETUP_URLCONF = "sethlans_manager.setup_mode.urls"


@pytest.fixture(autouse=True)
def _isolated_folders(monkeypatch, tmp_path):
    settings_dir = tmp_path / "settings"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("SETHLANS_SETTINGS_DIR", str(settings_dir))
    monkeypatch.setenv("SETHLANS_SETTINGS_FILE", str(settings_dir / "sethlans.ini"))
    for name in MOVING_ENV:
        monkeypatch.delenv(name, raising=False)
    return settings_dir


@pytest.fixture
def settings_dir(_isolated_folders):
    return _isolated_folders


@pytest.fixture
def ini_path(settings_dir):
    return settings_dir / "sethlans.ini"


@pytest.fixture(autouse=True)
def _fresh_setup_state(monkeypatch, tmp_path):
    from sethlans_manager.setup_mode import auth, leftovers, state, views

    monkeypatch.setattr(state, "STATE", state.SubmitState())
    monkeypatch.setattr(auth, "_auth", None)
    monkeypatch.setattr(views, "_server", {"setup_port": SETUP_PORT, "reserved_ports": set(RESERVED_PORTS)})
    monkeypatch.setattr(leftovers, "get_worker_dir", lambda: tmp_path / "install-worker")


@pytest.fixture
def fake_net(monkeypatch):
    """A machine named ``setuphost`` with one LAN address; every port free."""
    from sethlans_manager.setup_mode import net

    monkeypatch.setattr(net, "hostname", lambda: "setuphost")
    monkeypatch.setattr(net, "ipv4_addresses", lambda: [LAN_ADDRESS])
    calls = []

    def port_is_free(host, port):
        calls.append((host, port))
        return True

    monkeypatch.setattr(net, "port_is_free", port_is_free)
    return calls


@pytest.fixture
def clocks():
    return {"limit": FakeClock(), "session": FakeClock()}


@pytest.fixture
def setup_auth(clocks):
    from sethlans_manager.setup_mode import auth

    return auth.configure(
        TOKEN,
        limiter=auth.RateLimiter(clock=clocks["limit"]),
        sessions=auth.Sessions(clock=clocks["session"]),
    )


@pytest.fixture
def setup_client(settings, tmp_path, setup_auth, fake_net):
    """A Django test client on the setup-mode routes, middleware and limits."""
    from django.test import Client

    from sethlans_manager.body_limits import SETUP_BODY_LIMIT

    settings.ROOT_URLCONF = SETUP_URLCONF
    settings.MIDDLEWARE = SETUP_MIDDLEWARE
    settings.ALLOWED_HOSTS = ["localhost", "127.0.0.1", "setuphost", LAN_ADDRESS]
    settings.DATA_UPLOAD_MAX_MEMORY_SIZE = SETUP_BODY_LIMIT
    settings.SETUP_FRONTEND_DIR = tmp_path / "frontend"
    return Client(HTTP_HOST="127.0.0.1", raise_request_exception=False)


@pytest.fixture
def session(setup_client):
    return open_session(setup_client)


# --- validation ---

PLENTY = 500 * 1000 ** 3


@pytest.fixture
def probe(mocker):
    from sethlans_manager.setup_mode import db_probe, validate

    return mocker.patch.object(validate.db_probe, "check_database", return_value=db_probe.AdminAccounts(False, False))


@pytest.fixture
def free_space(mocker):
    from shared import data_folder

    return mocker.patch.object(data_folder, "free_bytes", return_value=PLENTY)


@pytest.fixture
def hasher(mocker):
    from sethlans_manager.setup_mode import validate_admin

    return mocker.patch.object(validate_admin, "make_password", return_value=FAKE_HASH)


@pytest.fixture
def ctx():
    from sethlans_manager.setup_mode.validate import Context

    return Context(new_parser(), SETUP_PORT, set(RESERVED_PORTS))


@pytest.fixture
def check(fake_net, probe, free_space, hasher, ctx):
    """``check(answers, ctx=None) -> (errors dict, plan)``."""
    from sethlans_manager.setup_mode.validate import validate

    def run(answers, context=None):
        errors, plan = validate(answers, context or ctx)
        return errors.by_field, plan

    return run
