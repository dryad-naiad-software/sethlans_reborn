# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for TLS setup frozen-mode path resolution.

Verifies that ``tls_setup.py`` reads config and stores certificates
in the per-user data directory when ``is_frozen()`` returns ``True``,
and falls back to the source-tree directory in source mode.

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
    """Patch is_frozen and get_data_dir on the tls_setup module."""
    monkeypatch.setattr(tls_mod, 'is_frozen', lambda: frozen)
    if frozen:
        monkeypatch.setattr(
            tls_mod, 'get_data_dir',
            lambda component: tmp_path / component,
        )


class TestFrozenConfigFilePath:
    """get_config_file_path resolves to data dir when frozen."""

    def test_frozen_config_path_in_data_dir(
        self, tmp_path, monkeypatch,
    ):
        """Config file path is data_dir/manager/manager.ini when frozen."""
        _patch_frozen(monkeypatch, tmp_path, frozen=True)

        result = get_config_file_path(Path('/fake/manager'))
        expected = tmp_path / 'manager' / 'manager.ini'
        assert result == expected

    def test_source_config_path_in_manager_dir(
        self, tmp_path, monkeypatch,
    ):
        """Config file path is manager_dir/manager.ini in source mode."""
        _patch_frozen(monkeypatch, tmp_path, frozen=False)

        manager_dir = tmp_path / 'manager'
        manager_dir.mkdir()
        result = get_config_file_path(manager_dir)
        assert result == manager_dir / 'manager.ini'

    def test_frozen_ignores_manager_dir_argument(
        self, tmp_path, monkeypatch,
    ):
        """In frozen mode, the manager_dir argument is not used."""
        _patch_frozen(monkeypatch, tmp_path, frozen=True)

        # Pass a completely different manager_dir
        result = get_config_file_path(Path('/completely/different'))
        # Result should still use data dir, not the argument
        assert str(tmp_path) in str(result)
        assert '/completely/different' not in str(result)


class TestFrozenTlsDir:
    """get_tls_dir resolves to data dir when frozen."""

    def test_frozen_tls_dir_in_data_dir(self, tmp_path, monkeypatch):
        """TLS dir is data_dir/manager/tls when frozen."""
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
