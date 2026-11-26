# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The release-list cache file (BS-REL-4, BS-REL-5, BS-REL-6, #321).

Shape: ``{"<version>": {"<platform>": {"file": "<name>", "sha256": "<64 hex>",
"source": "<source URL>"}}}``. ``file`` is a name, never a URL.

**Read.** :func:`lookup` reads without a lock (writes replace the whole file
atomically). A hit needs the version, the platform, ``file`` exactly
``blender-<version>-<platform>.<ext>`` and a 64-hex ``sha256``; anything else
is a miss, so an empty or damaged file sends the caller to the sources.

**Write.** :func:`merge` is called only with a full answer. Under
``shared.file_lock`` on ``<cache>.lock`` it reads, merges and replaces the file
(``read_limited`` and ``atomic_replace``). A missing file starts empty; a
file that exists but cannot be read is a WARNING and is not overwritten (a
damaged or too large one is replaced). A known file's ``sha256`` is never
replaced by a different one: the old value stays and a WARNING names both
sources. A failed write is a WARNING; the caller still has its answer.
"""
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from shared.blender_sources.errors import host_of
from shared.blender_sources.listing import is_sha256
from shared.blender_sources.platforms import build_file_name, is_platform_id, is_version

logger = logging.getLogger(__name__)

MAX_CACHE_BYTES = 4 * 1024 * 1024
LOCK_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class BuildEntry:
    """One build: its version, platform, file name, official SHA-256 and the source of both."""
    version: str
    platform: str
    file: str
    sha256: str
    source: str

    def as_cache_value(self) -> Dict[str, str]:
        return {"file": self.file, "sha256": self.sha256, "source": self.source}


def _read_bytes(cache_file: Path) -> bytes:
    """At most ``MAX_CACHE_BYTES + 1`` bytes of the cache; raises ``OSError`` (missing: ``FileNotFoundError``)."""
    from shared.settings_file_io import read_limited
    return read_limited(cache_file, MAX_CACHE_BYTES)


def _parse(data: bytes) -> Optional[dict]:
    """The parsed cache, or None when too large or not a JSON object."""
    if len(data) > MAX_CACHE_BYTES:
        return None
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _valid_entry(version: str, platform_id: str, value: object) -> Optional[BuildEntry]:
    if not (is_version(version) and is_platform_id(platform_id) and isinstance(value, dict)):
        return None
    file_name, sha256, source = value.get("file"), value.get("sha256"), value.get("source", "")
    if file_name != build_file_name(version, platform_id) or not is_sha256(sha256):
        return None
    return BuildEntry(version, platform_id, file_name, sha256.lower(), source if isinstance(source, str) else "")


def lookup(cache_file, version: str, platform_id: str) -> Optional[BuildEntry]:
    """The cached entry for ``version`` and ``platform_id``, or None (a miss)."""
    if cache_file is None:
        return None
    try:
        cache = _parse(_read_bytes(Path(cache_file)))
    except OSError:
        return None
    platforms = cache.get(version) if cache else None
    if not isinstance(platforms, dict):
        return None
    return _valid_entry(version, platform_id, platforms.get(platform_id))


def _merge_into(cache: dict, version: str, entries: Dict[str, BuildEntry]) -> dict:
    platforms = cache.get(version)
    platforms = dict(platforms) if isinstance(platforms, dict) else {}
    for platform_id, entry in entries.items():
        known = _valid_entry(version, platform_id, platforms.get(platform_id))
        if known is not None and known.sha256 != entry.sha256:
            logger.warning(
                "Blender %s %s: %s gives a different SHA-256 for %s than %s gave; keeping the cached one.",
                version, platform_id, host_of(entry.source), entry.file, host_of(known.source) or "the cache",
            )
            continue
        platforms[platform_id] = entry.as_cache_value()
    cache[version] = platforms
    return cache


def merge(cache_file, version: str, entries: Dict[str, BuildEntry]) -> None:
    """Merge a full answer for ``version`` into the cache file; never raises (BS-REL-6)."""
    if cache_file is None or not entries:
        return
    from shared.file_lock import file_lock
    from shared.settings_file_io import atomic_replace
    path = Path(cache_file)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(path.with_name(path.name + ".lock"), timeout=LOCK_TIMEOUT_SECONDS):
            try:
                cache = _parse(_read_bytes(path)) or {}
            except FileNotFoundError:
                cache = {}
            except OSError as exc:  # an unreadable file is left alone, never overwritten
                logger.warning("Could not read the Blender release cache %s; not updating it: %s", path, exc)
                return
            cache = _merge_into(cache, version, entries)
            atomic_replace(path, json.dumps(cache, indent=2, sort_keys=True).encode("utf-8"))
    except OSError as exc:  # FileLockTimeoutError is an OSError
        logger.warning("Could not write the Blender release cache %s: %s", path, exc)
