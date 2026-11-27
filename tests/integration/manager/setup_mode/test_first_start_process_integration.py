# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A real setup submit, then a real normal ``run_manager`` start, with
``SETHLANS_MANAGER_DATA_DIR`` set for both, as the dev launcher and the
install smoke tool set it.

The first start runs in the serving manager's own process, so the admin
from setup is made in the database that manager serves and logs in
through it. Neither the admin password nor its hash reaches a log or the
console. The in-process cases of the first-start step are in
``tests/integration/manager/test_first_start_integration.py``.
"""
from shared import settings_file

from .setup_process import ADMIN_PASSWORD, EXIT_SAVED, Client, child_env, manager_answers, request

USERNAME = "farmadmin"


def _first_admin(ini_path):
    parser = settings_file.read_settings(ini_path)
    if not parser.has_section(settings_file.SECTION_FIRST_ADMIN):
        return None
    return dict(parser.items(settings_file.SECTION_FIRST_ADMIN))


def _written_texts(setup, manager, manager_data):
    """Every log file and console output of both children."""
    texts = {"setup console": setup.output(), "manager console": manager.output()}
    for folder in (setup.settings_dir / "logs", manager_data / "logs"):
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                texts[str(path)] = path.read_text(encoding="utf-8", errors="replace")
    return texts


class TestFirstStartProcessIntegration:
    def test_setup_admin_logs_in_on_the_database_the_manager_serves(
            self, setup_process, manager_process, tmp_path, data_root):
        """#307, FR-FS-1, FR-FS-4, AC-5: admin from setup logs in on the served database; no secret in logs.

        Over 2 s (about 4 s on a desktop): two real children, setup mode and then a
        cold normal start with migrate and collectstatic. It is the one subprocess
        case of the first-start step.
        """
        manager_data = data_root.parent / "manager-override"  # not <data_root>/manager
        manager_data.mkdir()
        shared_env = {"SETHLANS_MANAGER_DATA_DIR": str(manager_data), "SETHLANS_DISABLE_RELEASE_FETCH": "1"}
        setup = setup_process(extra_env=shared_env)
        client = Client(setup.waitress_port)
        assert client.auth(setup.token).status == 200
        saved = client.submit(manager_answers(data_root, setup.setup_port))
        assert saved.status == 200, saved.raw
        assert setup.wait(timeout=10) == EXIT_SAVED, setup.output()
        password_hash = _first_admin(setup.ini_path)["password_hash"]
        # No FFmpeg download in a test: a missing override fails the parts check at once.
        settings_file.set_values("ffmpeg", {"path": str(tmp_path / "no-ffmpeg" / "ffmpeg")}, setup.ini_path)

        env = child_env(setup.tmp_path, setup.settings_dir, {**shared_env, "SETHLANS_SECURITY_DEBUG": "False"})
        manager = manager_process(env)
        login = request(manager.public_port, "POST", "/api/auth/login/",
                        {"username": USERNAME, "password": ADMIN_PASSWORD})

        assert login.status == 200, login.raw
        assert (login.json()["username"], login.json()["is_staff"]) == (USERNAME, True)
        assert _first_admin(setup.ini_path) is None
        assert settings_file.setup_finished(setup.ini_path) == "manager"
        assert (manager_data / "db.sqlite3").is_file()
        assert not (data_root / "manager" / "db.sqlite3").exists()

        manager.stop()
        digest = password_hash.rsplit("$", 1)[1]
        for name, text in _written_texts(setup, manager, manager_data).items():
            assert ADMIN_PASSWORD not in text, f"plain admin password in {name}"
            assert digest not in text, f"admin password hash in {name}"
