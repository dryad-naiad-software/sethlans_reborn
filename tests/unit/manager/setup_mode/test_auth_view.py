# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``POST /api/setup/auth/`` and the session check of the form and submit routes."""
import pytest

from sethlans_manager.setup_mode import auth
from tests.unit.manager.setup_mode._helpers import LAN_ADDRESS, TOKEN, open_session

AUTH = "/api/setup/auth/"
FORM = "/api/setup/form/"
TWO_HOURS = 2 * 60 * 60


def _auth(client, token=TOKEN, remote=LAN_ADDRESS):
    return client.post(AUTH, {"token": token}, content_type="application/json", REMOTE_ADDR=remote)


@pytest.fixture(autouse=True)
def _quiet_form(mocker):
    """The form route without a database read (the session check is under test)."""
    from sethlans_manager.setup_mode import views

    mocker.patch.object(views, "build_form", return_value={"ok": "form"})


class TestTokenCheck:

    def test_right_token_opens_a_session(self, setup_client):
        response = _auth(setup_client)
        assert response.status_code == 200
        assert set(response.json()) == {"session_token"}

    def test_wrong_token_is_403(self, setup_client):
        response = _auth(setup_client, token="nope")
        assert response.status_code == 403
        assert response.json()["error"] == "invalid_token"
        assert "set-cookie" not in {name.lower() for name in response.headers}

    def test_no_token_key_is_403(self, setup_client):
        response = setup_client.post(AUTH, {"other": 1}, content_type="application/json")
        assert response.status_code == 403

    def test_json_array_is_403(self, setup_client):
        response = setup_client.post(AUTH, [TOKEN], content_type="application/json")
        assert response.status_code == 403

    def test_form_encoded_body_is_400(self, setup_client):
        response = setup_client.post(AUTH, {"token": TOKEN})
        assert response.status_code == 400
        assert response.json()["error"] == "bad_request"

    def test_invalid_json_is_400(self, setup_client):
        response = setup_client.post(AUTH, b"{not json", content_type="application/json")
        assert response.status_code == 400
        assert response.json()["message"] == "The body is not valid JSON."

    def test_get_is_405_with_allow(self, setup_client):
        response = setup_client.get(AUTH)
        assert response.status_code == 405
        assert response["Allow"] == "POST"

    def test_wrong_token_logs_no_token(self, setup_client, caplog):
        caplog.set_level("INFO", logger="sethlans_manager.setup_mode.views")
        _auth(setup_client, token="guess-secret-value")
        _auth(setup_client)
        assert "guess-secret-value" not in caplog.text
        assert TOKEN not in caplog.text


class TestRateLimitThroughTheView:

    def test_eleventh_attempt_is_429_with_retry_after(self, setup_client):
        for _ in range(10):
            assert _auth(setup_client, token="nope").status_code == 403
        response = _auth(setup_client, token="nope")
        assert response.status_code == 429
        assert response.json()["error"] == "too_many_attempts"
        assert response["Retry-After"] == "60"

    def test_right_token_at_the_limit_is_429(self, setup_client):
        for _ in range(10):
            _auth(setup_client, token="nope")
        assert _auth(setup_client).status_code == 429

    def test_lan_client_at_the_limit_does_not_block_loopback(self, setup_client):
        for _ in range(10):
            _auth(setup_client, token="nope", remote=LAN_ADDRESS)
        assert _auth(setup_client, remote=LAN_ADDRESS).status_code == 429
        assert _auth(setup_client, remote="127.0.0.1").status_code == 200

    def test_limit_lifts_after_60_seconds(self, setup_client, clocks):
        for _ in range(10):
            _auth(setup_client, token="nope")
        clocks["limit"].advance(60)
        assert _auth(setup_client).status_code == 200


class TestSessionCookie:

    def test_cookie_flags(self, setup_client):
        response = _auth(setup_client)
        cookie = response.cookies[auth.SESSION_COOKIE]
        assert cookie.value == response.json()["session_token"]
        assert cookie["httponly"] is True
        assert cookie["secure"] is True
        assert cookie["samesite"] == "Strict"
        assert cookie["path"] == "/"

    def test_cookie_is_a_browser_session_cookie(self, setup_client):
        cookie = _auth(setup_client).cookies[auth.SESSION_COOKIE]
        assert cookie["max-age"] == ""
        assert cookie["expires"] == ""

    def test_set_cookie_header_text(self, setup_client):
        header = _auth(setup_client).cookies[auth.SESSION_COOKIE].output(header="").strip()
        for part in ("HttpOnly", "Secure", "SameSite=Strict", "Path=/"):
            assert part in header
        assert "Max-Age" not in header and "expires" not in header.lower()


class TestSessionOnTheForm:

    def test_cookie_and_header_open_the_form(self, setup_client, session):
        response = setup_client.get(FORM, HTTP_X_SETUP_SESSION=session)
        assert response.status_code == 200
        assert response.json() == {"ok": "form"}

    def test_no_session_is_403(self, setup_client):
        response = setup_client.get(FORM)
        assert response.status_code == 403
        assert response.json()["error"] == "not_authenticated"

    def test_cookie_without_header_is_403(self, setup_client, session):
        assert setup_client.get(FORM).status_code == 403

    def test_header_without_cookie_is_403(self, setup_client, session):
        setup_client.cookies.clear()
        assert setup_client.get(FORM, HTTP_X_SETUP_SESSION=session).status_code == 403

    def test_header_must_equal_the_cookie(self, setup_client, session):
        assert setup_client.get(FORM, HTTP_X_SETUP_SESSION=session + "x").status_code == 403

    def test_old_session_is_dropped_by_a_new_login(self, setup_client, session):
        newer = open_session(setup_client)
        assert setup_client.get(FORM, HTTP_X_SETUP_SESSION=newer).status_code == 200
        setup_client.cookies[auth.SESSION_COOKIE] = session
        assert setup_client.get(FORM, HTTP_X_SETUP_SESSION=session).status_code == 403

    def test_session_expires_after_two_idle_hours(self, setup_client, session, clocks):
        clocks["session"].advance(TWO_HOURS)
        assert setup_client.get(FORM, HTTP_X_SETUP_SESSION=session).status_code == 403

    def test_requests_keep_the_session_alive(self, setup_client, session, clocks):
        for _ in range(3):
            clocks["session"].advance(TWO_HOURS - 1)
            assert setup_client.get(FORM, HTTP_X_SETUP_SESSION=session).status_code == 200

    def test_submit_needs_the_session_too(self, setup_client, session):
        response = setup_client.post("/api/setup/submit/", {}, content_type="application/json")
        assert response.status_code == 403
        assert response.json()["error"] == "not_authenticated"
