# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for ``wizard/sethlans_wizard/progress.py`` (FR-CHK1 / FR-CHK1a).

Combines the dev agent's smoke pass with coverage expansion targeting
the per-process lock invariant, atomic-write semantics, transient
read-retry path, schema sanity, and read-after-corruption recovery.
"""

from __future__ import annotations

import json
import os
import platform
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from wizard.sethlans_wizard import progress


class TestAppendCheckpoint:

    def test_creates_progress_file_on_first_append(self, tmp_path):
        added = progress.append_checkpoint(
            tmp_path, "welcome_seen", topology="manager",
        )
        assert added is True
        path = tmp_path / progress.PROGRESS_FILENAME
        assert path.exists()
        payload = json.loads(path.read_text("utf-8"))
        assert payload["schema_version"] == 1
        assert payload["topology"] == "manager"
        assert payload["checkpoints"] == ["welcome_seen"]

    def test_idempotent_on_duplicate(self, tmp_path):
        progress.append_checkpoint(
            tmp_path, "welcome_seen", topology="manager",
        )
        added_again = progress.append_checkpoint(tmp_path, "welcome_seen")
        assert added_again is False
        path = tmp_path / progress.PROGRESS_FILENAME
        payload = json.loads(path.read_text("utf-8"))
        assert payload["checkpoints"] == ["welcome_seen"]

    def test_appends_in_order(self, tmp_path):
        for name in ("welcome_seen", "topology_chosen", "network_configured"):
            progress.append_checkpoint(tmp_path, name, topology="manager")
        path = tmp_path / progress.PROGRESS_FILENAME
        payload = json.loads(path.read_text("utf-8"))
        assert payload["checkpoints"] == [
            "welcome_seen", "topology_chosen", "network_configured",
        ]

    # ---- Coverage expansion: input validation ----

    def test_rejects_empty_checkpoint_name(self, tmp_path):
        with pytest.raises(ValueError):
            progress.append_checkpoint(tmp_path, "")

    def test_rejects_non_string_checkpoint_name(self, tmp_path):
        with pytest.raises(ValueError):
            progress.append_checkpoint(tmp_path, None)  # type: ignore[arg-type]

    def test_accepts_str_data_dir_via_coercion(self, tmp_path):
        # Coverage expansion: data_dir may arrive as a plain string.
        progress.append_checkpoint(str(tmp_path), "welcome_seen")
        assert (tmp_path / progress.PROGRESS_FILENAME).exists()

    # ---- Coverage expansion: topology backfill ----

    def test_topology_set_on_first_call_then_preserved(self, tmp_path):
        progress.append_checkpoint(
            tmp_path, "welcome_seen", topology="manager",
        )
        progress.append_checkpoint(
            tmp_path, "topology_chosen", topology="manager_worker",
        )
        payload = json.loads(
            (tmp_path / progress.PROGRESS_FILENAME).read_text("utf-8"),
        )
        # The first topology recorded wins.
        assert payload["topology"] == "manager"

    def test_null_topology_backfilled_on_subsequent_call(self, tmp_path):
        # Coverage expansion: when the existing payload has topology=None
        # and a later caller supplies one, it MUST backfill.
        progress.append_checkpoint(tmp_path, "welcome_seen")  # no topology
        progress.append_checkpoint(
            tmp_path, "topology_chosen", topology="manager",
        )
        payload = json.loads(
            (tmp_path / progress.PROGRESS_FILENAME).read_text("utf-8"),
        )
        assert payload["topology"] == "manager"

    # ---- Coverage expansion: atomicity / error paths ----

    def test_no_tmp_left_on_disk_after_success(self, tmp_path):
        progress.append_checkpoint(
            tmp_path, "welcome_seen", topology="manager",
        )
        # The atomic-write helper writes to ``...json.tmp`` then renames.
        tmp = tmp_path / (progress.PROGRESS_FILENAME + ".tmp")
        assert not tmp.exists()

    @pytest.mark.skipif(
        platform.system() == "Windows",
        reason="POSIX chmod semantics only",
    )
    def test_chmod_600_on_posix(self, tmp_path):
        progress.append_checkpoint(
            tmp_path, "welcome_seen", topology="manager",
        )
        target = tmp_path / progress.PROGRESS_FILENAME
        mode = os.stat(target).st_mode & 0o777
        assert mode == 0o600

    def test_corrupt_existing_payload_treated_as_empty(self, tmp_path):
        # Coverage expansion: a half-written or operator-edited file
        # MUST NOT crash the next append. The handler reads it as
        # empty + writes a fresh schema.
        target = tmp_path / progress.PROGRESS_FILENAME
        target.write_bytes(b"not valid json{{")
        progress.append_checkpoint(
            tmp_path, "welcome_seen", topology="manager",
        )
        payload = json.loads(target.read_text("utf-8"))
        assert payload["checkpoints"] == ["welcome_seen"]

    def test_non_dict_payload_treated_as_empty(self, tmp_path):
        # Coverage expansion: an array payload is invalid; treat as empty.
        target = tmp_path / progress.PROGRESS_FILENAME
        target.write_text(json.dumps(["welcome_seen"]), encoding="utf-8")
        progress.append_checkpoint(
            tmp_path, "topology_chosen", topology="manager",
        )
        payload = json.loads(target.read_text("utf-8"))
        assert payload["checkpoints"] == ["topology_chosen"]

    def test_existing_checkpoints_non_list_replaced(self, tmp_path):
        # Coverage expansion: ``checkpoints`` field is the wrong type.
        target = tmp_path / progress.PROGRESS_FILENAME
        target.write_text(
            json.dumps({"checkpoints": "not-a-list"}), encoding="utf-8",
        )
        progress.append_checkpoint(
            tmp_path, "welcome_seen", topology="manager",
        )
        payload = json.loads(target.read_text("utf-8"))
        assert payload["checkpoints"] == ["welcome_seen"]


class TestReadCheckpoints:

    def test_missing_file_returns_empty_dict(self, tmp_path):
        assert progress.read_checkpoints(tmp_path) == {}

    def test_round_trip(self, tmp_path):
        progress.append_checkpoint(
            tmp_path, "welcome_seen", topology="manager",
        )
        payload = progress.read_checkpoints(tmp_path)
        assert payload["topology"] == "manager"
        assert "welcome_seen" in payload["checkpoints"]

    def test_accepts_str_data_dir(self, tmp_path):
        progress.append_checkpoint(tmp_path, "welcome_seen")
        payload = progress.read_checkpoints(str(tmp_path))
        assert payload["checkpoints"] == ["welcome_seen"]

    def test_corrupt_file_returns_empty_dict(self, tmp_path):
        target = tmp_path / progress.PROGRESS_FILENAME
        target.write_bytes(b"\xff\xff bad utf-8")
        assert progress.read_checkpoints(tmp_path) == {}


class TestConcurrencyFR_CHK1a:
    """Coverage expansion: FR-CHK1a — process-wide RMW lock."""

    def test_two_concurrent_appends_both_persisted(self, tmp_path):
        # Two concurrent appends with DIFFERENT names must both land —
        # the per-process lock serializes the read-modify-write sequence.
        names = ["alpha_step", "beta_step"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(progress.append_checkpoint, tmp_path, n, "manager")
                for n in names
            ]
            for f in futures:
                f.result()
        payload = json.loads(
            (tmp_path / progress.PROGRESS_FILENAME).read_text("utf-8"),
        )
        assert set(payload["checkpoints"]) == set(names)

    def test_concurrent_idempotent_appends_dedupe(self, tmp_path):
        # Eight threads racing the SAME name — only one entry should
        # appear in the final array. Verifies idempotency holds under
        # contention, not just sequential calls.
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [
                pool.submit(
                    progress.append_checkpoint, tmp_path, "shared", "manager",
                )
                for _ in range(8)
            ]
            results = [f.result() for f in futures]
        # Exactly one return value should be True (the others see the
        # entry already present and short-circuit).
        assert results.count(True) == 1
        payload = json.loads(
            (tmp_path / progress.PROGRESS_FILENAME).read_text("utf-8"),
        )
        assert payload["checkpoints"] == ["shared"]

    def test_get_progress_lock_returns_singleton(self):
        # Coverage expansion: the lock accessor MUST always return the
        # same instance — anything else means a race-window.
        a = progress.get_progress_lock()
        b = progress.get_progress_lock()
        assert a is b
        assert isinstance(a, type(threading.Lock()))


class TestAtomicReplaceFailure:
    """Coverage expansion: simulated os.replace failure must not corrupt
    existing state (FR-CHK1a — atomic semantics)."""

    def test_replace_failure_leaves_original_intact(
        self, tmp_path, monkeypatch,
    ):
        # First append succeeds normally.
        progress.append_checkpoint(
            tmp_path, "welcome_seen", topology="manager",
        )
        target = tmp_path / progress.PROGRESS_FILENAME
        original = target.read_bytes()

        # Force os.replace to fail on the SECOND append.
        real_replace = os.replace

        def boom(src, dst):
            if str(dst).endswith(progress.PROGRESS_FILENAME):
                raise OSError("simulated replace failure")
            real_replace(src, dst)

        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(OSError):
            progress.append_checkpoint(
                tmp_path, "topology_chosen", topology="manager",
            )

        # Original payload is still on disk byte-identical.
        assert target.read_bytes() == original
