# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``GET /api/health/`` (``manager/workers/views/health.py``).

Validates the health endpoint returns HTTP 200 with a static JSON
response, requires no authentication, issues no database queries,
and rejects non-GET methods (FR-1, FR-2).
"""

import pytest
from django.test.utils import CaptureQueriesContext
from django.db import connection
from rest_framework.test import APIClient


@pytest.fixture
def api_client():
    return APIClient()


class TestHealthEndpointResponse:
    """Happy-path: the endpoint returns the expected static payload."""

    def test_returns_200_status(self, api_client):
        resp = api_client.get("/api/health/")
        assert resp.status_code == 200

    def test_returns_ok_body(self, api_client):
        resp = api_client.get("/api/health/")
        assert resp.json() == {"status": "ok"}

    def test_content_type_is_json(self, api_client):
        resp = api_client.get("/api/health/")
        assert resp["Content-Type"] == "application/json"


class TestHealthEndpointNoAuth:
    """The endpoint must be accessible without any authentication."""

    def test_unauthenticated_request_succeeds(self, api_client):
        # APIClient with no credentials set at all
        resp = api_client.get("/api/health/")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestHealthEndpointNoDatabaseQueries:
    """FR-2: the endpoint must issue zero database queries."""

    @pytest.mark.django_db
    def test_no_db_queries(self, api_client):
        with CaptureQueriesContext(connection) as ctx:
            resp = api_client.get("/api/health/")
        assert resp.status_code == 200
        assert len(ctx.captured_queries) == 0, (
            f"Expected 0 queries, got {len(ctx.captured_queries)}: "
            f"{[q['sql'] for q in ctx.captured_queries]}"
        )


class TestHealthEndpointMethodRestrictions:
    """Only GET is allowed; other HTTP methods must return 405."""

    def test_post_returns_405(self, api_client):
        resp = api_client.post("/api/health/", {}, format="json")
        assert resp.status_code == 405

    def test_put_returns_405(self, api_client):
        resp = api_client.put("/api/health/", {}, format="json")
        assert resp.status_code == 405

    def test_patch_returns_405(self, api_client):
        resp = api_client.patch("/api/health/", {}, format="json")
        assert resp.status_code == 405

    def test_delete_returns_405(self, api_client):
        resp = api_client.delete("/api/health/")
        assert resp.status_code == 405
