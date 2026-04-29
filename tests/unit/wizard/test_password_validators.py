# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for ``wizard/sethlans_wizard/password_validators.py``
(FR-M2-5 / FR-VENDOR2 / security-reviewer LOW-2 + LOW-6).

Combines the dev agent's smoke pass with full branch coverage:
boundary lengths, similarity ratio threshold, every shipped common
password reviewer-blessed sample, length cap (4096 byte DoS guard),
and resource SHA-256 fail-closed semantics.
"""

from __future__ import annotations

import hashlib

import pytest

from wizard.sethlans_wizard import password_validators as pv


@pytest.fixture(autouse=True)
def _reset_resource_cache():
    pv.reset_resource_cache_for_tests()
    yield
    pv.reset_resource_cache_for_tests()


class TestResourceLoading:

    def test_resource_loads_and_verifies(self):
        # Coverage expansion: pinned SHA-256 matches the shipped resource.
        assert pv.verify_resource() is None

    def test_resource_known_count(self):
        # Coverage expansion: the resource should expose at least a few
        # thousand entries; sanity-check it isn't an empty file that
        # somehow happened to hash-match a placeholder.
        pv.reset_resource_cache_for_tests()
        assert pv.verify_resource() is None
        # Trigger a load via the public API; the cache is then warm.
        pv.validate_password("Tr0ub4dor&3xp1l@in", user_attrs=[])
        from wizard.sethlans_wizard import password_validators as mod
        assert mod._common_passwords_cache is not None
        assert len(mod._common_passwords_cache) > 1000


class TestStrongPassword:

    def test_strong_password_passes(self):
        failures = pv.validate_password(
            "Tr0ub4dor&3xp1l@in", user_attrs=["alice", "alice@example.org"],
        )
        assert failures == []


class TestMinimumLength:

    def test_seven_chars_rejected(self):
        # Coverage expansion: boundary at 8 — 7 must fail.
        failures = pv.validate_password("Abc!@1z", user_attrs=[])
        assert "password_too_short" in failures

    def test_eight_chars_passes_length(self):
        # Coverage expansion: boundary — 8 must clear the length test.
        # We pick a non-common, non-numeric, non-similar string.
        failures = pv.validate_password("Abc!@1zX", user_attrs=[])
        assert "password_too_short" not in failures

    def test_short_password_rejected(self):
        failures = pv.validate_password("hi", user_attrs=[])
        assert "password_too_short" in failures


class TestLengthCap:
    """security-reviewer LOW-7 / FR-VENDOR2 — DoS guard at 4096 chars."""

    def test_oversized_password_rejected_immediately(self):
        # Coverage expansion: returns ONLY password_too_long — the
        # similarity check must NOT have run on a 5000-char string.
        failures = pv.validate_password("a" * 5000, user_attrs=[])
        assert failures == ["password_too_long"]

    def test_4097_chars_too_long(self):
        # Coverage expansion: boundary check.
        failures = pv.validate_password("a" * 4097, user_attrs=[])
        assert failures == ["password_too_long"]

    def test_4096_chars_not_too_long(self):
        # Coverage expansion: boundary — 4096 is the cap, not over it.
        failures = pv.validate_password("a" * 4096, user_attrs=[])
        # Still fails the all-numeric/common rules potentially, but NOT
        # the length cap.
        assert "password_too_long" not in failures

    def test_oversized_skips_difflib(self, mocker):
        # Coverage expansion: assert the similarity check is NOT called
        # when the length cap trips. Otherwise the DoS guard is a lie.
        spy = mocker.spy(pv, "_check_user_attribute_similarity")
        pv.validate_password("z" * 5000, user_attrs=["alice"])
        assert spy.call_count == 0


class TestUserAttributeSimilarity:
    """FR-VENDOR2 — difflib SequenceMatcher.quick_ratio >= 0.7 rejects."""

    def test_exact_match_to_username_rejected(self):
        # Coverage expansion: ratio == 1.0 — must reject.
        failures = pv.validate_password(
            "alicesPassword!1", user_attrs=["alicesPassword!1"],
        )
        assert "password_too_similar" in failures

    def test_dissimilar_password_passes_similarity(self):
        # Coverage expansion: ratio < 0.7 — passes the similarity gate.
        failures = pv.validate_password(
            "Tr0ub4dor&3xp1l@in", user_attrs=["alice", "alice@example.org"],
        )
        assert "password_too_similar" not in failures

    def test_email_local_part_compared_separately(self):
        # Coverage expansion: a password matching the email LOCAL part
        # (before the @) but not the full address must still be flagged.
        failures = pv.validate_password(
            "alicebobcat", user_attrs=["alicebobcat@verylongdomain.example"],
        )
        assert "password_too_similar" in failures

    def test_empty_user_attrs_skips_similarity(self):
        failures = pv.validate_password("abc12345!Z", user_attrs=[])
        assert "password_too_similar" not in failures

    def test_none_user_attrs_does_not_raise(self):
        # Coverage expansion: caller passes ``user_attrs=None``.
        failures = pv.validate_password("abc12345!Z")
        assert "password_too_similar" not in failures

    def test_non_string_attrs_skipped(self):
        # Coverage expansion: the validator must skip non-str entries
        # without crashing.
        failures = pv.validate_password(
            "abc12345!Z", user_attrs=[None, 42, "alice"],
        )
        assert isinstance(failures, list)


class TestCommonPasswordValidator:

    @pytest.mark.parametrize(
        "candidate", ["password", "123456", "qwerty", "letmein"],
    )
    def test_known_common_passwords_rejected(self, candidate):
        # Coverage expansion: each of these is a Django-snapshot regular.
        failures = pv.validate_password(candidate, user_attrs=[])
        # The longer ones don't trip too-short; either way too_common
        # should appear.
        assert "password_too_common" in failures

    def test_uppercase_common_password_normalized(self):
        # Coverage expansion: validator lowercases before set-membership.
        failures = pv.validate_password("PASSWORD", user_attrs=[])
        assert "password_too_common" in failures


class TestNumericPasswordValidator:

    def test_all_numeric_rejected(self):
        # Coverage expansion: 12345678 is all-digit AND length-OK.
        failures = pv.validate_password("12345678", user_attrs=[])
        assert "password_entirely_numeric" in failures

    def test_mostly_numeric_with_one_letter_passes_numeric_rule(self):
        # Coverage expansion: a single letter trips ``str.isdigit()``
        # back to False — and the digit-only rule no longer fires.
        failures = pv.validate_password("12345678a", user_attrs=[])
        assert "password_entirely_numeric" not in failures


class TestNonStringInput:

    def test_returns_explicit_failure_code(self):
        # Coverage expansion: protect against a caller forgetting JSON
        # type checking; the validator MUST NOT raise.
        assert pv.validate_password(None) == ["password_not_a_string"]  # type: ignore[arg-type]
        assert pv.validate_password(12345) == ["password_not_a_string"]  # type: ignore[arg-type]


class TestResourceFailClosed:
    """security-reviewer LOW-6 — corrupted resource must hard-fail every
    call until the file is restored. Proves the wizard refuses to ship
    a weakened validator silently."""

    def test_corrupted_sha_returns_resource_invalid(self, mocker):
        # Coverage expansion: simulate the file existing but with the
        # WRONG bytes. Validator should refuse to load + every call
        # returns ``common_passwords_resource_invalid``.
        bad_bytes = b"hacked\nthrough\n"
        fake_resource = mocker.MagicMock()
        fake_resource.read_bytes.return_value = bad_bytes

        fake_files = mocker.MagicMock()
        fake_files.joinpath.return_value = fake_resource

        mocker.patch.object(pv, "files", return_value=fake_files)
        pv.reset_resource_cache_for_tests()

        assert pv.verify_resource() == "common_passwords_resource_invalid"
        # And subsequent validate_password calls bubble the same code.
        failures = pv.validate_password("anything12!", user_attrs=[])
        assert "common_passwords_resource_invalid" in failures

    def test_missing_resource_returns_invalid(self, mocker):
        fake_files = mocker.MagicMock()
        fake_files.joinpath.return_value.read_bytes.side_effect = (
            FileNotFoundError("missing")
        )
        mocker.patch.object(pv, "files", return_value=fake_files)
        pv.reset_resource_cache_for_tests()
        assert pv.verify_resource() == "common_passwords_resource_invalid"

    def test_non_utf8_resource_returns_invalid(self, mocker):
        # Coverage expansion: bytes that hash-match an arbitrary digest
        # but aren't UTF-8 still fail loudly. We don't fake a hash here;
        # a UTF-8 decode error path lives in the loader.
        bad = b"\xff\xfe not utf-8"
        # Force the SHA pin to match so we exercise the decode-error branch.
        digest = hashlib.sha256(bad).hexdigest()
        mocker.patch.object(pv, "COMMON_PASSWORDS_SHA256", digest)
        fake_resource = mocker.MagicMock()
        fake_resource.read_bytes.return_value = bad
        fake_files = mocker.MagicMock()
        fake_files.joinpath.return_value = fake_resource
        mocker.patch.object(pv, "files", return_value=fake_files)
        pv.reset_resource_cache_for_tests()
        assert pv.verify_resource() == "common_passwords_resource_invalid"

    def test_load_failure_is_cached(self, mocker):
        # Coverage expansion: once a load fails, subsequent calls MUST
        # NOT re-attempt the read (cache the failure).
        fake_files = mocker.MagicMock()
        fake_files.joinpath.return_value.read_bytes.side_effect = (
            FileNotFoundError("missing")
        )
        spy = mocker.patch.object(pv, "files", return_value=fake_files)
        pv.reset_resource_cache_for_tests()

        for _ in range(5):
            pv.verify_resource()
        # First call hits .files(); subsequent calls short-circuit on
        # the cached error.
        assert spy.call_count == 1


class TestExports:

    def test_dunder_all(self):
        for name in (
            "MIN_PASSWORD_LENGTH",
            "MAX_PASSWORD_LENGTH",
            "SIMILARITY_THRESHOLD",
            "COMMON_PASSWORDS_SHA256",
            "verify_resource",
            "validate_password",
            "reset_resource_cache_for_tests",
        ):
            assert name in pv.__all__

    def test_constants_pinned(self):
        # FR-VENDOR2 / LOW-7 — these MUST stay locked.
        assert pv.MIN_PASSWORD_LENGTH == 8
        assert pv.MAX_PASSWORD_LENGTH == 4096
        assert pv.SIMILARITY_THRESHOLD == 0.7
