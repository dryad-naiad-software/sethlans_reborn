# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for the worker-side enrollment_key module.

Covers the Crockford base32 ``normalize`` rules (rejection of
I/L/O/U, length/regex validation) and the BLAKE2b signing subkey
derivation. Includes the AC-44 cross-boundary parity test vector —
the same normalized key and expected hex MUST also appear in
``tests/unit/manager/test_enrollment_key.py`` so manager/worker drift
is caught by CI.
"""

import hashlib

import pytest

from sethlans_worker_agent import enrollment_key


# ---------------------------------------------------------------------------
# AC-44: shared cross-boundary test vector (byte-identical to the
# manager-side test). Update both files together on any protocol
# personalization bump.
# ---------------------------------------------------------------------------

TEST_VECTOR_NORMALIZED = "4F9XK2PBQ7M3N8RT"
TEST_VECTOR_SUBKEY_HEX = (
    "3480beeb6e90d784df9150c015f27727f429f2faf2b7fffc03e402ffbca11204"
)


class TestNormalize:
    def test_happy_path_compact(self):
        assert enrollment_key.normalize("4F9XK2PBQ7M3N8RT") == "4F9XK2PBQ7M3N8RT"

    def test_happy_path_hyphenated(self):
        assert (
            enrollment_key.normalize("4F9X-K2PB-Q7M3-N8RT")
            == "4F9XK2PBQ7M3N8RT"
        )

    def test_lowercase_input_is_uppercased(self):
        assert (
            enrollment_key.normalize("4f9x-k2pb-q7m3-n8rt")
            == "4F9XK2PBQ7M3N8RT"
        )

    def test_strips_surrounding_whitespace(self):
        assert (
            enrollment_key.normalize("  4F9XK2PBQ7M3N8RT  ")
            == "4F9XK2PBQ7M3N8RT"
        )

    @pytest.mark.parametrize("banned", ["I", "L", "O", "U"])
    def test_rejects_ambiguous_chars(self, banned):
        bad_key = "4F9XK2PBQ7M3N8R" + banned
        with pytest.raises(ValueError, match="I/L/O/U"):
            enrollment_key.normalize(bad_key)

    def test_rejects_lowercase_ambiguous_chars(self):
        with pytest.raises(ValueError, match="I/L/O/U"):
            enrollment_key.normalize("4f9xk2pbq7m3n8ri")

    def test_rejects_wrong_length(self):
        with pytest.raises(ValueError, match="format invalid"):
            enrollment_key.normalize("4F9XK2PB")

    def test_rejects_non_crockford_char(self):
        # $ is not in the Crockford alphabet and is not in the I/L/O/U
        # set, so it trips the regex check rather than the char check.
        with pytest.raises(ValueError, match="format invalid"):
            enrollment_key.normalize("4F9XK2PBQ7M3N8R$")

    def test_rejects_non_string(self):
        with pytest.raises(ValueError, match="must be a string"):
            enrollment_key.normalize(12345)


class TestDeriveSigningSubkey:
    def test_deterministic(self):
        a = enrollment_key.derive_signing_subkey(TEST_VECTOR_NORMALIZED)
        b = enrollment_key.derive_signing_subkey(TEST_VECTOR_NORMALIZED)
        assert a == b

    def test_length_is_32_bytes(self):
        out = enrollment_key.derive_signing_subkey(TEST_VECTOR_NORMALIZED)
        assert isinstance(out, bytes)
        assert len(out) == 32

    def test_shared_test_vector_ac44(self):
        """AC-44 cross-boundary parity check.

        This test vector MUST match the manager's identical vector in
        ``tests/unit/manager/test_enrollment_key.py``. If it drifts,
        the HMAC on every enrollment response will fail and first-run
        enrollment will be broken end-to-end.
        """
        out = enrollment_key.derive_signing_subkey(TEST_VECTOR_NORMALIZED)
        assert out.hex() == TEST_VECTOR_SUBKEY_HEX

    def test_personalization_bytes_length_is_16(self):
        """BLAKE2b requires person to be exactly 16 bytes."""
        assert len(enrollment_key.HMAC_PERSONALIZATION) == 16

    def test_different_keys_produce_different_subkeys(self):
        a = enrollment_key.derive_signing_subkey("4F9XK2PBQ7M3N8RT")
        b = enrollment_key.derive_signing_subkey("4F9XK2PBQ7M3N8RV")
        assert a != b

    def test_personalization_affects_output(self):
        """Sanity check: a different personalization produces a different subkey."""
        alt = hashlib.blake2b(
            TEST_VECTOR_NORMALIZED.encode("utf-8"),
            person=b"sethlans-other--",  # 16 bytes, different content
            digest_size=32,
        ).digest()
        assert alt.hex() != TEST_VECTOR_SUBKEY_HEX
