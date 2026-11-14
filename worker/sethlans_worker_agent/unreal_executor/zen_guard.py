# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Zen/DDC data-path guard (Unreal foundation FR-UE-24, v4.1: S2; review
fixes Sec S2, S3).

A project or overlay ini that configures Zen or a DDC data path is
refused, because UE can **delete** the worker user's DDC: a
``[Zen.AutoLaunch] DataPath`` in a project ini becomes the default data
path and UE then migrates "from '<old>' to '<new>'. Old location will be
deleted."

``scan_project_config(project_dir)`` runs after working-copy
materialization and before any UE launch (FR-UE-2 step 4a). It reads
**every** ``*.ini`` anywhere in the working copy (``Config/**``,
``Saved/Config/**``, ``Plugins/**/Config``, ``Platforms/**/Config``, ...),
walking without following links. Each file is UTF-8 (optional BOM), or
UTF-16 when a BOM says so. The guard **fails closed** on a file that
cannot be decoded, that contains NUL, or that contains a character
Python treats as a line break but UE does not (``\\x0b``, ``\\x0c``,
``\\x1c``-``\\x1e``, ``\\x85``, U+2028, U+2029); lines are split exactly as
UE splits them (CR, LF, CRLF). Section names and keys compare
case-insensitively, after stripping an ini array prefix (``+``, ``-``,
``.``, ``!``) from the key. Refused entries (``ZEN_DDC_INI_RULES``):

1. **any** key in a section named ``Zen`` or ``Zen.<anything>`` (data
   path, port, ``ExtraArgs=--clean``, ...);
2. in a section whose name ends with ``DerivedDataBackendGraph``, any key
   whose value contains ``Path=``, ``EnvPathOverride=``,
   ``CommandLineOverride=`` or ``EditorOverrideSetting=``;
3. ``GlobalLocalDDCPath``, ``GlobalSharedDDCPath`` or ``GlobalS3DDCPath``
   in ``[/Script/UnrealEd.EditorSettings]`` (registry-backed in 5.8; extras);
4. ``LocalDerivedDataCache`` or ``SharedDerivedDataCache`` in
   ``[/Script/UnrealEd.EditorSettings]`` with any value other than the
   empty forms UE writes itself (``(Path="")``, ``(Path=)``, ``()``, empty;
   whitespace and case ignored). Fail closed, no ``Path=`` parsing (review
   D2, N1).

The files are never rewritten (they are part of the pinned project). A
hit is a content failure (job ERROR) with ``zen_ddc_guard=FAILED``.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple

from sethlans_worker_agent.project_archive import fs_safe

MAX_INI_BYTES = 16 * 1024 * 1024
_ARRAY_PREFIXES = '+-.!'
_GRAPH_VALUE_MARKERS = ('path=', 'envpathoverride=', 'commandlineoverride=', 'editoroverridesetting=')
_UE_LINE_BREAK = re.compile(r'\r\n|\r|\n')
_FORBIDDEN_CHARS = re.compile('[\x00\x0b\x0c\x1c\x1d\x1e\x85  ]')

# (kind, section, keys): 'zen' = section Zen or Zen.*; 'keys' = exact section, any value;
# 'path_keys' = exact section, any value but UE's own empty forms; 'graph' = section suffix.
ZEN_DDC_INI_RULES: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    ('zen', 'zen', ()),
    ('graph', 'deriveddatabackendgraph', ()),
    # Registry-backed in 5.8 (EditorSettings.h:69, 76); kept as extras.
    ('keys', '/script/unrealed.editorsettings', ('globallocalddcpath', 'globalsharedddcpath', 'globals3ddcpath')),
    # Review D2/N1: the config keys that do redirect a DDC (EditorSettings.h:83, 90; BaseEngine.ini:2866,
    # 2868) and outrank -LocalDataCachePath/-SharedDataCachePath (FileSystemCacheStore.cpp:3043-3057). UE
    # itself writes LocalDerivedDataCache=(Path="") on every launch, so only UE's empty forms are allowed.
    ('path_keys', '/script/unrealed.editorsettings', ('localderiveddatacache', 'sharedderiveddatacache')),
)
# The only values UE writes itself (iso3 farm profile). Anything else is refused: UE reads Path= with
# FParse::Value (boundary and quote rules, CString.h:726-778), which a regex here would only approximate,
# so decoys such as (XPath="",Path="\host\share") must not be parsed, only refused (review N1).
UE_EMPTY_DDC_VALUES = frozenset({'(path="")', '(path=)', '()', ''})


def _not_ue_empty(value: str) -> bool:
    """True unless ``value`` (whitespace removed, case-folded) is one of ``UE_EMPTY_DDC_VALUES``."""
    return ''.join(value.split()).casefold() not in UE_EMPTY_DDC_VALUES


@dataclass(frozen=True)
class GuardHit:
    relpath: str
    section: str
    key: str


@dataclass(frozen=True)
class ZenGuardResult:
    hits: Tuple[GuardHit, ...] = ()
    error: str = ''
    files: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.hits and not self.error

    @property
    def summary(self) -> str:
        return 'zen_ddc_guard=ok' if self.ok else 'zen_ddc_guard=FAILED'

    @property
    def message(self) -> str:
        if self.error:
            return self.error
        if not self.hits:
            return ''
        hit = self.hits[0]
        return (f"Project config sets a Zen/DDC data path ({hit.relpath}: [{hit.section}] {hit.key}); "
                "farm renders refuse it because Unreal may delete the worker user's DDC.")


def decode_ini(data: bytes) -> str:
    """UTF-8 (optional BOM) or BOM-marked UTF-16; raises ``UnicodeDecodeError``."""
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16')
    return data.decode('utf-8-sig')


def _refused(section: str, key: str, value: str) -> bool:
    lowered = section.lower()
    for kind, name, keys in ZEN_DDC_INI_RULES:
        if kind == 'zen' and (lowered == name or lowered.startswith(name + '.')):
            return True
        if kind == 'keys' and lowered == name and key in keys:
            return True
        if kind == 'path_keys' and lowered == name and key in keys and _not_ue_empty(value):
            return True
        if kind == 'graph' and lowered.endswith(name):
            folded = value.lower()
            if any(marker in folded for marker in _GRAPH_VALUE_MARKERS):
                return True
    return False


def scan_ini_text(text: str, relpath: str) -> List[GuardHit]:
    """Hits in one decoded ini (split on CR/LF only, as UE does)."""
    hits: List[GuardHit] = []
    section = ''
    for raw in _UE_LINE_BREAK.split(text):
        line = raw.strip(' \t')
        if not line or line[0] in ';#':
            continue
        if line.startswith('[') and line.endswith(']'):
            section = line[1:-1].strip(' \t')
            continue
        if '=' not in line:
            continue
        raw_key, value = line.split('=', 1)
        key = raw_key.strip(' \t').lstrip(_ARRAY_PREFIXES).strip(' \t').lower()
        if _refused(section, key, value):
            hits.append(GuardHit(relpath=relpath, section=section, key=raw_key.strip(' \t')))
    return hits


def _read(path: Path, relpath: str):
    """``(text, error)`` for one ini file."""
    try:
        with open(fs_safe.long_path(path), 'rb') as handle:
            text = decode_ini(handle.read(MAX_INI_BYTES + 1))
    except (OSError, UnicodeDecodeError):
        return None, f"Project config file {relpath} cannot be decoded; farm renders refuse it (Zen/DDC guard)."
    if _FORBIDDEN_CHARS.search(text):
        return None, (f"Project config file {relpath} contains NUL or a non-CR/LF line break; "
                      "farm renders refuse it (Zen/DDC guard).")
    return text, ''


def scan_project_config(project_dir) -> ZenGuardResult:
    """FR-UE-24 over every ``*.ini`` under ``project_dir``."""
    project_dir = Path(project_dir)
    try:
        listing = fs_safe.walk_tree(project_dir)
    except OSError as exc:
        return ZenGuardResult(error=f"Project config could not be checked for Zen/DDC paths ({exc}).")
    files = sorted(rel for rel in listing.files if rel.lower().endswith('.ini'))
    hits: List[GuardHit] = []
    for rel in files:
        if listing.files[rel].st_size > MAX_INI_BYTES:
            return ZenGuardResult(error=f"Project config file {rel} is too large to check (Zen/DDC guard).")
        text, error = _read(project_dir.joinpath(*rel.split('/')), rel)
        if error:
            return ZenGuardResult(error=error)
        hits.extend(scan_ini_text(text, rel))
    return ZenGuardResult(hits=tuple(hits), files=tuple(files))
