# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unreal Engine build strings (Unreal foundation FR-MU-5, R14).

A build is ``"<major>.<minor>.<patch>-<changelist>"``, for example
``5.8.4-58902052``. It identifies an installed engine exactly: the
manager routes a job only to a worker that reports the same build.
"""
import re
from dataclasses import dataclass

# ``\Z``, not ``$``: ``$`` also matches just before a trailing newline.
BUILD_RE = r'^(\d{1,3})\.(\d{1,3})\.(\d{1,4})-(\d{1,12})\Z'
_BUILD = re.compile(BUILD_RE, re.ASCII)

# Engine series (major, minor) phase 1 renders with (R14).
SUPPORTED_SERIES = frozenset({(5, 8)})


@dataclass(frozen=True)
class EngineBuild:
    major: int
    minor: int
    patch: int
    changelist: int

    @property
    def series(self) -> tuple:
        return (self.major, self.minor)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}-{self.changelist}"


def parse_build(value) -> EngineBuild:
    """Parse a build string. Raises ``ValueError`` naming the problem.

    The changelist must be > 0: source builds report 0 and cannot be
    matched across machines. Only the canonical spelling is accepted (no
    leading zeros), so equal builds are equal strings.
    """
    if not isinstance(value, str):
        raise ValueError("build must be a string")
    match = _BUILD.match(value)
    if match is None:
        raise ValueError(f"build {value!r} does not match MAJOR.MINOR.PATCH-CHANGELIST")
    major, minor, patch, changelist = (int(part) for part in match.groups())
    if changelist <= 0:
        raise ValueError(f"build {value!r} has changelist 0 (source build)")
    parsed = EngineBuild(major, minor, patch, changelist)
    if str(parsed) != value:
        raise ValueError(f"build {value!r} is not in canonical form (leading zeros)")
    return parsed


def is_supported(build) -> bool:
    """True when ``build`` parses and belongs to a supported series."""
    try:
        return parse_build(build).series in SUPPORTED_SERIES
    except ValueError:
        return False
