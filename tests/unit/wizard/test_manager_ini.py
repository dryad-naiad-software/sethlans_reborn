# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for ``wizard/sethlans_wizard/manager_ini.py`` (FR-M2-INI).

The helper writes the wizard's ``[server]`` / ``[database]`` sections to
the Sethlans settings file (``sethlans.ini``) through
``shared.settings_file``: atomic write + owner-only permissions,
multi-section preservation, lock singleton, input validation and a loud
failure on a corrupt existing file.
"""

from __future__ import annotations

import configparser
import os
import platform
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from shared import owner_only_acl
from wizard.sethlans_wizard import manager_ini


@pytest.fixture(autouse=True)
def target(tmp_path, monkeypatch):
    """The settings file every default-path call writes."""
    path = tmp_path / "sethlans.ini"
    monkeypatch.setenv("SETHLANS_SETTINGS_FILE", str(path))
    return path


def _read(path) -> configparser.RawConfigParser:
    parser = configparser.RawConfigParser(interpolation=None)
    parser.read(str(path), encoding="utf-8")
    return parser


class TestUpdateManagerIni:

    def test_creates_new_file(self, target):
        written = manager_ini.update_manager_ini(
            "server", {"bind_host": "127.0.0.1", "bind_port": 8080},
        )
        assert written == target
        parser = _read(target)
        assert parser.get("server", "bind_host") == "127.0.0.1"
        assert parser.getint("server", "bind_port") == 8080

    def test_preserves_unrelated_sections(self, target):
        manager_ini.update_manager_ini("server", {"bind_host": "0.0.0.0", "bind_port": 8080})
        manager_ini.update_manager_ini("database", {"engine": "sqlite", "name": "sethlans.db"})
        parser = _read(target)
        assert parser.get("server", "bind_host") == "0.0.0.0"
        assert parser.get("database", "engine") == "sqlite"

    def test_section_overwrite_idempotent(self, target):
        manager_ini.update_manager_ini("server", {"bind_host": "0.0.0.0", "bind_port": 8080})
        manager_ini.update_manager_ini("server", {"bind_host": "127.0.0.1", "bind_port": 9000})
        parser = _read(target)
        assert parser.get("server", "bind_host") == "127.0.0.1"
        assert parser.getint("server", "bind_port") == 9000

    def test_partial_update_preserves_other_keys_in_same_section(self, target):
        # FR-M2-INI — existing keys outside the update payload survive.
        manager_ini.update_manager_ini(
            "database",
            {"engine": "postgresql", "host": "db.example",
             "port": 5432, "user": "u", "password": "p%w", "name": "x"},
        )
        manager_ini.update_manager_ini("database", {"host": "new.example"})
        parser = _read(target)
        assert parser.get("database", "host") == "new.example"
        assert parser.get("database", "engine") == "postgresql"
        assert parser.getint("database", "port") == 5432
        assert parser.get("database", "user") == "u"
        assert parser.get("database", "password") == "p%w"
        assert parser.get("database", "name") == "x"

    def test_keeps_sections_written_by_others(self, target):
        target.write_text("[security]\ndebug = False\n", encoding="utf-8")
        manager_ini.update_manager_ini("server", {"bind_port": 8080})
        assert _read(target).get("security", "debug") == "False"

    def test_none_values_skipped_not_written_as_string(self, target):
        manager_ini.update_manager_ini(
            "server", {"bind_host": "127.0.0.1", "bind_port": 8080, "data_dir": None},
        )
        assert not _read(target).has_option("server", "data_dir")

    def test_int_values_coerced_to_str(self, target):
        manager_ini.update_manager_ini("server", {"bind_port": 9000})
        assert "9000" in target.read_text(encoding="utf-8")

    def test_explicit_ini_path(self, tmp_path, target):
        other = tmp_path / "deep" / "nest" / "sethlans.ini"
        written = manager_ini.update_manager_ini("server", {"bind_host": "127.0.0.1"}, str(other))
        assert written == other
        assert other.exists()
        assert not target.exists()


class TestInputValidation:

    def test_rejects_empty_section(self):
        with pytest.raises(ValueError):
            manager_ini.update_manager_ini("", {"k": "v"})

    def test_rejects_non_string_section(self):
        with pytest.raises(ValueError):
            manager_ini.update_manager_ini(42, {"k": "v"})  # type: ignore[arg-type]

    def test_rejects_empty_key(self):
        with pytest.raises(ValueError):
            manager_ini.update_manager_ini("server", {"": "v"})

    def test_rejects_non_string_key(self):
        with pytest.raises(ValueError):
            manager_ini.update_manager_ini("server", {42: "v"})  # type: ignore[dict-item]

    def test_rejects_line_break_in_value(self, target):
        with pytest.raises(ValueError):
            manager_ini.update_manager_ini("server", {"bind_host": "a\n[evil]"})
        assert not target.exists()


class TestAtomicWrite:

    def test_no_tmp_left_on_disk(self, tmp_path, target):
        manager_ini.update_manager_ini("server", {"bind_host": "127.0.0.1"})
        assert target.exists()
        assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []

    @pytest.mark.skipif(platform.system() == "Windows", reason="POSIX chmod semantics only")
    def test_chmod_600_on_posix(self, target):
        manager_ini.update_manager_ini("server", {"bind_host": "127.0.0.1"})
        assert os.stat(target).st_mode & 0o777 == 0o600

    @pytest.mark.skipif(platform.system() != "Windows", reason="Windows DACL only")
    def test_owner_only_dacl_on_windows(self, target):
        manager_ini.update_manager_ini("server", {"bind_host": "127.0.0.1"})
        assert owner_only_acl.verify_owner_only(target)


class TestCorruptExistingFile:

    def test_unparseable_existing_file_fails_and_is_kept(self, target):
        # A corrupt settings file is never silently replaced: the
        # caller's ``except OSError`` turns it into an error response.
        target.write_text("not = ini = format\nat all", encoding="utf-8")
        with pytest.raises(OSError):
            manager_ini.update_manager_ini("server", {"bind_host": "127.0.0.1"})
        assert target.read_text(encoding="utf-8") == "not = ini = format\nat all"


class TestLockSingleton:

    def test_lock_is_singleton(self):
        a = manager_ini.get_manager_ini_lock()
        b = manager_ini.get_manager_ini_lock()
        assert a is b
        assert isinstance(a, type(threading.Lock()))


class TestConcurrency:

    def test_concurrent_writes_to_different_sections_both_persist(self, target):
        # concurrency-reviewer F5 — writes are serialised so neither
        # side gets clobbered by the other reading a stale snapshot.
        def writer_a():
            manager_ini.update_manager_ini("server", {"bind_host": "0.0.0.0", "bind_port": 8080})

        def writer_b():
            manager_ini.update_manager_ini("database", {"engine": "sqlite", "name": "sethlans.db"})

        with ThreadPoolExecutor(max_workers=2) as pool:
            futs = [pool.submit(fn) for fn in (writer_a, writer_b)]
            for f in futs:
                f.result()
        parser = _read(target)
        assert parser.get("server", "bind_host") == "0.0.0.0"
        assert parser.get("database", "engine") == "sqlite"


class TestExports:

    def test_dunder_all(self):
        for name in ("MANAGER_INI_FILENAME", "update_manager_ini", "get_manager_ini_lock"):
            assert name in manager_ini.__all__

    def test_filename_constant(self):
        assert manager_ini.MANAGER_INI_FILENAME == "sethlans.ini"
