# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Trust, secrecy and retry rules of the settings file.

Owner-only from birth, refusing files and folders another account owns,
parse errors that never quote the file, the lock timeout as an
``OSError``, and the Windows read retry while a writer replaces the file.
"""
import builtins
import logging
import stat
import sys

import pytest

from shared import file_lock, owner_only_acl, settings_file, settings_file_io
from tests.unit.shared._settings_env import write_settings

SECRET = "Pa55-w0rd-n0t-in-logs"


@pytest.fixture
def ini(tmp_path):
    return tmp_path / "settings" / "sethlans.ini"


class TestOwnerOnlyFromBirth:

    def test_temp_file_is_owner_only_before_any_byte(self, ini, mocker):
        seen = {}
        real_write_all = settings_file_io._write_all

        def inspect_then_write(fd, data):
            temps = [p for p in ini.parent.iterdir() if p.name.endswith(".tmp")]
            assert len(temps) == 1
            seen["owner_only"] = owner_only_acl.verify_owner_only(temps[0])
            seen["size"] = temps[0].stat().st_size
            if sys.platform == "win32":
                protected, owner, aces = owner_only_acl._win_read_security(str(temps[0]))
                seen["sids"] = {sid for _t, _f, sid in aces}
                seen["owner"] = owner
            else:
                seen["mode"] = stat.S_IMODE(temps[0].stat().st_mode)
            real_write_all(fd, data)

        mocker.patch.object(settings_file_io, "_write_all", side_effect=inspect_then_write)
        settings_file.set_values("database", {"password": SECRET}, ini)
        assert seen["size"] == 0
        assert seen["owner_only"] is True
        if sys.platform == "win32":
            assert seen["owner"] == owner_only_acl.current_user_sid()
            assert seen["sids"] == {seen["owner"], owner_only_acl.SYSTEM_SID}
        else:
            assert seen["mode"] == 0o600

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows sharing mode")
    def test_temp_file_cannot_be_opened_while_written(self, ini, mocker):
        blocked = {}
        real_write_all = settings_file_io._write_all

        def try_open_then_write(fd, data):
            temp = next(p for p in ini.parent.iterdir() if p.name.endswith(".tmp"))
            try:
                open(temp, "rb").close()
                blocked["open"] = False
            except PermissionError:
                blocked["open"] = True
            real_write_all(fd, data)

        mocker.patch.object(settings_file_io, "_write_all", side_effect=try_open_then_write)
        settings_file.set_values("database", {"password": SECRET}, ini)
        assert blocked["open"] is True

    def test_new_lock_file_is_owner_only(self, ini):
        settings_file.set_values("server", {"port": "1"}, ini)
        assert owner_only_acl.verify_owner_only(settings_file.lock_path(ini))


class TestForeignOwner:

    def test_read_refuses_file_owned_by_someone_else(self, ini, mocker):
        write_settings(ini, "[server]\nport = 1\n")
        mocker.patch.object(owner_only_acl, "is_owned_by_current_user", return_value=False)
        with pytest.raises(settings_file.SettingsFileError, match="not owned by the current user"):
            settings_file.read_settings(ini)
        assert settings_file.setup_finished(ini) is None
        assert settings_file.configured_data_root(ini) is None

    def test_write_refuses_folder_owned_by_someone_else(self, ini, mocker):
        ini.parent.mkdir(parents=True)
        mocker.patch.object(owner_only_acl, "is_owned_by_current_user", return_value=False)
        with pytest.raises(settings_file_io.SettingsPermissionError):
            settings_file.set_values("server", {"port": "1"}, ini)
        assert not ini.exists()

    def test_loose_existing_folder_is_used_with_a_warning(self, ini, caplog):
        ini.parent.mkdir(parents=True)
        if sys.platform != "win32":
            ini.parent.chmod(0o755)  # a tmp folder on Windows already inherits a loose DACL
        assert not owner_only_acl.verify_owner_only(ini.parent)
        with caplog.at_level(logging.WARNING, logger=settings_file_io.logger.name):
            settings_file.set_values("server", {"port": "1"}, ini)
        assert any("not owner-only" in rec.getMessage() for rec in caplog.records)
        assert owner_only_acl.verify_owner_only(ini)

    def test_loose_folder_warning_is_logged_once_per_folder(self, tmp_path, caplog):
        folders = [tmp_path / "a", tmp_path / "b"]
        for folder in folders:
            folder.mkdir()
            if sys.platform != "win32":
                folder.chmod(0o755)
        with caplog.at_level(logging.WARNING, logger=settings_file_io.logger.name):
            for _ in range(3):
                for folder in folders:
                    settings_file.set_values("server", {"port": "1"}, folder / "sethlans.ini")
        warned = [rec.getMessage() for rec in caplog.records if "not owner-only" in rec.getMessage()]
        assert len(warned) == 2
        assert {str(folders[0]) in m for m in warned} == {True, False}


class TestNoSecretInErrors:

    @pytest.mark.parametrize("body", [
        f"password = {SECRET}\n",                          # MissingSectionHeaderError
        f"[database]\nengine = x\n{SECRET}\n",             # ParsingError
        f"[database]\npassword = {SECRET}\npassword = {SECRET}\n",  # DuplicateOptionError
    ])
    def test_parse_error_never_quotes_the_file(self, ini, body):
        write_settings(ini, body)
        with pytest.raises(settings_file.SettingsFileError) as info:
            settings_file.read_settings(ini)
        message = str(info.value)
        assert SECRET not in message
        assert "line" in message
        assert info.value.__cause__ is None and info.value.__suppress_context__

    def test_bad_utf8_error_never_quotes_the_file(self, ini):
        ini.parent.mkdir(parents=True)
        ini.write_bytes(b"[database]\npassword = \xff" + SECRET.encode())
        with pytest.raises(settings_file.SettingsFileError) as info:
            settings_file.read_settings(ini)
        assert SECRET not in str(info.value)
        assert "UnicodeDecodeError at byte" in str(info.value)


class TestLockTimeout:

    def test_timeout_reaches_except_oserror(self, ini, mocker):
        settings_file.set_values("server", {"port": "1"}, ini)
        mocker.patch.object(settings_file, "file_lock", lambda path: file_lock.file_lock(path, timeout=0.05))
        caught = None
        with file_lock.file_lock(settings_file.lock_path(ini)):
            try:
                settings_file.set_values("server", {"port": "2"}, ini)
            except OSError as exc:  # what every caller of the settings writers catches
                caught = exc
        assert isinstance(caught, file_lock.FileLockTimeoutError)
        assert isinstance(caught, TimeoutError)
        assert settings_file.read_settings(ini).get("server", "port") == "1"


class TestReadRetry:

    def _flaky_open(self, mocker, failures):
        real_open = builtins.open
        calls = []

        def flaky(path, *args, **kwargs):
            if str(path).endswith("sethlans.ini") and len(calls) < failures:
                calls.append(path)
                raise PermissionError("being replaced")
            return real_open(path, *args, **kwargs)

        mocker.patch.object(builtins, "open", side_effect=flaky)
        return calls

    def test_windows_read_retries_while_replaced(self, ini, mocker):
        write_settings(ini, "[server]\nport = 7\n")
        mocker.patch.object(settings_file_io, "_IS_WINDOWS", True)
        mocker.patch.object(settings_file_io, "_REPLACE_RETRY_SECONDS", 0)
        calls = self._flaky_open(mocker, 3)
        assert settings_file.read_settings(ini).get("server", "port") == "7"
        assert len(calls) == 3

    def test_posix_read_permission_error_is_not_retried(self, ini, mocker):
        write_settings(ini, "[server]\nport = 7\n")
        mocker.patch.object(settings_file_io, "_IS_WINDOWS", False)
        calls = self._flaky_open(mocker, 1)
        with pytest.raises(settings_file.SettingsFileError, match="PermissionError"):
            settings_file.read_settings(ini)
        assert len(calls) == 1

    def test_missing_file_is_still_empty(self, ini):
        assert settings_file.read_settings(ini).sections() == []
