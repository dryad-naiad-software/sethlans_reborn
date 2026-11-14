# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Byte and time limits of the project-archive pipeline, from
``config_unreal`` (Unreal foundation FR-WC-2). Tests build their own.
"""
from dataclasses import dataclass

from sethlans_worker_agent.project_archive.cache import CacheLimits

GIB = 1024 ** 3


@dataclass(frozen=True)
class PipelineLimits:
    max_archive_bytes: int
    max_uncompressed_bytes: int
    cache_max_bytes: int
    min_free_bytes: int
    copy_stall_seconds: float
    workcopy_mode: str = 'hardlink'

    @property
    def cache(self) -> CacheLimits:
        return CacheLimits(cache_max_bytes=self.cache_max_bytes, min_free_bytes=self.min_free_bytes)


def from_config() -> PipelineLimits:
    """The limits configured in ``[unreal]`` (read at call time)."""
    from sethlans_worker_agent import config_unreal
    return PipelineLimits(
        max_archive_bytes=config_unreal.MAX_ARCHIVE_GB * GIB,
        max_uncompressed_bytes=config_unreal.MAX_ARCHIVE_UNCOMPRESSED_GB * GIB,
        cache_max_bytes=config_unreal.CACHE_MAX_GB * GIB,
        min_free_bytes=config_unreal.MIN_FREE_GB * GIB,
        copy_stall_seconds=config_unreal.COPY_STALL_SECONDS,
        workcopy_mode=config_unreal.WORKCOPY_MODE,
    )
