# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A real ``run_manager --setup``: the saving 200 is delivered whole, then
the process exits 10 within 5 s, directly on Waitress and behind Caddy;
two submits at the same moment give one 200 and one 409.
"""
import threading
import time

import pytest

from .caddy_front import client_context
from .setup_process import ADMIN_PASSWORD, EXIT_SAVED, Client, fields_of, free_port, manager_answers, request

EXIT_WITHIN_S = 5.0
DATA_FOLDER_SHAPE = 'The data folder must be sent as {"path": "<absolute folder>"}, as the form gives it.'


def _assert_saved_body(resp, url_port: int):
    assert resp.status == 200, resp.raw
    body = resp.json()  # the whole body arrived: it parses
    assert body["ok"] is True
    assert body["mode"] == "manager"
    assert body["restarting"] is True
    assert body["url"] == f"https://127.0.0.1:{url_port}/"
    assert body["port_changed"] is False
    assert body["first_admin"] == "farmadmin"
    return body


def _assert_exits_10(proc, after: float):
    code = proc.wait(timeout=EXIT_WITHIN_S + 1)
    elapsed = time.monotonic() - after
    assert code == EXIT_SAVED, proc.output()
    assert elapsed <= EXIT_WITHIN_S, f"exit {elapsed:.2f} s after the response"


def _assert_listener_gone(port: int):
    with pytest.raises(OSError):
        request(port, "GET", "/api/health/", timeout=2)


class TestSetupExitIntegration:
    def test_direct_saving_response_is_whole_then_exit_10(self, setup_process, tmp_path, data_root):
        proc = setup_process()
        client = Client(proc.waitress_port)
        assert client.auth(proc.token).status == 200

        resp = client.submit(manager_answers(data_root, proc.setup_port))

        _assert_saved_body(resp, proc.setup_port)
        # Waitress streams it and closes the connection after it.
        assert resp.headers.get("connection", "").lower() == "close"
        assert resp.headers.get("transfer-encoding", "").lower() == "chunked"
        assert "content-length" not in resp.headers
        _assert_exits_10(proc, resp.finished_at)
        _assert_listener_gone(proc.waitress_port)

    def test_behind_caddy_saving_response_is_whole_then_exit_10(self, setup_process, caddy_front, tmp_path, data_root):
        front = caddy_front()
        proc = setup_process(setup_port=front.port)
        front.start(proc.waitress_port)
        client = Client(front.port, ssl_context=client_context(front.cert))
        health = client.call("GET", "/api/health/")
        assert health.status == 200 and health.json()["mode"] == "setup"
        assert client.auth(proc.token).status == 200

        resp = client.submit(manager_answers(data_root, front.port))

        _assert_saved_body(resp, front.port)
        _assert_exits_10(proc, resp.finished_at)
        _assert_listener_gone(proc.waitress_port)
        # Caddy itself stays up and now answers 502 for the gone upstream.
        assert front.proc.poll() is None
        after = client.call("GET", "/api/health/")
        assert after.status == 502

    def test_two_submits_at_once_give_one_200_and_one_409(self, setup_process, tmp_path, data_root):
        proc = setup_process()
        client = Client(proc.waitress_port)
        assert client.auth(proc.token).status == 200
        answers = manager_answers(data_root, proc.setup_port)
        barrier = threading.Barrier(2)
        results, errors = [], []

        def submit():
            barrier.wait()
            try:
                results.append(client.submit(answers))
            except OSError as exc:  # the process must answer both
                errors.append(exc)

        threads = [threading.Thread(target=submit) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        assert not errors, errors
        statuses = sorted(resp.status for resp in results)
        assert statuses == [200, 409], [r.raw for r in results]
        conflict = next(resp for resp in results if resp.status == 409)
        assert conflict.json()["error"] == "conflict"
        saved = next(resp for resp in results if resp.status == 200)
        _assert_saved_body(saved, proc.setup_port)
        _assert_exits_10(proc, max(resp.finished_at for resp in results))

    def test_failed_submit_returns_to_idle_and_the_next_one_saves(self, setup_process, tmp_path, data_root):
        proc = setup_process()
        client = Client(proc.waitress_port)
        assert client.auth(proc.token).status == 200
        bad = manager_answers(data_root, proc.setup_port,
                              admin={"username": "farmadmin", "password": "short"})

        refused = client.submit(bad)

        assert refused.status == 400
        assert "admin.password" in fields_of(refused)
        assert proc.proc.poll() is None  # still serving
        assert not proc.ini_path.exists()  # nothing written after a refusal
        resp = client.submit(manager_answers(data_root, proc.setup_port))
        _assert_saved_body(resp, proc.setup_port)
        _assert_exits_10(proc, resp.finished_at)

    def test_form_sent_back_filled_in_saves_then_exit_10(self, setup_process, tmp_path, data_root):
        proc = setup_process()
        client = Client(proc.waitress_port)
        assert client.auth(proc.token).status == 200
        form = client.call("GET", "/api/setup/form/").json()
        # Only what a person fills in changes; every read-only key goes back as the form gave it.
        form["mode"] = "manager"
        form["server"].update(host="127.0.0.1", port=proc.setup_port)
        form["admin"].update(username="farmadmin", email="admin@example.com", password=ADMIN_PASSWORD)
        form["data_folder"]["path"] = str(data_root)
        assert set(form["data_folder"]) >= {"path", "free_bytes", "low_space"}

        resp = client.submit(form)

        _assert_saved_body(resp, proc.setup_port)
        _assert_exits_10(proc, resp.finished_at)
        assert (data_root / ".sethlans-data").is_file()

    def test_data_folder_as_plain_text_is_refused(self, setup_process, tmp_path, data_root):
        proc = setup_process()
        client = Client(proc.waitress_port)
        assert client.auth(proc.token).status == 200
        plain = manager_answers(data_root, proc.setup_port)
        plain["data_folder"] = str(data_root)
        relative = manager_answers(data_root, proc.setup_port)
        relative["data_folder"] = {"path": "relative/data"}

        refused_plain = client.submit(plain)
        refused_relative = client.submit(relative)

        assert refused_plain.status == 400, refused_plain.raw
        assert fields_of(refused_plain) == {"data_folder": [DATA_FOLDER_SHAPE]}
        assert refused_relative.status == 400, refused_relative.raw
        assert list(fields_of(refused_relative)) == ["data_folder.path"]
        assert not (data_root).exists()
        assert proc.proc.poll() is None

    def test_port_changed_is_reported_for_a_new_port(self, setup_process, tmp_path, data_root):
        proc = setup_process()
        client = Client(proc.waitress_port)
        assert client.auth(proc.token).status == 200
        new_port = free_port()

        resp = client.submit(manager_answers(data_root, new_port))

        assert resp.status == 200, resp.raw
        body = resp.json()
        assert body["port_changed"] is True
        assert body["url"] == f"https://127.0.0.1:{new_port}/"
        _assert_exits_10(proc, resp.finished_at)
