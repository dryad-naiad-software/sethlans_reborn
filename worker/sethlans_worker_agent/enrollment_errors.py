# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Enrollment client errors.

Callers catch them through :mod:`sethlans_worker_agent.enrollment_client`,
which re-exports every class here. No message carries the enrollment key,
the proof, the API token or its ciphertext.
"""


class EnrollmentError(Exception):
    """Base class for all enrollment client failures."""


class InvalidKeyError(EnrollmentError):
    """Raised on HTTP 403, on malformed input keys, or on user error."""


class InvalidHostnameError(EnrollmentError):
    """Raised before any network call when the hostname cannot be sent."""


class HmacMismatchError(EnrollmentError):
    """Raised when the response signature does not verify."""


class NonceMismatchError(EnrollmentError):
    """Raised when the response nonce does not match the request nonce."""


class MalformedResponseError(EnrollmentError):
    """Raised on unexpected response shape or missing/extra fields."""


class TokenDecryptError(EnrollmentError):
    """Raised when the encrypted API token in the answer cannot be decrypted."""


class CertificateMismatchError(EnrollmentError):
    """Raised when the certificate seen on the enroll connection is not the signed one."""


class RateLimitedError(EnrollmentError):
    """Raised on HTTP 429."""


class ServiceUnavailableError(EnrollmentError):
    """Raised on HTTP 503 (manager enrollment service not ready)."""
