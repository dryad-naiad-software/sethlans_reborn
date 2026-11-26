# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Parsing of the Blender release pages and checksum files (BS-REL-7).

Only names that match these patterns are ever used, and only to build
``<source>Blender<series>/<name>`` (BS-SRC-5):

- series link: ``Blender<X>.<Y>/``;
- build link: ``blender-<X.Y.Z>-<platform id>.<that platform's extension>``,
  so ``.msi``, ``.msix``, ``.md5`` and unknown platforms are ignored;
- checksum line: ``<64 hex> <file name>``; other lines are ignored.

Every pattern is applied with ``fullmatch`` (``$`` would accept a trailing
newline) and spells digits ``[0-9]`` (a ``str`` pattern's digit class takes
any Unicode digit).

The link order on a page never matters: a Windows ``.zip`` is chosen
because ``.zip`` is the Windows extension, not because it sorts last.
"""
import re
from typing import Dict, List

from bs4 import BeautifulSoup

from shared.blender_sources.platforms import PLATFORM_EXTENSIONS, is_version, version_key

SERIES_LINK_RE = re.compile(r"Blender([0-9]+)\.([0-9]+)/")
BUILD_LINK_RE = re.compile(r"blender-([0-9]+\.[0-9]+\.[0-9]+)-([a-z0-9]+-[a-z0-9]+)\.(zip|tar\.xz|dmg)")
CHECKSUM_LINE_RE = re.compile(r"([0-9a-fA-F]{64})\s+(\S+)")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
MIN_MAJOR = 4


def link_targets(html) -> List[str]:
    """Every ``href`` of every ``<a>`` on a page."""
    soup = BeautifulSoup(html, "html.parser")
    return [href for href in (a.get("href") for a in soup.find_all("a")) if isinstance(href, str)]


def parse_series_index(html) -> List[str]:
    """Series (``"4.5"``) linked from the index page, ``X >= 4``, oldest first."""
    found = set()
    for href in link_targets(html):
        match = SERIES_LINK_RE.fullmatch(href)
        if match and int(match.group(1)) >= MIN_MAJOR and len(match.group(1)) <= 3 and len(match.group(2)) <= 3:
            found.add(f"{int(match.group(1))}.{int(match.group(2))}")
    return sorted(found, key=version_key)


def parse_build_links(html) -> Dict[str, Dict[str, str]]:
    """``{version: {platform id: file name}}`` for every valid build link on a series page."""
    builds: Dict[str, Dict[str, str]] = {}
    for href in link_targets(html):
        match = BUILD_LINK_RE.fullmatch(href)
        if not match:
            continue
        version, platform_id, ext = match.groups()
        if not is_version(version) or PLATFORM_EXTENSIONS.get(platform_id) != ext:
            continue
        builds.setdefault(version, {})[platform_id] = href
    return builds


def parse_checksums(text: str) -> Dict[str, str]:
    """``{file name: lower-case sha256}`` from a ``.sha256`` file."""
    sums: Dict[str, str] = {}
    for line in text.splitlines():
        match = CHECKSUM_LINE_RE.fullmatch(line.strip())
        if match:
            sums[match.group(2)] = match.group(1).lower()
    return sums


def is_sha256(value: object) -> bool:
    """True for 64 lower- or upper-case hex characters (BS-SUM-4 compares lower case)."""
    return isinstance(value, str) and SHA256_RE.fullmatch(value.lower()) is not None
