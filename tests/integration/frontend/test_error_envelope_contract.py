# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Frontend-backend contract tests for the unified setup error envelope.

Verifies:

1. Every backend ``ERROR_CODES`` slug appears in the TypeScript
   ``SetupErrorCode`` union at
   ``manager/frontend/src/app/core/models/error-envelope.ts``.
2. The TS file defines the ``SetupErrorEnvelope`` interface shape
   (``error: { code, message, details }``).
3. A representative sample of real ``/api/setup/*`` error responses
   matches that shape and uses codes from the allowed union.
"""

from __future__ import annotations

import json
import re

import pytest
from rest_framework.test import APIClient

from workers.utils.errors import ERROR_CODES

from .conftest import assert_envelope_shape

pytestmark = pytest.mark.django_db


# -------------------------------------------------------------------
# Contract 1: ERROR_CODES (Python) <-> SetupErrorCode (TypeScript)
# -------------------------------------------------------------------

_TS_CODE_RE = re.compile(r"'([a-z_]+)'")


def _ts_setup_error_codes(source: str) -> set[str]:
    """Parse the TS ``SetupErrorCode`` union into a set of slug strings."""
    # Match the block between ``export type SetupErrorCode =`` and `;`
    m = re.search(
        r"export\s+type\s+SetupErrorCode\s*=\s*([^;]+);",
        source,
    )
    assert m, "Could not find SetupErrorCode union in TS source"
    return set(_TS_CODE_RE.findall(m.group(1)))


class TestErrorCodeSync:
    """Python ``ERROR_CODES`` must equal TS ``SetupErrorCode`` union."""

    def test_every_backend_code_has_ts_entry(
        self, error_envelope_ts_source,
    ):
        ts_codes = _ts_setup_error_codes(error_envelope_ts_source)
        missing = ERROR_CODES - ts_codes
        assert not missing, (
            f"Backend emits codes with no TS entry: {sorted(missing)}.  "
            f"Add them to SetupErrorCode in error-envelope.ts."
        )

    def test_every_ts_code_is_backed_by_python(
        self, error_envelope_ts_source,
    ):
        ts_codes = _ts_setup_error_codes(error_envelope_ts_source)
        extra = ts_codes - ERROR_CODES
        assert not extra, (
            f"TS SetupErrorCode has codes the backend never emits: "
            f"{sorted(extra)}.  Remove them or wire up the backend."
        )


class TestEnvelopeInterfaceShape:
    """TS ``SetupErrorEnvelope`` must declare ``error.{code,message,details}``."""

    def test_interface_declared(self, error_envelope_ts_source):
        src = error_envelope_ts_source
        assert "export interface SetupErrorEnvelope" in src
        # Triplet of fields must all be present in the interface.
        assert re.search(r"code\s*:\s*SetupErrorCode", src)
        assert re.search(r"message\s*:\s*string", src)
        assert re.search(r"details\s*:", src)


# -------------------------------------------------------------------
# Contract 2: Live API responses match the envelope shape
# -------------------------------------------------------------------


class TestLiveEnvelopeShapes:
    """Sample real responses from the backend and match envelope shape."""

    def test_invalid_bootstrap_token_envelope(
        self,
        enter_setup_mode,
        fresh_bootstrap_limiter,
        patch_setup_token,
        patch_bootstrap_data_dir,
    ):
        client = APIClient()
        resp = client.post(
            "/api/setup/bootstrap/",
            data=json.dumps({"token": "short"}),
            content_type="application/json",
        )
        assert resp.status_code == 403
        body = resp.json()
        assert_envelope_shape(body)
        assert body["error"]["code"] == "invalid_token"
        assert body["error"]["code"] in ERROR_CODES

    def test_missing_token_envelope(
        self,
        enter_setup_mode,
        fresh_bootstrap_limiter,
        patch_setup_token,
        patch_bootstrap_data_dir,
    ):
        """POST with empty body collapses to same invalid_token envelope."""
        client = APIClient()
        resp = client.post(
            "/api/setup/bootstrap/",
            data="{}",
            content_type="application/json",
        )
        assert resp.status_code == 403
        body = resp.json()
        assert_envelope_shape(body)
        assert body["error"]["code"] == "invalid_token"

    def test_setup_endpoint_post_setup_returns_envelope(
        self, exit_setup_mode,
    ):
        """POST to a setup endpoint after sentinel -> 404 setup_complete."""
        client = APIClient()
        resp = client.post(
            "/api/setup/topology/",
            data=json.dumps({"topology": "manager"}),
            content_type="application/json",
        )
        assert resp.status_code == 404
        body = resp.json()
        assert_envelope_shape(body)
        assert body["error"]["code"] == "setup_complete"
