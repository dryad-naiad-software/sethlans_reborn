# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for the asset_manager module.

Tests asset path resolution, local cache hits, and path traversal
protection.
"""
from sethlans_worker_agent import asset_manager


class TestEnsureAssetIsAvailable:

    def test_returns_none_for_none_data(self):
        assert asset_manager.ensure_asset_is_available(None) is None

    def test_returns_none_for_empty_dict(self):
        assert asset_manager.ensure_asset_is_available({}) is None

    def test_returns_none_for_missing_blend_file_key(self):
        assert asset_manager.ensure_asset_is_available(
            {'other': 'data'}
        ) is None

    def test_returns_none_for_empty_blend_file_url(self):
        assert asset_manager.ensure_asset_is_available(
            {'blend_file': ''}
        ) is None

    def test_returns_cached_path(self, mocker, tmp_path):
        managed_dir = tmp_path / 'managed_assets'
        managed_dir.mkdir()
        mocker.patch(
            'sethlans_worker_agent.config.MANAGED_ASSETS_DIR',
            str(managed_dir)
        )

        # Create the cached file
        cached = managed_dir / 'media' / 'assets' / 'scene.blend'
        cached.parent.mkdir(parents=True)
        cached.write_bytes(b'BLEND')

        result = asset_manager.ensure_asset_is_available({
            'blend_file': 'http://localhost/media/assets/scene.blend'
        })
        assert result == str(cached)

    def test_downloads_when_not_cached(self, mocker, tmp_path):
        managed_dir = tmp_path / 'managed_assets'
        managed_dir.mkdir()
        mocker.patch(
            'sethlans_worker_agent.config.MANAGED_ASSETS_DIR',
            str(managed_dir)
        )

        expected_path = (
            managed_dir / 'media' / 'assets' / 'scene.blend'
        )

        mocker.patch(
            'sethlans_worker_agent.utils.file_operations.download_file',
            return_value=str(expected_path)
        )

        result = asset_manager.ensure_asset_is_available({
            'blend_file': 'http://localhost/media/assets/scene.blend'
        })
        assert result == str(expected_path)

    def test_rejects_path_traversal(self, mocker, tmp_path):
        managed_dir = tmp_path / 'managed_assets'
        managed_dir.mkdir()
        mocker.patch(
            'sethlans_worker_agent.config.MANAGED_ASSETS_DIR',
            str(managed_dir)
        )

        result = asset_manager.ensure_asset_is_available({
            'blend_file': 'http://localhost/../../etc/passwd'
        })
        assert result is None

    def test_download_failure_returns_none(self, mocker, tmp_path):
        import requests
        managed_dir = tmp_path / 'managed_assets'
        managed_dir.mkdir()
        mocker.patch(
            'sethlans_worker_agent.config.MANAGED_ASSETS_DIR',
            str(managed_dir)
        )
        mocker.patch(
            'sethlans_worker_agent.utils.file_operations.download_file',
            side_effect=requests.exceptions.ConnectionError("fail")
        )

        result = asset_manager.ensure_asset_is_available({
            'blend_file': 'http://localhost/media/assets/scene.blend'
        })
        assert result is None

    def test_download_path_mismatch_returns_none(self, mocker, tmp_path):
        managed_dir = tmp_path / 'managed_assets'
        managed_dir.mkdir()
        mocker.patch(
            'sethlans_worker_agent.config.MANAGED_ASSETS_DIR',
            str(managed_dir)
        )
        mocker.patch(
            'sethlans_worker_agent.utils.file_operations.download_file',
            return_value=str(tmp_path / 'wrong_name.blend')
        )

        result = asset_manager.ensure_asset_is_available({
            'blend_file': 'http://localhost/media/assets/scene.blend'
        })
        assert result is None
