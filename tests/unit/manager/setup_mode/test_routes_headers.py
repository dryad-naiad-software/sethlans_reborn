# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Setup-mode routes, the JSON 404, security headers, Cache-Control, hosts and health."""
import re

import pytest

from sethlans_manager.setup_mode import middleware, urls, views
from tests.unit.manager.setup_mode._helpers import LAN_ADDRESS

ALLOWED_ROUTES = {"api/health/", "api/setup/auth/", "api/setup/form/", "api/setup/submit/", "setup/"}
FRAME_HEADERS = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


class TestRouteSet:

    def test_only_the_setup_routes_exist(self):
        assert {str(pattern.pattern) for pattern in urls.urlpatterns} == ALLOWED_ROUTES

    def test_error_handlers_are_setup_modes_own(self):
        assert urls.handler404 == "sethlans_manager.setup_mode.views.not_found"
        assert urls.handler500 == "sethlans_manager.setup_mode.views.server_error"

    @pytest.mark.parametrize("path", [
        "/", "/api/", "/api/health", "/api/setup/", "/api/setup/auth", "/api/setup/status/",
        "/api/setup/topology/", "/api/jobs/", "/api/enroll/", "/api/heartbeat/", "/api/docs/",
        "/api/schema/", "/api/manager-defaults/", "/admin/", "/login/", "/setup/x/", "/static/x.js",
    ])
    def test_every_other_path_is_a_json_404(self, setup_client, path):
        response = setup_client.get(path)
        assert response.status_code == 404
        assert response.json() == {"error": "not_found", "message": "Not found."}

    def test_post_to_an_unknown_path_is_404(self, setup_client):
        assert setup_client.post("/api/jobs/", {}, content_type="application/json").status_code == 404


class TestSecurityHeaders:

    @pytest.mark.parametrize("path", ["/api/health/", "/setup/", "/nope/", "/api/setup/form/"])
    def test_frame_and_sniff_headers_everywhere(self, setup_client, path):
        response = setup_client.get(path)
        for name, value in FRAME_HEADERS.items():
            assert response[name] == value

    @pytest.mark.parametrize("path", ["/api/health/", "/api/setup/form/", "/api/nope/", "/setup/"])
    def test_api_and_page_are_no_store(self, setup_client, path):
        assert setup_client.get(path)["Cache-Control"] == "no-store"

    def test_other_paths_are_not_marked_no_store(self, setup_client):
        assert "Cache-Control" not in setup_client.get("/main.js")

    def test_static_files_get_the_frame_headers(self):
        headers = {}
        middleware.add_static_headers(headers, "/x/main.js", "/main.js")
        assert headers == FRAME_HEADERS


class TestAllowedHosts:

    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "setuphost", LAN_ADDRESS, "127.0.0.1:8080"])
    def test_known_names_are_served(self, setup_client, host):
        assert setup_client.get("/api/health/", HTTP_HOST=host).status_code == 200

    @pytest.mark.parametrize("host", ["evil.example", "10.9.9.9", "setuphost.evil.example"])
    def test_other_names_get_400(self, setup_client, host):
        response = setup_client.get("/api/health/", HTTP_HOST=host)
        assert response.status_code == 400
        assert b"setup" not in response.content

    def test_forwarded_host_is_not_trusted(self, setup_client):
        response = setup_client.get("/api/health/", HTTP_HOST="evil.example", HTTP_X_FORWARDED_HOST="127.0.0.1")
        assert response.status_code == 400


class TestHealth:

    def test_body(self, setup_client):
        body = setup_client.get("/api/health/").json()
        assert set(body) == {"mode", "boot_id", "last_failure"}
        assert body["mode"] == "setup"
        assert re.fullmatch(r"[0-9a-f]{32}", body["boot_id"])
        assert body["boot_id"] == views.BOOT_ID
        assert body["last_failure"] is None

    def test_last_failure_text(self, setup_client, settings_dir):
        (settings_dir / "setup").mkdir(parents=True)
        (settings_dir / "setup" / "last_failure").write_text("  The manager could not open the database.\n")
        assert setup_client.get("/api/health/").json()["last_failure"] == "The manager could not open the database."

    def test_no_session_needed(self, setup_client):
        assert setup_client.get("/api/health/").status_code == 200

    def test_post_is_405(self, setup_client):
        response = setup_client.post("/api/health/")
        assert response.status_code == 405
        assert response["Allow"] == "GET"


class TestSetupPage:

    def test_page_from_the_built_front_end(self, setup_client, settings):
        settings.SETUP_FRONTEND_DIR.mkdir()
        (settings.SETUP_FRONTEND_DIR / "index.html").write_bytes(b"<html>setup</html>")
        response = setup_client.get("/setup/")
        assert response.status_code == 200
        assert response.content == b"<html>setup</html>"
        assert response["Content-Type"] == "text/html; charset=utf-8"

    def test_missing_build_is_503_text(self, setup_client):
        response = setup_client.get("/setup/")
        assert response.status_code == 503
        assert response["Content-Type"].startswith("text/plain")

    def test_post_is_405(self, setup_client):
        assert setup_client.post("/setup/").status_code == 405


class TestServerError:

    def test_unhandled_error_is_a_json_500(self, setup_client, mocker):
        mocker.patch.object(views.leftovers, "last_failure", side_effect=RuntimeError("boom"))
        response = setup_client.get("/api/health/")
        assert response.status_code == 500
        assert response.json()["error"] == "server_error"
        assert b"boom" not in response.content
        assert response["X-Frame-Options"] == "DENY"
