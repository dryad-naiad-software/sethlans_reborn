# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reads, writes and atomic replace of ``shared.settings_file``."""
import os
import threading

import pytest

from shared import settings_file, settings_file_io
from shared.file_lock import FileLockTimeoutError, file_lock


@pytest.fixture
def ini(tmp_path):
    return tmp_path / "settings" / "sethlans.ini"


class TestRoundTrip:

    def test_percent_values_round_trip(self, ini):
        values = {
            "password": "p%ss%(name)s%%end",
            "engine_path": "C:\\db\\100%\\x.sqlite3",
            "plain": "100%",
        }
        settings_file.set_values("database", values, ini)
        parser = settings_file.read_settings(ini)
        assert dict(parser["database"]) == values
        assert "p%ss%(name)s%%end" in ini.read_text(encoding="utf-8")

    def test_set_values_keeps_other_keys_and_sections(self, ini):
        settings_file.set_values("server", {"host": "0.0.0.0", "port": 8080}, ini)
        settings_file.set_values("database", {"engine": "sqlite"}, ini)
        settings_file.set_values("server", {"port": 9000}, ini)
        parser = settings_file.read_settings(ini)
        assert parser.get("server", "host") == "0.0.0.0"
        assert parser.get("server", "port") == "9000"
        assert parser.get("database", "engine") == "sqlite"

    def test_missing_file_reads_empty(self, ini):
        assert settings_file.read_settings(ini).sections() == []

    @pytest.mark.parametrize("body", [b"no header\n", b"[a]\nk = 1\n[a]\nk = 2\n", b"\xff\xfe[a]\n"])
    def test_corrupt_file_raises(self, ini, body):
        ini.parent.mkdir(parents=True)
        ini.write_bytes(body)
        with pytest.raises(settings_file.SettingsFileError):
            settings_file.read_settings(ini)

    def test_oversized_file_raises(self, ini):
        ini.parent.mkdir(parents=True)
        ini.write_bytes(b"[a]\n" + b"k = v\n" * (settings_file.MAX_FILE_BYTES // 6 + 1))
        with pytest.raises(settings_file.SettingsFileError, match="larger than"):
            settings_file.read_settings(ini)

    def test_write_over_corrupt_file_fails_and_keeps_it(self, ini):
        ini.parent.mkdir(parents=True)
        ini.write_bytes(b"no header\n")
        with pytest.raises(settings_file.SettingsFileError):
            settings_file.set_values("server", {"port": 1}, ini)
        assert ini.read_bytes() == b"no header\n"


class TestValidation:

    @pytest.mark.parametrize("section", ["", " server", "a]b", "a\nb", 7])
    def test_bad_section(self, ini, section):
        with pytest.raises(ValueError):
            settings_file.set_values(section, {"k": "v"}, ini)

    @pytest.mark.parametrize("key", ["", "a=b", "a:b", "#c", "k\n", " k"])
    def test_bad_key(self, ini, key):
        with pytest.raises(ValueError):
            settings_file.set_values("s", {key: "v"}, ini)

    @pytest.mark.parametrize("value", [None, "a\nb", "a\rb"])
    def test_bad_value(self, ini, value):
        with pytest.raises(ValueError):
            settings_file.set_values("s", {"k": value}, ini)
        assert not ini.exists()


class TestAtomicReplace:

    @pytest.fixture
    def existing(self, ini):
        settings_file.set_values("server", {"port": "8080"}, ini)
        return ini.read_bytes()

    def _leftovers(self, ini):
        return [p.name for p in ini.parent.iterdir() if p.name.endswith(".tmp")]

    def test_failing_replace_keeps_old_file(self, ini, existing, mocker):
        mocker.patch.object(settings_file_io.os, "replace", side_effect=OSError("disk gone"))
        with pytest.raises(OSError, match="disk gone"):
            settings_file.set_values("server", {"port": "9999"}, ini)
        assert ini.read_bytes() == existing
        assert self._leftovers(ini) == []

    def test_failing_fsync_keeps_old_file(self, ini, existing, mocker):
        mocker.patch.object(settings_file_io.os, "fsync", side_effect=OSError("io error"))
        with pytest.raises(OSError, match="io error"):
            settings_file.set_values("server", {"port": "9999"}, ini)
        assert ini.read_bytes() == existing
        assert self._leftovers(ini) == []

    def test_failed_owner_only_check_fails_closed(self, ini, existing, mocker):
        mocker.patch.object(settings_file_io.owner_only_acl, "verify_owner_only", return_value=False)
        with pytest.raises(settings_file_io.SettingsPermissionError):
            settings_file.set_values("server", {"port": "9999"}, ini)
        assert ini.read_bytes() == existing
        assert self._leftovers(ini) == []

    def test_windows_replace_retries_while_target_is_open(self, ini, existing, mocker):
        real_replace = os.replace
        calls = []

        def flaky(src, dst):
            calls.append(src)
            if len(calls) < 3:
                raise PermissionError("in use")
            real_replace(src, dst)

        mocker.patch.object(settings_file_io, "_IS_WINDOWS", True)
        mocker.patch.object(settings_file_io, "_REPLACE_RETRY_SECONDS", 0)
        mocker.patch.object(settings_file_io.os, "replace", side_effect=flaky)
        settings_file.set_values("server", {"port": "9999"}, ini)
        assert len(calls) == 3
        assert settings_file.read_settings(ini).get("server", "port") == "9999"

    def test_posix_replace_permission_error_is_not_retried(self, ini, existing, mocker):
        mocker.patch.object(settings_file_io, "_IS_WINDOWS", False)
        replace = mocker.patch.object(settings_file_io.os, "replace", side_effect=PermissionError("denied"))
        with pytest.raises(PermissionError):
            settings_file.set_values("server", {"port": "9999"}, ini)
        assert replace.call_count == 1
        assert ini.read_bytes() == existing
        assert self._leftovers(ini) == []

    def test_unchanged_mutation_does_not_write(self, ini, existing, mocker):
        spy = mocker.spy(settings_file, "atomic_replace")
        assert settings_file.update_settings(lambda parser: False, ini) is False
        spy.assert_not_called()


class TestRemove:

    def test_remove_key(self, ini):
        settings_file.set_values("setup", {"version": "1", "completed_at": "x"}, ini)
        assert settings_file.remove_key("setup", "completed_at", ini) is True
        parser = settings_file.read_settings(ini)
        assert parser.has_option("setup", "version")
        assert not parser.has_option("setup", "completed_at")
        assert settings_file.remove_key("setup", "completed_at", ini) is False
        assert settings_file.remove_key("nosuch", "k", ini) is False

    def test_remove_section(self, ini):
        settings_file.set_values("first_admin", {"username": "a"}, ini)
        settings_file.set_values("server", {"port": "1"}, ini)
        assert settings_file.remove_section("first_admin", ini) is True
        assert settings_file.read_settings(ini).sections() == ["server"]
        assert settings_file.remove_section("first_admin", ini) is False

    def test_remove_on_missing_file_creates_nothing(self, ini):
        assert settings_file.remove_key("setup", "completed_at", ini) is False
        assert settings_file.remove_section("setup", ini) is False
        assert not ini.parent.exists()


class TestLock:

    def test_concurrent_writers_both_persist(self, ini):
        def writer(section):
            for i in range(10):
                settings_file.set_values(section, {f"k{i}": str(i)}, ini)

        threads = [threading.Thread(target=writer, args=(name,)) for name in ("a", "b", "c")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        parser = settings_file.read_settings(ini)
        for name in ("a", "b", "c"):
            assert len(parser[name]) == 10

    def test_held_lock_times_out(self, ini):
        settings_file.set_values("server", {"port": "1"}, ini)
        with file_lock(settings_file.lock_path(ini)):
            with pytest.raises(FileLockTimeoutError):
                with file_lock(settings_file.lock_path(ini), timeout=0.1):
                    pass

    def test_lock_file_sits_next_to_the_settings_file(self, ini):
        settings_file.set_values("server", {"port": "1"}, ini)
        assert settings_file.lock_path(ini) == ini.with_name("sethlans.ini.lock")
        assert os.path.exists(settings_file.lock_path(ini))
