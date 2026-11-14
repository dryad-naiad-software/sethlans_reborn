# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The archive manifest, ``sethlans-manifest.json`` (Unreal foundation
FR-PK-7, FR-PK-8).

The manifest is canonical JSON: sorted keys, ``(',', ':')`` separators,
UTF-8 without ASCII escaping. ``content_sha256`` is the SHA-256 of the
canonical JSON of the ``files`` list (sorted by path), so it identifies
the packaged content independently of the zip bytes, which can differ
between zlib builds.
"""
import hashlib
import json
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from . import rules


@dataclass(frozen=True)
class ManifestFile:
    path: str     # archive path, ``<Name>/Config/DefaultEngine.ini``
    size: int
    sha256: str

    def to_dict(self) -> dict:
        return {'path': self.path, 'size': self.size, 'sha256': self.sha256}


@dataclass(frozen=True)
class Manifest:
    project_name: str
    uproject: str
    files: Tuple[ManifestFile, ...]
    content_sha256: str
    detected_features: Dict[str, bool]
    warnings: Tuple[str, ...]
    schema: int = rules.MANIFEST_SCHEMA
    packager_version: str = rules.PACKAGER_VERSION

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def total_bytes(self) -> int:
        return sum(item.size for item in self.files)

    def to_dict(self) -> dict:
        return {
            'schema': self.schema,
            'packager_version': self.packager_version,
            'project_name': self.project_name,
            'uproject': self.uproject,
            'files': [item.to_dict() for item in self.files],
            'file_count': self.file_count,
            'total_bytes': self.total_bytes,
            'content_sha256': self.content_sha256,
            'detected_features': dict(self.detected_features),
            'warnings': list(self.warnings),
        }

    def to_json_bytes(self) -> bytes:
        return canonical_json(self.to_dict())


def canonical_json(value) -> bytes:
    """Sorted keys, no whitespace, UTF-8."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def content_sha256(files: Sequence[ManifestFile]) -> str:
    """SHA-256 of the canonical JSON of ``files`` sorted by path."""
    ordered = sorted(files, key=lambda item: item.path)
    return hashlib.sha256(canonical_json([item.to_dict() for item in ordered])).hexdigest()


def build_manifest(project_name: str, files: Sequence[ManifestFile], detected_features: Dict[str, bool],
                   warnings: Sequence[str]) -> Manifest:
    ordered = tuple(sorted(files, key=lambda item: item.path))
    return Manifest(
        project_name=project_name,
        uproject=f"{project_name}/{project_name}{rules.UPROJECT_SUFFIX}",
        files=ordered,
        content_sha256=content_sha256(ordered),
        detected_features={key: bool(value) for key, value in sorted(detected_features.items())},
        warnings=tuple(warnings),
    )


def parse_manifest(data: bytes) -> Manifest:
    """Read a manifest written by ``Manifest.to_json_bytes`` (no trust implied)."""
    doc = json.loads(data.decode('utf-8'))
    files: List[ManifestFile] = [ManifestFile(item['path'], item['size'], item['sha256']) for item in doc['files']]
    return Manifest(
        project_name=doc['project_name'], uproject=doc['uproject'], files=tuple(files),
        content_sha256=doc['content_sha256'], detected_features=dict(doc['detected_features']),
        warnings=tuple(doc['warnings']), schema=doc['schema'], packager_version=doc['packager_version'],
    )
