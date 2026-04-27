# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Lock-in regression test for issue #153.

The launcher generates the IPC secret as
``secrets.token_urlsafe(32).encode("ascii")`` — URL-safe base64 bytes
from the alphabet ``A-Z a-z 0-9 - _``. The wizard reads it via
:func:`wizard.sethlans_wizard.ipc.read_secret_file`, which calls
``raw.strip()`` on the file contents.

Test fixtures previously generated the IPC secret as
``secrets.token_bytes(32)`` (raw binary). About 4.6% of those random
secrets had whitespace bytes (``\\t \\n \\v \\f \\r`` or space) at one
or both edges, which got stripped on read — corrupting the secret and
triggering intermittent HMAC mismatches in marker validation.

The fix landed in the test fixtures (matching production exactly).
This test is the lock-in: it asserts both halves of the contract that
makes the production strip safe — the URL-safe alphabet contains no
whitespace bytes, AND a token round-trips through ``read_secret_file``
unchanged.
"""

from __future__ import annotations

import secrets

import pytest

from wizard.sethlans_wizard.ipc import read_secret_file


# Whitespace bytes that ``bytes.strip()`` removes by default. Any of
# these appearing as the first or last byte of a secret would corrupt
# the value on read.
_WHITESPACE_BYTES = (b" ", b"\t", b"\n", b"\v", b"\f", b"\r")


class TestReadSecretFileMatchesProductionShape:
    """Issue #153: production secrets must round-trip cleanly."""

    def test_token_urlsafe_alphabet_contains_no_whitespace(self):
        """``secrets.token_urlsafe`` output is whitespace-free.

        The URL-safe base64 alphabet is ``A-Z a-z 0-9 - _``, none of
        which are whitespace. Sample many tokens to make the guarantee
        observable rather than just stated.
        """
        for _ in range(200):
            secret = secrets.token_urlsafe(32).encode("ascii")
            for ws in _WHITESPACE_BYTES:
                assert ws not in secret, (
                    f"token_urlsafe produced a secret containing "
                    f"whitespace byte {ws!r}: {secret!r}"
                )

    def test_round_trip_through_read_secret_file_preserves_bytes(
        self, tmp_path,
    ):
        """A production-shape secret round-trips read_secret_file unchanged.

        Belt-and-suspenders: even though ``read_secret_file`` strips,
        the production alphabet guarantees the strip is a no-op, so
        the bytes the wizard sees are byte-for-byte the bytes the
        launcher wrote.
        """
        for _ in range(50):
            secret = secrets.token_urlsafe(32).encode("ascii")
            secret_path = tmp_path / ".ipc_secret"
            secret_path.write_bytes(secret)
            read_back = read_secret_file(secret_path)
            assert read_back == secret, (
                f"round-trip mismatch: wrote {secret!r}, "
                f"read_secret_file returned {read_back!r}"
            )
            # read_secret_file unlinks after read (SEC-MED-11); confirm
            # so the loop's next iteration starts from a clean slate.
            assert not secret_path.exists()

    @pytest.mark.parametrize("ws", _WHITESPACE_BYTES)
    def test_strip_would_corrupt_random_binary_secrets(self, ws):
        """Document the bug we're locking out.

        If a fixture generated the secret as random binary that
        happened to start or end with one of these whitespace bytes,
        ``bytes.strip()`` would silently shorten it. This test pins
        the failure mode so future contributors can't accidentally
        re-introduce ``secrets.token_bytes(32)`` in a fixture without
        understanding why it broke.
        """
        # A 32-byte binary "secret" with a whitespace edge byte.
        bad_secret = ws + b"x" * 30 + ws
        assert len(bad_secret) == 32
        stripped = bad_secret.strip()
        assert stripped != bad_secret, (
            f"expected strip() to corrupt a binary secret with "
            f"whitespace edges (byte {ws!r}); it did not"
        )
        # The corrupted form is what an HMAC over the same payload
        # would be computed against on the wizard side, while the
        # launcher signed with the un-stripped form — hence the
        # mismatch observed in CI.
        assert len(stripped) < len(bad_secret)
