# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Every Blender network fetch of the worker and the manager (BS-SRC-1).

One ordered list of HTTPS sources (``sources``), the release-list lookups
with their cache (``release_list``, ``release_cache``), the download with
fallback over the sources (``download``) and the platform table
(``platforms``). Standard library, ``requests`` and ``bs4`` only; no Django.
"""
from shared.blender_sources.download import download_build
from shared.blender_sources.errors import (
    AllSourcesFailedError,
    BlenderSourcesConfigError,
    BlenderSourcesError,
    BuildNotPublishedError,
    DownloadStoppedError,
)
from shared.blender_sources.platforms import (
    PLATFORM_EXTENSIONS,
    build_file_name,
    current_platform_id,
    is_platform_id,
    is_version,
    version_key,
)
from shared.blender_sources.release_cache import BuildEntry
from shared.blender_sources.release_list import find_build, latest_patch, list_series
from shared.blender_sources.sources import DEFAULT_SOURCES, SOURCES_ENV, configured_sources

__all__ = [
    "AllSourcesFailedError",
    "BlenderSourcesConfigError",
    "BlenderSourcesError",
    "BuildEntry",
    "BuildNotPublishedError",
    "DEFAULT_SOURCES",
    "DownloadStoppedError",
    "PLATFORM_EXTENSIONS",
    "SOURCES_ENV",
    "build_file_name",
    "configured_sources",
    "current_platform_id",
    "download_build",
    "find_build",
    "is_platform_id",
    "is_version",
    "latest_patch",
    "list_series",
    "version_key",
]
