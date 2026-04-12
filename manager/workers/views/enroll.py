# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
HMAC-bootstrapped worker enrollment endpoint.

``POST /api/enroll/`` is the single authoritative way for a worker to
bootstrap onto the manager.  The legacy ``X-Enrollment-Key`` header path
on ``/api/heartbeat/`` has been removed.

The request-processing order in ``enroll_view`` is non-negotiable — see
``development/specs/worker-enrollment.md`` FR-18 for the rationale behind
each step.  Reviewers committed to the ordering specifically to close
the "nonce burned before key validation" and "nonce consumed on
downstream failure" hazards.
"""

import hashlib
import hmac
import json
import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from sethlans_manager import runtime_state

from ..enrollment_key import (
    derive_signing_subkey,
    load_current,
    normalize as normalize_key,
)
from ..models import Worker
from ..nonce_store import NonceStore
from ..rate_limiter import InMemoryRateLimiter
from ..serializers_enrollment import EnrollmentRequestSerializer
from ._helpers import client_ip, get_or_create_worker_user

logger = logging.getLogger(__name__)

# Rate limiter dedicated to the enrollment endpoint.  Only key-validation
# failures count against the limit — serializer errors and replayed
# nonces do NOT increment it (FR-10, FR-13).
_rate_limiter = InMemoryRateLimiter(max_attempts=5, window_seconds=300)

# In-memory nonce store with 5-minute TTL; see ``nonce_store.py``.
_nonce_store = NonceStore(ttl_seconds=300, max_entries=10_000)


def _sign_payload(payload: dict, signing_key: bytes) -> str:
    """Return the hex HMAC-SHA256 over the canonical payload JSON.

    ``ensure_ascii=True`` is explicit so the canonical form is byte-
    identical across Python minor versions (the default is ``True``,
    but pinning it in code makes the invariant testable).
    """
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hmac.new(signing_key, canonical, hashlib.sha256).hexdigest()


def _readiness_ok() -> bool:
    """Return ``True`` iff ``runtime_state`` has everything we need."""
    return (
        runtime_state.manager_id is not None
        and runtime_state.cert_fingerprint is not None
    )


def _validate_key(raw_key, ip):
    """Return (normalized_key, None) on success or (None, Response) on fail.

    Increments the rate-limiter on invalid key (either malformed or
    mismatched) per FR-14.  Returns a 503 Response if the DB-backed
    key load fails — that's a server-misconfiguration signal, not a
    credential issue.
    """
    try:
        normalized_key = normalize_key(raw_key)
    except ValueError:
        _rate_limiter.record_attempt(ip)
        logger.warning("Enrollment rejected: invalid_key from %s", ip)
        return None, Response(
            {"detail": "Invalid enrollment key."},
            status=status.HTTP_403_FORBIDDEN,
        )
    try:
        expected_key = load_current()
    except Exception:
        logger.exception(
            "Enrollment rejected: ManagerSettings row missing or "
            "unreadable",
        )
        return None, Response(
            {"detail": "Enrollment service not ready."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if not hmac.compare_digest(normalized_key, expected_key):
        _rate_limiter.record_attempt(ip)
        logger.warning("Enrollment rejected: invalid_key from %s", ip)
        return None, Response(
            {"detail": "Invalid enrollment key."},
            status=status.HTTP_403_FORBIDDEN,
        )
    return normalized_key, None


def _do_atomic_db_work(user_model, hostname):
    """Create/refresh ``Worker`` + ``User`` + ``Token`` under a transaction.

    Returns the issued ``Token``.  Raises on any failure so the caller
    can log and return 500.  ``Worker.ip_address``, ``Worker.os`` and
    ``Worker.available_tools`` are deliberately left NULL/empty — the
    first post-enrollment heartbeat fills them in.
    """
    with transaction.atomic():
        username = f"worker_{hostname}"
        user = get_or_create_worker_user(user_model, username, hostname)
        if user is None:
            raise RuntimeError(
                "Could not create worker user after retries."
            )

        worker_qs = Worker.objects.select_for_update().filter(
            hostname=hostname,
        )
        try:
            worker = worker_qs.get()
            worker.user = user
            worker.is_active = True
            worker.last_seen = timezone.now()
            worker.save(
                update_fields=["user", "is_active", "last_seen"],
            )
        except Worker.DoesNotExist:
            Worker.objects.create(
                hostname=hostname,
                user=user,
                is_active=True,
                last_seen=timezone.now(),
            )

        token, _ = Token.objects.get_or_create(user=user)
        return token


@extend_schema(
    tags=["Worker Enrollment"],
    request=EnrollmentRequestSerializer,
    responses={
        200: OpenApiResponse(
            description="Enrollment envelope {payload, signature}",
        ),
        400: OpenApiResponse(
            description="Validation error or replayed nonce",
        ),
        403: OpenApiResponse(description="Invalid enrollment key"),
        429: OpenApiResponse(description="Rate-limited"),
        503: OpenApiResponse(description="Enrollment service not ready"),
    },
    description=(
        "HMAC-bootstrapped worker enrollment.  "
        "See development/specs/worker-enrollment.md."
    ),
)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def enroll_view(request):
    """Handle ``POST /api/enroll/`` with the FR-18 ordering."""
    # --- Step 1: rate-limit --------------------------------------
    ip = client_ip(request)
    if _rate_limiter.is_rate_limited(ip):
        return Response(
            {"detail": "Too many enrollment attempts. Try again later."},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    # --- Step 2: serializer validation ---------------------------
    serializer = EnrollmentRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST,
        )
    raw_key = serializer.validated_data["enrollment_key"]
    hostname = serializer.validated_data["hostname"]
    nonce = serializer.validated_data["nonce"]

    # --- Step 3: runtime_state readiness check -------------------
    if not _readiness_ok():
        logger.warning(
            "Enrollment rejected: runtime_state_not_ready from %s", ip,
        )
        return Response(
            {"detail": "Enrollment service not ready."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    # --- Step 4: enrollment key validation -----------------------
    normalized_key, err = _validate_key(raw_key, ip)
    if err is not None:
        return err

    # --- Step 5: nonce check-and-record --------------------------
    if not _nonce_store.check_and_record(nonce):
        return Response(
            {"detail": "Replayed nonce."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # --- Step 6: atomic DB work ----------------------------------
    user_model = get_user_model()
    try:
        token = _do_atomic_db_work(user_model, hostname)
    except Exception:
        logger.exception(
            "Enrollment DB write failed for hostname=%s", hostname,
        )
        return Response(
            {"detail": "Enrollment failed."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # --- Step 7: compute signing subkey --------------------------
    signing_key = derive_signing_subkey(normalized_key)

    # --- Step 8: build and sign the response payload -------------
    payload = {
        "api_token": token.key,
        "cert_fingerprint": runtime_state.cert_fingerprint,
        "manager_id": runtime_state.manager_id,
        "nonce": nonce,
    }
    signature = _sign_payload(payload, signing_key)

    # --- Step 9: log success (NEVER log full fingerprint/token/nonce)
    logger.info(
        "Enrollment success hostname=%s ip=%s fingerprint_prefix=%s",
        hostname, ip, payload["cert_fingerprint"][:12],
    )

    # --- Step 10: return envelope --------------------------------
    return Response({"payload": payload, "signature": signature})
