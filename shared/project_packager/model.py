# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Options and results of the packager's public API (Unreal foundation
FR-PK-1, FR-PK-4, FR-PK-11, FR-PK-13).
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

from . import rules
from .errors import PackagerError
from .manifest import Manifest

ON_UNSUPPORTED = ('error', 'exclude')


@dataclass(frozen=True)
class PackagerOptions:
    """How ``scan()`` and ``package()`` treat a project.

    Attributes:
        extra_top_level: Extra root directories to include; only names in
            ``rules.EXTENSION_DIRS`` (empty in phase 1) are accepted.
        on_unsupported: ``'error'`` (default) makes ``package()`` refuse a
            project with ``Source/``, ``Plugins/`` or C++ modules;
            ``'exclude'`` packages it without them and records a warning.
        overwrite: Replace an existing ``out_path``.
        max_total_bytes: Largest total size of the included files.
    """
    extra_top_level: Tuple[str, ...] = ()
    on_unsupported: str = 'error'
    overwrite: bool = False
    max_total_bytes: int = rules.DEFAULT_MAX_TOTAL_BYTES

    def __post_init__(self):
        if self.on_unsupported not in ON_UNSUPPORTED:
            raise PackagerError(f"on_unsupported must be one of {ON_UNSUPPORTED}, not {self.on_unsupported!r}")
        if isinstance(self.max_total_bytes, bool) or not isinstance(self.max_total_bytes, int) \
                or self.max_total_bytes <= 0:
            raise PackagerError("max_total_bytes must be a positive integer")


@dataclass(frozen=True)
class SourceFile:
    """A file found by the scan, with the ``lstat`` identity it had then."""
    relpath: str      # project-relative, '/'-separated (``Content/Maps/Main.umap``)
    size: int
    ino: int
    dev: int
    mtime_ns: int


@dataclass(frozen=True)
class ScanResult:
    """What ``package()`` would include (FR-PK-1).

    ``files`` and ``dirs`` are project-relative, ``/``-separated paths;
    the archive puts them under ``<project_name>/``.
    """
    project_name: str
    files: List[Tuple[str, int]]
    total_bytes: int
    excluded_top_level: List[str]
    detected_features: Dict[str, bool]
    warnings: List[str]
    dirs: List[str] = field(default_factory=list)
    sources: Tuple[SourceFile, ...] = field(default=(), repr=False, compare=False)
    root: str = field(default='', repr=False, compare=False)  # resolved root the sources are under

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def dir_count(self) -> int:
        """Directory entries in the archive, including ``<project_name>/``."""
        return len(self.dirs) + 1

    @property
    def archive_entry_count(self) -> int:
        """Every archive entry: files, directories and the manifest."""
        return self.file_count + self.dir_count + 1

    @property
    def unsupported(self) -> List[str]:
        """The detected features phase 1 does not package (FR-PK-4)."""
        return [name for name in ('source', 'plugins', 'cpp_modules') if self.detected_features.get(name)]


@dataclass(frozen=True)
class PackageResult:
    """The archive ``package()`` wrote (FR-PK-1, FR-PK-12).

    ``archive_sha256`` is the SHA-256 of the archive bytes: the value an
    Unreal asset pins (D3).
    """
    manifest: Manifest
    archive_sha256: str
    archive_size: int
    out_path: Path
