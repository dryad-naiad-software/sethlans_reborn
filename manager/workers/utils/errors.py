# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unified setup-mode error envelope + DRF exception handler.

All setup view error responses follow::

    {"error": {"code": <slug>, "message": <str>, "details": <dict>}}

The custom DRF exception handler is scoped to ``/api/setup/*`` paths
only (FR-8a) — non-setup paths keep the standard DRF error envelope.
"""

import logging

from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_default_exception_handler

logger = logging.getLogger(__name__)

# FR-9: canonical error-code vocabulary for setup-mode responses.
ERROR_CODES = frozenset({
    "invalid_token",
    "rate_limited",
    "setup_complete",
    "setup_in_progress",
    "precondition_unmet",
    "setup_session_conflict",
    "invalid_input",
    "internal_error",
})


def setup_error(code: str, message: str, status: int, details=None) -> Response:
    """Build a Response wrapped in the unified envelope.

    Parameters
    ----------
    code : str
        One of ``ERROR_CODES``.
    message : str
        Human-readable error message (never contains secrets/tokens).
    status : int
        HTTP status code.
    details : dict | None
        Optional structured detail payload.  ``None`` collapses to ``{}``.
    """
    if code not in ERROR_CODES:
        raise ValueError(f"Unknown setup error code: {code!r}")
    return Response(
        {
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
            },
        },
        status=status,
    )


def _infer_code(exc, status_code: int) -> str:
    """Map a non-SetupPhaseError exception to an envelope code."""
    if status_code == 429:
        return "rate_limited"
    if status_code == 403:
        return "invalid_token"
    if status_code in (400, 422):
        return "invalid_input"
    if status_code == 409:
        return "precondition_unmet"
    if status_code == 404:
        return "setup_complete"
    return "internal_error"


def setup_exception_handler(exc, context):
    """DRF exception handler scoping the unified envelope to /api/setup/*.

    For non-setup paths, the stock DRF envelope is preserved so existing
    worker/admin API clients continue to parse the current error shape.
    """
    # Imported lazily to avoid circular imports at settings load time.
    from workers.services.setup_phase import SetupPhaseError

    request = context.get("request") if context else None
    response = drf_default_exception_handler(exc, context)

    path = getattr(request, "path", "") if request is not None else ""
    if not path.startswith("/api/setup/"):
        return response

    if isinstance(exc, SetupPhaseError):
        return setup_error(exc.code, exc.message, exc.status, exc.details)

    if response is None:
        # Unhandled exception — collapse to internal_error envelope.
        logger.exception("Unhandled setup exception", exc_info=exc)
        return Response(
            {
                "error": {
                    "code": "internal_error",
                    "message": "Internal server error.",
                    "details": {},
                },
            },
            status=500,
        )

    # Rewrap the existing response into the unified envelope.
    message = ""
    detail = getattr(exc, "detail", None)
    if detail is not None:
        message = str(detail)
    else:
        message = str(exc)

    response.data = {
        "error": {
            "code": _infer_code(exc, response.status_code),
            "message": message,
            "details": {},
        },
    }
    return response
