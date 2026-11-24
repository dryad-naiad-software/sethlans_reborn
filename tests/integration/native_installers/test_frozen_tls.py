# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for TLS setup frozen-mode path resolution.

Verifies that ``tls_setup.py`` reads the settings file and stores
certificates in the manager's identity folder when ``is_frozen()``
returns ``True``, and falls back to the source-tree directory in source
mode.

Patches are applied at the ``sethlans_manager.tls_setup`` module level
because ``tls_setup.py`` uses ``from shared.frozen_paths import ...``,
binding the names locally.
"""

from pathlib import Path

import pytest

import sethlans_manager.tls_setup as tls_mod
from sethlans_manager.tls_setup import (
    get_config_file_path,
    get_tls_dir,
)


def _patch_frozen(monkeypatch, tmp_path, frozen=True):
    """Patch is_frozen and get_identity_dir on the tls_setup module."""
    monkeypatch.setattr(tls_mod, 'is_frozen', lambda: frozen)
    if frozen:
        monkeypatch.setattr(
            tls_mod, 'get_identity_dir',
            lambda component: tmp_path / component,
        )


class TestConfigFilePath:
    """get_config_file_path is the settings file in every mode."""

    @pytest.mark.parametrize('frozen', [True, False])
    def test_config_path_is_settings_path(
        self, tmp_path, monkeypatch, frozen,
    ):
        """The path is ``shared.settings_file.settings_path()``."""
        from shared.settings_file import settings_path
        _patch_frozen(monkeypatch, tmp_path, frozen=frozen)

        result = get_config_file_path()
        assert result == settings_path()
        assert result.name == 'sethlans.ini'


class TestFrozenTlsDir:
    """get_tls_dir resolves to the identity folder when frozen."""

    def test_frozen_tls_dir_in_identity_dir(self, tmp_path, monkeypatch):
        """TLS dir is <identity dir>/tls when frozen."""
        _patch_frozen(monkeypatch, tmp_path, frozen=True)
        monkeypatch.delenv('SETHLANS_TLS_DATA_DIR', raising=False)

        result = get_tls_dir(
            dev_mode=False,
            manager_dir=Path('/fake'),
            project_root=Path('/fake'),
        )
        expected = tmp_path / 'manager' / 'tls'
        assert result == expected

    def test_frozen_tls_dir_ignores_dev_mode(
        self, tmp_path, monkeypatch,
    ):
        """Frozen mode TLS dir is the same regardless of dev_mode."""
        _patch_frozen(monkeypatch, tmp_path, frozen=True)
        monkeypatch.delenv('SETHLANS_TLS_DATA_DIR', raising=False)

        result_dev = get_tls_dir(
            dev_mode=True,
            manager_dir=Path('/fake'),
            project_root=Path('/fake'),
        )
        result_prod = get_tls_dir(
            dev_mode=False,
            manager_dir=Path('/fake'),
            project_root=Path('/fake'),
        )
        assert result_dev == result_prod

    def test_env_override_takes_precedence_over_frozen(
        self, tmp_path, monkeypatch,
    ):
        """SETHLANS_TLS_DATA_DIR env var overrides frozen path."""
        _patch_frozen(monkeypatch, tmp_path, frozen=True)

        custom = tmp_path / 'custom_tls'
        monkeypatch.setenv(
            'SETHLANS_TLS_DATA_DIR', str(custom),
        )

        result = get_tls_dir(
            dev_mode=False,
            manager_dir=Path('/fake'),
            project_root=Path('/fake'),
        )
        assert result == custom


class TestSourceTlsDir:
    """get_tls_dir resolves to source-tree paths in source mode."""

    def test_source_dev_mode_returns_temp_dev_tls(
        self, tmp_path, monkeypatch,
    ):
        """Dev mode TLS dir is project_root/temp/dev-tls."""
        _patch_frozen(monkeypatch, tmp_path, frozen=False)
        monkeypatch.delenv('SETHLANS_TLS_DATA_DIR', raising=False)

        project_root = tmp_path / 'project'
        manager_dir = tmp_path / 'project' / 'manager'
        result = get_tls_dir(
            dev_mode=True,
            manager_dir=manager_dir,
            project_root=project_root,
        )
        assert result == project_root / 'temp' / 'dev-tls'

    def test_source_prod_mode_returns_manager_tls(
        self, tmp_path, monkeypatch,
    ):
        """Prod mode TLS dir is manager_dir/tls."""
        _patch_frozen(monkeypatch, tmp_path, frozen=False)
        monkeypatch.delenv('SETHLANS_TLS_DATA_DIR', raising=False)

        manager_dir = tmp_path / 'project' / 'manager'
        result = get_tls_dir(
            dev_mode=False,
            manager_dir=manager_dir,
            project_root=tmp_path / 'project',
        )
        assert result == manager_dir / 'tls'

    def test_relative_env_override_raises_value_error(
        self, tmp_path, monkeypatch,
    ):
        """Relative path in SETHLANS_TLS_DATA_DIR raises ValueError."""
        _patch_frozen(monkeypatch, tmp_path, frozen=False)
        monkeypatch.setenv(
            'SETHLANS_TLS_DATA_DIR', 'relative/path',
        )

        with pytest.raises(ValueError, match='absolute path'):
            get_tls_dir(
                dev_mode=False,
                manager_dir=tmp_path,
                project_root=tmp_path,
            )
