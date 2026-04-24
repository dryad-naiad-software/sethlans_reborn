# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Regression tests for the cross-platform build_version scheme.

The three platform driver scripts
(``tools/build_{windows,macos,linux}_installer.sh``) read the
authoritative semver from the repo-root ``VERSION`` file and append a
5-character git commit hash to produce
``BUILD_VERSION="${VERSION}.${GIT_HASH}"`` (e.g. ``0.2.0.abc12``).

This identifier flows through to artifact filenames and macOS
``CFBundleVersion``. Apple's ``CFBundleShortVersionString`` is strict
X.Y.Z, so ``packaging/macos/build_dmg.sh`` derives ``SEMVER`` from the
first three dotted components and substitutes both into
``Info.plist.template``.

These static assertions lock the conventions in place so a future
refactor of one platform doesn't desync the others.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSION_FILE = REPO_ROOT / "VERSION"
DMG_SCRIPT = REPO_ROOT / "packaging" / "macos" / "build_dmg.sh"
PLIST_TEMPLATE = REPO_ROOT / "packaging" / "macos" / "Info.plist.template"
DRIVERS = {
    "windows": REPO_ROOT / "tools" / "build_windows_installer.sh",
    "macos": REPO_ROOT / "tools" / "build_macos_installer.sh",
    "linux": REPO_ROOT / "tools" / "build_linux_installer.sh",
}


def _debug_version_state() -> str:
    """Diagnostic snapshot for VERSION-file path resolution failures.

    Emitted when the fixture can't read VERSION. The Linux CI runner
    lives under a ``_work`` tree; if a symlink leaks through
    ``Path(__file__).resolve()`` the computed REPO_ROOT won't match the
    cloned checkout. Print the raw/resolved file path and parents so
    the next CI run shows exactly which directory was used.
    """
    cwd = Path.cwd()
    parents = [str(p) for p in Path(__file__).resolve().parents[:4]]
    return (
        f"  VERSION_FILE={VERSION_FILE!s} exists={VERSION_FILE.exists()}\n"
        f"  REPO_ROOT={REPO_ROOT!s} exists={REPO_ROOT.exists()}\n"
        f"  test_file={Path(__file__)!s}\n"
        f"  test_file_resolved={Path(__file__).resolve()!s}\n"
        f"  parents[:4]={parents}\n"
        f"  cwd={cwd!s}\n"
    )


@pytest.fixture(scope="module")
def version_text() -> str:
    if not VERSION_FILE.is_file():
        pytest.fail(
            "VERSION file not found on disk.\n" + _debug_version_state()
        )
    return VERSION_FILE.read_text(encoding="utf-8").strip()


@pytest.fixture(scope="module")
def driver_contents() -> dict[str, str]:
    return {
        name: path.read_text(encoding="utf-8")
        for name, path in DRIVERS.items()
    }


@pytest.fixture(scope="module")
def dmg_contents() -> str:
    return DMG_SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def plist_contents() -> str:
    return PLIST_TEMPLATE.read_text(encoding="utf-8")


class TestVersionFile:
    def test_version_file_is_strict_semver(self, version_text: str) -> None:
        # VERSION holds the X.Y.Z base. The git hash suffix is appended
        # at build time, never written to VERSION.
        assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version_text), (
            f"VERSION must be strict X.Y.Z (got {version_text!r}). The "
            "5-char git hash is appended by the build scripts at build "
            "time; it never lives in VERSION itself."
        )


class TestDriverBuildVersion:
    """All three driver scripts must compute BUILD_VERSION identically."""

    GIT_HASH_PATTERN = re.compile(
        r'GIT_HASH=\$\(git rev-parse --short=5 HEAD',
    )
    BUILD_VERSION_PATTERN = re.compile(
        r'BUILD_VERSION="\$\{VERSION\}\.\$\{GIT_HASH\}"',
    )

    @pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
    def test_driver_resolves_short_git_hash(
        self, driver_contents: dict[str, str], platform: str,
    ) -> None:
        assert self.GIT_HASH_PATTERN.search(driver_contents[platform]), (
            f"{platform} driver must compute "
            "`GIT_HASH=$(git rev-parse --short=5 HEAD ...)`."
        )

    @pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
    def test_driver_appends_hash_to_build_version(
        self, driver_contents: dict[str, str], platform: str,
    ) -> None:
        assert self.BUILD_VERSION_PATTERN.search(driver_contents[platform]), (
            f"{platform} driver must define "
            '`BUILD_VERSION="${VERSION}.${GIT_HASH}"` so all three '
            "platforms produce matching X.Y.Z.HHHHH artifacts for a "
            "given commit."
        )

    @pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
    def test_driver_aborts_when_git_hash_missing(
        self, driver_contents: dict[str, str], platform: str,
    ) -> None:
        # Empty hash -> hard failure. Otherwise we'd silently emit
        # `sethlans-0.2.0.-...` filenames, which look broken.
        contents = driver_contents[platform]
        assert 'if [ -z "$GIT_HASH" ]; then' in contents, (
            f"{platform} driver must guard against an empty GIT_HASH "
            "(`if [ -z \"$GIT_HASH\" ]; then ... exit 1; fi`)."
        )

    def test_windows_output_path_uses_build_version(
        self, driver_contents: dict[str, str],
    ) -> None:
        assert (
            'OUTPUT="packaging/windows/sethlans-$BUILD_VERSION-windows-x64.exe"'
            in driver_contents["windows"]
        )

    def test_macos_output_path_uses_build_version(
        self, driver_contents: dict[str, str],
    ) -> None:
        assert (
            'OUTPUT="dist/sethlans-$BUILD_VERSION-macos-arm64.dmg"'
            in driver_contents["macos"]
        )

    def test_linux_output_path_uses_build_version(
        self, driver_contents: dict[str, str],
    ) -> None:
        assert (
            'OUTPUT="${DIST_ROOT}/sethlans-${BUILD_VERSION}-linux-x64.run"'
            in driver_contents["linux"]
        )

    def test_windows_passes_build_version_to_nsis(
        self, driver_contents: dict[str, str],
    ) -> None:
        # NSIS receives PRODUCT_VERSION via /D and uses it for filenames
        # and registry values. Must be the full BUILD_VERSION.
        pattern = re.compile(
            r'-DPRODUCT_VERSION="\$BUILD_VERSION"',
        )
        assert pattern.search(driver_contents["windows"]), (
            "Windows driver must invoke NSIS with "
            '`-DPRODUCT_VERSION="$BUILD_VERSION"`.'
        )

    def test_macos_passes_build_version_to_dmg_script(
        self, driver_contents: dict[str, str],
    ) -> None:
        pattern = re.compile(r'bash\s+"\$DMG_SCRIPT"\s+"\$BUILD_VERSION"')
        assert pattern.search(driver_contents["macos"]), (
            "macOS driver must invoke build_dmg.sh with the BUILD_VERSION "
            "(not the bare semver)."
        )


class TestDmgScriptSemverDerivation:
    """build_dmg.sh accepts BUILD_VERSION and derives the strict semver."""

    def test_dmg_script_arg_is_build_version(self, dmg_contents: str) -> None:
        assert (
            'BUILD_VERSION="${1:?Usage: build_dmg.sh <build_version>}"'
            in dmg_contents
        ), (
            "build_dmg.sh must accept its single positional arg as "
            "BUILD_VERSION (renamed from VERSION when the X.Y.Z.HHHHH "
            "scheme landed)."
        )

    def test_dmg_script_derives_semver_via_cut(self, dmg_contents: str) -> None:
        pattern = re.compile(
            r'SEMVER="\$\(echo "\$BUILD_VERSION" \| cut -d\. -f1-3\)"',
        )
        assert pattern.search(dmg_contents), (
            "build_dmg.sh must derive SEMVER as the first three dotted "
            "components of BUILD_VERSION (Apple's "
            "CFBundleShortVersionString accepts strict X.Y.Z only)."
        )

    def test_dmg_filename_uses_build_version(self, dmg_contents: str) -> None:
        assert (
            'DMG_NAME="sethlans-${BUILD_VERSION}-macos-arm64"' in dmg_contents
        ), (
            "DMG filename must include the full BUILD_VERSION so the "
            "artifact is uniquely identifiable to its commit."
        )

    def test_dmg_volname_uses_semver(self, dmg_contents: str) -> None:
        # The Finder volume label is user-facing — the 5-char hash is
        # noise there. Strict semver is friendlier.
        assert '-volname "Sethlans ${SEMVER}"' in dmg_contents, (
            "hdiutil -volname should display the strict semver "
            "(Finder volume label is user-facing)."
        )

    def test_dmg_plist_substitutes_both_keys(self, dmg_contents: str) -> None:
        # sed must rewrite both ${BUILD_VERSION} and ${SEMVER} in the
        # template — substituting only one would leave a literal
        # `${BUILD_VERSION}` or `${SEMVER}` in the deployed plist.
        assert 's/\\${BUILD_VERSION}/${BUILD_VERSION}/g' in dmg_contents, (
            "build_dmg.sh sed must substitute ${BUILD_VERSION} into the "
            "Info.plist template."
        )
        assert 's/\\${SEMVER}/${SEMVER}/g' in dmg_contents, (
            "build_dmg.sh sed must substitute ${SEMVER} into the "
            "Info.plist template."
        )


class TestPlistTemplateVersionKeys:
    """Info.plist.template uses BUILD_VERSION + SEMVER on the right keys."""

    def test_cf_bundle_version_uses_build_version(
        self, plist_contents: str,
    ) -> None:
        # CFBundleVersion accepts arbitrary build identifiers, so the
        # full BUILD_VERSION (semver + git hash) belongs here.
        pattern = re.compile(
            r'<key>CFBundleVersion</key>\s*<string>\$\{BUILD_VERSION\}</string>',
        )
        assert pattern.search(plist_contents), (
            "Info.plist.template's CFBundleVersion must use "
            "${BUILD_VERSION} (the semver+git-hash identifier)."
        )

    def test_cf_bundle_short_version_string_uses_semver(
        self, plist_contents: str,
    ) -> None:
        # Apple requires CFBundleShortVersionString to be strict X.Y.Z.
        # Anything else triggers App Store / Gatekeeper warnings.
        pattern = re.compile(
            r'<key>CFBundleShortVersionString</key>\s*'
            r'<string>\$\{SEMVER\}</string>',
        )
        assert pattern.search(plist_contents), (
            "Info.plist.template's CFBundleShortVersionString must use "
            "${SEMVER} (strict X.Y.Z; Apple rejects 4-component values "
            "for this key)."
        )
