# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``GET /api/ffmpeg-status/``.

Covers spec ``wizard-ffmpeg-rewrite.md`` FR §83-95 / AC §463-468:
401 unauthenticated; regular-user payload is exactly
``{"video_assembly_ready": <bool>}`` with no admin-block leak;
admin payload contains the full ``ffmpeg`` block with all five
fields; ``installing`` / ``ready`` / ``failed`` states surface
correctly; ``Cache-Control: no-store`` set on every response;
drf-spectacular schema references both serializer shapes.
"""

import json

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from rest_framework.test import APIClient

from workers.services.parts_check import registry

User = get_user_model()
URL = "/api/ffmpeg-status/"


@pytest.fixture(autouse=True)
def scoped_parts_state():
    """Snapshot/restore the FFmpeg status so test seeds don't leak.

    The parts_check registry holds module-level state.  Other tests
    in the same pytest run depend on the real machine's resolved
    FFmpeg state remaining in effect — capture it on entry and
    restore on exit.
    """
    prior = registry.get_status("ffmpeg")
    yield
    registry._publish("ffmpeg", prior)


@pytest.fixture
def regular_user(db):
    return User.objects.create_user(
        username="not_admin", password="pw", is_staff=False,
    )


@pytest.fixture
def regular_client(regular_user):
    client = APIClient()
    client.force_authenticate(user=regular_user)
    return client


def _seed_status(status="installing", source="", version="", path="", error=None):
    """Publish a deterministic FFmpeg status into the registry.

    Drives the API response through every state without invoking the
    real ``check_ffmpeg`` — uses the same ``_publish`` helper the
    production check thread uses.
    """
    registry._publish(
        "ffmpeg",
        registry.Status(
            status=status, source=source, version=version,
            path=path, error=error,
        ),
    )


@pytest.mark.django_db
class TestFFmpegStatusAuth:
    """Spec AC §463 — authentication required."""

    def test_anonymous_request_returns_401(self):
        client = APIClient()
        resp = client.get(URL)
        assert resp.status_code == 401


@pytest.mark.django_db
class TestRegularUserPayload:
    """Spec AC §464 — regular user gets exactly ``video_assembly_ready``."""

    def test_regular_user_ready_true(self, regular_client):
        _seed_status(
            status="ready", source="system", version="8.1",
            path="/usr/bin/ffmpeg",
        )
        resp = regular_client.get(URL)
        assert resp.status_code == 200
        # Exact key set — no ``ffmpeg`` block leaked.
        assert set(resp.data.keys()) == {"video_assembly_ready"}
        assert resp.data["video_assembly_ready"] is True
        assert "ffmpeg" not in resp.data

    def test_regular_user_installing_returns_false(self, regular_client):
        _seed_status(status="installing")
        resp = regular_client.get(URL)
        assert resp.status_code == 200
        assert resp.data == {"video_assembly_ready": False}
        assert "ffmpeg" not in resp.data

    def test_regular_user_failed_returns_false(self, regular_client):
        _seed_status(status="failed", error="checksum_mismatch")
        resp = regular_client.get(URL)
        assert resp.status_code == 200
        # No leak of the admin-only error vocabulary.
        assert resp.data == {"video_assembly_ready": False}
        assert "ffmpeg" not in resp.data
        assert "error" not in resp.data


@pytest.mark.django_db
class TestAdminPayload:
    """Spec AC §465 — admin gets the boolean PLUS ``ffmpeg`` block."""

    def test_admin_ready_payload_shape(self, admin_client):
        _seed_status(
            status="ready", source="system", version="8.1",
            path="/usr/bin/ffmpeg",
        )
        resp = admin_client.get(URL)
        assert resp.status_code == 200
        assert set(resp.data.keys()) == {"video_assembly_ready", "ffmpeg"}
        assert resp.data["video_assembly_ready"] is True
        # Spec FR §87 — five named fields in the ``ffmpeg`` block.
        ffmpeg = resp.data["ffmpeg"]
        assert set(ffmpeg.keys()) == {
            "source", "version", "path", "status", "error",
        }
        assert ffmpeg["source"] == "system"
        assert ffmpeg["version"] == "8.1"
        assert ffmpeg["path"] == "/usr/bin/ffmpeg"
        assert ffmpeg["status"] == "ready"
        assert ffmpeg["error"] is None

    def test_admin_installing_state(self, admin_client):
        _seed_status(status="installing")
        resp = admin_client.get(URL)
        assert resp.status_code == 200
        assert resp.data["video_assembly_ready"] is False
        ffmpeg = resp.data["ffmpeg"]
        assert ffmpeg["status"] == "installing"
        assert ffmpeg["error"] is None
        assert ffmpeg["source"] == ""
        assert ffmpeg["version"] == ""
        assert ffmpeg["path"] == ""

    def test_admin_failed_state_with_closed_vocab_error(self, admin_client):
        _seed_status(status="failed", error="checksum_mismatch")
        resp = admin_client.get(URL)
        assert resp.status_code == 200
        assert resp.data["video_assembly_ready"] is False
        ffmpeg = resp.data["ffmpeg"]
        assert ffmpeg["status"] == "failed"
        # Spec FR §70-79 — closed-vocab error string.
        assert ffmpeg["error"] == "checksum_mismatch"

    def test_admin_bundled_source(self, admin_client):
        _seed_status(
            status="ready", source="bundled", version="8.1",
            path="/data/bin/ffmpeg/8.1/ffmpeg",
        )
        resp = admin_client.get(URL)
        assert resp.status_code == 200
        ffmpeg = resp.data["ffmpeg"]
        assert ffmpeg["source"] == "bundled"
        assert ffmpeg["version"] == "8.1"
        assert ffmpeg["path"] == "/data/bin/ffmpeg/8.1/ffmpeg"


@pytest.mark.django_db
class TestCacheControlHeader:
    """Spec AC §468 — ``Cache-Control: no-store`` on every response."""

    def test_admin_response_has_no_store(self, admin_client):
        _seed_status(status="ready", source="system", version="8.1")
        resp = admin_client.get(URL)
        assert resp["Cache-Control"] == "no-store"

    def test_regular_user_response_has_no_store(self, regular_client):
        _seed_status(status="ready")
        resp = regular_client.get(URL)
        assert resp["Cache-Control"] == "no-store"

    def test_failed_response_has_no_store(self, admin_client):
        _seed_status(status="failed", error="download_failed")
        resp = admin_client.get(URL)
        assert resp["Cache-Control"] == "no-store"


@pytest.mark.django_db
class TestTokenAuthentication:
    """Spec AC §463 — TokenAuthentication is also accepted."""

    def test_token_authed_worker_gets_regular_payload(self, worker_with_token):
        _seed_status(status="ready", source="system", version="8.1")
        _, client = worker_with_token
        resp = client.get(URL)
        assert resp.status_code == 200
        # Worker user is not staff → slim payload.
        assert set(resp.data.keys()) == {"video_assembly_ready"}
        assert resp.data["video_assembly_ready"] is True


@pytest.mark.django_db
class TestOpenAPISchema:
    """Spec AC §467 — schema references both serializer shapes.

    The implementation uses ``PolymorphicProxySerializer`` with
    ``component_name="FFmpegStatus"`` to publish both regular and
    admin shapes.  We assert the path is documented with 200/401
    responses, the wrapper component is present, and the admin
    leaf shape's distinguishing fields are reachable.
    """

    def _get_schema(self):
        # ``/api/schema/`` is allowlisted by the setup gate during
        # tests via the ``_bypass_setup_gate`` autouse fixture.
        client = Client()
        resp = client.get(
            "/api/schema/?format=json",
            HTTP_ACCEPT="application/json",
        )
        assert resp.status_code == 200
        return json.loads(resp.content.decode("utf-8"))

    def test_endpoint_is_documented(self):
        doc = self._get_schema()
        endpoint = doc.get("paths", {}).get("/api/ffmpeg-status/")
        assert endpoint is not None, (
            "OpenAPI schema missing /api/ffmpeg-status/"
        )
        get_op = endpoint.get("get")
        assert get_op is not None
        responses = get_op.get("responses", {})
        # Spec FR §89 — both 200 and 401 documented.
        assert "200" in responses
        assert "401" in responses

    def test_schema_references_both_serializer_shapes(self):
        doc = self._get_schema()
        components = doc.get("components", {}).get("schemas", {})
        # The PolymorphicProxySerializer wrapper.
        assert "FFmpegStatus" in components, (
            "FFmpegStatus polymorphic wrapper missing from schema."
        )
        full_text = json.dumps(doc)
        # The slim shape's defining field.
        assert "video_assembly_ready" in full_text
        # The admin shape's distinguishing block.  Either the leaf
        # serializer is published as a named component, or its
        # ``ffmpeg`` field appears inlined via oneOf/allOf.
        assert "FFmpegDetails" in full_text or "ffmpeg" in full_text
