# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Smoke tests for ``manager/workers/enrollment_key.py``.

These tests are deliberately narrow.  The broader suite (rotation
atomicity, DB persistence, management-command integration) is owned by
the test agents downstream.  The purpose here is to (1) pin the
cross-boundary parity invariant for ``derive_signing_subkey`` via a
shared test vector the worker-side test file uses verbatim and (2)
catch regressions on the normalisation rules before shipping the
implementation to reviewers.
"""

import hashlib

import pytest

from workers import enrollment_key


# ------------------------------------------------------------------
# Shared cross-boundary test vector — MUST MATCH worker-side test.
#
# NOTE: the spec's ``HMAC_PERSONALIZATION`` of ``b"sethlans-enroll-v1"``
# is 18 bytes and exceeds ``hashlib.blake2b``'s 16-byte person cap.  The
# manager and worker both use the 16-byte prefix of that literal:
#
#     b"sethlans-enroll-v1"[:16] == b"sethlans-enroll-"
#
# The expected hex below was computed once by running
#
#     hashlib.blake2b(
#         b"4F9XK2PBQ7M3N8RT",
#         person=b"sethlans-enroll-v1"[:16],
#         digest_size=32,
#     ).hexdigest()
#
# Both manager and worker implementations MUST produce this exact value.
# ------------------------------------------------------------------
TEST_VECTOR_NORMALIZED = "4F9XK2PBQ7M3N8RT"
TEST_VECTOR_SUBKEY_HEX = (
    "3480beeb6e90d784df9150c015f27727f429f2faf2b7fffc03e402ffbca11204"
)


# ---- normalize() ----

class TestNormalize:

    def test_happy_path(self):
        assert enrollment_key.normalize("4F9X-K2PB-Q7M3-N8RT") == \
            "4F9XK2PBQ7M3N8RT"

    def test_uppercases_and_strips(self):
        assert enrollment_key.normalize("  4f9x-k2pb-q7m3-n8rt  ") == \
            "4F9XK2PBQ7M3N8RT"

    def test_compact_form_accepted(self):
        assert enrollment_key.normalize("4F9XK2PBQ7M3N8RT") == \
            "4F9XK2PBQ7M3N8RT"

    @pytest.mark.parametrize(
        "bad_char", ["I", "L", "O", "U", "i", "l", "o", "u"],
    )
    def test_rejects_ambiguous_characters(self, bad_char):
        key = f"4F9XK2PB{bad_char * 2}M3N8RT"
        with pytest.raises(ValueError, match="ambiguous"):
            enrollment_key.normalize(key)

    def test_rejects_wrong_length(self):
        with pytest.raises(ValueError, match="format invalid"):
            enrollment_key.normalize("4F9XK2PB")  # too short
        with pytest.raises(ValueError, match="format invalid"):
            enrollment_key.normalize("4F9XK2PBQ7M3N8RT5")  # too long

    def test_rejects_non_string(self):
        with pytest.raises(ValueError, match="must be a string"):
            enrollment_key.normalize(12345)

    def test_rejects_invalid_characters(self):
        # '$' is not in the Crockford alphabet
        with pytest.raises(ValueError, match="format invalid"):
            enrollment_key.normalize("4F9XK2PBQ7M3N8R$")


# ---- format_display() ----

class TestFormatDisplay:

    def test_hyphenates_in_groups_of_four(self):
        assert enrollment_key.format_display("4F9XK2PBQ7M3N8RT") == \
            "4F9X-K2PB-Q7M3-N8RT"

    def test_round_trip_via_normalize(self):
        canonical = enrollment_key.normalize("4F9X-K2PB-Q7M3-N8RT")
        displayed = enrollment_key.format_display(canonical)
        assert enrollment_key.normalize(displayed) == canonical


# ---- generate_key() ----

class TestGenerateKey:

    def test_generates_canonical_length(self):
        for _ in range(32):
            key = enrollment_key.generate_key()
            # Should normalise without raising and round-trip cleanly.
            assert enrollment_key.normalize(key) == key

    def test_generates_unique_keys(self):
        # Not strictly guaranteed but overwhelmingly likely.
        keys = {enrollment_key.generate_key() for _ in range(100)}
        assert len(keys) == 100


# ---- derive_signing_subkey() — cross-boundary parity (AC-44) ----

class TestDeriveSigningSubkey:

    def test_shared_vector_matches(self):
        """AC-44: manager output must match worker output byte-for-byte.

        If this test fails, the worker-side test file with the same
        vector will also fail — fix both sides together.
        """
        subkey = enrollment_key.derive_signing_subkey(
            TEST_VECTOR_NORMALIZED,
        )
        assert subkey.hex() == TEST_VECTOR_SUBKEY_HEX

    def test_derivation_is_deterministic(self):
        a = enrollment_key.derive_signing_subkey(TEST_VECTOR_NORMALIZED)
        b = enrollment_key.derive_signing_subkey(TEST_VECTOR_NORMALIZED)
        assert a == b

    def test_different_keys_produce_different_subkeys(self):
        a = enrollment_key.derive_signing_subkey("4F9XK2PBQ7M3N8RT")
        b = enrollment_key.derive_signing_subkey("5F9XK2PBQ7M3N8RT")
        assert a != b

    def test_personalization_capped_to_16_bytes(self):
        # Sanity check for the cap workaround — the personalisation
        # must never exceed blake2b's 16-byte limit.
        assert len(enrollment_key.HMAC_PERSONALIZATION) <= 16

    def test_personalization_pin_matches_spec_prefix(self):
        # Document the truncation contract for reviewers: the shared
        # personalisation is the 16-byte prefix of the original literal.
        assert (
            enrollment_key.HMAC_PERSONALIZATION
            == b"sethlans-enroll-v1"[:16]
        )

    def test_subkey_length_is_32(self):
        subkey = enrollment_key.derive_signing_subkey(
            TEST_VECTOR_NORMALIZED,
        )
        assert len(subkey) == 32

    def test_matches_direct_blake2b_invocation(self):
        """Defence-in-depth: if we ever refactor to a non-stdlib impl,
        this test fails and forces a review."""
        direct = hashlib.blake2b(
            TEST_VECTOR_NORMALIZED.encode("utf-8"),
            person=enrollment_key.HMAC_PERSONALIZATION,
            digest_size=32,
        ).digest()
        assert enrollment_key.derive_signing_subkey(
            TEST_VECTOR_NORMALIZED,
        ) == direct
