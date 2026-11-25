# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``setup_mode.entry``: arguments, the settings guard, the setup port and the start order."""
import os

import pytest

from sethlans_manager.setup_mode import entry
from tests.unit.manager.setup_mode._helpers import TOKEN


class TestArgumentProblems:

    @pytest.mark.parametrize("argv, expected", [
        (["--setup", "--hash-password"], "--setup and --hash-password cannot be combined"),
        (["--setup", "--dev"], "--dev cannot be combined with --setup or --hash-password"),
        (["--hash-password", "--manage", "migrate"], "--manage cannot be combined with --setup or --hash-password"),
        (["--setup", "--answers", "x.ini"], "--answers is not available yet"),
        (["--setup", "extra"], "Unknown arguments: extra"),
        (["--hash-password", "-v", "x"], "Unknown arguments: -v x"),
    ])
    def test_problem_message(self, argv, expected):
        assert entry._argument_problem(argv) == expected

    @pytest.mark.parametrize("argv", [["--setup"], ["--hash-password"]])
    def test_single_flag_is_fine(self, argv):
        assert entry._argument_problem(argv) == ""

    def test_run_exits_2_before_touching_django(self, mocker, capsys, monkeypatch):
        monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "sethlans_manager.settings")
        setup = mocker.patch.object(entry, "_setup_django")
        assert entry.run(["--setup", "--dev"]) == entry.EXIT_USAGE
        setup.assert_not_called()
        assert os.environ["DJANGO_SETTINGS_MODULE"] == "sethlans_manager.settings"
        assert capsys.readouterr().err.startswith("[ERROR] --dev cannot be combined")


class TestSettingsGuard:

    def test_runtime_settings_in_this_process_are_refused(self):
        # The test process runs the run-time settings with ``workers``.
        problem = entry._setup_django()
        assert problem == ("Setup mode needs sethlans_manager.settings_setup, "
                           "but sethlans_manager.settings was loaded")

    def test_workers_import_is_refused_even_with_the_setup_module(self, settings):
        settings.SETTINGS_MODULE = entry.SETUP_SETTINGS
        # ``workers`` is installed and imported in this process (the run-time app).
        assert entry._setup_django() == "Setup mode must not load the workers app"

    def test_run_assigns_the_setup_module_and_stops_on_a_problem(self, mocker, monkeypatch, capsys):
        monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "sethlans_manager.settings")
        mocker.patch.object(entry, "_setup_django", return_value="bad settings")
        serve = mocker.patch.object(entry, "serve_setup")
        assert entry.run(["--setup"]) == entry.EXIT_USAGE
        assert os.environ["DJANGO_SETTINGS_MODULE"] == entry.SETUP_SETTINGS
        serve.assert_not_called()
        assert "[ERROR] bad settings" in capsys.readouterr().err

    def test_run_dispatches_hash_password(self, mocker, monkeypatch):
        monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "sethlans_manager.settings")
        mocker.patch.object(entry, "_setup_django", return_value="")
        hasher = mocker.patch.object(entry, "hash_password", return_value=0)
        serve = mocker.patch.object(entry, "serve_setup")
        assert entry.run(["--hash-password"]) == 0
        hasher.assert_called_once()
        serve.assert_not_called()


class TestSetupPort:

    def test_env_wins(self, monkeypatch, ini_path):
        ini_path.parent.mkdir(parents=True)
        ini_path.write_text("[server]\nport = 9443\n")
        monkeypatch.setenv("SETHLANS_SETUP_PORT", " 9555 ")
        assert entry.setup_port(ini_path) == 9555

    def test_earlier_server_port(self, ini_path):
        ini_path.parent.mkdir(parents=True)
        ini_path.write_text("[server]\nport = 9443\n")
        assert entry.setup_port(ini_path) == 9443

    def test_default_without_file(self, ini_path):
        assert entry.setup_port(ini_path) == 8080

    @pytest.mark.parametrize("text", ["[server]\nport = nope\n", "not an ini file"])
    def test_default_for_unusable_values(self, ini_path, text):
        ini_path.parent.mkdir(parents=True)
        ini_path.write_text(text)
        assert entry.setup_port(ini_path) == 8080


@pytest.fixture
def start(mocker, settings, tmp_path):
    """``serve_setup`` with logging, the server and the leftovers check replaced."""
    from sethlans_manager.setup_mode import leftovers, logging_setup, serve, views

    settings.SETUP_FRONTEND_DIR = tmp_path / "frontend"
    calls = []
    mocker.patch.object(logging_setup, "build_logging", return_value={})
    mocker.patch.object(logging_setup, "configure")
    mocker.patch.object(leftovers, "remove_pending_setup", side_effect=lambda: calls.append("leftovers"))
    mocker.patch.object(serve, "run", side_effect=lambda port: calls.append(("serve", port)) or 10)
    configure = mocker.patch.object(views, "configure")
    return calls, configure


def _write_token(settings_dir, data=TOKEN.encode()):
    folder = settings_dir / "setup"
    folder.mkdir(parents=True)
    (folder / "setup_token").write_bytes(data)


class TestServeSetup:

    def test_no_token_file_exits_2_after_removing_leftovers(self, start):
        calls, configure = start
        assert entry.serve_setup() == entry.EXIT_USAGE
        assert calls == ["leftovers"]
        configure.assert_not_called()

    def test_with_a_token_serves_on_the_public_loopback_port(self, start, settings_dir, ini_path):
        calls, configure = start
        _write_token(settings_dir)
        ini_path.write_text("[server]\nport = 9443\nwaitress_loopback_port_public = 18090\n"
                            "waitress_loopback_port_internal = 18088\n")
        assert entry.serve_setup() == 10
        assert calls == ["leftovers", ("serve", 18090)]
        configure.assert_called_once_with(9443, {18090, 18088})

    def test_token_is_installed(self, start, settings_dir):
        from sethlans_manager.setup_mode import auth

        _write_token(settings_dir)
        entry.serve_setup()
        assert auth.current().attempt(TOKEN, "127.0.0.1") == auth.OK

    def test_frozen_without_the_page_exits_2(self, start, mocker, settings_dir):
        calls, _configure = start
        _write_token(settings_dir)
        mocker.patch.object(entry, "is_frozen", return_value=True)
        assert entry.serve_setup() == entry.EXIT_USAGE
        assert calls == []

    def test_frozen_with_the_page_serves(self, start, mocker, settings, settings_dir):
        calls, _configure = start
        _write_token(settings_dir)
        settings.SETUP_FRONTEND_DIR.mkdir()
        (settings.SETUP_FRONTEND_DIR / "index.html").write_text("<html></html>")
        mocker.patch.object(entry, "is_frozen", return_value=True)
        assert entry.serve_setup() == 10
        assert calls[-1] == ("serve", 8090)

    @pytest.mark.parametrize("target, error", [
        ("build_logging", PermissionError("denied")),
        ("configure", ValueError("Unable to configure handler 'file'")),  # dictConfig wraps the OSError
    ])
    def test_log_that_cannot_open_exits_2(self, start, capsys, target, error):
        from sethlans_manager.setup_mode import logging_setup

        calls, _configure = start
        getattr(logging_setup, target).side_effect = error
        assert entry.serve_setup() == entry.EXIT_USAGE
        assert "Cannot open the setup log" in capsys.readouterr().err
        assert calls == []
