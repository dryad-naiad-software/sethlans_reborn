# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
What the packager includes, and the rules its output must satisfy
(Unreal foundation FR-PK-3, FR-PK-4, FR-PK-9, FR-PK-13, FR-PK-14).

**Allowlist (FR-PK-3).** The root ``<Name>.uproject`` and the subtrees
``Config/`` and ``Content/``. Everything else at the root is excluded.
Inside an included subtree, a name starting with ``.`` and the OS junk
files ``Thumbs.db``, ``desktop.ini`` and ``.DS_Store`` are skipped.

**Worker rules (FR-PK-9, FR-PK-14).** The worker rejects a whole archive
for one bad name or one compression bomb (FR-PA-6), so every rule it
applies to names and ratios is mirrored here. The packager cannot import
the worker (``shared`` stays independent of it); the unit test
``tests/unit/shared/project_packager/test_worker_rule_parity.py`` keeps
the two in step.
"""
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

from shared.unreal.unc import fold_component
from shared.unreal.validation import validate_uproject_relpath

from .errors import PackagerError, UnsupportedProjectFeature

PACKAGER_VERSION = "1"
MANIFEST_NAME = 'sethlans-manifest.json'
MANIFEST_SCHEMA = 1
UPROJECT_SUFFIX = '.uproject'
INCLUDED_DIRS = ('Config', 'Content')
UNSUPPORTED_DIRS = ('Plugins', 'Source')
JUNK_NAMES = frozenset({'THUMBS.DB', 'DESKTOP.INI', '.DS_STORE'})  # compared after fold_component

GIB = 1024 ** 3
MIB = 1024 ** 2
MAX_FILES = 500_000
DEFAULT_MAX_TOTAL_BYTES = 512 * GIB
MAX_DEPTH = 64
UPROJECT_MAX_BYTES = 16 * MIB
ZIP64_FORCE_BYTES = 4 * GIB

# The worker's extraction limits (FR-PA-6). The packager refuses to build an
# archive that would exceed them, so its output always extracts (FR-PK-14).
WORKER_MAX_ENTRIES = 200_000
WORKER_MAX_CD_BYTES = 64 * MIB
ENTRY_RATIO_LIMIT = 200
ENTRY_RATIO_MIN_BYTES = MIB
TOTAL_RATIO_LIMIT = 100
# Upper bound of one central-directory header without its name: 46 fixed
# bytes plus a full Zip64 extra field (4 + 3 x 8).
CD_HEADER_MAX_OVERHEAD = 46 + 28

MAX_COMPONENT = 255
_FORBIDDEN = frozenset('<>:"|?*')
_SHORT_NAME = re.compile(r'~\d')
_RESERVED = frozenset(
    {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'}
    | {f'COM{n}' for n in '123456789¹²³'}
    | {f'LPT{n}' for n in '123456789¹²³'}
)


@dataclass(frozen=True)
class ExtensionRule:
    """How an extra top-level directory is packaged (parked OQ-2)."""
    description: str


# Extra top-level directories that ``PackagerOptions.extra_top_level`` may
# name. Empty in phase 1: ``Plugins/`` and ``Source/`` wait on OQ-2.
EXTENSION_DIRS: Dict[str, ExtensionRule] = {}


def fold_key(relpath: str) -> Tuple[str, ...]:
    """Case-insensitive identity of a ``/``-separated path (as NTFS compares)."""
    return tuple(fold_component(part) for part in relpath.split('/'))


def is_skipped_child(name: str) -> bool:
    """True for a dotfile or OS junk file inside an included subtree."""
    return name.startswith('.') or fold_component(name) in JUNK_NAMES


def validate_project_name(uproject_file: str) -> str:
    """The project name (the stem) of the root ``.uproject`` (FR-PK-2)."""
    stem = uproject_file[:-len(UPROJECT_SUFFIX)]
    try:
        validate_uproject_relpath(f"{stem}/{uproject_file}")
    except ValueError as exc:
        raise PackagerError(
            f"{uproject_file!r}: the project name must start with a letter and use only "
            f"letters, digits and '_' (at most 64 characters), with the suffix '.uproject' ({exc})"
        ) from exc
    return stem


def _not_utf8(component: str) -> bool:
    try:
        component.encode('utf-8')
    except UnicodeEncodeError:
        return True
    return False


# (test, reason) in the worker's order (FR-PA-6 rules 1-2), plus two the
# worker cannot see: a backslash (it is read as a separator) and a name
# that cannot be written as UTF-8.
_COMPONENT_RULES = (
    (lambda c: c in ('', '.', '..'), "empty, '.' or '..' component"),
    (lambda c: any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in c), "NUL or control character"),
    (lambda c: '\\' in c, "backslash in a name (the worker reads it as a separator)"),
    (lambda c: any(ch in _FORBIDDEN for ch in c), 'one of <>:"|?*'),
    (lambda c: c.split('.', 1)[0].rstrip(' ').upper() in _RESERVED, "Windows reserved device name"),
    (lambda c: c.endswith((' ', '.')), "ends with a space or a dot"),
    (lambda c: len(c) > MAX_COMPONENT, f"longer than {MAX_COMPONENT} characters"),
    (lambda c: _SHORT_NAME.search(c) is not None, "8.3 short-name shape (~ followed by a digit)"),
    (_not_utf8, "not valid UTF-8"),
)


def component_problem(component: str) -> str | None:
    """Why the worker would reject this path component (FR-PA-6 rules 1-2), or None."""
    for test, reason in _COMPONENT_RULES:
        if test(component):
            return reason
    return None


def name_problems(relpaths: Iterable[str]) -> List[Tuple[str, str]]:
    """``(path, reason)`` for each path the worker would reject by name."""
    problems = []
    for relpath in relpaths:
        for component in relpath.split('/'):
            reason = component_problem(component)
            if reason is not None:
                problems.append((relpath, reason))
                break
    return problems


def collisions(relpaths: Iterable[str]) -> List[Tuple[str, str]]:
    """``(path, reason)`` for each path equal to an earlier one when letter case is ignored.

    Covers the worker's duplicate and file-versus-directory checks
    (FR-PA-6 rule 6): directories are listed among the paths.
    """
    seen: Dict[Tuple[str, ...], str] = {}
    clashes = []
    for relpath in relpaths:
        key = fold_key(relpath)
        if key in seen:
            clashes.append((relpath, f"same name as {seen[key]} when letter case is ignored"))
        else:
            seen[key] = relpath
    return clashes


def accepted_extensions(extra_top_level) -> Tuple[str, ...]:
    """The extra top-level directories to include (FR-PK-4).

    Raises:
        UnsupportedProjectFeature: a name is not in ``EXTENSION_DIRS``.
    """
    if isinstance(extra_top_level, str) or not all(isinstance(n, str) for n in extra_top_level):
        raise PackagerError("extra_top_level must be a tuple of directory names")
    for name in extra_top_level:
        if name not in EXTENSION_DIRS:
            raise UnsupportedProjectFeature(f"'{name}/' packaging is not supported yet (parked decision)")
    return tuple(extra_top_level)


def ratio_exceeds(file_size: int, compress_size: int, limit: int, min_bytes: int = 0) -> bool:
    """The worker's ratio test: ``file_size / max(compress_size, 1) > limit``."""
    return file_size >= min_bytes and file_size / max(compress_size, 1) > limit
