# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
HMAC-bootstrapped worker enrollment endpoint.

``POST /api/enroll/`` is the single authoritative way for a worker to
bootstrap onto the manager.  The legacy ``X-Enrollment-Key`` header path
on ``/api/heartbeat/`` has been removed.

The worker never sends the enrollment key: it sends a key proof (an
HMAC over ``nonce + "\\n" + hostname`` under ``derive_proof_subkey``),
and the answer carries the API token encrypted under
``derive_token_subkey`` (setup-rebuild FR-WK-8).

The request-processing order in ``enroll_view`` is non-negotiable — see
``development/specs/worker-enrollment.md`` FR-18 for the rationale behind
each step.  Reviewers committed to the ordering specifically to close
the "nonce burned before key validation" and "nonce consumed on
downstream failure" hazards: the proof is checked before the nonce is
recorded, so a bad proof never burns a nonce.
"""

import hmac
import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from sethlans_manager import runtime_state

from ..enrollment_key import compute_proof, load_current
from ..models import Worker
from ..nonce_store import NonceStore
from ..rate_limiter import InMemoryRateLimiter
from ..serializers_enrollment import (
    LEGACY_KEY_DETAIL,
    EnrollmentBadRequestSerializer,
    EnrollmentDetailSerializer,
    EnrollmentEnvelopeSerializer,
    EnrollmentRequestSerializer,
    carries_legacy_key,
)
from ..services.enrollment_envelope import build_envelope
from ..services.worker_tokens import issue_fresh_token
from ._helpers import client_ip, get_or_create_worker_user

logger = logging.getLogger(__name__)

# Rate limiter dedicated to the enrollment endpoint.  Only key-proof
# failures count against the limit — shape errors and replayed nonces
# do NOT increment it (FR-10, FR-13).
_rate_limiter = InMemoryRateLimiter(max_attempts=5, window_seconds=300)

# In-memory nonce store with 5-minute TTL; see ``nonce_store.py``.
_nonce_store = NonceStore(ttl_seconds=300, max_entries=10_000)


def _readiness_ok() -> bool:
    """Return ``True`` iff ``runtime_state`` has everything we need."""
    return (
        runtime_state.manager_id is not None
        and runtime_state.cert_fingerprint is not None
    )


def _check_proof(hostname, nonce, proof, ip):
    """Return (current_key, None) on success or (None, Response) on fail.

    The expected proof is computed from the manager's CURRENT key and
    compared in constant time.  A mismatch counts against the rate
    limiter and answers 403 ``Invalid enrollment key.`` (FR-14).  A
    failed key load answers 503 — that is a server-misconfiguration
    signal, not a credential issue.  The proof is never logged.
    """
    try:
        current_key = load_current()
    except Exception:
        logger.exception(
            "Enrollment rejected: ManagerSettings row missing or "
            "unreadable",
        )
        return None, Response(
            {"detail": "Enrollment service not ready."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    expected = compute_proof(current_key, nonce, hostname)
    # Both strings are lowercase hex (the serializer checked ``proof``),
    # so ``compare_digest`` never sees non-ASCII text.
    if not hmac.compare_digest(expected, proof):
        _rate_limiter.record_attempt(ip)
        logger.warning("Enrollment rejected: invalid_key from %s", ip)
        return None, Response(
            {"detail": "Invalid enrollment key."},
            status=status.HTTP_403_FORBIDDEN,
        )
    return current_key, None


def _do_atomic_db_work(user_model, hostname):
    """Create/refresh ``Worker`` + ``User`` + ``Token`` under a transaction.

    Returns the issued ``Token``, which is always a new key: any token
    the user already had is deleted in the same transaction (#221).
    Raises on any failure so the caller
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

        return issue_fresh_token(user)


def _bad_request(body):
    return Response(body, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=["Worker Enrollment"],
    request=EnrollmentRequestSerializer,
    responses={
        200: OpenApiResponse(
            response=EnrollmentEnvelopeSerializer,
            description=(
                "Signed envelope {payload, signature}.  The API token is "
                "AES-GCM encrypted under the token subkey "
                "(api_token_nonce, api_token_ct)."
            ),
        ),
        400: OpenApiResponse(
            response=EnrollmentBadRequestSerializer,
            description=(
                "Shape error (field errors), a body that still carries "
                "enrollment_key, or {\"detail\": \"Replayed nonce.\"}"
            ),
        ),
        403: OpenApiResponse(
            response=EnrollmentDetailSerializer,
            description="Key proof does not match the current key",
        ),
        429: OpenApiResponse(
            response=EnrollmentDetailSerializer,
            description="Rate-limited",
        ),
        500: OpenApiResponse(
            response=EnrollmentDetailSerializer,
            description=(
                "{\"detail\": \"Enrollment failed.\"}: the worker, user "
                "or token could not be written"
            ),
        ),
        503: OpenApiResponse(
            response=EnrollmentDetailSerializer,
            description="Enrollment service not ready",
        ),
    },
    description=(
        "HMAC-bootstrapped worker enrollment with a key proof: the "
        "worker sends {hostname, nonce, proof}, never the key.  "
        "See development/specs/worker-enrollment.md and "
        "development/specs/setup-rebuild.md FR-WK-8."
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

    # --- Step 2: shape checks ------------------------------------
    if carries_legacy_key(request.data):
        return _bad_request({"detail": LEGACY_KEY_DETAIL})
    serializer = EnrollmentRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return _bad_request(serializer.errors)
    hostname = serializer.validated_data["hostname"]
    nonce = serializer.validated_data["nonce"]
    proof = serializer.validated_data["proof"]

    # --- Step 3: runtime_state readiness check -------------------
    if not _readiness_ok():
        logger.warning(
            "Enrollment rejected: runtime_state_not_ready from %s", ip,
        )
        return Response(
            {"detail": "Enrollment service not ready."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    # --- Step 4: key proof against the CURRENT key ---------------
    current_key, err = _check_proof(hostname, nonce, proof, ip)
    if err is not None:
        return err

    # --- Step 5: nonce check-and-record --------------------------
    if not _nonce_store.check_and_record(nonce):
        return _bad_request({"detail": "Replayed nonce."})

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

    # --- Step 7: encrypt the token, build and sign the payload ---
    envelope = build_envelope(
        current_key,
        token.key,
        nonce,
        runtime_state.manager_id,
        runtime_state.cert_fingerprint,
    )

    # --- Step 8: log success (NEVER log the token, its ciphertext,
    # the proof, the nonce or the full fingerprint) ---------------
    logger.info(
        "Enrollment success hostname=%s ip=%s fingerprint_prefix=%s",
        hostname, ip, runtime_state.cert_fingerprint[:12],
    )

    # --- Step 9: return envelope ---------------------------------
    return Response(envelope)
