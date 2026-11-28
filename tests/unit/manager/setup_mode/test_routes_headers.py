# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Setup-mode routes, the JSON 404, security headers, Cache-Control, hosts and health."""
import re

import pytest

from sethlans_manager.setup_mode import leftovers, middleware, urls, views
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


BAD_HOST = {"error": "bad_host", "message": "This host name is not one of this machine's names."}


class TestAllowedHosts:

    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "setuphost", LAN_ADDRESS, "127.0.0.1:8080"])
    def test_known_names_are_served(self, setup_client, host):
        assert setup_client.get("/api/health/", HTTP_HOST=host).status_code == 200

    @pytest.mark.parametrize("host", ["evil.example", "10.9.9.9", "setuphost.evil.example"])
    def test_other_names_get_a_json_400(self, setup_client, host):
        response = setup_client.get("/api/health/", HTTP_HOST=host)
        assert response.status_code == 400
        assert response.json() == BAD_HOST

    @pytest.mark.parametrize("path", ["/api/health/", "/setup/", "/nope/"])
    def test_bad_host_response_has_every_security_header(self, setup_client, path):
        response = setup_client.get(path, HTTP_HOST="evil.example")
        for name, value in FRAME_HEADERS.items():
            assert response[name] == value
        if path != "/nope/":
            assert response["Cache-Control"] == "no-store"

    def test_bad_host_is_logged_truncated_to_100_characters(self, setup_client, caplog):
        caplog.set_level("WARNING", logger=middleware.__name__)
        setup_client.get("/api/health/", HTTP_HOST="a" * 150 + ".example")
        records = [r for r in caplog.records if r.name == middleware.__name__]
        assert [r.levelname for r in records] == ["WARNING"]
        assert repr("a" * 100) in records[0].getMessage()
        assert "a" * 101 not in records[0].getMessage()

    def test_bad_host_never_reaches_a_view(self, setup_client, mocker):
        health = mocker.patch.object(leftovers, "last_failure")
        setup_client.get("/api/health/", HTTP_HOST="evil.example")
        health.assert_not_called()

    def test_forwarded_host_is_not_trusted(self, setup_client):
        response = setup_client.get("/api/health/", HTTP_HOST="evil.example", HTTP_X_FORWARDED_HOST="127.0.0.1")
        assert response.status_code == 400


class TestErrorHandlers:

    def test_handlers_are_setup_modes_own(self):
        assert urls.handler400 == "sethlans_manager.setup_mode.views.bad_request"
        assert urls.handler403 == "sethlans_manager.setup_mode.views.permission_denied"

    def test_suspicious_operation_is_a_json_400(self, setup_client, mocker):
        from django.core.exceptions import SuspiciousOperation

        mocker.patch.object(leftovers, "last_failure", side_effect=SuspiciousOperation("odd"))
        response = setup_client.get("/api/health/")
        assert response.status_code == 400
        assert response.json() == {"error": "bad_request", "message": "The request was refused."}
        assert response["X-Frame-Options"] == "DENY"

    def test_permission_denied_is_a_json_403(self, setup_client, mocker):
        from django.core.exceptions import PermissionDenied

        mocker.patch.object(leftovers, "last_failure", side_effect=PermissionDenied("no"))
        response = setup_client.get("/api/health/")
        assert response.status_code == 403
        assert response.json() == {"error": "forbidden", "message": "Not allowed."}


class TestHealth:

    def test_body(self, setup_client):
        from sethlans_manager import __version__

        body = setup_client.get("/api/health/").json()
        assert set(body) == {"mode", "boot_id", "version", "last_failure"}
        assert (body["mode"], body["version"], body["last_failure"]) == ("setup", __version__, None)
        assert re.fullmatch(r"[0-9a-f]{32}", body["boot_id"])
        assert body["boot_id"] == views.BOOT_ID

    def test_head_is_answered(self, setup_client):
        response = setup_client.head("/api/health/")
        assert response.status_code == 200
        assert response.content == b""
        assert response["Cache-Control"] == "no-store"

    def test_last_failure_is_a_fixed_summary(self, setup_client, settings_dir):
        """#348: health needs no session, so it shows a fixed phrase, not the reason's text."""
        (settings_dir / "setup").mkdir(parents=True)
        (settings_dir / "setup" / "last_failure").write_text("  The manager could not open the database.\n")
        assert setup_client.get("/api/health/").json()["last_failure"] == (
            "The manager did not start. Enter the setup token to see why.")

    def test_no_session_needed(self, setup_client):
        assert setup_client.get("/api/health/").status_code == 200

    @pytest.mark.parametrize("method", ["post", "put", "delete", "patch"])
    def test_other_methods_are_405(self, setup_client, method):
        response = getattr(setup_client, method)("/api/health/")
        assert response.status_code == 405
        assert response["Allow"] == "GET, HEAD"
        assert response.json()["message"] == "Use GET or HEAD."


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
        mocker.patch.object(leftovers, "last_failure", side_effect=RuntimeError("boom"))
        response = setup_client.get("/api/health/")
        assert response.status_code == 500
        assert response.json()["error"] == "server_error"
        assert b"boom" not in response.content
        assert response["X-Frame-Options"] == "DENY"
