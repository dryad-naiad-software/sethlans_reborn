# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The validation table: the whole form, mode, bind address, port and data folder."""
import sys

import pytest

from tests.unit.manager.setup_mode._helpers import LAN_ADDRESS, SETUP_PORT, answers

ROOT = "C:\\" if sys.platform == "win32" else "/"


class TestWholeForm:

    def test_valid_form_gives_a_plan(self, check, tmp_path):
        errors, plan = check(answers(tmp_path))
        assert errors == {}
        assert (plan.mode, plan.server_host, plan.server_port) == ("manager", "0.0.0.0", 18444)
        assert str(plan.data_root) == str(tmp_path / "sethlans-data")
        assert plan.database == {"engine": "sqlite"}
        assert plan.first_admin["username"] == "boss"
        assert plan.warnings == []

    @pytest.mark.parametrize("value", [[], "text", 5, None])
    def test_not_an_object(self, check, value):
        assert check(value) == ({"non_field_errors": ["The form must be a JSON object."]}, None)

    def test_every_error_is_reported_at_once(self, check, tmp_path, probe):
        errors, plan = check(answers(tmp_path, mode="bogus", server={"host": "10.255.255.1", "port": 80},
                                     data_folder={"path": "relative"},
                                     admin={"username": "bad name!", "password": ""}))
        assert plan is None
        assert set(errors) == {"mode", "server.host", "server.port", "data_folder.path", "admin.username",
                               "admin.password"}
        probe.assert_not_called()  # no data folder, so no database test

    def test_validation_writes_nothing(self, check, tmp_path, ini_path):
        check(answers(tmp_path))
        assert not (tmp_path / "sethlans-data").exists()
        assert not ini_path.exists()


class TestMode:

    @pytest.mark.parametrize("mode, message", [
        (None, "The mode is required."),
        ("", "The mode is required."),
        ("bogus", "Choose one of manager, manager_worker, worker."),
        (3, "The mode must be text."),
    ])
    def test_mode_errors(self, check, tmp_path, mode, message):
        errors, _plan = check(answers(tmp_path, mode=mode))
        assert errors == {"mode": [message]}


class TestServer:

    @pytest.mark.parametrize("host", ["0.0.0.0", "127.0.0.1", LAN_ADDRESS])
    def test_bind_addresses_accepted(self, check, tmp_path, host):
        errors, plan = check(answers(tmp_path, server={"host": host, "port": 18444}))
        assert errors == {} and plan.server_host == host

    @pytest.mark.parametrize("host, message", [
        ("10.255.255.1", "Choose 0.0.0.0, 127.0.0.1 or an address of this machine."),
        ("localhost", "Choose 0.0.0.0, 127.0.0.1 or an address of this machine."),
        ("", "The bind address is required."),
        ("0.0.0.0\n", None),
    ])
    def test_bind_address_errors(self, check, tmp_path, host, message):
        errors, _plan = check(answers(tmp_path, server={"host": host, "port": 18444}))
        if message is None:  # surrounding white space is stripped
            assert "server.host" not in errors
        else:
            assert errors["server.host"] == [message]

    @pytest.mark.parametrize("port, message", [
        (1023, "The port must be between 1024 and 65535."),
        (65536, "The port must be between 1024 and 65535."),
        ("abc", "The port must be a whole number."),
        (True, "The port must be a whole number."),
        (8080.5, "The port must be a whole number."),
        ("8²", "The port must be a whole number."),
        ("٣٣٣٣", "The port must be a whole number."),
        ("+9443", "The port must be a whole number."),
        (None, "The port is required."),
    ])
    def test_port_errors(self, check, tmp_path, port, message):
        errors, _plan = check(answers(tmp_path, server={"host": "0.0.0.0", "port": port}))
        assert errors == {"server.port": [message]}

    @pytest.mark.parametrize("port", [1024, 65535, "8443"])
    def test_port_bounds_and_digit_strings(self, check, tmp_path, port):
        errors, plan = check(answers(tmp_path, server={"host": "0.0.0.0", "port": port}))
        assert errors == {} and plan.server_port == int(port)

    @pytest.mark.parametrize("port", [8090, 8088])
    def test_reserved_loopback_ports(self, check, tmp_path, port):
        errors, _plan = check(answers(tmp_path, server={"host": "0.0.0.0", "port": port}))
        assert errors == {"server.port": [f"Port {port} is used by the manager's own loopback listeners."]}

    def test_port_in_use(self, check, tmp_path, monkeypatch, fake_net):
        from sethlans_manager.setup_mode import net

        monkeypatch.setattr(net, "port_is_free", lambda host, port: False)
        errors, _plan = check(answers(tmp_path, server={"host": LAN_ADDRESS, "port": 9000}))
        assert errors == {"server.port": [f"Port 9000 is in use on {LAN_ADDRESS}."]}

    def test_port_checked_on_the_chosen_address(self, check, tmp_path, fake_net):
        check(answers(tmp_path, server={"host": LAN_ADDRESS, "port": 9000}))
        assert fake_net == [(LAN_ADDRESS, 9000)]

    def test_setup_port_is_exempt_from_the_in_use_check(self, check, tmp_path, monkeypatch, fake_net):
        from sethlans_manager.setup_mode import net

        monkeypatch.setattr(net, "port_is_free", lambda host, port: False)
        errors, plan = check(answers(tmp_path, server={"host": "0.0.0.0", "port": SETUP_PORT}))
        assert errors == {} and plan.server_port == SETUP_PORT

    @pytest.mark.parametrize("value", ["0.0.0.0:80", ["x"], 1])
    def test_server_not_an_object(self, check, tmp_path, value):
        errors, _plan = check(answers(tmp_path, server=value))
        assert errors["server"] == ["Must be an object."]


class TestDataFolder:

    @pytest.mark.parametrize("value, message", [
        (None, "The data folder is required."),
        ("  ", "The data folder is required."),
        ("relative/data", "The data folder must be an absolute path."),
        (ROOT, f"{ROOT} is the root of a drive or file system"),
    ])
    def test_errors(self, check, tmp_path, value, message):
        errors, _plan = check(answers(tmp_path, data_folder={"path": value}))
        assert errors == {"data_folder.path": [message]}

    @pytest.mark.parametrize("value", [None, "/data", ["/data"]])
    def test_must_be_the_forms_object(self, check, tmp_path, value):
        errors, _plan = check(answers(tmp_path, data_folder=value))
        assert errors == {"data_folder": ['The data folder must be sent as {"path": "<absolute folder>"}, '
                                          "as the form gives it."]}

    def test_read_only_keys_of_the_form_are_ignored(self, check, tmp_path):
        folder = {"path": str(tmp_path / "d"), "free_bytes": "junk", "low_space": "junk"}
        errors, plan = check(answers(tmp_path, data_folder=folder))
        assert errors == {} and str(plan.data_root) == str(tmp_path / "d")

    def test_control_character(self, check, tmp_path):
        errors, _plan = check(answers(tmp_path, data_folder={"path": str(tmp_path / "ab")}))
        assert errors == {"data_folder.path": ["The data folder contains a control character."]}

    def test_too_long(self, check, tmp_path):
        errors, _plan = check(answers(tmp_path, data_folder={"path": "/" + "a" * 4096}))
        assert errors == {"data_folder.path": ["The data folder is longer than 4096 characters."]}

    def test_a_file_is_refused(self, check, tmp_path):
        target = tmp_path / "file"
        target.write_text("x")
        errors, _plan = check(answers(tmp_path, data_folder={"path": str(target)}))
        assert errors == {"data_folder.path": [f"{target} is a link or not a folder; choose a plain folder."]}


class TestLowSpaceWarning:

    @pytest.mark.parametrize("free, warned", [(19_999_999_999, True), (20 * 1000 ** 3, False), (None, False)])
    def test_threshold_is_20_gb(self, check, tmp_path, free_space, free, warned):
        free_space.return_value = free
        errors, plan = check(answers(tmp_path))
        assert errors == {}
        assert bool(plan.warnings) is warned

    def test_warning_text(self, check, tmp_path, free_space):
        free_space.return_value = 5 * 1000 ** 3 + 7
        _errors, plan = check(answers(tmp_path))
        root = tmp_path / "sethlans-data"
        assert plan.warnings == [f"Only 5 GB are free on the drive of {root}; renders and tools need more "
                                 "(20 GB or more)."]
