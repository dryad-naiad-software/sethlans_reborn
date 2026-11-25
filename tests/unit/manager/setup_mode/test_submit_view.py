# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``POST /api/setup/submit/``: body cap, 409s, the state machine, error shapes and the streamed 200."""
import json
import threading

import pytest

from sethlans_manager.setup_mode import state, submit, views

SUBMIT = "/api/setup/submit/"
OK_BODY = {"ok": True, "mode": "manager", "restarting": True}
CONFLICT = {"error": "conflict", "message": "Setup is already being saved, or was saved."}


def _saved(body=None):
    return submit.Outcome(200, json.dumps(OK_BODY if body is None else body).encode("utf-8"), saved=True)


@pytest.fixture
def apply(mocker):
    return mocker.patch.object(views, "apply_answers", return_value=_saved())


def _submit(client, session, body=b"{}", **extra):
    return client.post(SUBMIT, body, content_type="application/json", HTTP_X_SETUP_SESSION=session, **extra)


class TestBodyCap:

    def test_over_64_kib_is_413(self, setup_client, session, apply):
        body = b'{"pad": "' + b"a" * 65536 + b'"}'
        response = _submit(setup_client, session, body)
        assert response.status_code == 413
        assert response.json()["error"] == "too_large"
        apply.assert_not_called()
        assert state.STATE.value == state.IDLE

    def test_exactly_64_kib_is_read(self, setup_client, session, apply):
        pad = 65536 - len(b'{"pad": ""}')
        body = b'{"pad": "' + b"a" * pad + b'"}'
        assert len(body) == 65536
        assert _submit(setup_client, session, body).status_code == 200
        assert apply.call_args.args[0] == {"pad": "a" * pad}

    def test_auth_body_over_the_cap_is_413(self, setup_client):
        body = b'{"token": "' + b"a" * 65536 + b'"}'
        response = setup_client.post("/api/setup/auth/", body, content_type="application/json")
        assert response.status_code == 413

    def test_non_json_body_is_400(self, setup_client, session, apply):
        response = setup_client.post(SUBMIT, {"a": "b"}, HTTP_X_SETUP_SESSION=session)
        assert response.status_code == 400
        apply.assert_not_called()


class TestConflicts:

    def test_second_submit_after_save_is_409(self, setup_client, session, apply):
        assert _submit(setup_client, session).status_code == 200
        response = _submit(setup_client, session)
        assert response.status_code == 409
        assert response.json() == CONFLICT
        assert apply.call_count == 1
        assert state.STATE.value == state.SAVED

    def test_submit_while_one_is_applying_is_409(self, setup_client, session, mocker):
        entered, release = threading.Event(), threading.Event()

        def slow_apply(*_args):
            entered.set()
            assert release.wait(10)
            return _saved()

        mocker.patch.object(views, "apply_answers", side_effect=slow_apply)
        results = {}
        first = threading.Thread(target=lambda: results.setdefault("first", _submit(setup_client, session)))
        first.start()
        try:
            assert entered.wait(10)
            assert state.STATE.value == state.APPLYING
            second = _submit(setup_client, session)
        finally:
            release.set()
            first.join(10)
        assert second.status_code == 409
        assert second.json() == CONFLICT
        assert results["first"].status_code == 200
        assert state.STATE.value == state.SAVED

    def test_state_already_applying_gives_409_without_reading(self, setup_client, session, apply):
        assert state.STATE.begin()
        assert _submit(setup_client, session).status_code == 409
        apply.assert_not_called()


class TestBackToIdle:

    def test_validation_errors_are_400_and_idle(self, setup_client, session, apply):
        apply.return_value = submit.invalid({"admin.username": ["The username is required."]})
        response = _submit(setup_client, session)
        assert response.status_code == 400
        assert response.json() == {"error": "invalid", "fields": {"admin.username": ["The username is required."]}}
        assert state.STATE.value == state.IDLE
        apply.return_value = _saved()
        assert _submit(setup_client, session).status_code == 200

    def test_write_failure_is_500_and_idle(self, setup_client, session, apply):
        apply.return_value = submit.settings_unwritable("Could not write the settings file x: PermissionError: no")
        response = _submit(setup_client, session)
        assert response.status_code == 500
        assert response.json() == {"error": "settings_unwritable",
                                   "message": "Could not write the settings file x: PermissionError: no"}
        assert state.STATE.value == state.IDLE

    def test_an_exception_resets_to_idle(self, setup_client, session, apply):
        apply.side_effect = RuntimeError("broken")
        response = _submit(setup_client, session)
        assert response.status_code == 500
        assert response.json()["error"] == "server_error"
        assert state.STATE.value == state.IDLE

    def test_unreadable_settings_file_is_500_and_idle(self, setup_client, session, apply, ini_path):
        ini_path.parent.mkdir(parents=True)
        ini_path.write_text("garbage without a section")
        response = _submit(setup_client, session)
        assert response.status_code == 500
        body = response.json()
        assert body["error"] == "settings_unwritable"
        assert body["message"].startswith("The settings file could not be read: ")
        apply.assert_not_called()
        assert state.STATE.value == state.IDLE

    def test_only_an_unsaved_outcome_resets(self, setup_client, session, apply):
        apply.return_value = submit.invalid({"mode": ["x"]})
        _submit(setup_client, session)
        assert state.STATE.value == state.IDLE
        apply.return_value = _saved()
        _submit(setup_client, session)
        assert state.STATE.value == state.SAVED


class TestSavedResponse:

    def test_streams_the_encoded_body_as_is(self, setup_client, session, apply):
        apply.return_value = submit.Outcome(200, b'{"ok": true, "x": 1}', saved=True)
        response = _submit(setup_client, session, HTTP_HOST="setuphost:18443")
        assert response.streaming is True
        assert b"".join(response.streaming_content) == b'{"ok": true, "x": 1}'
        assert response["Content-Type"] == "application/json"
        assert not response.has_header("Connection")
        assert not response.has_header("Content-Length")

    def test_marks_the_environ_and_passes_context(self, setup_client, session, apply):
        response = _submit(setup_client, session, body=b'{"mode": "manager"}', HTTP_HOST="setuphost:18443")
        answers, ctx, host = apply.call_args.args
        assert answers == {"mode": "manager"}
        assert (ctx.setup_port, ctx.reserved_ports) == (18443, {8090, 8088})
        assert host == "setuphost"
        assert response.wsgi_request.META[views.SAVED_ENVIRON_KEY] is True

    def test_failed_submit_does_not_mark_the_environ(self, setup_client, session, apply):
        apply.return_value = submit.invalid({"mode": ["x"]})
        response = _submit(setup_client, session)
        assert views.SAVED_ENVIRON_KEY not in response.wsgi_request.META

    @pytest.mark.parametrize("host, expected", [
        ("127.0.0.1:8080", "127.0.0.1"), ("localhost", "localhost"), ("[::1]:8080", "[::1]"),
    ])
    def test_request_host_without_port(self, rf, settings, host, expected):
        settings.ALLOWED_HOSTS = ["*"]
        assert views._request_host(rf.get("/", HTTP_HOST=host)) == expected


class TestSubmitState:

    def test_idle_applying_saved(self):
        machine = state.SubmitState()
        assert machine.value == state.IDLE and machine.saved_for() is None
        assert machine.begin() is True
        assert machine.begin() is False
        machine.mark_saved()
        assert machine.value == state.SAVED
        assert machine.saved_for() >= 0
        assert machine.begin() is False

    def test_reset_only_leaves_applying(self):
        machine = state.SubmitState()
        machine.begin()
        machine.reset()
        assert machine.value == state.IDLE
        machine.begin()
        machine.mark_saved()
        machine.reset()
        assert machine.value == state.SAVED

    def test_mark_saved_needs_applying(self):
        with pytest.raises(RuntimeError, match="mark_saved from IDLE"):
            state.SubmitState().mark_saved()

    def test_only_one_of_many_threads_begins(self):
        machine = state.SubmitState()
        barrier = threading.Barrier(8)
        wins = []

        def race():
            barrier.wait(10)
            wins.append(machine.begin())

        threads = [threading.Thread(target=race) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        assert sorted(wins) == [False] * 7 + [True]
