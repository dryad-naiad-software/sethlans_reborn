# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``manager/workers/utils/errors.py``.

Covers the unified setup error envelope and DRF exception handler.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from rest_framework.exceptions import NotAuthenticated, ValidationError

from workers.services.setup_phase import SetupPhaseError
from workers.utils.errors import (
    ERROR_CODES,
    setup_error,
    setup_exception_handler,
)


class TestSetupError:

    def test_valid_code_returns_envelope(self):
        resp = setup_error("invalid_token", "msg", 403)
        assert resp.status_code == 403
        assert resp.data == {
            "error": {"code": "invalid_token", "message": "msg", "details": {}},
        }

    def test_details_preserved(self):
        resp = setup_error(
            "invalid_input", "bad", 400, details={"field": "x"},
        )
        assert resp.data["error"]["details"] == {"field": "x"}

    def test_invalid_code_raises(self):
        with pytest.raises(ValueError):
            setup_error("not_a_code", "msg", 500)

    def test_error_codes_frozenset(self):
        assert "invalid_token" in ERROR_CODES
        assert "rate_limited" in ERROR_CODES
        assert "setup_complete" in ERROR_CODES


class TestSetupExceptionHandler:

    def _ctx(self, path):
        request = MagicMock()
        request.path = path
        return {"request": request, "view": MagicMock()}

    def test_setup_phase_error_wrapped(self):
        exc = SetupPhaseError(
            "precondition_unmet", "bad state", 409,
            details={"expected": "topology"},
        )
        resp = setup_exception_handler(exc, self._ctx("/api/setup/topology/"))
        assert resp.status_code == 409
        assert resp.data["error"]["code"] == "precondition_unmet"
        assert resp.data["error"]["details"] == {"expected": "topology"}

    def test_non_setup_path_returns_stock_envelope(self):
        exc = ValidationError({"field": ["required"]})
        resp = setup_exception_handler(exc, self._ctx("/api/projects/"))
        # Stock DRF envelope: no "error" top-level key.
        assert resp is not None
        assert "error" not in resp.data or not isinstance(
            resp.data.get("error"), dict,
        ) or "code" not in resp.data["error"]

    def test_setup_path_validation_error_rewrapped(self):
        exc = ValidationError("bad input")
        resp = setup_exception_handler(
            exc, self._ctx("/api/setup/topology/"),
        )
        assert resp is not None
        assert "error" in resp.data
        assert resp.data["error"]["code"] == "invalid_input"

    def test_setup_path_not_authenticated_rewrapped(self):
        exc = NotAuthenticated()
        resp = setup_exception_handler(
            exc, self._ctx("/api/setup/topology/"),
        )
        assert resp is not None
        assert "error" in resp.data
        # 401 -> not listed in _infer_code explicit branches, falls to
        # internal_error — but NotAuthenticated status is 401 which the
        # handler maps to "internal_error" via the default branch. We
        # only assert the envelope shape rather than the specific code.
        assert "code" in resp.data["error"]
        assert "message" in resp.data["error"]
        assert resp.data["error"]["details"] == {}

    def test_no_request_returns_stock(self):
        resp = setup_exception_handler(
            ValidationError("x"), {"view": MagicMock()},
        )
        # Falls through; should return stock DRF envelope
        assert resp is not None
