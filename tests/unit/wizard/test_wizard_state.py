# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Coverage expansion: ``wizard/sethlans_wizard/wizard_state.py``
(FR-M2-5 / FR-M2-6 / FR-M2-7).

The dev agent's smoke pass exercised wizard_state INDIRECTLY through
the admin / worker-password / pending-setup handler tests. This file
locks the contract directly: setters reject invalid input, getters
round-trip, the test-only reset helper wipes every slice, and the
module-level lock keeps writes from racing.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from wizard.sethlans_wizard import wizard_state


@pytest.fixture(autouse=True)
def _reset():
    wizard_state.reset_state_for_tests()
    yield
    wizard_state.reset_state_for_tests()


class TestSetAdmin:

    def test_round_trip(self):
        wizard_state.set_admin("alice", "alice@example.org", "secret-pa55")
        admin = wizard_state.get_admin()
        assert admin == {
            "username": "alice",
            "email": "alice@example.org",
            "password_plaintext": "secret-pa55",
        }

    def test_overwrites_prior_tuple(self):
        wizard_state.set_admin("alice", "a@example.org", "pw1")
        wizard_state.set_admin("bob", "b@example.org", "pw2")
        admin = wizard_state.get_admin()
        assert admin["username"] == "bob"
        assert admin["password_plaintext"] == "pw2"

    @pytest.mark.parametrize(
        "args",
        [
            ("", "a@example.org", "pw"),
            ("alice", "", "pw"),
            ("alice", "a@example.org", ""),
            (None, "a@example.org", "pw"),
            ("alice", None, "pw"),
            ("alice", "a@example.org", None),
            (123, "a@example.org", "pw"),
        ],
    )
    def test_rejects_empty_or_non_string_fields(self, args):
        with pytest.raises(ValueError):
            wizard_state.set_admin(*args)

    def test_get_admin_returns_none_when_unset(self):
        assert wizard_state.get_admin() is None

    def test_clear_admin_drops_state(self):
        wizard_state.set_admin("alice", "a@example.org", "pw")
        wizard_state.clear_admin()
        assert wizard_state.get_admin() is None


class TestSetWorkerPassword:

    def test_round_trip(self):
        wizard_state.set_worker_password_hash("a" * 64, "b" * 32)
        state = wizard_state.get_worker_password()
        assert state == {"hash": "a" * 64, "salt": "b" * 32}

    @pytest.mark.parametrize(
        "args",
        [
            ("", "salt"),
            ("hash", ""),
            (None, "salt"),
            ("hash", None),
            (123, "salt"),
        ],
    )
    def test_rejects_empty_or_non_string(self, args):
        with pytest.raises(ValueError):
            wizard_state.set_worker_password_hash(*args)

    def test_get_returns_none_when_unset(self):
        assert wizard_state.get_worker_password() is None

    def test_overwrites_prior(self):
        wizard_state.set_worker_password_hash("a" * 64, "b" * 32)
        wizard_state.set_worker_password_hash("c" * 64, "d" * 32)
        state = wizard_state.get_worker_password()
        assert state == {"hash": "c" * 64, "salt": "d" * 32}


class TestSetFFmpeg:

    def test_round_trip(self):
        wizard_state.set_ffmpeg("7.1", "/opt/sethlans/ffmpeg/7.1/ffmpeg")
        meta = wizard_state.get_ffmpeg()
        assert meta == {
            "version": "7.1",
            "binary_path": "/opt/sethlans/ffmpeg/7.1/ffmpeg",
        }

    @pytest.mark.parametrize(
        "args",
        [
            ("", "/path"),
            ("7.1", ""),
            (None, "/path"),
            ("7.1", None),
            (7.1, "/path"),
        ],
    )
    def test_rejects_empty_or_non_string(self, args):
        with pytest.raises(ValueError):
            wizard_state.set_ffmpeg(*args)

    def test_get_returns_none_when_unset(self):
        assert wizard_state.get_ffmpeg() is None


class TestResetStateForTests:

    def test_clears_every_slice(self):
        wizard_state.set_admin("alice", "a@example.org", "pw")
        wizard_state.set_worker_password_hash("a" * 64, "b" * 32)
        wizard_state.set_ffmpeg("7.1", "/p")
        wizard_state.reset_state_for_tests()
        assert wizard_state.get_admin() is None
        assert wizard_state.get_worker_password() is None
        assert wizard_state.get_ffmpeg() is None


class TestThreadSafety:
    """Coverage expansion: the module-level lock must serialize concurrent
    writes — interleaved set_*/get_* calls from many threads must not
    return torn state."""

    def test_concurrent_set_admin_lands_one_consistent_tuple(self):
        # Eight threads racing different tuples; the final state must be
        # ONE consistent tuple (no half-written value where username
        # belongs to caller A and password to caller B).
        def writer(i: int) -> None:
            wizard_state.set_admin(
                f"user{i}", f"user{i}@example.org", f"pw-{i}",
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(writer, range(8)))
        admin = wizard_state.get_admin()
        assert admin is not None
        # username should match its email + password (consistent tuple).
        idx = admin["username"].removeprefix("user")
        assert admin["email"] == f"user{idx}@example.org"
        assert admin["password_plaintext"] == f"pw-{idx}"

    def test_concurrent_get_admin_does_not_crash(self):
        # The lock prevents a writer from being interrupted mid-write,
        # so readers always see the prior consistent tuple.
        wizard_state.set_admin("alice", "a@example.org", "pw")
        results: list[dict | None] = []

        def reader() -> None:
            results.append(wizard_state.get_admin())

        threads = [threading.Thread(target=reader) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert all(r is not None and r["username"] == "alice" for r in results)


class TestExports:

    def test_dunder_all(self):
        for name in (
            "set_admin", "get_admin", "clear_admin",
            "set_worker_password_hash", "get_worker_password",
            "set_ffmpeg", "get_ffmpeg", "reset_state_for_tests",
        ):
            assert name in wizard_state.__all__
