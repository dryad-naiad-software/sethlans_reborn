# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Coverage expansion: ``wizard/sethlans_wizard/wizard_state.py``
(FR-M2-5 / FR-M2-6).

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


def _admin_writer_loop(stop: threading.Event) -> None:
    """Helper for ``TestSnapshot``: spin admin writes until stopped."""
    i = 0
    while not stop.is_set():
        wizard_state.set_admin(
            f"user{i}",
            f"user{i}@example.org",
            f"pw-user{i}",
        )
        i = (i + 1) % 100


def _worker_pw_writer_loop(stop: threading.Event) -> None:
    """Helper for ``TestSnapshot``: spin worker-password writes."""
    i = 0
    while not stop.is_set():
        # hash and salt both encode ``i`` so we can verify they came
        # from the same writer in any consistent snapshot.
        wizard_state.set_worker_password_hash(
            f"{i:064x}", f"{i:032x}",
        )
        i = (i + 1) % 100


def _check_admin_coherent(snap: dict, errors: list[str]) -> None:
    a = snap["admin"]
    if a is None:
        return
    user = a["username"]
    if (
        a["email"] != f"{user}@example.org"
        or a["password_plaintext"] != f"pw-{user}"
    ):
        errors.append(f"torn admin: {a!r}")


def _check_worker_pw_coherent(snap: dict, errors: list[str]) -> None:
    w = snap["worker_password"]
    if w is None:
        return
    h_int = int(w["hash"], 16)
    s_int = int(w["salt"], 16)
    if h_int != s_int:
        errors.append(
            f"torn worker_password: hash={h_int} salt={s_int}"
        )


def _snapper_loop(snapshots: list[dict], errors: list[str]) -> None:
    for _ in range(500):
        snap = wizard_state.snapshot()
        _check_admin_coherent(snap, errors)
        _check_worker_pw_coherent(snap, errors)
        snapshots.append(snap)


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


class TestResetStateForTests:

    def test_clears_every_slice(self):
        wizard_state.set_admin("alice", "a@example.org", "pw")
        wizard_state.set_worker_password_hash("a" * 64, "b" * 32)
        wizard_state.reset_state_for_tests()
        assert wizard_state.get_admin() is None
        assert wizard_state.get_worker_password() is None


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


class TestSnapshot:
    """Concurrency-reviewer C2 — ``snapshot()`` returns a single coherent
    view of every slice, taken under the module lock once. Callers that
    read more than one slice MUST use this instead of chaining
    individual getters.
    """

    def test_empty_snapshot_returns_all_none_slices(self):
        snap = wizard_state.snapshot()
        assert snap == {
            "admin": None,
            "worker_password": None,
        }

    def test_snapshot_round_trip(self):
        wizard_state.set_admin("alice", "a@example.org", "pw")
        wizard_state.set_worker_password_hash("h" * 64, "s" * 32)
        snap = wizard_state.snapshot()
        assert snap["admin"]["username"] == "alice"
        assert snap["worker_password"]["hash"] == "h" * 64

    def test_snapshot_is_plain_data_after_lock_release(self):
        # The snapshot dict is a fresh dict; mutating it does NOT
        # affect subsequent calls.
        wizard_state.set_admin("alice", "a@example.org", "pw")
        snap = wizard_state.snapshot()
        snap["admin"]["username"] = "MUTATED"
        snap2 = wizard_state.snapshot()
        assert snap2["admin"]["username"] == "alice"

    def test_concurrent_writers_produce_coherent_snapshot(self):
        """Two threads write to different slices in tight loops while a
        third repeatedly snapshots. Every snapshot's admin tuple MUST
        be self-consistent (username+email+password from same writer)
        and every snapshot's worker_password MUST be self-consistent
        (hash + salt from same writer). Asserts no torn cross-slice
        read across the snapshot.
        """
        stop = threading.Event()
        snapshots: list[dict] = []
        errors: list[str] = []

        threads = [
            threading.Thread(
                target=_admin_writer_loop, args=(stop,), daemon=True,
            ),
            threading.Thread(
                target=_worker_pw_writer_loop,
                args=(stop,), daemon=True,
            ),
        ]
        for t in threads:
            t.start()
        snap_thread = threading.Thread(
            target=_snapper_loop, args=(snapshots, errors),
        )
        snap_thread.start()
        snap_thread.join(timeout=10)
        stop.set()
        for t in threads:
            t.join(timeout=2)

        assert not errors, errors[:5]
        assert len(snapshots) > 0


class TestExports:

    def test_dunder_all(self):
        for name in (
            "set_admin", "get_admin", "clear_admin",
            "set_worker_password_hash", "get_worker_password",
            "snapshot",
            "reset_state_for_tests",
        ):
            assert name in wizard_state.__all__
