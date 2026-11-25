# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``leftovers`` (``pending_setup.json``, earlier files, ``last_failure``) and the setup log config."""
import logging

import pytest

from sethlans_manager.setup_mode import leftovers, logging_setup
from shared import owner_only_acl


class TestPendingSetup:

    def test_deleted_and_logged(self, settings_dir, caplog):
        settings_dir.mkdir()
        pending = settings_dir / "pending_setup.json"
        pending.write_text('{"password_plaintext": "pw-left-behind"}')
        caplog.set_level(logging.INFO, logger=leftovers.__name__)
        assert leftovers.remove_pending_setup() is True
        assert not pending.exists()
        record = [r for r in caplog.records if r.name == leftovers.__name__][-1]
        assert record.levelno == logging.INFO
        assert str(pending) in record.getMessage()
        assert "pw-left-behind" not in caplog.text

    def test_none_there(self, settings_dir):
        assert leftovers.remove_pending_setup() is False

    def test_explicit_folder(self, tmp_path):
        (tmp_path / "pending_setup.json").write_text("{}")
        assert leftovers.remove_pending_setup(tmp_path) is True

    def test_cannot_delete_is_a_warning(self, tmp_path, caplog):
        (tmp_path / "pending_setup.json").mkdir()
        caplog.set_level(logging.INFO, logger=leftovers.__name__)
        assert leftovers.remove_pending_setup(tmp_path) is False
        assert [r.levelno for r in caplog.records if r.name == leftovers.__name__] == [logging.WARNING]

    def test_other_leftovers_are_not_deleted(self, tmp_path):
        (tmp_path / "topology.json").write_text("{}")
        leftovers.remove_pending_setup(tmp_path)
        assert (tmp_path / "topology.json").exists()


class TestLeftoverPaths:

    def test_none(self, tmp_path):
        assert leftovers.leftover_paths(tmp_path) == []

    def test_each_earlier_file_is_listed(self, tmp_path):
        (tmp_path / "manager").mkdir()
        (tmp_path / "manager" / "manager.ini").write_text("")
        (tmp_path / "topology.json").write_text("")
        (tmp_path / ".setup_complete").write_text("")
        (tmp_path / "wizard").mkdir()
        (tmp_path / "worker").mkdir()
        (tmp_path / "worker" / ".setup_complete").write_text("")
        legacy = tmp_path / "install-worker"
        legacy.mkdir()
        (legacy / "config.ini").write_text("")
        assert leftovers.leftover_paths(tmp_path) == [
            str(tmp_path / "manager" / "manager.ini"), str(tmp_path / "topology.json"),
            str(tmp_path / ".setup_complete"), str(tmp_path / "wizard"),
            str(tmp_path / "worker" / ".setup_complete"), str(legacy / "config.ini"),
        ]


class TestLastFailure:

    @pytest.fixture
    def failure_file(self, settings_dir):
        (settings_dir / "setup").mkdir(parents=True)
        return settings_dir / "setup" / "last_failure"

    def test_missing(self, settings_dir):
        assert leftovers.last_failure() is None

    @pytest.mark.parametrize("data", [b"", b"  \n"])
    def test_empty_is_none(self, failure_file, data):
        failure_file.write_bytes(data)
        assert leftovers.last_failure() is None

    def test_text_is_stripped(self, failure_file):
        failure_file.write_bytes(b"\n The database refused the login. \n")
        assert leftovers.last_failure() == "The database refused the login."

    def test_capped_at_4096_bytes(self, failure_file):
        failure_file.write_bytes(b"a" * 5000)
        assert leftovers.last_failure() == "a" * 4096

    def test_bad_utf8_is_replaced(self, failure_file):
        failure_file.write_bytes(b"bad \xff byte")
        assert leftovers.last_failure() == "bad � byte"


class TestLoggingConfig:

    def test_log_file_in_the_settings_folder(self, settings_dir):
        config = logging_setup.build_logging()
        assert config["handlers"]["file"]["filename"] == str(settings_dir / "logs" / "setup-mode.log")
        handler_class = "sethlans_manager.setup_mode.logging_setup.OwnerOnlyRotatingFileHandler"
        assert config["handlers"]["file"]["class"] == handler_class

    def test_folders_are_created_owner_only(self, settings_dir):
        logging_setup.build_logging()
        assert owner_only_acl.verify_owner_only(settings_dir)
        assert owner_only_acl.verify_owner_only(settings_dir / "logs")

    def test_database_drivers_are_quiet(self, settings_dir):
        loggers = logging_setup.build_logging()["loggers"]
        for name in ("django.db.backends", "pymysql", "psycopg"):
            assert loggers[name]["level"] == "WARNING"
            assert loggers[name]["propagate"] is False
        assert loggers[""]["level"] == "INFO"

    def test_no_request_logger_is_configured(self, settings_dir):
        loggers = logging_setup.build_logging()["loggers"]
        assert not {"django.request", "django.server", "waitress"} & set(loggers)

    def test_settings_folder_owned_by_another_account_raises(self, settings_dir, mocker):
        settings_dir.mkdir()
        mocker.patch.object(owner_only_acl, "is_owned_by_current_user", return_value=False)
        with pytest.raises(PermissionError):
            logging_setup.build_logging()


class TestOwnerOnlyLogFile:

    def test_handler_creates_the_file_owner_only(self, tmp_path):
        path = tmp_path / "setup-mode.log"
        handler = logging_setup.OwnerOnlyRotatingFileHandler(str(path), maxBytes=64, backupCount=1,
                                                             encoding="utf-8")
        try:
            handler.emit(logging.makeLogRecord({"msg": "first line"}))
        finally:
            handler.close()
        assert path.is_file()
        assert owner_only_acl.verify_owner_only(path)

    def test_rollover_keeps_both_files_owner_only(self, tmp_path):
        path = tmp_path / "setup-mode.log"
        handler = logging_setup.OwnerOnlyRotatingFileHandler(str(path), maxBytes=32, backupCount=1,
                                                             encoding="utf-8")
        try:
            for index in range(4):
                handler.emit(logging.makeLogRecord({"msg": f"line {index} " + "x" * 20}))
        finally:
            handler.close()
        rotated = tmp_path / "setup-mode.log.1"
        assert rotated.is_file()
        assert owner_only_acl.verify_owner_only(path) and owner_only_acl.verify_owner_only(rotated)

    def test_existing_loose_file_is_tightened(self, tmp_path):
        path = tmp_path / "setup-mode.log"
        path.write_bytes(b"old line")
        if not owner_only_acl._is_windows():
            path.chmod(0o644)
        logging_setup.ensure_owner_only_file(path)
        assert owner_only_acl.verify_owner_only(path)
        assert path.read_bytes() == b"old line"

    def test_file_of_another_account_is_refused(self, tmp_path, mocker):
        path = tmp_path / "setup-mode.log"
        path.write_text("")
        mocker.patch.object(owner_only_acl, "is_owned_by_current_user", return_value=False)
        with pytest.raises(PermissionError, match="not owned by the current user"):
            logging_setup.ensure_owner_only_file(path)
