# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
DRF serializers for ``POST /api/enroll/``.

The request carries a key proof, never the enrollment key (setup-rebuild
FR-WK-8): ``{"hostname", "nonce", "proof"}``.  Every field is a shape
check that answers 400 on a mismatch before any key material is touched:
only a ``str`` that matches its pattern in full passes, so non-ASCII
text, numbers, surrounding whitespace and a trailing newline are all
refused (never a 500).

The response serializers below only document the signed envelope in the
OpenAPI schema; the view builds the envelope itself.
"""

import re
from collections.abc import Mapping

from django.core.validators import RegexValidator
from rest_framework import serializers

# Hostname: 1-253 characters of ASCII letters, digits, ``.``, ``_``, ``-``.
HOSTNAME_RE = re.compile(r"[A-Za-z0-9._-]{1,253}")

# Nonce: 16 random bytes as exactly 32 lowercase hex characters.
NONCE_RE = re.compile(r"[0-9a-f]{32}")

# Proof: an HMAC-SHA256 hex digest, exactly 64 lowercase hex characters.
PROOF_RE = re.compile(r"[0-9a-f]{64}")

# The field an older worker still sends.  A body that carries it is
# refused with 400 so that worker gets a clear message.
LEGACY_KEY_FIELD = "enrollment_key"
LEGACY_KEY_DETAIL = (
    "This manager no longer accepts the enrollment key in the request; "
    "the worker must send a key proof. Update the worker."
)


def carries_legacy_key(data) -> bool:
    """Return ``True`` when the request body still has ``enrollment_key``."""
    return isinstance(data, Mapping) and LEGACY_KEY_FIELD in data


class ExactStringField(serializers.CharField):
    """A string field that accepts only a ``str`` matching *pattern* in full.

    Unlike ``CharField`` it neither converts numbers to text nor trims
    whitespace: the value the proof was computed over must be the value
    the view uses.

    The check itself lives in ``to_internal_value``.  The anchored
    ``RegexValidator`` built from the same pattern only puts ``pattern``
    into the OpenAPI schema (drf-spectacular turns ``\\A``/``\\Z`` into
    ``^``/``$``); it runs after ``to_internal_value`` and so can never
    fail on its own.
    """

    def __init__(self, pattern, message, **kwargs):
        kwargs.setdefault("trim_whitespace", False)
        kwargs["validators"] = [
            *kwargs.get("validators", ()),
            RegexValidator(rf"\A(?:{pattern.pattern})\Z", message=message),
        ]
        super().__init__(**kwargs)
        self._pattern = pattern
        self._message = message

    def to_internal_value(self, data):
        if not isinstance(data, str) or not self._pattern.fullmatch(data):
            raise serializers.ValidationError(self._message)
        return data


class EnrollmentRequestSerializer(serializers.Serializer):
    """Request body for ``POST /api/enroll/``."""

    hostname = ExactStringField(
        HOSTNAME_RE, "Invalid hostname.", min_length=1, max_length=253,
        help_text="1-253 characters of A-Z, a-z, 0-9, '.', '_' and '-'.",
    )
    nonce = ExactStringField(
        NONCE_RE, "Invalid nonce format.", min_length=32, max_length=32,
        help_text="16 random bytes as 32 lowercase hex characters.",
    )
    proof = ExactStringField(
        PROOF_RE, "Invalid proof format.", min_length=64, max_length=64,
        help_text=(
            "Lowercase hex HMAC-SHA256 of nonce + '\\n' + hostname under "
            "the proof subkey of the enrollment key."
        ),
    )


class EnrollmentPayloadSerializer(serializers.Serializer):
    """The signed payload of the enroll answer (schema only)."""

    cert_fingerprint = serializers.CharField()
    manager_id = serializers.CharField()
    nonce = serializers.CharField()
    api_token_nonce = serializers.CharField(
        help_text="AES-GCM nonce: 12 random bytes as 24 lowercase hex.",
    )
    api_token_ct = serializers.CharField(
        help_text=(
            "Standard base64 (padded) AES-GCM ciphertext of the API "
            "token under the token subkey; associated data is "
            "nonce + '\\n' + manager_id."
        ),
    )


class EnrollmentEnvelopeSerializer(serializers.Serializer):
    """The enroll answer: payload plus its HMAC-SHA256 hex signature."""

    payload = EnrollmentPayloadSerializer()
    signature = serializers.CharField()


class EnrollmentDetailSerializer(serializers.Serializer):
    """A ``{"detail": ...}`` error answer (schema only)."""

    detail = serializers.CharField()


class EnrollmentBadRequestSerializer(serializers.Serializer):
    """The 400 answer (schema only).

    Either a field-error map (each failing field with its messages) or a
    ``{"detail": ...}`` answer for a legacy ``enrollment_key`` body or a
    replayed nonce.
    """

    hostname = serializers.ListField(child=serializers.CharField(), required=False)
    nonce = serializers.ListField(child=serializers.CharField(), required=False)
    proof = serializers.ListField(child=serializers.CharField(), required=False)
    non_field_errors = serializers.ListField(child=serializers.CharField(), required=False)
    detail = serializers.CharField(required=False)
