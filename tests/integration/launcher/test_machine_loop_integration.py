# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The launcher loop from source with its real children (setup-rebuild chunk 6b).

Each test drives the setup page through the loop's own Caddy, the way a
person does, and follows the loop through ``setup/launcher_state.json``.
A run takes about 10-40 s: setup mode, then a cold manager start
(``migrate``, ``collectstatic``) and, where the mode has one, the worker
and its own Caddy. The tests run one after another (worker mode starts a
second manager).
"""
import sqlite3

import pytest

from shared import settings_file
from tests.integration.manager.setup_mode.caddy_front import CaddyFront, caddy_binary_or_skip
from tests.integration.manager.setup_mode.setup_process import (
    ADMIN_PASSWORD, Client, child_env, free_port, manager_answers,
)

from ._loop_harness import STOP_S, unverified_context

TIMEOUT_S = 600
WORKER_PASSWORD = "Plain-Worker-Page-7e2b4c1d"
ADMIN = "farmadmin"

pytestmark = pytest.mark.timeout(TIMEOUT_S, method="thread")


def _worker_section() -> dict:
    return {"compute": "cpu", "cards": [], "combine": False, "unreal_enabled": False,
            "ui_password": WORKER_PASSWORD}


def _session(run) -> Client:
    client = Client(run.public_port, ssl_context=unverified_context())
    resp = client.auth(run.token_bytes().decode("ascii"))
    assert resp.status == 200, resp.raw
    return client


def _submit(run, answers) -> None:
    resp = _session(run).submit(answers)
    assert resp.status == 200, resp.raw


def _login(run) -> int:
    return run.https("POST", "/api/auth/login/", body={"username": ADMIN, "password": ADMIN_PASSWORD}).status


def _assert_setup_closed(run) -> None:
    folder = run.settings_dir / "setup"
    assert not (folder / "setup_token").exists()
    assert not (folder / "last_failure").exists()


def _stopped_cleanly(run) -> None:
    assert run.stop() == 0
    assert run.procs and all(proc.poll() is not None for proc in run.procs)
    assert run.state()["state"] == "STOPPING"


class TestMachineLoopIntegration:
    @pytest.mark.parametrize("mode", ["manager", "manager_worker"])
    def test_a_browser_setup_reaches_running(self, loop_run, data_root, mode):
        """FR-LN-1, FR-SEC-1, D3, D8: setup on the setup port through Caddy, then the chosen
        mode runs on the same port; the token file goes when setup finishes; Quit stops all."""
        loop_run.start()
        assert loop_run.wait_state("SETUP", 60)["port"] == loop_run.public_port
        answers = manager_answers(data_root, loop_run.public_port)
        if mode == "manager_worker":
            answers = dict(answers, mode=mode, worker=_worker_section())
        _submit(loop_run, answers)

        running = loop_run.wait_state("RUNNING")

        assert running == {"state": "RUNNING", "port": loop_run.public_port, "reason": None}
        health = loop_run.https("GET", "/api/health/")
        assert health.status == 200 and health.json()["mode"] == "normal", health.raw
        assert _login(loop_run) == 200
        if mode == "manager_worker":
            worker = run_worker_health(loop_run)
            assert worker.status == 200 and "boot_id" in worker.json(), worker.raw
        _assert_setup_closed(loop_run)
        assert settings_file.setup_finished() == mode
        _stopped_cleanly(loop_run)

    def test_worker_mode_enrolls_with_a_separate_manager(self, loop_run, manager_process, data_root, tmp_path):
        """FR-LN-1, D3, FR-WK-1: worker-only setup enrolls with another manager; the launcher
        then stops its Caddy (the setup port closes) and runs only the worker."""
        manager = _separate_manager(manager_process, tmp_path)
        front = CaddyFront(caddy_binary_or_skip(), _folder(tmp_path / "front"), manager["cert"], manager["key"],
                           manager["cert"].parent)
        try:
            front.start(manager["proc"].public_port)
            loop_run.start()
            loop_run.wait_state("SETUP", 60)
            _submit(loop_run, {"mode": "worker", "data_folder": {"path": str(data_root)},
                               "worker": _worker_section(),
                               "manager": {"url": f"https://127.0.0.1:{front.port}",
                                           "enrollment_key": _enrollment_key(manager["db"]), "name": "Farm"}})

            running = loop_run.wait_state("RUNNING")

            assert running["port"] is None
            assert loop_run.ctx.manager is None and loop_run.ctx.worker.poll() is None
            with pytest.raises(OSError):
                loop_run.https("GET", "/api/health/")
            assert run_worker_health(loop_run).status == 200
            assert _worker_count(manager["db"]) == 1
            _stopped_cleanly(loop_run)
        finally:
            front.stop()

    def test_a_failed_first_start_returns_to_setup_with_the_same_token(self, loop_run, data_root):
        """FR-LN-6, #348: the first start fails (exit 11: the database file's place is a
        folder); setup opens again with the same token, byte for byte, the open page's token
        still works, and health shows only the fixed summary."""
        (loop_run.manager_data / "db.sqlite3").mkdir(parents=True)
        loop_run.start()
        loop_run.wait_state("SETUP", 60)
        token = loop_run.token_bytes()
        _submit(loop_run, manager_answers(data_root, loop_run.public_port))

        again = loop_run.wait_state("SETUP", with_reason=True)

        assert again["reason"].startswith("Database migration failed"), again
        assert loop_run.token_bytes() == token
        assert (loop_run.settings_dir / "setup" / "last_failure").read_text(encoding="utf-8") == again["reason"]
        health = loop_run.https("GET", "/api/health/")
        assert health.json()["last_failure"] == ("The database could not be prepared. "
                                                 "Enter the setup token to see why.")
        _session(loop_run)
        assert settings_file.setup_finished() == "manager"
        _stopped_cleanly(loop_run)

    def test_reset_round_trip_keeps_the_database(self, loop_run, data_root):
        """FR-RS-1, FR-RS-2: Reset stops the manager, removes completed_at only, and opens
        setup with a fresh token; keeping the admin, the next start serves the same database."""
        loop_run.start()
        loop_run.wait_state("SETUP", 60)
        first_token = loop_run.token_bytes()
        _submit(loop_run, manager_answers(data_root, loop_run.public_port))
        loop_run.wait_state("RUNNING")
        database = loop_run.manager_data / "db.sqlite3"
        manager = loop_run.ctx.manager
        before = settings_file.read_settings()

        loop_run.request_reset()
        loop_run.wait_state("SETUP", STOP_S)

        assert manager.poll() is not None
        assert loop_run.token_bytes() != first_token
        after = settings_file.read_settings()
        assert settings_file.setup_finished() is None
        assert not after.has_option("setup", "completed_at")
        assert {s: dict(after[s]) for s in after.sections() if s != "setup"} == \
            {s: dict(before[s]) for s in before.sections() if s != "setup"}
        assert _admins(database) == [ADMIN]

        answers = dict(manager_answers(data_root, loop_run.public_port), admin={"keep_existing": True})
        _submit(loop_run, answers)
        loop_run.wait_state("RUNNING")
        assert _login(loop_run) == 200
        _stopped_cleanly(loop_run)


def run_worker_health(run):
    return run.https_unverified("GET", "/api/health/", run.worker_port)


def _folder(path):
    path.mkdir(parents=True)
    return path


def _separate_manager(manager_process, tmp_path) -> dict:
    """A finished manager-mode install of its own (FR-FS-2 admin from the environment)."""
    settings_dir = _folder(tmp_path / "other-settings")
    data = tmp_path / "other-manager-data"
    (settings_dir / "sethlans.ini").write_text(
        "[setup]\nversion = 1\nmode = manager\ncompleted_at = 2026-11-28T10:00:00+00:00\n\n"
        f"[server]\nport = {free_port('0.0.0.0')}\n\n[ffmpeg]\npath = {tmp_path / 'none' / 'ffmpeg'}\n",
        encoding="utf-8")
    _folder(tmp_path / "other")
    env = child_env(tmp_path / "other", settings_dir, {
        "SETHLANS_MANAGER_DATA_DIR": str(data), "SETHLANS_DISABLE_RELEASE_FETCH": "1",
        "SETHLANS_MANAGER_PUBLISH_BROADCASTER_PARAMS": "0", "SETHLANS_SECURITY_DEBUG": "False",
        "SETHLANS_ADMIN_USERNAME": ADMIN, "SETHLANS_ADMIN_PASSWORD": ADMIN_PASSWORD,
    })
    proc = manager_process(env, name="other-manager")
    return {"proc": proc, "db": data / "db.sqlite3", "cert": data / "tls" / "cert.pem", "key": data / "tls" / "key.pem"}


def _query(database, sql):
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        return [row[0] for row in connection.execute(sql)]


def _enrollment_key(database) -> str:
    return _query(database, "SELECT enrollment_key FROM workers_managersettings WHERE id = 1")[0]


def _worker_count(database) -> int:
    return _query(database, "SELECT COUNT(*) FROM workers_worker")[0]


def _admins(database) -> list:
    return _query(database, "SELECT username FROM auth_user WHERE is_superuser = 1")
