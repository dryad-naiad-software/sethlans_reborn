# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unreal project packager (Unreal foundation FR-PK), pure stdlib.

Builds the pristine work zip of an artist's Unreal project: the root
``<Name>.uproject`` plus ``Config/`` and ``Content/``, under ``<Name>/``,
with a ``sethlans-manifest.json`` at the root. The output is
deterministic and always passes the worker's extraction rules
(FR-PK-14). The project directory is only read.

This is the library only. Which program on the user's computer calls it
(and its UI) is parked (OQ-1); it has no Django dependency and reports
progress through a callback with a cancel event, so a local app can call
it directly::

    from shared.project_packager import package, scan

    preview = scan(project_dir)
    result = package(project_dir, out_path)
    result.archive_sha256   # the asset's archive pin (D3)

Modules: ``rules`` (allowlist and worker rules), ``scanner``, ``writer``,
``manifest``, ``source_io`` (read-only, link-refusing, contained file
access, with ``source_win``/``source_posix`` for the per-platform
containment checks), ``api`` (``package``), ``model`` and ``errors``.
"""
from .api import package
from .errors import PackagerError, PackagingCancelled, SourceChangedError, UnsupportedProjectFeature
from .manifest import Manifest, ManifestFile
from .model import PackageResult, PackagerOptions, ScanResult
from .rules import EXTENSION_DIRS, MANIFEST_NAME, PACKAGER_VERSION, ExtensionRule
from .scanner import scan

__all__ = [
    'EXTENSION_DIRS', 'ExtensionRule', 'MANIFEST_NAME', 'Manifest', 'ManifestFile', 'PACKAGER_VERSION',
    'PackageResult', 'PackagerError', 'PackagerOptions', 'PackagingCancelled', 'ScanResult',
    'SourceChangedError', 'UnsupportedProjectFeature', 'package', 'scan',
]
