# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
UNC path rules for the Sethlans share (Unreal foundation FR-MU-5, FR-MU-6).

``validate_unc_dir`` checks a share **root** (the manager's setting and
each worker's ``SETHLANS_UNREAL_SHARE``); a root may be the bare share,
``\\\\nas\\sethlans``. ``validate_unc_path`` checks a **resolved** path,
which needs at least one component after the share
(``shared.unreal.share_paths.resolve_in_share`` builds on it).

A violation raises ``ValueError`` whose message names the rule. The
checks are purely lexical: no filesystem or network call is made.

Documented limit: the rules cannot see server-side junctions or DFS
referrals; a path that passes can still resolve elsewhere on the server.
"""
import re
import unicodedata
from dataclasses import dataclass
from typing import Tuple

MIN_LENGTH = 5
MAX_LENGTH = 1024

_HOST = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252}[A-Za-z0-9])?\Z', re.ASCII)
_DRIVE = re.compile(r'^[A-Za-z]:', re.ASCII)
_FORBIDDEN_CHARS = frozenset('<>:"|?*')
_SHORT_NAME = re.compile(r'~\d')
_BAD_CATEGORIES = frozenset({'Cc', 'Cf', 'Co', 'Cn', 'Zl', 'Zp'})
_ADMIN_SHARE = re.compile(r'^(?:[A-Za-z]\$|admin\$|ipc\$|pipe|mailslot)\Z', re.IGNORECASE)


def fold_component(part: str) -> str:
    """Length-preserving case fold, per character (like the NTFS upcase table).

    ``str.casefold``/``upper`` turn ``'ß'`` into ``'ss'``/``'SS'``, which would
    make two different NTFS names compare equal. A character whose uppercase
    is longer than one character is kept as it is.
    """
    out = []
    for ch in part:
        upper = ch.upper()
        out.append(upper if len(upper) == 1 else ch)
    return ''.join(out)


@dataclass(frozen=True)
class UncPath:
    """A validated UNC path: ``\\\\host\\share[\\rest...]``."""
    host: str
    share: str
    rest: Tuple[str, ...] = ()

    @property
    def canonical(self) -> str:
        """``\\\\`` + lowercase host + ``\\`` + the remaining components (rule 8)."""
        return '\\\\' + '\\'.join((self.host.lower(), self.share) + self.rest)

    def components(self) -> Tuple[str, ...]:
        """Host, share and rest components, case-folded per character (``fold_component``)."""
        return tuple(fold_component(part) for part in (self.host, self.share) + self.rest)

    def is_within(self, prefix: 'UncPath') -> bool:
        """True when ``prefix`` is this path or a whole-component ancestor."""
        mine, theirs = self.components(), prefix.components()
        return len(mine) >= len(theirs) and mine[:len(theirs)] == theirs

    def __str__(self) -> str:
        return self.canonical


def _check_text(path) -> None:
    if not isinstance(path, str):
        raise ValueError("UNC path must be a string (rule 1)")
    if not MIN_LENGTH <= len(path) <= MAX_LENGTH:
        raise ValueError(f"UNC path length must be {MIN_LENGTH}-{MAX_LENGTH} characters (rule 1)")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in path):
        raise ValueError("UNC path contains a control character (rule 1)")
    if '/' in path:
        raise ValueError("UNC path contains a forward slash (rule 1)")
    if _DRIVE.match(path):
        raise ValueError("drive-letter paths are not allowed, use a UNC path (rule 7)")
    if not path.startswith('\\\\') or path.startswith('\\\\\\'):
        raise ValueError("UNC path must start with exactly two backslashes (rule 2)")


def _check_component(part: str) -> None:
    if part in ('.', '..'):
        raise ValueError(f"component {part!r} is not allowed (rule 4)")
    if any(ch in _FORBIDDEN_CHARS for ch in part):
        raise ValueError(f"component {part!r} contains one of <>:\"|?* (rule 4)")
    if part.endswith((' ', '.')):
        raise ValueError(f"component {part!r} ends with a space or a dot (rule 4)")
    if part.startswith(' '):
        raise ValueError(f"component {part!r} starts with a space (rule 4)")
    if _SHORT_NAME.search(part):
        raise ValueError(f"component {part!r} looks like an 8.3 short name (rule 4)")
    if any(unicodedata.category(ch) in _BAD_CATEGORIES for ch in part):
        raise ValueError(f"component {part!r} contains an invisible or control character (rule 4)")
    if not unicodedata.is_normalized('NFC', part):
        raise ValueError(f"component {part!r} is not NFC-normalized (rule 4)")


def _parse(path, *, min_rest: int) -> UncPath:
    _check_text(path)
    parts = path[2:].split('\\')
    host = parts[0]
    if not _HOST.match(host):
        raise ValueError(f"host {host!r} is not a valid host name or address (rule 2)")
    if len(parts) < 2:
        raise ValueError("UNC path has no share component (rule 3)")
    if any(part == '' for part in parts[1:]):
        raise ValueError("UNC path has an empty component (rule 3)")
    share, rest = parts[1], tuple(parts[2:])
    if len(rest) < min_rest:
        raise ValueError("UNC path needs a component after the share (rule 3)")
    for part in (share,) + rest:
        _check_component(part)
    if _ADMIN_SHARE.match(share):
        raise ValueError(f"share {share!r} is an administrative or IPC share (rule 5)")
    return UncPath(host=host, share=share, rest=rest)


def validate_unc_dir(path) -> UncPath:
    """Validate a Sethlans share root (FR-MU-6). A single trailing ``\\`` is stripped."""
    if isinstance(path, str) and path.endswith('\\') and not path.endswith('\\\\'):
        path = path[:-1]
    return _parse(path, min_rest=0)


def validate_unc_path(path) -> UncPath:
    """Validate a resolved path under a share (at least one component after it)."""
    return _parse(path, min_rest=1)


def shares_match(a, b) -> bool:
    """Normalized share comparison used for routing (FR-MU-5, FR-RT-5).

    True only when both values are non-empty, both pass
    ``validate_unc_dir`` and their per-character case-folded components
    are equal (``fold_component``; ``ß`` and ``ss`` differ, as on NTFS).
    Host aliases are not resolved: ``alpha`` and ``alpha.lan`` differ.
    """
    if not a or not b:
        return False
    try:
        return validate_unc_dir(a).components() == validate_unc_dir(b).components()
    except ValueError:
        return False


def is_single_label_host(path: UncPath) -> bool:
    """True when the host has no ``.`` and is not an IPv4 literal (Sec N4)."""
    host = path.host
    return '.' not in host and not host.isdigit()
