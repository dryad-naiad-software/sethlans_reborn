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
from shared import settings_file_io


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    """Redirect the config store to a temp directory for every test."""
    data_dir = tmp_path / "sethlans-worker"
    # config.json and its lock live in the identity folder; data and
    # identity are the same folder by default.
    for name in ("get_data_dir", "get_identity_dir"):
        monkeypatch.setattr(
            f"sethlans_worker_agent.config_store.paths.{name}",
            lambda: data_dir,
        )
        # The re-exported symbol in the package __init__ also needs to
        # point at the same callable.
        monkeypatch.setattr(config_store, name, lambda: data_dir)
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


class TestSharedFileIo:
    def test_writes_and_reads_go_through_the_windows_retry(self, _tmp_data_dir, mocker):
        """Chunk 4 review (concurrency): config.json is replaced and read with the shared settings-file helpers,
        whose Windows sharing-violation retry is tested in tests/unit/shared/test_settings_file.py."""
        assert io_mod.replace_with_retry is settings_file_io.replace_with_retry
        assert io_mod.read_limited is settings_file_io.read_limited
        _tmp_data_dir.mkdir()
        replace = mocker.spy(io_mod, "replace_with_retry")
        read = mocker.spy(io_mod, "read_limited")

        config_store.set("manager.host", "h")
        assert config_store.get("manager.host") == "h"

        assert replace.call_args.args[1] == _tmp_data_dir / "config.json"
        assert read.call_args.args == (_tmp_data_dir / "config.json", io_mod.MAX_CONFIG_BYTES)

    @pytest.mark.parametrize("extra, fails", [(0, False), (1, True)], ids=["at-cap", "one-byte-over"])
    def test_file_over_the_cap_is_a_read_error(self, _tmp_data_dir, monkeypatch, extra, fails):
        """Chunk 4 review: a config.json larger than MAX_CONFIG_BYTES is not parsed; a strict read raises."""
        cap = 64
        monkeypatch.setattr(io_mod, "MAX_CONFIG_BYTES", cap)
        _tmp_data_dir.mkdir()
        body = '{"manager": {"host": "h"}}'
        (_tmp_data_dir / "config.json").write_bytes((body + " " * (cap + extra - len(body))).encode("ascii"))
        if fails:
            with pytest.raises(config_store.ConfigReadError, match="larger than 64 bytes"):
                config_store.load_strict()
        else:
            assert config_store.load_strict()["manager"]["host"] == "h"


def _cross_process_lock_held() -> bool:
    """True when another handle cannot take the config file lock now (timeout 0, no wait)."""
    probe = io_mod.CrossProcessLock(config_store.lockfile_path(), timeout=0)
    try:
        probe.__enter__()
    except config_store.ConfigLockTimeoutError:
        probe._fh.close()
        return True
    probe.__exit__(None, None, None)
    return False


class TestCompareAndSet:
    """``set_many(pairs, expect=...)``: the card choice write of the worker page vs ``--setup-apply``."""

    @pytest.fixture
    def config_file(self, _tmp_data_dir):
        _tmp_data_dir.mkdir()
        path = _tmp_data_dir / "config.json"
        path.write_text(json.dumps({"compute": {"revision": 3}}), encoding="utf-8")
        return path

    def test_matching_check_writes(self, config_file):
        """Chunk 4 review (concurrency): a passing check writes every pair in one write."""
        config_store.set_many([("compute.revision", 4), ("unreal.enabled", True)],
                              expect={"compute.revision": lambda value: value == 3})
        assert json.loads(config_file.read_text(encoding="utf-8")) == {
            "compute": {"revision": 4}, "unreal": {"enabled": True}}

    @pytest.mark.parametrize("dotted, found", [("compute.revision", 3), ("compute.missing", None)],
                             ids=["other-value", "missing"])
    def test_failing_check_raises_and_writes_nothing(self, config_file, dotted, found):
        """Chunk 4 review (concurrency): a failing check raises ConfigChangedError (key, stored value or None)
        and the file is left byte for byte."""
        before = config_file.read_bytes()
        with pytest.raises(config_store.ConfigChangedError) as caught:
            config_store.set_many([("compute.revision", 9)], expect={dotted: lambda value: value == 2})
        assert (caught.value.key, caught.value.value) == (dotted, found)
        assert config_file.read_bytes() == before

    def test_check_sees_the_file_as_it_is_inside_both_locks(self, config_file):
        """Chunk 4 review (concurrency): the check runs on config.json read again inside the in-process and the
        cross-process lock, so a write by another process just before is seen."""
        seen = []
        config_file.write_text(json.dumps({"compute": {"revision": 7}}), encoding="utf-8")  # the other process

        def check(value):
            seen.append((value, config_store._config_lock.locked(), _cross_process_lock_held()))
            return True
        config_store.set_many([("compute.revision", 8)], expect={"compute.revision": check})
        assert seen == [(7, True, True)]


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
