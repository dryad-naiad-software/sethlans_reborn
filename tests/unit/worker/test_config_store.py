# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Smoke tests for the worker config store.

Covers:
  * Round-trip ``set``/``get`` against a temporary data dir.
  * Atomic write: a mid-write failure leaves the original file intact
    and does not expose a partial tempfile to ``load()``.
  * AC-27a regression: 10 threads each setting a distinct dotted key
    concurrently — the final merged file contains all 10 values.

The broader multi-process file-lock test (AC-27b) and the platform
path-selection matrix are owned by the dedicated test agent.
"""

import json
import threading
from pathlib import Path

import pytest

from sethlans_worker_agent import config_store
from sethlans_worker_agent.config_store import io as io_mod


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    """Redirect the config store to a temp directory for every test."""
    data_dir = tmp_path / "sethlans-worker"
    monkeypatch.setattr(
        "sethlans_worker_agent.config_store.paths.get_data_dir",
        lambda: data_dir,
    )
    # The re-exported symbol in the package __init__ also needs to
    # point at the same callable.
    monkeypatch.setattr(config_store, "get_data_dir", lambda: data_dir)
    return data_dir


class TestRoundTrip:
    def test_get_default_when_missing(self):
        assert config_store.get("manager.api_token", "") == ""

    def test_set_then_get(self):
        config_store.set("manager.api_token", "abc-123")
        assert config_store.get("manager.api_token") == "abc-123"

    def test_set_creates_nested_structure(self):
        config_store.set("manager.port", 9090)
        merged = config_store.load()
        assert merged["manager"]["port"] == 9090

    def test_set_persists_across_fresh_load(self, _tmp_data_dir):
        config_store.set("manager.host", "studio.local")
        # Fresh load bypasses in-memory state entirely.
        raw = json.loads(
            (_tmp_data_dir / "config.json").read_text(encoding="utf-8")
        )
        assert raw["manager"]["host"] == "studio.local"

    def test_multiple_sets_preserve_siblings(self):
        config_store.set("manager.host", "h")
        config_store.set("manager.port", 8080)
        config_store.set("manager.api_token", "tok")
        merged = config_store.load()
        assert merged["manager"]["host"] == "h"
        assert merged["manager"]["port"] == 8080
        assert merged["manager"]["api_token"] == "tok"

    def test_set_rejects_empty_dotted_key(self):
        with pytest.raises(ValueError):
            config_store.set("", "x")


class TestAtomicWrite:
    def test_mid_write_crash_leaves_original_intact(
        self, _tmp_data_dir, monkeypatch,
    ):
        config_store.set("manager.host", "good-host")

        # Fault-inject a crash during json.dump so os.replace never
        # runs. atomic_write's except branch is responsible for
        # unlinking the tempfile.
        real_dump = json.dump

        def boom(*args, **kwargs):
            raise OSError("simulated disk failure")

        monkeypatch.setattr(io_mod.json, "dump", boom)

        with pytest.raises(OSError, match="simulated"):
            config_store.set("manager.host", "new-host")

        # Restore before reading so load() works normally.
        monkeypatch.setattr(io_mod.json, "dump", real_dump)

        # Original file is untouched.
        raw = json.loads(
            (_tmp_data_dir / "config.json").read_text(encoding="utf-8")
        )
        assert raw["manager"]["host"] == "good-host"

        # And no stray .tmp file is visible to load().
        tmps = list(_tmp_data_dir.glob(".config_*.tmp"))
        assert tmps == []


class TestConcurrentSet:
    def test_ten_threads_distinct_keys_all_land(self):
        """AC-27a: 10 concurrent mutations all survive the race.

        Covers the load -> mutate -> save cycle being held under the
        module lock for the ENTIRE sequence. Without the lock, later
        writes would clobber earlier ones because each thread reads
        the pre-existing config before mutating.
        """
        errors: list = []

        def worker(n: int):
            try:
                config_store.set(f"worker.slot_{n}", f"value-{n}")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [
            threading.Thread(target=worker, args=(i,))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == []
        merged = config_store.load()
        worker_section = merged.get("worker", {})
        for i in range(10):
            assert worker_section.get(f"slot_{i}") == f"value-{i}", (
                f"missing slot_{i}; final config = {merged!r}"
            )


class TestPermissions:
    def test_set_creates_file(self, _tmp_data_dir):
        config_store.set("manager.api_token", "x")
        assert (_tmp_data_dir / "config.json").exists()

    def test_parent_dir_created(self, _tmp_data_dir):
        config_store.set("manager.api_token", "x")
        assert _tmp_data_dir.exists()
        assert _tmp_data_dir.is_dir()


class TestSystemWideIgnoredOnNonLinux:
    def test_load_does_not_require_system_path(self):
        """The Linux system-wide path is ``None`` on Windows/macOS;
        ``load()`` must not crash on either platform."""
        merged = config_store.load()
        assert isinstance(merged, dict)


def test_get_data_dir_is_path_like():
    out = config_store.get_data_dir()
    assert isinstance(out, Path)
