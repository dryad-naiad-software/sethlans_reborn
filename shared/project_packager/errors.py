# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Exceptions raised by the Unreal project packager (Unreal foundation FR-PK).

Every packager failure is a ``PackagerError``; callers that only want to
show a message can catch that one class.
"""
from typing import Iterable

MAX_LISTED_PATHS = 50


class PackagerError(Exception):
    """The project cannot be packaged; the message says why.

    ``paths`` holds the offending project paths, when there are any (at
    most ``MAX_LISTED_PATHS`` of them, FR-PK-9).
    """

    def __init__(self, message: str, paths: Iterable[str] = ()):
        super().__init__(message)
        self.paths = tuple(paths)[:MAX_LISTED_PATHS]


class UnsupportedProjectFeature(PackagerError):
    """The project uses something phase 1 cannot package (FR-PK-4, OQ-2)."""


class SourceChangedError(PackagerError):
    """A source file changed, or was swapped, while it was packaged (FR-PK-5)."""

    def __init__(self, relpath: str, detail: str = ''):
        self.relpath = relpath
        suffix = f" ({detail})" if detail else ''
        super().__init__(
            f"{relpath!r} changed while it was being packaged{suffix}; "
            "close the editor or wait for it to finish saving, then try again",
            (relpath,),
        )


class PackagingCancelled(PackagerError):
    """``package()`` stopped because its ``cancel`` event was set."""

    def __init__(self):
        super().__init__("packaging was cancelled")
