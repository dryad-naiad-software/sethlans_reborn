# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup-mode bootstrap endpoint (``POST /api/setup/bootstrap/``).

Swaps the 256-bit setup token in ``manager.ini [setup] token`` for a
Django session cookie carrying ``setup_phase=True``.  This is the ONLY
anonymous, state-changing setup endpoint (FR-1b); every other
``/api/setup/*`` call is session-authenticated.

The endpoint is ``@csrf_exempt`` by design — the 256-bit token itself
is the anti-CSRF proof (FR-1 / S2).
"""

from __future__ import annotations

import hmac
import logging
import uuid
from pathlib import Path

from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from shared.frozen_paths import get_data_dir, is_frozen
from workers.authentication import SetupPhaseAuthentication
from workers.rate_limiter import InMemoryRateLimiter
from workers.services.ini_atomic import bind_setup_session_id
from workers.services.setup_phase import SetupPhaseError
from workers.services.setup_session import enforce_setup_session_binding
from workers.services.setup_token import read_setup_token
from workers.utils.errors import setup_error
from workers.views._helpers import client_ip as _get_client_ip

logger = logging.getLogger(__name__)

# Module-level limiter: 10 attempts / IP / 5 minutes (FR-2).
_bootstrap_rate_limiter = InMemoryRateLimiter(
    max_attempts=10, window_seconds=300,
)

# Minimum acceptable token length in bytes (FR-2a).
_MIN_TOKEN_BYTES = 32


def _data_dir() -> Path:
    if is_frozen():
        return get_data_dir("manager")
    return settings.BASE_DIR


@extend_schema(tags=["Setup"])
@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def setup_bootstrap_view(request):
    """Swap the setup token for a setup-phase session cookie.

    - Rate-limited 10/IP/5min → 429 ``rate_limited``.
    - Missing / empty / <32-byte / mismatched token → 403 ``invalid_token``.
    - On success → 204, ``request.session.cycle_key()`` then
      ``session["setup_phase"] = True`` and ``session["setup_session_id"]``
      set to a fresh UUID which is also bound atomically to
      ``manager.ini [setup] session_id`` if unset (FR-4a).
    """
    ip = _get_client_ip(request)
    if _bootstrap_rate_limiter.is_rate_limited(ip):
        return setup_error(
            "rate_limited", "Too many attempts.", 429, details={},
        )
    # Record the attempt up front so even successful bursts are counted
    # — this is cheap and prevents token-guessing scripts from exploiting
    # an unbounded success path.
    _bootstrap_rate_limiter.record_attempt(ip)

    provided_raw = request.data.get("token") if hasattr(request, "data") else None
    provided = provided_raw if isinstance(provided_raw, str) else ""
    expected = read_setup_token() or ""

    # Constant-time collapse: all failure modes share the same code.  We
    # still call compare_digest so timing doesn't leak whether expected
    # was empty.  Pad to equalise lengths first.
    max_len = max(len(provided), len(expected), _MIN_TOKEN_BYTES)
    a = provided.ljust(max_len, "\x00")
    b = expected.ljust(max_len, "\x00")
    ok = hmac.compare_digest(a, b)
    if (
        len(provided) < _MIN_TOKEN_BYTES
        or not expected
        or not ok
    ):
        return setup_error(
            "invalid_token", "Invalid setup token.", 403, details={},
        )

    # Prevent session fixation (FR-1a / S1).
    request.session.cycle_key()
    request.session["setup_phase"] = True
    setup_session_id = uuid.uuid4().hex
    request.session["setup_session_id"] = setup_session_id
    request.session.save()

    # Bind session_id in manager.ini (single-writer guard, FR-4a / C3).
    # We persist the generated setup_session_id (not the Django session
    # key) so Django's session store churn (cycle_key during login etc.)
    # doesn't invalidate the binding.
    bind_setup_session_id(_data_dir(), setup_session_id)

    return Response(status=204)


# Belt-and-suspenders: the bootstrap view declares no authentication
# classes and so never routes through ``SetupPhaseAuthentication``.  We
# still advertise the dedicated setup-phase CSRF-exempt marker so that
# any future refactor that wires bootstrap through the shared auth class
# still honours the 256-bit-token-as-anti-CSRF-proof contract.
from workers.authentication import SETUP_CSRF_EXEMPT_ATTR  # noqa: E402
setattr(setup_bootstrap_view, SETUP_CSRF_EXEMPT_ATTR, True)


# --- Session probe (FR-BE-1 / setup-token-entry spec) -----------------
# Reusable inline serializer describing the unified setup-error envelope
# ({"error": {"code": str, "message": str, "details": dict}}).  Used by
# OpenAPI to advertise the 403 body of the session probe below.
_SetupErrorSerializer = inline_serializer(
    name="SetupErrorEnvelope",
    fields={
        "error": inline_serializer(
            name="SetupErrorEnvelopeBody",
            fields={
                "code": serializers.CharField(),
                "message": serializers.CharField(),
                "details": serializers.DictField(),
            },
        ),
    },
)


@extend_schema(
    tags=["Setup"],
    summary="Probe whether a bound setup-phase session exists.",
    description=(
        "Used exclusively by the Angular `setupSessionGuard` on the "
        "`/setup/wizard` route.  Returns 204 when the caller already "
        "holds a bound setup-phase session (set by `POST /api/setup/"
        "bootstrap/`); returns the unified `setup_in_progress` 403 "
        "envelope otherwise."
    ),
    responses={
        204: None,
        403: OpenApiResponse(
            response=_SetupErrorSerializer,
            description="No bound setup session.",
        ),
    },
)
@api_view(["GET"])
@authentication_classes([SetupPhaseAuthentication])
@permission_classes([AllowAny])
def setup_session_view(request):
    """Return 204 iff this request carries a bound setup-phase session.

    We do our own check (rather than relying on ``IsSetupPhaseUser``)
    and raise ``SetupPhaseError("setup_in_progress", ...)`` explicitly
    on failure.  The default DRF 403 fall-through in
    :func:`workers.utils.errors._infer_code` maps bare 403 responses to
    ``invalid_token`` — which is the correct code for the bootstrap
    view, but wrong for this probe.  Raising ``SetupPhaseError``
    short-circuits the ``isinstance`` branch of
    :func:`setup_exception_handler` before ``_infer_code`` runs, so we
    emit the correct ``setup_in_progress`` envelope without touching
    the shared helper.
    """
    session = getattr(request, "session", None)
    if session is None or session.get("setup_phase") is not True:
        raise SetupPhaseError(
            code="setup_in_progress",
            message="Setup session required.",
            status=403,
            details={},
        )
    # Validate the single-writer session_id binding (FR-4a / C3).  If
    # another tab owns the binding, ``enforce_setup_session_binding``
    # raises ``SetupPhaseError(setup_session_conflict, 409)`` — caught
    # by the unified handler and rendered as the 409 envelope.
    enforce_setup_session_binding(request)
    return Response(status=204)
