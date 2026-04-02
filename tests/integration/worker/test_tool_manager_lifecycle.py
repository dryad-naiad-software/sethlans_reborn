# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for ToolManager lifecycle operations.

Exercises scan discovery with real filesystem structures, cache
TTL behavior, download exclusion filtering, cache invalidation,
and reference counting for safe version removal.
"""

import time


# -- Scan finds installed Blender versions --

def test_scan_finds_installed_versions(tool_manager, mocker):
    """Scan discovers versions from real directory structure."""
    blender_dir = tool_manager.blender_dir

    # Create fake Blender installation directories
    dir_411 = blender_dir / "blender-4.1.1-windows-x64"
    dir_411.mkdir()
    exe_411 = dir_411 / "blender.exe"
    exe_411.write_text("fake")

    dir_420 = blender_dir / "blender-4.2.0-windows-x64"
    dir_420.mkdir()
    exe_420 = dir_420 / "blender.exe"
    exe_420.write_text("fake")

    # Also create a non-blender directory that should be ignored
    (blender_dir / "some-other-folder").mkdir()

    mocker.patch.object(
        tool_manager, '_get_executable_path_for_install',
        side_effect=lambda name: str(blender_dir / name / "blender.exe"),
    )

    result = tool_manager.scan_for_local_blenders()
    versions = sorted([b['version'] for b in result])

    assert '4.1.1' in versions
    assert '4.2.0' in versions


def test_scan_ignores_invalid_directories(tool_manager, mocker):
    """Scan skips directories that don't match blender naming pattern."""
    blender_dir = tool_manager.blender_dir

    # Create directories that don't match the blender-X.Y.Z-platform pattern
    (blender_dir / "not-a-blender").mkdir()
    (blender_dir / "blender-invalid").mkdir()
    (blender_dir / "blender-4.1.1").mkdir()  # missing platform

    result = tool_manager.scan_for_local_blenders()
    assert result == []


# -- Scan cache returns cached results within TTL --

def test_scan_cache_returns_cached_within_ttl(tool_manager):
    """Cached scan results are returned without filesystem access."""
    tool_manager._scan_cache = [
        {'version': '4.1.1', 'platform': 'windows-x64'},
    ]
    tool_manager._scan_cache_time = time.time()

    # Remove blender_dir contents so a real scan would return empty
    import shutil
    shutil.rmtree(tool_manager.blender_dir)

    result = tool_manager.scan_for_local_blenders()
    assert len(result) == 1
    assert result[0]['version'] == '4.1.1'


def test_scan_cache_refreshes_after_ttl(tool_manager):
    """Scan refreshes from filesystem after TTL expires."""
    tool_manager._scan_cache = [
        {'version': '4.1.1', 'platform': 'windows-x64'},
    ]
    tool_manager._scan_cache_time = time.time() - 60  # Expired

    # No real dirs, so fresh scan returns empty
    result = tool_manager.scan_for_local_blenders()
    assert result == []


# -- Scan excludes versions in _downloading_versions set --

def test_scan_excludes_downloading_versions(tool_manager):
    """Versions being downloaded are filtered out of scan results."""
    tool_manager._scan_cache = [
        {'version': '4.1.1', 'platform': 'windows-x64'},
        {'version': '4.2.0', 'platform': 'windows-x64'},
        {'version': '4.3.0', 'platform': 'linux-x64'},
    ]
    tool_manager._scan_cache_time = time.time()
    tool_manager._downloading_versions.add('4.2.0')

    result = tool_manager.scan_for_local_blenders()
    versions = [b['version'] for b in result]

    assert '4.1.1' in versions
    assert '4.3.0' in versions
    assert '4.2.0' not in versions


# -- Cache invalidated after download --

def test_cache_invalidated_after_download(tool_manager, mocker):
    """ensure_blender_version_available invalidates scan cache."""
    tool_manager._scan_cache = [
        {'version': '4.1.1', 'platform': 'windows-x64'},
    ]

    mocker.patch.object(
        tool_manager, '_resolve_version', return_value='4.2.0',
    )
    mocker.patch.object(
        tool_manager, 'get_blender_executable_path',
        return_value=None,
    )
    mocker.patch.object(
        tool_manager, '_download_and_install',
        return_value='/fake/blender',
    )

    tool_manager.ensure_blender_version_available('4.2')

    # Cache should be cleared (set to None before download)
    assert tool_manager._scan_cache is None


# -- Cache invalidated after remove --

def test_cache_invalidated_after_remove(tool_manager, mocker):
    """remove_blender_version invalidates scan cache on success."""
    mocker.patch.object(
        tool_manager, '_get_platform_identifier',
        return_value='windows-x64',
    )

    install_dir = tool_manager.blender_dir / "blender-4.1.1-windows-x64"
    install_dir.mkdir(parents=True)
    (install_dir / "blender.exe").write_text("fake")

    tool_manager._scan_cache = [{'version': '4.1.1'}]
    tool_manager._scan_cache_time = time.time()

    result = tool_manager.remove_blender_version('4.1.1')

    assert result is True
    assert not install_dir.exists()
    assert tool_manager._scan_cache is None


# -- Reference counting --

def test_acquire_increments_release_decrements(tool_manager):
    """Reference count increments on acquire, decrements on release."""
    assert tool_manager.is_version_in_use('4.1.1') is False

    tool_manager.acquire_version('4.1.1')
    assert tool_manager.is_version_in_use('4.1.1') is True

    tool_manager.release_version('4.1.1')
    assert tool_manager.is_version_in_use('4.1.1') is False


def test_multiple_acquires_need_matching_releases(tool_manager):
    """Multiple acquires require matching number of releases."""
    tool_manager.acquire_version('4.1.1')
    tool_manager.acquire_version('4.1.1')
    tool_manager.acquire_version('4.1.1')

    tool_manager.release_version('4.1.1')
    assert tool_manager.is_version_in_use('4.1.1') is True

    tool_manager.release_version('4.1.1')
    assert tool_manager.is_version_in_use('4.1.1') is True

    tool_manager.release_version('4.1.1')
    assert tool_manager.is_version_in_use('4.1.1') is False


def test_in_use_version_cannot_be_removed(tool_manager, mocker):
    """Version with active reference count cannot be removed."""
    mocker.patch.object(
        tool_manager, '_get_platform_identifier',
        return_value='windows-x64',
    )

    install_dir = tool_manager.blender_dir / "blender-4.1.1-windows-x64"
    install_dir.mkdir(parents=True)
    (install_dir / "blender.exe").write_text("fake")

    tool_manager.acquire_version('4.1.1')
    assert tool_manager.remove_blender_version('4.1.1') is False
    assert install_dir.exists()

    tool_manager.release_version('4.1.1')
    assert tool_manager.remove_blender_version('4.1.1') is True
    assert not install_dir.exists()


def test_release_without_acquire_is_safe(tool_manager):
    """Releasing a version that was never acquired does not error."""
    tool_manager.release_version('4.1.1')
    assert tool_manager.is_version_in_use('4.1.1') is False
