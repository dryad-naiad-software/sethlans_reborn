# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
#
# Created by Mario Estrella on 07/22/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# tests/unit/worker_agent/test_asset_manager.py
"""
Unit tests for the asset_manager module, focusing on path traversal
protection (GitHub issue #3).
"""

import pytest
from pathlib import Path
from sethlans_worker_agent import asset_manager, config


@pytest.fixture(autouse=True)
def mock_config_dir(mocker, tmp_path):
    """Pin MANAGED_ASSETS_DIR to a temp directory for every test."""
    mocker.patch.object(config, 'MANAGED_ASSETS_DIR', str(tmp_path / "managed_assets"))
    return tmp_path / "managed_assets"


# ------------------------------------------------------------------ #
# Happy-path tests
# ------------------------------------------------------------------ #

class TestEnsureAssetHappyPath:
    """Verify normal operation with well-formed inputs."""

    def test_returns_cached_path_when_file_exists(self, mocker, mock_config_dir):
        """If the asset is already downloaded, return its path immediately."""
        # Create the cached file on disk
        cached = mock_config_dir / "media" / "assets" / "scene.blend"
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text("blend data")

        asset_data = {
            "blend_file": "http://manager:7075/media/assets/scene.blend"
        }

        result = asset_manager.ensure_asset_is_available(asset_data)

        assert result == str(cached)

    def test_downloads_file_when_not_cached(self, mocker, mock_config_dir):
        """When the file is missing locally, download it and return the path."""
        expected_path = mock_config_dir / "media" / "assets" / "scene.blend"
        mock_download = mocker.patch(
            "sethlans_worker_agent.utils.file_operations.download_file",
            return_value=str(expected_path),
        )

        asset_data = {
            "blend_file": "http://manager:7075/media/assets/scene.blend"
        }
        result = asset_manager.ensure_asset_is_available(asset_data)

        assert result == str(expected_path)
        mock_download.assert_called_once()

    def test_returns_none_when_asset_data_is_none(self):
        """None asset_data should return None."""
        assert asset_manager.ensure_asset_is_available(None) is None

    def test_returns_none_when_blend_file_key_missing(self):
        """Missing blend_file key should return None."""
        assert asset_manager.ensure_asset_is_available({}) is None

    def test_returns_none_when_blend_file_is_empty_string(self):
        """Empty blend_file URL should return None."""
        assert asset_manager.ensure_asset_is_available({"blend_file": ""}) is None


# ------------------------------------------------------------------ #
# Path traversal attack tests (GitHub issue #3)
# ------------------------------------------------------------------ #

class TestPathTraversalProtection:
    """Verify that malicious URLs cannot escape the managed assets dir."""

    @pytest.mark.parametrize("malicious_url", [
        "http://manager:7075/media/../../etc/passwd",
        "http://manager:7075/media/assets/../../../../../../etc/shadow",
        "http://manager:7075/media/assets/../../../secret.txt",
        "http://manager:7075/../../../windows/system32/config/sam",
    ])
    def test_rejects_dot_dot_traversal(self, malicious_url, mocker):
        """URLs with .. components that escape base dir must return None."""
        mock_download = mocker.patch(
            "sethlans_worker_agent.utils.file_operations.download_file"
        )
        asset_data = {"blend_file": malicious_url}

        result = asset_manager.ensure_asset_is_available(asset_data)

        assert result is None
        mock_download.assert_not_called()

    def test_encoded_traversal_stays_contained(self, mocker, mock_config_dir):
        """URL-encoded %2F is not decoded by urlparse, so the literal
        '..%2F' becomes a safe directory name that stays inside the base.
        The result is either None (download mismatch) or a contained path."""
        # urlparse keeps %2F encoded, so the relative_path is literally
        # "media/..%2F..%2Fetc%2Fpasswd" — no actual traversal occurs.
        expected = (
            mock_config_dir / "media" / "..%2F..%2Fetc%2Fpasswd"
        )
        mocker.patch(
            "sethlans_worker_agent.utils.file_operations.download_file",
            return_value=str(expected),
        )
        asset_data = {
            "blend_file": "http://manager:7075/media/..%2F..%2Fetc%2Fpasswd"
        }

        result = asset_manager.ensure_asset_is_available(asset_data)

        if result is not None:
            resolved = Path(result).resolve()
            base = Path(config.MANAGED_ASSETS_DIR).resolve()
            assert resolved.is_relative_to(base)
        # Either way, no escape from the base directory

    def test_safe_relative_path_is_allowed(self, mocker, mock_config_dir):
        """A deep but contained path should work fine."""
        expected = mock_config_dir / "media" / "assets" / "2025" / "07" / "scene.blend"
        mocker.patch(
            "sethlans_worker_agent.utils.file_operations.download_file",
            return_value=str(expected),
        )
        asset_data = {
            "blend_file": "http://manager:7075/media/assets/2025/07/scene.blend"
        }

        result = asset_manager.ensure_asset_is_available(asset_data)

        assert result == str(expected)


# ------------------------------------------------------------------ #
# Edge cases
# ------------------------------------------------------------------ #

class TestAssetManagerEdgeCases:
    """Boundary and edge-case scenarios."""

    def test_download_failure_returns_none(self, mocker):
        """Network failure during download should return None gracefully."""
        import requests
        mocker.patch(
            "sethlans_worker_agent.utils.file_operations.download_file",
            side_effect=requests.exceptions.ConnectionError("refused"),
        )
        asset_data = {
            "blend_file": "http://manager:7075/media/assets/scene.blend"
        }

        result = asset_manager.ensure_asset_is_available(asset_data)

        assert result is None

    def test_download_path_mismatch_returns_none(self, mocker, mock_config_dir):
        """If download_file returns a different path, should return None."""
        wrong_path = str(mock_config_dir / "wrong" / "path.blend")

        # Mock Path.exists to return False (asset not cached) so we
        # enter the download branch
        original_exists = Path.exists
        call_count = 0

        def patched_exists(self):
            nonlocal call_count
            call_count += 1
            # First call is the cache check — return False to force download
            if call_count == 1:
                return False
            return original_exists(self)

        mocker.patch.object(Path, 'exists', patched_exists)

        mocker.patch(
            "sethlans_worker_agent.utils.file_operations.download_file",
            return_value=wrong_path,
        )
        mocker.patch(
            "sethlans_worker_agent.asset_manager.os.path.exists",
            return_value=True,
        )
        mocker.patch("sethlans_worker_agent.asset_manager.os.remove")

        asset_data = {
            "blend_file": "http://manager:7075/media/assets/mismatch.blend"
        }

        result = asset_manager.ensure_asset_is_available(asset_data)

        assert result is None

    def test_unexpected_exception_returns_none(self, mocker):
        """Unexpected errors should be caught and return None."""
        mocker.patch(
            "sethlans_worker_agent.utils.file_operations.download_file",
            side_effect=RuntimeError("disk full"),
        )
        asset_data = {
            "blend_file": "http://manager:7075/media/assets/scene.blend"
        }

        result = asset_manager.ensure_asset_is_available(asset_data)

        assert result is None
