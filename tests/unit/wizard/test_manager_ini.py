# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for ``wizard/sethlans_wizard/manager_ini.py`` (FR-M2-INI).

Combines the dev agent's smoke pass with coverage expansion: atomic
write + permissions, multi-section preservation, lock singleton,
input validation, error handling on corrupt existing INI.
"""

from __future__ import annotations

import configparser
import os
import platform
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from wizard.sethlans_wizard import manager_ini


class TestUpdateManagerIni:

    def test_creates_new_file(self, tmp_path):
        manager_ini.update_manager_ini(
            tmp_path, "server",
            {"bind_host": "127.0.0.1", "bind_port": 8080},
        )
        target = tmp_path / "manager.ini"
        assert target.exists()
        parser = configparser.ConfigParser()
        parser.read(str(target))
        assert parser.get("server", "bind_host") == "127.0.0.1"
        assert parser.getint("server", "bind_port") == 8080

    def test_preserves_unrelated_sections(self, tmp_path):
        manager_ini.update_manager_ini(
            tmp_path, "server",
            {"bind_host": "0.0.0.0", "bind_port": 8080},
        )
        manager_ini.update_manager_ini(
            tmp_path, "database",
            {"engine": "sqlite", "name": "sethlans.db"},
        )
        target = tmp_path / "manager.ini"
        parser = configparser.ConfigParser()
        parser.read(str(target))
        assert parser.get("server", "bind_host") == "0.0.0.0"
        assert parser.get("database", "engine") == "sqlite"

    def test_section_overwrite_idempotent(self, tmp_path):
        manager_ini.update_manager_ini(
            tmp_path, "server",
            {"bind_host": "0.0.0.0", "bind_port": 8080},
        )
        manager_ini.update_manager_ini(
            tmp_path, "server",
            {"bind_host": "127.0.0.1", "bind_port": 9000},
        )
        target = tmp_path / "manager.ini"
        parser = configparser.ConfigParser()
        parser.read(str(target))
        assert parser.get("server", "bind_host") == "127.0.0.1"
        assert parser.getint("server", "bind_port") == 9000

    # ---- Coverage expansion: partial-update (preserve other keys) ----

    def test_partial_update_preserves_other_keys_in_same_section(
        self, tmp_path,
    ):
        # FR-M2-INI — existing keys outside the update payload survive.
        manager_ini.update_manager_ini(
            tmp_path, "database",
            {"engine": "postgresql", "host": "db.example",
             "port": 5432, "user": "u", "password": "p", "name": "x"},
        )
        # Now update only ``host`` — every other key MUST stick around.
        manager_ini.update_manager_ini(
            tmp_path, "database", {"host": "new.example"},
        )
        parser = configparser.ConfigParser()
        parser.read(str(tmp_path / "manager.ini"))
        assert parser.get("database", "host") == "new.example"
        assert parser.get("database", "engine") == "postgresql"
        assert parser.getint("database", "port") == 5432
        assert parser.get("database", "user") == "u"
        assert parser.get("database", "password") == "p"
        assert parser.get("database", "name") == "x"

    def test_none_values_skipped_not_written_as_string(self, tmp_path):
        # Coverage expansion: a None value in *values* must be silently
        # dropped — without this rule a Python None becomes the string
        # "None" via configparser.
        manager_ini.update_manager_ini(
            tmp_path, "server",
            {"bind_host": "127.0.0.1", "bind_port": 8080,
             "data_dir": None},
        )
        parser = configparser.ConfigParser()
        parser.read(str(tmp_path / "manager.ini"))
        assert not parser.has_option("server", "data_dir")

    def test_int_values_coerced_to_str(self, tmp_path):
        # Coverage expansion: configparser only stores strings; the
        # helper must coerce ints transparently.
        manager_ini.update_manager_ini(
            tmp_path, "server", {"bind_port": 9000},
        )
        target = tmp_path / "manager.ini"
        # Read raw text — ensure no python repr leaks in.
        text = target.read_text(encoding="utf-8")
        assert "9000" in text


class TestInputValidation:

    def test_rejects_empty_section(self, tmp_path):
        with pytest.raises(ValueError):
            manager_ini.update_manager_ini(tmp_path, "", {"k": "v"})

    def test_rejects_non_string_section(self, tmp_path):
        with pytest.raises(ValueError):
            manager_ini.update_manager_ini(tmp_path, 42, {"k": "v"})  # type: ignore[arg-type]

    def test_rejects_empty_key(self, tmp_path):
        with pytest.raises(ValueError):
            manager_ini.update_manager_ini(tmp_path, "server", {"": "v"})

    def test_rejects_non_string_key(self, tmp_path):
        with pytest.raises(ValueError):
            manager_ini.update_manager_ini(
                tmp_path, "server", {42: "v"},  # type: ignore[dict-item]
            )

    def test_accepts_str_data_dir(self, tmp_path):
        # Coverage expansion: data_dir may arrive as plain string.
        manager_ini.update_manager_ini(
            str(tmp_path), "server", {"bind_host": "127.0.0.1"},
        )
        assert (tmp_path / "manager.ini").exists()


class TestAtomicWrite:

    def test_no_tmp_left_on_disk(self, tmp_path):
        manager_ini.update_manager_ini(
            tmp_path, "server", {"bind_host": "127.0.0.1"},
        )
        assert not (tmp_path / "manager.ini.tmp").exists()
        assert (tmp_path / "manager.ini").exists()

    def test_creates_parent_dir(self, tmp_path):
        nested = tmp_path / "deep" / "nest"
        manager_ini.update_manager_ini(
            nested, "server", {"bind_host": "127.0.0.1"},
        )
        assert (nested / "manager.ini").exists()

    @pytest.mark.skipif(
        platform.system() == "Windows",
        reason="POSIX chmod semantics only",
    )
    def test_chmod_600_on_posix(self, tmp_path):
        manager_ini.update_manager_ini(
            tmp_path, "server", {"bind_host": "127.0.0.1"},
        )
        target = tmp_path / "manager.ini"
        mode = os.stat(target).st_mode & 0o777
        assert mode == 0o600

    @pytest.mark.skipif(
        platform.system() != "Windows",
        reason="Windows ACL helper only",
    )
    def test_calls_acl_tighten_on_windows(self, tmp_path, mocker):
        spy = mocker.patch(
            "wizard.sethlans_wizard.manager_ini.tighten_acls_windows",
        )
        manager_ini.update_manager_ini(
            tmp_path, "server", {"bind_host": "127.0.0.1"},
        )
        assert spy.called
        called_path = spy.call_args[0][0]
        assert Path(called_path) == tmp_path / "manager.ini"


class TestCorruptExistingFile:

    def test_unparseable_existing_file_rewritten_from_scratch(self, tmp_path):
        # Coverage expansion: a previous run wrote something that
        # configparser cannot parse (a stray operator edit). The helper
        # MUST recover instead of crashing.
        target = tmp_path / "manager.ini"
        target.write_text("not = ini = format\nat all", encoding="utf-8")
        manager_ini.update_manager_ini(
            tmp_path, "server", {"bind_host": "127.0.0.1"},
        )
        parser = configparser.ConfigParser()
        parser.read(str(target))
        assert parser.get("server", "bind_host") == "127.0.0.1"


class TestLockSingleton:

    def test_lock_is_singleton(self):
        a = manager_ini.get_manager_ini_lock()
        b = manager_ini.get_manager_ini_lock()
        assert a is b
        assert isinstance(a, type(threading.Lock()))


class TestConcurrency:

    def test_concurrent_writes_to_different_sections_both_persist(
        self, tmp_path,
    ):
        # Coverage expansion: concurrency-reviewer F5 — the per-process
        # lock MUST serialize multi-section writes so neither side gets
        # clobbered by the other reading a stale snapshot.
        def writer_a():
            manager_ini.update_manager_ini(
                tmp_path, "server",
                {"bind_host": "0.0.0.0", "bind_port": 8080},
            )

        def writer_b():
            manager_ini.update_manager_ini(
                tmp_path, "database",
                {"engine": "sqlite", "name": "sethlans.db"},
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futs = [pool.submit(fn) for fn in (writer_a, writer_b)]
            for f in futs:
                f.result()
        parser = configparser.ConfigParser()
        parser.read(str(tmp_path / "manager.ini"))
        assert parser.get("server", "bind_host") == "0.0.0.0"
        assert parser.get("database", "engine") == "sqlite"


class TestExports:

    def test_dunder_all(self):
        for name in (
            "MANAGER_INI_FILENAME",
            "update_manager_ini",
            "get_manager_ini_lock",
        ):
            assert name in manager_ini.__all__

    def test_filename_constant(self):
        assert manager_ini.MANAGER_INI_FILENAME == "manager.ini"
