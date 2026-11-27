# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``EnrollmentRequestSerializer`` and ``carries_legacy_key``.

The request body is ``{"hostname", "nonce", "proof"}`` (setup-rebuild
FR-WK-8).  Every field is a shape check that refuses anything but a ``str``
matching its pattern in full, so the view never compares a non-ASCII or
re-shaped value.  No view, no DB, no network.
"""

import pytest

from workers.serializers_enrollment import (
    EnrollmentRequestSerializer,
    carries_legacy_key,
)

VALID_HOSTNAME = "worker-01.example.com"
VALID_NONCE = "00112233445566778899aabbccddeeff"
VALID_PROOF = "0123456789abcdef" * 4


def _body(**overrides):
    base = {"hostname": VALID_HOSTNAME, "nonce": VALID_NONCE, "proof": VALID_PROOF}
    base.update(overrides)
    return base


def test_valid_body_passes_unchanged():
    """FR-WK-8: the values the proof covers reach the view exactly as sent."""
    s = EnrollmentRequestSerializer(data=_body())
    assert s.is_valid(), s.errors
    assert s.validated_data == _body()


@pytest.mark.parametrize("missing", ["hostname", "nonce", "proof"])
def test_missing_field_is_required(missing):
    """FR-WK-8: hostname, nonce and proof are all required."""
    body = _body()
    del body[missing]
    s = EnrollmentRequestSerializer(data=body)
    assert not s.is_valid()
    assert s.errors[missing][0].code == "required"


@pytest.mark.parametrize("hostname", [".", "-leading-hyphen", "trailing-hyphen-", "under_score.local", "a" * 253])
def test_hostname_pattern_edges_accepted(hostname):
    """FR-WK-8: any 1-253 characters of letters, digits, '.', '_' and '-' (no RFC 1123 label rules)."""
    assert EnrollmentRequestSerializer(data=_body(hostname=hostname)).is_valid()


@pytest.mark.parametrize("field, value", [
    ("hostname", ""),
    ("hostname", "a" * 254),
    ("hostname", "host name"),
    ("hostname", "höst-01"),
    ("hostname", "worker-01\n"),
    ("nonce", VALID_NONCE.upper()),
    ("nonce", VALID_NONCE[:-1]),
    ("nonce", VALID_NONCE + "0"),
    ("nonce", 123456),
    ("proof", VALID_PROOF.upper()),
    ("proof", VALID_PROOF + "0"),
    ("proof", "é" * 64),
], ids=[
    "host-empty", "host-254", "host-space", "host-non-ascii", "host-trailing-newline",
    "nonce-uppercase", "nonce-31", "nonce-33", "nonce-int",
    "proof-uppercase", "proof-65", "proof-non-ascii",
])
def test_bad_shapes_are_refused(field, value):
    """FR-WK-8: non-ASCII, uppercase hex, wrong lengths, a trailing newline and a number are refused."""
    s = EnrollmentRequestSerializer(data=_body(**{field: value}))
    assert not s.is_valid()
    assert set(s.errors) == {field}


@pytest.mark.parametrize("data, expected", [
    ({"enrollment_key": "4F9XK2PBQ7M3N8RT", "hostname": "h", "nonce": "n"}, True),
    (_body(), False),
    (["enrollment_key"], False),
], ids=["old-body", "new-body", "not-a-mapping"])
def test_carries_legacy_key(data, expected):
    """FR-WK-8: a body that still has enrollment_key is recognised; a non-mapping body is not."""
    assert carries_legacy_key(data) is expected
