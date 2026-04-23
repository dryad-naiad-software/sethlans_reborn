# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Windows VERSIONINFO resource builder for PyInstaller-frozen exes.

Builds a unified "Sethlans" ``VSVersionInfo`` structure that PyInstaller
stamps onto the PE header of each frozen ``.exe`` via the ``EXE(version=)``
kwarg. All four Sethlans components (launcher, manager, worker,
tray_helper) share the same branding strings; only ``OriginalFilename``
and ``InternalName`` differ per component.

Issue #109: without this resource, Task Manager Details, File Properties,
and SmartScreen dialogs fall back to generic filename-derived strings.

The version number is read from the repo-root ``VERSION`` file — single
source of truth, shared with ``shared.version.get_version()`` (#106).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Tuple


_COMPANY_NAME = "Dryad and Naiad Software LLC"
_FILE_DESCRIPTION = "Sethlans"
_LEGAL_COPYRIGHT = "© 2025 Dryad and Naiad Software LLC"
_PRODUCT_NAME = "Sethlans"

# en-US language (0x0409) + Unicode code page (0x04B0 / 1200). The
# lang-code key in the StringTable is the hex string `040904B0`; the
# matching VarFileInfo Translation pair is `[1033, 1200]`.
_LANG_CODE_PAGE = "040904B0"
_TRANSLATION = [1033, 1200]

_VERSION_FILE = Path(__file__).resolve().parent.parent.parent / "VERSION"

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _read_version_string() -> str:
    """Read and return the trimmed contents of the repo-root VERSION file.

    SystemExit with a clear message if the file is absent — mirrors the
    existing ``_CADDY_SRC.is_file()`` guard pattern used in the specs so
    the failure surfaces at build time rather than at runtime on a user's
    machine.
    """
    if not _VERSION_FILE.is_file():
        raise SystemExit(
            f"VERSION file not found at {_VERSION_FILE}. "
            "Expected the repo-root VERSION file to be present before "
            "building the Windows VERSIONINFO resource (issue #109)."
        )
    return _VERSION_FILE.read_text(encoding="utf-8").strip()


def _parse_semver(version_str: str) -> Tuple[int, int, int, int]:
    """Parse ``MAJOR.MINOR.PATCH`` into the 4-int tuple PyInstaller wants.

    PyInstaller's ``FixedFileInfo`` expects a ``(major, minor, patch,
    build)`` tuple. Build number always defaults to 0 — Sethlans does not
    currently emit a build counter.

    Raises ``ValueError`` on non-semver input (e.g. ``'0.2'``,
    ``'0.2.0-rc1'``, ``'not-a-version'``) so the failure surfaces at
    build time rather than shipping a broken resource.
    """
    match = _SEMVER_RE.match(version_str)
    if not match:
        raise ValueError(
            f"VERSION file contents {version_str!r} is not a plain "
            "three-part semver (MAJOR.MINOR.PATCH). Pre-release suffixes "
            "and build metadata are not supported by the Windows "
            "VERSIONINFO resource builder (issue #109)."
        )
    major, minor, patch = (int(g) for g in match.groups())
    return (major, minor, patch, 0)


def make_version_info(original_filename: str, internal_name: str):
    """Build a fully-populated PyInstaller ``VSVersionInfo`` for a Sethlans exe.

    Parameters
    ----------
    original_filename:
        The actual built exe name (``run_launcher.exe``, etc.). Windows
        cross-checks this against the on-disk filename in some tools.
    internal_name:
        Short component identifier (``run_launcher``, etc.). Shown in
        Task Manager's "Command line" inspection and some shell surfaces.

    Returns
    -------
    ``PyInstaller.utils.win32.versioninfo.VSVersionInfo`` suitable for
    passing to ``EXE(version=...)`` in a spec file.

    Notes
    -----
    The PyInstaller import is deferred into this function so the module
    can be imported in environments where PyInstaller is unavailable
    (e.g. the test venv before build deps are installed). An
    ``ImportError`` only surfaces when someone actually asks for a
    version resource.
    """
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    version_str = _read_version_string()
    version_tuple = _parse_semver(version_str)

    string_table = StringTable(
        _LANG_CODE_PAGE,
        [
            StringStruct("CompanyName", _COMPANY_NAME),
            StringStruct("FileDescription", _FILE_DESCRIPTION),
            StringStruct("FileVersion", version_str),
            StringStruct("InternalName", internal_name),
            StringStruct("LegalCopyright", _LEGAL_COPYRIGHT),
            StringStruct("OriginalFilename", original_filename),
            StringStruct("ProductName", _PRODUCT_NAME),
            StringStruct("ProductVersion", version_str),
        ],
    )

    return VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=version_tuple,
            prodvers=version_tuple,
        ),
        kids=[
            StringFileInfo([string_table]),
            VarFileInfo([VarStruct("Translation", list(_TRANSLATION))]),
        ],
    )
