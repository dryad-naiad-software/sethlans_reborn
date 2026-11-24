# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``setup_finished`` and the owner-only permissions of the settings file."""
import stat
import sys

import pytest

from shared import owner_only_acl, settings_file
from tests.unit.shared._settings_env import clear_path_env, write_settings

GOOD = "[setup]\nversion = 1\nmode = {mode}\ncompleted_at = 2026-11-24T10:00:00+00:00\n"


@pytest.fixture
def ini(tmp_path):
    return tmp_path / "sethlans.ini"


class TestSetupFinished:

    @pytest.mark.parametrize("mode", ["manager", "manager_worker", "worker"])
    def test_good_file_returns_mode(self, ini, mode):
        write_settings(ini, GOOD.format(mode=mode))
        assert settings_file.setup_finished(ini) == mode

    def test_zulu_timestamp_parses(self, ini):
        write_settings(ini, "[setup]\nversion = 1\nmode = worker\ncompleted_at = 2026-11-24T10:00:00Z\n")
        assert settings_file.setup_finished(ini) == "worker"

    def test_missing_file(self, ini):
        assert settings_file.setup_finished(ini) is None

    def test_unreadable_file(self, ini):
        ini.mkdir()  # a folder where the file should be: open() fails on every OS
        assert settings_file.setup_finished(ini) is None

    @pytest.mark.parametrize("body", [b"garbage without header\n", b"\xff\xfe\x00", b""])
    def test_unparsable_or_empty_file(self, ini, body):
        ini.write_bytes(body)
        assert settings_file.setup_finished(ini) is None

    @pytest.mark.parametrize("version", ["", "0", "2", "1.0", "one"])
    def test_wrong_version(self, ini, version):
        write_settings(ini, GOOD.format(mode="manager").replace("version = 1", f"version = {version}"))
        assert settings_file.setup_finished(ini) is None

    def test_missing_version(self, ini):
        write_settings(ini, GOOD.format(mode="manager").replace("version = 1\n", ""))
        assert settings_file.setup_finished(ini) is None

    @pytest.mark.parametrize("mode", ["", "both", "manager+worker", "worker_only", "MANAGER"])
    def test_bad_mode(self, ini, mode):
        write_settings(ini, GOOD.format(mode=mode))
        assert settings_file.setup_finished(ini) is None

    def test_missing_completed_at(self, ini):
        write_settings(ini, "[setup]\nversion = 1\nmode = manager\n")
        assert settings_file.setup_finished(ini) is None

    @pytest.mark.parametrize("stamp", ["", "yesterday", "2026-13-40T99:00:00", "1700000000"])
    def test_unparsable_completed_at(self, ini, stamp):
        write_settings(ini, f"[setup]\nversion = 1\nmode = manager\ncompleted_at = {stamp}\n")
        assert settings_file.setup_finished(ini) is None

    def test_no_setup_section(self, ini):
        write_settings(ini, "[server]\nport = 8080\n")
        assert settings_file.setup_finished(ini) is None

    def test_default_path_follows_settings_path(self, monkeypatch, ini):
        clear_path_env(monkeypatch)
        write_settings(ini, GOOD.format(mode="manager_worker"))
        monkeypatch.setenv("SETHLANS_SETTINGS_FILE", str(ini))
        assert settings_file.setup_finished() == "manager_worker"

    def test_bad_env_does_not_raise(self, monkeypatch):
        clear_path_env(monkeypatch)
        monkeypatch.setenv("SETHLANS_SETTINGS_FILE", "relative.ini")
        assert settings_file.setup_finished() is None

    def test_reset_by_removing_completed_at(self, ini):
        write_settings(ini, GOOD.format(mode="manager"))
        settings_file.remove_key("setup", "completed_at", ini)
        assert settings_file.setup_finished(ini) is None
        assert settings_file.read_settings(ini).get("setup", "mode") == "manager"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DACL check")
class TestWindowsOwnerOnly:

    def test_written_file_dacl_is_owner_only(self, tmp_path):
        ini = tmp_path / "new-settings" / "sethlans.ini"
        settings_file.set_values("server", {"port": "8080"}, ini)
        assert owner_only_acl.verify_owner_only(ini)
        protected, owner, aces = owner_only_acl._win_read_security(str(ini))
        assert protected
        assert owner == owner_only_acl.current_user_sid()
        assert {sid for _type, _flags, sid in aces} == {owner, owner_only_acl.SYSTEM_SID}

    def test_new_settings_folder_is_owner_only(self, tmp_path):
        ini = tmp_path / "new-settings" / "sethlans.ini"
        settings_file.set_values("server", {"port": "8080"}, ini)
        assert owner_only_acl.verify_owner_only(ini.parent)

    def test_rewrite_keeps_owner_only(self, tmp_path):
        ini = tmp_path / "sethlans.ini"
        ini.write_text("[server]\nport = 1\n", encoding="utf-8")  # inherited, not owner-only
        assert not owner_only_acl.verify_owner_only(ini)
        settings_file.set_values("server", {"port": "2"}, ini)
        assert owner_only_acl.verify_owner_only(ini)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
class TestPosixOwnerOnly:

    def test_written_file_mode_is_0600(self, tmp_path):
        ini = tmp_path / "sethlans.ini"
        settings_file.set_values("server", {"port": "8080"}, ini)
        assert stat.S_IMODE(ini.stat().st_mode) == 0o600
        assert owner_only_acl.verify_owner_only(ini)

    def test_rewrite_tightens_loose_file(self, tmp_path):
        ini = tmp_path / "sethlans.ini"
        ini.write_text("[server]\nport = 1\n", encoding="utf-8")
        ini.chmod(0o644)
        settings_file.set_values("server", {"port": "2"}, ini)
        assert stat.S_IMODE(ini.stat().st_mode) == 0o600

    def test_new_settings_folder_is_0700(self, tmp_path):
        ini = tmp_path / "new-settings" / "sethlans.ini"
        settings_file.set_values("server", {"port": "8080"}, ini)
        assert stat.S_IMODE(ini.parent.stat().st_mode) == 0o700
