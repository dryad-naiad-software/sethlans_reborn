# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for logging_config.py frozen-mode LOGS_DIR resolution.

Verifies that ``LOGS_DIR`` resolves to the per-user data directory in
frozen mode, and to the source-tree ``logs/`` directory in source mode.
"""

import importlib


def _reload_logging_config(monkeypatch, tmp_data_dir, frozen=True):
    """Reload logging_config.py under simulated frozen or source mode.

    Returns the reloaded logging_config module.
    """
    import shared.frozen_paths as fp_mod

    if frozen:
        monkeypatch.setattr(fp_mod, 'is_frozen', lambda: True)
        monkeypatch.setattr(
            fp_mod, 'get_data_dir',
            lambda component: tmp_data_dir / component,
        )
    else:
        monkeypatch.setattr(fp_mod, 'is_frozen', lambda: False)

    # Ensure data dir exists so mkdir() calls succeed
    (tmp_data_dir / 'manager' / 'logs').mkdir(parents=True, exist_ok=True)

    import sethlans_manager.logging_config as lc_mod
    importlib.reload(lc_mod)
    return lc_mod


class TestFrozenLogsDir:
    """LOGS_DIR resolves to the data directory in frozen mode."""

    def test_logs_dir_in_data_dir_frozen(self, tmp_path, monkeypatch):
        """LOGS_DIR is under get_data_dir('manager') when frozen."""
        lc = _reload_logging_config(
            monkeypatch, tmp_path, frozen=True,
        )
        expected = tmp_path / 'manager' / 'logs'
        assert lc.LOGS_DIR == expected

    def test_log_file_handler_uses_data_dir(self, tmp_path, monkeypatch):
        """The file handler filename is inside the data-dir LOGS_DIR."""
        lc = _reload_logging_config(
            monkeypatch, tmp_path, frozen=True,
        )
        handler_filename = lc.LOGGING['handlers']['file']['filename']
        assert str(tmp_path / 'manager' / 'logs') in str(handler_filename)

    def test_logs_dir_created_frozen(self, tmp_path, monkeypatch):
        """LOGS_DIR is created if it doesn't exist (frozen mode)."""
        logs_dir = tmp_path / 'manager' / 'logs'
        # Remove if exists from fixture setup
        if logs_dir.exists():
            logs_dir.rmdir()
        (tmp_path / 'manager').mkdir(parents=True, exist_ok=True)

        lc = _reload_logging_config(
            monkeypatch, tmp_path, frozen=True,
        )
        assert lc.LOGS_DIR.exists()


class TestSourceLogsDir:
    """LOGS_DIR resolves to the source tree in source mode."""

    def test_logs_dir_uses_base_dir_source(self, tmp_path, monkeypatch):
        """LOGS_DIR is BASE_DIR / 'logs' when not frozen."""
        lc = _reload_logging_config(
            monkeypatch, tmp_path, frozen=False,
        )
        # In source mode, LOGS_DIR should NOT be under tmp_path
        assert str(tmp_path) not in str(lc.LOGS_DIR)
        assert lc.LOGS_DIR.name == 'logs'

    def test_log_file_handler_uses_source_dir(
        self, tmp_path, monkeypatch,
    ):
        """The file handler uses the source-tree logs directory."""
        lc = _reload_logging_config(
            monkeypatch, tmp_path, frozen=False,
        )
        handler_filename = lc.LOGGING['handlers']['file']['filename']
        assert str(tmp_path) not in str(handler_filename)


class TestLoggingConfigStructure:
    """Verify the LOGGING dict structure is valid regardless of mode."""

    def test_logging_dict_has_required_keys(self, tmp_path, monkeypatch):
        """LOGGING dict contains version, handlers, loggers."""
        lc = _reload_logging_config(
            monkeypatch, tmp_path, frozen=True,
        )
        logging_dict = lc.LOGGING
        assert logging_dict['version'] == 1
        assert 'handlers' in logging_dict
        assert 'loggers' in logging_dict
        assert 'formatters' in logging_dict

    def test_console_handler_present(self, tmp_path, monkeypatch):
        """Console handler is defined in LOGGING."""
        lc = _reload_logging_config(
            monkeypatch, tmp_path, frozen=True,
        )
        assert 'console' in lc.LOGGING['handlers']

    def test_file_handler_present(self, tmp_path, monkeypatch):
        """File handler is defined with RotatingFileHandler."""
        lc = _reload_logging_config(
            monkeypatch, tmp_path, frozen=True,
        )
        file_handler = lc.LOGGING['handlers']['file']
        assert 'RotatingFileHandler' in file_handler['class']

    def test_file_handler_has_max_bytes(self, tmp_path, monkeypatch):
        """File handler has maxBytes configured."""
        lc = _reload_logging_config(
            monkeypatch, tmp_path, frozen=True,
        )
        file_handler = lc.LOGGING['handlers']['file']
        assert file_handler['maxBytes'] > 0

    def test_file_handler_has_backup_count(self, tmp_path, monkeypatch):
        """File handler has backupCount configured."""
        lc = _reload_logging_config(
            monkeypatch, tmp_path, frozen=True,
        )
        file_handler = lc.LOGGING['handlers']['file']
        assert file_handler['backupCount'] > 0
