# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The ``settings_setup`` module and ``net.allowed_hosts`` (read, never activated here)."""
import importlib
import socket
from collections import namedtuple

import pytest
from django.conf import Settings
from django.conf import settings as runtime_settings

from sethlans_manager.setup_mode import net

Addr = namedtuple("Addr", "family address")


@pytest.fixture
def machine(monkeypatch):
    monkeypatch.setattr(net.socket, "gethostname", lambda: "box")
    monkeypatch.setattr(net.socket, "getfqdn", lambda: "box.lan.example")
    monkeypatch.setattr(net.psutil, "net_if_addrs", lambda: {
        "eth0": [Addr(socket.AF_INET, "192.168.1.20"), Addr(socket.AF_INET6, "fe80::1")],
        "lo": [Addr(socket.AF_INET, "127.0.0.1")],
        "wg0": [Addr(socket.AF_INET, "10.0.0.3")],
    })


def _load_setup_settings():
    """Re-run ``settings_setup`` against the current environment."""
    module = importlib.import_module("sethlans_manager.settings_setup")
    return importlib.reload(module)


class TestAllowedHosts:

    def test_names_and_addresses(self, machine):
        assert net.allowed_hosts() == ["localhost", "127.0.0.1", "box", "box.local", "box.lan.example",
                                       "10.0.0.3", "192.168.1.20"]

    def test_earlier_server_host_is_added_once(self, machine):
        assert net.allowed_hosts("192.168.1.99")[-1] == "192.168.1.99"
        assert net.allowed_hosts("10.0.0.3").count("10.0.0.3") == 1

    def test_any_address_is_never_a_host(self, machine):
        assert "0.0.0.0" not in net.allowed_hosts("0.0.0.0")

    def test_fqdn_equal_to_the_hostname_is_not_repeated(self, machine, monkeypatch):
        monkeypatch.setattr(net.socket, "getfqdn", lambda: "box")
        assert net.allowed_hosts().count("box") == 1

    def test_bind_addresses(self, machine):
        assert net.bind_addresses() == ["0.0.0.0", "127.0.0.1", "10.0.0.3", "192.168.1.20"]

    @pytest.mark.parametrize("host, loopback", [
        ("localhost", True), (" LOCALHOST ", True), ("127.0.0.1", True), ("127.8.8.8", True),
        ("::1", True), ("[::1]", True), ("db.example", False), ("192.168.1.5", False),
    ])
    def test_is_loopback_host(self, host, loopback):
        assert net.is_loopback_host(host) is loopback


class TestSettingsSetupModule:

    def test_no_workers_no_database_no_debug(self, machine):
        module = _load_setup_settings()
        assert module.INSTALLED_APPS == ["django.contrib.auth", "django.contrib.contenttypes",
                                         "sethlans_manager.setup_mode"]
        assert module.DATABASES == {}
        # pytest.ini sets SETHLANS_SECURITY_DEBUG=True; setup mode ignores it.
        assert module.DEBUG is False
        assert module.ROOT_URLCONF == "sethlans_manager.setup_mode.urls"
        assert module.MIDDLEWARE == ["sethlans_manager.setup_mode.middleware.SecurityHeadersMiddleware"]

    def test_body_limit_is_64_kib(self, machine):
        module = _load_setup_settings()
        assert module.DATA_UPLOAD_MAX_MEMORY_SIZE == 65536

    def test_hosts_include_the_earlier_server_host(self, machine, ini_path):
        ini_path.parent.mkdir(parents=True)
        ini_path.write_text("[server]\nhost = 192.168.1.77\n")
        module = _load_setup_settings()
        assert module.ALLOWED_HOSTS[-1] == "192.168.1.77"
        assert module.USE_X_FORWARDED_HOST is False

    def test_unreadable_settings_file_still_loads(self, machine, ini_path):
        ini_path.parent.mkdir(parents=True)
        ini_path.write_text("garbage without a section")
        module = _load_setup_settings()
        assert "localhost" in module.ALLOWED_HOSTS

    def test_secret_key_is_new_each_load(self, machine):
        first = _load_setup_settings().SECRET_KEY
        assert _load_setup_settings().SECRET_KEY != first
        assert len(first) >= 50

    def test_effective_validators_and_hashers_equal_runtime(self, machine):
        setup = Settings("sethlans_manager.settings_setup")
        assert setup.AUTH_PASSWORD_VALIDATORS == runtime_settings.AUTH_PASSWORD_VALIDATORS
        assert setup.PASSWORD_HASHERS == runtime_settings.PASSWORD_HASHERS

    def test_neither_module_overrides_the_default_hashers(self):
        import sethlans_manager.settings as runtime_module
        import sethlans_manager.settings_setup as setup_module

        assert not hasattr(setup_module, "PASSWORD_HASHERS")
        assert not hasattr(runtime_module, "PASSWORD_HASHERS")
