# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Drift-guard tests for the macOS ``Info.plist`` version fields.

Issue #107 — ``packaging/pyinstaller/launcher.spec`` and
``packaging/pyinstaller/tray_helper.spec`` previously hardcoded
``CFBundleShortVersionString`` / ``CFBundleVersion`` to ``'0.1.0'``,
so the macOS ``.app`` bundle advertised the wrong version to Finder,
``mdls``, Gatekeeper, and crash reporters regardless of the repo-root
``VERSION`` file. The fix derives both fields from VERSION at build
time. These tests lock that wiring in so a future drift is caught
before the bundle ships.

Companion to ``tests/unit/test_pyinstaller_specs.py`` (kept separate
to respect the 300-line file-size cap).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_DIR = REPO_ROOT / "packaging" / "pyinstaller"
LAUNCHER_SPEC = SPEC_DIR / "launcher.spec"
TRAY_SPEC = SPEC_DIR / "tray_helper.spec"

# Both spec files set up _BUNDLE_VERSION via _VERSION_SRC.read_text.
# We assert the variable is referenced in the info_plist block, not
# that it equals any specific number — VERSION drifts intentionally.
_BUNDLE_VERSION_NAME = "_BUNDLE_VERSION"


@pytest.fixture(scope="module")
def launcher_spec_text() -> str:
    return LAUNCHER_SPEC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tray_spec_text() -> str:
    return TRAY_SPEC.read_text(encoding="utf-8")


def _info_plist_block(spec_text: str) -> str:
    """Return the ``info_plist={...}`` literal from a spec file.

    Both specs have exactly one ``info_plist={`` keyword arg passed to
    ``BUNDLE(...)``. The block is bounded by the matching closing
    brace at the same indentation level as ``info_plist={``.
    """
    match = re.search(
        r"info_plist=\{(?P<body>.*?)\n\s*\},",
        spec_text,
        flags=re.DOTALL,
    )
    assert match is not None, (
        "Could not locate info_plist={...} block in spec text — the "
        "drift guard cannot run. Check the spec wasn't reformatted."
    )
    return match.group("body")


@pytest.mark.parametrize(
    "fixture_name, spec_label",
    [
        ("launcher_spec_text", "launcher.spec"),
        ("tray_spec_text", "tray_helper.spec"),
    ],
)
class TestInfoPlistDrivenByVersionFile:
    """Both .app bundles' Info.plist version fields read from VERSION."""

    def test_no_hardcoded_legacy_version(
        self, fixture_name: str, spec_label: str, request,
    ) -> None:
        """The literal ``'0.1.0'`` must not appear in the info_plist block."""
        spec_text = request.getfixturevalue(fixture_name)
        plist = _info_plist_block(spec_text)
        assert "'0.1.0'" not in plist, (
            f"{spec_label} info_plist contains the hardcoded legacy "
            "version '0.1.0' — should reference _BUNDLE_VERSION read "
            "from the repo-root VERSION file (#107)."
        )
        assert '"0.1.0"' not in plist, (
            f"{spec_label} info_plist contains the hardcoded legacy "
            'version "0.1.0" — should reference _BUNDLE_VERSION read '
            "from the repo-root VERSION file (#107)."
        )

    def test_short_version_reads_from_version_file(
        self, fixture_name: str, spec_label: str, request,
    ) -> None:
        """``CFBundleShortVersionString`` references _BUNDLE_VERSION."""
        spec_text = request.getfixturevalue(fixture_name)
        plist = _info_plist_block(spec_text)
        assert "CFBundleShortVersionString" in plist, (
            f"{spec_label} info_plist is missing CFBundleShortVersionString."
        )
        # Match e.g. 'CFBundleShortVersionString': _BUNDLE_VERSION,
        pattern = (
            r"['\"]CFBundleShortVersionString['\"]\s*:\s*"
            + re.escape(_BUNDLE_VERSION_NAME)
        )
        assert re.search(pattern, plist), (
            f"{spec_label}: CFBundleShortVersionString must be "
            f"assigned the {_BUNDLE_VERSION_NAME} variable, not a "
            "hardcoded literal."
        )

    def test_bundle_version_reads_from_version_file(
        self, fixture_name: str, spec_label: str, request,
    ) -> None:
        """``CFBundleVersion`` references _BUNDLE_VERSION."""
        spec_text = request.getfixturevalue(fixture_name)
        plist = _info_plist_block(spec_text)
        assert "CFBundleVersion" in plist, (
            f"{spec_label} info_plist is missing CFBundleVersion."
        )
        pattern = (
            r"['\"]CFBundleVersion['\"]\s*:\s*"
            + re.escape(_BUNDLE_VERSION_NAME)
        )
        assert re.search(pattern, plist), (
            f"{spec_label}: CFBundleVersion must be assigned the "
            f"{_BUNDLE_VERSION_NAME} variable, not a hardcoded literal."
        )


class TestBundleVersionVariableWiring:
    """Each spec defines _BUNDLE_VERSION by reading the VERSION file."""

    @pytest.mark.parametrize(
        "spec_path, spec_label",
        [
            (LAUNCHER_SPEC, "launcher.spec"),
            (TRAY_SPEC, "tray_helper.spec"),
        ],
    )
    def test_spec_reads_version_into_bundle_version(
        self, spec_path: Path, spec_label: str,
    ) -> None:
        """``_BUNDLE_VERSION`` must come from ``_VERSION_SRC.read_text(...)``.

        Both specs already validate _VERSION_SRC exists for the
        version-file COPY into the bundle; this test confirms the
        macOS Info.plist branch reads the same source.
        """
        text = spec_path.read_text(encoding="utf-8")
        # Match e.g. _BUNDLE_VERSION = _VERSION_SRC.read_text(...)
        pattern = (
            r"_BUNDLE_VERSION\s*=\s*_VERSION_SRC\.read_text"
        )
        assert re.search(pattern, text), (
            f"{spec_label}: _BUNDLE_VERSION must be derived from "
            "_VERSION_SRC.read_text(...) so the macOS Info.plist "
            "version fields stay in lockstep with the repo-root "
            "VERSION file (#107)."
        )
        # And it must be .strip()'d — CFBundle*Version is sensitive to
        # trailing whitespace.
        assert ".strip()" in text, (
            f"{spec_label}: _BUNDLE_VERSION read must end in .strip() "
            "to drop the VERSION file's trailing newline."
        )
