# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for Django settings frozen-mode path resolution.

Verifies that ``settings.py`` resolves DATABASES, MEDIA_ROOT,
STATIC_ROOT, and _config_file_path to the OS-conventional per-user
data directory when ``is_frozen()`` returns ``True``, and to the
source-tree directory when it returns ``False``.

These tests reload the settings module under simulated frozen mode
(by patching ``sys.frozen`` and ``sys._MEIPASS``) to exercise both
branches without a real PyInstaller bundle.
"""

import importlib
from pathlib import Path


def _reload_settings_with_frozen(monkeypatch, tmp_data_dir, frozen=True):
    """Reload settings.py under simulated frozen or source mode.

    Patches ``shared.frozen_paths`` functions before reimporting so
    that the module-level ``if is_frozen():`` branches pick up the
    patched values.

    Returns the reloaded settings module.
    """
    import shared.frozen_paths as fp_mod

    if frozen:
        monkeypatch.setattr(fp_mod, 'is_frozen', lambda: True)
        monkeypatch.setattr(
            fp_mod, 'get_data_dir',
            lambda component: tmp_data_dir / component,
        )
        monkeypatch.setattr(
            fp_mod, 'get_manager_dir',
            lambda: tmp_data_dir / 'manager',
        )
        monkeypatch.setattr(
            fp_mod, 'get_frontend_dist_dir',
            lambda: tmp_data_dir / 'frontend' / 'dist',
        )
    else:
        monkeypatch.setattr(fp_mod, 'is_frozen', lambda: False)

    # Ensure the data directories exist so mkdir calls don't fail
    (tmp_data_dir / 'manager').mkdir(parents=True, exist_ok=True)
    (tmp_data_dir / 'manager' / 'logs').mkdir(parents=True, exist_ok=True)
    (tmp_data_dir / 'frontend' / 'dist').mkdir(
        parents=True, exist_ok=True,
    )

    # Reload config_loader first — it computes BASE_DIR and
    # _config_file_path from the patched ``shared.frozen_paths`` helpers
    # and is imported by settings.py.  See GitHub issue #103.
    import sethlans_manager.config_loader as cl_mod
    importlib.reload(cl_mod)

    # Reload logging_config next (settings imports it)
    import sethlans_manager.logging_config as lc_mod
    importlib.reload(lc_mod)

    import sethlans_manager.settings as settings_mod
    importlib.reload(settings_mod)
    return settings_mod


class TestFrozenModeSettingsPaths:
    """Settings resolve paths to the data directory in frozen mode."""

    def test_database_name_points_to_data_dir(self, tmp_path, monkeypatch):
        """DATABASES['default']['NAME'] is under the data dir."""
        monkeypatch.delenv('SETHLANS_DB_NAME', raising=False)
        settings = _reload_settings_with_frozen(
            monkeypatch, tmp_path, frozen=True,
        )
        db_name = Path(str(settings.DATABASES['default']['NAME']))
        assert str(tmp_path / 'manager') in str(db_name)
        assert db_name.name == 'db.sqlite3'

    def test_media_root_points_to_data_dir(self, tmp_path, monkeypatch):
        """MEDIA_ROOT is under the data dir."""
        monkeypatch.delenv('SETHLANS_MEDIA_ROOT', raising=False)
        settings = _reload_settings_with_frozen(
            monkeypatch, tmp_path, frozen=True,
        )
        assert str(tmp_path / 'manager') in str(settings.MEDIA_ROOT)

    def test_static_root_points_to_data_dir(self, tmp_path, monkeypatch):
        """STATIC_ROOT is under the data dir."""
        settings = _reload_settings_with_frozen(
            monkeypatch, tmp_path, frozen=True,
        )
        assert str(tmp_path / 'manager') in str(settings.STATIC_ROOT)

    def test_config_file_path_points_to_data_dir(
        self, tmp_path, monkeypatch,
    ):
        """_config_file_path (manager.ini) is under the data dir."""
        settings = _reload_settings_with_frozen(
            monkeypatch, tmp_path, frozen=True,
        )
        cfg_path = settings._config_file_path
        assert str(tmp_path / 'manager') in str(cfg_path)
        assert cfg_path.name == 'manager.ini'

    def test_base_dir_points_to_manager_dir(
        self, tmp_path, monkeypatch,
    ):
        """BASE_DIR is set to get_manager_dir() in frozen mode."""
        settings = _reload_settings_with_frozen(
            monkeypatch, tmp_path, frozen=True,
        )
        assert settings.BASE_DIR == tmp_path / 'manager'


class TestSourceModeSettingsPaths:
    """Settings resolve paths to the source tree in source mode."""

    def test_database_uses_base_dir(self, tmp_path, monkeypatch):
        """DATABASES['default']['NAME'] is under BASE_DIR in source mode."""
        monkeypatch.delenv('SETHLANS_DB_NAME', raising=False)
        settings = _reload_settings_with_frozen(
            monkeypatch, tmp_path, frozen=False,
        )
        db_name = Path(str(settings.DATABASES['default']['NAME']))
        # In source mode, BASE_DIR is settings.py's parent.parent
        assert db_name.name == 'db.sqlite3'
        # DB should NOT be in tmp_path (the fake data dir)
        assert str(tmp_path) not in str(db_name)

    def test_media_root_uses_base_dir(self, tmp_path, monkeypatch):
        """MEDIA_ROOT is under BASE_DIR in source mode."""
        monkeypatch.delenv('SETHLANS_MEDIA_ROOT', raising=False)
        settings = _reload_settings_with_frozen(
            monkeypatch, tmp_path, frozen=False,
        )
        # Should not contain our tmp_path
        assert str(tmp_path) not in str(settings.MEDIA_ROOT)

    def test_static_root_uses_base_dir(self, tmp_path, monkeypatch):
        """STATIC_ROOT is under BASE_DIR in source mode."""
        settings = _reload_settings_with_frozen(
            monkeypatch, tmp_path, frozen=False,
        )
        assert str(tmp_path) not in str(settings.STATIC_ROOT)

    def test_config_file_path_uses_base_dir(
        self, tmp_path, monkeypatch,
    ):
        """_config_file_path is BASE_DIR / 'manager.ini' in source mode."""
        settings = _reload_settings_with_frozen(
            monkeypatch, tmp_path, frozen=False,
        )
        cfg_path = settings._config_file_path
        assert cfg_path.name == 'manager.ini'
        # Should not contain our tmp_path
        assert str(tmp_path) not in str(cfg_path)


class TestFrozenModeDoesNotBreakSourceMode:
    """Toggling frozen mode back to source does not break settings."""

    def test_round_trip_frozen_then_source(self, tmp_path, monkeypatch):
        """Load frozen, then reload source -- settings are consistent."""
        frozen_settings = _reload_settings_with_frozen(
            monkeypatch, tmp_path, frozen=True,
        )
        frozen_db = str(frozen_settings.DATABASES['default']['NAME'])

        source_settings = _reload_settings_with_frozen(
            monkeypatch, tmp_path, frozen=False,
        )
        source_db = str(source_settings.DATABASES['default']['NAME'])

        # They should be different paths
        assert frozen_db != source_db


class TestEnvVarOverridesInFrozenMode:
    """Env vars still take precedence in frozen mode."""

    def test_db_name_env_overrides_frozen_path(
        self, tmp_path, monkeypatch,
    ):
        """SETHLANS_DB_NAME env var overrides the frozen default."""
        custom_db = str(tmp_path / 'custom' / 'my.db')
        monkeypatch.setenv('SETHLANS_DB_NAME', custom_db)
        settings = _reload_settings_with_frozen(
            monkeypatch, tmp_path, frozen=True,
        )
        assert str(settings.DATABASES['default']['NAME']) == custom_db

    def test_media_root_env_overrides_frozen_path(
        self, tmp_path, monkeypatch,
    ):
        """SETHLANS_MEDIA_ROOT env var overrides the frozen default."""
        custom_media = str(tmp_path / 'custom' / 'media')
        monkeypatch.setenv('SETHLANS_MEDIA_ROOT', custom_media)
        settings = _reload_settings_with_frozen(
            monkeypatch, tmp_path, frozen=True,
        )
        assert str(settings.MEDIA_ROOT) == custom_media
