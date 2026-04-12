# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
DRF serializer for the ``POST /api/enroll/`` request body.

Split into its own module to avoid bloating ``manager/workers/serializers``
and to keep enrollment-specific validators in one place.  ``validate_*``
hooks are used rather than DRF field types so the error messages match
the shapes documented in the spec.

Note that ``validate_enrollment_key`` does NOT normalise the value here —
normalisation happens once in the view after a call to
``enrollment_key.normalize()`` so the "rejection is a single codepath"
rule (FR-15) holds.  The serializer only enforces the coarse length cap
to keep pathological inputs out of the normaliser.
"""

import base64
import re

from rest_framework import serializers

# RFC 1123 hostname — labels may contain ``a-zA-Z0-9-`` and must not
# begin or end with a hyphen.  Up to 253 characters total, up to 63 per
# label.
HOSTNAME_RE = re.compile(
    r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?"
    r"(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$"
)

# ``base64.urlsafe`` alphabet without padding.  22 chars decode to
# exactly 16 bytes (22 * 6 = 132 bits, fit in 128 with 4 spare bits).
NONCE_CHARSET_RE = re.compile(r"^[A-Za-z0-9_\-]{22}$")

# Upper bound on the raw ``enrollment_key`` field: the compact form is
# 16 chars and the hyphenated form is 19; 32 is loose enough to tolerate
# whitespace but tight enough to deflect pathological inputs before
# hitting ``normalize``.
_MAX_KEY_LENGTH = 32


class EnrollmentRequestSerializer(serializers.Serializer):
    """Request body for ``POST /api/enroll/``."""

    enrollment_key = serializers.CharField(max_length=_MAX_KEY_LENGTH)
    hostname = serializers.CharField(max_length=253)
    nonce = serializers.CharField(min_length=22, max_length=22)

    def validate_hostname(self, value: str) -> str:
        # max_length=253 on the CharField already enforces length;
        # this validator only needs the RFC 1123 regex check.
        if not HOSTNAME_RE.match(value):
            raise serializers.ValidationError("Invalid hostname.")
        return value

    def validate_nonce(self, value: str) -> str:
        if len(value) != 22 or not NONCE_CHARSET_RE.match(value):
            raise serializers.ValidationError(
                "Invalid nonce format.",
            )
        try:
            decoded = base64.urlsafe_b64decode(value + "==")
        except Exception as exc:
            raise serializers.ValidationError(
                "Invalid nonce encoding.",
            ) from exc
        if len(decoded) != 16:
            raise serializers.ValidationError(
                "Invalid nonce length.",
            )
        return value
